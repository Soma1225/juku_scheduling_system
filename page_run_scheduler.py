# -*- coding: utf-8 -*-
"""
page_run_scheduler.py

Web画面から、講習会のスケジューリング(scheduler.run_scheduler_for_camp)を
実行するページ。

スケジューリングは数十分〜最大2時間程度かかる可能性があるため、
ボタンを押した後はバックグラウンドスレッドで処理を進め、
画面自体はすぐに操作可能な状態に戻す。進捗はページの再読み込みで確認する。
"""

import threading
import time
from db import get_conn
from page_camps import list_camps

# 講習会ごとの実行状況を、メモリ上で保持する。
# {camp_id: {"status": "idle"|"running"|"done"|"error", "started_at": ..., "result": {...}, "error": "..."}}
_JOBS: dict[int, dict] = {}
_JOBS_LOCK = threading.Lock()

TIME_LIMIT_OPTIONS = [
    (300, "5分(お試し・動作確認用)"),
    (1800, "30分"),
    (3600, "1時間"),
    (7200, "2時間(推奨・最も精度が高い)"),
]


def _get_job_status(camp_id: int) -> dict:
    with _JOBS_LOCK:
        return dict(_JOBS.get(camp_id, {"status": "idle"}))


def _run_in_background(camp_id: int, time_limit_seconds: float) -> None:
    """バックグラウンドスレッドの実体。自前でDB接続を持つ(呼び出し元のconnとは別)。"""
    from scheduler import run_scheduler_for_camp  # 重いimportなので使う直前に読み込む

    with _JOBS_LOCK:
        _JOBS[camp_id] = {"status": "running", "started_at": time.time()}

    conn = get_conn()
    try:
        result = run_scheduler_for_camp(conn, camp_id, time_limit_seconds=time_limit_seconds)
        with _JOBS_LOCK:
            _JOBS[camp_id] = {"status": "done", "started_at": _JOBS[camp_id]["started_at"], "result": result}
    except Exception as e:
        with _JOBS_LOCK:
            _JOBS[camp_id] = {
                "status": "error", "started_at": _JOBS[camp_id]["started_at"], "error": str(e),
            }
    finally:
        conn.close()


def start_scheduling(camp_id: int, time_limit_seconds: float) -> None:
    """既に実行中でなければ、新しいバックグラウンドスレッドを起動する。"""
    with _JOBS_LOCK:
        current = _JOBS.get(camp_id, {"status": "idle"})
        if current.get("status") == "running":
            raise ValueError("この講習会は既に実行中です")

    thread = threading.Thread(target=_run_in_background, args=(camp_id, time_limit_seconds), daemon=True)
    thread.start()


def render(qs: dict, message_html: str = "") -> str:
    camp_id = qs.get("camp_id", [""])[0]
    conn = get_conn()
    camps = list_camps(conn)
    conn.close()

    def options(rows, selected=""):
        return "".join(
            f'<option value="{i}"{" selected" if str(i) == selected else ""}>{name}</option>' for i, name in rows
        )

    body_html = ""
    if camp_id:
        job = _get_job_status(int(camp_id))
        status = job.get("status", "idle")

        if status == "running":
            elapsed_min = (time.time() - job["started_at"]) / 60
            body_html = f"""
            <div class="msg success">実行中です(経過時間: 約{elapsed_min:.0f}分)。
            このままページを開いたままにしても、他の画面に移動しても構いません。
            終わったかどうかは、このページを再読み込みして確認してください。</div>
            <button onclick="location.reload()">最新状況に更新する</button>
            """
        elif status == "done":
            result = job["result"]
            unfulfilled_total = sum(result["unfulfilled"].values())
            unfulfilled_html = ""
            if result["unfulfilled"]:
                rows = ""
                for enrollment_id, count in result["unfulfilled"].items():
                    rows += f"<tr><td>enrollment_id={enrollment_id}</td><td>{count}コマ</td></tr>"
                unfulfilled_html = f"""
                <h1 style="font-size:14px;color:#993C1D;margin-top:20px;">未割当のコマ</h1>
                <table><tr><th>契約</th><th>未割当コマ数</th></tr>{rows}</table>
                """
            body_html = f"""
            <div class="msg success">
              完了しました(ステータス: {result['status']})<br>
              作成されたセッション数: {result['n_sessions']}件 / 割当数: {result['n_assignments']}件<br>
              未割当の合計: {unfulfilled_total}コマ
            </div>
            {unfulfilled_html}
            <form method="POST" action="/run-scheduler" style="margin-top:20px;">
              <input type="hidden" name="camp_id" value="{camp_id}">
              <label>もう一度実行する場合の制限時間</label>
              <select name="time_limit_seconds">
                {"".join(f'<option value="{v}">{label}</option>' for v, label in TIME_LIMIT_OPTIONS)}
              </select>
              <button type="submit">もう一度実行する</button>
            </form>
            """
        elif status == "error":
            body_html = f'<div class="msg error">エラーが発生しました: {job.get("error")}</div>'
        else:
            body_html = f"""
            <form method="POST" action="/run-scheduler">
              <input type="hidden" name="camp_id" value="{camp_id}">
              <label>制限時間</label>
              <select name="time_limit_seconds">
                {"".join(f'<option value="{v}"{" selected" if v == 7200 else ""}>{label}</option>' for v, label in TIME_LIMIT_OPTIONS)}
              </select>
              <div class="hint">制限時間を長くするほど、より質の高い(無駄のない・希望に沿った)時間割になる可能性が高くなります</div>
              <button type="submit">スケジューリングを実行する</button>
            </form>
            """

    return f"""
    <h1>スケジューリングの実行</h1>
    <div class="hint">講習会を選んで実行してください。実行中も他の画面を操作できます</div>
    {message_html}
    <label>講習会</label>
    <select onchange="location.href='/run-scheduler?camp_id='+this.value">
      <option value="">選択してください</option>{options(camps, camp_id)}
    </select>
    {body_html if camp_id else '<div class="hint">先に講習会を選択してください</div>'}
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    camp_id = get("camp_id")
    time_limit = float(get("time_limit_seconds") or 7200)

    start_scheduling(int(camp_id), time_limit)
    message_html = '<div class="msg success">スケジューリングを開始しました。バックグラウンドで実行されます</div>'
    return message_html, {"camp_id": [camp_id]}
