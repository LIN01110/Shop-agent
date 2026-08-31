"""
Purpose: ESCI检索评估脚本：在ESCI数据集上评估检索质量。
"""

import argparse
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from server.config import get_settings  # noqa: E402
from server.rag.post_process import SearchFilters  # noqa: E402
from server.rag.vector_store import (  # noqa: E402
    ChromaStore,
    LocalJsonVectorStore,
    build_chroma_embedding_function,
    load_product_documents,
)
from server.tools.product_search import ProductSearchTool  # noqa: E402


DEFAULT_DATA_DIR = ROOT_DIR / "data" / "benchmarks" / "esci_small"
DEFAULT_CHROMA_DIR = DEFAULT_DATA_DIR / "chroma"
DEFAULT_COLLECTION_NAME = "esci_small_products"
POSITIVE_LABELS = {"E", "S"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate retrieval on an ESCI small subset.")
    parser.add_argument("--products", default=str(DEFAULT_DATA_DIR / "products.json"))
    parser.add_argument("--queries", default=str(DEFAULT_DATA_DIR / "queries.jsonl"))
    parser.add_argument("--store", choices=["local", "chroma"], default="local")
    parser.add_argument("--embedding", choices=["hashing", "ark", "ark-multimodal"], default="hashing")
    parser.add_argument("--embedding-model", default="")
    parser.add_argument("--persist-dir", default=str(DEFAULT_CHROMA_DIR))
    parser.add_argument("--collection-name", default=DEFAULT_COLLECTION_NAME)
    parser.add_argument("--embedding-batch-size", type=int, default=64)
    parser.add_argument("--product-limit", type=int, default=0)
    parser.add_argument("--allow-full-multimodal-index", action="store_true")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", default=str(DEFAULT_DATA_DIR / "retrieval_report.json"))
    args = parser.parse_args()

    products_path = Path(args.products)
    queries_path = Path(args.queries)
    queries = load_jsonl(queries_path)
    if args.limit > 0:
        queries = queries[: args.limit]

    store, store_metadata = build_store(args, products_path)
    tool = ProductSearchTool(store)

    rows = []
    latencies = []
    for query in queries:
        started = time.perf_counter()
        cards = tool.run(query["query"], SearchFilters(), top_k=args.top_k)
        elapsed_ms = (time.perf_counter() - started) * 1000
        latencies.append(elapsed_ms)
        retrieved_ids = [card["id"] for card in cards]
        rows.append(evaluate_query(query, retrieved_ids, k=args.top_k, latency_ms=elapsed_ms))

    summary = summarize(
        rows,
        latencies,
        store_build_ms=float(store_metadata["store_build_ms"]),
        top_k=args.top_k,
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "products": str(products_path),
        "queries": str(queries_path),
        "product_count": store_metadata["product_count"],
        "query_count": len(queries),
        "top_k": args.top_k,
        "store": store_metadata,
        "summary": summary,
        "label_summary": summarize_by_label(rows),
        "miss_examples": miss_examples(rows),
        "examples": rows[:20],
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Wrote report to {output_path}")
    return 0


def build_store(args: argparse.Namespace, products_path: Path) -> tuple[Any, dict[str, Any]]:
    started = time.perf_counter()
    if args.store == "local":
        store = LocalJsonVectorStore(products_path)
        return store, {
            "store": "local",
            "embedding": "lexical",
            "product_count": len(store.documents),
            "store_build_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    if args.embedding in {"ark", "ark-multimodal"}:
        settings = get_settings()
        if not settings.ark_api_key.strip():
            raise RuntimeError("ARK_API_KEY is required for --embedding ark.")
        use_ark_embedding = True
        embedding_api = "multimodal" if args.embedding == "ark-multimodal" else "text"
        api_key = settings.ark_api_key
        base_url = settings.ark_base_url
        model = args.embedding_model.strip() or settings.ark_embedding_model
    else:
        settings = get_settings()
        use_ark_embedding = False
        embedding_api = "text"
        api_key = ""
        base_url = settings.ark_base_url
        model = settings.ark_embedding_model

    effective_batch_size = min(args.embedding_batch_size, 32) if args.embedding == "ark-multimodal" else args.embedding_batch_size
    embedding_function, collection_name = build_chroma_embedding_function(
        use_ark_embedding=use_ark_embedding,
        embedding_api=embedding_api,
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_seconds=settings.embedding_timeout_seconds,
        batch_size=effective_batch_size,
        collection_name=args.collection_name,
    )
    store = ChromaStore(
        Path(args.persist_dir),
        collection_name=collection_name,
        embedding_function=embedding_function,
    )
    documents = load_product_documents(products_path)
    if args.product_limit > 0:
        documents = documents[: args.product_limit]
    if (
        args.embedding == "ark-multimodal"
        and args.product_limit <= 0
        and not args.allow_full_multimodal_index
        and store.count() < len(documents)
    ):
        raise RuntimeError(
            "Refusing to build a full ark-multimodal ESCI index by default. "
            "Use --product-limit for smoke tests or --allow-full-multimodal-index for an intentional full run."
        )
    count_before = store.count()
    ingested = False
    if count_before < len(documents):
        ingest_started = time.perf_counter()
        store.add(documents)
        ingested = True
        ingest_ms = round((time.perf_counter() - ingest_started) * 1000, 2)
    else:
        ingest_ms = 0.0

    return store, {
        "store": "chroma",
        "embedding": args.embedding,
        "embedding_api": embedding_api,
        "embedding_model": model if args.embedding in {"ark", "ark-multimodal"} else "local_hashing_embedding",
        "embedding_batch_size": effective_batch_size if args.embedding in {"ark", "ark-multimodal"} else None,
        "persist_dir": str(Path(args.persist_dir)),
        "collection_name": collection_name,
        "collection_count_before": count_before,
        "collection_count_after": store.count(),
        "ingested": ingested,
        "ingest_ms": ingest_ms,
        "product_count": len(documents),
        "store_build_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def evaluate_query(query: dict[str, Any], retrieved_ids: list[str], *, k: int, latency_ms: float) -> dict[str, Any]:
    positive_ids = set(query.get("positive_product_ids", []))
    labels = {key: str(value) for key, value in query.get("labels", {}).items()}
    relevance = {key: int(value) for key, value in query.get("relevance", {}).items()}
    hits = [product_id for product_id in retrieved_ids[:k] if product_id in positive_ids]
    return {
        "query_id": query.get("query_id", ""),
        "query": query.get("query", ""),
        "retrieved_ids": retrieved_ids[:k],
        "positive_count": len(positive_ids),
        "hit_count": len(hits),
        "recall_at_k": len(hits) / len(positive_ids) if positive_ids else 0.0,
        "mrr_at_k": reciprocal_rank(retrieved_ids[:k], positive_ids),
        "ndcg_at_k": ndcg_at_k(retrieved_ids[:k], relevance, k),
        "label_hits": label_hits(retrieved_ids[:k], labels),
        "label_totals": label_totals(labels),
        "latency_ms": latency_ms,
    }


def reciprocal_rank(retrieved_ids: list[str], positive_ids: set[str]) -> float:
    for index, product_id in enumerate(retrieved_ids, 1):
        if product_id in positive_ids:
            return 1.0 / index
    return 0.0


def ndcg_at_k(retrieved_ids: list[str], relevance: dict[str, int], k: int) -> float:
    dcg = 0.0
    for index, product_id in enumerate(retrieved_ids[:k], 1):
        rel = relevance.get(product_id, 0)
        dcg += (2**rel - 1) / math.log2(index + 1)
    ideal_rels = sorted(relevance.values(), reverse=True)[:k]
    idcg = sum((2**rel - 1) / math.log2(index + 1) for index, rel in enumerate(ideal_rels, 1))
    return dcg / idcg if idcg > 0 else 0.0


def summarize(rows: list[dict[str, Any]], latencies: list[float], *, store_build_ms: float, top_k: int) -> dict[str, Any]:
    if not rows:
        return {
            "top_k": top_k,
            "store_build_ms": store_build_ms,
            "recall_at_k": 0.0,
            "mrr_at_k": 0.0,
            "ndcg_at_k": 0.0,
            "p50_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
        }
    return {
        "top_k": top_k,
        "store_build_ms": round(store_build_ms, 2),
        "recall_at_k": round(statistics.fmean(row["recall_at_k"] for row in rows), 4),
        "mrr_at_k": round(statistics.fmean(row["mrr_at_k"] for row in rows), 4),
        "ndcg_at_k": round(statistics.fmean(row["ndcg_at_k"] for row in rows), 4),
        "p50_latency_ms": round(percentile(latencies, 50), 2),
        "p95_latency_ms": round(percentile(latencies, 95), 2),
        "avg_latency_ms": round(statistics.fmean(latencies), 2),
    }


def summarize_by_label(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    aggregate: dict[str, dict[str, float]] = {}
    for row in rows:
        for label, total in row.get("label_totals", {}).items():
            slot = aggregate.setdefault(label, {"hits": 0.0, "total": 0.0, "query_count": 0.0})
            slot["hits"] += float(row.get("label_hits", {}).get(label, 0))
            slot["total"] += float(total)
            slot["query_count"] += 1.0

    summary: dict[str, dict[str, float]] = {}
    for label, values in sorted(aggregate.items()):
        total = values["total"]
        summary[label] = {
            "hit_count": int(values["hits"]),
            "total": int(total),
            "recall_at_k": round(values["hits"] / total, 4) if total else 0.0,
            "query_count": int(values["query_count"]),
        }
    return summary


def label_hits(retrieved_ids: list[str], labels: dict[str, str]) -> dict[str, int]:
    counts = {label: 0 for label in sorted(set(labels.values()))}
    for product_id in retrieved_ids:
        label = labels.get(product_id)
        if label:
            counts[label] = counts.get(label, 0) + 1
    return counts


def label_totals(labels: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label in labels.values():
        counts[label] = counts.get(label, 0) + 1
    return counts


def miss_examples(rows: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: (row["ndcg_at_k"], row["recall_at_k"], -row["positive_count"]))
    return [
        {
            "query_id": row["query_id"],
            "query": row["query"],
            "positive_count": row["positive_count"],
            "hit_count": row["hit_count"],
            "recall_at_k": row["recall_at_k"],
            "ndcg_at_k": row["ndcg_at_k"],
            "retrieved_ids": row["retrieved_ids"],
        }
        for row in ordered[:limit]
    ]


def percentile(values: list[float], percentile_value: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile_value / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


if __name__ == "__main__":
    raise SystemExit(main())
