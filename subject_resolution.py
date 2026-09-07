"""画像取込の用紙科目行を、既存SUBJECTSの具体科目へ解決する。"""

import datetime
import json

from db import get_grade_at_fiscal_year
from excel_import import resolve_subject


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _grade_band(grade: int) -> str:
    if 1 <= grade <= 3:
        return "小学生低学年"
    if 4 <= grade <= 6:
        return "小学生高学年"
    if 7 <= grade <= 9:
        return "中学生"
    if 10 <= grade <= 12:
        return "高校生"
    raise ValueError(f"用紙年度時点の学年が範囲外です: {grade}")


def _subject_options(conn, grade: int, row_label: str, track: str | None) -> list[dict]:
    """用紙の大分類から選択可能な個別指導科目を返す。"""
    band = _grade_band(grade)
    if row_label == "その他":
        rows = conn.execute(
            """
            SELECT subject_id,subject_group,subject_name,track FROM SUBJECTS
            WHERE course_category='個別指導' AND grade_band=?
            ORDER BY subject_group,subject_name,subject_id
            """,
            (band,),
        ).fetchall()
    elif band == "高校生" and row_label == "数学・算数":
        # 既存Excel取込と同じ、用紙年度時点の学年＋文理による数学判定を再利用する。
        result = resolve_subject(conn, "数学", grade, track)
        ids = [candidate["id"] for candidate in result["candidates"]]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""
            SELECT subject_id,subject_group,subject_name,track FROM SUBJECTS
            WHERE course_category='個別指導' AND subject_id IN ({placeholders})
            ORDER BY subject_name,subject_id
            """,
            ids,
        ).fetchall()
    else:
        group = "算数" if row_label == "数学・算数" and band.startswith("小学生") else (
            "数学" if row_label == "数学・算数" else row_label
        )
        rows = conn.execute(
            """
            SELECT subject_id,subject_group,subject_name,track FROM SUBJECTS
            WHERE course_category='個別指導' AND grade_band=? AND subject_group=?
            ORDER BY subject_name,track,subject_id
            """,
            (band, group),
        ).fetchall()
    return [
        {
            "id": row[0],
            "label": row[2] + (f"（{row[3]}）" if row[3] else ""),
            "group": row[1],
        }
        for row in rows
    ]


def resolve_page_subjects(conn, *, page_id: int) -> dict[str, int]:
    """生徒と正の回数が確定した行だけ、科目を自動確定または確認待ちにする。"""
    context = conn.execute(
        """
        SELECT p.batch_id,b.paper_fiscal_year,ps.candidate_student_id,
               s.enrollment_year,s.base_grade,s.track
        FROM IMAGE_IMPORT_PAGES p
        JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
        JOIN IMAGE_IMPORT_PAGE_STUDENTS ps
          ON ps.page_id=p.page_id AND ps.is_selected=1 AND ps.is_deleted=0
        JOIN STUDENTS s ON s.student_id=ps.candidate_student_id
        WHERE p.page_id=? AND p.is_deleted=0 AND b.is_deleted=0
        """,
        (page_id,),
    ).fetchone()
    if not context:
        return {"matched": 0, "review": 0, "skipped": 0}
    batch_id, paper_fy, _, enrollment_year, base_grade, track = context
    grade = get_grade_at_fiscal_year(enrollment_year, base_grade, paper_fy)
    rows = conn.execute(
        """
        SELECT import_enrollment_id,subject_row_label,resolved_count
        FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS
        WHERE page_id=? AND subject_match_status='PENDING' AND is_deleted=0
        ORDER BY import_enrollment_id
        """,
        (page_id,),
    ).fetchall()
    now = _now_iso()
    counts = {"matched": 0, "review": 0, "skipped": 0}
    try:
        conn.execute("BEGIN IMMEDIATE")
        for enrollment_id, row_label, resolved_count in rows:
            if resolved_count is None or resolved_count <= 0:
                counts["skipped"] += 1
                continue
            options = _subject_options(conn, grade, row_label, track)
            # 「その他」は印刷ラベルだけでは具体科目を確定できない。
            if len(options) == 1 and row_label != "その他":
                subject_id = options[0]["id"]
                conn.execute(
                    """
                    UPDATE IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                    SET recognized_subject_id=?,resolved_subject_id=?,subject_match_status='AUTO_MATCHED'
                    WHERE import_enrollment_id=?
                    """,
                    (subject_id, subject_id, enrollment_id),
                )
                counts["matched"] += 1
            else:
                status = "AMBIGUOUS" if options else "NOT_FOUND"
                conn.execute(
                    "UPDATE IMAGE_IMPORT_SUBJECT_ENROLLMENTS SET subject_match_status=? WHERE import_enrollment_id=?",
                    (status, enrollment_id),
                )
                conn.execute(
                    """
                    INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
                        (page_id,item_type,related_subject_enrollment_id,candidate_value_text,created_at)
                    VALUES (?,'SUBJECT_MATCH',?,?,?)
                    """,
                    (page_id, enrollment_id, json.dumps(options, ensure_ascii=False), now),
                )
                counts["review"] += 1
        if counts["review"]:
            conn.execute(
                "UPDATE IMAGE_IMPORT_BATCHES SET status='REVIEW_PENDING' WHERE batch_id=? AND status<>'IMPORTED'",
                (batch_id,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return counts


def list_manual_subject_options(conn, *, page_id: int) -> list[tuple]:
    """手動訂正では、同じ学年帯の個別指導科目をすべて提示する。"""
    context = conn.execute(
        """
        SELECT b.paper_fiscal_year,s.enrollment_year,s.base_grade
        FROM IMAGE_IMPORT_PAGES p
        JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
        JOIN IMAGE_IMPORT_PAGE_STUDENTS ps
          ON ps.page_id=p.page_id AND ps.is_selected=1 AND ps.is_deleted=0
        JOIN STUDENTS s ON s.student_id=ps.candidate_student_id
        WHERE p.page_id=? AND p.is_deleted=0 AND b.is_deleted=0
        """,
        (page_id,),
    ).fetchone()
    if not context:
        return []
    grade = get_grade_at_fiscal_year(context[1], context[2], context[0])
    return conn.execute(
        """
        SELECT subject_id,subject_group,subject_name,track FROM SUBJECTS
        WHERE course_category='個別指導' AND grade_band=?
        ORDER BY subject_group,subject_name,track,subject_id
        """,
        (_grade_band(grade),),
    ).fetchall()


def confirm_page_subjects(
    conn,
    *,
    page_id: int,
    confirmed_subjects: dict[int, int],
    operator_instructor_id: int,
) -> int:
    """曖昧な科目を職員確定し、元の確認項目と監査ログを確定する。"""
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
    if not page or not confirmed_subjects:
        raise ValueError("確定する科目がありません")
    allowed = {row[0] for row in list_manual_subject_options(conn, page_id=page_id)}
    if any(subject_id not in allowed for subject_id in confirmed_subjects.values()):
        raise ValueError("この生徒の学年に対応しない科目が含まれています")
    placeholders = ",".join("?" for _ in confirmed_subjects)
    rows = conn.execute(
        f"""
        SELECT e.import_enrollment_id,e.subject_row_label,e.subject_match_status,r.review_item_id
        FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS e
        JOIN IMAGE_IMPORT_REVIEW_ITEMS r
          ON r.related_subject_enrollment_id=e.import_enrollment_id
         AND r.item_type='SUBJECT_MATCH' AND r.resolution='PENDING' AND r.is_deleted=0
        WHERE e.page_id=? AND e.subject_match_status IN ('AMBIGUOUS','NOT_FOUND')
          AND e.is_deleted=0 AND e.import_enrollment_id IN ({placeholders})
        """,
        (page_id, *confirmed_subjects.keys()),
    ).fetchall()
    if len(rows) != len(confirmed_subjects):
        raise ValueError("対象の一部が既に確定済みか、このページに属していません")
    now = _now_iso()
    operator_name = f"{operator[0]}{operator[1]}"
    try:
        conn.execute("BEGIN IMMEDIATE")
        for enrollment_id, row_label, old_status, review_item_id in rows:
            subject_id = confirmed_subjects[enrollment_id]
            subject = conn.execute(
                "SELECT subject_group,subject_name FROM SUBJECTS WHERE subject_id=?",
                (subject_id,),
            ).fetchone()
            chosen_label = f"{subject[0]}/{subject[1]}"
            conn.execute(
                """
                UPDATE IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                SET resolved_subject_id=?,subject_match_status='MANUALLY_CONFIRMED',
                    reviewed_by_instructor_id=?,reviewed_at=?
                WHERE import_enrollment_id=?
                """,
                (subject_id, operator_instructor_id, now, enrollment_id),
            )
            conn.execute(
                """
                UPDATE IMAGE_IMPORT_REVIEW_ITEMS
                SET resolution='CORRECTED',corrected_value_text=?,
                    resolved_by_instructor_id=?,resolved_at=?
                WHERE review_item_id=?
                """,
                (chosen_label, operator_instructor_id, now, review_item_id),
            )
            conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_AUDIT_LOG
                    (batch_id,page_id,action_type,target_table,target_id,before_value_json,
                     after_value_json,actor_type,operator_instructor_id,operator_name,
                     school_id,school_name,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    page[0], page_id, "CORRECT", "IMAGE_IMPORT_SUBJECT_ENROLLMENTS", enrollment_id,
                    json.dumps({"row_label": row_label, "subject_match_status": old_status}, ensure_ascii=False),
                    json.dumps({"resolved_subject_id": subject_id, "subject_match_status": "MANUALLY_CONFIRMED"}, ensure_ascii=False),
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
        conn.execute(
            "UPDATE IMAGE_IMPORT_BATCHES SET status=? WHERE batch_id=? AND status<>'IMPORTED'",
            ("REVIEW_PENDING" if pending else "REVIEWED", page[0]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(rows)
