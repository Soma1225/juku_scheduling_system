"""確認済み画像取込バッチの本登録計画・バックアップ・確定処理。"""

import datetime
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from image_import_db import classify_camp_enrollment_action, get_matching_camp_enrollments


DEFAULT_BACKUP_DIR = Path(__file__).with_name("backups")


@dataclass
class ImportPlan:
    batch_id: int
    term_id: int | None = None
    is_regular: bool = False
    enrollment_inserts: list[dict] = field(default_factory=list)
    enrollment_updates: list[dict] = field(default_factory=list)
    availability_inserts: list[dict] = field(default_factory=list)
    availability_updates: list[dict] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def can_import(self) -> bool:
        return not self.blockers and not self.conflicts


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _page_students(conn, batch_id: int) -> dict[int, int]:
    rows = conn.execute(
        """
        SELECT p.page_id,ps.candidate_student_id
        FROM IMAGE_IMPORT_PAGES p
        LEFT JOIN IMAGE_IMPORT_PAGE_STUDENTS ps
          ON ps.page_id=p.page_id AND ps.is_selected=1 AND ps.is_deleted=0
        WHERE p.batch_id=? AND p.is_deleted=0 AND p.processing_status<>'SKIPPED'
        ORDER BY p.page_number
        """,
        (batch_id,),
    ).fetchall()
    return {page_id: student_id for page_id, student_id in rows if student_id is not None}


def build_import_plan(conn, *, batch_id: int, term_id: int | None = None) -> ImportPlan:
    plan = ImportPlan(batch_id=batch_id, term_id=term_id)
    batch = conn.execute(
        "SELECT camp_id,status FROM IMAGE_IMPORT_BATCHES WHERE batch_id=? AND is_deleted=0",
        (batch_id,),
    ).fetchone()
    if not batch:
        plan.blockers.append("対象バッチが見つかりません")
        return plan
    camp_id, batch_status = batch
    plan.is_regular = camp_id is None
    if batch_status == "IMPORTED":
        plan.blockers.append("このバッチは既に本登録済みです")
        return plan
    if plan.is_regular:
        term = conn.execute(
            "SELECT term_id FROM TERMS WHERE term_id=?", (term_id,)
        ).fetchone() if term_id is not None else None
        if not term:
            plan.blockers.append("通常授業の本登録先となる学期を選択してください")

    page_count = conn.execute(
        "SELECT COUNT(*) FROM IMAGE_IMPORT_PAGES WHERE batch_id=? AND is_deleted=0 AND processing_status<>'SKIPPED'",
        (batch_id,),
    ).fetchone()[0]
    if page_count == 0:
        plan.blockers.append("本登録対象のページがありません")
    page_students = _page_students(conn, batch_id)
    if len(page_students) != page_count:
        plan.blockers.append("生徒が未確定のページがあります")
    bad_quality = conn.execute(
        "SELECT COUNT(*) FROM IMAGE_IMPORT_PAGES WHERE batch_id=? AND layout_quality<>'OK' AND is_deleted=0 AND processing_status<>'SKIPPED'",
        (batch_id,),
    ).fetchone()[0]
    if bad_quality:
        plan.blockers.append(f"画質エラーのページが{bad_quality}件あります（再スキャンまたは手入力が必要です）")
    pending_reviews = conn.execute(
        """
        SELECT COUNT(*) FROM IMAGE_IMPORT_REVIEW_ITEMS r
        JOIN IMAGE_IMPORT_PAGES p ON p.page_id=r.page_id
        WHERE p.batch_id=? AND p.processing_status<>'SKIPPED'
          AND r.resolution='PENDING' AND r.is_deleted=0 AND p.is_deleted=0
        """,
        (batch_id,),
    ).fetchone()[0]
    if pending_reviews:
        plan.blockers.append(f"未確認の項目が{pending_reviews}件あります")

    enrollment_rows = conn.execute(
        """
        SELECT e.import_enrollment_id,e.page_id,e.subject_row_label,e.resolved_count,
               e.resolved_subject_id,e.count_status,e.subject_match_status
        FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS e
        JOIN IMAGE_IMPORT_PAGES p ON p.page_id=e.page_id
        WHERE p.batch_id=? AND p.processing_status<>'SKIPPED'
          AND e.is_deleted=0 AND p.is_deleted=0
        ORDER BY e.import_enrollment_id
        """,
        (batch_id,),
    ).fetchall()
    staged_keys: dict[tuple[int, int, int], int] = {}
    for enrollment_id, page_id, row_label, count, subject_id, count_status, subject_status in enrollment_rows:
        if count_status in ("PENDING", "AMBIGUOUS") or count is None:
            plan.blockers.append(f"ページ{page_id}の「{row_label}」は回数が未確定です")
            continue
        if count == 0:
            continue
        student_id = page_students.get(page_id)
        if student_id is None:
            continue
        if subject_id is None or subject_status in ("PENDING", "AMBIGUOUS", "NOT_FOUND"):
            plan.blockers.append(f"ページ{page_id}の「{row_label}」は具体科目が未確定です")
            continue
        key = ((term_id if plan.is_regular else camp_id), student_id, subject_id)
        if key in staged_keys:
            plan.conflicts.append(
                f"取込候補内で同じ生徒・科目が複数あります（候補ID {staged_keys[key]} / {enrollment_id}）"
            )
            continue
        staged_keys[key] = enrollment_id
        if plan.is_regular:
            if not 1 <= count <= 5:
                plan.blockers.append(
                    f"ページ{page_id}の「{row_label}」は週あたり回数を1〜5回で確定してください"
                )
                continue
            existing = conn.execute(
                """
                SELECT request_id,student_id,term_id,subject_id,desired_count_per_week,
                       format,assigned_instructor_id,status
                FROM REGULAR_COURSE_REQUESTS
                WHERE student_id=? AND term_id=? AND subject_id=?
                ORDER BY request_id
                """,
                (student_id, term_id, subject_id),
            ).fetchall() if term_id is not None else []
        else:
            existing = get_matching_camp_enrollments(conn, camp_id, student_id, subject_id)
        action = classify_camp_enrollment_action(existing)
        item = {
            "import_enrollment_id": enrollment_id,
            "page_id": page_id,
            "camp_id": camp_id,
            "student_id": student_id,
            "subject_id": subject_id,
            "contracted_count": count,
        }
        if plan.is_regular:
            item.update({"term_id": term_id, "desired_count_per_week": count})
        if action == "INSERT":
            plan.enrollment_inserts.append(item)
        elif action == "UPDATE":
            item["existing"] = existing[0]
            plan.enrollment_updates.append(item)
        else:
            plan.conflicts.append(
                f"本登録に重複があります（{'term' if plan.is_regular else 'camp'}={key[0]}, "
                f"student={student_id}, subject={subject_id}, {len(existing)}件）"
            )

    availability_rows = conn.execute(
        """
        SELECT a.import_availability_id,a.page_id,a.matched_slot_id,a.resolved_is_available,
               a.availability_status,a.session_date,a.period_number
        FROM IMAGE_IMPORT_AVAILABILITY a
        JOIN IMAGE_IMPORT_PAGES p ON p.page_id=a.page_id
        WHERE p.batch_id=? AND p.processing_status<>'SKIPPED'
          AND a.is_deleted=0 AND p.is_deleted=0
        ORDER BY a.import_availability_id
        """,
        (batch_id,),
    ).fetchall()
    staged_availability: dict[tuple, dict] = {}
    for availability_id, page_id, slot_id, value, status, session_key, period_number in availability_rows:
        student_id = page_students.get(page_id)
        if student_id is None:
            continue
        invalid_statuses = ("PENDING", "AMBIGUOUS") if plan.is_regular else (
            "PENDING", "AMBIGUOUS", "SLOT_NOT_FOUND", "OUT_OF_CAMP_RANGE"
        )
        if status in invalid_statuses or value is None or (not plan.is_regular and slot_id is None):
            plan.blockers.append(f"ページ{page_id}の対応可能時間候補ID {availability_id}が未確定です")
            continue
        if plan.is_regular:
            weekday = session_key
            if weekday not in ("月", "火", "水", "木", "金", "土", "日"):
                plan.blockers.append(f"ページ{page_id}の対応可能時間候補ID {availability_id}の曜日が不正です")
                continue
            key = (student_id, term_id, weekday, period_number)
        else:
            key = (student_id, slot_id)
        item = {
            "import_availability_ids": [availability_id],
            "page_id": page_id,
            "student_id": student_id,
            "slot_id": slot_id,
            "is_available": value,
        }
        if plan.is_regular:
            item.update({"term_id": term_id, "day_of_week": key[2], "period_number": key[3]})
        prior = staged_availability.get(key)
        if prior:
            if prior["is_available"] != value:
                plan.conflicts.append(
                    f"取込候補内で同じ生徒・日時の可否が食い違います（student={student_id}, slot={slot_id}）"
                )
            else:
                prior["import_availability_ids"].append(availability_id)
            continue
        staged_availability[key] = item
        if plan.is_regular:
            existing = conn.execute(
                """
                SELECT availability_id,student_id,term_id,day_of_week,period_number,is_available
                FROM STUDENT_WEEKLY_AVAILABILITY
                WHERE student_id=? AND term_id=? AND day_of_week=? AND period_number=?
                ORDER BY availability_id
                """,
                key,
            ).fetchall() if term_id is not None else []
        else:
            existing = conn.execute(
                """
                SELECT availability_id,student_id,slot_id,is_available
                FROM CAMP_STUDENT_AVAILABILITY WHERE student_id=? AND slot_id=?
                ORDER BY availability_id
                """,
                key,
            ).fetchall()
        if len(existing) == 0:
            plan.availability_inserts.append(item)
        elif len(existing) == 1:
            item["existing"] = existing[0]
            plan.availability_updates.append(item)
        else:
            plan.conflicts.append(
                f"本登録の対応可能時間に重複があります（student={student_id}, "
                f"{'weekday=' + str(key[2]) + ', period=' + str(key[3]) if plan.is_regular else 'slot=' + str(slot_id)}, "
                f"{len(existing)}件）"
            )
    # 同じエラーが多数出る場合でも画面を読みやすくする。
    plan.blockers = list(dict.fromkeys(plan.blockers))
    plan.conflicts = list(dict.fromkeys(plan.conflicts))
    return plan


def backup_database(conn, *, batch_id: int, backup_dir: Path = DEFAULT_BACKUP_DIR) -> Path:
    """SQLite backup APIで、本登録直前の一貫したDBコピーを作る。"""
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    if not db_path:
        raise ValueError("インメモリDBはバックアップできません")
    backup_dir = Path(backup_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destination = backup_dir / f"juku_schedule.before_image_import_batch_{batch_id}_{stamp}.db"
    backup_conn = sqlite3.connect(destination)
    try:
        conn.backup(backup_conn)
    finally:
        backup_conn.close()
    return destination


def execute_import_batch(
    conn,
    *,
    batch_id: int,
    operator_instructor_id: int,
    term_id: int | None = None,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> tuple[ImportPlan, Path]:
    """直前再検査とバックアップ後、候補を既存本テーブルへ1トランザクションで反映する。"""
    operator = conn.execute(
        "SELECT last_name,first_name FROM INSTRUCTORS WHERE instructor_id=? AND status='在籍'",
        (operator_instructor_id,),
    ).fetchone()
    if not operator:
        raise ValueError("在籍中の担当講師を選択してください")
    plan = build_import_plan(conn, batch_id=batch_id, term_id=term_id)
    if not plan.can_import:
        raise ValueError("未確認項目または重複競合があるため、本登録できません")
    conn.commit()
    backup_path = backup_database(conn, batch_id=batch_id, backup_dir=backup_dir)
    now = _now_iso()
    operator_name = f"{operator[0]}{operator[1]}"
    try:
        conn.execute("BEGIN IMMEDIATE")
        # バックアップ後からロック取得までの変化も再検査する。
        locked_plan = build_import_plan(conn, batch_id=batch_id, term_id=term_id)
        if not locked_plan.can_import:
            raise ValueError("本登録直前の再確認で、未確認項目または重複競合が見つかりました")
        batch = conn.execute(
            "SELECT school_id,school_name FROM IMAGE_IMPORT_BATCHES WHERE batch_id=?",
            (batch_id,),
        ).fetchone()
        for item in locked_plan.enrollment_inserts:
            if locked_plan.is_regular:
                cur = conn.execute(
                    """
                    INSERT INTO REGULAR_COURSE_REQUESTS
                        (student_id,term_id,subject_id,desired_count_per_week,format,status)
                    VALUES (?,?,?,?, '1:2','PENDING')
                    """,
                    (item["student_id"], item["term_id"], item["subject_id"], item["desired_count_per_week"]),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO CAMP_COURSE_ENROLLMENTS
                        (camp_id,student_id,subject_id,contracted_count,format)
                    VALUES (?,?,?,?, '1:2')
                    """,
                    (item["camp_id"], item["student_id"], item["subject_id"], item["contracted_count"]),
                )
            target_id = cur.lastrowid
            _mark_enrollment_imported(
                conn, item, target_id, batch_id, operator_instructor_id, operator_name,
                batch, now, None, is_regular=locked_plan.is_regular,
            )
        for item in locked_plan.enrollment_updates:
            existing = item["existing"]
            target_id = existing[0]
            if locked_plan.is_regular:
                conn.execute(
                    "UPDATE REGULAR_COURSE_REQUESTS SET desired_count_per_week=?,status='PENDING' WHERE request_id=?",
                    (item["desired_count_per_week"], target_id),
                )
            else:
                conn.execute(
                    "UPDATE CAMP_COURSE_ENROLLMENTS SET contracted_count=? WHERE enrollment_id=?",
                    (item["contracted_count"], target_id),
                )
            _mark_enrollment_imported(
                conn, item, target_id, batch_id, operator_instructor_id, operator_name,
                batch, now, existing, is_regular=locked_plan.is_regular,
            )
        for item in locked_plan.availability_inserts:
            if locked_plan.is_regular:
                cur = conn.execute(
                    """INSERT INTO STUDENT_WEEKLY_AVAILABILITY
                       (student_id,term_id,day_of_week,period_number,is_available)
                       VALUES(?,?,?,?,?)""",
                    (item["student_id"], item["term_id"], item["day_of_week"],
                     item["period_number"], item["is_available"]),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO CAMP_STUDENT_AVAILABILITY(student_id,slot_id,is_available) VALUES(?,?,?)",
                    (item["student_id"], item["slot_id"], item["is_available"]),
                )
            _mark_availability_imported(
                conn, item, cur.lastrowid, batch_id, operator_instructor_id,
                operator_name, batch, now, None, is_regular=locked_plan.is_regular,
            )
        for item in locked_plan.availability_updates:
            existing = item["existing"]
            target_id = existing[0]
            target_table = "STUDENT_WEEKLY_AVAILABILITY" if locked_plan.is_regular else "CAMP_STUDENT_AVAILABILITY"
            conn.execute(
                f"UPDATE {target_table} SET is_available=? WHERE availability_id=?",
                (item["is_available"], target_id),
            )
            _mark_availability_imported(
                conn, item, target_id, batch_id, operator_instructor_id, operator_name,
                batch, now, existing, is_regular=locked_plan.is_regular,
            )
        conn.execute(
            "UPDATE IMAGE_IMPORT_PAGES SET processing_status='IMPORTED' WHERE batch_id=? AND is_deleted=0 AND processing_status<>'SKIPPED'",
            (batch_id,),
        )
        conn.execute(
            "UPDATE IMAGE_IMPORT_BATCHES SET status='IMPORTED',imported_at=? WHERE batch_id=?",
            (now, batch_id),
        )
        conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_AUDIT_LOG
                (batch_id,action_type,target_table,target_id,after_value_json,actor_type,
                 operator_instructor_id,operator_name,school_id,school_name,created_at)
            VALUES (:batch_id,'IMPORT','IMAGE_IMPORT_BATCHES',:target_id,:after_value_json,
                    'INSTRUCTOR',:operator_id,:operator_name,:school_id,:school_name,:created_at)
            """,
            {
                "batch_id": batch_id,
                "target_id": batch_id,
                "after_value_json": json.dumps({
                    "status": "IMPORTED",
                    "term_id": locked_plan.term_id if locked_plan.is_regular else None,
                    "enrollment_inserts": len(locked_plan.enrollment_inserts),
                    "enrollment_updates": len(locked_plan.enrollment_updates),
                    "availability_inserts": len(locked_plan.availability_inserts),
                    "availability_updates": len(locked_plan.availability_updates),
                    "backup_path": str(backup_path),
                }, ensure_ascii=False),
                "operator_id": operator_instructor_id,
                "operator_name": operator_name,
                "school_id": batch[0],
                "school_name": batch[1],
                "created_at": now,
            },
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return locked_plan, backup_path


def _mark_enrollment_imported(
    conn, item, target_id, batch_id, operator_id, operator_name, batch, now, before,
    *, is_regular: bool = False,
):
    # 既存列はCAMP_COURSE_ENROLLMENTSへの外部キーなので、通常授業では監査ログだけで関連を残す。
    if not is_regular:
        conn.execute(
            "UPDATE IMAGE_IMPORT_SUBJECT_ENROLLMENTS SET imported_enrollment_id=? WHERE import_enrollment_id=?",
            (target_id, item["import_enrollment_id"]),
        )
    target_table = "REGULAR_COURSE_REQUESTS" if is_regular else "CAMP_COURSE_ENROLLMENTS"
    after = (
        {"term_id": item["term_id"], "desired_count_per_week": item["desired_count_per_week"], "status": "PENDING"}
        if is_regular else {"contracted_count": item["contracted_count"]}
    )
    conn.execute(
        """
        INSERT INTO IMAGE_IMPORT_AUDIT_LOG
            (batch_id,page_id,action_type,target_table,target_id,before_value_json,after_value_json,
             actor_type,operator_instructor_id,operator_name,school_id,school_name,created_at)
        VALUES (:batch_id,:page_id,'IMPORT',:target_table,:target_id,
                :before_value_json,:after_value_json,'INSTRUCTOR',:operator_id,:operator_name,
                :school_id,:school_name,:created_at)
        """,
        {
            "batch_id": batch_id,
            "page_id": item["page_id"],
            "target_table": target_table,
            "target_id": target_id,
            "before_value_json": json.dumps(before, ensure_ascii=False) if before else None,
            "after_value_json": json.dumps(after, ensure_ascii=False),
            "operator_id": operator_id,
            "operator_name": operator_name,
            "school_id": batch[0],
            "school_name": batch[1],
            "created_at": now,
        },
    )


def _mark_availability_imported(
    conn, item, target_id, batch_id, operator_id, operator_name, batch, now, before,
    *, is_regular: bool = False,
):
    # 既存列はCAMP_STUDENT_AVAILABILITYへの外部キーなので、通常授業では監査ログだけで関連を残す。
    if not is_regular:
        for import_id in item["import_availability_ids"]:
            conn.execute(
                "UPDATE IMAGE_IMPORT_AVAILABILITY SET imported_availability_id=? WHERE import_availability_id=?",
                (target_id, import_id),
            )
    target_table = "STUDENT_WEEKLY_AVAILABILITY" if is_regular else "CAMP_STUDENT_AVAILABILITY"
    after = {"is_available": item["is_available"]}
    if is_regular:
        after.update({
            "term_id": item["term_id"], "day_of_week": item["day_of_week"],
            "period_number": item["period_number"],
        })
    conn.execute(
        """
        INSERT INTO IMAGE_IMPORT_AUDIT_LOG
            (batch_id,page_id,action_type,target_table,target_id,before_value_json,after_value_json,
             actor_type,operator_instructor_id,operator_name,school_id,school_name,created_at)
        VALUES (:batch_id,:page_id,'IMPORT',:target_table,:target_id,
                :before_value_json,:after_value_json,'INSTRUCTOR',:operator_id,:operator_name,
                :school_id,:school_name,:created_at)
        """,
        {
            "batch_id": batch_id,
            "page_id": item["page_id"],
            "target_table": target_table,
            "target_id": target_id,
            "before_value_json": json.dumps(before, ensure_ascii=False) if before else None,
            "after_value_json": json.dumps(after, ensure_ascii=False),
            "operator_id": operator_id,
            "operator_name": operator_name,
            "school_id": batch[0],
            "school_name": batch[1],
            "created_at": now,
        },
    )
