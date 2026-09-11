"""Core schema migrations that may rebuild application master tables."""

from pathlib import Path


MIGRATION_ID = "003_instructor_snapshot_import"
MIGRATION_PATH = Path(__file__).with_name("migrations") / f"{MIGRATION_ID}.sql"
CLOSURE_MIGRATION_ID = "005_closure_dates"
CLOSURE_MIGRATION_PATH = Path(__file__).with_name("migrations") / f"{CLOSURE_MIGRATION_ID}.sql"
MAKEUP_MIGRATION_ID = "006_makeup_sessions"
MAKEUP_MIGRATION_PATH = Path(__file__).with_name("migrations") / f"{MAKEUP_MIGRATION_ID}.sql"


def _instructor_columns(conn) -> dict[str, tuple]:
    return {row[1]: row for row in conn.execute("PRAGMA table_info(INSTRUCTORS)").fetchall()}


def _ensure_simple_migration(conn, migration_id: str, migration_path: Path) -> bool:
    applied = conn.execute(
        "SELECT 1 FROM APP_SCHEMA_MIGRATIONS WHERE migration_id=?", (migration_id,)
    ).fetchone()
    if applied:
        return False
    conn.executescript(
        "BEGIN IMMEDIATE;\n"
        + migration_path.read_text(encoding="utf-8")
        + "\nINSERT INTO APP_SCHEMA_MIGRATIONS (migration_id, applied_at) "
          f"VALUES ('{migration_id}', strftime('%Y-%m-%dT%H:%M:%fZ','now'));\n"
          "COMMIT;"
    )
    return True


def ensure_core_schema(conn) -> bool:
    """講師略称と学年未設定を扱えるINSTRUCTORSスキーマへ一度だけ更新する。"""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS APP_SCHEMA_MIGRATIONS (
               migration_id TEXT PRIMARY KEY,
               applied_at TEXT NOT NULL
           )"""
    )
    simple_migration_applied = False
    for migration_id, migration_path in (
        (CLOSURE_MIGRATION_ID, CLOSURE_MIGRATION_PATH),
        (MAKEUP_MIGRATION_ID, MAKEUP_MIGRATION_PATH),
    ):
        simple_migration_applied = (
            _ensure_simple_migration(conn, migration_id, migration_path)
            or simple_migration_applied
        )
    applied = conn.execute(
        "SELECT 1 FROM APP_SCHEMA_MIGRATIONS WHERE migration_id = ?", (MIGRATION_ID,)
    ).fetchone()
    if applied:
        return simple_migration_applied

    columns = _instructor_columns(conn)
    if not columns:
        raise RuntimeError("INSTRUCTORSテーブルが見つかりません")

    # 新規DBはschema.sqlですでに新形式なので、再構築せず履歴だけ記録する。
    if "short_name" in columns and columns["academic_year"][3] == 0:
        conn.execute(
            "INSERT INTO APP_SCHEMA_MIGRATIONS (migration_id, applied_at) "
            "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
            (MIGRATION_ID,),
        )
        conn.commit()
        return True

    # SQLiteではNOT NULL制約をALTER COLUMNできないため、IDを保ったまま再構築する。
    # 参照元テーブルを保持するため、再構築中だけ外部キー検査を止め、完了後に全件検査する。
    conn.commit()
    foreign_keys_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    if foreign_keys_enabled:
        conn.execute("PRAGMA foreign_keys = OFF")
    try:
        migration_sql = MIGRATION_PATH.read_text(encoding="utf-8")
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            + migration_sql
            + "\nINSERT INTO APP_SCHEMA_MIGRATIONS (migration_id, applied_at) "
              f"VALUES ('{MIGRATION_ID}', strftime('%Y-%m-%dT%H:%M:%fZ','now'));\n"
              "COMMIT;"
        )
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"講師マスタ移行後の外部キー検査に失敗しました: {violations[:3]}")
    except Exception:
        conn.rollback()
        raise
    finally:
        if foreign_keys_enabled:
            conn.execute("PRAGMA foreign_keys = ON")
    return True
