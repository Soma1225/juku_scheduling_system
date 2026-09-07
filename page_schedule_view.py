# -*- coding: utf-8 -*-
"""
page_schedule_view.py

スケジューラーが組んだ結果(SESSIONS/ASSIGNMENTS)を確認するための、
3種類の閲覧専用ページ。

1. 日付単位の授業スケジュール(/schedule-by-day): ある1日の、全セッション一覧
2. 講師視点の時間割(/schedule-instructor): 講師を選ぶと、その人の日付×限グリッド
3. 生徒視点の時間割(/schedule-student): 生徒を選ぶと、その人の日付×限グリッド
"""

import datetime
import html
from db import get_conn
from page_camps import list_camps
from page_instructors import list_instructors
from page_home import get_follow_schedule_for_date, get_schedule_for_date

WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]
PERIOD_NUMBERS = [1, 2, 3, 4, 5]


def _weekday_jp(date_str: str) -> str:
    y, m, d = map(int, date_str.split("-"))
    return WEEKDAY_JP[datetime.date(y, m, d).weekday()]


def _options(rows, selected=""):
    return "".join(
        f'<option value="{i}"{" selected" if str(i) == selected else ""}>{name}</option>' for i, name in rows
    )


def _get_period_labels(conn) -> dict[int, str]:
    numerals = {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤"}
    rows = conn.execute("SELECT period_number, start_time, end_time FROM PERIODS ORDER BY period_number").fetchall()
    return {p: f"{numerals.get(p, p)}{s}〜{e}" for p, s, e in rows}


def _camp_dates(conn, camp_id: int) -> list[str]:
    return [
        row[0] for row in conn.execute(
            """SELECT DISTINCT ts.session_date FROM SESSIONS s
               JOIN TIME_SLOTS ts ON ts.slot_id = s.slot_id
               WHERE s.camp_id = ? ORDER BY ts.session_date""",
            (camp_id,),
        ).fetchall()
    ]


# ---------------------------------------------------------
# 1. 日付単位の授業スケジュール
# ---------------------------------------------------------

def render_by_day(qs: dict, message_html: str = "") -> str:
    camp_id = qs.get("camp_id", [""])[0]
    session_date = qs.get("session_date", [""])[0]
    conn = get_conn()
    camps = list_camps(conn)

    body_html = ""
    if camp_id:
        dates = _camp_dates(conn, int(camp_id))
        if not session_date and dates:
            session_date = dates[0]
        date_options = "".join(
            f'<option value="{d}"{" selected" if d == session_date else ""}>{d}（{_weekday_jp(d)}）</option>'
            for d in dates
        )

        rows_html = ""
        if session_date:
            period_labels = _get_period_labels(conn)
            sessions = conn.execute(
                """SELECT s.session_id, ts.period_number, i.last_name || i.first_name AS instructor_name
                   FROM SESSIONS s
                   JOIN TIME_SLOTS ts ON ts.slot_id = s.slot_id
                   JOIN INSTRUCTORS i ON i.instructor_id = s.instructor_id
                   WHERE s.camp_id = ? AND ts.session_date = ?
                   ORDER BY ts.period_number, i.last_name_kana""",
                (camp_id, session_date),
            ).fetchall()

            for session_id, period_number, instructor_name in sessions:
                members = conn.execute(
                    """SELECT st.last_name || st.first_name, sub.subject_group || '/' || sub.subject_name
                       FROM ASSIGNMENTS a
                       JOIN STUDENTS st ON st.student_id = a.student_id
                       JOIN SUBJECTS sub ON sub.subject_id = a.subject_id
                       WHERE a.session_id = ?""",
                    (session_id,),
                ).fetchall()
                member_text = "、".join(f"{name}（{subj}）" for name, subj in members)
                rows_html += (
                    f"<tr><td>{period_labels.get(period_number, f'{period_number}限')}</td>"
                    f"<td>{instructor_name}</td><td>{member_text}</td></tr>"
                )

            table_html = (
                f"<table><tr><th>限</th><th>講師</th><th>生徒（科目）</th></tr>{rows_html}</table>"
                if rows_html else '<div class="hint">この日はセッションがありません</div>'
            )
        else:
            table_html = '<div class="hint">この講習会にはまだ時間割が組まれていません</div>'

        body_html = f"""
        <label>日付</label>
        <select onchange="location.href='/schedule-by-day?camp_id={camp_id}&session_date='+this.value">
          {date_options}
        </select>
        {table_html}
        """

    conn.close()
    return f"""
    <h1>日付単位の授業スケジュール</h1>
    <div class="hint">講習会を選ぶと、日付ごとの全セッション一覧を確認できます</div>
    {message_html}
    <label>講習会</label>
    <select onchange="location.href='/schedule-by-day?camp_id='+this.value">
      <option value="">選択してください</option>{_options(camps, camp_id)}
    </select>
    {body_html if camp_id else '<div class="hint">先に講習会を選択してください</div>'}
    """


# ---------------------------------------------------------
# 共通: 講師/生徒の日付別時間割グリッド組み立て
# ---------------------------------------------------------

def _parse_target_date(value: str | None) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value) if value else datetime.date.today()
    except ValueError:
        return datetime.date.today()


def _build_timetable_grid(conn, records: list[dict], role: str, empty_message: str) -> str:
    """ホーム画面と同じレコード形式から、対象者1人の5限グリッドを作る。"""
    period_labels = _get_period_labels(conn)
    by_period: dict[int, list[dict]] = {period: [] for period in PERIOD_NUMBERS}
    for record in records:
        if record["period"] in by_period:
            by_period[record["period"]].append(record)
    if not any(by_period.values()):
        return f'<div class="hint">{html.escape(empty_message)}</div>'

    headers = "".join(
        f'<th>{html.escape(period_labels.get(period, f"{period}限"))}</th>'
        for period in PERIOD_NUMBERS
    )
    cells = ""
    for period in PERIOD_NUMBERS:
        entries = []
        for record in by_period[period]:
            if role == "instructor":
                primary = record["student_name"]
            else:
                primary = record["instructor_name"]
            subject = record["subject_name"] or record["subject_group"]
            entries.append(
                '<div style="padding:7px 4px;border-bottom:1px solid #e5e5e5;">'
                f'{html.escape(primary)}<br><small>{html.escape(subject)}</small></div>'
            )
        cells += f'<td style="vertical-align:top;min-width:150px;">{"".join(entries) or "-"}</td>'
    return f"""
    <div style="overflow-x:auto;">
      <table class="grid"><tr>{headers}</tr><tr>{cells}</tr></table>
    </div>
    """


def _date_navigation(path: str, target_date: datetime.date, entity_param: str, entity_id: str) -> str:
    previous_date = (target_date - datetime.timedelta(days=1)).isoformat()
    next_date = (target_date + datetime.timedelta(days=1)).isoformat()
    current_date = target_date.isoformat()
    entity_query = f'&{entity_param}={entity_id}' if entity_id else ""
    return f"""
    <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:16px 0;">
      <a href="{path}?date={previous_date}{entity_query}">← 前日</a>
      <input type="date" value="{current_date}"
             onchange="location.href='{path}?date='+this.value+'&{entity_param}={entity_id}'">
      <span>{target_date.year}年{target_date.month}月{target_date.day}日（{_weekday_jp(current_date)}）</span>
      <a href="{path}?date={next_date}{entity_query}">翌日 →</a>
    </div>
    """


# ---------------------------------------------------------
# 2. 講師視点の時間割
# ---------------------------------------------------------

def render_instructor_view(qs: dict, message_html: str = "") -> str:
    target_date = _parse_target_date(qs.get("date", [None])[0])
    instructor_id = qs.get("instructor_id", [""])[0]
    conn = get_conn()
    instructors = list_instructors(conn)
    grid_html = follow_html = ""
    if instructor_id:
        entity_id = int(instructor_id)
        records = [
            record for record in get_schedule_for_date(conn, target_date)
            if record["instructor_id"] == entity_id
        ]
        follow_records = [
            record for record in get_follow_schedule_for_date(conn, target_date)
            if record["instructor_id"] == entity_id
        ]
        grid_html = _build_timetable_grid(conn, records, "instructor", "この日の授業予定はありません")
        follow_html = _build_timetable_grid(conn, follow_records, "instructor", "この日の教科フォローはありません")
    conn.close()

    return f"""
    <h1>講師視点の時間割</h1>
    <div class="hint">日付と講師を選ぶと、通常授業と講習会をまとめて確認できます</div>
    {message_html}
    {_date_navigation('/schedule-instructor', target_date, 'instructor_id', instructor_id)}
    <label>講師</label>
    <select onchange="location.href='/schedule-instructor?date={target_date.isoformat()}&instructor_id='+this.value">
      <option value="">選択してください</option>{_options(instructors, instructor_id)}
    </select>
    {('<h2 style="margin-top:24px;">通常授業・講習会</h2>' + grid_html +
      '<h2 style="margin-top:26px;">教科フォロー</h2>' + follow_html)
      if instructor_id else '<div class="hint">講師を選択してください</div>'}
    """


# ---------------------------------------------------------
# 3. 生徒視点の時間割
# ---------------------------------------------------------

def render_student_view(qs: dict, message_html: str = "") -> str:
    target_date = _parse_target_date(qs.get("date", [None])[0])
    student_id = qs.get("student_id", [""])[0]
    conn = get_conn()
    students = conn.execute(
        "SELECT student_id, last_name || ' ' || first_name FROM STUDENTS ORDER BY last_name_kana, first_name_kana"
    ).fetchall()
    grid_html = follow_html = ""
    if student_id:
        entity_id = int(student_id)
        records = [
            record for record in get_schedule_for_date(conn, target_date)
            if record["student_id"] == entity_id
        ]
        follow_records = [
            record for record in get_follow_schedule_for_date(conn, target_date)
            if record["student_id"] == entity_id
        ]
        grid_html = _build_timetable_grid(conn, records, "student", "この日の授業予定はありません")
        follow_html = _build_timetable_grid(conn, follow_records, "student", "この日の教科フォローはありません")
    conn.close()

    return f"""
    <h1>生徒視点の時間割</h1>
    <div class="hint">日付と生徒を選ぶと、通常授業と講習会をまとめて確認できます</div>
    {message_html}
    {_date_navigation('/schedule-student', target_date, 'student_id', student_id)}
    <label>生徒</label>
    <select onchange="location.href='/schedule-student?date={target_date.isoformat()}&student_id='+this.value">
      <option value="">選択してください</option>{_options(students, student_id)}
    </select>
    {('<h2 style="margin-top:24px;">通常授業・講習会</h2>' + grid_html +
      '<h2 style="margin-top:26px;">教科フォロー</h2>' + follow_html)
      if student_id else '<div class="hint">生徒を選択してください</div>'}
    """
