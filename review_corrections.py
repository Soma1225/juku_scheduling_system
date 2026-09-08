"""確定済み画像レビューを、元行を変更せず新しい履歴として訂正する。"""

import datetime
import json

from subject_resolution import list_manual_subject_options, resolve_page_subjects


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _operator(conn, operator_instructor_id: int):
    row = conn.execute(
        "SELECT last_name,first_name FROM INSTRUCTORS "
        "WHERE instructor_id=? AND status='在籍'",
        (operator_instructor_id,),
    ).fetchone()
    if not row:
        raise ValueError("在籍中の担当講師を選択してください")
    return f"{row[0]}{row[1]}"


def _load_original(conn, review_item_id: int):
    row = conn.execute(
        """
        SELECT r.review_item_id,r.page_id,r.item_type,
               r.related_page_student_id,r.related_subject_enrollment_id,
               r.related_availability_id,r.crop_image_path,r.candidate_value_text,
               r.resolution,p.batch_id,b.status,b.school_id,b.school_name
        FROM IMAGE_IMPORT_REVIEW_ITEMS r
        JOIN IMAGE_IMPORT_PAGES p ON p.page_id=r.page_id
        JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
        WHERE r.review_item_id=? AND r.is_deleted=0
          AND p.is_deleted=0 AND b.is_deleted=0
        """,
        (review_item_id,),
    ).fetchone()
    if not row:
        raise ValueError("訂正元の確認項目が見つかりません")
    if row[8] == "PENDING":
        raise ValueError("未確定項目は通常の確認画面で確定してください")
    if row[10] == "IMPORTED":
        raise ValueError("本登録済みバッチはこの画面から訂正できません")

    newer = conn.execute(
        """
        SELECT 1 FROM IMAGE_IMPORT_REVIEW_ITEMS n
        WHERE n.page_id=? AND n.item_type=? AND n.review_item_id>?
          AND n.is_deleted=0 AND (
            (?='STUDENT_MATCH') OR
            (? IS NOT NULL AND n.related_subject_enrollment_id=?) OR
            (? IS NOT NULL AND n.related_availability_id=?)
          )
        LIMIT 1
        """,
        (row[1], row[2], row[0], row[2], row[4], row[4], row[5], row[5]),
    ).fetchone()
    if newer:
        raise ValueError("この項目には新しい判断履歴があります。最新の履歴を訂正してください")
    return row


def correct_resolved_review(
    conn,
    *,
    review_item_id: int,
    replacement_value: int,
    reason: str,
    operator_instructor_id: int,
) -> int:
    """確定済みレビューを訂正し、新レビュー行と監査ログを追記する。

    replacement_value は項目種別ごとに、生徒ID・回数・科目ID・可否(0/1)を表す。
    元のレビュー行は一切更新しない。
    """
    reason = reason.strip()
    if not reason:
        raise ValueError("訂正理由を入力してください")
    if len(reason) > 1000:
        raise ValueError("訂正理由は1000文字以内で入力してください")
    operator_name = _operator(conn, operator_instructor_id)
    original = _load_original(conn, review_item_id)
    (
        _, page_id, item_type, related_student_id, related_enrollment_id,
        related_availability_id, crop_path, candidate_text, _, batch_id,
        _, school_id, school_name,
    ) = original
    now = _now_iso()
    before = {"superseded_review_item_id": review_item_id, "correction_reason": reason}
    after = {"correction_reason": reason}
    corrected_text = ""

    try:
        conn.execute("BEGIN IMMEDIATE")
        if item_type == "STUDENT_MATCH":
            student = conn.execute(
                "SELECT last_name,first_name FROM STUDENTS "
                "WHERE student_id=? AND enrollment_status='在籍'",
                (replacement_value,),
            ).fetchone()
            if not student:
                raise ValueError("在籍中の生徒を選択してください")
            selected = conn.execute(
                """
                SELECT page_student_id,candidate_student_id FROM IMAGE_IMPORT_PAGE_STUDENTS
                WHERE page_id=? AND is_selected=1 AND is_deleted=0
                """,
                (page_id,),
            ).fetchone()
            if not selected:
                raise ValueError("現在確定している生徒が見つかりません")
            chosen = conn.execute(
                """
                SELECT page_student_id FROM IMAGE_IMPORT_PAGE_STUDENTS
                WHERE page_id=? AND candidate_student_id=? AND is_deleted=0
                """,
                (page_id, replacement_value),
            ).fetchone()
            conn.execute(
                """
                UPDATE IMAGE_IMPORT_PAGE_STUDENTS
                SET match_status='AMBIGUOUS',is_selected=0,
                    reviewed_by_instructor_id=NULL,reviewed_at=NULL
                WHERE page_student_id=?
                """,
                (selected[0],),
            )
            if chosen:
                new_related_student_id = chosen[0]
                conn.execute(
                    """
                    UPDATE IMAGE_IMPORT_PAGE_STUDENTS
                    SET match_status='MANUALLY_CONFIRMED',is_selected=1,
                        reviewed_by_instructor_id=?,reviewed_at=?
                    WHERE page_student_id=?
                    """,
                    (operator_instructor_id, now, new_related_student_id),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
                        (page_id,candidate_student_id,match_status,is_selected,
                         reviewed_by_instructor_id,reviewed_at,created_at)
                    VALUES (?,?,'MANUALLY_CONFIRMED',1,?,?,?)
                    """,
                    (page_id, replacement_value, operator_instructor_id, now, now),
                )
                new_related_student_id = cursor.lastrowid
            corrected_text = f"{student[0]}{student[1]}"
            before.update(page_student_id=selected[0], student_id=selected[1])
            after.update(page_student_id=new_related_student_id, student_id=replacement_value)
            related_student_id = new_related_student_id

            # 学年帯が変わる可能性があるため、科目対応だけ再解決する。
            conn.execute(
                """
                UPDATE IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                SET recognized_subject_id=NULL,resolved_subject_id=NULL,subject_match_status='PENDING'
                WHERE page_id=? AND COALESCE(resolved_count,0)>0 AND is_deleted=0
                """,
                (page_id,),
            )
            resolve_page_subjects(conn, page_id=page_id, manage_transaction=False)
        elif item_type == "COUNT_AMBIGUOUS":
            if replacement_value < 0 or replacement_value > 99:
                raise ValueError("受講回数は0〜99の範囲で入力してください")
            target = conn.execute(
                """
                SELECT resolved_count,count_status FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                WHERE import_enrollment_id=? AND page_id=? AND is_deleted=0
                """,
                (related_enrollment_id, page_id),
            ).fetchone()
            if not target:
                raise ValueError("訂正対象の受講回数が見つかりません")
            conn.execute(
                """
                UPDATE IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                SET resolved_count=?,count_status='MANUALLY_CONFIRMED',
                    reviewed_by_instructor_id=?,reviewed_at=?
                WHERE import_enrollment_id=?
                """,
                (replacement_value, operator_instructor_id, now, related_enrollment_id),
            )
            if replacement_value == 0:
                conn.execute(
                    """
                    UPDATE IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                    SET recognized_subject_id=NULL,resolved_subject_id=NULL,subject_match_status='PENDING'
                    WHERE import_enrollment_id=?
                    """,
                    (related_enrollment_id,),
                )
            else:
                subject_status = conn.execute(
                    "SELECT subject_match_status FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS WHERE import_enrollment_id=?",
                    (related_enrollment_id,),
                ).fetchone()[0]
                if subject_status == "PENDING":
                    resolve_page_subjects(conn, page_id=page_id, manage_transaction=False)
            corrected_text = str(replacement_value)
            before.update(resolved_count=target[0], count_status=target[1])
            after.update(resolved_count=replacement_value, count_status="MANUALLY_CONFIRMED")
        elif item_type == "SUBJECT_MATCH":
            allowed = {row[0]: row for row in list_manual_subject_options(conn, page_id=page_id)}
            if replacement_value not in allowed:
                raise ValueError("この生徒の学年に対応する科目を選択してください")
            target = conn.execute(
                """
                SELECT resolved_subject_id,subject_match_status
                FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                WHERE import_enrollment_id=? AND page_id=? AND is_deleted=0
                """,
                (related_enrollment_id, page_id),
            ).fetchone()
            if not target:
                raise ValueError("訂正対象の科目が見つかりません")
            conn.execute(
                """
                UPDATE IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                SET resolved_subject_id=?,subject_match_status='MANUALLY_CONFIRMED',
                    reviewed_by_instructor_id=?,reviewed_at=?
                WHERE import_enrollment_id=?
                """,
                (replacement_value, operator_instructor_id, now, related_enrollment_id),
            )
            option = allowed[replacement_value]
            corrected_text = f"{option[1]}/{option[2]}"
            before.update(resolved_subject_id=target[0], subject_match_status=target[1])
            after.update(resolved_subject_id=replacement_value, subject_match_status="MANUALLY_CONFIRMED")
        elif item_type in ("AVAILABILITY_AMBIGUOUS", "SLOT_NOT_FOUND"):
            if replacement_value not in (0, 1):
                raise ValueError("対応可または対応不可を選択してください")
            target = conn.execute(
                """
                SELECT resolved_is_available,availability_status
                FROM IMAGE_IMPORT_AVAILABILITY
                WHERE import_availability_id=? AND page_id=? AND is_deleted=0
                """,
                (related_availability_id, page_id),
            ).fetchone()
            if not target:
                raise ValueError("訂正対象の対応可能時間が見つかりません")
            conn.execute(
                """
                UPDATE IMAGE_IMPORT_AVAILABILITY
                SET resolved_is_available=?,availability_status='MANUALLY_CONFIRMED',
                    reviewed_by_instructor_id=?,reviewed_at=?
                WHERE import_availability_id=?
                """,
                (replacement_value, operator_instructor_id, now, related_availability_id),
            )
            corrected_text = "対応可" if replacement_value else "対応不可"
            before.update(resolved_is_available=target[0], availability_status=target[1])
            after.update(resolved_is_available=replacement_value, availability_status="MANUALLY_CONFIRMED")
        else:
            raise ValueError("この種類の項目は訂正画面の対象外です")

        cursor = conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
                (page_id,item_type,related_page_student_id,
                 related_subject_enrollment_id,related_availability_id,
                 crop_image_path,candidate_value_text,resolution,corrected_value_text,
                 resolved_by_instructor_id,resolved_at,created_at)
            VALUES (?,?,?,?,?,?,?,'CORRECTED',?,?,?,?)
            """,
            (
                page_id, item_type, related_student_id, related_enrollment_id,
                related_availability_id, crop_path, candidate_text, corrected_text,
                operator_instructor_id, now, now,
            ),
        )
        new_review_item_id = cursor.lastrowid
        after["review_item_id"] = new_review_item_id
        conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_AUDIT_LOG
                (batch_id,page_id,action_type,target_table,target_id,
                 before_value_json,after_value_json,actor_type,
                 operator_instructor_id,operator_name,school_id,school_name,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                batch_id, page_id, "CORRECT", "IMAGE_IMPORT_REVIEW_ITEMS",
                new_review_item_id, json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False), "INSTRUCTOR",
                operator_instructor_id, operator_name, school_id, school_name, now,
            ),
        )
        pending = conn.execute(
            """
            SELECT COUNT(*) FROM IMAGE_IMPORT_REVIEW_ITEMS r
            JOIN IMAGE_IMPORT_PAGES p ON p.page_id=r.page_id
            WHERE p.batch_id=? AND r.resolution='PENDING' AND r.is_deleted=0
              AND p.is_deleted=0
            """,
            (batch_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE IMAGE_IMPORT_BATCHES SET status=? WHERE batch_id=?",
            ("REVIEW_PENDING" if pending else "REVIEWED", batch_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return new_review_item_id
