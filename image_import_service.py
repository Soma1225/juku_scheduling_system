"""スキャンPDFの保存・ページ画像化・候補バッチ登録。"""

import datetime
import hashlib
import shutil
import uuid
from pathlib import Path

from image_preprocessing import preprocess_page, write_preprocess_metadata
from image_layouts import extract_identity_regions, extract_subject_count_regions
from handwritten_counts import store_subject_count_candidates
from availability_recognition import store_availability_candidates
from free_text_capture import capture_free_text
from student_image_matching import recognize_student_candidates
from subject_resolution import resolve_page_subjects
from camp_form_tracking import record_scanned_return


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
        raise RuntimeError(
            "PDF画像化に必要なpypdfium2がありません。pip install pypdfium2 を実行してください"
        ) from exc

    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page_count = len(document)
        if page_count == 0:
            raise ValueError("PDFにページがありません")
        if page_count > MAX_PAGES:
            raise ValueError(f"PDFのページ数が上限({MAX_PAGES}ページ)を超えています")
        output_paths = []
        for page_index in range(page_count):
            page = document[page_index]
            try:
                bitmap = page.render(scale=dpi / 72)
                image = bitmap.to_pil()
                output_path = output_dir / f"page_{page_index + 1:04d}_original.png"
                image.save(output_path, format="PNG", optimize=True)
                output_paths.append(output_path)
            finally:
                page.close()
        return output_paths
    finally:
        document.close()


def create_import_batch(
    conn,
    *,
    camp_id: int,
    paper_fiscal_year: int,
    paper_type: str,
    pdf_content: bytes,
    storage_root: Path = DEFAULT_STORAGE_ROOT,
    layout_key: str = "hq-standard",
    layout_version: int = 1,
) -> tuple[int, int, list[int]]:
    """PDFを保存・画像化して候補バッチを登録する。

    戻り値は(batch_id, page_count, 同一PDFハッシュの既存batch_id一覧)。
    同一PDFは警告対象だが、再処理を妨げないため登録自体は許可する。
    """
    if not pdf_content:
        raise ValueError("PDFファイルが空です")
    if len(pdf_content) > MAX_PDF_BYTES:
        raise ValueError("PDFファイルは100MB以下にしてください")
    if not pdf_content.lstrip().startswith(b"%PDF-"):
        raise ValueError("選択されたファイルはPDFとして認識できません")
    if not conn.execute("SELECT 1 FROM CAMPS WHERE camp_id=?", (camp_id,)).fetchone():
        raise ValueError("選択された講習会が見つかりません")

    pdf_hash = _sha256_bytes(pdf_content)
    duplicate_ids = [
        row[0]
        for row in conn.execute(
            """
            SELECT batch_id FROM IMAGE_IMPORT_BATCHES
            WHERE source_pdf_hash=? AND is_deleted=0 ORDER BY batch_id
            """,
            (pdf_hash,),
        ).fetchall()
    ]

    storage_root = Path(storage_root).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    work_dir = storage_root / f".staging_{uuid.uuid4().hex}"
    final_dir = storage_root / f"batch_{uuid.uuid4().hex}"
    work_dir.mkdir()
    try:
        source_path = work_dir / "source.pdf"
        source_path.write_bytes(pdf_content)
        original_page_paths = render_pdf_pages(source_path, work_dir)
        processed_pages = []
        for page_number, original_page_path in enumerate(original_page_paths, start=1):
            corrected_path = work_dir / f"page_{page_number:04d}.png"
            result = preprocess_page(original_page_path, corrected_path)
            write_preprocess_metadata(result, work_dir / f"page_{page_number:04d}_preprocess.json")
            regions = extract_identity_regions(
                corrected_path,
                work_dir / f"page_{page_number:04d}_regions",
            )
            count_regions = extract_subject_count_regions(
                corrected_path,
                work_dir / f"page_{page_number:04d}_regions",
            )
            processed_pages.append((corrected_path, result, regions, count_regions))
        work_dir.replace(final_dir)
        final_source_path = final_dir / "source.pdf"
        final_pages = []
        for path, result, regions, count_regions in processed_pages:
            final_regions = {
                name: final_dir / region_path.relative_to(work_dir)
                for name, region_path in regions.items()
            }
            final_count_regions = {
                label: final_dir / region_path.relative_to(work_dir)
                for label, region_path in count_regions.items()
            }
            final_pages.append((final_dir / path.name, result, final_regions, final_count_regions))

        created_at = _now_iso()
        batch_status = (
            "REVIEW_PENDING"
            if any(result.layout_quality != "OK" for _, result, _, _ in final_pages)
            else "PROCESSING"
        )
        try:
            cursor = conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_BATCHES
                    (camp_id,paper_fiscal_year,paper_type,layout_key,layout_version,
                     source_pdf_path,source_pdf_hash,page_count,status,created_at)
                VALUES (?,?,?,?,?,?,?,?, ?, ?)
                """,
                (camp_id, paper_fiscal_year, paper_type, layout_key, layout_version,
                 str(final_source_path), pdf_hash, len(final_pages), batch_status, created_at),
            )
            batch_id = cursor.lastrowid
            recognition_requires_review = False
            for page_number, (page_path, preprocess_result, regions, count_regions) in enumerate(final_pages, start=1):
                page_cursor = conn.execute(
                    """
                    INSERT INTO IMAGE_IMPORT_PAGES
                        (batch_id,page_number,page_image_path,page_image_hash,layout_quality,created_at)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (batch_id, page_number, str(page_path), _sha256_file(page_path),
                     preprocess_result.layout_quality, created_at),
                )
                selected_student_id, _ = recognize_student_candidates(
                    conn,
                    page_id=page_cursor.lastrowid,
                    name_crop_path=regions["student_name"],
                    grade_crop_path=regions["grade"],
                    paper_fiscal_year=paper_fiscal_year,
                    layout_quality=preprocess_result.layout_quality,
                )
                if selected_student_id is not None:
                    record_scanned_return(
                        conn, page_id=page_cursor.lastrowid, student_id=selected_student_id,
                    )
                recognition_requires_review |= selected_student_id is None
                page_id = page_cursor.lastrowid
                recognition_requires_review |= bool(
                    store_subject_count_candidates(conn, page_id=page_id, count_regions=count_regions)
                )
                subject_resolution = resolve_page_subjects(
                    conn, page_id=page_id, manage_transaction=False,
                )
                recognition_requires_review |= bool(subject_resolution["review"])
                _, availability_review_count = store_availability_candidates(
                    conn,
                    page_id=page_id,
                    page_image_path=page_path,
                    paper_type=paper_type,
                    paper_fiscal_year=paper_fiscal_year,
                    crop_output_dir=page_path.parent / f"{page_path.stem}_availability_review",
                )
                recognition_requires_review |= bool(availability_review_count)
                capture_free_text(
                    conn,
                    page_id=page_id,
                    page_image_path=page_path,
                    output_dir=page_path.parent / f"{page_path.stem}_free_text",
                )
            if recognition_requires_review or batch_status == "REVIEW_PENDING":
                batch_status = "REVIEW_PENDING"
                conn.execute(
                    "UPDATE IMAGE_IMPORT_BATCHES SET status=? WHERE batch_id=?",
                    (batch_status, batch_id),
                )
            conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_AUDIT_LOG
                    (batch_id,action_type,target_table,target_id,source_pdf_path,
                     actor_type,created_at)
                VALUES (?,'UPLOAD','IMAGE_IMPORT_BATCHES',?,?,'SYSTEM',?)
                """,
                (batch_id, batch_id, str(final_source_path), created_at),
            )
            conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_AUDIT_LOG
                    (batch_id,action_type,target_table,target_id,source_pdf_path,
                     actor_type,created_at)
                VALUES (?,'RECOGNIZE','IMAGE_IMPORT_BATCHES',?,?,'SYSTEM',?)
                """,
                (batch_id, batch_id, str(final_source_path), created_at),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            shutil.rmtree(final_dir, ignore_errors=True)
            raise
        return batch_id, len(final_pages), duplicate_ids
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
