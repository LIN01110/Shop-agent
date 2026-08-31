"""
Purpose: 参考数据集准备脚本：从外部数据集抽取商品数据，构建products_ref.json。
"""

import argparse
import json
import sys
import zipfile
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from server.rag.taxonomy import infer_product_type_ids  # noqa: E402


def normalize_product(raw: dict[str, Any]) -> dict[str, Any]:
    skus = raw.get("skus") or []
    sku_prices = [float(sku["price"]) for sku in skus if "price" in sku]
    price = min(sku_prices) if sku_prices else float(raw.get("base_price", 0))
    max_price = max(sku_prices) if sku_prices else price

    rag = raw.get("rag_knowledge") or {}
    faqs = rag.get("official_faq") or []
    reviews = rag.get("user_reviews") or []

    faq_text = " ".join(
        f"问：{item.get('question', '')} 答：{item.get('answer', '')}"
        for item in faqs[:8]
    )
    review_text = " ".join(
        f"{item.get('rating', '')}星评价：{item.get('content', '')}"
        for item in reviews[:8]
    )
    marketing = rag.get("marketing_description", "")
    description = " ".join(part for part in [marketing, faq_text, review_text] if part)

    sku_options: dict[str, list[str]] = {}
    sku_summaries: list[str] = []
    for sku in skus:
        properties = sku.get("properties") or {}
        if properties:
            sku_summaries.append(
                " ".join(f"{key}:{value}" for key, value in properties.items())
            )
        for key, value in properties.items():
            sku_options.setdefault(key, [])
            if value not in sku_options[key]:
                sku_options[key].append(value)

    tags = [
        raw.get("category", ""),
        raw.get("sub_category", ""),
        raw.get("brand", ""),
        *raw.get("title", "").replace("/", " ").split(),
        *sku_summaries[:10],
    ]

    product = {
        "id": raw["product_id"],
        "name": raw["title"],
        "category": raw.get("category", ""),
        "sub_category": raw.get("sub_category", ""),
        "brand": raw.get("brand", ""),
        "price": price,
        "stock": 100,
        "image_url": raw.get("image_path", ""),
        "detail_url": "",
        "tags": [tag for tag in tags if tag],
        "attributes": {
            "base_price": raw.get("base_price", price),
            "min_sku_price": price,
            "max_sku_price": max_price,
            "sku_count": len(skus),
            "sku_options": sku_options,
            "faq_count": len(faqs),
            "review_count": len(reviews),
        },
        "description": description,
    }
    product["product_types"] = infer_product_type_ids(product)
    return product


def convert(zip_path: Path, output_path: Path) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            if not name.endswith(".json"):
                continue
            raw = json.loads(archive.read(name).decode("utf-8"))
            products.append(normalize_product(raw))

    products.sort(key=lambda item: item["id"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(products, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return products


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize the reference ecommerce dataset.")
    parser.add_argument(
        "--zip",
        default=str(ROOT_DIR / "ecommerce_agent_dataset_ref.zip"),
        help="Path to the reference dataset zip.",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT_DIR / "data" / "products_ref.json"),
        help="Path to write normalized product JSON.",
    )
    args = parser.parse_args()

    products = convert(Path(args.zip), Path(args.output))
    categories = sorted({item["category"] for item in products})
    print(f"Wrote {len(products)} products to {args.output}")
    print("Categories:", ", ".join(categories))


if __name__ == "__main__":
    main()
