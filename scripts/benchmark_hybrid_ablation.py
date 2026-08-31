"""
Purpose: BM25 混合检索消融实验：在同一测试集（82 条规则标注查询）上对比
  vector_only   — 向量库裸召回（LocalJsonVectorStore）
  bm25_only     — BM25 倒排索引（jieba 分词，server/rag/bm25_engine.py）
  hybrid_rrf    — 向量 + BM25 的 RRF 融合（Reciprocal Rank Fusion, k=60）
  hybrid_rrf_ce — RRF 融合 Top-20 + Cross-Encoder 精排（BAAI/bge-reranker-base，纯 CE 分数）
  pipeline_nlu  — 生产路径（检索管道 + NLU 过滤，来自 benchmark_retrieval_quality）

指标：Recall@5 / Recall@10 / NDCG@10 / MRR。
"""

import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from benchmark_common import EVAL_DIR, mrr, ndcg_at_k, recall_at_k, save_json

from server.agent.filters import extract_filters
from server.app_container import create_commerce_fact_provider
from server.commerce.facts import configure_fact_provider
from server.config import get_settings
from server.rag.bm25_engine import BM25Engine
from server.rag.cross_encoder import RerankCandidate, SimpleCrossEncoderReranker
from server.rag.documents import load_product_documents
from server.rag.retrieval_pipeline import ProductRetrievalPipeline
from server.rag.vector_store import LocalJsonVectorStore
from scripts.benchmark_retrieval_quality import conditions_to_search_filters, evaluate_config, hit_id

TOP_K = 10
RRF_K = 60
CE_CANDIDATES = 20


def rrf_fusion(rankings: list[list[str]], k: int = RRF_K) -> list[str]:
    """Reciprocal Rank Fusion：score = Σ 1/(k + rank)。"""
    scores: dict[str, float] = {}
    for ranked in rankings:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return [doc_id for doc_id, _ in sorted(scores.items(), key=lambda item: -item[1])]


def main() -> None:
    queries_path = EVAL_DIR / "retrieval_queries.jsonl"
    queries = [json.loads(line) for line in queries_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    settings = get_settings()
    configure_fact_provider(create_commerce_fact_provider(settings))

    documents = load_product_documents(settings.product_data_file)
    store = LocalJsonVectorStore(settings.product_data_file)
    pipeline = ProductRetrievalPipeline(store)

    bm25 = BM25Engine()
    bm25.build(documents)
    print(f"BM25 索引构建完成：{len(documents)} 篇文档")

    doc_text = {doc.id: getattr(doc, "text", "") or "" for doc in documents}
    reranker = SimpleCrossEncoderReranker(
        model_name=settings.cross_encoder_model,
        device=settings.cross_encoder_device,
        batch_size=settings.cross_encoder_batch_size,
    )

    def vector_only(query: dict) -> list[str]:
        return [hit_id(h) for h in store.query(query=query["query"], top_k=TOP_K)]

    def bm25_only(query: dict) -> list[str]:
        return [hit.id for hit in bm25.search(query["query"], top_k=TOP_K)]

    def hybrid_rrf(query: dict) -> list[str]:
        vector_ranked = [hit_id(h) for h in store.query(query=query["query"], top_k=TOP_K * 2)]
        bm25_ranked = [hit.id for hit in bm25.search(query["query"], top_k=TOP_K * 2)]
        return rrf_fusion([vector_ranked, bm25_ranked])[:TOP_K]

    def hybrid_rrf_ce(query: dict) -> list[str]:
        vector_ranked = [hit_id(h) for h in store.query(query=query["query"], top_k=CE_CANDIDATES)]
        bm25_ranked = [hit.id for hit in bm25.search(query["query"], top_k=CE_CANDIDATES)]
        fused = rrf_fusion([vector_ranked, bm25_ranked])[:CE_CANDIDATES]
        candidates = [RerankCandidate(id=pid, text=doc_text.get(pid, pid)) for pid in fused]
        reranked = reranker.rerank(query["query"], candidates, use_ensemble=False)
        return [r.id for r in reranked[:TOP_K]]

    def pipeline_nlu(query: dict) -> list[str]:
        filters = conditions_to_search_filters(extract_filters(query["query"]))
        return [hit_id(h) for h in pipeline.run(query["query"], filters, top_k=TOP_K).hits]

    configs = [
        ("vector_only", vector_only),
        ("bm25_only", bm25_only),
        ("hybrid_rrf", hybrid_rrf),
        ("hybrid_rrf_ce", hybrid_rrf_ce),
        ("pipeline_nlu", pipeline_nlu),
    ]

    results = {
        "meta": {
            "queries": len(queries),
            "top_k": TOP_K,
            "rrf_k": RRF_K,
            "ce_candidates": CE_CANDIDATES,
            "ce_model": settings.cross_encoder_model,
            "bm25": "jieba 分词 + rank-bm25（server/rag/bm25_engine.py）",
            "note": "hybrid_rrf_ce 为 RRF 融合 Top-20 候选 + Cross-Encoder 纯分数精排（use_ensemble=False）",
        },
        "configs": {},
    }
    for name, fn in configs:
        outcome = evaluate_config(name, queries, fn)
        results["configs"][name] = outcome
        o = outcome["overall"]
        print(f"{name:14s} Recall@5={o['recall@5']:.3f} Recall@10={o['recall@10']:.3f} NDCG@10={o['ndcg@10']:.3f} MRR={o['mrr']:.3f}")

    out = save_json(results, "hybrid_ablation.json")
    print(f"结果已保存: {out}")


if __name__ == "__main__":
    main()
