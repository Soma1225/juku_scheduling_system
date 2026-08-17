# -*- coding: utf-8 -*-
"""page_instructors.py: 講師(INSTRUCTORS)の新規登録"""

from db import get_conn


def insert_instructor(conn, last_name, first_name, last_name_kana, first_name_kana) -> int:
    if not last_name.strip() or not first_name.strip():
        raise ValueError("姓・名を入力してください")
    if not last_name_kana.strip() or not first_name_kana.strip():
        raise ValueError("ふりがな(姓・名)を入力してください")
    cur = conn.execute(
        "INSERT INTO INSTRUCTORS (last_name, first_name, last_name_kana, first_name_kana) VALUES (?, ?, ?, ?)",
        (last_name.strip(), first_name.strip(), last_name_kana.strip(), first_name_kana.strip()),
    )
    conn.commit()
    return cur.lastrowid


def list_instructors(conn) -> list[tuple[int, str]]:
    """他のページ(担当科目・対応可能時間)からも参照される、共有の一覧取得関数。"""
    return conn.execute(
        "SELECT instructor_id, last_name || ' ' || first_name FROM INSTRUCTORS "
        "ORDER BY last_name_kana, first_name_kana"
    ).fetchall()


def render(qs: dict, message_html: str = "") -> str:
    conn = get_conn()
    rows = conn.execute(
        "SELECT instructor_id, last_name, first_name, last_name_kana, first_name_kana "
        "FROM INSTRUCTORS ORDER BY last_name_kana, first_name_kana"
    ).fetchall()
    conn.close()
    list_html = "".join(f"<li>#{r[0]} {r[1]} {r[2]}（{r[3]}{r[4]}）</li>" for r in rows) or "<li>まだ登録がありません</li>"
    return f"""
    <h1>講師 新規登録</h1>
    {message_html}
    <form method="POST" action="/instructors">
      <label>姓 <span class="req">*</span></label>
      <input type="text" name="last_name" required placeholder="例: 田中">
      <label>名 <span class="req">*</span></label>
      <input type="text" name="first_name" required placeholder="例: 太郎">
      <label>姓(ふりがな) <span class="req">*</span></label>
      <input type="text" name="last_name_kana" required placeholder="例: たなか">
      <label>名(ふりがな) <span class="req">*</span></label>
      <input type="text" name="first_name_kana" required placeholder="例: たろう">
      <button type="submit">登録する</button>
    </form>
    <h1 style="font-size:14px;margin-top:28px;">登録済みの講師 ({len(rows)}名)</h1>
    <ul style="list-style:none;padding:0;font-size:13px;">{list_html}</ul>
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    new_id = insert_instructor(conn, get("last_name"), get("first_name"), get("last_name_kana"), get("first_name_kana"))
    message_html = (
        f'<div class="msg success">登録しました → instructor_id={new_id} / '
        f'{get("last_name")} {get("first_name")}</div>'
    )
    return message_html, {}
