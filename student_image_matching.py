"""氏名・学年の画像照合結果を、生徒候補テーブルへ保存する。"""

import datetime
import json
from pathlib import Path

from db import format_grade_label, get_grade_at_fiscal_year
from visual_text_match import (
    compare_masks,
    normalize_observed_text,
    rank_visual_candidates,
    render_candidate_mask,
)


AUTO_MATCH_MIN_SCORE = 0.80
AUTO_MATCH_MIN_MARGIN = 0.12
MAX_STUDENT_CANDIDATES = 5


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def recognize_student_candidates(
    conn,
    *,
    page_id: int,
    name_crop_path: Path,
    grade_crop_path: Path,
    paper_fiscal_year: int,
    layout_quality: str,
) -> tuple[int | None, list[tuple[int, str, float]]]:
    """在籍生徒を画像照合し、候補と必要なレビュー項目を保存する。"""
    students = conn.execute(
        """
        SELECT student_id,last_name,first_name,enrollment_year,base_grade
        FROM STUDENTS WHERE enrollment_status='在籍' ORDER BY student_id
        """
    ).fetchall()
    created_at = _now_iso()
    if not students:
        cursor = conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
                (page_id,match_status,is_selected,created_at)
            VALUES (?,'NOT_FOUND',0,?)
            """,
            (page_id, created_at),
        )
        conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
                (page_id,item_type,related_page_student_id,crop_image_path,
                 candidate_value_text,created_at)
            VALUES (?,'STUDENT_MATCH',?,?,?,?)
            """,
            (page_id, cursor.lastrowid, str(name_crop_path), "候補なし", created_at),
        )
        return None, []

    name_candidates = [
        (row[0], f"{row[1]}{row[2]}")
        for row in students
    ]
    name_matches = {
        match.candidate_id: match.score
        for match in rank_visual_candidates(name_crop_path, name_candidates)
    }
    observed_grade = normalize_observed_text(grade_crop_path)
    ranked = []
    for student_id, last_name, first_name, enrollment_year, base_grade in students:
        grade = get_grade_at_fiscal_year(enrollment_year, base_grade, paper_fiscal_year)
        grade_label = format_grade_label(grade)
        grade_score = compare_masks(observed_grade, render_candidate_mask(grade_label))
        combined_score = name_matches[student_id] * 0.75 + grade_score * 0.25
        ranked.append((student_id, f"{last_name}{first_name}", combined_score, grade_label))
    ranked.sort(key=lambda item: item[2], reverse=True)

    top_score = ranked[0][2]
    second_score = ranked[1][2] if len(ranked) > 1 else 0.0
    auto_selected = (
        layout_quality == "OK"
        and top_score >= AUTO_MATCH_MIN_SCORE
        and top_score - second_score >= AUTO_MATCH_MIN_MARGIN
    )
    selected_student_id = ranked[0][0] if auto_selected else None
    candidate_rows = []
    top_page_student_id = None
    for rank, (student_id, label, score, grade_label) in enumerate(
        ranked[:MAX_STUDENT_CANDIDATES], start=1
    ):
        is_top = rank == 1
        status = "AUTO_MATCHED" if auto_selected and is_top else "AMBIGUOUS"
        cursor = conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
                (page_id,candidate_student_id,candidate_rank,match_confidence,
                 match_status,is_selected,created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (page_id, student_id, rank, score, status,
             1 if auto_selected and is_top else 0, created_at),
        )
        if is_top:
            top_page_student_id = cursor.lastrowid
        candidate_rows.append((student_id, label, score))

    conn.execute(
        """
        UPDATE IMAGE_IMPORT_PAGES
        SET recognized_name_text=?,recognized_grade_text=?,processing_status='RECOGNIZED'
        WHERE page_id=?
        """,
        (ranked[0][1], ranked[0][3], page_id),
    )
    if not auto_selected:
        conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
                (page_id,item_type,related_page_student_id,crop_image_path,
                 candidate_value_text,created_at)
            VALUES (?,'STUDENT_MATCH',?,?,?,?)
            """,
            (
                page_id,
                top_page_student_id,
                str(name_crop_path),
                f"{ranked[0][1]} / {ranked[0][3]} / score={top_score:.3f}",
                created_at,
            ),
        )
    return selected_student_id, candidate_rows


def confirm_student_match(
    conn,
    *,
    page_id: int,
    page_student_id: int,
    operator_instructor_id: int,
) -> int:
    """職員が選んだ生徒候補を確定し、レビューと監査履歴も更新する。"""
    operator = conn.execute(
        """
        SELECT last_name,first_name FROM INSTRUCTORS
        WHERE instructor_id=? AND status='在籍'
        """,
        (operator_instructor_id,),
    ).fetchone()
    if not operator:
        raise ValueError("在籍中の担当講師を選択してください")
    page = conn.execute(
        """
        SELECT p.batch_id,p.layout_quality,b.school_id,b.school_name
        FROM IMAGE_IMPORT_PAGES p
        JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
        WHERE p.page_id=? AND p.is_deleted=0 AND b.is_deleted=0
        """,
        (page_id,),
    ).fetchone()
    if not page:
        raise ValueError("対象ページが見つかりません")
    chosen = conn.execute(
        """
        SELECT ps.candidate_student_id,ps.match_status,ps.is_selected,
               s.last_name||s.first_name
        FROM IMAGE_IMPORT_PAGE_STUDENTS ps
        JOIN STUDENTS s ON s.student_id=ps.candidate_student_id
        WHERE ps.page_student_id=? AND ps.page_id=? AND ps.is_deleted=0
        """,
        (page_student_id, page_id),
    ).fetchone()
    if not chosen:
        raise ValueError("選択された生徒候補が見つかりません")

    existing_selected = conn.execute(
        """
        SELECT page_student_id,candidate_student_id,match_status
        FROM IMAGE_IMPORT_PAGE_STUDENTS
        WHERE page_id=? AND is_selected=1 AND is_deleted=0
        """,
        (page_id,),
    ).fetchone()
    if existing_selected and existing_selected[0] == page_student_id:
        return chosen[0]
    if existing_selected:
        raise ValueError("このページの生徒は既に確定済みです。訂正は新しい確認項目として行ってください")

    review = conn.execute(
        """
        SELECT review_item_id,related_page_student_id
        FROM IMAGE_IMPORT_REVIEW_ITEMS
        WHERE page_id=? AND item_type='STUDENT_MATCH'
          AND resolution='PENDING' AND is_deleted=0
        ORDER BY review_item_id LIMIT 1
        """,
        (page_id,),
    ).fetchone()
    if not review:
        raise ValueError("未処理の生徒確認項目が見つかりません")

    now = _now_iso()
    operator_name = f"{operator[0]}{operator[1]}"
    before_value = {
        "page_student_id": page_student_id,
        "candidate_student_id": chosen[0],
        "match_status": chosen[1],
        "is_selected": chosen[2],
    }
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE IMAGE_IMPORT_PAGE_STUDENTS
            SET match_status='MANUALLY_CONFIRMED',is_selected=1,
                reviewed_by_instructor_id=?,reviewed_at=?
            WHERE page_student_id=?
            """,
            (operator_instructor_id, now, page_student_id),
        )
        resolution = "APPROVED" if review[1] == page_student_id else "CORRECTED"
        corrected_value = None if resolution == "APPROVED" else chosen[3]
        conn.execute(
            """
            UPDATE IMAGE_IMPORT_REVIEW_ITEMS
            SET resolution=?,corrected_value_text=?,resolved_by_instructor_id=?,resolved_at=?
            WHERE review_item_id=?
            """,
            (resolution, corrected_value, operator_instructor_id, now, review[0]),
        )
        from camp_form_tracking import record_scanned_return
        record_scanned_return(conn, page_id=page_id, student_id=chosen[0])
        after_value = dict(before_value)
        after_value.update(
            match_status="MANUALLY_CONFIRMED",
            is_selected=1,
            reviewed_by_instructor_id=operator_instructor_id,
            reviewed_at=now,
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
                page[0], page_id, "APPROVE" if resolution == "APPROVED" else "CORRECT",
                "IMAGE_IMPORT_PAGE_STUDENTS", page_student_id,
                json.dumps(before_value, ensure_ascii=False),
                json.dumps(after_value, ensure_ascii=False),
                "INSTRUCTOR", operator_instructor_id, operator_name, page[2], page[3], now,
            ),
        )
        pending_count = conn.execute(
            """
            SELECT COUNT(*) FROM IMAGE_IMPORT_REVIEW_ITEMS r
            JOIN IMAGE_IMPORT_PAGES p ON p.page_id=r.page_id
            WHERE p.batch_id=? AND r.resolution='PENDING' AND r.is_deleted=0
            """,
            (page[0],),
        ).fetchone()[0]
        bad_quality_count = conn.execute(
            """
            SELECT COUNT(*) FROM IMAGE_IMPORT_PAGES
            WHERE batch_id=? AND layout_quality<>'OK' AND is_deleted=0
            """,
            (page[0],),
        ).fetchone()[0]
        conn.execute(
            "UPDATE IMAGE_IMPORT_BATCHES SET status=? WHERE batch_id=?",
            ("REVIEW_PENDING" if pending_count or bad_quality_count else "PROCESSING", page[0]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return chosen[0]


def confirm_student_by_master(
    conn,
    *,
    page_id: int,
    student_id: int,
    operator_instructor_id: int,
) -> int:
    """画像候補に出なかった生徒を、在籍生徒マスタから職員が直接確定する。"""
    existing_candidate = conn.execute(
        """
        SELECT page_student_id FROM IMAGE_IMPORT_PAGE_STUDENTS
        WHERE page_id=? AND candidate_student_id=? AND is_deleted=0
        """,
        (page_id, student_id),
    ).fetchone()
    if existing_candidate:
        return confirm_student_match(
            conn,
            page_id=page_id,
            page_student_id=existing_candidate[0],
            operator_instructor_id=operator_instructor_id,
        )
    operator = conn.execute(
        "SELECT last_name,first_name FROM INSTRUCTORS WHERE instructor_id=? AND status='在籍'",
        (operator_instructor_id,),
    ).fetchone()
    if not operator:
        raise ValueError("在籍中の担当講師を選択してください")
    student = conn.execute(
        """
        SELECT last_name,first_name FROM STUDENTS
        WHERE student_id=? AND enrollment_status='在籍'
        """,
        (student_id,),
    ).fetchone()
    if not student:
        raise ValueError("在籍中の生徒を選択してください")
    page = conn.execute(
        """
        SELECT p.batch_id,p.processing_status,b.status,b.school_id,b.school_name
        FROM IMAGE_IMPORT_PAGES p JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
        WHERE p.page_id=? AND p.is_deleted=0 AND b.is_deleted=0
        """,
        (page_id,),
    ).fetchone()
    if not page:
        raise ValueError("対象ページが見つかりません")
    if page[1] == "SKIPPED" or page[2] == "IMPORTED":
        raise ValueError("除外済みまたは本登録済みのページでは生徒を変更できません")
    if conn.execute(
        "SELECT 1 FROM IMAGE_IMPORT_PAGE_STUDENTS WHERE page_id=? AND is_selected=1 AND is_deleted=0",
        (page_id,),
    ).fetchone():
        raise ValueError("このページの生徒は既に確定済みです")
    review = conn.execute(
        """
        SELECT review_item_id FROM IMAGE_IMPORT_REVIEW_ITEMS
        WHERE page_id=? AND item_type='STUDENT_MATCH' AND resolution='PENDING' AND is_deleted=0
        ORDER BY review_item_id LIMIT 1
        """,
        (page_id,),
    ).fetchone()
    if not review:
        raise ValueError("未処理の生徒確認項目が見つかりません")
    now = _now_iso()
    student_name = f"{student[0]}{student[1]}"
    operator_name = f"{operator[0]}{operator[1]}"
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
                (page_id,candidate_student_id,match_status,is_selected,
                 reviewed_by_instructor_id,reviewed_at,created_at)
            VALUES (?,?,'MANUALLY_CONFIRMED',1,?,?,?)
            """,
            (page_id, student_id, operator_instructor_id, now, now),
        )
        page_student_id = cursor.lastrowid
        conn.execute(
            """
            UPDATE IMAGE_IMPORT_REVIEW_ITEMS
            SET resolution='CORRECTED',corrected_value_text=?,
                resolved_by_instructor_id=?,resolved_at=?
            WHERE review_item_id=?
            """,
            (student_name, operator_instructor_id, now, review[0]),
        )
        from camp_form_tracking import record_scanned_return
        record_scanned_return(conn, page_id=page_id, student_id=student_id)
        conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_AUDIT_LOG
                (batch_id,page_id,action_type,target_table,target_id,after_value_json,
                 actor_type,operator_instructor_id,operator_name,school_id,school_name,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                page[0], page_id, "CORRECT", "IMAGE_IMPORT_PAGE_STUDENTS", page_student_id,
                json.dumps({
                    "candidate_student_id": student_id,
                    "match_status": "MANUALLY_CONFIRMED",
                    "is_selected": 1,
                }, ensure_ascii=False),
                "INSTRUCTOR", operator_instructor_id, operator_name, page[3], page[4], now,
            ),
        )
        pending = conn.execute(
            """
            SELECT COUNT(*) FROM IMAGE_IMPORT_REVIEW_ITEMS r
            JOIN IMAGE_IMPORT_PAGES p ON p.page_id=r.page_id
            WHERE p.batch_id=? AND p.processing_status<>'SKIPPED'
              AND r.resolution='PENDING' AND r.is_deleted=0 AND p.is_deleted=0
            """,
            (page[0],),
        ).fetchone()[0]
        bad_quality = conn.execute(
            """
            SELECT COUNT(*) FROM IMAGE_IMPORT_PAGES
            WHERE batch_id=? AND processing_status<>'SKIPPED'
              AND layout_quality<>'OK' AND is_deleted=0
            """,
            (page[0],),
        ).fetchone()[0]
        conn.execute(
            "UPDATE IMAGE_IMPORT_BATCHES SET status=? WHERE batch_id=?",
            ("REVIEW_PENDING" if pending or bad_quality else "REVIEWED", page[0]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return student_id
