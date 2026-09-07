"""講習会用紙の配布・回収状況を確認するページ。"""

import html

from camp_form_tracking import (
    add_manual_target,
    list_tracking_rows,
    mark_all_distributed,
    sync_regular_targets,
    update_distribution_status,
)
from db import get_conn


STATUS_LABELS = {
    "TARGET": "未配布",
    "DISTRIBUTED": "配布済み・未回収",
    "RETURNED": "回収済み",
    "EXEMPT": "配布対象外",
}
SOURCE_LABELS = {
    "AUTO_REGULAR": "通常授業から自動",
    "MANUAL_EXCEPTION": "例外として追加",
    "SCAN_DISCOVERED": "スキャン時に検出",
}


def _operator_options(instructors) -> str:
    return '<option value="">担当者を選択してください</option>' + "".join(
        f'<option value="{row[0]}">{html.escape(row[1])}</option>' for row in instructors
    )


def render(qs: dict, message_html: str = "") -> str:
    camp_id_text = qs.get("camp_id", [""])[0]
    try:
        camp_id = int(camp_id_text) if camp_id_text else None
    except ValueError:
        camp_id = None
    conn = get_conn()
    camps = conn.execute(
        "SELECT camp_id,camp_name,planned_start_date,planned_end_date FROM CAMPS ORDER BY planned_start_date DESC"
    ).fetchall()
    instructors = conn.execute(
        "SELECT instructor_id,last_name||first_name FROM INSTRUCTORS WHERE status='在籍' "
        "ORDER BY last_name_kana,first_name_kana,instructor_id"
    ).fetchall()
    students = conn.execute(
        "SELECT student_id,last_name||first_name FROM STUDENTS WHERE enrollment_status='在籍' "
        "ORDER BY last_name_kana,first_name_kana,student_id"
    ).fetchall()
    rows = list_tracking_rows(conn, camp_id=camp_id) if camp_id else []
    conn.close()

    camp_options = '<option value="">講習会を選択してください</option>' + "".join(
        f'<option value="{row[0]}"{" selected" if row[0] == camp_id else ""}>'
        f'{html.escape(row[1])}（{row[2]}〜{row[3]}）</option>' for row in camps
    )
    operator_options = _operator_options(instructors)
    student_options = '<option value="">生徒を選択してください</option>' + "".join(
        f'<option value="{row[0]}">{html.escape(row[1])}</option>' for row in students
    )
    summary = {status: 0 for status in STATUS_LABELS}
    for row in rows:
        summary[row[4]] += 1
    summary_html = " ／ ".join(f"{label}: {summary[status]}名" for status, label in STATUS_LABELS.items())

    row_html = "".join(
        f"""
        <tr>
          <td>{html.escape(row[2])}</td>
          <td>{html.escape(SOURCE_LABELS[row[3]])}</td>
          <td>{html.escape(STATUS_LABELS[row[4]])}</td>
          <td>{row[7]}ページ{'（重複確認）' if row[7] > 1 else ''}</td>
          <td>{html.escape(row[8] or '自動処理')}</td>
          <td>
            <form method="POST" action="/camp-form-tracking" class="row-form operator-required-form">
              <input type="hidden" name="action" value="update_status">
              <input type="hidden" name="camp_id" value="{camp_id}">
              <input type="hidden" name="distribution_id" value="{row[0]}">
              <select name="status" required style="width:145px;">
                <option value="">状態を変更</option>
                <option value="DISTRIBUTED">配布済み</option>
                <option value="RETURNED">手動で回収済み</option>
                <option value="EXEMPT">配布対象外</option>
              </select>
              <select name="operator_instructor_id" class="remembered-operator" required style="width:145px;">{operator_options}</select>
              <button type="submit" class="btn-update">更新</button>
            </form>
          </td>
        </tr>
        """ for row in rows
    ) or '<tr><td colspan="6">配布候補はまだ作成されていません</td></tr>'

    controls = ""
    if camp_id:
        controls = f"""
        <div class="hint">通常授業の個別指導契約から候補を作った後、実際に配った生徒を「配布済み」にします。</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
          <form method="POST" action="/camp-form-tracking" class="operator-required-form">
            <input type="hidden" name="action" value="sync_targets"><input type="hidden" name="camp_id" value="{camp_id}">
            <label>操作担当者 <span class="req">*</span></label>
            <select name="operator_instructor_id" class="remembered-operator" required>{operator_options}</select>
            <button type="submit">通常授業から配布候補を更新</button>
          </form>
          <form method="POST" action="/camp-form-tracking" class="operator-required-form">
            <input type="hidden" name="action" value="mark_all_distributed"><input type="hidden" name="camp_id" value="{camp_id}">
            <label>操作担当者 <span class="req">*</span></label>
            <select name="operator_instructor_id" class="remembered-operator" required>{operator_options}</select>
            <button type="submit">未配布候補を一括で配布済みにする</button>
          </form>
        </div>
        <form method="POST" action="/camp-form-tracking" class="operator-required-form" style="margin-top:18px;">
          <input type="hidden" name="action" value="add_exception"><input type="hidden" name="camp_id" value="{camp_id}">
          <h1 style="font-size:14px;">例外生徒の追加</h1>
          <div class="hint">戦略指導のみだが、講習会では個別指導を受ける生徒などを追加します。</div>
          <select name="student_id" required>{student_options}</select>
          <label>操作担当者 <span class="req">*</span></label>
          <select name="operator_instructor_id" class="remembered-operator" required>{operator_options}</select>
          <button type="submit">配布候補へ追加</button>
        </form>
        <h1 style="font-size:15px;margin-top:28px;">配布・回収一覧</h1>
        <div class="hint">{summary_html}</div>
        <table><tr><th>生徒</th><th>追加理由</th><th>状態</th><th>認識済み</th><th>最終更新者</th><th>操作</th></tr>{row_html}</table>
        """

    return f"""
    <h1>講習会用紙 配布・回収確認</h1>
    <div class="hint">誰に配ったかを記録し、PDF取込後に未回収と重複回収を確認します。</div>
    {message_html}
    <label>対象講習会</label>
    <select onchange="location.href='/camp-form-tracking?camp_id='+this.value">{camp_options}</select>
    {controls}
    <script>
    (() => {{
      const key = 'imageImportOperatorInstructorId';
      document.querySelectorAll('.remembered-operator').forEach(select => {{
        const saved = localStorage.getItem(key);
        if (saved && [...select.options].some(option => option.value === saved)) select.value = saved;
        select.addEventListener('change', () => localStorage.setItem(key, select.value));
      }});
    }})();
    </script>
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    try:
        camp_id = int(get("camp_id"))
        operator_id = int(get("operator_instructor_id"))
    except (TypeError, ValueError):
        raise ValueError("講習会と操作担当者を選択してください")
    action = get("action")
    if action == "sync_targets":
        count = sync_regular_targets(conn, camp_id=camp_id, operator_instructor_id=operator_id)
        message = f"通常授業から配布候補を更新しました（新規{count}名）"
    elif action == "mark_all_distributed":
        count = mark_all_distributed(conn, camp_id=camp_id, operator_instructor_id=operator_id)
        message = f"{count}名を配布済みにしました"
    elif action == "add_exception":
        try:
            student_id = int(get("student_id"))
        except (TypeError, ValueError):
            raise ValueError("例外として追加する生徒を選択してください")
        add_manual_target(
            conn, camp_id=camp_id, student_id=student_id, operator_instructor_id=operator_id,
        )
        message = "例外生徒を配布候補へ追加しました"
    elif action == "update_status":
        try:
            distribution_id = int(get("distribution_id"))
        except (TypeError, ValueError):
            raise ValueError("更新する配布記録を選択してください")
        update_distribution_status(
            conn, distribution_id=distribution_id, status=get("status"),
            operator_instructor_id=operator_id,
        )
        message = "配布状態を更新しました"
    else:
        raise ValueError("不明な操作です")
    return f'<div class="msg success">{html.escape(message)}</div>', {"camp_id": [str(camp_id)]}
