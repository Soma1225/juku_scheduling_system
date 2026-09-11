"""担当科目・習熟度と共通の枠制約から、空きのある講師を検索する。"""

from __future__ import annotations

import datetime

from enrollment_calendar_grid import DAYS, PERIOD_NUMBERS, get_instructor_slot_constraints


def search_instructors(
    conn,
    *,
    subject_id: int,
    minimum_level: int,
    as_of_date: str | None = None,
) -> dict:
    if minimum_level not in (1, 2):
        raise ValueError("最低レベルは1または2を指定してください")
    if conn.execute("SELECT 1 FROM SUBJECTS WHERE subject_id=?", (subject_id,)).fetchone() is None:
        raise ValueError("対象の科目が見つかりません")
    as_of_date = as_of_date or datetime.date.today().isoformat()
    candidates = conn.execute(
        """SELECT i.instructor_id,i.last_name||i.first_name,isub.proficiency_level
           FROM INSTRUCTOR_SUBJECTS isub
           JOIN INSTRUCTORS i ON i.instructor_id=isub.instructor_id
           WHERE isub.subject_id=? AND isub.proficiency_level>=? AND i.status='在籍'
           ORDER BY isub.proficiency_level DESC,i.last_name_kana,i.first_name_kana""",
        (subject_id, minimum_level),
    ).fetchall()

    results = []
    term_name = ""
    for instructor_id, instructor_name, proficiency_level in candidates:
        (_term_id, term_name, _resolved_name, occupied_counts,
         unavailable) = get_instructor_slot_constraints(
            conn, int(instructor_id), as_of_date
        )
        free_slots = [
            (day, period)
            for day in DAYS
            for period in PERIOD_NUMBERS
            if occupied_counts.get((day, period), 0) < 2
            and (day, period) not in unavailable
        ]
        results.append({
            "instructor_id": int(instructor_id),
            "instructor_name": instructor_name,
            "proficiency_level": int(proficiency_level),
            "free_slots": free_slots,
        })

    # 候補0人でも、対象学期が分かるよう共通関数と同じ学期解決を行う。
    if not candidates:
        from enrollment_calendar_grid import resolve_term_for_date
        _term_id, term_name = resolve_term_for_date(conn, as_of_date)
    return {"as_of_date": as_of_date, "term_name": term_name, "instructors": results}
