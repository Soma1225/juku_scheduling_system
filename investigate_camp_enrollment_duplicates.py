"""本番DBの講習会受講科目重複を、読み取り専用で調査する。"""

import argparse
import sqlite3
from pathlib import Path

from image_import_db import find_camp_enrollment_duplicates, get_matching_camp_enrollments


def open_read_only(db_path: Path):
    """SQLite URIのmode=roを使い、調査中の書き込みを物理的に禁止する。"""
    return sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CAMP_COURSE_ENROLLMENTSの重複を読み取り専用で調査します。"
    )
    parser.add_argument(
        "db_path",
        nargs="?",
        default=Path(__file__).with_name("juku_schedule.db"),
        type=Path,
        help="調査するjuku_schedule.dbのパス",
    )
    args = parser.parse_args()
    if not args.db_path.is_file():
        parser.error(f"DBファイルが見つかりません: {args.db_path}")

    conn = open_read_only(args.db_path)
    try:
        duplicates = find_camp_enrollment_duplicates(conn)
        print(f"duplicate_groups: {len(duplicates)}")
        for duplicate in duplicates:
            print(
                "\n"
                f"camp_id={duplicate.camp_id}, student_id={duplicate.student_id}, "
                f"subject_id={duplicate.subject_id}, row_count={duplicate.row_count}"
            )
            rows = get_matching_camp_enrollments(
                conn,
                duplicate.camp_id,
                duplicate.student_id,
                duplicate.subject_id,
            )
            for row in rows:
                print(
                    "  "
                    f"enrollment_id={row[0]}, contracted_count={row[4]}, "
                    f"format={row[5]}, assigned_instructor_id={row[6]}, "
                    f"enrollment_end_date={row[7]}"
                )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
