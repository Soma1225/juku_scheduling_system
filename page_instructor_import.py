"""講師情報スナップショットから講師マスタを取り込む画面。"""

from instructor_snapshot_import import import_instructor_snapshot


def render(qs: dict, message_html: str = "") -> str:
    return f"""
    <h1>講師マスタ Excel取り込み</h1>
    <div class="hint">
      現行システムからコピーしたinfo.xlsxの「講師情報」シートを読み込みます。
      元ファイルへの書き込みは行いません。
    </div>
    {message_html}
    <form method="POST" action="/instructor-import" enctype="multipart/form-data">
      <input type="hidden" name="action" value="import">
      <label>Excelスナップショット <span class="req">*</span></label>
      <input type="file" name="excel_file" accept=".xlsx" required>
      <button type="submit">講師マスタを取り込む</button>
    </form>
    <div class="hint" style="margin-top:18px;">
      「退職」の行は除外します。パスワード、登録日、登録者、管理者登録フラグ、備考は読み込み・保存しません。
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

    result = import_instructor_snapshot(conn, file_info["content"])
    message = (
        '<div class="msg success">取り込みが完了しました '
        f'（新規: {result["created_count"]}名、更新: {result["updated_count"]}名、'
        f'退職のため除外: {result["retired_skipped_count"]}名）</div>'
    )
    return message, {}
