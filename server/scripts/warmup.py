"""
启动预热脚本：预加载索引 + 执行高频 query 填充缓存。

用法：
    python -m server.scripts.warmup

或代码中调用：
    from server.scripts.warmup import warmup
    warmup(store, cache, queries=["连衣裙", "口红", "精华"])
"""

from __future__ import annotations

import time
from typing import Any


# 默认高频查询词（电商场景）
DEFAULT_WARMUP_QUERIES = [
    "连衣裙",
    "口红",
    "精华",
    "运动鞋",
    "T恤",
    "牛仔裤",
    "面膜",
    "香水",
    "包包",
    "手表",
]


def warmup(
    retriever: Any,
    cache: Any | None = None,
    queries: list[str] | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """
    预热：执行一批高频查询，填充缓存和索引。
    
    Args:
        retriever: 检索器（ProductRetrievalPipeline 或 HybridRetriever）
        cache: 缓存对象（可选）
        queries: 预热查询词列表，默认使用 DEFAULT_WARMUP_QUERIES
        top_k: 每次检索返回数量
    
    Returns:
        预热统计
    """
    queries = queries or DEFAULT_WARMUP_QUERIES
    stats = {
        "total_queries": len(queries),
        "success": 0,
        "failed": 0,
        "total_latency_ms": 0.0,
        "results_warmed": 0,
    }

    for query in queries:
        try:
            started = time.perf_counter()
            result = retriever.run(query=query, filters=None, top_k=top_k)
            latency = (time.perf_counter() - started) * 1000

            stats["success"] += 1
            stats["total_latency_ms"] += latency
            stats["results_warmed"] += len(result.hits if hasattr(result, "hits") else [])

            if cache and hasattr(cache, "set"):
                cache_key = f"warmup:{query}"
                cache.set(cache_key, result, ttl=300)

        except Exception as exc:
            stats["failed"] += 1
            print(f"[warmup] Query '{query}' failed: {exc}")

    avg_latency = stats["total_latency_ms"] / max(stats["success"], 1)
    print(f"[warmup] Done: {stats['success']}/{stats['total_queries']} succeeded, avg latency {avg_latency:.1f}ms")
    return stats


def main() -> None:
    """命令行入口。"""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

    from server.app_container import create_input_processor, create_store
    from server.config import get_settings

    settings = get_settings()
    print("[warmup] Loading settings...")

    store = create_store(settings)
    print(f"[warmup] Store ready: {store.__class__.__name__}")

    # 简单预热：直接走向量检索
    from server.rag.retrieval_pipeline import ProductRetrievalPipeline
    pipeline = ProductRetrievalPipeline(store=store)

    stats = warmup(pipeline, queries=DEFAULT_WARMUP_QUERIES)
    print(f"[warmup] Stats: {stats}")


if __name__ == "__main__":
    main()
