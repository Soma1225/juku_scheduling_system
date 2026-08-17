# -*- coding: utf-8 -*-
"""
page_camps.py

講習会(CAMPS)の登録。
登録すると同時に、その期間分の TIME_SLOTS(日付×1〜5限) を自動生成する。
(講習会は「予定期間から前後にはみ出す可能性がある」ため、
 実際には計画期間の前後に少し余裕を持たせて生成する)
"""

from datetime import date, timedelta
from db import get_conn

BUFFER_DAYS = 14  # 計画期間の前後にこれだけ余裕を持ってTIME_SLOTSを生成しておく


def _validate_date(label, value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} は YYYY-MM-DD 形式で入力してください")


def ensure_time_slots_for_range(conn, start_date: str, end_date: str, buffer_days: int = BUFFER_DAYS) -> int:
    """
    指定期間(前後にbuffer_days日ずつ余裕を持たせる)について、
    1〜5限のTIME_SLOTSを生成する。既存の行はスキップされる(UNIQUE制約+INSERT OR IGNORE)。
    戻り値: 新規作成された行数
    """
    start = date.fromisoformat(start_date) - timedelta(days=buffer_days)
    end = date.fromisoformat(end_date) + timedelta(days=buffer_days)

    periods = [row[0] for row in conn.execute("SELECT period_number FROM PERIODS").fetchall()]
    if not periods:
        raise ValueError("PERIODSが未登録です。先に時限マスタを設定してください")

    rows = []
    d = start
    while d <= end:
        for p in periods:
            rows.append((d.isoformat(), p))
        d += timedelta(days=1)

    before = conn.execute("SELECT COUNT(*) FROM TIME_SLOTS").fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO TIME_SLOTS (session_date, period_number) VALUES (?, ?)", rows
    )
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM TIME_SLOTS").fetchone()[0]
    return after - before


def insert_camp(conn, camp_name: str, planned_start_date: str, planned_end_date: str) -> tuple[int, int]:
    if not camp_name.strip():
        raise ValueError("camp_name を入力してください")
    start = _validate_date("planned_start_date", planned_start_date)
    end = _validate_date("planned_end_date", planned_end_date)
    if start >= end:
        raise ValueError("planned_start_date は planned_end_date より前の日付にしてください")

    cur = conn.execute(
        "INSERT INTO CAMPS (camp_name, planned_start_date, planned_end_date) VALUES (?, ?, ?)",
        (camp_name.strip(), planned_start_date, planned_end_date),
    )
    conn.commit()
    camp_id = cur.lastrowid

    n_slots = ensure_time_slots_for_range(conn, planned_start_date, planned_end_date)
    return camp_id, n_slots


def list_camps(conn) -> list[tuple[int, str]]:
    """他のページ(受講契約・可用時間)からも参照される、共有の一覧取得関数。"""
    return conn.execute("SELECT camp_id, camp_name FROM CAMPS ORDER BY planned_start_date DESC").fetchall()


def render(qs: dict, message_html: str = "") -> str:
    conn = get_conn()
    rows = conn.execute(
        "SELECT camp_id, camp_name, planned_start_date, planned_end_date FROM CAMPS ORDER BY planned_start_date DESC"
    ).fetchall()
    conn.close()
    list_html = "".join(
        f"<li>#{r[0]} {r[1]}（{r[2]} 〜 {r[3]}）</li>" for r in rows
    ) or "<li>まだ登録がありません</li>"
    return f"""
    <h1>講習会マスタ登録</h1>
    <div class="hint">登録すると、期間の前後{BUFFER_DAYS}日を含めた日付枠(TIME_SLOTS)が自動生成されます</div>
    {message_html}
    <form method="POST" action="/camps">
      <label>講習会名 <span class="req">*</span></label>
      <input type="text" name="camp_name" required placeholder="例: 2026年 春期講習">
      <label>組んでほしい開始日 <span class="req">*</span></label>
      <input type="date" name="planned_start_date" required>
      <label>組んでほしい終了日 <span class="req">*</span></label>
      <input type="date" name="planned_end_date" required>
      <button type="submit">登録する</button>
    </form>
    <ul style="list-style:none;padding:0;font-size:13px;margin-top:20px;">{list_html}</ul>
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    camp_id, n_slots = insert_camp(conn, get("camp_name"), get("planned_start_date"), get("planned_end_date"))
    message_html = (
        f'<div class="msg success">登録しました → camp_id={camp_id} / {get("camp_name")}'
        f'（日付枠を{n_slots}件新規生成）</div>'
    )
    return message_html, {}
