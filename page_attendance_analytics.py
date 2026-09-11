"""月次の稼働率と講師別担当コマ数を表示する画面。"""

from __future__ import annotations

import datetime
import html

from attendance_analytics import get_instructor_performance, get_utilization_report, normalize_month
from db import get_conn
from page_instructors import list_instructors


def _shift_month(first: datetime.date, offset: int) -> datetime.date:
    index = first.year * 12 + first.month - 1 + offset
    return datetime.date(index // 12, index % 12 + 1, 1)


def _month_options(conn, selected: str) -> str:
    today = datetime.date.today().replace(day=1)
    months = {_shift_month(today, offset).strftime("%Y-%m") for offset in range(-24, 13)}
    for (stored_month,) in conn.execute(
        """SELECT DISTINCT substr(session_date,1,7) FROM ATTENDANCE_RECORDS
           WHERE length(session_date)>=7"""
    ).fetchall():
        try:
            months.add(normalize_month(stored_month))
        except ValueError:
            continue
    try:
        months.add(normalize_month(selected))
    except ValueError:
        pass
    return "".join(
        f'<option value="{month}"{" selected" if month == selected else ""}>'
        f'{month[:4]}年{int(month[5:])}月</option>'
        for month in sorted(months, reverse=True)
    )


def _summary_cards(report: dict, *, include_rate: bool = True) -> str:
    rate = (
        f'<div class="analytics-card"><span>1:2稼働率</span><strong>{report["utilization_rate"]:.1f}%</strong></div>'
        if include_rate else ""
    )
    return f"""
    <style>
      .analytics-cards {{ display:flex; gap:12px; flex-wrap:wrap; margin:18px 0; }}
      .analytics-card {{ min-width:140px; padding:14px 18px; border:1px solid #ddd;
                         border-radius:8px; background:#fafafa; }}
      .analytics-card span {{ display:block; color:#666; font-size:12px; }}
      .analytics-card strong {{ display:block; margin-top:4px; color:#1F4E5F; font-size:22px; }}
    </style>
    <div class="analytics-cards">
      <div class="analytics-card"><span>総コマ数</span><strong>{report["total_sessions"]}</strong></div>
      <div class="analytics-card"><span>1:1</span><strong>{report["one_to_one_sessions"]}</strong></div>
      <div class="analytics-card"><span>1:2</span><strong>{report["one_to_two_sessions"]}</strong></div>
      <div class="analytics-card"><span>その他</span><strong>{report["other_sessions"]}</strong></div>
      {rate}
    </div>
    """


def render_utilization(qs: dict, message_html: str = "") -> str:
    requested = qs.get("month", [datetime.date.today().strftime("%Y-%m")])[0]
    try:
        month = normalize_month(requested)
        error_html = ""
    except ValueError as exc:
        month = datetime.date.today().strftime("%Y-%m")
        error_html = f'<div class="msg error">{html.escape(str(exc))}</div>'
    conn = get_conn()
    report = get_utilization_report(conn, month)
    options = _month_options(conn, month)
    conn.close()
    daily_rows = "".join(
        f"""<tr><td>{item['session_date']}</td><td>{item['total_sessions']}</td>
        <td>{item['one_to_two_sessions']}</td><td>{item['utilization_rate']:.1f}%</td></tr>"""
        for item in report["daily"]
    ) or '<tr><td colspan="4" class="hint">この月の出席記録はありません</td></tr>'
    return f"""
    <h1>稼働率</h1>
    <div class="hint">出席済み授業のうち、講師1人が生徒2人を担当したコマの割合です。</div>
    {message_html}{error_html}
    <form method="GET" action="/utilization">
      <label>対象年月</label>
      <select name="month" onchange="this.form.submit()">{options}</select>
    </form>
    {_summary_cards(report)}
    <h2 style="font-size:15px;">日ごとの内訳</h2>
    <table><tr><th>日付</th><th>総コマ数</th><th>1:2コマ数</th><th>稼働率</th></tr>
      {daily_rows}
    </table>
    """


def render_instructor_performance(qs: dict, message_html: str = "") -> str:
    instructor_id = qs.get("instructor_id", [""])[0]
    requested = qs.get("month", [datetime.date.today().strftime("%Y-%m")])[0]
    try:
        month = normalize_month(requested)
        error_html = ""
    except ValueError as exc:
        month = datetime.date.today().strftime("%Y-%m")
        error_html = f'<div class="msg error">{html.escape(str(exc))}</div>'
    conn = get_conn()
    instructors = list_instructors(conn)
    month_options = _month_options(conn, month)
    instructor_options = "".join(
        f'<option value="{item_id}"{" selected" if str(item_id) == instructor_id else ""}>'
        f'{html.escape(name)}</option>'
        for item_id, name in instructors
    )
    report_html = '<div class="hint">講師を選択してください</div>'
    if instructor_id:
        try:
            instructor_name = next(name for item_id, name in instructors if str(item_id) == instructor_id)
            report = get_instructor_performance(conn, int(instructor_id), month)
            subject_rows = "".join(
                f'<tr><td>{html.escape(item["subject_name"])}</td><td>{item["session_count"]}</td></tr>'
                for item in report["subjects"]
            ) or '<tr><td colspan="2" class="hint">この月の出席記録はありません</td></tr>'
            report_html = f"""
            <h2 style="font-size:15px;">{html.escape(instructor_name)}の実績</h2>
            {_summary_cards(report, include_rate=False)}
            <h2 style="font-size:15px;">科目ごとのコマ数</h2>
            <table><tr><th>科目</th><th>コマ数</th></tr>{subject_rows}</table>
            """
        except (StopIteration, ValueError):
            error_html += '<div class="msg error">対象の講師が見つかりません</div>'
    conn.close()
    return f"""
    <h1>講師実績確認</h1>
    <div class="hint">出席済み授業を基に担当コマ数を確認します。支払い金額は計算しません。</div>
    {message_html}{error_html}
    <form method="GET" action="/instructor-performance">
      <label>対象講師</label>
      <select name="instructor_id" onchange="this.form.submit()">
        <option value="">選択してください</option>{instructor_options}
      </select>
      <label>対象年月</label>
      <select name="month" onchange="this.form.submit()">{month_options}</select>
    </form>
    {report_html}
    """
