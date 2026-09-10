"""確定済み画像レビューの訂正履歴を追加する画面。"""

import html

from db import get_conn
from review_corrections import correct_resolved_review
from subject_resolution import list_manual_subject_options


ITEM_LABELS = {
    "STUDENT_MATCH": "生徒",
    "COUNT_AMBIGUOUS": "受講回数",
    "SUBJECT_MATCH": "科目",
    "AVAILABILITY_AMBIGUOUS": "対応可能時間",
    "SLOT_NOT_FOUND": "再照合した対応可能時間",
}


def _latest_resolved_items(conn, batch_id: int):
    return conn.execute(
        """
        SELECT r.review_item_id,r.page_id,p.page_number,r.item_type,r.resolution,
               r.corrected_value_text,r.resolved_at,r.related_subject_enrollment_id,
               r.related_availability_id
        FROM IMAGE_IMPORT_REVIEW_ITEMS r
        JOIN IMAGE_IMPORT_PAGES p ON p.page_id=r.page_id
        WHERE p.batch_id=? AND p.is_deleted=0 AND r.is_deleted=0
          AND r.resolution<>'PENDING'
          AND r.item_type IN ('STUDENT_MATCH','COUNT_AMBIGUOUS','SUBJECT_MATCH',
                              'AVAILABILITY_AMBIGUOUS','SLOT_NOT_FOUND')
          AND NOT EXISTS (
            SELECT 1 FROM IMAGE_IMPORT_REVIEW_ITEMS n
            WHERE n.page_id=r.page_id AND n.item_type=r.item_type
              AND n.review_item_id>r.review_item_id AND n.is_deleted=0
              AND (
                r.item_type='STUDENT_MATCH' OR
                (r.related_subject_enrollment_id IS NOT NULL
                 AND n.related_subject_enrollment_id=r.related_subject_enrollment_id) OR
                (r.related_availability_id IS NOT NULL
                 AND n.related_availability_id=r.related_availability_id)
              )
          )
        ORDER BY p.page_number,r.review_item_id
        """,
        (batch_id,),
    ).fetchall()


def render(qs: dict, message_html: str = "") -> str:
    try:
        batch_id = int(qs.get("batch_id", [""])[0])
    except (TypeError, ValueError):
        return '<div class="msg error">バッチIDが不正です</div>'
    conn = get_conn()
    batch = conn.execute(
        """
        SELECT b.batch_id,c.camp_name,b.paper_fiscal_year,b.paper_type,b.status
        FROM IMAGE_IMPORT_BATCHES b LEFT JOIN CAMPS c ON c.camp_id=b.camp_id
        WHERE b.batch_id=? AND b.is_deleted=0
        """,
        (batch_id,),
    ).fetchone()
    if not batch:
        conn.close()
        return '<div class="msg error">指定されたバッチが見つかりません</div>'
    instructors = conn.execute(
        "SELECT instructor_id,last_name||first_name FROM INSTRUCTORS "
        "WHERE status='在籍' ORDER BY last_name_kana,first_name_kana,instructor_id"
    ).fetchall()
    students = conn.execute(
        "SELECT student_id,last_name||first_name FROM STUDENTS "
        "WHERE enrollment_status='在籍' ORDER BY last_name_kana,first_name_kana,student_id"
    ).fetchall()
    items = _latest_resolved_items(conn, batch_id)
    operator_options = '<option value="">担当者を選択してください</option>' + "".join(
        f'<option value="{row[0]}">{html.escape(row[1])}</option>' for row in instructors
    )
    student_options = "".join(
        f'<option value="{row[0]}">{html.escape(row[1])}</option>' for row in students
    )
    cards = []
    for item in items:
        review_id, page_id, page_number, item_type, resolution, corrected, resolved_at, enrollment_id, availability_id = item
        current = "-"
        control = ""
        if item_type == "STUDENT_MATCH":
            row = conn.execute(
                """
                SELECT s.last_name||s.first_name FROM IMAGE_IMPORT_PAGE_STUDENTS ps
                JOIN STUDENTS s ON s.student_id=ps.candidate_student_id
                WHERE ps.page_id=? AND ps.is_selected=1 AND ps.is_deleted=0
                """,
                (page_id,),
            ).fetchone()
            current = row[0] if row else "未選択"
            control = f'<select name="replacement_value" required><option value="">生徒を選択</option>{student_options}</select>'
        elif item_type == "COUNT_AMBIGUOUS":
            row = conn.execute(
                "SELECT subject_row_label,resolved_count FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS WHERE import_enrollment_id=?",
                (enrollment_id,),
            ).fetchone()
            current = f"{row[0]}：{row[1]}回" if row else "対象なし"
            control = '<input type="number" name="replacement_value" min="0" max="99" required style="width:100px;">'
        elif item_type == "SUBJECT_MATCH":
            row = conn.execute(
                """
                SELECT e.subject_row_label,s.subject_group,s.subject_name
                FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS e
                LEFT JOIN SUBJECTS s ON s.subject_id=e.resolved_subject_id
                WHERE e.import_enrollment_id=?
                """,
                (enrollment_id,),
            ).fetchone()
            current = f"{row[0]}：{row[1]}/{row[2]}" if row and row[2] else "未確定"
            options = "".join(
                f'<option value="{opt[0]}">{html.escape(opt[1] + "/" + opt[2])}</option>'
                for opt in list_manual_subject_options(conn, page_id=page_id)
            )
            control = f'<select name="replacement_value" required><option value="">科目を選択</option>{options}</select>'
        else:
            row = conn.execute(
                """
                SELECT session_date,period_number,resolved_is_available
                FROM IMAGE_IMPORT_AVAILABILITY WHERE import_availability_id=?
                """,
                (availability_id,),
            ).fetchone()
            current = (
                f"{row[0]} {row[1]}限：{'対応可' if row[2] else '対応不可'}"
                if row and row[2] is not None else "未確定"
            )
            control = (
                '<select name="replacement_value" required><option value="">選択</option>'
                '<option value="1">対応可</option><option value="0">対応不可</option></select>'
            )
        disabled = batch[4] == "IMPORTED"
        form = '<div class="hint">本登録済みのため、この画面では訂正できません。</div>' if disabled else f"""
          <form method="POST" action="/image-import-corrections" class="operator-required-form">
            <input type="hidden" name="action" value="correct_review">
            <input type="hidden" name="batch_id" value="{batch_id}">
            <input type="hidden" name="review_item_id" value="{review_id}">
            <label>訂正後の値 <span class="req">*</span></label>{control}
            <label>訂正理由 <span class="req">*</span></label>
            <textarea name="reason" rows="2" maxlength="1000" required></textarea>
            <label>訂正担当者 <span class="req">*</span></label>
            <select name="operator_instructor_id" class="remembered-operator" required>{operator_options}</select>
            <button type="submit">新しい訂正履歴を作成</button>
          </form>
        """
        cards.append(f"""
        <section style="border-top:1px solid #ddd;margin-top:20px;padding-top:16px;">
          <h1 style="font-size:15px;">{page_number}ページ目・{ITEM_LABELS[item_type]}</h1>
          <div class="hint">レビュー#{review_id} ／ {resolution} ／ {html.escape(resolved_at or '-')}</div>
          <p>現在値：<strong>{html.escape(current)}</strong></p>
          {form}
        </section>
        """)
    conn.close()
    empty = '<div class="hint">訂正可能な確定済み項目はありません。</div>' if not cards else ""
    return f"""
    <h1>確定済みレビューの訂正</h1>
    <div class="hint">{html.escape(batch[1] or '通常授業')} ／ {batch[2]}年度 {html.escape(batch[3])} ／ {batch[4]}</div>
    {message_html}
    <p><a href="/image-import-review?batch_id={batch_id}">← 取込結果確認へ戻る</a></p>
    <div class="hint">元の判断は変更せず、新しいレビュー項目と監査ログを追加します。</div>
    {empty}{''.join(cards)}
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

    if get("action") != "correct_review":
        raise ValueError("不明な操作です")
    try:
        batch_id = int(get("batch_id"))
        review_item_id = int(get("review_item_id"))
        replacement_value = int(get("replacement_value"))
        operator_id = int(get("operator_instructor_id"))
    except (TypeError, ValueError):
        raise ValueError("訂正値と担当者を選択してください")
    new_id = correct_resolved_review(
        conn,
        review_item_id=review_item_id,
        replacement_value=replacement_value,
        reason=get("reason"),
        operator_instructor_id=operator_id,
    )
    message = f"元の判断を保持したまま、訂正履歴#{new_id}を作成しました"
    return f'<div class="msg success">{html.escape(message)}</div>', {"batch_id": [str(batch_id)]}
