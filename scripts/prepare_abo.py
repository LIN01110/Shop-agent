"""
Purpose: ABO (Amazon Berkeley Objects) 数据预处理：将 listings parquet 转换为项目商品 schema，
         并生成合成标题查询集，用于大规模检索质量/延迟压测。

数据源: https://amazon-berkeley-objects.s3.amazonaws.com/index.html (CC-BY-4.0，仅研究用途)
镜像:   https://hf-mirror.com/datasets/hyper3labs/amazon-berkeley-objects

用法:
  python scripts/prepare_abo.py --max-products 50000 --max-queries 500
  python scripts/prepare_abo.py --max-products 0        # 全量 147k
"""

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = ROOT_DIR / "data_external" / "abo" / "listings"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "data" / "benchmarks" / "abo"

# ABO 英文 product_type/node 关键词 → 项目中文一级类目（近似映射，评测用途）
CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("beauty|skin|hair care|cosmetic|makeup|lipstick|shampoo|lotion|fragrance|nail", "美妆护肤"),
    ("shoe|shirt|clothing|dress|jeans|jacket|sock|apparel|fashion|sport|athletic|boot|sneaker|hat|bag|luggage|backpack|watch|jewelry|earring|necklace|ring", "服饰运动"),
    ("electron|phone|computer|laptop|tablet|camera|headphone|speaker|audio|cable|charger|battery|monitor|keyboard|mouse|tv|television|sound|recording", "数码电子"),
    ("home|kitchen|furniture|bedding|bed|pillow|cookware|knife|storage|lamp|light|decor|garden|tool|bath|towel|curtain|rug|table|chair", "家居生活"),
    ("food|snack|grocery|beverage|tea|coffee|candy|vitamin|supplement|gourmet", "食品饮料"),
    ("toy|baby|infant|kids|game|puzzle|doll|stroller|diaper", "母婴玩具"),
]

# 每类目合成价格带（元）；ABO 无价格字段，价格仅用于过滤器压测，会在 attributes 标记 synthesized
PRICE_BANDS: dict[str, tuple[float, float]] = {
    "美妆护肤": (29.0, 1299.0),
    "服饰运动": (39.0, 1599.0),
    "数码电子": (49.0, 8999.0),
    "家居生活": (19.0, 2999.0),
    "食品饮料": (9.9, 399.0),
    "母婴玩具": (15.0, 999.0),
    "其他": (9.9, 999.0),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare ABO listings into the project product schema.")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--max-products", type=int, default=50000, help="0 表示全量")
    parser.add_argument("--max-queries", type=int, default=500)
    parser.add_argument("--query-lang", choices=["zh", "en", "both"], default="zh")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    raw_files = sorted(Path(args.raw_dir).glob("listings-*.parquet"))
    if not raw_files:
        raise FileNotFoundError(f"Missing ABO parquet shards under {args.raw_dir}")

    rng = random.Random(args.seed)
    zh_products: list[dict[str, Any]] = []
    other_products: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for path in raw_files:
        table = pq.read_table(path)
        for row in table.to_pylist():
            product = convert_product(row)
            if product is None:
                continue
            pid = product["id"]
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            if product["attributes"]["has_zh_title"]:
                zh_products.append(product)
            else:
                other_products.append(product)

    # 中文标题商品优先入选，其余随机填充到目标规模
    rng.shuffle(other_products)
    if args.max_products > 0:
        budget = max(0, args.max_products - len(zh_products))
        products = zh_products + other_products[:budget]
    else:
        products = zh_products + other_products
    # 不打乱最终顺序：保持 zh 商品在前，使 --product-limit N 截断时查询正例仍在索引内

    queries = build_queries(zh_products, query_lang=args.query_lang, max_queries=args.max_queries, rng=rng)
    product_ids = {p["id"] for p in products}
    queries = [
        q for q in queries if q["positive_product_ids"][0] in product_ids
    ]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "products.json").write_text(
        json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "queries.jsonl").write_text(
        "\n".join(json.dumps(q, ensure_ascii=False) for q in queries) + "\n", encoding="utf-8"
    )
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Amazon Berkeley Objects (CC-BY-4.0), https://github.com/jazcollins/amazon-berkeley-objects",
        "mirror": "https://hf-mirror.com/datasets/hyper3labs/amazon-berkeley-objects",
        "raw_dir": str(Path(args.raw_dir)),
        "max_products": args.max_products,
        "seed": args.seed,
        "products": len(products),
        "products_with_zh_title": sum(1 for p in products if p["attributes"]["has_zh_title"]),
        "queries": len(queries),
        "price_synthesized": True,
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Wrote {len(products)} products ({metadata['products_with_zh_title']} zh-titled) to {output_dir / 'products.json'}")
    print(f"Wrote {len(queries)} queries to {output_dir / 'queries.jsonl'}")
    print(f"Wrote metadata to {output_dir / 'metadata.json'}")
    return 0


def convert_product(row: dict[str, Any]) -> dict[str, Any] | None:
    item_id = str(row.get("item_id") or "").strip()
    if not item_id:
        return None
    raw = parse_raw_listing(row.get("raw_listing_json"))

    zh_title = pick_language_value(raw, "item_name", "zh")
    title = zh_title or clean_text(row.get("title"))
    if not title:
        return None

    brand = pick_language_value(raw, "brand", "zh") or clean_text(row.get("brand")) or "Unknown"
    product_type = clean_text(row.get("product_type_readable")) or "unknown"
    node_paths = row.get("node_paths") or []
    category = map_category(product_type, node_paths)
    color = pick_language_value(raw, "color", "zh") or clean_text(row.get("color"))
    bullets_zh = pick_language_values(raw, "bullet_point", "zh")
    bullets_en = [clean_text(row.get("style"))] if row.get("style") else []
    description = build_description(raw, bullets_zh, bullets_en)

    return {
        "id": f"abo_{safe_id(item_id)}",
        "name": title[:240],
        "category": category,
        "sub_category": product_type[:120],
        "brand": brand[:120],
        "product_types": [product_type],
        "price": synthesize_price(item_id, category),
        "stock": 0,
        "image_url": clean_text(row.get("main_image_url")),
        "detail_url": "",
        "tags": build_tags(category, product_type, brand, title, color),
        "attributes": {
            "source": "amazon_berkeley_objects",
            "raw_item_id": item_id,
            "country": clean_text(row.get("country")),
            "product_type": product_type,
            "node_paths": [str(p)[:200] for p in node_paths[:3]],
            "has_zh_title": bool(zh_title),
            "price_synthesized": True,
        },
        "description": description[:2000],
    }


def parse_raw_listing(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def pick_language_value(raw: dict[str, Any], field: str, lang_prefix: str) -> str:
    for entry in raw.get(field) or []:
        if isinstance(entry, dict) and str(entry.get("language_tag", "")).startswith(lang_prefix):
            return clean_text(entry.get("value"))
    return ""


def pick_language_values(raw: dict[str, Any], field: str, lang_prefix: str) -> list[str]:
    values: list[str] = []
    for entry in raw.get(field) or []:
        if isinstance(entry, dict) and str(entry.get("language_tag", "")).startswith(lang_prefix):
            text = clean_text(entry.get("value"))
            if text:
                values.append(text)
    return values


def build_description(raw: dict[str, Any], bullets_zh: list[str], bullets_en: list[str]) -> str:
    desc = pick_language_value(raw, "product_description", "zh") or pick_language_value(
        raw, "product_description", "en"
    )
    bullets = bullets_zh or pick_language_values(raw, "bullet_point", "en") or bullets_en
    parts = [part for part in [desc, "；".join(bullets[:5])] if part]
    return " ".join(parts)


def map_category(product_type: str, node_paths: list[Any]) -> str:
    import re

    haystack = " ".join([product_type, *(str(p) for p in node_paths[:2])]).lower()
    for pattern, category in CATEGORY_KEYWORDS:
        if re.search(pattern, haystack):
            return category
    return "其他"


def synthesize_price(item_id: str, category: str) -> float:
    low, high = PRICE_BANDS.get(category, PRICE_BANDS["其他"])
    digest = hashlib.md5(item_id.encode("utf-8")).hexdigest()
    ratio = int(digest[:8], 16) / 0xFFFFFFFF
    price = low + ratio * (high - low)
    return round(price, 2)


def build_tags(category: str, product_type: str, brand: str, title: str, color: str) -> list[str]:
    tags = ["amazon_abo", category, product_type, brand, title]
    if color:
        tags.append(color)
    return [tag for tag in tags if tag]


def build_queries(
    zh_products: list[dict[str, Any]],
    *,
    query_lang: str,
    max_queries: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """合成标题查询：截取清洗后标题前 32 字符（去品牌/通用前缀，保留规格后缀）作为查询，目标商品为正例。

    ABO 存在大量仅规格不同的近似重复商品（同款戒指的尺寸 11/14），
    短前缀查询在语义上无法区分它们，因此：
    1. 查询保留到 32 字符，让尺寸/颜色等区分性后缀进入查询；
    2. 按清洗后标题去重，保证每个正例的标题在查询集中唯一。
    这是检索系统常用的 title-to-product 基准，用于同一管线下横向对比 embedding 方案。
    """
    queries: list[dict[str, Any]] = []
    pool = list(zh_products)
    rng.shuffle(pool)
    import re

    bracket_prefix = re.compile(r"^\[[^\]]*\]\s*")
    generic_tokens = ["亚马逊品牌", "亚马逊倍思", "Amazon Collection", "AmazonBasics", "亚马逊自营"]
    cjk = re.compile(r"[一-鿿]")
    seen_titles: set[str] = set()
    for product in pool:
        title = str(product["name"])
        brand = str(product["brand"])
        # 去掉 [Brand] 前缀与品牌名/通用标记重复出现，避免查询退化为品牌截断碎片
        text = bracket_prefix.sub("", title)
        if brand and brand != "Unknown":
            base_brand = brand.split("(")[0].strip()
            text = text.replace(brand, "").replace(base_brand, "")
        for token in generic_tokens:
            text = text.replace(token, "")
        text = " ".join(text.split()).strip(" ,、-")
        if text in seen_titles:
            continue
        seen_titles.add(text)
        query_text = text[:32].strip()
        # 中文查询集要求至少 4 个 CJK 字符，否则退化为英文品牌词
        if len(query_text) < 6 or len(cjk.findall(query_text)) < 4:
            continue
        pid = product["id"]
        queries.append(
            {
                "query_id": f"abo_title_{len(queries):05d}",
                "query": query_text,
                "locale": "zh",
                "labels": {pid: "E"},
                "relevance": {pid: 3},
                "positive_product_ids": [pid],
                "synthetic": True,
            }
        )
        if len(queries) >= max_queries:
            break
    return queries


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"prepare_abo failed: {exc}", file=sys.stderr)
        raise
