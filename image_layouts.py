"""本部指定帳票の、ページ寸法に依存しない認識領域定義。"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NormalizedRegion:
    left: float
    top: float
    right: float
    bottom: float


# 現行本部帳票。下部の月数が変わっても、ヘッダー領域は共通。
HQ_STANDARD_REGIONS = {
    # マークシート版では右上隅にstudent_idのQRコードを印刷する。
    "student_qr": NormalizedRegion(0.820, 0.025, 0.975, 0.180),
    "grade": NormalizedRegion(0.108, 0.158, 0.218, 0.197),
    "student_name": NormalizedRegion(0.335, 0.158, 0.590, 0.197),
}

# マークシート版（layout_key=mark-sheet-v1）の初期座標。実スキャンで
# キャリブレーションしやすいよう、すべてページ比率で一か所に集約する。
SUBJECT_SELECTION_REGIONS = {
    label: NormalizedRegion(0.445, top, 0.475, bottom)
    for label, (top, bottom) in {
        "英語": (0.297, 0.311), "数学・算数": (0.317, 0.331),
        "国語": (0.337, 0.350), "理科": (0.356, 0.369),
        "社会": (0.376, 0.389), "その他": (0.396, 0.409),
    }.items()
}


def _bubble_row(top: float, bottom: float, left: float, right: float) -> tuple[NormalizedRegion, ...]:
    width = (right - left) / 10
    return tuple(
        NormalizedRegion(left + digit * width, top, left + (digit + 1) * width, bottom)
        for digit in range(10)
    )


SUBJECT_DIGIT_REGIONS = {
    label: {
        "tens": _bubble_row(region.top, region.bottom, 0.500, 0.700),
        "ones": _bubble_row(region.top, region.bottom, 0.725, 0.925),
    }
    for label, region in SUBJECT_SELECTION_REGIONS.items()
}

# 通常授業用紙の曜日×5限グリッド。列は月〜土、行は1〜5限。
WEEKLY_AVAILABILITY_GRID = NormalizedRegion(0.080, 0.500, 0.920, 0.750)

SUBJECT_COUNT_REGIONS = {
    "英語": NormalizedRegion(0.486, 0.297, 0.558, 0.311),
    "数学・算数": NormalizedRegion(0.486, 0.317, 0.558, 0.331),
    "国語": NormalizedRegion(0.486, 0.337, 0.558, 0.350),
    "理科": NormalizedRegion(0.486, 0.356, 0.558, 0.369),
    "社会": NormalizedRegion(0.486, 0.376, 0.558, 0.389),
    "その他": NormalizedRegion(0.486, 0.396, 0.558, 0.409),
}

CURRICULUM_REGIONS = {
    "英語": NormalizedRegion(0.630, 0.297, 0.955, 0.311),
    "数学・算数": NormalizedRegion(0.630, 0.317, 0.955, 0.331),
    "国語": NormalizedRegion(0.630, 0.337, 0.955, 0.350),
    "理科": NormalizedRegion(0.630, 0.356, 0.955, 0.369),
    "社会": NormalizedRegion(0.630, 0.376, 0.955, 0.389),
    "その他": NormalizedRegion(0.630, 0.396, 0.955, 0.409),
}

DETAIL_REGIONS = {
    "理科": NormalizedRegion(0.085, 0.356, 0.205, 0.369),
    "社会": NormalizedRegion(0.085, 0.376, 0.205, 0.389),
}

OTHER_SUBJECT_REGION = NormalizedRegion(0.100, 0.396, 0.360, 0.409)
REMARKS_REGION = NormalizedRegion(0.040, 0.815, 0.820, 0.885)


def crop_normalized_region(image_path: Path, output_path: Path, region: NormalizedRegion) -> Path:
    import cv2

    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"画像を読み込めません: {image_path}")
    height, width = image.shape
    x1, x2 = round(region.left * width), round(region.right * width)
    y1, y2 = round(region.top * height), round(region.bottom * height)
    if x1 >= x2 or y1 >= y2:
        raise ValueError("認識領域の座標が不正です")
    crop = image[y1:y2, x1:x2]
    output_path = Path(output_path)
    if not cv2.imwrite(str(output_path), crop, [cv2.IMWRITE_PNG_COMPRESSION, 6]):
        raise OSError(f"切り抜き画像を保存できません: {output_path}")
    return output_path


def extract_identity_regions(page_image_path: Path, output_dir: Path) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        name: crop_normalized_region(
            page_image_path,
            output_dir / f"{name}.png",
            region,
        )
        for name, region in HQ_STANDARD_REGIONS.items()
    }


def extract_qr_region(page_image_path: Path, output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return crop_normalized_region(
        page_image_path, output_dir / "student_qr.png", HQ_STANDARD_REGIONS["student_qr"]
    )


def extract_subject_count_regions(page_image_path: Path, output_dir: Path) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        label: crop_normalized_region(
            page_image_path,
            output_dir / f"count_{index:02d}.png",
            region,
        )
        for index, (label, region) in enumerate(SUBJECT_COUNT_REGIONS.items(), start=1)
    }
