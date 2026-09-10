from io import BytesIO
import sqlite3
import unittest

import openpyxl

import app
from core_migrations import ensure_core_schema
from instructor_snapshot_import import import_instructor_snapshot, parse_instructor_snapshot
import layout
import page_instructor_import


def build_snapshot(rows) -> bytes:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "講師情報"
    worksheet.append(["スナップショット"])
    worksheet.append([])
    worksheet.append([
        "講師番号", "講師名（漢字）", "講師名（略）", "講師名（かな）", "状態",
        "パスワード", "登録日", "登録者", "管理者登録フラグ", "備考",
    ])
    for row in rows:
        worksheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class InstructorSnapshotImportTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        with open("schema.sql", encoding="utf-8") as schema_file:
            self.conn.executescript(schema_file.read())

    def tearDown(self):
        self.conn.close()

    def _sample(self):
        return build_snapshot([
            ["A-001", "田中 太郎", "田", "たなか たろう", "勤務",
             "secret-1", "2020-01-01", "管理者", 1, "個人情報を含む備考"],
            ["A-002", "佐藤 花子", "佐", "さとう はなこ", "退職",
             "secret-2", "2020-01-02", "管理者", 0, "退職者の備考"],
            ["A-003", "鈴木一郎", "鈴", "すずきいちろう", "休職",
             "secret-3", "2020-01-03", "担当者", 0, "別の備考"],
        ])

    def test_parser_reads_only_whitelisted_fields(self):
        records = parse_instructor_snapshot(self._sample())
        self.assertEqual(len(records), 3)
        self.assertEqual(
            set(records[0]),
            {"external_instructor_id", "full_name", "short_name", "kana", "source_status", "source_row"},
        )
        self.assertNotIn("secret-1", repr(records))
        self.assertNotIn("個人情報を含む備考", repr(records))

    def test_import_excludes_retired_and_treats_other_statuses_as_active(self):
        result = import_instructor_snapshot(self.conn, self._sample())
        self.assertEqual(result, {
            "source_count": 3,
            "created_count": 2,
            "updated_count": 0,
            "retired_skipped_count": 1,
        })
        rows = self.conn.execute(
            """SELECT external_instructor_id, last_name, first_name, last_name_kana,
                      first_name_kana, short_name, academic_year, status
               FROM INSTRUCTORS ORDER BY external_instructor_id"""
        ).fetchall()
        self.assertEqual(rows, [
            ("A-001", "田中", "太郎", "たなか", "たろう", "田", None, "在籍"),
            ("A-003", "鈴木一郎", "", "すずきいちろう", "", "鈴", None, "在籍"),
        ])

    def test_reimport_updates_by_instructor_number_without_duplication(self):
        import_instructor_snapshot(self.conn, self._sample())
        changed = build_snapshot([
            ["A-001", "田中 次郎", "田次", "たなか じろう", "勤務"],
        ])
        result = import_instructor_snapshot(self.conn, changed)
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM INSTRUCTORS").fetchone()[0], 2)
        self.assertEqual(
            self.conn.execute(
                "SELECT first_name, short_name FROM INSTRUCTORS WHERE external_instructor_id='A-001'"
            ).fetchone(),
            ("次郎", "田次"),
        )

    def test_duplicate_short_name_is_rejected_before_writing(self):
        duplicate = build_snapshot([
            ["A-001", "田中 太郎", "同", "たなか たろう", "勤務"],
            ["A-002", "佐藤 花子", "同", "さとう はなこ", "勤務"],
        ])
        with self.assertRaisesRegex(ValueError, "講師名（略）が重複"):
            import_instructor_snapshot(self.conn, duplicate)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM INSTRUCTORS").fetchone()[0], 0)

    def test_empty_sheet_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "1件も見つかりません"):
            import_instructor_snapshot(self.conn, build_snapshot([]))

    def test_page_route_and_upload_handler(self):
        self.assertEqual(
            app.ROUTES["/instructor-import"],
            (page_instructor_import.render, page_instructor_import.handle_post),
        )
        instructor_menu = dict(next(items for name, items in layout.MENU_GROUPS if name == "講師情報"))
        self.assertIn("/instructor-import", instructor_menu)
        message, query = page_instructor_import.handle_post({
            "action": ["import"],
            "_files": {"excel_file": {"filename": "info-copy.xlsx", "content": self._sample()}},
        }, self.conn)
        self.assertIn("新規: 2名", message)
        self.assertEqual(query, {})


class InstructorSnapshotMigrationTests(unittest.TestCase):
    def test_existing_ids_and_foreign_keys_survive_rebuild(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
            CREATE TABLE INSTRUCTORS (
                instructor_id INTEGER PRIMARY KEY AUTOINCREMENT,
                last_name TEXT NOT NULL, first_name TEXT NOT NULL,
                last_name_kana TEXT NOT NULL, first_name_kana TEXT NOT NULL,
                external_instructor_id TEXT UNIQUE,
                academic_year TEXT NOT NULL,
                status TEXT NOT NULL,
                academic_year_confirmed_fiscal_year INTEGER
            );
            CREATE TABLE ASSIGNED_TEST (
                id INTEGER PRIMARY KEY,
                instructor_id INTEGER NOT NULL REFERENCES INSTRUCTORS(instructor_id)
            );
            INSERT INTO INSTRUCTORS VALUES(7,'既存','講師','きぞん','こうし','E-7','B2','在籍',2026);
            INSERT INTO ASSIGNED_TEST VALUES(1,7);
        """)
        self.assertTrue(ensure_core_schema(conn))
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(INSTRUCTORS)")}
        self.assertIn("short_name", columns)
        self.assertEqual(columns["academic_year"][3], 0)
        self.assertEqual(conn.execute("SELECT instructor_id FROM INSTRUCTORS").fetchone()[0], 7)
        self.assertEqual(conn.execute("SELECT instructor_id FROM ASSIGNED_TEST").fetchone()[0], 7)
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertFalse(ensure_core_schema(conn))
        conn.close()


if __name__ == "__main__":
    unittest.main()
