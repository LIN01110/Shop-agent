"""
Purpose: 纯向量余弦方式横向对比多个 sentence-transformers 模型在 ABO 中文查询集上的检索质量。
         不走 Chroma，直接 encode 后矩阵打分，适合 base vs LoRA 快速 A/B。

用法:
  HF_ENDPOINT=https://hf-mirror.com python scripts/evaluate_st_models.py \
    --models BAAI/bge-small-zh-v1.5 models/bge-small-zh-v1.5-lora-merged
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from server.rag.vector_store import load_product_documents  # noqa: E402

DEFAULT_PRODUCTS = ROOT_DIR / "data" / "benchmarks" / "abo_full" / "products.json"
DEFAULT_QUERIES = ROOT_DIR / "data" / "benchmarks" / "abo_full" / "queries.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare ST models on ABO zh title queries via direct cosine.")
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--products", default=str(DEFAULT_PRODUCTS))
    parser.add_argument("--queries", default=str(DEFAULT_QUERIES))
    parser.add_argument("--zh-only", action="store_true", default=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    from sentence_transformers import SentenceTransformer

    documents = load_product_documents(Path(args.products))
    if args.zh_only:
        documents = [
            d for d in documents
            if (d.metadata.get("attributes") or {}).get("has_zh_title")
        ]
    doc_ids = [d.id for d in documents]
    id_to_idx = {pid: i for i, pid in enumerate(doc_ids)}
    print(f"corpus={len(documents)} (zh_only={args.zh_only})", flush=True)

    queries = [json.loads(l) for l in Path(args.queries).read_text(encoding="utf-8").splitlines() if l.strip()]
    queries = [q for q in queries if q["positive_product_ids"][0] in id_to_idx]
    print(f"queries={len(queries)}", flush=True)

    results = {}
    for model_path in args.models:
        started = time.perf_counter()
        model = SentenceTransformer(model_path)
        model.max_seq_length = 96
        doc_vecs = model.encode(
            [d.text for d in documents], batch_size=args.batch_size,
            normalize_embeddings=True, show_progress_bar=False,
        )
        query_vecs = model.encode(
            [q["query"] for q in queries], batch_size=args.batch_size,
            normalize_embeddings=True, show_progress_bar=False,
        )
        doc_vecs = np.asarray(doc_vecs, dtype=np.float32)
        query_vecs = np.asarray(query_vecs, dtype=np.float32)
        scores = query_vecs @ doc_vecs.T
        top_idx = np.argpartition(-scores, args.top_k, axis=1)[:, : args.top_k]

        recall = mrr = ndcg = 0.0
        for qi, q in enumerate(queries):
            ranked = top_idx[qi][np.argsort(-scores[qi][top_idx[qi]])]
            ranked_ids = [doc_ids[i] for i in ranked]
            hit_rank = None
            for rank, pid in enumerate(ranked_ids, start=1):
                if pid in q["positive_product_ids"]:
                    hit_rank = rank
                    break
            if hit_rank is not None:
                recall += 1
                mrr += 1.0 / hit_rank
                ndcg += 1.0 / np.log2(hit_rank + 1)
        n = len(queries)
        elapsed = time.perf_counter() - started
        results[model_path] = {
            "recall_at_k": round(recall / n, 4),
            "mrr_at_k": round(mrr / n, 4),
            "ndcg_at_k": round(ndcg / n, 4),
            "eval_seconds": round(elapsed, 1),
        }
        print(f"{model_path}: {results[model_path]}", flush=True)

    report = {
        "products": str(args.products),
        "queries": str(args.queries),
        "corpus_size": len(documents),
        "query_count": len(queries),
        "top_k": args.top_k,
        "results": results,
    }
    output = args.output or str(Path(args.queries).parent / "st_model_compare.json")
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
