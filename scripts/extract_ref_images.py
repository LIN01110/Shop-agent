"""
Purpose: 商品主图抽取脚本：从zip压缩包解压商品图片到data/product_images/。
"""

from pathlib import Path, PurePosixPath
from zipfile import ZipFile


ROOT_DIR = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT_DIR / "ecommerce_agent_dataset_ref.zip"
OUTPUT_DIR = ROOT_DIR / "data" / "product_images"


def main() -> None:
    if not ZIP_PATH.exists():
        raise FileNotFoundError(f"Reference dataset zip not found: {ZIP_PATH}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    with ZipFile(ZIP_PATH) as archive:
        for name in archive.namelist():
            normalized = name.replace("\\", "/")
            if "/images/" not in normalized or not normalized.lower().endswith((".jpg", ".jpeg", ".png")):
                continue

            filename = PurePosixPath(normalized).name
            target = OUTPUT_DIR / filename
            with archive.open(name) as source, target.open("wb") as destination:
                destination.write(source.read())
            count += 1

    print(f"Extracted {count} product images into {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
