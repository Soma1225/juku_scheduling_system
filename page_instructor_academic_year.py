# -*- coding: utf-8 -*-
"""
page_instructor_academic_year.py

大学生講師の学年(academic_year)を、4月1日を過ぎたら「進級案」として提示し、
本人が(社用PC上で)自分でボタンを押して承認する仕組み。

- 上限(B12/M4/D6)の講師は、これ以上の進級案を出さず、
  「上限に達しているのに在籍のまま」というホーム画面の通知対象にする(個別確認が必要)。
- 一度確認(承認/留年)すると、その年度はもう聞かれない
  (academic_year_confirmed_fiscal_year に記録する)。
"""

import datetime
from db import get_conn
from page_instructors import ACADEMIC_YEAR_GROUPS

_NEXT_YEAR = {}
for _group_label, _items in ACADEMIC_YEAR_GROUPS:
    _codes = [code for code, _label in _items]
    for _i in range(len(_codes) - 1):
        _NEXT_YEAR[_codes[_i]] = _codes[_i + 1]
    # 最後の学年(B12/M4/D6)は次が無い(上限)ので _NEXT_YEAR に登録しない
MAX_ACADEMIC_YEARS = {"B12", "M4", "D6"}


def get_current_academic_fiscal_year(today: datetime.date | None = None) -> int:
    """大学の年度(4月始まり)を返す。1〜3月は前年度扱い。"""
    today = today or datetime.date.today()
    return today.year if today.month >= 4 else today.year - 1


def get_pending_promotions(conn) -> list[dict]:
    """
    進級案を提示すべき講師の一覧(上限に達していない、かつ今年度まだ確認していない、在籍中)。
    """
    current_fy = get_current_academic_fiscal_year()
    rows = conn.execute(
        """SELECT instructor_id, last_name || first_name, academic_year, academic_year_confirmed_fiscal_year
           FROM INSTRUCTORS
           WHERE status = '在籍' AND academic_year IS NOT NULL
             AND (academic_year_confirmed_fiscal_year IS NULL OR academic_year_confirmed_fiscal_year < ?)""",
        (current_fy,),
    ).fetchall()

    pending = []
    for instructor_id, name, current_year, confirmed_fy in rows:
        if current_year in MAX_ACADEMIC_YEARS:
            continue  # 上限到達者は別の通知(要個別確認)で扱う
        pending.append({
            "instructor_id": instructor_id, "name": name,
            "current_year": current_year, "proposed_year": _NEXT_YEAR[current_year],
        })
    return pending


def get_at_max_unconfirmed(conn) -> list[dict]:
    """学年が上限(B12/M4/D6)に達しているのに、今年度まだ確認していない在籍講師の一覧。"""
    current_fy = get_current_academic_fiscal_year()
    rows = conn.execute(
        """SELECT instructor_id, last_name || first_name, academic_year
           FROM INSTRUCTORS
           WHERE status = '在籍' AND academic_year IN ('B12', 'M4', 'D6')
             AND (academic_year_confirmed_fiscal_year IS NULL OR academic_year_confirmed_fiscal_year < ?)""",
        (current_fy,),
    ).fetchall()
    return [{"instructor_id": r[0], "name": r[1], "current_year": r[2]} for r in rows]


def approve_promotion(conn, instructor_id: int) -> None:
    """進級を承認する: 学年を1つ進め、確認年度を記録する。"""
    current_fy = get_current_academic_fiscal_year()
    row = conn.execute("SELECT academic_year FROM INSTRUCTORS WHERE instructor_id = ?", (instructor_id,)).fetchone()
    if row is None:
        raise ValueError(f"instructor_id={instructor_id} が見つかりません")
    current_year = row[0]
    if current_year not in _NEXT_YEAR:
        raise ValueError("この講師は既に学年の上限に達しています")
    conn.execute(
        "UPDATE INSTRUCTORS SET academic_year = ?, academic_year_confirmed_fiscal_year = ? WHERE instructor_id = ?",
        (_NEXT_YEAR[current_year], current_fy, instructor_id),
    )
    conn.commit()


def decline_promotion(conn, instructor_id: int) -> None:
    """留年など、学年を据え置く場合。確認年度だけ記録し、学年はそのまま。"""
    current_fy = get_current_academic_fiscal_year()
    conn.execute(
        "UPDATE INSTRUCTORS SET academic_year_confirmed_fiscal_year = ? WHERE instructor_id = ?",
        (current_fy, instructor_id),
    )
    conn.commit()


def acknowledge_at_max(conn, instructor_id: int) -> None:
    """上限到達者を「確認済み」にする(学年はそのまま、確認年度だけ更新)。"""
    decline_promotion(conn, instructor_id)


def render(qs: dict, message_html: str = "") -> str:
    conn = get_conn()
    pending = get_pending_promotions(conn)
    at_max = get_at_max_unconfirmed(conn)
    conn.close()

    pending_html = ""
    for p in pending:
        pending_html += f"""
        <tr>
          <td>{p['name']}</td>
          <td>{p['current_year']} → {p['proposed_year']}</td>
          <td style="display:flex;gap:6px;">
            <form method="POST" action="/instructor-academic-year">
              <input type="hidden" name="action" value="approve">
              <input type="hidden" name="instructor_id" value="{p['instructor_id']}">
              <button type="submit">進級を承認する</button>
            </form>
            <form method="POST" action="/instructor-academic-year">
              <input type="hidden" name="action" value="decline">
              <input type="hidden" name="instructor_id" value="{p['instructor_id']}">
              <button type="submit" style="background:#5F5E5A;">留年(据え置き)</button>
            </form>
          </td>
        </tr>
        """
    pending_table = (
        f"<table><tr><th>講師名</th><th>学年</th><th>対応</th></tr>{pending_html}</table>"
        if pending else '<div class="hint">進級案の対象者はいません</div>'
    )

    at_max_html = ""
    for a in at_max:
        at_max_html += f"""
        <tr>
          <td>{a['name']}</td>
          <td>{a['current_year']}（上限）</td>
          <td>
            <form method="POST" action="/instructor-academic-year">
              <input type="hidden" name="action" value="acknowledge">
              <input type="hidden" name="instructor_id" value="{a['instructor_id']}">
              <button type="submit" style="background:#5F5E5A;">確認済みにする</button>
            </form>
          </td>
        </tr>
        """
    at_max_table = (
        f"<table><tr><th>講師名</th><th>学年</th><th>対応</th></tr>{at_max_html}</table>"
        if at_max else '<div class="hint">上限到達で要確認の講師はいません</div>'
    )

    return f"""
    <h1>講師 学年更新の確認</h1>
    <div class="hint">4月1日以降、まだ今年度の学年を確認していない講師がここに表示されます。本人がこの画面でボタンを押して確認してください</div>
    {message_html}
    <h1 style="font-size:14px;color:#534AB7;margin-top:24px;">進級案（承認 or 留年を選んでください）</h1>
    {pending_table}
    <h1 style="font-size:14px;color:#993C1D;margin-top:28px;">学年が上限に達している講師（卒業・進路を個別に確認してください）</h1>
    {at_max_table}
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    action = get("action")
    instructor_id = int(get("instructor_id"))

    if action == "approve":
        approve_promotion(conn, instructor_id)
        message_html = '<div class="msg success">進級を承認しました</div>'
    elif action == "decline":
        decline_promotion(conn, instructor_id)
        message_html = '<div class="msg success">学年を据え置きました</div>'
    elif action == "acknowledge":
        acknowledge_at_max(conn, instructor_id)
        message_html = '<div class="msg success">確認済みにしました</div>'
    else:
        raise ValueError(f"不明な action です: {action}")

    return message_html, {}
