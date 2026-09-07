"""印刷済み文字画像を、既知のMS Pゴシック候補と照合する。"""

from dataclasses import dataclass
from pathlib import Path


DEFAULT_FONT_PATHS = (
    Path(r"C:\Windows\Fonts\msgothic.ttc"),
    Path(r"C:\Windows\Fonts\YuGothM.ttc"),
)


@dataclass(frozen=True)
class VisualMatch:
    candidate_id: object
    label: str
    score: float


def find_japanese_font() -> Path:
    for path in DEFAULT_FONT_PATHS:
        if path.is_file():
            return path
    raise RuntimeError("MS Pゴシック等の日本語フォントが見つかりません")


def _remove_rule_components(mask):
    import cv2
    import numpy as np

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    cleaned = np.zeros_like(mask)
    height, width = mask.shape
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        is_horizontal_rule = w > width * 0.75 and h < max(8, height * 0.12)
        is_vertical_rule = h > height * 0.75 and w < max(8, width * 0.04)
        if area >= 4 and not is_horizontal_rule and not is_vertical_rule:
            cleaned[labels == index] = 255
    return cleaned


def normalize_observed_text(image_path: Path):
    import cv2

    gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"文字画像を読み込めません: {image_path}")
    # 帳票の太いセル枠を比較対象から確実に外す。
    margin_y = max(1, round(gray.shape[0] * 0.16))
    margin_x = max(1, round(gray.shape[1] * 0.045))
    inner = gray[margin_y:-margin_y, margin_x:-margin_x]
    mask = cv2.threshold(inner, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    return _remove_rule_components(mask)


def render_candidate_mask(text: str, font_path: Path | None = None):
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    font_path = font_path or find_japanese_font()
    # msgothic.ttc: 0=MS Gothic, 1=MS UI Gothic, 2=MS P Gothic。
    # 本部Excelが使用しているMS P Gothicを明示的に選ぶ。
    font_index = 2 if font_path.name.lower() == "msgothic.ttc" else 0
    font = ImageFont.truetype(str(font_path), 96, index=font_index)
    canvas = Image.new("L", (1400, 180), 255)
    draw = ImageDraw.Draw(canvas)
    draw.text((20, 20), text, font=font, fill=0)
    array = np.asarray(canvas)
    return cv2.threshold(array, 200, 255, cv2.THRESH_BINARY_INV)[1]


def _fit_to_canvas(mask, canvas_width=640, canvas_height=96):
    import cv2
    import numpy as np

    points = cv2.findNonZero(mask)
    canvas = np.zeros((canvas_height, canvas_width), dtype=np.uint8)
    if points is None:
        return canvas
    x, y, width, height = cv2.boundingRect(points)
    glyphs = mask[y:y + height, x:x + width]
    scale = min((canvas_width - 12) / width, (canvas_height - 12) / height)
    resized = cv2.resize(
        glyphs,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    offset_x = (canvas_width - resized.shape[1]) // 2
    offset_y = (canvas_height - resized.shape[0]) // 2
    canvas[offset_y:offset_y + resized.shape[0], offset_x:offset_x + resized.shape[1]] = resized
    return cv2.threshold(canvas, 80, 255, cv2.THRESH_BINARY)[1]


def compare_masks(observed_mask, candidate_mask) -> float:
    """線幅・スキャンのにじみを許容した双方向包含率を返す。"""
    import cv2

    observed = _fit_to_canvas(observed_mask)
    candidate = _fit_to_canvas(candidate_mask)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    observed_dilated = cv2.dilate(observed, kernel)
    candidate_dilated = cv2.dilate(candidate, kernel)
    observed_pixels = max(1, cv2.countNonZero(observed))
    candidate_pixels = max(1, cv2.countNonZero(candidate))
    observed_covered = cv2.countNonZero(cv2.bitwise_and(observed, candidate_dilated)) / observed_pixels
    candidate_covered = cv2.countNonZero(cv2.bitwise_and(candidate, observed_dilated)) / candidate_pixels
    return float((observed_covered + candidate_covered) / 2)


def rank_visual_candidates(image_path: Path, candidates, font_path: Path | None = None) -> list[VisualMatch]:
    observed = normalize_observed_text(image_path)
    matches = []
    for candidate_id, label in candidates:
        variants = {label, label.replace(" ", ""), label.replace("　", "")}
        score = max(compare_masks(observed, render_candidate_mask(v, font_path)) for v in variants if v)
        matches.append(VisualMatch(candidate_id, label, score))
    return sorted(matches, key=lambda item: item.score, reverse=True)
