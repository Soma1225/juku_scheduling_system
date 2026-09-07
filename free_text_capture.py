"""自由記述欄の切り抜き・筆跡検出・添付保存。"""

import datetime
import hashlib
from pathlib import Path

from image_layouts import (
    CURRICULUM_REGIONS,
    DETAIL_REGIONS,
    OTHER_SUBJECT_REGION,
    REMARKS_REGION,
    crop_normalized_region,
)


MIN_MEANINGFUL_INK_PIXELS = 80


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def meaningful_ink_pixels(image_path: Path) -> int:
    """微小な汚れを除外した黒画素数を返す。"""
    import cv2
    import numpy as np

    gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"自由記述画像を読み込めません: {image_path}")
    mask = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    cleaned = np.zeros_like(mask)
    height, width = mask.shape
    for index in range(1, count):
        component_width = int(stats[index, cv2.CC_STAT_WIDTH])
        component_height = int(stats[index, cv2.CC_STAT_HEIGHT])
        is_horizontal_rule = component_width > width * 0.70 and component_height < height * 0.15
        is_vertical_rule = component_height > height * 0.70 and component_width < width * 0.04
        if (
            int(stats[index, cv2.CC_STAT_AREA]) >= 10
            and not is_horizontal_rule
            and not is_vertical_rule
        ):
            cleaned[labels == index] = 255
    return int(cv2.countNonZero(cleaned))


def _insert_attachment(conn, page_id: int, attachment_type: str, path: Path, created_at: str):
    conn.execute(
        """
        INSERT INTO IMAGE_IMPORT_ATTACHMENTS
            (page_id,attachment_type,crop_image_path,crop_image_hash,created_at)
        VALUES (?,?,?,?,?)
        """,
        (page_id, attachment_type, str(path), _hash_file(path), created_at),
    )


def capture_free_text(
    conn,
    *,
    page_id: int,
    page_image_path: Path,
    output_dir: Path,
) -> int:
    """自由記述欄を保存し、作成した添付件数を返す。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    attachment_count = 0

    for index, (subject_label, region) in enumerate(CURRICULUM_REGIONS.items(), start=1):
        path = crop_normalized_region(
            page_image_path, output_dir / f"curriculum_{index:02d}.png", region
        )
        if meaningful_ink_pixels(path) >= MIN_MEANINGFUL_INK_PIXELS:
            _insert_attachment(conn, page_id, "CURRICULUM_REQUEST", path, created_at)
            attachment_count += 1
        else:
            path.unlink(missing_ok=True)

    other_path = crop_normalized_region(
        page_image_path, output_dir / "other_subject.png", OTHER_SUBJECT_REGION
    )
    if meaningful_ink_pixels(other_path) >= MIN_MEANINGFUL_INK_PIXELS:
        _insert_attachment(conn, page_id, "OTHER_FREE_TEXT", other_path, created_at)
        attachment_count += 1
    else:
        other_path.unlink(missing_ok=True)

    remarks_path = crop_normalized_region(
        page_image_path, output_dir / "remarks.png", REMARKS_REGION
    )
    if meaningful_ink_pixels(remarks_path) >= MIN_MEANINGFUL_INK_PIXELS:
        _insert_attachment(conn, page_id, "REMARKS", remarks_path, created_at)
        attachment_count += 1
    else:
        remarks_path.unlink(missing_ok=True)

    # 印刷済みの括弧と手書き文字の分離には白紙テンプレートが必要なため、
    # 理科・社会の括弧内は保守的に常時保存する。
    for index, (subject_label, region) in enumerate(DETAIL_REGIONS.items(), start=1):
        path = crop_normalized_region(
            page_image_path, output_dir / f"detail_{index:02d}.png", region
        )
        conn.execute(
            """
            UPDATE IMAGE_IMPORT_SUBJECT_ENROLLMENTS
            SET detail_crop_image_path=?
            WHERE page_id=? AND subject_row_label=? AND is_deleted=0
            """,
            (str(path), page_id, subject_label),
        )
        _insert_attachment(conn, page_id, "OTHER_FREE_TEXT", path, created_at)
        attachment_count += 1
    return attachment_count
