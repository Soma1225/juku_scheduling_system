# -*- coding: utf-8 -*-
"""
page_run_scheduler.py

Web画面から、講習会のスケジューリング(scheduler.run_scheduler_for_camp)を
実行するページ。

スケジューリングは数十分〜最大2時間程度かかる可能性があるため、
ボタンを押した後はバックグラウンドスレッドで処理を進め、
画面自体はすぐに操作可能な状態に戻す。進捗はページの再読み込みで確認する。
"""

import html
import threading
import time
from db import get_conn
from page_camps import list_camps
from camp_schedule_excel_export import CampScheduleExportError, validate_camp_schedule_export

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


def _run_in_background(
    camp_id: int, time_limit_seconds: float, target_student_ids: set[int] | None = None
) -> None:
    """バックグラウンドスレッドの実体。自前でDB接続を持つ(呼び出し元のconnとは別)。"""
    from scheduler import run_scheduler_for_camp  # 重いimportなので使う直前に読み込む

    with _JOBS_LOCK:
        _JOBS[camp_id] = {"status": "running", "started_at": time.time()}

    conn = get_conn()
    try:
        result = run_scheduler_for_camp(
            conn,
            camp_id,
            time_limit_seconds=time_limit_seconds,
            target_student_ids=target_student_ids,
        )
        with _JOBS_LOCK:
            _JOBS[camp_id] = {"status": "done", "started_at": _JOBS[camp_id]["started_at"], "result": result}
    except Exception as e:
        with _JOBS_LOCK:
            _JOBS[camp_id] = {
                "status": "error", "started_at": _JOBS[camp_id]["started_at"], "error": str(e),
            }
    finally:
        conn.close()


def start_scheduling(
    camp_id: int, time_limit_seconds: float, target_student_ids: set[int] | None = None
) -> None:
    """既に実行中でなければ、新しいバックグラウンドスレッドを起動する。"""
    with _JOBS_LOCK:
        current = _JOBS.get(camp_id, {"status": "idle"})
        if current.get("status") == "running":
            raise ValueError("この講習会は既に実行中です")

    thread = threading.Thread(
        target=_run_in_background,
        args=(camp_id, time_limit_seconds, target_student_ids),
        daemon=True,
    )
    thread.start()


def _render_run_forms(camp_id: str, students: list[tuple[int, str]]) -> str:
    time_options = "".join(
        f'<option value="{value}"{" selected" if value == 7200 else ""}>{label}</option>'
        for value, label in TIME_LIMIT_OPTIONS
    )
    student_options = "".join(
        f'<label style="display:block;margin:4px 0;">'
        f'<input type="checkbox" name="target_student_ids" value="{student_id}"> '
        f'{html.escape(student_name)}</label>'
        for student_id, student_name in students
    )
    if not student_options:
        student_options = '<div class="hint">この講習会には対象となる生徒契約がありません</div>'

    return f"""
    <section style="margin-top:20px;padding:16px;border:1px solid #ddd;border-radius:8px;">
      <h2 style="font-size:16px;margin-top:0;">講習会全体をスケジューリング</h2>
      <form method="POST" action="/run-scheduler">
        <input type="hidden" name="camp_id" value="{camp_id}">
        <input type="hidden" name="mode" value="full">
        <label>制限時間</label>
        <select name="time_limit_seconds">{time_options}</select>
        <div class="hint">全生徒・全科目を自由に組み直します</div>
        <button type="submit">全体スケジューリングを実行する</button>
      </form>
    </section>
    <section style="margin-top:20px;padding:16px;border:1px solid #ddd;border-radius:8px;">
      <h2 style="font-size:16px;margin-top:0;">対象生徒を選んで部分再計算</h2>
      <div class="hint">対象外の通常授業継続科目は必ず固定します。対象生徒だけで解けない場合に限り、対象外の講習会限定科目を動かします。</div>
      <form method="POST" action="/run-scheduler">
        <input type="hidden" name="camp_id" value="{camp_id}">
        <input type="hidden" name="mode" value="partial">
        <label>対象生徒（複数選択可）</label>
        <div style="max-height:240px;overflow:auto;border:1px solid #ddd;padding:8px;margin-bottom:12px;">
          {student_options}
        </div>
        <label>制限時間</label>
        <select name="time_limit_seconds">{time_options}</select>
        <button type="submit"{" disabled" if not students else ""}>選択した生徒を再計算する</button>
      </form>
    </section>
    """


def render(qs: dict, message_html: str = "") -> str:
    camp_id = qs.get("camp_id", [""])[0]
    conn = get_conn()
    camps = list_camps(conn)
    students = []
    export_rows = []
    export_errors = []
    if camp_id:
        students = conn.execute(
            """SELECT DISTINCT st.student_id, st.last_name || ' ' || st.first_name
               FROM CAMP_COURSE_ENROLLMENTS e
               JOIN STUDENTS st ON st.student_id = e.student_id
               WHERE e.camp_id = ?
               ORDER BY st.last_name_kana, st.first_name_kana""",
            (int(camp_id),),
        ).fetchall()
        try:
            _, export_rows, export_errors = validate_camp_schedule_export(conn, int(camp_id))
        except CampScheduleExportError as exc:
            export_errors = [str(exc)]
    conn.close()

    def options(rows, selected=""):
        return "".join(
            f'<option value="{i}"{" selected" if str(i) == selected else ""}>{name}</option>' for i, name in rows
        )

    body_html = ""
    export_html = ""
    if camp_id:
        if export_errors and export_rows:
            rows = "".join(f"<li>{html.escape(error)}</li>" for error in export_errors)
            export_html = f"""
            <section style="margin-top:20px;padding:16px;border:1px solid #ddd;border-radius:8px;">
              <h2 style="font-size:16px;margin-top:0;">現行フォーマット Excel出力</h2>
              <div class="msg error">テンプレートに収まらないため出力できません。<ul>{rows}</ul></div>
            </section>
            """
        elif export_errors:
            export_html = f"""
            <section style="margin-top:20px;padding:16px;border:1px solid #ddd;border-radius:8px;">
              <h2 style="font-size:16px;margin-top:0;">現行フォーマット Excel出力</h2>
              <div class="hint">{html.escape(export_errors[0])}</div>
            </section>
            """
        else:
            export_html = f"""
            <section style="margin-top:20px;padding:16px;border:1px solid #ddd;border-radius:8px;">
              <h2 style="font-size:16px;margin-top:0;">現行フォーマット Excel出力</h2>
              <div class="hint">確定済みの割当を、25人×5コマ・1シート7日形式で出力します。</div>
              <button type="button" onclick="location.href='/camp-schedule-export?camp_id={camp_id}'">Excelをダウンロードする</button>
            </section>
            """
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

            partial_html = ""
            if result.get("partial_recalculation"):
                feasible = result["status"] in {"OPTIMAL", "FEASIBLE"}
                if feasible:
                    changed_rows = "".join(
                        "<tr>"
                        f"<td>{html.escape(item.get('student_name', ''))}</td>"
                        f"<td>{html.escape(item.get('subject_name', ''))}</td>"
                        f"<td>{html.escape(item['before_display'])}</td>"
                        f"<td>{html.escape(item['after_display'])}</td>"
                        f"<td>{'対象外・講習会限定科目' if item.get('is_non_target_limited') else '対象生徒'}</td>"
                        "</tr>"
                        for item in result.get("changed_assignments", [])
                    )
                    if not changed_rows:
                        changed_rows = '<tr><td colspan="5">変更された割当はありません</td></tr>'
                    rescue_warning = ""
                    if result.get("moved_non_target_limited_enrollment_ids"):
                        rescue_warning = (
                            '<div class="msg error">対象生徒だけでは解けなかったため、対象外生徒の'
                            '講習会限定科目も移動しました。下表の「対象外・講習会限定科目」を確認してください。</div>'
                        )
                    elif result.get("relaxation_used"):
                        rescue_warning = (
                            '<div class="hint">救済段階まで計算しましたが、対象外生徒の割当変更はありません。</div>'
                        )
                    partial_html = f"""
                    <h2 style="font-size:16px;margin-top:20px;">部分再計算の変更内容</h2>
                    <div>変更: {result.get('n_changed_enrollments', 0)}契約 /
                    変更なし: {result.get('n_unchanged_enrollments', 0)}契約</div>
                    {rescue_warning}
                    <table><tr><th>生徒</th><th>科目</th><th>変更前</th>
                    <th>変更後</th><th>区分</th></tr>{changed_rows}</table>
                    """
                else:
                    partial_html = """
                    <div class="msg error">
                      部分再計算で実行可能な時間割を見つけられませんでした。
                      既存の時間割は削除・変更せず、そのまま保持しています。
                    </div>
                    """
            completion_class = "success" if result["status"] in {"OPTIMAL", "FEASIBLE"} else "error"
            body_html = f"""
            <div class="msg {completion_class}">
              完了しました(ステータス: {result['status']})<br>
              現在のセッション数: {result['n_sessions']}件 / 割当数: {result['n_assignments']}件<br>
              未割当の合計: {unfulfilled_total}コマ
            </div>
            {unfulfilled_html}
            {partial_html}
            {_render_run_forms(camp_id, students)}
            """
        elif status == "error":
            body_html = (
                f'<div class="msg error">エラーが発生しました: {html.escape(job.get("error", ""))}</div>'
                + _render_run_forms(camp_id, students)
            )
        else:
            body_html = _render_run_forms(camp_id, students)

    return f"""
    <h1>スケジューリングの実行</h1>
    <div class="hint">講習会を選んで実行してください。実行中も他の画面を操作できます</div>
    {message_html}
    <label>講習会</label>
    <select onchange="location.href='/run-scheduler?camp_id='+this.value">
      <option value="">選択してください</option>{options(camps, camp_id)}
    </select>
    {body_html if camp_id else '<div class="hint">先に講習会を選択してください</div>'}
    {export_html}
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    camp_id = get("camp_id")
    time_limit = float(get("time_limit_seconds") or 7200)
    mode = get("mode", "full")

    target_student_ids = None
    if mode == "partial":
        try:
            target_student_ids = {
                int(value) for value in fields.get("target_student_ids", []) if value
            }
        except ValueError as exc:
            raise ValueError("対象生徒の指定が不正です") from exc
        if not target_student_ids:
            raise ValueError("部分再計算の対象生徒を1人以上選択してください")

    start_scheduling(int(camp_id), time_limit, target_student_ids=target_student_ids)
    action_name = "部分再計算" if target_student_ids is not None else "全体スケジューリング"
    message_html = f'<div class="msg success">{action_name}を開始しました。バックグラウンドで実行されます</div>'
    return message_html, {"camp_id": [camp_id]}
