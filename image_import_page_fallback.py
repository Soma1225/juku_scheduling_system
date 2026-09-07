"""低画質ページを再スキャンまたは手入力へ安全に退避する。"""

import datetime
import json


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _operator(conn, instructor_id: int):
    row = conn.execute(
        "SELECT last_name,first_name FROM INSTRUCTORS WHERE instructor_id=? AND status='在籍'",
        (instructor_id,),
    ).fetchone()
    if not row:
        raise ValueError("在籍中の担当講師を選択してください")
    return row


def _refresh_batch_status(conn, batch_id: int) -> str:
    active_pages = conn.execute(
        """
        SELECT COUNT(*) FROM IMAGE_IMPORT_PAGES
        WHERE batch_id=? AND is_deleted=0 AND processing_status<>'SKIPPED'
        """,
        (batch_id,),
    ).fetchone()[0]
    if active_pages == 0:
        status = "CANCELLED"
    else:
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
        status = "REVIEW_PENDING" if pending or bad_quality else "REVIEWED"
    conn.execute("UPDATE IMAGE_IMPORT_BATCHES SET status=? WHERE batch_id=?", (status, batch_id))
    return status


def skip_problem_page(
    conn,
    *,
    page_id: int,
    fallback_mode: str,
    operator_instructor_id: int,
) -> None:
    """問題ページを本登録対象から外す。候補・画像・監査履歴は削除しない。"""
    if fallback_mode not in ("RESCAN", "MANUAL"):
        raise ValueError("再スキャンまたは手入力を選択してください")
    operator = _operator(conn, operator_instructor_id)
    page = conn.execute(
        """
        SELECT p.batch_id,p.layout_quality,p.processing_status,b.status,b.school_id,b.school_name
        FROM IMAGE_IMPORT_PAGES p JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
        WHERE p.page_id=? AND p.is_deleted=0 AND b.is_deleted=0
        """,
        (page_id,),
    ).fetchone()
    if not page:
        raise ValueError("対象ページが見つかりません")
    if page[3] == "IMPORTED":
        raise ValueError("本登録済みバッチのページは除外できません")
    if page[2] == "SKIPPED":
        raise ValueError("このページは既に除外済みです")
    if page[1] == "OK":
        raise ValueError("画質判定が正常なページは、この操作では除外できません")
    now = _now_iso()
    operator_name = f"{operator[0]}{operator[1]}"
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE IMAGE_IMPORT_PAGES SET processing_status='SKIPPED' WHERE page_id=?",
            (page_id,),
        )
        # 確認項目は認識時点の証拠なので削除しない。
        # 本登録判定側がprocessing_status='SKIPPED'のページを除外する。
        _refresh_batch_status(conn, page[0])
        conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_AUDIT_LOG
                (batch_id,page_id,action_type,target_table,target_id,before_value_json,
                 after_value_json,actor_type,operator_instructor_id,operator_name,
                 school_id,school_name,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                page[0], page_id, "REJECT", "IMAGE_IMPORT_PAGES", page_id,
                json.dumps({"processing_status": page[2]}, ensure_ascii=False),
                json.dumps({"processing_status": "SKIPPED", "fallback_mode": fallback_mode}, ensure_ascii=False),
                "INSTRUCTOR", operator_instructor_id, operator_name, page[4], page[5], now,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def restore_skipped_page(conn, *, page_id: int, operator_instructor_id: int) -> None:
    """誤って除外したページを確認待ちへ戻す。"""
    operator = _operator(conn, operator_instructor_id)
    page = conn.execute(
        """
        SELECT p.batch_id,p.processing_status,b.status,b.school_id,b.school_name
        FROM IMAGE_IMPORT_PAGES p JOIN IMAGE_IMPORT_BATCHES b ON b.batch_id=p.batch_id
        WHERE p.page_id=? AND p.is_deleted=0 AND b.is_deleted=0
        """,
        (page_id,),
    ).fetchone()
    if not page or page[1] != "SKIPPED":
        raise ValueError("除外済みのページが見つかりません")
    if page[2] == "IMPORTED":
        raise ValueError("本登録済みバッチのページは復帰できません")
    now = _now_iso()
    operator_name = f"{operator[0]}{operator[1]}"
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE IMAGE_IMPORT_PAGES SET processing_status='RECOGNIZED' WHERE page_id=?",
            (page_id,),
        )
        _refresh_batch_status(conn, page[0])
        conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_AUDIT_LOG
                (batch_id,page_id,action_type,target_table,target_id,before_value_json,
                 after_value_json,actor_type,operator_instructor_id,operator_name,
                 school_id,school_name,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                page[0], page_id, "RESTORE", "IMAGE_IMPORT_PAGES", page_id,
                json.dumps({"processing_status": "SKIPPED"}, ensure_ascii=False),
                json.dumps({"processing_status": "RECOGNIZED"}, ensure_ascii=False),
                "INSTRUCTOR", operator_instructor_id, operator_name, page[3], page[4], now,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
