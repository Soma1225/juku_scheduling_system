"""スキャンページの傾き補正と、後続認識へ進めるための品質判定。"""

import json
import math
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path


# 実物スキャンの蓄積後に調整する暫定閾値。判定ロジックから分離しておく。
MAX_AUTO_DESKEW_DEGREES = 10.0
MIN_STRUCTURE_LINES = 8
MIN_BLUR_SCORE = 20.0
WARN_BLUR_SCORE = 50.0
MIN_MEAN_BRIGHTNESS = 80.0
MAX_MEAN_BRIGHTNESS = 252.0
WARN_DARK_RATIO = 0.30


@dataclass(frozen=True)
class PreprocessResult:
    original_path: str
    corrected_path: str
    deskew_angle_degrees: float | None
    detected_line_count: int
    blur_score: float | None
    mean_brightness: float | None
    dark_pixel_ratio: float | None
    layout_quality: str
    engine: str
    warning: str | None = None


def _nearest_axis_angle(angle_degrees: float) -> float:
    """水平・垂直罫線の角度を、水平基準の傾きへ正規化する。"""
    while angle_degrees <= -45:
        angle_degrees += 90
    while angle_degrees > 45:
        angle_degrees -= 90
    return angle_degrees


def _weighted_median(values: list[tuple[float, float]]) -> float:
    values = sorted(values, key=lambda item: item[0])
    midpoint = sum(weight for _, weight in values) / 2
    cumulative = 0.0
    for value, weight in values:
        cumulative += weight
        if cumulative >= midpoint:
            return value
    return values[-1][0]


def estimate_skew(gray) -> tuple[float | None, int]:
    """帳票の罫線をHough変換で検出し、傾き角と採用線数を返す。"""
    import cv2

    height, width = gray.shape[:2]
    scale = min(1.0, 2000.0 / max(height, width))
    work = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    binary = cv2.threshold(work, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    lines = cv2.HoughLinesP(
        binary,
        1,
        math.pi / 1800,
        threshold=max(80, int(min(work.shape) * 0.08)),
        minLineLength=max(80, int(min(work.shape) * 0.12)),
        maxLineGap=max(10, int(min(work.shape) * 0.01)),
    )
    if lines is None:
        return None, 0

    candidates = []
    for x1, y1, x2, y2 in lines[:, 0]:
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        angle = _nearest_axis_angle(math.degrees(math.atan2(dy, dx)))
        if abs(angle) <= 15:
            candidates.append((angle, length))
    if not candidates:
        return None, 0
    return _weighted_median(candidates), len(candidates)


def classify_quality(
    *,
    angle: float | None,
    line_count: int,
    blur_score: float,
    mean_brightness: float,
    dark_ratio: float,
) -> str:
    """測定値をDBのlayout_qualityへ変換する。"""
    if (
        blur_score < MIN_BLUR_SCORE
        or mean_brightness < MIN_MEAN_BRIGHTNESS
        or mean_brightness > MAX_MEAN_BRIGHTNESS
    ):
        return "ILLEGIBLE"
    if angle is None or line_count < MIN_STRUCTURE_LINES or abs(angle) > MAX_AUTO_DESKEW_DEGREES:
        return "STRUCTURE_FAILED"
    if blur_score < WARN_BLUR_SCORE or dark_ratio > WARN_DARK_RATIO:
        return "PARTIAL_UNREADABLE"
    return "OK"


def preprocess_page(original_path: Path, corrected_path: Path) -> PreprocessResult:
    """原画像を保持したまま、補正画像と品質測定値を作成する。"""
    original_path = Path(original_path).resolve()
    corrected_path = Path(corrected_path).resolve()
    try:
        import cv2
    except ImportError:
        shutil.copy2(original_path, corrected_path)
        return PreprocessResult(
            str(original_path), str(corrected_path), None, 0, None, None, None,
            "STRUCTURE_FAILED", "none", "OpenCVがないため補正を省略しました",
        )

    gray = cv2.imread(str(original_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"ページ画像を読み込めません: {original_path}")
    angle, line_count = estimate_skew(gray)
    corrected = gray
    if angle is not None and abs(angle) <= MAX_AUTO_DESKEW_DEGREES and abs(angle) >= 0.05:
        height, width = gray.shape
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
        corrected = cv2.warpAffine(
            gray, matrix, (width, height), flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT, borderValue=255,
        )

    blur_score = float(cv2.Laplacian(corrected, cv2.CV_64F).var())
    mean_brightness = float(corrected.mean())
    dark_ratio = float((corrected < 80).mean())
    quality = classify_quality(
        angle=angle,
        line_count=line_count,
        blur_score=blur_score,
        mean_brightness=mean_brightness,
        dark_ratio=dark_ratio,
    )
    if not cv2.imwrite(str(corrected_path), corrected, [cv2.IMWRITE_PNG_COMPRESSION, 6]):
        raise OSError(f"補正画像を保存できません: {corrected_path}")
    return PreprocessResult(
        str(original_path), str(corrected_path), angle, line_count, blur_score,
        mean_brightness, dark_ratio, quality, f"opencv-{cv2.__version__}",
    )


def write_preprocess_metadata(result: PreprocessResult, metadata_path: Path) -> None:
    metadata_path.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
