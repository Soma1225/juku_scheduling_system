# -*- coding: utf-8 -*-
"""page_periods.py: 時限マスタ(PERIODS)の編集。追記型ではなく、固定5行を編集する形。"""

from db import get_conn


def _validate_time(label, value):
    parts = value.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"{label} は HH:MM 形式で入力してください")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"{label} の時刻が不正です")


def update_period(conn, period_number, start_time, end_time) -> None:
    _validate_time("start_time", start_time)
    _validate_time("end_time", end_time)
    if start_time >= end_time:
        raise ValueError("start_time は end_time より前にしてください")
    conn.execute(
        "UPDATE PERIODS SET start_time = ?, end_time = ? WHERE period_number = ?",
        (start_time, end_time, period_number),
    )
    conn.commit()


def render(qs: dict, message_html: str = "") -> str:
    conn = get_conn()
    rows = conn.execute("SELECT period_number, start_time, end_time FROM PERIODS ORDER BY period_number").fetchall()
    conn.close()
    rows_html = "".join(
        f'<tr><td>{p}限</td>'
        f'<td><input type="text" name="start_{p}" value="{s}"></td>'
        f'<td><input type="text" name="end_{p}" value="{e}"></td></tr>'
        for p, s, e in rows
    )
    return f"""
    <h1>時限マスタ編集</h1>
    <div class="hint">1〜5限は固定です。実際の時刻と異なる場合のみ書き換えて保存してください</div>
    {message_html}
    <form method="POST" action="/periods">
      <table><tr><th>限</th><th>開始時刻</th><th>終了時刻</th></tr>{rows_html}</table>
      <button type="submit">保存する</button>
    </form>
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    for p in range(1, 6):
        update_period(conn, p, get(f"start_{p}"), get(f"end_{p}"))
    return '<div class="msg success">保存しました</div>', {}
