"""PDF分割からQR・黒塗り・回数マーク認識までのステージング処理。"""

from __future__ import annotations

import datetime
import hashlib
import shutil
import uuid
from pathlib import Path

from free_text_capture import capture_free_text
from image_layouts import extract_qr_region
from image_preprocessing import preprocess_page, write_preprocess_metadata
from mark_sheet_recognition import store_mark_sheet_availability, store_subject_mark_candidates
from qr_student_recognition import recognize_qr_student_candidates
from subject_resolution import resolve_page_subjects


MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_PAGES = 500
DEFAULT_STORAGE_ROOT = Path(__file__).with_name("image_import_data")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def render_pdf_pages(pdf_path: Path, output_dir: Path, dpi: int = 300) -> list[Path]:
    """pypdfium2でPDF全ページをPNG化する。"""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError("PDF画像化に必要なpypdfium2がありません") from exc
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        if len(document) == 0:
            raise ValueError("PDFにページがありません")
        if len(document) > MAX_PAGES:
            raise ValueError(f"PDFのページ数が上限({MAX_PAGES}ページ)を超えています")
        paths = []
        for index in range(len(document)):
            page = document[index]
            try:
                image = page.render(scale=dpi / 72).to_pil()
                path = output_dir / f"page_{index + 1:04d}_original.png"
                image.save(path, format="PNG", optimize=True)
                paths.append(path)
            finally:
                page.close()
        return paths
    finally:
        document.close()


def _create_page_review(conn, *, page_id: int, crop_path: Path, message: str, created_at: str) -> None:
    """ページ単位の警告を、確定済みDDLのレビュー枠へ保存する。"""
    related = conn.execute(
        """SELECT page_student_id FROM IMAGE_IMPORT_PAGE_STUDENTS
           WHERE page_id=? AND is_deleted=0 ORDER BY candidate_rank,page_student_id LIMIT 1""",
        (page_id,),
    ).fetchone()
    page_student_id = related[0] if related else conn.execute(
        """INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
           (page_id,match_status,is_selected,created_at) VALUES (?,'NOT_FOUND',0,?)""",
        (page_id, created_at),
    ).lastrowid
    conn.execute(
        """INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
           (page_id,item_type,related_page_student_id,crop_image_path,candidate_value_text,created_at)
           VALUES (?,'STUDENT_MATCH',?,?,?,?)""",
        (page_id, page_student_id, str(crop_path), message, created_at),
    )
    conn.execute(
        """INSERT INTO IMAGE_IMPORT_ATTACHMENTS
           (page_id,attachment_type,crop_image_path,crop_image_hash,created_at)
           VALUES (?,'OTHER_FREE_TEXT',?,?,?)""",
        (page_id, str(crop_path), _sha256_file(crop_path), created_at),
    )


def create_import_batch(
    conn,
    *,
    camp_id: int | None,
    paper_fiscal_year: int,
    paper_type: str,
    pdf_content: bytes,
    storage_root: Path = DEFAULT_STORAGE_ROOT,
    layout_key: str = "mark-sheet-v1",
    layout_version: int = 1,
) -> tuple[int, int, list[int]]:
    """全Stageを実行し、結果をIMAGE_IMPORT_*だけへ保存する。"""
    if not pdf_content:
        raise ValueError("PDFファイルが空です")
    if len(pdf_content) > MAX_PDF_BYTES:
        raise ValueError("PDFファイルは100MB以下にしてください")
    if not pdf_content.lstrip().startswith(b"%PDF-"):
        raise ValueError("選択されたファイルはPDFとして認識できません")
    if paper_type == "通常":
        camp_id = None
    elif camp_id is None or not conn.execute(
        "SELECT 1 FROM CAMPS WHERE camp_id=?", (camp_id,)
    ).fetchone():
        raise ValueError("選択された講習会が見つかりません")

    pdf_hash = _sha256_bytes(pdf_content)
    duplicate = conn.execute(
        """SELECT batch_id FROM IMAGE_IMPORT_BATCHES
           WHERE source_pdf_hash=? AND is_deleted=0 ORDER BY batch_id LIMIT 1""",
        (pdf_hash,),
    ).fetchone()
    if duplicate:
        raise ValueError(f"このPDFは既に取り込み済みです（バッチ#{duplicate[0]}）")

    storage_root = Path(storage_root).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    work_dir = storage_root / f".staging_{uuid.uuid4().hex}"
    final_dir = storage_root / f"batch_{uuid.uuid4().hex}"
    work_dir.mkdir()
    try:
        source_path = work_dir / "source.pdf"
        source_path.write_bytes(pdf_content)
        prepared = []
        for page_number, original in enumerate(render_pdf_pages(source_path, work_dir), start=1):
            corrected = work_dir / f"page_{page_number:04d}.png"
            result = preprocess_page(original, corrected)
            write_preprocess_metadata(result, work_dir / f"page_{page_number:04d}_preprocess.json")
            qr_crop = extract_qr_region(corrected, work_dir / f"page_{page_number:04d}_regions")
            prepared.append((corrected, result, qr_crop))
        work_dir.replace(final_dir)
        pages = [
            (final_dir / path.name, result, final_dir / qr.relative_to(work_dir))
            for path, result, qr in prepared
        ]
        created_at = _now_iso()
        status = "REVIEW_PENDING" if any(r.layout_quality != "OK" for _, r, _ in pages) else "PROCESSING"
        try:
            batch_id = conn.execute(
                """INSERT INTO IMAGE_IMPORT_BATCHES
                   (camp_id,paper_fiscal_year,paper_type,layout_key,layout_version,
                    source_pdf_path,source_pdf_hash,page_count,status,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (camp_id, paper_fiscal_year, paper_type, layout_key, layout_version,
                 str(final_dir / "source.pdf"), pdf_hash, len(pages), status, created_at),
            ).lastrowid
            review_needed = status == "REVIEW_PENDING"
            for page_number, (page_path, preprocess_result, qr_crop) in enumerate(pages, start=1):
                page_hash = _sha256_file(page_path)
                prior_page = conn.execute(
                    "SELECT page_id FROM IMAGE_IMPORT_PAGES WHERE page_image_hash=? AND is_deleted=0 LIMIT 1",
                    (page_hash,),
                ).fetchone()
                page_id = conn.execute(
                    """INSERT INTO IMAGE_IMPORT_PAGES
                       (batch_id,page_number,page_image_path,page_image_hash,layout_quality,created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (batch_id, page_number, str(page_path), page_hash,
                     preprocess_result.layout_quality, created_at),
                ).lastrowid

                if preprocess_result.layout_quality == "ILLEGIBLE":
                    _create_page_review(
                        conn, page_id=page_id, crop_path=qr_crop,
                        message="ページが著しく不鮮明です。再スキャンしてください", created_at=created_at,
                    )
                    conn.execute(
                        "UPDATE IMAGE_IMPORT_PAGES SET processing_status='RECOGNIZED' WHERE page_id=?", (page_id,)
                    )
                    review_needed = True
                    continue

                selected, _ = recognize_qr_student_candidates(
                    conn, page_id=page_id, page_image_path=page_path, qr_crop_path=qr_crop,
                )
                review_needed |= selected is None
                if prior_page:
                    _create_page_review(
                        conn, page_id=page_id, crop_path=qr_crop,
                        message=f"同一画像のページが既にあります（ページID={prior_page[0]}）",
                        created_at=created_at,
                    )
                    review_needed = True
                review_needed |= bool(store_subject_mark_candidates(
                    conn, page_id=page_id, page_image_path=page_path,
                    crop_output_dir=page_path.parent / f"{page_path.stem}_subject_review",
                ))
                subject_result = resolve_page_subjects(conn, page_id=page_id, manage_transaction=False)
                review_needed |= bool(subject_result["review"])
                _, availability_reviews = store_mark_sheet_availability(
                    conn, page_id=page_id, page_image_path=page_path,
                    paper_type=paper_type, paper_fiscal_year=paper_fiscal_year,
                    crop_output_dir=page_path.parent / f"{page_path.stem}_availability_review",
                )
                review_needed |= bool(availability_reviews)
                capture_free_text(
                    conn, page_id=page_id, page_image_path=page_path,
                    output_dir=page_path.parent / f"{page_path.stem}_free_text",
                )
                if preprocess_result.layout_quality == "PARTIAL_UNREADABLE":
                    _create_page_review(
                        conn, page_id=page_id, crop_path=qr_crop,
                        message="ページの一部が不鮮明です。認識結果を確認してください",
                        created_at=created_at,
                    )
                    review_needed = True
                conn.execute(
                    "UPDATE IMAGE_IMPORT_PAGES SET processing_status='RECOGNIZED' WHERE page_id=?", (page_id,)
                )

            if review_needed:
                conn.execute("UPDATE IMAGE_IMPORT_BATCHES SET status='REVIEW_PENDING' WHERE batch_id=?", (batch_id,))
            for action in ("UPLOAD", "RECOGNIZE"):
                conn.execute(
                    """INSERT INTO IMAGE_IMPORT_AUDIT_LOG
                       (batch_id,action_type,target_table,target_id,source_pdf_path,actor_type,created_at)
                       VALUES (?,?,?,?,?,'SYSTEM',?)""",
                    (batch_id, action, "IMAGE_IMPORT_BATCHES", batch_id,
                     str(final_dir / "source.pdf"), created_at),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            shutil.rmtree(final_dir, ignore_errors=True)
            raise
        return batch_id, len(pages), []
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
