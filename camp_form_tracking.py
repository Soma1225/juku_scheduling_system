"""講習会用紙の配布対象作成と回収状況管理。"""

import datetime
import sqlite3


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _active_operator(conn, instructor_id: int):
    row = conn.execute(
        "SELECT last_name,first_name FROM INSTRUCTORS WHERE instructor_id=? AND status='在籍'",
        (instructor_id,),
    ).fetchone()
    if not row:
        raise ValueError("在籍中の担当講師を選択してください")
    return row


def sync_regular_targets(conn, *, camp_id: int, operator_instructor_id: int) -> int:
    """現在有効な個別指導契約を持つ在籍生徒を、未登録分だけ配布候補へ追加する。"""
    _active_operator(conn, operator_instructor_id)
    if not conn.execute("SELECT 1 FROM CAMPS WHERE camp_id=?", (camp_id,)).fetchone():
        raise ValueError("講習会が見つかりません")
    today = datetime.date.today().isoformat()
    student_ids = [
        row[0]
        for row in conn.execute(
            """
            SELECT DISTINCT s.student_id
            FROM STUDENTS s
            JOIN REGULAR_COURSE_ENROLLMENTS e ON e.student_id=s.student_id
            JOIN SUBJECTS sub ON sub.subject_id=e.subject_id
            WHERE s.enrollment_status='在籍'
              AND sub.course_category='個別指導'
              AND e.effective_start_date<=?
              AND (e.effective_end_date IS NULL OR e.effective_end_date>=?)
            ORDER BY s.student_id
            """,
            (today, today),
        ).fetchall()
    ]
    now = _now_iso()
    before = conn.total_changes
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            """
            INSERT OR IGNORE INTO CAMP_FORM_DISTRIBUTIONS
                (camp_id,student_id,source,status,created_by_instructor_id,
                 updated_by_instructor_id,created_at,updated_at)
            VALUES (?,?,'AUTO_REGULAR','TARGET',?,?,?,?)
            """,
            [(camp_id, student_id, operator_instructor_id, operator_instructor_id, now, now)
             for student_id in student_ids],
        )
        created = conn.total_changes - before
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return created


def add_manual_target(
    conn, *, camp_id: int, student_id: int, operator_instructor_id: int
) -> int:
    """戦略指導のみ等の例外生徒を配布対象へ追加する。"""
    _active_operator(conn, operator_instructor_id)
    student = conn.execute(
        "SELECT enrollment_status FROM STUDENTS WHERE student_id=?", (student_id,)
    ).fetchone()
    if not student or student[0] != "在籍":
        raise ValueError("在籍中の生徒を選択してください")
    now = _now_iso()
    try:
        cur = conn.execute(
            """
            INSERT INTO CAMP_FORM_DISTRIBUTIONS
                (camp_id,student_id,source,status,created_by_instructor_id,
                 updated_by_instructor_id,created_at,updated_at)
            VALUES (?,?,'MANUAL_EXCEPTION','TARGET',?,?,?,?)
            """,
            (camp_id, student_id, operator_instructor_id, operator_instructor_id, now, now),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ValueError("この生徒は既に配布一覧へ登録されています") from exc
    return cur.lastrowid


def mark_all_distributed(conn, *, camp_id: int, operator_instructor_id: int) -> int:
    _active_operator(conn, operator_instructor_id)
    now = _now_iso()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            UPDATE CAMP_FORM_DISTRIBUTIONS
            SET status='DISTRIBUTED',distributed_at=?,updated_by_instructor_id=?,updated_at=?
            WHERE camp_id=? AND status='TARGET' AND is_deleted=0
            """,
            (now, operator_instructor_id, now, camp_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return cur.rowcount


def update_distribution_status(
    conn, *, distribution_id: int, status: str, operator_instructor_id: int
) -> None:
    _active_operator(conn, operator_instructor_id)
    if status not in ("DISTRIBUTED", "RETURNED", "EXEMPT"):
        raise ValueError("不正な配布状態です")
    row = conn.execute(
        "SELECT status,returned_page_id FROM CAMP_FORM_DISTRIBUTIONS WHERE distribution_id=? AND is_deleted=0",
        (distribution_id,),
    ).fetchone()
    if not row:
        raise ValueError("配布記録が見つかりません")
    if status == "EXEMPT" and row[1] is not None:
        raise ValueError("スキャン済みの用紙を配布対象外にはできません")
    now = _now_iso()
    distributed_at = now if status == "DISTRIBUTED" else None
    returned_at = now if status == "RETURNED" else None
    if status == "RETURNED" and row[0] == "DISTRIBUTED":
        distributed_at = conn.execute(
            "SELECT distributed_at FROM CAMP_FORM_DISTRIBUTIONS WHERE distribution_id=?",
            (distribution_id,),
        ).fetchone()[0]
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE CAMP_FORM_DISTRIBUTIONS
            SET status=?,distributed_at=?,returned_at=?,updated_by_instructor_id=?,updated_at=?
            WHERE distribution_id=?
            """,
            (status, distributed_at, returned_at, operator_instructor_id, now, distribution_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def record_scanned_return(conn, *, page_id: int, student_id: int) -> None:
    """生徒確定済みのスキャンページを、該当講習会の回収記録へ反映する。"""
    row = conn.execute(
        """
        SELECT b.camp_id FROM IMAGE_IMPORT_PAGES p
        JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
        WHERE p.page_id=? AND p.is_deleted=0 AND b.is_deleted=0
        """,
        (page_id,),
    ).fetchone()
    if not row:
        raise ValueError("スキャンページが見つかりません")
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO CAMP_FORM_DISTRIBUTIONS
            (camp_id,student_id,source,status,returned_at,returned_page_id,created_at,updated_at)
        VALUES (?,?,'SCAN_DISCOVERED','RETURNED',?,?,?,?)
        ON CONFLICT(camp_id,student_id) DO UPDATE SET
            status='RETURNED',returned_at=excluded.returned_at,
            returned_page_id=COALESCE(CAMP_FORM_DISTRIBUTIONS.returned_page_id,excluded.returned_page_id),
            updated_at=excluded.updated_at
        """,
        (row[0], student_id, now, page_id, now, now),
    )


def list_tracking_rows(conn, *, camp_id: int):
    return conn.execute(
        """
        SELECT d.distribution_id,d.student_id,s.last_name||s.first_name,d.source,d.status,
               d.distributed_at,d.returned_at,
               (SELECT COUNT(*) FROM IMAGE_IMPORT_PAGE_STUDENTS ps
                JOIN IMAGE_IMPORT_PAGES p ON p.page_id=ps.page_id
                JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
                WHERE b.camp_id=d.camp_id AND ps.candidate_student_id=d.student_id
                  AND ps.is_selected=1 AND ps.is_deleted=0 AND p.is_deleted=0
                  AND p.processing_status<>'SKIPPED' AND b.is_deleted=0) AS scan_count,
               i.last_name||i.first_name
        FROM CAMP_FORM_DISTRIBUTIONS d
        JOIN STUDENTS s ON s.student_id=d.student_id
        LEFT JOIN INSTRUCTORS i ON i.instructor_id=d.updated_by_instructor_id
        WHERE d.camp_id=? AND d.is_deleted=0
        ORDER BY CASE d.status WHEN 'DISTRIBUTED' THEN 1 WHEN 'TARGET' THEN 2
                 WHEN 'RETURNED' THEN 3 ELSE 4 END,s.last_name_kana,s.first_name_kana,s.student_id
        """,
        (camp_id,),
    ).fetchall()
