"""Versioned SQLite migrations used by the application."""

from pathlib import Path


MIGRATION_DIR = Path(__file__).with_name("migrations")
MIGRATIONS = (
    ("001_image_import_v6", MIGRATION_DIR / "001_image_import_v6.sql"),
    ("002_student_instructor_preferences", MIGRATION_DIR / "002_student_instructor_preferences.sql"),
    ("004_regular_course_requests", MIGRATION_DIR / "004_regular_course_requests.sql"),
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
    upgraded = _ensure_nullable_batch_camp(conn)
    return applied_any or upgraded


def _ensure_nullable_batch_camp(conn) -> bool:
    """初期DDLのcamp_id NOT NULL版を、データを保ったまま修正する。"""
    columns = {
        row[1]: row for row in conn.execute("PRAGMA table_info(IMAGE_IMPORT_BATCHES)")
    }
    if not columns or columns["camp_id"][3] == 0:
        return False
    duplicate_hashes = conn.execute(
        """SELECT 1 FROM IMAGE_IMPORT_BATCHES
           WHERE is_deleted=0 GROUP BY source_pdf_hash HAVING COUNT(*)>1 LIMIT 1"""
    ).fetchone()
    unique_sql = "UNIQUE" if not duplicate_hashes else ""
    was_foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.executescript(f"""
        BEGIN IMMEDIATE;
        CREATE TABLE IMAGE_IMPORT_BATCHES_NEW (
            batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
            camp_id INTEGER REFERENCES CAMPS(camp_id),
            paper_fiscal_year INTEGER NOT NULL,
            paper_type TEXT NOT NULL,
            layout_key TEXT NOT NULL,
            layout_version INTEGER NOT NULL,
            source_pdf_path TEXT NOT NULL,
            source_pdf_hash TEXT NOT NULL {unique_sql} CHECK (LENGTH(source_pdf_hash) = 64),
            page_count INTEGER NOT NULL CHECK (page_count > 0),
            status TEXT NOT NULL DEFAULT 'PROCESSING'
                CHECK (status IN ('PROCESSING','REVIEW_PENDING','REVIEWED','IMPORTED','CANCELLED')),
            uploaded_by_instructor_id INTEGER REFERENCES INSTRUCTORS(instructor_id),
            school_id TEXT,
            school_name TEXT,
            created_at TEXT NOT NULL,
            imported_at TEXT,
            is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0,1))
        );
        INSERT INTO IMAGE_IMPORT_BATCHES_NEW SELECT * FROM IMAGE_IMPORT_BATCHES;
        DROP TABLE IMAGE_IMPORT_BATCHES;
        ALTER TABLE IMAGE_IMPORT_BATCHES_NEW RENAME TO IMAGE_IMPORT_BATCHES;
        CREATE INDEX idx_image_import_batches_camp ON IMAGE_IMPORT_BATCHES(camp_id);
        CREATE INDEX idx_image_import_batches_status ON IMAGE_IMPORT_BATCHES(status);
        CREATE INDEX idx_image_import_batches_pdf_hash ON IMAGE_IMPORT_BATCHES(source_pdf_hash);
        COMMIT;
        """)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if was_foreign_keys else 'OFF'}")
    return True
