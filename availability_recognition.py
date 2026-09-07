"""月別対応可能時間表の動的検出と、各マスの3段階判定。"""

import calendar
import datetime
import math
import json
from dataclasses import dataclass
from pathlib import Path


AVAILABLE_MAX_INK_RATIO = 0.02
UNAVAILABLE_MIN_INK_RATIO = 0.06
UNAVAILABLE_MIN_LINE_SCORE = 0.60
PARTIAL_STROKE_MIN_INK_RATIO = 0.02
PARTIAL_STROKE_MIN_SPAN_SCORE = 0.20


@dataclass(frozen=True)
class MonthGrid:
    day_boundaries: tuple[int, ...]
    period_boundaries: tuple[int, ...]


@dataclass(frozen=True)
class CellAnalysis:
    state: str
    ink_ratio: float
    line_crossing_score: float
    confidence: float


def _group_centers(values: list[int]) -> list[int]:
    groups = []
    for value in values:
        if not groups or value > groups[-1][-1] + 1:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [round(sum(group) / len(group)) for group in groups]


def detect_month_grids(gray) -> list[MonthGrid]:
    """ページ下半分の長罫線から、月表と日・時限境界を検出する。"""
    import numpy as np

    height, width = gray.shape
    binary = (gray < 120).astype(np.uint8)
    horizontal = binary[:, int(width * 0.02):int(width * 0.98)].mean(axis=1)
    all_centers = _group_centers([
        y for y, ratio in enumerate(horizontal)
        if int(height * 0.45) < y < int(height * 0.86) and ratio > 0.45
    ])
    strong = [y for y in all_centers if horizontal[y] > 0.90]

    triples = []
    index = 0
    while index < len(strong) - 2:
        top = strong[index]
        header_bottom = next((y for y in strong[index + 1:] if 70 <= y - top <= 130), None)
        if header_bottom is None:
            index += 1
            continue
        body_bottom = next((y for y in strong if 220 <= y - header_bottom <= 330), None)
        if body_bottom is None:
            index += 1
            continue
        triples.append((top, header_bottom, body_bottom))
        index = strong.index(body_bottom) + 1

    grids = []
    for top, body_top, bottom in triples:
        period_boundaries = [y for y in all_centers if body_top - 3 <= y <= bottom + 3]
        if len(period_boundaries) != 6:
            continue
        vertical = binary[top:bottom + 1].mean(axis=0)
        vertical_centers = _group_centers([
            x for x, ratio in enumerate(vertical)
            if int(width * 0.01) < x < int(width * 0.99) and ratio > 0.55
        ])
        if len(vertical_centers) < 4:
            continue
        gaps = [b - a for a, b in zip(vertical_centers, vertical_centers[1:])]
        label_gap_index = max(range(len(gaps)), key=gaps.__getitem__)
        day_boundaries = vertical_centers[label_gap_index + 1:]
        if len(day_boundaries) < 29:
            continue
        grids.append(MonthGrid(tuple(day_boundaries), tuple(period_boundaries)))
    return grids


def analyze_availability_cell(cell) -> CellAnalysis:
    import cv2
    import numpy as np

    ink_ratio = float((cell < 180).mean())
    edges = cv2.Canny(cell, 50, 150)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=10,
        minLineLength=max(8, int(min(cell.shape) * 0.45)), maxLineGap=5,
    )
    line_score = 0.0
    if lines is not None:
        longest = max(math.hypot(x2 - x1, y2 - y1) for x1, y1, x2, y2 in lines[:, 0])
        line_score = min(1.0, longest / math.hypot(*cell.shape))

    # 大きな斜線が複数セルをまたぐ場合、セル端にはHough変換の最短線長に
    # 届かない短い線分だけが残る。連結成分の広がりも線の根拠として使う。
    binary = cv2.threshold(cell, 180, 255, cv2.THRESH_BINARY_INV)[1]
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    span_score = max(
        (
            math.hypot(width, height) / math.hypot(cell.shape[1], cell.shape[0])
            for contour in contours
            for _, _, width, height in [cv2.boundingRect(contour)]
            if cv2.contourArea(contour) >= 4
        ),
        default=0.0,
    )
    effective_line_score = max(line_score, min(1.0, span_score))

    if ink_ratio < AVAILABLE_MAX_INK_RATIO and line_score < 0.30:
        confidence = min(1.0, 1.0 - ink_ratio / AVAILABLE_MAX_INK_RATIO)
        return CellAnalysis("AVAILABLE", ink_ratio, effective_line_score, confidence)
    partial_stroke = (
        ink_ratio >= PARTIAL_STROKE_MIN_INK_RATIO
        and span_score >= PARTIAL_STROKE_MIN_SPAN_SCORE
    )
    if ink_ratio >= UNAVAILABLE_MIN_INK_RATIO or line_score >= UNAVAILABLE_MIN_LINE_SCORE or partial_stroke:
        confidence = min(1.0, max(
            ink_ratio / UNAVAILABLE_MIN_INK_RATIO,
            line_score / UNAVAILABLE_MIN_LINE_SCORE,
            span_score / PARTIAL_STROKE_MIN_SPAN_SCORE if partial_stroke else 0.0,
        ))
        return CellAnalysis("UNAVAILABLE", ink_ratio, effective_line_score, confidence)
    return CellAnalysis("AMBIGUOUS", ink_ratio, effective_line_score, 0.5)


def expected_months(paper_type: str, fiscal_year: int, grid_count: int) -> list[tuple[int, int]]:
    if paper_type == "夏期":
        months = [(fiscal_year, 7), (fiscal_year, 8)]
    elif paper_type == "冬期":
        months = [(fiscal_year, 12), (fiscal_year + 1, 1), (fiscal_year + 1, 2)]
    elif paper_type == "春期":
        months = [(fiscal_year, 3), (fiscal_year, 4)]
        if grid_count == 1:
            months = months[:1]
    else:
        raise ValueError(f"未対応の講習会種別です: {paper_type}")
    if len(months) != grid_count:
        raise ValueError(f"{paper_type}の月表数が想定と異なります（検出{grid_count}、想定{len(months)}）")
    return months


def store_availability_candidates(
    conn,
    *,
    page_id: int,
    page_image_path: Path,
    paper_type: str,
    paper_fiscal_year: int,
    crop_output_dir: Path,
) -> tuple[int, int]:
    """可用時間候補を保存し、(保存セル数,要確認数)を返す。"""
    import cv2

    gray = cv2.imread(str(page_image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"ページ画像を読み込めません: {page_image_path}")
    grids = detect_month_grids(gray)
    months = expected_months(paper_type, paper_fiscal_year, len(grids))
    batch = conn.execute(
        """
        SELECT b.camp_id,c.planned_start_date,c.planned_end_date
        FROM IMAGE_IMPORT_PAGES p
        JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
        JOIN CAMPS c ON c.camp_id=b.camp_id WHERE p.page_id=?
        """,
        (page_id,),
    ).fetchone()
    if not batch:
        raise ValueError("対象ページの講習会が見つかりません")
    crop_output_dir = Path(crop_output_dir)
    crop_output_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    saved = reviews = 0
    for grid, (year, month) in zip(grids, months):
        valid_days = calendar.monthrange(year, month)[1]
        if len(grid.day_boundaries) - 1 < valid_days:
            raise ValueError(f"{year}-{month:02d}の日付列が不足しています")
        for period_index in range(5):
            y1, y2 = grid.period_boundaries[period_index:period_index + 2]
            for day in range(1, valid_days + 1):
                x1, x2 = grid.day_boundaries[day - 1:day + 1]
                cell = gray[y1 + 6:y2 - 6, x1 + 6:x2 - 6]
                analysis = analyze_availability_cell(cell)
                session_date = datetime.date(year, month, day).isoformat()
                slot = conn.execute(
                    "SELECT slot_id FROM TIME_SLOTS WHERE session_date=? AND period_number=?",
                    (session_date, period_index + 1),
                ).fetchone()
                if not (batch[1] <= session_date <= batch[2]):
                    status, recognized, resolved = "OUT_OF_CAMP_RANGE", None, None
                elif not slot:
                    status, recognized, resolved = "SLOT_NOT_FOUND", None, None
                elif analysis.state == "AVAILABLE":
                    status, recognized, resolved = "AUTO_AVAILABLE", 1, 1
                elif analysis.state == "UNAVAILABLE":
                    status, recognized, resolved = "AUTO_UNAVAILABLE", 0, 0
                else:
                    status, recognized, resolved = "AMBIGUOUS", None, None
                cursor = conn.execute(
                    """
                    INSERT INTO IMAGE_IMPORT_AVAILABILITY
                        (page_id,session_date,period_number,matched_slot_id,ink_ratio,
                         line_crossing_score,recognition_confidence,recognized_is_available,
                         resolved_is_available,availability_status,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (page_id, session_date, period_index + 1, slot[0] if slot else None,
                     analysis.ink_ratio, analysis.line_crossing_score, analysis.confidence,
                     recognized, resolved, status, created_at),
                )
                saved += 1
                if status in ("AMBIGUOUS", "SLOT_NOT_FOUND", "OUT_OF_CAMP_RANGE"):
                    crop_path = crop_output_dir / f"{session_date}_p{period_index + 1}.png"
                    cv2.imwrite(str(crop_path), cell)
                    item_type = "AVAILABILITY_AMBIGUOUS" if status == "AMBIGUOUS" else status
                    conn.execute(
                        """
                        INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
                            (page_id,item_type,related_availability_id,crop_image_path,
                             candidate_value_text,created_at)
                        VALUES (?,?,?,?,?,?)
                        """,
                        (page_id, item_type, cursor.lastrowid, str(crop_path),
                         f"ink={analysis.ink_ratio:.3f}, line={analysis.line_crossing_score:.3f}",
                         created_at),
                    )
                    reviews += 1
    return saved, reviews


def confirm_availability_cells(
    conn,
    *,
    page_id: int,
    confirmed_values: dict[int, int],
    operator_instructor_id: int,
) -> int:
    """曖昧な可用時間セルを職員確定し、レビュー・監査も更新する。"""
    operator = conn.execute(
        "SELECT last_name,first_name FROM INSTRUCTORS WHERE instructor_id=? AND status='在籍'",
        (operator_instructor_id,),
    ).fetchone()
    if not operator:
        raise ValueError("在籍中の担当講師を選択してください")
    page = conn.execute(
        """
        SELECT p.batch_id,b.school_id,b.school_name
        FROM IMAGE_IMPORT_PAGES p JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
        WHERE p.page_id=? AND p.is_deleted=0 AND b.is_deleted=0
        """,
        (page_id,),
    ).fetchone()
    if not page or not confirmed_values:
        raise ValueError("確定する対応可能時間がありません")
    if any(value not in (0, 1) for value in confirmed_values.values()):
        raise ValueError("対応可または対応不可を選択してください")
    placeholders = ",".join("?" for _ in confirmed_values)
    rows = conn.execute(
        f"""
        SELECT a.import_availability_id,a.session_date,a.period_number,r.review_item_id
        FROM IMAGE_IMPORT_AVAILABILITY a
        JOIN IMAGE_IMPORT_REVIEW_ITEMS r
          ON r.related_availability_id=a.import_availability_id
         AND r.item_type='AVAILABILITY_AMBIGUOUS'
         AND r.resolution='PENDING' AND r.is_deleted=0
        WHERE a.page_id=? AND a.availability_status='AMBIGUOUS' AND a.is_deleted=0
          AND a.import_availability_id IN ({placeholders})
        """,
        (page_id, *confirmed_values.keys()),
    ).fetchall()
    if len(rows) != len(confirmed_values):
        raise ValueError("対象の一部が既に確定済みか、ページに属していません")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    operator_name = f"{operator[0]}{operator[1]}"
    try:
        conn.execute("BEGIN IMMEDIATE")
        for availability_id, session_date, period_number, review_item_id in rows:
            value = confirmed_values[availability_id]
            conn.execute(
                """
                UPDATE IMAGE_IMPORT_AVAILABILITY
                SET resolved_is_available=?,availability_status='MANUALLY_CONFIRMED',
                    reviewed_by_instructor_id=?,reviewed_at=?
                WHERE import_availability_id=?
                """,
                (value, operator_instructor_id, now, availability_id),
            )
            conn.execute(
                """
                UPDATE IMAGE_IMPORT_REVIEW_ITEMS
                SET resolution='CORRECTED',corrected_value_text=?,
                    resolved_by_instructor_id=?,resolved_at=? WHERE review_item_id=?
                """,
                ("対応可" if value else "対応不可", operator_instructor_id, now, review_item_id),
            )
            conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_AUDIT_LOG
                    (batch_id,page_id,action_type,target_table,target_id,
                     before_value_json,after_value_json,actor_type,
                     operator_instructor_id,operator_name,school_id,school_name,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    page[0], page_id, "CORRECT", "IMAGE_IMPORT_AVAILABILITY", availability_id,
                    json.dumps({"availability_status": "AMBIGUOUS"}, ensure_ascii=False),
                    json.dumps({"session_date": session_date, "period_number": period_number,
                                "resolved_is_available": value,
                                "availability_status": "MANUALLY_CONFIRMED"}, ensure_ascii=False),
                    "INSTRUCTOR", operator_instructor_id, operator_name, page[1], page[2], now,
                ),
            )
        pending = conn.execute(
            """
            SELECT COUNT(*) FROM IMAGE_IMPORT_REVIEW_ITEMS r
            JOIN IMAGE_IMPORT_PAGES p ON p.page_id=r.page_id
            WHERE p.batch_id=? AND r.resolution='PENDING' AND r.is_deleted=0
            """,
            (page[0],),
        ).fetchone()[0]
        bad_quality = conn.execute(
            "SELECT COUNT(*) FROM IMAGE_IMPORT_PAGES WHERE batch_id=? AND layout_quality<>'OK' AND is_deleted=0",
            (page[0],),
        ).fetchone()[0]
        conn.execute(
            "UPDATE IMAGE_IMPORT_BATCHES SET status=? WHERE batch_id=?",
            ("REVIEW_PENDING" if pending or bad_quality else "PROCESSING", page[0]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(rows)


def resolve_availability_exception(
    conn,
    *,
    page_id: int,
    import_availability_id: int,
    resolution: str,
    operator_instructor_id: int,
) -> str:
    """枠未登録または期間外のセルを、証跡を残して解決する。

    ``SLOT_NOT_FOUND`` は時間枠を再照合して AVAILABLE/UNAVAILABLE を確定する。
    ``OUT_OF_CAMP_RANGE`` は EXCLUDE で候補を論理削除し、元画像とレビューは残す。
    """
    operator = conn.execute(
        "SELECT last_name,first_name FROM INSTRUCTORS WHERE instructor_id=? AND status='在籍'",
        (operator_instructor_id,),
    ).fetchone()
    if not operator:
        raise ValueError("在籍中の担当講師を選択してください")
    row = conn.execute(
        """
        SELECT a.session_date,a.period_number,a.availability_status,r.review_item_id,
               p.batch_id,b.status,b.school_id,b.school_name
        FROM IMAGE_IMPORT_AVAILABILITY a
        JOIN IMAGE_IMPORT_REVIEW_ITEMS r
          ON r.related_availability_id=a.import_availability_id
         AND r.item_type=a.availability_status
         AND r.resolution='PENDING' AND r.is_deleted=0
        JOIN IMAGE_IMPORT_PAGES p ON p.page_id=a.page_id
        JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
        WHERE a.import_availability_id=? AND a.page_id=? AND a.is_deleted=0
          AND a.availability_status IN ('SLOT_NOT_FOUND','OUT_OF_CAMP_RANGE')
          AND p.is_deleted=0 AND b.is_deleted=0
        """,
        (import_availability_id, page_id),
    ).fetchone()
    if not row:
        raise ValueError("未解決の時間枠例外が見つかりません")
    session_date, period_number, old_status, review_item_id, batch_id, batch_status, school_id, school_name = row
    if batch_status == "IMPORTED":
        raise ValueError("本登録済みバッチは変更できません")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    operator_name = f"{operator[0]}{operator[1]}"
    before = {
        "availability_status": old_status,
        "session_date": session_date,
        "period_number": period_number,
    }
    try:
        conn.execute("BEGIN IMMEDIATE")
        if old_status == "SLOT_NOT_FOUND":
            if resolution not in ("AVAILABLE", "UNAVAILABLE"):
                raise ValueError("対応可または対応不可を選択してください")
            slot = conn.execute(
                "SELECT slot_id FROM TIME_SLOTS WHERE session_date=? AND period_number=?",
                (session_date, period_number),
            ).fetchone()
            if not slot:
                raise ValueError("時間枠がまだ登録されていません。講習会の日程を確認してください")
            value = 1 if resolution == "AVAILABLE" else 0
            conn.execute(
                """
                UPDATE IMAGE_IMPORT_AVAILABILITY
                SET matched_slot_id=?,resolved_is_available=?,
                    availability_status='MANUALLY_CONFIRMED',
                    reviewed_by_instructor_id=?,reviewed_at=?
                WHERE import_availability_id=?
                """,
                (slot[0], value, operator_instructor_id, now, import_availability_id),
            )
            corrected_text = "対応可" if value else "対応不可"
            conn.execute(
                """
                UPDATE IMAGE_IMPORT_REVIEW_ITEMS
                SET resolution='CORRECTED',corrected_value_text=?,
                    resolved_by_instructor_id=?,resolved_at=?
                WHERE review_item_id=?
                """,
                (corrected_text, operator_instructor_id, now, review_item_id),
            )
            action_type = "CORRECT"
            after = {
                "availability_status": "MANUALLY_CONFIRMED",
                "matched_slot_id": slot[0],
                "resolved_is_available": value,
                "resolution_reason": "時間枠登録後に再照合",
            }
            message = f"{session_date} {period_number}限を再照合して{corrected_text}で確定しました"
        else:
            if resolution != "EXCLUDE":
                raise ValueError("期間外として除外を選択してください")
            conn.execute(
                "UPDATE IMAGE_IMPORT_AVAILABILITY SET is_deleted=1 WHERE import_availability_id=?",
                (import_availability_id,),
            )
            conn.execute(
                """
                UPDATE IMAGE_IMPORT_REVIEW_ITEMS
                SET resolution='REJECTED',resolved_by_instructor_id=?,resolved_at=?
                WHERE review_item_id=?
                """,
                (operator_instructor_id, now, review_item_id),
            )
            action_type = "REJECT"
            after = {
                "availability_status": "OUT_OF_CAMP_RANGE",
                "is_deleted": 1,
                "resolution_reason": "講習期間外として取込対象外",
            }
            message = f"{session_date} {period_number}限を期間外として取込対象から除外しました"
        conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_AUDIT_LOG
                (batch_id,page_id,action_type,target_table,target_id,
                 before_value_json,after_value_json,actor_type,
                 operator_instructor_id,operator_name,school_id,school_name,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                batch_id, page_id, action_type, "IMAGE_IMPORT_AVAILABILITY",
                import_availability_id, json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False), "INSTRUCTOR",
                operator_instructor_id, operator_name, school_id, school_name, now,
            ),
        )
        pending = conn.execute(
            """
            SELECT COUNT(*) FROM IMAGE_IMPORT_REVIEW_ITEMS r
            JOIN IMAGE_IMPORT_PAGES p ON p.page_id=r.page_id
            WHERE p.batch_id=? AND p.processing_status<>'SKIPPED'
              AND r.resolution='PENDING' AND r.is_deleted=0 AND p.is_deleted=0
            """,
            (batch_id,),
        ).fetchone()[0]
        bad_quality = conn.execute(
            """
            SELECT COUNT(*) FROM IMAGE_IMPORT_PAGES
            WHERE batch_id=? AND processing_status<>'SKIPPED'
              AND layout_quality<>'OK' AND is_deleted=0
            """,
            (batch_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE IMAGE_IMPORT_BATCHES SET status=? WHERE batch_id=?",
            ("REVIEW_PENDING" if pending or bad_quality else "REVIEWED", batch_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return message
