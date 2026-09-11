"""休校日の一覧・追加・削除を行う設定ページ。"""

from __future__ import annotations

import datetime
import html
import sqlite3

from db import get_conn


def add_closure_date(conn, closure_date: str, closure_name: str) -> None:
    try:
        datetime.date.fromisoformat(closure_date)
    except ValueError as exc:
        raise ValueError("休校日は YYYY-MM-DD 形式で入力してください") from exc
    closure_name = closure_name.strip()
    if not closure_name:
        raise ValueError("休校名を入力してください")
    try:
        conn.execute(
            "INSERT INTO CLOSURE_DATES(closure_date,closure_name) VALUES(?,?)",
            (closure_date, closure_name),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ValueError(f"{closure_date} は既に休校日として登録されています") from exc


def delete_closure_date(conn, closure_date: str) -> None:
    result = conn.execute("DELETE FROM CLOSURE_DATES WHERE closure_date=?", (closure_date,))
    if result.rowcount == 0:
        conn.rollback()
        raise ValueError("削除対象の休校日が見つかりません")
    conn.commit()


def render(qs: dict, message_html: str = "") -> str:
    conn = get_conn()
    rows = conn.execute(
        "SELECT closure_date,closure_name FROM CLOSURE_DATES ORDER BY closure_date DESC"
    ).fetchall()
    conn.close()
    rows_html = "".join(
        f"""<tr><td>{html.escape(closure_date)}</td><td>{html.escape(closure_name)}</td><td>
        <form class="row-form" method="POST" action="/closure-dates"
              onsubmit="return confirm('この休校日を削除しますか？');">
          <input type="hidden" name="action" value="delete">
          <input type="hidden" name="closure_date" value="{html.escape(closure_date)}">
          <button class="btn-remove" type="submit">削除</button>
        </form></td></tr>"""
        for closure_date, closure_name in rows
    ) or '<tr><td colspan="3" class="hint">休校日の登録はありません</td></tr>'
    return f"""
    <h1>休校日設定</h1>
    <div class="hint">通常授業・講習会で共通利用する休校日を登録します。過去日も登録できます。</div>
    {message_html}
    <form method="POST" action="/closure-dates">
      <input type="hidden" name="action" value="add">
      <label>休校日 <span class="req">*</span></label>
      <input type="date" name="closure_date" required>
      <label>休校名 <span class="req">*</span></label>
      <input type="text" name="closure_name" placeholder="例: 夏期休校日" required>
      <button type="submit">休校日を追加する</button>
    </form>
    <table><tr><th>日付</th><th>休校名</th><th></th></tr>{rows_html}</table>
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    action = fields.get("action", [""])[0]
    closure_date = fields.get("closure_date", [""])[0]
    if action == "add":
        add_closure_date(conn, closure_date, fields.get("closure_name", [""])[0])
        return '<div class="msg success">休校日を登録しました</div>', {}
    if action == "delete":
        delete_closure_date(conn, closure_date)
        return '<div class="msg success">休校日を削除しました</div>', {}
    raise ValueError(f"不明な action です: {action}")
