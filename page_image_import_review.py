"""画像取込バッチの読み取り専用確認画面。"""

import html

from db import get_conn
from student_image_matching import confirm_student_by_master, confirm_student_match
from handwritten_counts import confirm_subject_counts
from availability_recognition import confirm_availability_cells
from subject_resolution import (
    confirm_page_subjects,
    list_manual_subject_options,
    resolve_page_subjects,
)
from image_import_commit import build_import_plan, execute_import_batch
from image_import_commit import DEFAULT_BACKUP_DIR
from image_import_config import load_backup_directory
from image_import_page_fallback import restore_skipped_page, skip_problem_page


def render(qs: dict, message_html: str = "") -> str:
    try:
        batch_id = int(qs.get("batch_id", [""])[0])
    except ValueError:
        return '<div class="msg error">バッチIDが不正です</div>'

    conn = get_conn()
    batch = conn.execute(
        """
        SELECT b.batch_id,c.camp_name,b.paper_fiscal_year,b.paper_type,b.status,b.camp_id
        FROM IMAGE_IMPORT_BATCHES b JOIN CAMPS c ON c.camp_id=b.camp_id
        WHERE b.batch_id=? AND b.is_deleted=0
        """,
        (batch_id,),
    ).fetchone()
    pages = conn.execute(
        """
        SELECT page_id,page_number,recognized_name_text,recognized_grade_text,
               layout_quality,processing_status
        FROM IMAGE_IMPORT_PAGES
        WHERE batch_id=? AND is_deleted=0 ORDER BY page_number
        """,
        (batch_id,),
    ).fetchall()
    instructors = conn.execute(
        "SELECT instructor_id,last_name||first_name FROM INSTRUCTORS "
        "WHERE status='在籍' ORDER BY last_name_kana,first_name_kana,instructor_id"
    ).fetchall()
    active_students = conn.execute(
        "SELECT student_id,last_name||first_name FROM STUDENTS WHERE enrollment_status='在籍' "
        "ORDER BY last_name_kana,first_name_kana,student_id"
    ).fetchall()
    import_plan = build_import_plan(conn, batch_id=batch_id)
    candidate_map = {}
    count_map = {}
    subject_map = {}
    subject_options_map = {}
    availability_summary_map = {}
    ambiguous_availability_map = {}
    attachment_map = {}
    for page in pages:
        candidate_map[page[0]] = conn.execute(
            """
            SELECT ps.candidate_rank,s.last_name||s.first_name,ps.match_confidence,
                   ps.match_status,ps.is_selected,ps.page_student_id,ps.candidate_student_id
            FROM IMAGE_IMPORT_PAGE_STUDENTS ps
            LEFT JOIN STUDENTS s ON s.student_id=ps.candidate_student_id
            WHERE ps.page_id=? AND ps.is_deleted=0
            ORDER BY ps.candidate_rank
            """,
            (page[0],),
        ).fetchall()
        count_map[page[0]] = conn.execute(
            """
            SELECT e.import_enrollment_id,e.subject_row_label,e.count_status,e.resolved_count,
                   r.review_item_id
            FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS e
            LEFT JOIN IMAGE_IMPORT_REVIEW_ITEMS r
              ON r.related_subject_enrollment_id=e.import_enrollment_id
             AND r.item_type='COUNT_AMBIGUOUS' AND r.resolution='PENDING' AND r.is_deleted=0
            WHERE e.page_id=? AND e.is_deleted=0 ORDER BY e.import_enrollment_id
            """,
            (page[0],),
        ).fetchall()
        subject_map[page[0]] = conn.execute(
            """
            SELECT e.import_enrollment_id,e.subject_row_label,e.resolved_count,
                   e.subject_match_status,sub.subject_group,sub.subject_name,r.review_item_id
            FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS e
            LEFT JOIN SUBJECTS sub ON sub.subject_id=e.resolved_subject_id
            LEFT JOIN IMAGE_IMPORT_REVIEW_ITEMS r
              ON r.related_subject_enrollment_id=e.import_enrollment_id
             AND r.item_type='SUBJECT_MATCH' AND r.resolution='PENDING' AND r.is_deleted=0
            WHERE e.page_id=? AND e.is_deleted=0 AND COALESCE(e.resolved_count,0)>0
            ORDER BY e.import_enrollment_id
            """,
            (page[0],),
        ).fetchall()
        subject_options_map[page[0]] = list_manual_subject_options(conn, page_id=page[0])
        availability_summary_map[page[0]] = dict(conn.execute(
            """
            SELECT availability_status,COUNT(*) FROM IMAGE_IMPORT_AVAILABILITY
            WHERE page_id=? AND is_deleted=0 GROUP BY availability_status
            """,
            (page[0],),
        ).fetchall())
        ambiguous_availability_map[page[0]] = conn.execute(
            """
            SELECT a.import_availability_id,a.session_date,a.period_number,
                   a.ink_ratio,a.line_crossing_score,r.review_item_id
            FROM IMAGE_IMPORT_AVAILABILITY a
            JOIN IMAGE_IMPORT_REVIEW_ITEMS r
              ON r.related_availability_id=a.import_availability_id
             AND r.item_type='AVAILABILITY_AMBIGUOUS'
             AND r.resolution='PENDING' AND r.is_deleted=0
            WHERE a.page_id=? AND a.availability_status='AMBIGUOUS' AND a.is_deleted=0
            ORDER BY a.session_date,a.period_number
            """,
            (page[0],),
        ).fetchall()
        attachment_map[page[0]] = conn.execute(
            """
            SELECT attachment_id,attachment_type,crop_image_path
            FROM IMAGE_IMPORT_ATTACHMENTS
            WHERE page_id=? AND is_deleted=0 ORDER BY attachment_id
            """,
            (page[0],),
        ).fetchall()
    conn.close()
    if not batch:
        return '<div class="msg error">指定されたバッチが見つかりません</div>'

    page_cards = ""
    for page_id, page_number, recognized_name, recognized_grade, quality, status in pages:
        is_skipped = status == "SKIPPED"
        candidates = candidate_map[page_id]
        has_selected = any(row[4] for row in candidates)
        selected_candidate = next((row for row in candidates if row[4]), None)
        candidate_rows = "".join(
            f"<tr><td>{'<input type=\"radio\" name=\"page_student_id\" value=\"' + str(row[5]) + '\" required>' if not is_skipped and not has_selected and row[5] else ''}</td>"
            f"<td>{row[0] or '-'}</td><td>{html.escape(row[1] or '候補なし')}</td>"
            f"<td>{f'{row[2]:.3f}' if row[2] is not None else '-'}</td>"
            f"<td>{row[3]}</td><td>{'確定' if row[4] else ''}</td></tr>"
            for row in candidates
        ) or '<tr><td colspan="6">候補なし</td></tr>'
        operator_options = '<option value="">担当者を選択してください</option>' + "".join(
            f'<option value="{row[0]}">{html.escape(row[1])}</option>' for row in instructors
        )
        confirm_form_start = (
            f'<form method="POST" action="/image-import-review" class="operator-required-form">'
            f'<input type="hidden" name="action" value="confirm_student">'
            f'<input type="hidden" name="batch_id" value="{batch_id}">'
            f'<input type="hidden" name="page_id" value="{page_id}">'
            if not is_skipped and not has_selected and candidates else ""
        )
        confirm_controls = (
            f'<label>確認した担当者 <span class="req">*</span></label>'
            f'<select name="operator_instructor_id" class="remembered-operator" required>{operator_options}</select>'
            f'<div class="operator-warning" style="font-size:12px;color:#993C1D;margin-top:5px;">担当者を選択しないと確定できません</div>'
            f'<button type="submit">選択した生徒で確定</button></form>'
            if confirm_form_start else ""
        )
        manual_student_form = ""
        if not is_skipped and not has_selected:
            manual_student_options = '<option value="">生徒を選択してください</option>' + "".join(
                f'<option value="{row[0]}">{html.escape(row[1])}</option>' for row in active_students
            )
            manual_student_form = f"""
            <details style="margin-top:12px;">
              <summary style="font-size:13px;cursor:pointer;color:#534AB7;">候補にいない生徒をマスタから選ぶ</summary>
              <form method="POST" action="/image-import-review" class="operator-required-form">
                <input type="hidden" name="action" value="confirm_student_master">
                <input type="hidden" name="batch_id" value="{batch_id}">
                <input type="hidden" name="page_id" value="{page_id}">
                <label>在籍生徒 <span class="req">*</span></label>
                <select name="student_id" required>{manual_student_options}</select>
                <label>確認した担当者 <span class="req">*</span></label>
                <select name="operator_instructor_id" class="remembered-operator" required>{operator_options}</select>
                <button type="submit">この生徒で確定</button>
              </form>
            </details>
            """
        count_rows = count_map[page_id]
        has_pending_counts = not is_skipped and any(row[2] == "AMBIGUOUS" and row[4] for row in count_rows)
        count_table_rows = "".join(
            f"<tr><td>{html.escape(row[1])}</td>"
            f"<td>{'<img src=\"/image-import-review-crop?review_item_id=' + str(row[4]) + '\" style=\"width:130px;max-height:55px;object-fit:contain;border:1px solid #ddd;\">' if row[4] else '空欄'}</td>"
            f"<td>{'<input type=\"number\" name=\"count_' + str(row[0]) + '\" min=\"0\" max=\"99\" required style=\"width:90px;\">' if not is_skipped and row[2] == 'AMBIGUOUS' and row[4] else str(row[3] if row[3] is not None else '-')}</td>"
            f"<td>{row[2]}</td></tr>"
            for row in count_rows
        )
        count_form = ""
        if count_rows:
            count_form_start = (
                f'<form method="POST" action="/image-import-review" class="operator-required-form">'
                f'<input type="hidden" name="action" value="confirm_counts">'
                f'<input type="hidden" name="batch_id" value="{batch_id}">'
                f'<input type="hidden" name="page_id" value="{page_id}">'
                if has_pending_counts else ""
            )
            count_controls = (
                f'<label>確認した担当者 <span class="req">*</span></label>'
                f'<select name="operator_instructor_id" class="remembered-operator" required>{operator_options}</select>'
                f'<div class="operator-warning" style="font-size:12px;color:#993C1D;margin-top:5px;">担当者を選択しないと確定できません</div>'
                f'<button type="submit">表示された回数をまとめて確定</button></form>'
                if has_pending_counts else ""
            )
            count_form = (
                f'<h1 style="font-size:14px;margin-top:22px;">お申込回数</h1>{count_form_start}'
                f'<table><tr><th>科目</th><th>記入画像</th><th>確定回数</th><th>状態</th></tr>{count_table_rows}</table>'
                f'{count_controls}'
            )
        subject_rows = subject_map[page_id]
        pending_subjects = [] if is_skipped else [
            row for row in subject_rows if row[3] in ("AMBIGUOUS", "NOT_FOUND") and row[6]
        ]
        subject_form = ""
        if subject_rows:
            subject_options = '<option value="">科目を選択してください</option>' + "".join(
                f'<option value="{row[0]}">{html.escape(row[1] + "/" + row[2] + (("（" + row[3] + "）") if row[3] else ""))}</option>'
                for row in subject_options_map[page_id]
            )
            subject_table_rows = "".join(
                f'<tr><td>{html.escape(row[1])}</td><td>{row[2]}回</td>'
                f'<td>{html.escape((row[4] + "/" + row[5]) if row[5] else "未確定")}</td>'
                f'<td>{row[3]}</td><td>'
                f'{("<select name=\"subject_" + str(row[0]) + "\" required>" + subject_options + "</select>") if row in pending_subjects else ""}'
                f'</td></tr>'
                for row in subject_rows
            )
            form_start = (
                f'<form method="POST" action="/image-import-review" class="operator-required-form">'
                f'<input type="hidden" name="action" value="confirm_subjects">'
                f'<input type="hidden" name="batch_id" value="{batch_id}">'
                f'<input type="hidden" name="page_id" value="{page_id}">'
                if pending_subjects else ""
            )
            controls = (
                f'<label>確認した担当者 <span class="req">*</span></label>'
                f'<select name="operator_instructor_id" class="remembered-operator" required>{operator_options}</select>'
                f'<div class="operator-warning" style="font-size:12px;color:#993C1D;margin-top:5px;">担当者を選択しないと確定できません</div>'
                f'<button type="submit">選択した科目をまとめて確定</button></form>'
                if pending_subjects else ""
            )
            subject_form = (
                f'<h1 style="font-size:14px;margin-top:22px;">受講科目</h1>{form_start}'
                f'<table><tr><th>用紙の行</th><th>回数</th><th>具体科目</th><th>状態</th><th>職員選択</th></tr>{subject_table_rows}</table>'
                f'{controls}'
            )
        availability_summary = availability_summary_map[page_id]
        availability_summary_html = " ／ ".join(
            f"{label}: {availability_summary.get(status, 0)}"
            for status, label in (
                ("AUTO_AVAILABLE", "対応可"),
                ("AUTO_UNAVAILABLE", "対応不可"),
                ("MANUALLY_CONFIRMED", "職員確定"),
                ("AMBIGUOUS", "要確認"),
                ("SLOT_NOT_FOUND", "枠未登録"),
                ("OUT_OF_CAMP_RANGE", "期間外"),
            )
        )
        ambiguous_cells = [] if is_skipped else ambiguous_availability_map[page_id]
        availability_form = ""
        if availability_summary:
            ambiguous_rows = "".join(
                f'<tr><td>{row[1]}</td><td>{row[2]}限</td>'
                f'<td><img src="/image-import-review-crop?review_item_id={row[5]}" '
                f'style="width:90px;max-height:60px;object-fit:contain;border:1px solid #ddd;"></td>'
                f'<td>{row[3]:.3f}</td><td>{row[4]:.3f}</td>'
                f'<td><select name="availability_{row[0]}" required style="width:120px;">'
                f'<option value="">選択</option><option value="1">対応可</option>'
                f'<option value="0">対応不可</option></select></td></tr>'
                for row in ambiguous_cells
            )
            if ambiguous_cells:
                availability_form = (
                    f'<h1 style="font-size:14px;margin-top:22px;">対応可能時間</h1>'
                    f'<div class="hint">{availability_summary_html}</div>'
                    f'<form method="POST" action="/image-import-review" class="operator-required-form">'
                    f'<input type="hidden" name="action" value="confirm_availability">'
                    f'<input type="hidden" name="batch_id" value="{batch_id}">'
                    f'<input type="hidden" name="page_id" value="{page_id}">'
                    f'<table><tr><th>日付</th><th>限</th><th>画像</th><th>黒画素率</th><th>線</th><th>確定</th></tr>{ambiguous_rows}</table>'
                    f'<label>確認した担当者 <span class="req">*</span></label>'
                    f'<select name="operator_instructor_id" class="remembered-operator" required>{operator_options}</select>'
                    f'<div class="operator-warning" style="font-size:12px;color:#993C1D;margin-top:5px;">担当者を選択しないと確定できません</div>'
                    f'<button type="submit">曖昧なマスをまとめて確定</button></form>'
                )
            else:
                availability_form = (
                    f'<h1 style="font-size:14px;margin-top:22px;">対応可能時間</h1>'
                    f'<div class="hint">{availability_summary_html}</div>'
                )
        attachment_rows = attachment_map[page_id]
        attachment_html = ""
        if attachment_rows:
            cards = "".join(
                f'<div><div class="hint" style="margin-bottom:4px;">{html.escape(row[1])}</div>'
                f'<a href="/image-import-attachment?attachment_id={row[0]}" target="_blank">'
                f'<img src="/image-import-attachment?attachment_id={row[0]}" '
                f'style="width:100%;max-height:160px;object-fit:contain;border:1px solid #ddd;"></a></div>'
                for row in attachment_rows
            )
            attachment_html = (
                '<h1 style="font-size:14px;margin-top:22px;">自由記述・科目詳細</h1>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">{cards}</div>'
            )
        fallback_html = ""
        if is_skipped:
            fallback_html = f"""
            <div class="msg error">このページは自動取込対象から除外されています。</div>
            <form method="POST" action="/image-import-review" class="operator-required-form">
              <input type="hidden" name="action" value="restore_page">
              <input type="hidden" name="batch_id" value="{batch_id}">
              <input type="hidden" name="page_id" value="{page_id}">
              <label>復帰操作の担当者 <span class="req">*</span></label>
              <select name="operator_instructor_id" class="remembered-operator" required>{operator_options}</select>
              <button type="submit">このページを確認待ちへ戻す</button>
            </form>
            """
        elif quality != "OK":
            manual_links = ""
            if selected_candidate:
                manual_links = (
                    f'<div style="margin-top:10px;font-size:13px;">手入力先: '
                    f'<a href="/camp-enrollments?camp_id={batch[5]}&student_id={selected_candidate[6]}">受講科目・回数</a> ／ '
                    f'<a href="/camp-availability-student?camp_id={batch[5]}&student_id={selected_candidate[6]}">対応可能時間</a></div>'
                )
            fallback_html = f"""
            <div class="msg error">画質または帳票構造に問題があります。このページはそのまま本登録できません。</div>
            {manual_links}
            <form method="POST" action="/image-import-review" class="operator-required-form">
              <input type="hidden" name="action" value="skip_page">
              <input type="hidden" name="batch_id" value="{batch_id}">
              <input type="hidden" name="page_id" value="{page_id}">
              <label>対応方法 <span class="req">*</span></label>
              <select name="fallback_mode" required>
                <option value="">選択してください</option>
                <option value="RESCAN">この1枚を再スキャンする</option>
                <option value="MANUAL">既存画面で手入力する</option>
              </select>
              <label>操作担当者 <span class="req">*</span></label>
              <select name="operator_instructor_id" class="remembered-operator" required>{operator_options}</select>
              <button type="submit">対応方法を記録して自動取込から除外</button>
            </form>
            """
        page_cards += f"""
        <section style="border-top:1px solid #ddd;margin-top:26px;padding-top:20px;">
          <h1 style="font-size:16px;">{page_number}ページ目</h1>
          <div class="hint">画質: {quality} ／ 処理状態: {status}</div>
          {fallback_html}
          <div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;align-items:start;">
            <a href="/image-import-preview?page_id={page_id}&kind=corrected" target="_blank">
              <img src="/image-import-preview?page_id={page_id}&kind=corrected" alt="補正済みページ"
                   style="width:100%;max-height:620px;object-fit:contain;border:1px solid #ddd;">
            </a>
            <div>
              <div class="hint" style="margin-bottom:4px;">氏名欄</div>
              <img src="/image-import-preview?page_id={page_id}&kind=student_name"
                   style="width:100%;border:1px solid #ddd;">
              <div class="hint" style="margin:14px 0 4px;">学年欄</div>
              <img src="/image-import-preview?page_id={page_id}&kind=grade"
                   style="width:55%;border:1px solid #ddd;">
              <p style="font-size:13px;">認識: {html.escape(recognized_name or '-')}／{html.escape(recognized_grade or '-')}</p>
            </div>
          </div>
          {confirm_form_start}
          <table><tr><th>選択</th><th>順位</th><th>生徒候補</th><th>総合確信度</th><th>状態</th><th></th></tr>{candidate_rows}</table>
          {confirm_controls}
          {manual_student_form}
          {count_form}
          {subject_form}
          {availability_form}
          {attachment_html}
        </section>
        """

    global_operator_options = '<option value="">担当者を選択してください</option>' + "".join(
        f'<option value="{row[0]}">{html.escape(row[1])}</option>' for row in instructors
    )
    issues = import_plan.blockers + import_plan.conflicts
    issue_html = "".join(f"<li>{html.escape(issue)}</li>" for issue in issues)
    if batch[4] == "IMPORTED":
        import_panel = '<div class="msg success">このバッチは本登録済みです。</div>'
    elif import_plan.can_import:
        import_panel = f"""
        <div class="msg success">本登録可能です。受講科目: 新規{len(import_plan.enrollment_inserts)}件・更新{len(import_plan.enrollment_updates)}件、
        対応可能時間: 新規{len(import_plan.availability_inserts)}件・更新{len(import_plan.availability_updates)}件</div>
        <form method="POST" action="/image-import-review" class="operator-required-form">
          <input type="hidden" name="action" value="import_batch">
          <input type="hidden" name="batch_id" value="{batch_id}">
          <label>本登録を実行する担当者 <span class="req">*</span></label>
          <select name="operator_instructor_id" class="remembered-operator" required>{global_operator_options}</select>
          <div class="operator-warning" style="font-size:12px;color:#993C1D;margin-top:5px;">担当者を選択しないと本登録できません</div>
          <button type="submit">バックアップを作成して本登録する</button>
        </form>
        """
    else:
        import_panel = f'<div class="msg error">本登録前に解決が必要です。<ul>{issue_html}</ul></div>'

    return f"""
    <h1>取込結果確認: バッチ#{batch[0]}</h1>
    <div class="hint">{html.escape(batch[1])} ／ {batch[2]}年度 {html.escape(batch[3])} ／ {batch[4]}</div>
    {message_html}
    <a href="/image-import" style="font-size:13px;">← PDF取り込みへ戻る</a>
    <h1 style="font-size:15px;margin-top:24px;">本登録前の確認</h1>
    {import_panel}
    {page_cards}
    <script>
    (() => {{
      const key = 'imageImportOperatorInstructorId';
      document.querySelectorAll('.remembered-operator').forEach(select => {{
        const saved = localStorage.getItem(key);
        if (saved && [...select.options].some(option => option.value === saved)) select.value = saved;
        const warning = select.parentElement.querySelector('.operator-warning');
        const refresh = () => warning.style.display = select.value ? 'none' : 'block';
        select.addEventListener('change', () => {{ localStorage.setItem(key, select.value); refresh(); }});
        refresh();
      }});
    }})();
    </script>
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    action = get("action")
    if action not in ("confirm_student", "confirm_student_master", "confirm_counts", "confirm_subjects", "confirm_availability", "import_batch", "skip_page", "restore_page"):
        raise ValueError("不明な操作です")
    try:
        batch_id = int(get("batch_id"))
        operator_id = int(get("operator_instructor_id"))
    except (TypeError, ValueError):
        raise ValueError("対象項目と担当者を選択してください")
    if action == "import_batch":
        plan, backup_path = execute_import_batch(
            conn, batch_id=batch_id, operator_instructor_id=operator_id,
            backup_dir=load_backup_directory(DEFAULT_BACKUP_DIR),
        )
        changed = (
            len(plan.enrollment_inserts) + len(plan.enrollment_updates)
            + len(plan.availability_inserts) + len(plan.availability_updates)
        )
        message = f"本登録が完了しました（変更{changed}件）。直前バックアップ: {backup_path}"
        return f'<div class="msg success">{html.escape(message)}</div>', {"batch_id": [str(batch_id)]}
    try:
        page_id = int(get("page_id"))
    except (TypeError, ValueError):
        raise ValueError("対象ページを選択してください")
    if action == "skip_page":
        skip_problem_page(
            conn, page_id=page_id, fallback_mode=get("fallback_mode"),
            operator_instructor_id=operator_id,
        )
        message = "対応方法を記録し、このページを自動取込対象から除外しました"
        return f'<div class="msg success">{message}</div>', {"batch_id": [str(batch_id)]}
    if action == "restore_page":
        restore_skipped_page(
            conn, page_id=page_id, operator_instructor_id=operator_id,
        )
        message = "除外したページを確認待ちへ戻しました"
        return f'<div class="msg success">{message}</div>', {"batch_id": [str(batch_id)]}
    if action == "confirm_student":
        try:
            page_student_id = int(get("page_student_id"))
        except (TypeError, ValueError):
            raise ValueError("生徒候補を選択してください")
        confirm_student_match(
            conn, page_id=page_id, page_student_id=page_student_id,
            operator_instructor_id=operator_id,
        )
        resolve_page_subjects(conn, page_id=page_id)
        message = "生徒を確定し、担当者付きで監査履歴を保存しました"
    elif action == "confirm_student_master":
        try:
            student_id = int(get("student_id"))
        except (TypeError, ValueError):
            raise ValueError("在籍生徒を選択してください")
        confirm_student_by_master(
            conn, page_id=page_id, student_id=student_id,
            operator_instructor_id=operator_id,
        )
        resolve_page_subjects(conn, page_id=page_id)
        message = "生徒マスタから生徒を確定し、担当者付きで監査履歴を保存しました"
    elif action == "confirm_counts":
        confirmed_counts = {}
        for key, values in fields.items():
            if key.startswith("count_"):
                try:
                    confirmed_counts[int(key.removeprefix("count_"))] = int(values[0])
                except (TypeError, ValueError):
                    raise ValueError("回数は0〜99の整数で入力してください")
        confirmed = confirm_subject_counts(
            conn, page_id=page_id, confirmed_counts=confirmed_counts,
            operator_instructor_id=operator_id,
        )
        resolution = resolve_page_subjects(conn, page_id=page_id)
        message = f"{confirmed}科目の回数を確定し、監査履歴を保存しました"
        if resolution["matched"] or resolution["review"]:
            message += f"（科目: 自動確定{resolution['matched']}件、要確認{resolution['review']}件）"
    elif action == "confirm_subjects":
        confirmed_subjects = {}
        for key, values in fields.items():
            if key.startswith("subject_"):
                try:
                    confirmed_subjects[int(key.removeprefix("subject_"))] = int(values[0])
                except (TypeError, ValueError):
                    raise ValueError("具体科目を選択してください")
        confirmed = confirm_page_subjects(
            conn, page_id=page_id, confirmed_subjects=confirmed_subjects,
            operator_instructor_id=operator_id,
        )
        message = f"{confirmed}科目を確定し、監査履歴を保存しました"
    else:
        confirmed_values = {}
        for key, values in fields.items():
            if key.startswith("availability_"):
                try:
                    confirmed_values[int(key.removeprefix("availability_"))] = int(values[0])
                except (TypeError, ValueError):
                    raise ValueError("各マスについて対応可または対応不可を選択してください")
        confirmed = confirm_availability_cells(
            conn, page_id=page_id, confirmed_values=confirmed_values,
            operator_instructor_id=operator_id,
        )
        message = f"{confirmed}マスを確定し、監査履歴を保存しました"
    return f'<div class="msg success">{message}</div>', {
        "batch_id": [str(batch_id)]
    }
