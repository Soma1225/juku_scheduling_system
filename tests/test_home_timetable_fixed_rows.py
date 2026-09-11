import datetime
import re
import unittest
from unittest import mock

import page_home


def _record(index: int, *, period: int = 1, instructor_id: int = 1) -> dict:
    return {
        "period": period,
        "instructor_id": instructor_id,
        "instructor_name": "田中一郎",
        "student_id": index,
        "student_name": f"生徒{index}",
        "base_grade": 8,
        "subject_id": 1,
        "subject_group": "数学",
        "subject_name": "数学",
    }


def _body_row_count(rendered: str) -> int:
    body = re.search(r"<tbody>(.*?)</tbody>", rendered, re.DOTALL)
    if body is None:
        return 0
    return body.group(1).count("<tr>")


class HomeTimetableFixedRowsTests(unittest.TestCase):
    def setUp(self):
        self.target_date = datetime.date(2026, 9, 14)

    def render(self, records: list[dict]) -> str:
        with mock.patch.object(page_home, "_attendance_cell", return_value="<button>○</button>"):
            return page_home._build_timetable_html(None, self.target_date, records)

    def test_empty_day_still_displays_fifteen_rows(self):
        rendered = self.render([])
        self.assertIn('class="timetable"', rendered)
        self.assertEqual(_body_row_count(rendered), page_home.TIMETABLE_ROWS_PER_PERIOD)
        self.assertNotIn("この日の予定はありません", rendered)

    def test_three_student_instructor_block_keeps_rowspan_and_twelve_empty_rows(self):
        rendered = self.render([_record(1), _record(2), _record(3)])
        self.assertEqual(_body_row_count(rendered), 15)
        self.assertIn('rowspan="3" class="col-instructor"', rendered)
        self.assertEqual(rendered.count('class="col-instructor"'), 1)
        self.assertIn('class="subject-badge"', rendered)
        self.assertIn('class="col-attendance"', rendered)

    def test_more_than_fifteen_records_are_not_truncated(self):
        rendered = self.render([_record(index) for index in range(1, 17)])
        self.assertEqual(_body_row_count(rendered), 16)
        self.assertIn("生徒16", rendered)
        self.assertIn('rowspan="16" class="col-instructor"', rendered)


if __name__ == "__main__":
    unittest.main()
