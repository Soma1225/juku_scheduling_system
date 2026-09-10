"""黒塗り式の対応可能時間・科目・十進マークシートを認識する。"""

from __future__ import annotations

import calendar
import datetime
import hashlib
from dataclasses import dataclass
from pathlib import Path

from availability_recognition import detect_month_grids, expected_months
from image_layouts import (
    SUBJECT_DIGIT_REGIONS,
    SUBJECT_SELECTION_REGIONS,
    WEEKLY_AVAILABILITY_GRID,
    NormalizedRegion,
)


MARK_THRESHOLD = 0.50
AMBIGUOUS_LOW = 0.35
AMBIGUOUS_HIGH = 0.65
WEEKDAYS = ("月", "火", "水", "木", "金", "土")


@dataclass(frozen=True)
class MarkAnalysis:
    black_ratio: float
    marked: bool
    ambiguous: bool


def analyze_mark(cell) -> MarkAnalysis:
    """黒ピクセル50%以上をマークとし、35〜65%は要確認とする。"""
    ratio = float((cell < 128).mean()) if cell.size else 0.0
    return MarkAnalysis(ratio, ratio >= MARK_THRESHOLD, AMBIGUOUS_LOW <= ratio <= AMBIGUOUS_HIGH)


def _crop(gray, region: NormalizedRegion):
    height, width = gray.shape[:2]
    x1, x2 = round(region.left * width), round(region.right * width)
    y1, y2 = round(region.top * height), round(region.bottom * height)
    return gray[y1:y2, x1:x2]


def _save_crop(cell, path: Path) -> Path:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", cell, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    if not ok:
        raise OSError(f"切り抜き画像を保存できません: {path}")
    path.write_bytes(encoded.tobytes())
    return path


def _attach(conn, page_id: int, crop_path: Path, created_at: str) -> None:
    digest = hashlib.sha256(crop_path.read_bytes()).hexdigest()
    conn.execute(
        """INSERT INTO IMAGE_IMPORT_ATTACHMENTS
           (page_id,attachment_type,crop_image_path,crop_image_hash,created_at)
           VALUES (?,'OTHER_FREE_TEXT',?,?,?)""",
        (page_id, str(crop_path), digest, created_at),
    )


def _marked_digits(gray, regions: tuple[NormalizedRegion, ...]) -> tuple[list[int], bool, list[float]]:
    analyses = [analyze_mark(_crop(gray, region)) for region in regions]
    return (
        [digit for digit, result in enumerate(analyses) if result.marked],
        any(result.ambiguous for result in analyses),
        [result.black_ratio for result in analyses],
    )


def evaluate_count_marks(
    tens: list[int], ones: list[int], *, ambiguous: bool = False
) -> tuple[int | None, str | None]:
    """十・一の位を補完せず評価し、確定値またはレビュー理由を返す。"""
    if ambiguous:
        return None, "回数マークが閾値付近"
    if len(tens) != 1 or len(ones) != 1:
        reasons = []
        if len(tens) != 1:
            reasons.append(f"十の位のマーク数={len(tens)}")
        if len(ones) != 1:
            reasons.append(f"一の位のマーク数={len(ones)}")
        return None, "／".join(reasons)
    return tens[0] * 10 + ones[0], None


def store_subject_mark_candidates(
    conn,
    *,
    page_id: int,
    page_image_path: Path,
    crop_output_dir: Path,
) -> int:
    """選択科目と十・一の位を保存し、推測できない行はレビューへ回す。"""
    import cv2

    gray = cv2.imread(str(page_image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"ページ画像を読み込めません: {page_image_path}")
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    reviews = 0
    for row_number, (label, selection_region) in enumerate(SUBJECT_SELECTION_REGIONS.items(), start=1):
        selection = analyze_mark(_crop(gray, selection_region))
        digit_regions = SUBJECT_DIGIT_REGIONS[label]
        tens, tens_ambiguous, tens_ratios = _marked_digits(gray, digit_regions["tens"])
        ones, ones_ambiguous, ones_ratios = _marked_digits(gray, digit_regions["ones"])
        if not selection.marked and not selection.ambiguous and not tens and not ones:
            continue
        # 指示書どおり、十・一の位が両方空欄なら行自体を作らない。
        if (
            not tens and not ones and not tens_ambiguous and not ones_ambiguous
            and not selection.ambiguous
        ):
            continue

        count, count_error = evaluate_count_marks(
            tens, ones, ambiguous=tens_ambiguous or ones_ambiguous
        )
        valid = selection.marked and not selection.ambiguous and count_error is None
        cursor = conn.execute(
            """INSERT INTO IMAGE_IMPORT_SUBJECT_ENROLLMENTS
               (page_id,subject_row_label,recognized_count_text,recognized_count,
                recognized_count_confidence,resolved_count,count_status,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                page_id, label, str(count) if count is not None else None, count,
                1.0 if count is not None else None, count if valid else None,
                "AUTO_RECOGNIZED" if valid else "AMBIGUOUS", created_at,
            ),
        )
        if valid:
            continue

        reasons = []
        if not selection.marked or selection.ambiguous:
            reasons.append("科目選択マークが未選択または閾値付近")
        if count_error:
            reasons.append(count_error)
        row_region = NormalizedRegion(
            selection_region.left, selection_region.top,
            digit_regions["ones"][-1].right, selection_region.bottom,
        )
        crop_path = _save_crop(
            _crop(gray, row_region), Path(crop_output_dir) / f"subject_{row_number:02d}.png"
        )
        conn.execute(
            """INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
               (page_id,item_type,related_subject_enrollment_id,crop_image_path,
                candidate_value_text,created_at)
               VALUES (?,'COUNT_AMBIGUOUS',?,?,?,?)""",
            (
                page_id, cursor.lastrowid, str(crop_path), "／".join(reasons)
                + f"（十={tens}, 一={ones}, 比率={tens_ratios + ones_ratios}）", created_at,
            ),
        )
        _attach(conn, page_id, crop_path, created_at)
        reviews += 1
    return reviews


def _store_availability_cell(
    conn, *, page_id: int, key: str, period: int, cell, crop_path: Path,
    camp_range: tuple[str, str] | None, created_at: str,
) -> tuple[int, int]:
    analysis = analyze_mark(cell)
    if not analysis.marked and not analysis.ambiguous:
        return 0, 0
    slot = conn.execute(
        "SELECT slot_id FROM TIME_SLOTS WHERE session_date=? AND period_number=?", (key, period)
    ).fetchone() if len(key) == 10 else None
    if camp_range and not (camp_range[0] <= key <= camp_range[1]):
        status, recognized, resolved = "OUT_OF_CAMP_RANGE", None, None
    elif len(key) == 10 and not slot:
        status, recognized, resolved = "SLOT_NOT_FOUND", None, None
    elif analysis.ambiguous:
        status, recognized, resolved = "AMBIGUOUS", 0 if analysis.marked else 1, None
    else:
        status, recognized, resolved = "AUTO_UNAVAILABLE", 0, 0
    cursor = conn.execute(
        """INSERT INTO IMAGE_IMPORT_AVAILABILITY
           (page_id,session_date,period_number,matched_slot_id,ink_ratio,
            recognition_confidence,recognized_is_available,resolved_is_available,
            availability_status,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (page_id, key, period, slot[0] if slot else None, analysis.black_ratio,
         1.0 - abs(analysis.black_ratio - MARK_THRESHOLD), recognized, resolved, status, created_at),
    )
    needs_review = status != "AUTO_UNAVAILABLE"
    if needs_review:
        _save_crop(cell, crop_path)
        item_type = "AVAILABILITY_AMBIGUOUS" if status == "AMBIGUOUS" else status
        conn.execute(
            """INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
               (page_id,item_type,related_availability_id,crop_image_path,
                candidate_value_text,created_at)
               VALUES (?,?,?,?,?,?)""",
            (page_id, item_type, cursor.lastrowid, str(crop_path),
             f"black_ratio={analysis.black_ratio:.3f}", created_at),
        )
        _attach(conn, page_id, crop_path, created_at)
    return 1, int(needs_review)


def store_mark_sheet_availability(
    conn,
    *,
    page_id: int,
    page_image_path: Path,
    paper_type: str,
    paper_fiscal_year: int,
    crop_output_dir: Path,
) -> tuple[int, int]:
    """講習会は日付、通常用紙は曜日の黒塗りセルだけを保存する。"""
    import cv2

    gray = cv2.imread(str(page_image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"ページ画像を読み込めません: {page_image_path}")
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    saved = reviews = 0
    crop_output_dir = Path(crop_output_dir)
    if paper_type == "通常":
        grid = WEEKLY_AVAILABILITY_GRID
        height, width = gray.shape
        x1, x2 = round(grid.left * width), round(grid.right * width)
        y1, y2 = round(grid.top * height), round(grid.bottom * height)
        cell_width, cell_height = (x2 - x1) / 6, (y2 - y1) / 5
        for day_index, weekday in enumerate(WEEKDAYS):
            for period_index in range(5):
                left, right = round(x1 + day_index * cell_width), round(x1 + (day_index + 1) * cell_width)
                top, bottom = round(y1 + period_index * cell_height), round(y1 + (period_index + 1) * cell_height)
                cell = gray[top + 3:bottom - 3, left + 3:right - 3]
                added, review = _store_availability_cell(
                    conn, page_id=page_id, key=weekday, period=period_index + 1, cell=cell,
                    crop_path=crop_output_dir / f"{weekday}_p{period_index + 1}.png",
                    camp_range=None, created_at=created_at,
                )
                saved += added; reviews += review
        return saved, reviews

    grids = detect_month_grids(gray)
    months = expected_months(paper_type, paper_fiscal_year, len(grids))
    camp = conn.execute(
        """SELECT c.planned_start_date,c.planned_end_date
           FROM IMAGE_IMPORT_PAGES p JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
           JOIN CAMPS c ON c.camp_id=b.camp_id WHERE p.page_id=?""", (page_id,)
    ).fetchone()
    if not camp:
        raise ValueError("対象ページの講習会が見つかりません")
    for grid, (year, month) in zip(grids, months):
        for period_index in range(5):
            y1, y2 = grid.period_boundaries[period_index:period_index + 2]
            for day in range(1, calendar.monthrange(year, month)[1] + 1):
                x1, x2 = grid.day_boundaries[day - 1:day + 1]
                key = datetime.date(year, month, day).isoformat()
                cell = gray[y1 + 6:y2 - 6, x1 + 6:x2 - 6]
                added, review = _store_availability_cell(
                    conn, page_id=page_id, key=key, period=period_index + 1, cell=cell,
                    crop_path=crop_output_dir / f"{key}_p{period_index + 1}.png",
                    camp_range=(camp[0], camp[1]), created_at=created_at,
                )
                saved += added; reviews += review
    return saved, reviews
