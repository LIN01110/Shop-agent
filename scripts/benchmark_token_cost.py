"""
Purpose: Token 成本对比实验 —— 规则路由（生产路径）vs 无路由基线（每问必检索+LLM生成）。

双臂：
  routed    — 生产路径：orchestrator 规则路由，只有需要生成的请求才调用 LLM
              （经 ArkChatClient 流式调用，usage 由 stream_options.include_usage 捕获进 USAGE_LOG）
  unrouted  — 无路由基线：每个问题一律检索 + build_grounded_messages + LLM 生成
              （与幻觉评测 RAG 臂同构，usage 由非流式响应直接返回）

问题集：与幻觉评测相同的 24 个导购问题（build_questions）。
输出：results/token_cost.json + 控制台汇总。
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from benchmark_common import deepseek_chat, load_products, save_json

from server.agent.filters import extract_filters
from server.agent.orchestrator import get_orchestrator
from server.app_container import create_commerce_fact_provider
from server.commerce.facts import configure_fact_provider
from server.config import get_settings
from server.llm import ark_client
from server.llm.prompt import build_grounded_messages
from server.rag.vector_store import LocalJsonVectorStore
from server.tools.product_search import ProductSearchTool
from scripts.benchmark_hallucination import build_questions
from scripts.benchmark_retrieval_quality import conditions_to_search_filters


def sum_usage(records: list[dict]) -> dict:
    return {
        "calls": len(records),
        "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in records),
        "completion_tokens": sum(r.get("completion_tokens", 0) for r in records),
        "total_tokens": sum(r.get("total_tokens", 0) for r in records),
    }


async def run_routed_arm(questions: list[dict]) -> dict:
    """生产路径：orchestrator 规则路由，逐问独立会话。"""
    orchestrator = get_orchestrator()
    per_question: list[dict] = []
    for index, item in enumerate(questions, 1):
        question = item["question"]
        ark_client.USAGE_LOG.clear()
        session_id = f"token-cost-{index}"
        text_parts: list[str] = []
        async for event in orchestrator.stream_chat(session_id=session_id, user_message=question):
            if event.get("event") == "token":
                text_parts.append(event["data"]["text"])
        usage = sum_usage(list(ark_client.USAGE_LOG))
        per_question.append({
            "question": question,
            "llm_calls": usage["calls"],
            **usage,
            "answer_chars": len("".join(text_parts)),
        })
        print(f"[routed {index}/{len(questions)}] {question[:18]}… calls={usage['calls']} total={usage['total_tokens']}")
    return {"per_question": per_question, "totals": {
        "calls": sum(q["llm_calls"] for q in per_question),
        "prompt_tokens": sum(q["prompt_tokens"] for q in per_question),
        "completion_tokens": sum(q["completion_tokens"] for q in per_question),
        "total_tokens": sum(q["total_tokens"] for q in per_question),
    }}


def run_unrouted_arm(questions: list[dict], search_tool: ProductSearchTool) -> dict:
    """无路由基线：每问必检索 + LLM 生成（与幻觉评测 RAG 臂同构）。"""
    per_question: list[dict] = []
    for index, item in enumerate(questions, 1):
        question = item["question"]
        filters = conditions_to_search_filters(extract_filters(question))
        cards = search_tool.run(question, filters, top_k=5)
        messages = build_grounded_messages(user_message=question, cards=cards, intent="recommendation")
        resp = deepseek_chat(messages, max_tokens=4096)
        usage = resp.get("usage") or {}
        per_question.append({
            "question": question,
            "llm_calls": 1,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "answer_chars": len(resp.get("content", "")),
        })
        print(f"[unrouted {index}/{len(questions)}] {question[:18]}… total={usage.get('total_tokens', 0)}")
    return {"per_question": per_question, "totals": {
        "calls": sum(q["llm_calls"] for q in per_question),
        "prompt_tokens": sum(q["prompt_tokens"] for q in per_question),
        "completion_tokens": sum(q["completion_tokens"] for q in per_question),
        "total_tokens": sum(q["total_tokens"] for q in per_question),
    }}


def main() -> None:
    arm = sys.argv[1] if len(sys.argv) > 1 else "both"
    settings = get_settings()
    configure_fact_provider(create_commerce_fact_provider(settings))
    store = LocalJsonVectorStore(settings.product_data_file)
    search_tool = ProductSearchTool(store)

    questions = build_questions(load_products())
    print(f"评测问题数: {len(questions)}，臂: {arm}")

    if arm in ("routed", "both"):
        routed = asyncio.run(run_routed_arm(questions))
        save_json({"routed": routed}, "token_cost_routed.json")
    if arm in ("unrouted", "both"):
        unrouted = run_unrouted_arm(questions, search_tool)
        save_json({"unrouted": unrouted}, "token_cost_unrouted.json")
    if arm == "both":
        merge_summary()


def merge_summary() -> None:
    results_dir = ROOT_DIR / "results"
    routed = json.loads((results_dir / "token_cost_routed.json").read_text(encoding="utf-8"))["routed"]
    unrouted = json.loads((results_dir / "token_cost_unrouted.json").read_text(encoding="utf-8"))["unrouted"]
    r, u = routed["totals"], unrouted["totals"]
    summary = {
        "questions": len(routed["per_question"]),
        "routed": r,
        "unrouted": u,
        "call_reduction_rate": round(1 - r["calls"] / u["calls"], 4) if u["calls"] else None,
        "token_reduction_rate": round(1 - r["total_tokens"] / u["total_tokens"], 4) if u["total_tokens"] else None,
        "prompt_token_reduction_rate": round(1 - r["prompt_tokens"] / u["prompt_tokens"], 4) if u["prompt_tokens"] else None,
    }
    save_json({"summary": summary, "routed": routed, "unrouted": unrouted}, "token_cost.json")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
