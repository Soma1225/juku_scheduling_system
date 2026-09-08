"""画像取込機能から既存DBを安全に参照するための補助関数。"""

from dataclasses import dataclass
from typing import Literal, Sequence


EnrollmentAction = Literal["INSERT", "UPDATE", "CONFLICT"]


@dataclass(frozen=True)
class CampEnrollmentDuplicate:
    camp_id: int
    student_id: int
    subject_id: int
    row_count: int


def find_camp_enrollment_duplicates(conn) -> list[CampEnrollmentDuplicate]:
    """講習会・生徒・科目が同じ重複群を読み取り専用で返す。"""
    rows = conn.execute(
        """
        SELECT camp_id, student_id, subject_id, COUNT(*) AS row_count
        FROM CAMP_COURSE_ENROLLMENTS
        GROUP BY camp_id, student_id, subject_id
        HAVING COUNT(*) > 1
        ORDER BY camp_id, student_id, subject_id
        """
    ).fetchall()
    return [CampEnrollmentDuplicate(*row) for row in rows]


def get_matching_camp_enrollments(conn, camp_id, student_id, subject_id):
    """本登録候補と同じキーを持つ既存行を、内容確認用に全件返す。"""
    return conn.execute(
        """
        SELECT enrollment_id, camp_id, student_id, subject_id,
               contracted_count, format, assigned_instructor_id,
               enrollment_end_date
        FROM CAMP_COURSE_ENROLLMENTS
        WHERE camp_id = ? AND student_id = ? AND subject_id = ?
        ORDER BY enrollment_id
        """,
        (camp_id, student_id, subject_id),
    ).fetchall()


def classify_camp_enrollment_action(existing_rows: Sequence[object]) -> EnrollmentAction:
    """既存件数から、安全に提示できる本登録操作を決める。

    0件は新規登録、1件は既存行の更新候補、2件以上は自動処理せず
    職員確認を要する競合として扱う。
    """
    row_count = len(existing_rows)
    if row_count == 0:
        return "INSERT"
    if row_count == 1:
        return "UPDATE"
    return "CONFLICT"
