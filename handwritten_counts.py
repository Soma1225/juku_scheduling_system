"""手書き受講回数セルの空欄判定と候補保存。"""

import datetime
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


MIN_INK_COMPONENT_AREA = 8
BLANK_MAX_INK_PIXELS = 40
MODEL_PATH = Path(__file__).with_name("models") / "mnist-12.onnx"
MAX_COUNT_DIGITS = 2
# 実物用紙で再評価するまで、誤登録を避けるため高めに設定する。
AUTO_RECOGNITION_MIN_CONFIDENCE = 0.995
AUTO_RECOGNITION_MIN_MARGIN = 0.90


@dataclass(frozen=True)
class CountCellAnalysis:
    is_blank: bool
    ink_pixels: int
    component_count: int
    recognized_count: int | None = None
    confidence: float | None = None
    confidence_margin: float | None = None
    recognition_note: str | None = None


def _remove_scan_noise(mask):
    """罫線と小さなゴミを除き、数字らしい連結成分だけを残す。"""
    import cv2
    import numpy as np

    height, width = mask.shape
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    cleaned = np.zeros_like(mask)
    for index in range(1, count):
        x, y, component_width, component_height, area = map(int, stats[index])
        is_horizontal_rule = component_width >= width * 0.70 and component_height <= max(4, height * 0.12)
        is_vertical_rule = component_height >= height * 0.70 and component_width <= max(4, width * 0.04)
        if area >= MIN_INK_COMPONENT_AREA and not is_horizontal_rule and not is_vertical_rule:
            cleaned[labels == index] = 255
    return cleaned


def _ink_mask(gray):
    import cv2

    # Otsu法により、複合機ごとの濃淡差を固定閾値だけに依存させない。
    raw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    return _remove_scan_noise(raw)


def _digit_boxes(mask) -> list[tuple[int, int, int, int]]:
    """左から右の順で、1〜2桁の数字候補領域を返す。"""
    import cv2

    height, width = mask.shape
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if area < MIN_INK_COMPONENT_AREA:
            continue
        if box_height < max(8, int(height * 0.25)):
            continue
        if box_width >= width * 0.70:
            continue
        boxes.append((x, y, box_width, box_height))
    return sorted(boxes, key=lambda box: box[0])


def _normalize_digit(mask, box):
    """MNISTと同じ28x28・黒背景・白文字へ正規化する。"""
    import cv2
    import numpy as np

    x, y, width, height = box
    digit = mask[y:y + height, x:x + width]
    scale = min(20.0 / max(width, 1), 20.0 / max(height, 1))
    resized = cv2.resize(
        digit,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
    )
    canvas = np.zeros((28, 28), dtype=np.uint8)
    top = (28 - resized.shape[0]) // 2
    left = (28 - resized.shape[1]) // 2
    canvas[top:top + resized.shape[0], left:left + resized.shape[1]] = resized

    moments = cv2.moments(canvas)
    if moments["m00"]:
        center_x = moments["m10"] / moments["m00"]
        center_y = moments["m01"] / moments["m00"]
        matrix = np.float32([[1, 0, 13.5 - center_x], [0, 1, 13.5 - center_y]])
        canvas = cv2.warpAffine(canvas, matrix, (28, 28), borderValue=0)
    return canvas


@lru_cache(maxsize=1)
def _load_digit_net():
    import cv2
    import numpy as np

    if not MODEL_PATH.is_file():
        return None
    # バイト列で渡し、日本語を含むWindowsパスでも読み込めるようにする。
    model_buffer = np.frombuffer(MODEL_PATH.read_bytes(), dtype=np.uint8)
    return cv2.dnn.readNetFromONNX(model_buffer)


def _predict_digit(digit_image) -> tuple[int, float, float]:
    import numpy as np

    net = _load_digit_net()
    if net is None:
        raise FileNotFoundError(f"数字認識モデルがありません: {MODEL_PATH}")
    tensor = digit_image.astype(np.float32).reshape(1, 1, 28, 28) / 255.0
    net.setInput(tensor)
    logits = net.forward().reshape(-1)
    probabilities = np.exp(logits - logits.max())
    probabilities /= probabilities.sum()
    order = np.argsort(probabilities)[::-1]
    best, second = int(order[0]), int(order[1])
    return best, float(probabilities[best]), float(probabilities[best] - probabilities[second])


def recognize_count_cell(image_path: Path) -> CountCellAnalysis:
    """空欄または0〜99の手書き回数を、OpenCV DNNで保守的に認識する。"""
    import cv2

    gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"回数セル画像を読み込めません: {image_path}")
    mask = _ink_mask(gray)
    ink_pixels = int(cv2.countNonZero(mask))
    component_count = max(0, cv2.connectedComponents(mask, 8)[0] - 1)
    if ink_pixels <= BLANK_MAX_INK_PIXELS:
        return CountCellAnalysis(True, ink_pixels, component_count, 0, 1.0, 1.0, "空欄")

    boxes = _digit_boxes(mask)
    if not 1 <= len(boxes) <= MAX_COUNT_DIGITS:
        return CountCellAnalysis(
            False, ink_pixels, component_count,
            recognition_note=f"数字領域を{len(boxes)}個検出（1〜2個のみ対応）",
        )
    try:
        predictions = [_predict_digit(_normalize_digit(mask, box)) for box in boxes]
    except (FileNotFoundError, cv2.error) as exc:
        return CountCellAnalysis(False, ink_pixels, component_count, recognition_note=str(exc))

    text = "".join(str(item[0]) for item in predictions)
    confidence = min(item[1] for item in predictions)
    margin = min(item[2] for item in predictions)
    return CountCellAnalysis(
        False, ink_pixels, component_count, int(text), confidence, margin,
        f"推定{text}（信頼度={confidence:.3f},差={margin:.3f}）",
    )


def analyze_count_cell(image_path: Path) -> CountCellAnalysis:
    """小さな汚れを除外し、実質的な筆跡があるかを判定する。"""
    import cv2

    gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"回数セル画像を読み込めません: {image_path}")
    cleaned = _ink_mask(gray)
    component_count = max(0, cv2.connectedComponents(cleaned, 8)[0] - 1)
    ink_pixels = int(cv2.countNonZero(cleaned))
    return CountCellAnalysis(
        is_blank=ink_pixels <= BLANK_MAX_INK_PIXELS,
        ink_pixels=ink_pixels,
        component_count=component_count,
    )


def store_subject_count_candidates(conn, *, page_id: int, count_regions: dict[str, Path]) -> int:
    """6科目の回数候補を保存し、非空欄には確認項目を作成する。

    戻り値は職員確認が必要なセル数。
    """
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    review_count = 0
    for subject_label, crop_path in count_regions.items():
        analysis = recognize_count_cell(crop_path)
        if analysis.is_blank:
            cursor = conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                    (page_id,subject_row_label,recognized_count_text,recognized_count,
                     recognized_count_confidence,resolved_count,count_status,created_at)
                VALUES (?,?,'',0,1.0,0,'AUTO_BLANK',?)
                """,
                (page_id, subject_label, created_at),
            )
        elif (
            analysis.recognized_count is not None
            and analysis.confidence is not None
            and analysis.confidence_margin is not None
            and analysis.confidence >= AUTO_RECOGNITION_MIN_CONFIDENCE
            and analysis.confidence_margin >= AUTO_RECOGNITION_MIN_MARGIN
        ):
            cursor = conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                    (page_id,subject_row_label,recognized_count_text,recognized_count,
                     recognized_count_confidence,resolved_count,count_status,created_at)
                VALUES (?,?,?,?,?,?,'AUTO_RECOGNIZED',?)
                """,
                (
                    page_id, subject_label, str(analysis.recognized_count),
                    analysis.recognized_count, analysis.confidence,
                    analysis.recognized_count, created_at,
                ),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                    (page_id,subject_row_label,recognized_count_text,recognized_count,
                     recognized_count_confidence,count_status,created_at)
                VALUES (?,?,?,?,?,'AMBIGUOUS',?)
                """,
                (
                    page_id, subject_label,
                    str(analysis.recognized_count) if analysis.recognized_count is not None else None,
                    analysis.recognized_count, analysis.confidence, created_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
                    (page_id,item_type,related_subject_enrollment_id,crop_image_path,
                     candidate_value_text,created_at)
                VALUES (?,'COUNT_AMBIGUOUS',?,?,?,?)
                """,
                (
                    page_id,
                    cursor.lastrowid,
                    str(crop_path),
                    analysis.recognition_note or f"筆跡あり（有効画素数={analysis.ink_pixels}）",
                    created_at,
                ),
            )
            review_count += 1
    return review_count


def confirm_subject_counts(
    conn,
    *,
    page_id: int,
    confirmed_counts: dict[int, int],
    operator_instructor_id: int,
) -> int:
    """1ページ分の曖昧な回数を職員確定し、監査ログへ追記する。"""
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
    if not page:
        raise ValueError("対象ページが見つかりません")
    if not confirmed_counts:
        raise ValueError("確定する回数がありません")
    for value in confirmed_counts.values():
        if value < 0 or value > 99:
            raise ValueError("受講回数は0〜99の範囲で入力してください")

    placeholders = ",".join("?" for _ in confirmed_counts)
    rows = conn.execute(
        f"""
        SELECT e.import_enrollment_id,e.subject_row_label,e.count_status,
               r.review_item_id
        FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS e
        JOIN IMAGE_IMPORT_REVIEW_ITEMS r
          ON r.related_subject_enrollment_id=e.import_enrollment_id
         AND r.item_type='COUNT_AMBIGUOUS' AND r.resolution='PENDING' AND r.is_deleted=0
        WHERE e.page_id=? AND e.count_status='AMBIGUOUS' AND e.is_deleted=0
          AND e.import_enrollment_id IN ({placeholders})
        """,
        (page_id, *confirmed_counts.keys()),
    ).fetchall()
    if len(rows) != len(confirmed_counts):
        raise ValueError("対象の一部が既に確定済みか、ページに属していません")

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    operator_name = f"{operator[0]}{operator[1]}"
    try:
        conn.execute("BEGIN IMMEDIATE")
        for enrollment_id, subject_label, old_status, review_item_id in rows:
            value = confirmed_counts[enrollment_id]
            conn.execute(
                """
                UPDATE IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                SET resolved_count=?,count_status='MANUALLY_CONFIRMED',
                    reviewed_by_instructor_id=?,reviewed_at=?
                WHERE import_enrollment_id=?
                """,
                (value, operator_instructor_id, now, enrollment_id),
            )
            conn.execute(
                """
                UPDATE IMAGE_IMPORT_REVIEW_ITEMS
                SET resolution='CORRECTED',corrected_value_text=?,
                    resolved_by_instructor_id=?,resolved_at=?
                WHERE review_item_id=?
                """,
                (str(value), operator_instructor_id, now, review_item_id),
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
                    page[0], page_id, "CORRECT", "IMAGE_IMPORT_SUBJECT_ENROLLMENTS",
                    enrollment_id,
                    json.dumps({"count_status": old_status}, ensure_ascii=False),
                    json.dumps({"resolved_count": value, "count_status": "MANUALLY_CONFIRMED"}, ensure_ascii=False),
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
