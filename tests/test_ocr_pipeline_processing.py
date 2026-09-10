from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import cv2

from image_import_migrations import _ensure_nullable_batch_camp, ensure_image_import_schema
from image_import_service import create_import_batch, render_pdf_pages
from image_preprocessing import PreprocessResult
from image_layouts import SUBJECT_DIGIT_REGIONS, SUBJECT_SELECTION_REGIONS, WEEKLY_AVAILABILITY_GRID
from mark_sheet_recognition import (
    analyze_mark,
    evaluate_count_marks,
    store_mark_sheet_availability,
    store_subject_mark_candidates,
)
from qr_student_recognition import recognize_qr_student_candidates


class OcrPipelineProcessingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        with open("schema.sql", encoding="utf-8") as source:
            self.conn.executescript(source.read())
        ensure_image_import_schema(self.conn)
        self.conn.executemany(
            "INSERT INTO PERIODS(period_number,start_time,end_time) VALUES (?,?,?)",
            [(period, "00:00", "00:00") for period in range(1, 6)],
        )
        self.conn.execute(
            """INSERT INTO STUDENTS
               (student_id,last_name,first_name,last_name_kana,first_name_kana,
                enrollment_year,base_grade,enrollment_status)
               VALUES (10,'山田','太郎','','',2026,8,'在籍'),
                      (20,'佐藤','花子','','',2026,9,'在籍')"""
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _page(self):
        batch_id = self.conn.execute(
            """INSERT INTO IMAGE_IMPORT_BATCHES
               (camp_id,paper_fiscal_year,paper_type,layout_key,layout_version,
                source_pdf_path,source_pdf_hash,page_count,created_at)
               VALUES (NULL,2026,'通常','mark-sheet-v1',1,'x.pdf',?,1,'now')""",
            ("a" * 64,),
        ).lastrowid
        return self.conn.execute(
            """INSERT INTO IMAGE_IMPORT_PAGES
               (batch_id,page_number,page_image_path,page_image_hash,created_at)
               VALUES (?,?,?,?,?)""",
            (batch_id, 1, "p.png", "b" * 64, "now"),
        ).lastrowid

    @staticmethod
    def _fill_region(image, region, ratio=1.0):
        height, width = image.shape
        x1, x2 = round(region.left * width), round(region.right * width)
        y1, y2 = round(region.top * height), round(region.bottom * height)
        fill_width = max(1, round((x2 - x1) * ratio))
        image[y1:y2, x1:x1 + fill_width] = 0

    def test_black_mark_uses_fifty_percent_and_flags_near_threshold(self):
        self.assertFalse(analyze_mark(np.full((10, 10), 255, dtype=np.uint8)).marked)
        half = np.full((10, 10), 255, dtype=np.uint8)
        half[:, :5] = 0
        result = analyze_mark(half)
        self.assertTrue(result.marked)
        self.assertTrue(result.ambiguous)
        dark = np.zeros((10, 10), dtype=np.uint8)
        self.assertTrue(analyze_mark(dark).marked)
        self.assertFalse(analyze_mark(dark).ambiguous)

    def test_multi_page_pdf_is_split_into_png_pages(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "two-pages.pdf"
            first = Image.new("RGB", (300, 400), "white")
            second = Image.new("RGB", (300, 400), "white")
            first.save(pdf_path, format="PDF", save_all=True, append_images=[second])
            paths = render_pdf_pages(pdf_path, root, dpi=72)
            self.assertEqual(len(paths), 2)
            self.assertTrue(all(path.is_file() for path in paths))

    def test_count_marks_never_guess_missing_or_double_digits(self):
        self.assertEqual(evaluate_count_marks([0], [8]), (8, None))
        self.assertIsNotNone(evaluate_count_marks([2], [])[1])
        self.assertIsNotNone(evaluate_count_marks([], [4])[1])
        self.assertIsNotNone(evaluate_count_marks([1, 2], [4])[1])
        self.assertIsNotNone(evaluate_count_marks([1], [4], ambiguous=True)[1])

    def test_subject_marks_store_valid_count_and_review_one_sided_count(self):
        page_id = self._page()
        image = np.full((1400, 1000), 255, dtype=np.uint8)
        self._fill_region(image, SUBJECT_SELECTION_REGIONS["英語"])
        self._fill_region(image, SUBJECT_DIGIT_REGIONS["英語"]["tens"][0])
        self._fill_region(image, SUBJECT_DIGIT_REGIONS["英語"]["ones"][8])
        self._fill_region(image, SUBJECT_SELECTION_REGIONS["数学・算数"])
        self._fill_region(image, SUBJECT_DIGIT_REGIONS["数学・算数"]["tens"][2])
        self._fill_region(image, SUBJECT_SELECTION_REGIONS["国語"], ratio=0.5)
        with tempfile.TemporaryDirectory() as directory:
            page_path = Path(directory) / "page.png"
            cv2.imwrite(str(page_path), image)
            reviews = store_subject_mark_candidates(
                self.conn, page_id=page_id, page_image_path=page_path,
                crop_output_dir=Path(directory) / "reviews",
            )
        rows = self.conn.execute(
            """SELECT subject_row_label,resolved_count,count_status
               FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS ORDER BY import_enrollment_id"""
        ).fetchall()
        self.assertEqual(rows, [
            ("英語", 8, "AUTO_RECOGNIZED"),
            ("数学・算数", None, "AMBIGUOUS"),
            ("国語", None, "AMBIGUOUS"),
        ])
        self.assertEqual(reviews, 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM IMAGE_IMPORT_ATTACHMENTS").fetchone()[0], 2)

    def test_regular_availability_stores_only_unavailable_and_ambiguous_cells(self):
        page_id = self._page()
        image = np.full((1000, 1000), 255, dtype=np.uint8)
        grid = WEEKLY_AVAILABILITY_GRID
        x1, x2 = round(grid.left * 1000), round(grid.right * 1000)
        y1, y2 = round(grid.top * 1000), round(grid.bottom * 1000)
        cell_width, cell_height = (x2 - x1) / 6, (y2 - y1) / 5
        # 月曜1限は明確な黒塗り、火曜2限は50%でレビュー対象。
        image[round(y1):round(y1 + cell_height), round(x1):round(x1 + cell_width)] = 0
        left, right = round(x1 + cell_width), round(x1 + 2 * cell_width)
        top, bottom = round(y1 + cell_height), round(y1 + 2 * cell_height)
        image[top:bottom, left:left + round((right - left) * 0.5)] = 0
        with tempfile.TemporaryDirectory() as directory:
            page_path = Path(directory) / "page.png"
            cv2.imwrite(str(page_path), image)
            saved, reviews = store_mark_sheet_availability(
                self.conn, page_id=page_id, page_image_path=page_path,
                paper_type="通常", paper_fiscal_year=2026,
                crop_output_dir=Path(directory) / "reviews",
            )
        rows = self.conn.execute(
            """SELECT session_date,period_number,availability_status
               FROM IMAGE_IMPORT_AVAILABILITY ORDER BY import_availability_id"""
        ).fetchall()
        self.assertEqual(rows[0], ("月", 1, "AUTO_UNAVAILABLE"))
        self.assertEqual(rows[1], ("火", 2, "AMBIGUOUS"))
        self.assertEqual((saved, reviews), (2, 1))

    def test_single_existing_qr_auto_selects_without_name_recognition(self):
        page_id = self._page()
        with tempfile.TemporaryDirectory() as directory:
            crop = Path(directory) / "qr.png"
            crop.write_bytes(b"qr")
            with patch("qr_student_recognition.decode_qr_values", return_value=["10"]):
                selected, candidates = recognize_qr_student_candidates(
                    self.conn, page_id=page_id, page_image_path=crop, qr_crop_path=crop
                )
        self.assertEqual((selected, candidates), (10, [10]))
        self.assertEqual(
            self.conn.execute(
                "SELECT candidate_student_id,match_status,is_selected FROM IMAGE_IMPORT_PAGE_STUDENTS"
            ).fetchone(),
            (10, "AUTO_MATCHED", 1),
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM IMAGE_IMPORT_REVIEW_ITEMS").fetchone()[0], 0)

    def test_missing_and_multiple_qr_are_not_guessed_and_create_review_crop(self):
        for values, expected_candidates in (([], 0), (["10", "20"], 2)):
            with self.subTest(values=values):
                self.conn.execute("DELETE FROM IMAGE_IMPORT_AUDIT_LOG")
                self.conn.execute("DELETE FROM IMAGE_IMPORT_ATTACHMENTS")
                self.conn.execute("DELETE FROM IMAGE_IMPORT_REVIEW_ITEMS")
                self.conn.execute("DELETE FROM IMAGE_IMPORT_PAGE_STUDENTS")
                self.conn.execute("DELETE FROM IMAGE_IMPORT_PAGES")
                self.conn.execute("DELETE FROM IMAGE_IMPORT_BATCHES")
                self.conn.commit()
                page_id = self._page()
                with tempfile.TemporaryDirectory() as directory:
                    crop = Path(directory) / "qr.png"
                    crop.write_bytes(b"qr-crop")
                    with patch("qr_student_recognition.decode_qr_values", return_value=values):
                        selected, candidates = recognize_qr_student_candidates(
                            self.conn, page_id=page_id, page_image_path=crop, qr_crop_path=crop
                        )
                self.assertIsNone(selected)
                self.assertEqual(len(candidates), expected_candidates)
                self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM IMAGE_IMPORT_REVIEW_ITEMS").fetchone()[0], 1)
                self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM IMAGE_IMPORT_ATTACHMENTS").fetchone()[0], 1)

    def test_regular_pdf_batch_uses_null_camp_and_never_writes_master_tables(self):
        def fake_render(_pdf_path, output_dir, dpi=300):
            path = output_dir / "page_0001_original.png"
            path.write_bytes(b"original")
            return [path]

        def fake_preprocess(original, corrected):
            corrected.write_bytes(original.read_bytes())
            return PreprocessResult(
                str(original), str(corrected), 0.0, 20, 100.0, 240.0, 0.01, "OK", "test"
            )

        def fake_qr_crop(_page, output_dir):
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / "student_qr.png"
            path.write_bytes(b"qr")
            return path

        student_count = self.conn.execute("SELECT COUNT(*) FROM STUDENTS").fetchone()[0]
        with tempfile.TemporaryDirectory() as directory, \
             patch("image_import_service.render_pdf_pages", side_effect=fake_render), \
             patch("image_import_service.preprocess_page", side_effect=fake_preprocess), \
             patch("image_import_service.write_preprocess_metadata"), \
             patch("image_import_service.extract_qr_region", side_effect=fake_qr_crop), \
             patch("image_import_service.recognize_qr_student_candidates", return_value=(10, [10])), \
             patch("image_import_service.store_subject_mark_candidates", return_value=0), \
             patch("image_import_service.resolve_page_subjects", return_value={"review": 0}), \
             patch("image_import_service.store_mark_sheet_availability", return_value=(0, 0)), \
             patch("image_import_service.capture_free_text"):
            result = create_import_batch(
                self.conn, camp_id=999, paper_fiscal_year=2026, paper_type="通常",
                pdf_content=b"%PDF-test-content", storage_root=Path(directory),
            )
            self.assertEqual(result[1], 1)
            self.assertIsNone(self.conn.execute("SELECT camp_id FROM IMAGE_IMPORT_BATCHES").fetchone()[0])
            self.assertEqual(
                self.conn.execute("SELECT processing_status FROM IMAGE_IMPORT_PAGES").fetchone()[0],
                "RECOGNIZED",
            )
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM STUDENTS").fetchone()[0], student_count)
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM REGULAR_COURSE_ENROLLMENTS").fetchone()[0], 0)
            with self.assertRaisesRegex(ValueError, "既に取り込み済み"):
                create_import_batch(
                    self.conn, camp_id=None, paper_fiscal_year=2026, paper_type="通常",
                    pdf_content=b"%PDF-test-content", storage_root=Path(directory),
                )


class ExistingBatchSchemaUpgradeTests(unittest.TestCase):
    def test_not_null_camp_schema_is_rebuilt_without_losing_rows_or_children(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE CAMPS(camp_id INTEGER PRIMARY KEY);
            CREATE TABLE INSTRUCTORS(instructor_id INTEGER PRIMARY KEY);
            CREATE TABLE IMAGE_IMPORT_BATCHES(
              batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
              camp_id INTEGER NOT NULL REFERENCES CAMPS(camp_id),
              paper_fiscal_year INTEGER NOT NULL,paper_type TEXT NOT NULL,
              layout_key TEXT NOT NULL,layout_version INTEGER NOT NULL,
              source_pdf_path TEXT NOT NULL,source_pdf_hash TEXT NOT NULL,
              page_count INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'PROCESSING',
              uploaded_by_instructor_id INTEGER REFERENCES INSTRUCTORS(instructor_id),
              school_id TEXT,school_name TEXT,created_at TEXT NOT NULL,
              imported_at TEXT,is_deleted INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE CHILD(page_id INTEGER PRIMARY KEY,
                               batch_id INTEGER REFERENCES IMAGE_IMPORT_BATCHES(batch_id));
            INSERT INTO CAMPS VALUES(1);
            INSERT INTO IMAGE_IMPORT_BATCHES
              (camp_id,paper_fiscal_year,paper_type,layout_key,layout_version,
               source_pdf_path,source_pdf_hash,page_count,created_at)
            VALUES(1,2026,'夏期','old',1,'x.pdf','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',1,'now');
            INSERT INTO CHILD VALUES(1,1);
            """
        )
        self.assertTrue(_ensure_nullable_batch_camp(conn))
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(IMAGE_IMPORT_BATCHES)")}
        self.assertEqual(columns["camp_id"][3], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM IMAGE_IMPORT_BATCHES").fetchone()[0], 1)
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        conn.close()


if __name__ == "__main__":
    unittest.main()
