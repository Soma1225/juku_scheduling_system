"""Versioned SQLite migrations used by the application."""

from pathlib import Path


MIGRATION_DIR = Path(__file__).with_name("migrations")
MIGRATIONS = (
    ("001_image_import_v6", MIGRATION_DIR / "001_image_import_v6.sql"),
    ("002_student_instructor_preferences", MIGRATION_DIR / "002_student_instructor_preferences.sql"),
)


def ensure_image_import_schema(conn) -> bool:
    """Apply the image-import schema exactly once.

    Returns True when this call applied the migration, False when it was already
    present. The caller owns the connection lifecycle.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS APP_SCHEMA_MIGRATIONS (
            migration_id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied_any = False
    for migration_id, migration_path in MIGRATIONS:
        already_applied = conn.execute(
            "SELECT 1 FROM APP_SCHEMA_MIGRATIONS WHERE migration_id = ?",
            (migration_id,),
        ).fetchone()
        if already_applied:
            continue
        migration_sql = migration_path.read_text(encoding="utf-8")
        escaped_id = migration_id.replace("'", "''")
        try:
            conn.executescript(
                "BEGIN IMMEDIATE;\n"
                + migration_sql
                + "\nINSERT INTO APP_SCHEMA_MIGRATIONS (migration_id, applied_at) "
                  f"VALUES ('{escaped_id}', strftime('%Y-%m-%dT%H:%M:%fZ','now'));\n"
                  "COMMIT;"
            )
        except Exception:
            conn.rollback()
            raise
        applied_any = True
    return applied_any
