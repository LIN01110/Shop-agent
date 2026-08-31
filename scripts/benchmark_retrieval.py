"""
Purpose: 检索性能压测脚本：对比本地JSON和Chroma在不同商品库规模下的检索延迟和召回率。
"""

import argparse
import gc
import json
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from server.rag.post_process import SearchFilters  # noqa: E402
from server.rag.vector_store import ChromaStore, LocalJsonVectorStore, VectorDocument, load_product_documents  # noqa: E402
from server.tools.product_search import ProductSearchTool  # noqa: E402


DEFAULT_CASES = [
    {
        "name": "sports_shoes",
        "query": "推荐一款运动鞋",
        "filters": {"product_types": ["clothes.sports_shoes"]},
    },
    {
        "name": "compound_running_shoes",
        "query": "推荐一款适合跑步的鞋",
        "filters": {"product_types": ["clothes.sports_shoes"]},
    },
    {
        "name": "shoes_or_pants",
        "query": "推荐运动鞋或运动裤",
        "filters": {"product_types": ["clothes.sports_shoes", "clothes.sports_pants"]},
    },
    {
        "name": "eye_cream_budget",
        "query": "推荐一款保湿眼霜，预算250以内",
        "filters": {"max_price": 250, "keywords": ["保湿", "眼霜"], "product_types": ["beauty.eye_cream"]},
    },
    {
        "name": "phone_photo_budget",
        "query": "推荐一款拍照手机，预算4000以内",
        "filters": {"max_price": 4000, "keywords": ["拍照"], "product_types": ["electronics.phone"]},
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark retrieval latency on synthetic large product stores.")
    parser.add_argument("--data", default=str(ROOT_DIR / "data" / "products_ref.json"))
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 10000, 50000])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--store", choices=["local", "chroma"], default="local")
    parser.add_argument("--persist-dir", default="", help="Optional Chroma persist dir for --store chroma.")
    parser.add_argument("--case-file", default="", help="Optional JSON file with benchmark cases.")
    parser.add_argument("--output", default="", help="Optional path to write a JSON report.")
    args = parser.parse_args()

    cases = load_cases(Path(args.case_file)) if args.case_file else DEFAULT_CASES
    base_documents = load_product_documents(Path(args.data))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": str(args.data),
        "runs": args.runs,
        "warmup": args.warmup,
        "top_k": args.top_k,
        "store": args.store,
        "store_builds": [],
        "results": [],
    }

    print("size\tcase\tp50_ms\tp95_ms\tavg_ms\tmin_ms\tmax_ms\tcards")
    temp_dirs: list[Path] = []
    for size in args.sizes:
        expand_started = time.perf_counter()
        documents = expand_documents(base_documents, size)
        expand_ms = (time.perf_counter() - expand_started) * 1000
        build_started = time.perf_counter()
        store = build_store(args.store, documents, size, args.persist_dir, temp_dirs)
        build_ms = (time.perf_counter() - build_started) * 1000
        report["store_builds"].append(
            {
                "size": size,
                "document_expand_ms": expand_ms,
                "store_build_ms": build_ms,
            }
        )
        print(
            f"# size={size} document_expand_ms={expand_ms:.2f} store_build_ms={build_ms:.2f}",
            file=sys.stderr,
        )
        tool = ProductSearchTool(store)

        for case in cases:
            filters = build_search_filters(case.get("filters", {}))
            for _ in range(args.warmup):
                tool.run(case["query"], filters=filters, top_k=args.top_k)

            latencies: list[float] = []
            card_counts: list[int] = []
            for _ in range(args.runs):
                started = time.perf_counter()
                cards = tool.run(case["query"], filters=filters, top_k=args.top_k)
                elapsed_ms = (time.perf_counter() - started) * 1000
                latencies.append(elapsed_ms)
                card_counts.append(len(cards))

            summary = summarize_latencies(latencies)
            result = {
                "size": size,
                "case": case["name"],
                "query": case["query"],
                "filters": case.get("filters", {}),
                "cards_min": min(card_counts) if card_counts else 0,
                "cards_max": max(card_counts) if card_counts else 0,
                **summary,
            }
            report["results"].append(result)
            print(
                f"{size}\t{case['name']}\t{summary['p50_ms']:.2f}\t{summary['p95_ms']:.2f}\t"
                f"{summary['avg_ms']:.2f}\t{summary['min_ms']:.2f}\t{summary['max_ms']:.2f}\t"
                f"{result['cards_min']}-{result['cards_max']}"
            )

        close_store(store)
        del tool
        del store
        gc.collect()

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nWrote report to {output_path}")

    for temp_dir in temp_dirs:
        cleanup_directory(temp_dir)

    return 0


def load_cases(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def expand_documents(base_documents: list[VectorDocument], target_size: int) -> list[VectorDocument]:
    if target_size <= 0:
        raise ValueError("target_size must be positive")
    if not base_documents:
        raise ValueError("base_documents must not be empty")

    expanded: list[VectorDocument] = []
    for index in range(target_size):
        base = base_documents[index % len(base_documents)]
        metadata = dict(base.metadata)
        metadata["tags"] = list(base.metadata.get("tags", []))
        metadata["product_types"] = list(base.metadata.get("product_types", []))
        metadata["attributes"] = dict(base.metadata.get("attributes", {}))
        metadata["id"] = f"{base.id}__bench_{index}"
        metadata["name"] = f"{metadata.get('name', '')} #{index}"
        text = f"{base.text} benchmark_copy_{index % 997}"
        expanded.append(VectorDocument(id=metadata["id"], text=text, metadata=metadata))
    return expanded


def build_store(
    store_type: str,
    documents: list[VectorDocument],
    size: int,
    persist_dir: str,
    temp_dirs: list[Path],
):
    if store_type == "local":
        return LocalJsonVectorStore.from_documents(documents)

    if persist_dir:
        persist_path = Path(persist_dir) / f"benchmark_{size}"
    else:
        persist_path = Path(tempfile.mkdtemp(prefix="rag_chroma_bench_"))
        temp_dirs.append(persist_path)

    store = ChromaStore(
        persist_path,
        collection_name=f"benchmark_{size}_{int(time.time())}",
    )
    store.add(documents)
    return store


def close_store(store: Any) -> None:
    client = getattr(store, "client", None)
    system = getattr(client, "_system", None)
    stop = getattr(system, "stop", None)
    if callable(stop):
        stop()


def cleanup_directory(path: Path) -> None:
    for attempt in range(3):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            gc.collect()
            time.sleep(0.2 * (attempt + 1))
    shutil.rmtree(path, ignore_errors=True)


def build_search_filters(payload: dict[str, Any]) -> SearchFilters:
    return SearchFilters(
        max_price=payload.get("max_price"),
        keywords=list(payload.get("keywords", [])),
        product_types=list(payload.get("product_types", [])),
        exclusions=list(payload.get("exclusions", [])),
    )


def summarize_latencies(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "avg_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0}
    return {
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "avg_ms": statistics.fmean(values),
        "min_ms": min(values),
        "max_ms": max(values),
    }


def percentile(values: list[float], percentile_value: int) -> float:
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
