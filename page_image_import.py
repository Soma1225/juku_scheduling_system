"""記入済み講習会用紙PDFの取り込み画面。"""

import html

from db import get_conn, get_current_academic_fiscal_year
from image_import_service import create_import_batch
from image_import_commit import DEFAULT_BACKUP_DIR
from image_import_db import find_camp_enrollment_duplicates, get_matching_camp_enrollments
from image_import_config import (
    choose_backup_directory,
    load_backup_directory,
    save_backup_directory,
)


def render(qs: dict, message_html: str = "") -> str:
    backup_directory = load_backup_directory(DEFAULT_BACKUP_DIR)
    conn = get_conn()
    enrollment_count = conn.execute(
        "SELECT COUNT(*) FROM CAMP_COURSE_ENROLLMENTS"
    ).fetchone()[0]
    camps = conn.execute(
        "SELECT camp_id,camp_name,planned_start_date,planned_end_date "
        "FROM CAMPS ORDER BY planned_start_date DESC"
    ).fetchall()
    batches = conn.execute(
        """
        SELECT b.batch_id,c.camp_name,b.paper_fiscal_year,b.paper_type,
               b.page_count,b.status,b.created_at,
               SUM(CASE WHEN p.layout_quality <> 'OK' THEN 1 ELSE 0 END)
        FROM IMAGE_IMPORT_BATCHES b JOIN CAMPS c ON c.camp_id=b.camp_id
        JOIN IMAGE_IMPORT_PAGES p ON p.batch_id=b.batch_id AND p.is_deleted=0
        WHERE b.is_deleted=0
        GROUP BY b.batch_id
        ORDER BY b.batch_id DESC LIMIT 20
        """
    ).fetchall()
    conn.close()

    camp_options = '<option value="">選択してください</option>' + "".join(
        f'<option value="{row[0]}">{html.escape(row[1])}（{row[2]}〜{row[3]}）</option>'
        for row in camps
    )
    current_fy = get_current_academic_fiscal_year()
    year_options = "".join(
        f'<option value="{year}"{" selected" if year == current_fy else ""}>{year}年度</option>'
        for year in range(current_fy - 2, current_fy + 3)
    )
    batch_rows = "".join(
        f'<tr><td><a href="/image-import-review?batch_id={row[0]}">#{row[0]}</a></td>'
        f"<td>{html.escape(row[1])}</td><td>{row[2]}年度 {html.escape(row[3])}</td>"
        f"<td>{row[4]}ページ</td><td>{row[5]}{'（要確認' + str(row[7]) + 'ページ）' if row[7] else ''}</td><td>{row[6]}</td></tr>"
        for row in batches
    ) or '<tr><td colspan="6">取り込み履歴はありません</td></tr>'

    return f"""
    <h1>記入用紙 PDF取り込み</h1>
    <div class="hint">複合機で一括スキャンしたPDFを、講習会単位で登録します。この段階では本登録データは変更しません。</div>
    {message_html}
    <div style="border:1px solid #e5e5e5;border-radius:8px;padding:12px 14px;margin:16px 0;">
      <div style="font-size:13px;font-weight:bold;">本登録前バックアップの保存先</div>
      <div class="hint" style="margin:5px 0 8px;word-break:break-all;">{html.escape(str(backup_directory))}</div>
      <form method="POST" action="/image-import">
        <input type="hidden" name="action" value="choose_backup_folder">
        <button type="submit" style="margin:0;width:auto;padding:8px 14px;">エクスプローラーで保存先を選択</button>
      </form>
    </div>
    <div style="border:1px solid #e5e5e5;border-radius:8px;padding:12px 14px;margin:16px 0;">
      <div style="font-size:13px;font-weight:bold;">本番DBの重複安全確認</div>
      <div class="hint" style="margin:5px 0 8px;">
        現在の講習会受講契約は{enrollment_count}件です。同一の「講習会・生徒・科目」が
        複数行ないかを、DBを書き換えずに確認します。
      </div>
      <form method="POST" action="/image-import">
        <input type="hidden" name="action" value="check_enrollment_duplicates">
        <button type="submit" style="margin:0;width:auto;padding:8px 14px;">重複を確認</button>
      </form>
    </div>
    <form method="POST" action="/image-import" enctype="multipart/form-data">
      <input type="hidden" name="action" value="upload">
      <label>対象講習会 <span class="req">*</span></label>
      <select name="camp_id" required>{camp_options}</select>
      <label>用紙年度 <span class="req">*</span></label>
      <select name="paper_fiscal_year" required>{year_options}</select>
      <label>講習会種別 <span class="req">*</span></label>
      <select name="paper_type" required>
        <option value="夏期">夏期</option><option value="冬期">冬期</option><option value="春期">春期</option>
      </select>
      <label>スキャンPDF <span class="req">*</span></label>
      <input type="file" name="scan_pdf" accept="application/pdf,.pdf" required>
      <button type="submit">PDFを取り込んでページ画像を作成</button>
    </form>
    <h1 style="font-size:15px;color:#534AB7;margin-top:32px;">最近の取り込み</h1>
    <table><tr><th>ID</th><th>講習会</th><th>用紙</th><th>ページ数</th><th>状態</th><th>登録日時</th></tr>{batch_rows}</table>
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    action = get("action")
    if action == "check_enrollment_duplicates":
        duplicates = find_camp_enrollment_duplicates(conn)
        total = conn.execute("SELECT COUNT(*) FROM CAMP_COURSE_ENROLLMENTS").fetchone()[0]
        if not duplicates:
            return (
                '<div class="msg success">重複はありませんでした。'
                f'確認対象: {total}件。DBへの変更は行っていません。</div>',
                {},
            )
        details = []
        for duplicate in duplicates:
            rows = get_matching_camp_enrollments(
                conn, duplicate.camp_id, duplicate.student_id, duplicate.subject_id,
            )
            row_ids = ", ".join(str(row[0]) for row in rows)
            details.append(
                f"講習会ID={duplicate.camp_id}、生徒ID={duplicate.student_id}、"
                f"科目ID={duplicate.subject_id}（{duplicate.row_count}件、登録ID: {row_ids}）"
            )
        detail_html = "<br>".join(html.escape(detail) for detail in details)
        return (
            '<div class="msg error">重複が見つかりました。自動で削除・統合はしません。<br>'
            f'{detail_html}<br>確認対象: {total}件。</div>',
            {},
        )
    if action == "choose_backup_folder":
        current = load_backup_directory(DEFAULT_BACKUP_DIR)
        selected = choose_backup_directory(current)
        if selected is None:
            return '<div class="msg error">保存先の変更をキャンセルしました</div>', {}
        saved = save_backup_directory(selected)
        return f'<div class="msg success">バックアップ保存先を変更しました: {html.escape(str(saved))}</div>', {}
    if action != "upload":
        raise ValueError("不明な操作です")
    files = fields.get("_files", {})
    file_info = files.get("scan_pdf")
    if not file_info:
        raise ValueError("PDFファイルを選択してください")
    if not file_info["filename"].lower().endswith(".pdf"):
        raise ValueError("拡張子が.pdfのファイルを選択してください")
    try:
        camp_id = int(get("camp_id"))
        fiscal_year = int(get("paper_fiscal_year"))
    except (TypeError, ValueError):
        raise ValueError("講習会と用紙年度を選択してください")
    paper_type = get("paper_type")
    if paper_type not in ("夏期", "冬期", "春期"):
        raise ValueError("講習会種別を選択してください")

    batch_id, page_count, duplicate_ids = create_import_batch(
        conn,
        camp_id=camp_id,
        paper_fiscal_year=fiscal_year,
        paper_type=paper_type,
        pdf_content=file_info["content"],
    )
    duplicate_note = (
        f" 同じPDFの既存バッチがあります: {', '.join('#' + str(i) for i in duplicate_ids)}"
        if duplicate_ids else ""
    )
    return (
        f'<div class="msg success">バッチ#{batch_id}として{page_count}ページを登録しました。'
        f'本登録データはまだ変更していません。{html.escape(duplicate_note)}</div>',
        {},
    )
