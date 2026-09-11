"""通常授業・教科フォロー登録で共有する曜日×限グリッド。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import html


DAYS = ("月", "火", "水", "木", "金", "土", "日")
PERIOD_NUMBERS = (1, 2, 3, 4, 5)
ENROLLMENT_TABLES = ("REGULAR_COURSE_ENROLLMENTS", "FOLLOW_COURSE_ENROLLMENTS")


@dataclass(frozen=True)
class SlotDecision:
    disabled: bool
    reason: str = ""


def resolve_term_for_date(conn, effective_start_date: str) -> tuple[int, str]:
    """開始日を含む学期を一意に特定する（学期末日も含む）。"""
    try:
        date.fromisoformat(effective_start_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("契約開始日は YYYY-MM-DD 形式で入力してください") from exc

    rows = conn.execute(
        """SELECT term_id,term_name FROM TERMS
           WHERE start_date<=? AND end_date>=?
           ORDER BY start_date""",
        (effective_start_date, effective_start_date),
    ).fetchall()
    if not rows:
        raise ValueError("契約開始日を含む学期が登録されていません")
    if len(rows) > 1:
        raise ValueError("契約開始日を含む学期が複数あります。学期設定を確認してください")
    return int(rows[0][0]), str(rows[0][1])


def _active_condition(alias: str = "e") -> str:
    return (
        f"{alias}.effective_start_date<=? AND "
        f"({alias}.effective_end_date IS NULL OR {alias}.effective_end_date>?)"
    )


def evaluate_enrollment_slots(
    conn,
    student_id: int,
    instructor_id: int,
    effective_start_date: str,
) -> tuple[int, str, dict[tuple[str, int], SlotDecision]]:
    """両契約テーブルと可用性を横断し、各枠が登録可能かを返す。"""
    term_id, term_name = resolve_term_for_date(conn, effective_start_date)

    student_occupied: set[tuple[str, int]] = set()
    instructor_counts: dict[tuple[str, int], int] = {}
    for table in ENROLLMENT_TABLES:
        for day_of_week, period_number in conn.execute(
            f"""SELECT day_of_week,period_number FROM {table} e
                WHERE student_id=? AND {_active_condition()}""",
            (student_id, effective_start_date, effective_start_date),
        ).fetchall():
            student_occupied.add((day_of_week, int(period_number)))

        for day_of_week, period_number, count in conn.execute(
            f"""SELECT day_of_week,period_number,COUNT(*) FROM {table} e
                WHERE instructor_id=? AND {_active_condition()}
                GROUP BY day_of_week,period_number""",
            (instructor_id, effective_start_date, effective_start_date),
        ).fetchall():
            key = (day_of_week, int(period_number))
            instructor_counts[key] = instructor_counts.get(key, 0) + int(count)

    # この画面では、指示仕様どおり「行がある＝対応不可」として扱う。
    student_unavailable = {
        (day, int(period))
        for day, period in conn.execute(
            """SELECT day_of_week,period_number FROM STUDENT_WEEKLY_AVAILABILITY
               WHERE student_id=? AND term_id=?""",
            (student_id, term_id),
        ).fetchall()
    }
    instructor_unavailable = {
        (day, int(period))
        for day, period in conn.execute(
            """SELECT day_of_week,period_number FROM INSTRUCTOR_WEEKLY_AVAILABILITY
               WHERE instructor_id=? AND term_id=?""",
            (instructor_id, term_id),
        ).fetchall()
    }
    instructor = conn.execute(
        "SELECT last_name||first_name FROM INSTRUCTORS WHERE instructor_id=?",
        (instructor_id,),
    ).fetchone()
    instructor_name = instructor[0] if instructor else "選択した講師"

    decisions: dict[tuple[str, int], SlotDecision] = {}
    for day in DAYS:
        for period in PERIOD_NUMBERS:
            key = (day, period)
            if key in student_occupied:
                decisions[key] = SlotDecision(True, "本人：授業あり")
            elif instructor_counts.get(key, 0) >= 2:
                decisions[key] = SlotDecision(True, f"{instructor_name}先生：1:2の上限")
            elif key in student_unavailable:
                decisions[key] = SlotDecision(True, "本人：対応不可")
            elif key in instructor_unavailable:
                decisions[key] = SlotDecision(True, f"{instructor_name}先生：対応不可")
            else:
                decisions[key] = SlotDecision(False)
    return term_id, term_name, decisions


def validate_enrollment_slot(
    conn,
    student_id: int,
    instructor_id: int,
    effective_start_date: str,
    day_of_week: str,
    period_number: int,
) -> int:
    """直接POSTでもdisabled枠を登録できないよう、登録直前に再判定する。"""
    if day_of_week not in DAYS or period_number not in PERIOD_NUMBERS:
        raise ValueError("曜日または限が不正です")
    term_id, _term_name, decisions = evaluate_enrollment_slots(
        conn, student_id, instructor_id, effective_start_date
    )
    decision = decisions[(day_of_week, period_number)]
    if decision.disabled:
        raise ValueError(f"{day_of_week}曜{period_number}限には登録できません（{decision.reason}）")
    return term_id


def build_enrollment_calendar_grid(
    *,
    action_path: str,
    student_id: int,
    subject_id: int,
    instructor_id: int,
    effective_start_date: str,
    decisions: dict[tuple[str, int], SlotDecision],
) -> str:
    """各セルが独立した登録フォームになった曜日×5限グリッドを描画する。"""
    headers = "".join(f"<th>{html.escape(day)}</th>" for day in DAYS)
    rows = []
    for period in PERIOD_NUMBERS:
        cells = []
        for day in DAYS:
            decision = decisions[(day, period)]
            hidden = "".join(
                f'<input type="hidden" name="{name}" value="{html.escape(str(value))}">'
                for name, value in (
                    ("action", "add"),
                    ("student_id", student_id),
                    ("subject_id", subject_id),
                    ("instructor_id", instructor_id),
                    ("effective_start_date", effective_start_date),
                    ("day_of_week", day),
                    ("period_number", period),
                )
            )
            disabled = " disabled" if decision.disabled else ""
            label = (
                f'<span class="slot-reason">{html.escape(decision.reason)}</span>'
                if decision.reason else '<span class="slot-open">登録</span>'
            )
            cells.append(
                f'<td><form class="slot-form" method="POST" action="{html.escape(action_path)}">'
                f'{hidden}<button class="slot-button" type="submit"{disabled}>'
                f'{day}{period}限<br>{label}</button></form></td>'
            )
        rows.append(f"<tr><th>{period}限</th>{''.join(cells)}</tr>")
    return f"""
    <style>
      .enrollment-grid {{ width:100%; table-layout:fixed; margin-top:12px; }}
      .enrollment-grid th,.enrollment-grid td {{ padding:4px; text-align:center; vertical-align:middle; }}
      .slot-form {{ margin:0; }}
      .slot-button {{ min-height:68px; width:100%; padding:7px 4px; font-size:12px; }}
      .slot-button:disabled {{ background:#d7d7d4; color:#666; cursor:not-allowed; opacity:1; }}
      .slot-reason {{ display:inline-block; margin-top:4px; font-size:10px; line-height:1.25; }}
      .slot-open {{ font-weight:bold; }}
    </style>
    <table class="enrollment-grid"><tr><th>限＼曜日</th>{headers}</tr>{''.join(rows)}</table>
    """
