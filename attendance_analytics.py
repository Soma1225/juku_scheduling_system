"""出席済みATTENDANCE_RECORDSを元にしたコマ数・1:2稼働率集計。"""

from __future__ import annotations

import datetime
from collections import defaultdict


def normalize_month(month: str) -> str:
    try:
        parsed = datetime.date.fromisoformat(f"{month}-01")
    except (TypeError, ValueError) as exc:
        raise ValueError("対象年月は YYYY-MM 形式で指定してください") from exc
    normalized = parsed.strftime("%Y-%m")
    if month != normalized:
        raise ValueError("対象年月は YYYY-MM 形式で指定してください")
    return normalized


def month_bounds(month: str) -> tuple[str, str]:
    normalized = normalize_month(month)
    start = datetime.date.fromisoformat(f"{normalized}-01")
    if start.month == 12:
        following = datetime.date(start.year + 1, 1, 1)
    else:
        following = datetime.date(start.year, start.month + 1, 1)
    return start.isoformat(), following.isoformat()


def get_attended_lesson_groups(
    conn, month: str, instructor_id: int | None = None
) -> list[dict]:
    """講師×日付×限を1コマとして、出席したdistinct生徒数を返す。"""
    start, following = month_bounds(month)
    instructor_filter = " AND instructor_id=?" if instructor_id is not None else ""
    params: list[object] = [start, following]
    if instructor_id is not None:
        params.append(instructor_id)
    cursor = conn.execute(
        f"""SELECT instructor_id,session_date,period_number,
                   COUNT(DISTINCT student_id) AS student_count
            FROM ATTENDANCE_RECORDS
            WHERE status='出席' AND session_date>=? AND session_date<?
              {instructor_filter}
            GROUP BY instructor_id,session_date,period_number
            ORDER BY session_date,period_number,instructor_id""",
        params,
    )
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def summarize_groups(groups: list[dict]) -> dict:
    total = len(groups)
    one_to_one = sum(1 for item in groups if item["student_count"] == 1)
    one_to_two = sum(1 for item in groups if item["student_count"] == 2)
    other = total - one_to_one - one_to_two
    return {
        "total_sessions": total,
        "one_to_one_sessions": one_to_one,
        "one_to_two_sessions": one_to_two,
        "other_sessions": other,
        "utilization_rate": round(one_to_two / total * 100, 1) if total else 0.0,
    }


def get_utilization_report(conn, month: str) -> dict:
    groups = get_attended_lesson_groups(conn, month)
    summary = summarize_groups(groups)
    by_date: dict[str, list[dict]] = defaultdict(list)
    for item in groups:
        by_date[item["session_date"]].append(item)
    daily = []
    for session_date in sorted(by_date):
        item = summarize_groups(by_date[session_date])
        item["session_date"] = session_date
        daily.append(item)
    return {"month": normalize_month(month), **summary, "daily": daily}


def get_instructor_performance(conn, instructor_id: int, month: str) -> dict:
    groups = get_attended_lesson_groups(conn, month, instructor_id)
    summary = summarize_groups(groups)
    start, following = month_bounds(month)
    # 同一コマ内の同一科目は、生徒が2人でも科目別1コマとして数える。
    subjects = conn.execute(
        """SELECT sub.subject_name,COUNT(*)
           FROM (
             SELECT session_date,period_number,subject_id
             FROM ATTENDANCE_RECORDS
             WHERE status='出席' AND instructor_id=?
               AND session_date>=? AND session_date<?
             GROUP BY session_date,period_number,subject_id
           ) attended_subjects
           JOIN SUBJECTS sub ON sub.subject_id=attended_subjects.subject_id
           GROUP BY sub.subject_id,sub.subject_name
           ORDER BY COUNT(*) DESC,sub.subject_name""",
        (instructor_id, start, following),
    ).fetchall()
    return {
        "month": normalize_month(month),
        "instructor_id": instructor_id,
        **summary,
        "subjects": [
            {"subject_name": subject_name, "session_count": count}
            for subject_name, count in subjects
        ],
    }
