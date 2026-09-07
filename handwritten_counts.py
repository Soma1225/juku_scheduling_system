"""手書き受講回数セルの空欄判定と候補保存。"""

import datetime
import json
from dataclasses import dataclass
from pathlib import Path


MIN_INK_COMPONENT_AREA = 8
BLANK_MAX_INK_PIXELS = 40


@dataclass(frozen=True)
class CountCellAnalysis:
    is_blank: bool
    ink_pixels: int
    component_count: int
    recognized_count: int | None = None
    confidence: float | None = None


def analyze_count_cell(image_path: Path) -> CountCellAnalysis:
    """小さな汚れを除外し、実質的な筆跡があるかを判定する。"""
    import cv2
    import numpy as np

    gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"回数セル画像を読み込めません: {image_path}")
    mask = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    cleaned = np.zeros_like(mask)
    component_count = 0
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area >= MIN_INK_COMPONENT_AREA:
            cleaned[labels == index] = 255
            component_count += 1
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
        analysis = analyze_count_cell(crop_path)
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
        else:
            cursor = conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                    (page_id,subject_row_label,count_status,created_at)
                VALUES (?,?,'AMBIGUOUS',?)
                """,
                (page_id, subject_label, created_at),
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
                    f"筆跡あり（有効画素数={analysis.ink_pixels}）",
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
