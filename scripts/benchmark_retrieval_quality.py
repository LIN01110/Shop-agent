"""
Purpose: 检索质量评测：对比三种检索配置的 Recall@K / NDCG@10 / MRR。

三种配置：
  raw_recall      — 向量库裸召回（无后过滤、无重排序）
  pipeline_rerank — 完整检索管道 + 空过滤器（召回→7个PostProcessor→去重→重排序）
  pipeline_nlu    — 完整检索管道 + NLU 自动解析的过滤器（生产路径，extract_filters）

Ground truth 来自 data/evaluation/retrieval_queries.jsonl（规则自动标注）。
"""

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from benchmark_common import EVAL_DIR, mrr, ndcg_at_k, recall_at_k, save_json

from server.agent.filters import extract_filters
from server.commerce.facts import configure_fact_provider
from server.app_container import create_commerce_fact_provider
from server.config import get_settings
from server.rag.post_process import SearchFilters
from server.rag.retrieval_pipeline import ProductRetrievalPipeline
from server.rag.vector_store import LocalJsonVectorStore

TOP_K = 10


def conditions_to_search_filters(conditions) -> SearchFilters:
    """把 extract_filters 的 FilterCondition 列表映射为 SearchFilters（对齐 from_session 逻辑）。"""
    min_price = max_price = None
    keywords: list[str] = []
    product_types: list[str] = []
    excluded_product_types: list[str] = []
    exclusions: list[str] = []
    for cond in conditions:
        if cond.kind == "max_price":
            max_price = float(cond.value)
        elif cond.kind == "min_price":
            min_price = float(cond.value)
        elif cond.kind == "keyword":
            keywords.append(cond.value)
        elif cond.kind == "product_type":
            product_types.append(cond.value)
        elif cond.kind == "exclude_product_type":
            excluded_product_types.append(cond.value)
        elif cond.kind == "exclude":
            exclusions.append(cond.value)
    return SearchFilters(
        min_price=min_price,
        max_price=max_price,
        keywords=keywords,
        product_types=product_types,
        excluded_product_types=excluded_product_types,
        exclusions=exclusions,
    )


def hit_id(hit: dict) -> str:
    return str(hit.get("id") or hit.get("metadata", {}).get("id", ""))


def evaluate_config(name: str, queries: list[dict], rank_fn) -> dict:
    per_query: list[dict] = []
    for query in queries:
        relevant = set(query["relevant_ids"])
        ranked_ids = rank_fn(query)
        per_query.append({
            "query_id": query["query_id"],
            "query": query["query"],
            "query_type": query["query_type"],
            "relevant_count": len(relevant),
            "recall@5": round(recall_at_k(ranked_ids, relevant, 5), 4),
            "recall@10": round(recall_at_k(ranked_ids, relevant, 10), 4),
            "ndcg@10": round(ndcg_at_k(ranked_ids, relevant, 10), 4),
            "mrr": round(mrr(ranked_ids, relevant), 4),
        })

    def avg(field: str, rows: list[dict]) -> float:
        return round(sum(r[field] for r in rows) / len(rows), 4) if rows else 0.0

    by_type: dict[str, dict] = {}
    for query_type in sorted({r["query_type"] for r in per_query}):
        rows = [r for r in per_query if r["query_type"] == query_type]
        by_type[query_type] = {
            "count": len(rows),
            "recall@5": avg("recall@5", rows),
            "recall@10": avg("recall@10", rows),
            "ndcg@10": avg("ndcg@10", rows),
            "mrr": avg("mrr", rows),
        }

    return {
        "config": name,
        "overall": {
            "count": len(per_query),
            "recall@5": avg("recall@5", per_query),
            "recall@10": avg("recall@10", per_query),
            "ndcg@10": avg("ndcg@10", per_query),
            "mrr": avg("mrr", per_query),
        },
        "by_query_type": by_type,
        "per_query": per_query,
    }


def main() -> None:
    queries_path = EVAL_DIR / "retrieval_queries.jsonl"
    if not queries_path.exists():
        raise SystemExit("请先运行 scripts/benchmark_build_eval_data.py 生成评测数据集")
    queries = [json.loads(line) for line in queries_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    settings = get_settings()
    configure_fact_provider(create_commerce_fact_provider(settings))

    store = LocalJsonVectorStore(settings.product_data_file)
    pipeline = ProductRetrievalPipeline(store)

    def raw_recall(query: dict) -> list[str]:
        hits = store.query(query=query["query"], top_k=TOP_K)
        return [hit_id(h) for h in hits]

    def pipeline_rerank(query: dict) -> list[str]:
        result = pipeline.run(query["query"], SearchFilters(), top_k=TOP_K)
        return [hit_id(h) for h in result.hits]

    def pipeline_nlu(query: dict) -> list[str]:
        filters = conditions_to_search_filters(extract_filters(query["query"]))
        result = pipeline.run(query["query"], filters, top_k=TOP_K)
        return [hit_id(h) for h in result.hits]

    configs = [
        ("raw_recall", raw_recall),
        ("pipeline_rerank", pipeline_rerank),
        ("pipeline_nlu", pipeline_nlu),
    ]

    results = {
        "meta": {
            "queries_file": str(queries_path),
            "num_queries": len(queries),
            "top_k": TOP_K,
            "store": "LocalJsonVectorStore",
            "ground_truth": "规则自动标注（类目/子类目/品牌/价格字段匹配）",
        },
        "configs": {},
    }
    for name, fn in configs:
        outcome = evaluate_config(name, queries, fn)
        results["configs"][name] = outcome
        overall = outcome["overall"]
        print(
            f"{name:18s} Recall@5={overall['recall@5']:.3f} "
            f"Recall@10={overall['recall@10']:.3f} "
            f"NDCG@10={overall['ndcg@10']:.3f} MRR={overall['mrr']:.3f}"
        )

    out = save_json(results, "retrieval_quality.json")
    print(f"结果已保存: {out}")


if __name__ == "__main__":
    main()
