# -*- coding: utf-8 -*-
"""page_terms.py: 学期マスタ(TERMS)の登録"""

from datetime import date
from db import get_conn


def _validate_date(label, value):
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} は YYYY-MM-DD 形式で入力してください")


def insert_term(conn, term_name, start_date, end_date) -> int:
    if not term_name.strip():
        raise ValueError("term_name を入力してください")
    _validate_date("start_date", start_date)
    _validate_date("end_date", end_date)
    if start_date >= end_date:
        raise ValueError("start_date は end_date より前の日付にしてください")
    cur = conn.execute(
        "INSERT INTO TERMS (term_name, start_date, end_date) VALUES (?, ?, ?)",
        (term_name.strip(), start_date, end_date),
    )
    conn.commit()
    return cur.lastrowid


def list_terms(conn) -> list[tuple[int, str]]:
    """他のページ(対応可能時間)からも参照される、共有の一覧取得関数。"""
    return conn.execute("SELECT term_id, term_name FROM TERMS ORDER BY start_date").fetchall()


def render(qs: dict, message_html: str = "") -> str:
    conn = get_conn()
    rows = conn.execute("SELECT term_id, term_name, start_date, end_date FROM TERMS ORDER BY start_date").fetchall()
    conn.close()
    list_html = "".join(f"<li>#{r[0]} {r[1]}（{r[2]} 〜 {r[3]}）</li>" for r in rows) or "<li>まだ登録がありません</li>"
    return f"""
    <h1>学期マスタ登録</h1>
    {message_html}
    <form method="POST" action="/terms">
      <label>学期名 <span class="req">*</span></label>
      <input type="text" name="term_name" required placeholder="例: 2026年度前期">
      <label>開始日 <span class="req">*</span></label>
      <input type="date" name="start_date" required>
      <label>終了日 <span class="req">*</span></label>
      <input type="date" name="end_date" required>
      <button type="submit">登録する</button>
    </form>
    <ul style="list-style:none;padding:0;font-size:13px;margin-top:20px;">{list_html}</ul>
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    new_id = insert_term(conn, get("term_name"), get("start_date"), get("end_date"))
    message_html = f'<div class="msg success">登録しました → term_id={new_id} / {get("term_name")}</div>'
    return message_html, {}
