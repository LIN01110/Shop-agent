"""
Purpose: 构建 sentence-transformers 对比学习训练集（LoRA 微调 BGE 用）。

数据源（均本地已有）:
  1. ESCI 原始 parquet：query-product 人工标注对，取 E/S 为正例（英文，真实用户查询）
  2. ABO 中文商品：清洗标题 → 描述/要点 对（中文，领域适配）；评测集涉及的商品排除，防泄漏

输出: data/benchmarks/st_train/train_pairs.jsonl  {"query": ..., "positive": ...}

用法:
  python scripts/build_st_training_data.py --max-esci 20000 --max-abo 12000
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

ROOT_DIR = Path(__file__).resolve().parents[1]
ESCI_DIR = ROOT_DIR / "data_external" / "esci" / "tasksource_raw"
ABO_PRODUCTS = ROOT_DIR / "data" / "benchmarks" / "abo_full" / "products.json"
ABO_QUERIES = ROOT_DIR / "data" / "benchmarks" / "abo_full" / "queries.jsonl"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "benchmarks" / "st_train" / "train_pairs.jsonl"

POSITIVE_LABELS = {"E", "S"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ST contrastive training pairs from ESCI + ABO.")
    parser.add_argument("--max-esci", type=int, default=20000)
    parser.add_argument("--max-abo", type=int, default=12000)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    esci_pairs = build_esci_pairs(args.max_esci, rng)
    abo_pairs = build_abo_pairs(args.max_abo, rng)

    pairs = esci_pairs + abo_pairs
    rng.shuffle(pairs)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in pairs) + "\n", encoding="utf-8"
    )
    print(f"esci_pairs={len(esci_pairs)} abo_pairs={len(abo_pairs)} total={len(pairs)}")
    print(f"Wrote {output}")
    return 0


def build_esci_pairs(max_pairs: int, rng: random.Random) -> list[dict[str, str]]:
    if max_pairs <= 0:
        return []
    examples_path = ESCI_DIR / "shopping_queries_dataset_examples.parquet"
    products_path = ESCI_DIR / "shopping_queries_dataset_products.parquet"
    if not examples_path.exists() or not products_path.exists():
        print(f"WARN: ESCI raw parquet missing under {ESCI_DIR}, skip", file=sys.stderr)
        return []

    examples = ds.dataset(examples_path, format="parquet").to_table(
        columns=["query", "product_id", "esci_label"],
        filter=(pc.field("product_locale") == "us")
        & pc.field("esci_label").isin(list(POSITIVE_LABELS)),
    ).to_pylist()
    rng.shuffle(examples)
    examples = examples[: max_pairs * 2]  # 过滤缺标题后可能不足，多取一倍

    product_ids = list({str(e["product_id"]) for e in examples})
    products = ds.dataset(products_path, format="parquet").to_table(
        columns=["product_id", "product_title", "product_description", "product_bullet_point"],
        filter=(pc.field("product_locale") == "us") & pc.field("product_id").isin(product_ids),
    ).to_pylist()
    text_by_id = {}
    for p in products:
        title = clean(p.get("product_title"))
        if not title:
            continue
        extra = clean(p.get("product_description")) or clean(p.get("product_bullet_point"))
        text_by_id[str(p["product_id"])] = f"{title} {extra[:300]}".strip()

    pairs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for e in examples:
        query = clean(e.get("query"))
        doc = text_by_id.get(str(e["product_id"]))
        if not query or not doc:
            continue
        key = (query.lower(), str(e["product_id"]))
        if key in seen:
            continue
        seen.add(key)
        pairs.append({"query": query, "positive": doc, "source": "esci"})
        if len(pairs) >= max_pairs:
            break
    return pairs


def build_abo_pairs(max_pairs: int, rng: random.Random) -> list[dict[str, str]]:
    if not ABO_PRODUCTS.exists():
        print(f"WARN: {ABO_PRODUCTS} missing, skip", file=sys.stderr)
        return []
    eval_pids = set()
    if ABO_QUERIES.exists():
        for line in ABO_QUERIES.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            eval_pids.update(item.get("positive_product_ids", []))

    products = json.loads(ABO_PRODUCTS.read_text(encoding="utf-8"))
    zh_products = [
        p for p in products
        if p["attributes"].get("has_zh_title") and p["id"] not in eval_pids
    ]
    rng.shuffle(zh_products)

    bracket_prefix = re.compile(r"^\[[^\]]*\]\s*")
    pairs: list[dict[str, str]] = []
    for p in zh_products:
        title = bracket_prefix.sub("", str(p["name"])).strip()
        desc = clean(p.get("description"))
        if len(title) < 6 or not desc:
            continue
        pairs.append({"query": title[:64], "positive": f"{title} {desc[:300]}", "source": "abo_zh"})
        if len(pairs) >= max_pairs:
            break
    return pairs


def clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


if __name__ == "__main__":
    raise SystemExit(main())
