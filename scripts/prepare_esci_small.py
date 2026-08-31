"""
Purpose: ESCI数据预处理脚本：将原始Parquet转换为项目可用的JSON格式。
"""

import argparse
import json
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = ROOT_DIR / "data_external" / "esci" / "esci-data" / "shopping_queries_dataset"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "data" / "benchmarks" / "esci_small"

LABEL_RELEVANCE = {"E": 3, "S": 2, "C": 1, "I": 0}
POSITIVE_LABELS = {"E", "S"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a local ESCI small retrieval benchmark subset.")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--locale", default="us")
    parser.add_argument("--max-queries", type=int, default=300)
    parser.add_argument("--max-products", type=int, default=12000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    output_dir = Path(args.output_dir)
    examples_path = raw_dir / "shopping_queries_dataset_examples.parquet"
    products_path = raw_dir / "shopping_queries_dataset_products.parquet"
    ensure_file(examples_path)
    ensure_file(products_path)

    examples = load_examples(examples_path, locale=args.locale)
    query_groups = group_examples_by_query(examples)
    selected_queries = select_queries(query_groups, max_queries=args.max_queries, seed=args.seed)
    selected_product_ids = select_product_ids(selected_queries, max_products=args.max_products)
    products_by_id = load_products(products_path, locale=args.locale, product_ids=selected_product_ids)

    benchmark_products = [
        convert_product(product_id, product, locale=args.locale)
        for product_id, product in products_by_id.items()
    ]
    benchmark_queries = convert_queries(selected_queries, products_by_id.keys(), locale=args.locale)

    output_dir.mkdir(parents=True, exist_ok=True)
    products_output = output_dir / "products.json"
    queries_output = output_dir / "queries.jsonl"
    metadata_output = output_dir / "metadata.json"
    products_output.write_text(json.dumps(benchmark_products, ensure_ascii=False, indent=2), encoding="utf-8")
    queries_output.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in benchmark_queries) + "\n",
        encoding="utf-8",
    )
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "https://github.com/amazon-science/esci-data",
        "raw_dir": str(raw_dir),
        "locale": args.locale,
        "max_queries": args.max_queries,
        "max_products": args.max_products,
        "seed": args.seed,
        "products": len(benchmark_products),
        "queries": len(benchmark_queries),
        "positive_labels": sorted(POSITIVE_LABELS),
        "label_relevance": LABEL_RELEVANCE,
    }
    metadata_output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {len(benchmark_products)} products to {products_output}")
    print(f"Wrote {len(benchmark_queries)} queries to {queries_output}")
    print(f"Wrote metadata to {metadata_output}")
    return 0


def ensure_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing ESCI file: {path}")


def load_examples(path: Path, *, locale: str) -> list[dict[str, Any]]:
    schema_names = set(pq.ParquetFile(path).schema.names)
    required = ["query_id", "query", "product_id", "esci_label", "product_locale"]
    missing = [name for name in required if name not in schema_names]
    if missing:
        raise ValueError(f"Examples parquet missing columns: {missing}")

    columns = [name for name in [*required, "split", "small_version", "large_version"] if name in schema_names]
    dataset = ds.dataset(path, format="parquet")
    table = dataset.to_table(
        columns=columns,
        filter=pc.field("product_locale") == locale,
    )
    return table.to_pylist()


def group_examples_by_query(examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
    for item in examples:
        query = str(item.get("query", "")).strip()
        if not query:
            continue
        groups[(item.get("query_id"), query)].append(item)

    query_groups: list[dict[str, Any]] = []
    for (query_id, query), judgements in groups.items():
        labels = {str(item.get("esci_label", "")).upper() for item in judgements}
        if not labels & POSITIVE_LABELS:
            continue
        query_groups.append(
            {
                "query_id": str(query_id),
                "query": query,
                "split": infer_split(judgements),
                "judgements": judgements,
            }
        )
    return query_groups


def infer_split(judgements: list[dict[str, Any]]) -> str:
    for item in judgements:
        split = str(item.get("split", "")).strip()
        if split:
            return split
    return ""


def select_queries(query_groups: list[dict[str, Any]], *, max_queries: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    test_like = [item for item in query_groups if str(item.get("split", "")).lower() in {"test", "eval"}]
    pool = test_like or query_groups
    pool = sorted(pool, key=lambda item: item["query_id"])
    rng.shuffle(pool)
    return pool[:max_queries]


def select_product_ids(queries: list[dict[str, Any]], *, max_products: int) -> set[str]:
    ranked_ids: list[str] = []
    for query in queries:
        judgements = sorted(
            query["judgements"],
            key=lambda item: LABEL_RELEVANCE.get(str(item.get("esci_label", "")).upper(), 0),
            reverse=True,
        )
        for item in judgements:
            product_id = str(item.get("product_id", "")).strip()
            if product_id:
                ranked_ids.append(product_id)

    result: list[str] = []
    seen: set[str] = set()
    for product_id in ranked_ids:
        if product_id in seen:
            continue
        seen.add(product_id)
        result.append(product_id)
        if len(result) >= max_products:
            break
    return set(result)


def load_products(path: Path, *, locale: str, product_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not product_ids:
        return {}
    schema_names = set(pq.ParquetFile(path).schema.names)
    columns = [
        name
        for name in [
            "product_id",
            "product_locale",
            "product_title",
            "product_description",
            "product_bullet_point",
            "product_brand",
            "product_color",
            "product_color_name",
        ]
        if name in schema_names
    ]
    dataset = ds.dataset(path, format="parquet")
    table = dataset.to_table(
        columns=columns,
        filter=(pc.field("product_locale") == locale) & pc.field("product_id").isin(list(product_ids)),
    )
    products: dict[str, dict[str, Any]] = {}
    for item in table.to_pylist():
        product_id = str(item.get("product_id", "")).strip()
        if product_id:
            products[product_id] = item
    return products


def convert_product(product_id: str, product: dict[str, Any], *, locale: str) -> dict[str, Any]:
    title = clean_text(product.get("product_title")) or product_id
    description = clean_text(product.get("product_description"))
    bullet_point = clean_text(product.get("product_bullet_point"))
    brand = clean_text(product.get("product_brand")) or "Unknown"
    color = clean_text(product.get("product_color_name") or product.get("product_color"))
    tags = ["amazon_esci", locale, brand, title]
    if color:
        tags.append(color)
    return {
        "id": make_public_product_id(product_id, locale),
        "name": title[:240],
        "category": "Amazon ESCI",
        "sub_category": locale,
        "brand": brand[:120],
        "product_types": [],
        "price": 0.0,
        "stock": 0,
        "image_url": "",
        "detail_url": "",
        "tags": tags,
        "attributes": {
            "source": "amazon_esci",
            "locale": locale,
            "raw_product_id": product_id,
            "color": color,
        },
        "description": " ".join(part for part in [description, bullet_point] if part)[:2000],
    }


def convert_queries(
    selected_queries: list[dict[str, Any]],
    available_product_ids: set[str],
    *,
    locale: str,
) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    for item in selected_queries:
        labels: dict[str, str] = {}
        relevance: dict[str, int] = {}
        for judgement in item["judgements"]:
            raw_product_id = str(judgement.get("product_id", "")).strip()
            if raw_product_id not in available_product_ids:
                continue
            label = str(judgement.get("esci_label", "")).upper()
            product_id = make_public_product_id(raw_product_id, locale)
            labels[product_id] = label
            relevance[product_id] = LABEL_RELEVANCE.get(label, 0)
        if not any(label in POSITIVE_LABELS for label in labels.values()):
            continue
        queries.append(
            {
                "query_id": item["query_id"],
                "query": item["query"],
                "locale": locale,
                "labels": labels,
                "relevance": relevance,
                "positive_product_ids": [
                    product_id for product_id, label in labels.items() if label in POSITIVE_LABELS
                ],
            }
        )
    return queries


def make_public_product_id(product_id: str, locale: str) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in product_id)
    return f"esci_{locale}_{safe}"


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"prepare_esci_small failed: {exc}", file=sys.stderr)
        raise
