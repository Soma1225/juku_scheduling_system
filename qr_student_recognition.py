"""用紙のQRコードからstudent_idだけを読み取り、候補テーブルへ保存する。"""

from __future__ import annotations

import datetime
import hashlib
from pathlib import Path


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def decode_qr_values(image_path: Path) -> list[str]:
    """OpenCVで単一・複数QRを検出し、空でない値を重複なしで返す。"""
    import cv2

    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"ページ画像を読み込めません: {image_path}")
    detector = cv2.QRCodeDetector()
    values: list[str] = []
    try:
        detected, decoded, _, _ = detector.detectAndDecodeMulti(image)
        if detected:
            values.extend(value.strip() for value in decoded if value and value.strip())
    except (AttributeError, cv2.error, ValueError):
        pass
    if not values:
        value, _, _ = detector.detectAndDecode(image)
        if value and value.strip():
            values.append(value.strip())
    return list(dict.fromkeys(values))


def _attach_review_crop(conn, *, page_id: int, crop_path: Path, created_at: str) -> None:
    content_hash = hashlib.sha256(Path(crop_path).read_bytes()).hexdigest()
    conn.execute(
        """INSERT INTO IMAGE_IMPORT_ATTACHMENTS
           (page_id,attachment_type,crop_image_path,crop_image_hash,created_at)
           VALUES (?,'OTHER_FREE_TEXT',?,?,?)""",
        (page_id, str(crop_path), content_hash, created_at),
    )


def recognize_qr_student_candidates(
    conn,
    *,
    page_id: int,
    page_image_path: Path,
    qr_crop_path: Path,
) -> tuple[int | None, list[int]]:
    """QR値を厳密な整数student_idとして照合し、曖昧時は推測しない。"""
    values = decode_qr_values(page_image_path)
    parsed_ids: list[int] = []
    invalid_values: list[str] = []
    for value in values:
        if value.isascii() and value.isdecimal():
            parsed_ids.append(int(value))
        else:
            invalid_values.append(value)
    parsed_ids = list(dict.fromkeys(parsed_ids))
    existing = {
        row[0]
        for row in conn.execute(
            f"SELECT student_id FROM STUDENTS WHERE student_id IN ({','.join('?' for _ in parsed_ids)})",
            parsed_ids,
        ).fetchall()
    } if parsed_ids else set()
    missing_ids = [student_id for student_id in parsed_ids if student_id not in existing]
    valid_ids = [student_id for student_id in parsed_ids if student_id in existing]
    created_at = _now_iso()
    exactly_one = len(values) == 1 and len(valid_ids) == 1 and not invalid_values and not missing_ids

    candidate_row_ids: list[int] = []
    for rank, student_id in enumerate(valid_ids, start=1):
        cursor = conn.execute(
            """INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
               (page_id,candidate_student_id,candidate_rank,match_confidence,
                match_status,is_selected,created_at)
               VALUES (?,?,?,1.0,?,?,?)""",
            (page_id, student_id, rank, "AUTO_MATCHED" if exactly_one else "AMBIGUOUS",
             1 if exactly_one else 0, created_at),
        )
        candidate_row_ids.append(cursor.lastrowid)

    if exactly_one:
        return valid_ids[0], valid_ids

    if candidate_row_ids:
        related_id = candidate_row_ids[0]
    else:
        cursor = conn.execute(
            """INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
               (page_id,match_status,is_selected,created_at)
               VALUES (?,'NOT_FOUND',0,?)""",
            (page_id, created_at),
        )
        related_id = cursor.lastrowid

    if not values:
        reason = "QRコードを検出できませんでした"
    elif len(values) > 1:
        reason = f"QRコードを複数検出しました（{', '.join(values)}）"
    else:
        reason = f"QR値『{values[0]}』に対応する生徒が見つかりません"
    conn.execute(
        """INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
           (page_id,item_type,related_page_student_id,crop_image_path,
            candidate_value_text,created_at)
           VALUES (?,'STUDENT_MATCH',?,?,?,?)""",
        (page_id, related_id, str(qr_crop_path), reason, created_at),
    )
    _attach_review_crop(conn, page_id=page_id, crop_path=qr_crop_path, created_at=created_at)
    return None, valid_ids
