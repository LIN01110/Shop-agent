"""
Purpose: 延迟评测：检索管道延迟（进程内）、/chat 端到端延迟（HTTP SSE）、DeepSeek 生成延迟。

三部分独立报告：
  1. retrieval   — ProductRetrievalPipeline.run() 全量评测查询 ×3 轮，总延迟 + 各阶段耗时分解
  2. http_chat   — POST /chat（SSE）首事件时间(TTFB)与完整流时间；需服务已启动
  3. deepseek    — DeepSeek API 非流式调用延迟（10 次）
"""

import json
import sys
import time
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from benchmark_common import EVAL_DIR, deepseek_chat, percentile, save_json

from server.agent.filters import extract_filters
from server.app_container import create_commerce_fact_provider
from server.commerce.facts import configure_fact_provider
from server.config import get_settings
from server.rag.retrieval_pipeline import ProductRetrievalPipeline
from server.rag.vector_store import LocalJsonVectorStore
from scripts.benchmark_retrieval_quality import conditions_to_search_filters

HTTP_BASE = "http://localhost:8000"
HTTP_MESSAGES = [
    "跑步鞋推荐",
    "雅诗兰黛的精华怎么样",
    "我想买一副降噪蓝牙耳机",
    "100元以内的面膜有什么推荐",
    "提神咖啡推荐",
    "智能手机有什么值得买的",
]
HTTP_REPEATS = 5


def bench_retrieval(queries: list[dict]) -> dict:
    settings = get_settings()
    configure_fact_provider(create_commerce_fact_provider(settings))
    store = LocalJsonVectorStore(settings.product_data_file)
    pipeline = ProductRetrievalPipeline(store)

    totals: list[float] = []
    stage_times: dict[str, list[float]] = {}
    for _ in range(3):
        for query in queries:
            filters = conditions_to_search_filters(extract_filters(query["query"]))
            started = time.perf_counter()
            result = pipeline.run(query["query"], filters, top_k=10)
            totals.append((time.perf_counter() - started) * 1000)
            for step in result.diagnostics.steps:
                stage_times.setdefault(step.name, []).append(step.elapsed_ms)

    return {
        "runs": f"{len(queries)} 查询 × 3 轮 = {len(totals)} 次",
        "total_ms": {
            "p50": percentile(totals, 50),
            "p90": percentile(totals, 90),
            "p99": percentile(totals, 99),
            "mean": round(sum(totals) / len(totals), 2),
        },
        "stages_mean_ms": {
            name: round(sum(values) / len(values), 3) for name, values in sorted(stage_times.items())
        },
    }


def bench_http_chat() -> dict:
    ttfb_list: list[float] = []
    total_list: list[float] = []
    errors = 0
    for _ in range(HTTP_REPEATS):
        for message in HTTP_MESSAGES:
            started = time.perf_counter()
            try:
                with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
                    with client.stream(
                        "POST",
                        f"{HTTP_BASE}/chat",
                        json={"message": message, "session_id": "benchmark_latency"},
                    ) as response:
                        first = True
                        for _chunk in response.iter_bytes():
                            if first:
                                ttfb_list.append((time.perf_counter() - started) * 1000)
                                first = False
                        if first:  # 空流
                            errors += 1
                total_list.append((time.perf_counter() - started) * 1000)
            except Exception:  # noqa: BLE001
                errors += 1

    return {
        "runs": f"{len(HTTP_MESSAGES)} 消息 × {HTTP_REPEATS} 轮",
        "errors": errors,
        "ttfb_ms": {
            "p50": percentile(ttfb_list, 50),
            "p90": percentile(ttfb_list, 90),
            "p99": percentile(ttfb_list, 99),
        },
        "total_ms": {
            "p50": percentile(total_list, 50),
            "p90": percentile(total_list, 90),
            "p99": percentile(total_list, 99),
        },
    }


def bench_deepseek() -> dict:
    latencies: list[float] = []
    tokens: list[int] = []
    for index in range(10):
        result = deepseek_chat(
            [
                {"role": "system", "content": "你是电商导购助手，回答简洁。"},
                {"role": "user", "content": f"用 50 字以内介绍第 {index + 1} 款适合学生的跑步鞋。"},
            ],
            max_tokens=256,
        )
        latencies.append(result["latency_ms"])
        tokens.append(int(result["usage"].get("completion_tokens", 0)))
    return {
        "runs": 10,
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p90": percentile(latencies, 90),
            "min": round(min(latencies), 1),
            "max": round(max(latencies), 1),
        },
        "completion_tokens_mean": round(sum(tokens) / len(tokens), 1),
    }


def main() -> None:
    queries_path = EVAL_DIR / "retrieval_queries.jsonl"
    queries = [json.loads(line) for line in queries_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    print("[1/3] 检索管道延迟（进程内）...")
    retrieval = bench_retrieval(queries)
    print(f"      总延迟 P50={retrieval['total_ms']['p50']}ms P99={retrieval['total_ms']['p99']}ms")

    print("[2/3] /chat 端到端延迟（HTTP SSE）...")
    try:
        with httpx.Client(timeout=httpx.Timeout(5.0)) as client:
            client.get(f"{HTTP_BASE}/health")
        http_chat = bench_http_chat()
        print(f"      TTFB P50={http_chat['ttfb_ms']['p50']}ms 完整流 P50={http_chat['total_ms']['p50']}ms")
    except Exception:  # noqa: BLE001
        http_chat = {"skipped": "服务未启动，跳过 HTTP 测试（请先启动 uvicorn）"}
        print("      服务未启动，跳过")

    print("[3/3] DeepSeek 生成延迟...")
    deepseek = bench_deepseek()
    print(f"      延迟 P50={deepseek['latency_ms']['p50']}ms")

    out = save_json(
        {
            "meta": {"http_base": HTTP_BASE, "note": "HTTP 测试结果取决于服务当前配置（USE_LLM 开关）"},
            "retrieval": retrieval,
            "http_chat": http_chat,
            "deepseek": deepseek,
        },
        "latency.json",
    )
    print(f"结果已保存: {out}")


if __name__ == "__main__":
    main()
