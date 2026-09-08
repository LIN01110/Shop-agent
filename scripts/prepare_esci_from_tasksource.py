"""
Adapt tasksource/esci (HF mirror of Amazon ESCI, joined format) into the raw
parquet layout expected by scripts/prepare_esci_small.py.

Input : data_external/esci/tasksource/test_shard0.parquet  (one test shard)
Output: data_external/esci/tasksource_raw/
          shopping_queries_dataset_examples.parquet
          shopping_queries_dataset_products.parquet

Labels are mapped back from full names to E/S/C/I.
Only product_locale == 'us' and small_version == 1 rows are kept (ESCI-S).
"""

import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data_external" / "esci" / "tasksource" / "test_shard0.parquet"
OUT_DIR = ROOT / "data_external" / "esci" / "tasksource_raw"

LABEL_MAP = {
    "exact": "E",
    "substitute": "S",
    "complement": "C",
    "irrelevant": "I",
    "e": "E",
    "s": "S",
    "c": "C",
    "i": "I",
}


def main() -> int:
    table = pq.read_table(SRC)
    print("columns:", table.schema.names)
    df = table.to_pandas()
    print(f"rows total: {len(df)}")
    print("label values:", sorted(df["esci_label"].astype(str).unique()))
    print("locale values:", sorted(df["product_locale"].astype(str).unique()))

    df["esci_label"] = df["esci_label"].astype(str).str.strip().str.lower().map(LABEL_MAP)
    before = len(df)
    df = df[df["esci_label"].notna()]
    print(f"rows after label map: {len(df)} (dropped {before - len(df)})")

    df = df[(df["product_locale"] == "us") & (df["small_version"] == 1)]
    print(f"rows us + small_version=1: {len(df)}")

    examples = df[["query_id", "query", "product_id", "esci_label", "product_locale", "small_version", "large_version"]].copy()
    examples["split"] = "test"

    product_cols = [
        "product_id",
        "product_locale",
        "product_title",
        "product_description",
        "product_bullet_point",
        "product_brand",
        "product_color",
    ]
    products = df[product_cols].drop_duplicates(subset=["product_id"], keep="first").copy()
    for col in product_cols:
        products[col] = products[col].fillna("").astype(str)
    print(f"unique products: {len(products)}")
    print(f"unique queries: {examples['query_id'].nunique()}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(examples, preserve_index=False), OUT_DIR / "shopping_queries_dataset_examples.parquet")
    pq.write_table(pa.Table.from_pandas(products, preserve_index=False), OUT_DIR / "shopping_queries_dataset_products.parquet")
    print(f"wrote raw parquets to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
