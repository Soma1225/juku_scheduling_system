"""「時間割一覧」スナップショットから生徒・通常授業を取り込む画面。"""

from datetime import date
import html

from timetable_snapshot_import import import_timetable_snapshot


def render(qs: dict, message_html: str = "") -> str:
    selected_date = html.escape(qs.get("effective_start_date", [date.today().isoformat()])[0])
    return f"""
    <h1>生徒・通常授業 Excel取り込み</h1>
    <div class="hint">
      現行システムからコピーしたExcelの「時間割一覧」シートを読み込み、
      生徒マスタと通常授業パターンを一括登録します。元ファイルへの書き込みは行いません。
    </div>
    {message_html}
    <form method="POST" action="/excel-import" enctype="multipart/form-data">
      <input type="hidden" name="action" value="import">
      <label>Excelスナップショット <span class="req">*</span></label>
      <input type="file" name="excel_file" accept=".xlsx" required>
      <label>通常授業の適用開始日 <span class="req">*</span></label>
      <input type="date" name="effective_start_date" value="{selected_date}" required>
      <button type="submit">生徒・通常授業を取り込む</button>
    </form>
    <div class="hint" style="margin-top:18px;">
      先に「講師マスタ Excel取り込み」を実行してください。教員略称・学年コード・科目を
      完全一致で判定できない行はスキップされ、エラー一覧に表示されます（他の行は登録されます）。
    </div>
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    action = fields.get("action", [""])[0]
    if action != "import":
        raise ValueError(f"不明な action です: {action}")
    files = fields.get("_files", {})
    if "excel_file" not in files:
        raise ValueError("Excelファイルを選択してください")
    file_info = files["excel_file"]
    if not file_info["filename"].lower().endswith(".xlsx"):
        raise ValueError("拡張子が.xlsxのスナップショットを選択してください")
    start_date = fields.get("effective_start_date", [date.today().isoformat()])[0]
    qs = {"effective_start_date": [start_date]}

    result = import_timetable_snapshot(
        conn,
        file_info["content"],
        effective_start_date=start_date,
    )

    skipped_errors = result["skipped_errors"]
    error_items = "".join(f"<li>{html.escape(error)}</li>" for error in skipped_errors)
    error_summary = ""
    if skipped_errors:
        error_summary = (
            f'<div class="msg error">{len(skipped_errors)}件はエラーのためスキップしました。</div>'
            f'<ul class="error-list">{error_items}</ul>'
        )
    message = (
        '<div class="msg success">取り込みが完了しました '
        f'（生徒の新規登録: {result["new_students"]}名、'
        f'通常授業の登録: {result["new_enrollments"]}件、'
        f'登録済みのためスキップ: {result["skipped_enrollments"]}件）</div>'
        f'{error_summary}'
    )
    return message, qs
