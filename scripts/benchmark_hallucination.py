"""
Purpose: 幻觉评测（DeepSeek 三臂对比实验）—— 适配新版三层幻觉守卫。

三组实验：
  pure_llm    — 纯 LLM 回答（无商品上下文），观察模型编造倾向
  rag         — RAG：检索商品卡片注入 Prompt（build_grounded_messages）
  rag_guard   — RAG + 三层幻觉守卫完整管线：
                检测（Layer1 商品存在性 + Layer2 属性事实）→
                数值错误自动修正 → 严重幻觉纠错 Prompt 重生成（最多 2 次）→
                重生成超限降级确定性模板

判定器：server/agent/hallucination_guard.py 的 check_product_existence +
check_attribute_facts（规则确定性判定，与 Guard 同规则集）。
"""

import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from benchmark_common import deepseek_chat, load_products, save_json

from server.agent.filters import extract_filters
from server.agent.hallucination_guard import (
    HallucinationViolation,
    check_attribute_facts,
    check_product_existence,
    guard_hallucination,
)
from server.app_container import create_commerce_fact_provider
from server.commerce.facts import configure_fact_provider
from server.config import get_settings
from server.llm.prompt import build_grounded_messages
from server.rag.vector_store import LocalJsonVectorStore
from server.tools.product_search import ProductSearchTool
from scripts.benchmark_retrieval_quality import conditions_to_search_filters

PURE_SYSTEM_PROMPT = (
    "你是一名电商导购助手。请根据用户的需求推荐具体商品，"
    "给出商品名称、品牌、价格和推荐理由，回答控制在 150 字以内。"
)

MAX_REGENERATE = 2


def build_questions(products: list[dict]) -> list[dict]:
    """每个大类生成 6 个导购问题（含预算/品牌/对比/功效型），共 24 个。"""
    by_cat: dict[str, list[dict]] = defaultdict(list)
    by_sub: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for product in products:
        by_cat[product["category"]].append(product)
        by_sub[(product["category"], product["sub_category"])].append(product)

    questions: list[dict] = []
    for category, items in sorted(by_cat.items()):
        subs = sorted({p["sub_category"] for p in items}, key=lambda s: -len(by_sub[(category, s)]))
        main_sub = subs[0]
        main_items = by_sub[(category, main_sub)]
        brands = sorted({p["brand"] for p in main_items})
        median_price = sorted(p["price"] for p in main_items)[len(main_items) // 2]
        budget = int(median_price * 1.2 // 10 * 10)
        second_sub = subs[1] if len(subs) > 1 else main_sub

        questions.extend([
            {"category": category, "question": f"给我推荐一款{main_sub}"},
            {"category": category, "question": f"{brands[0]}的{main_sub}值得买吗？多少钱"},
            {"category": category, "question": f"预算{budget}元以内，想买{main_sub}，有什么推荐"},
            {"category": category, "question": f"对比一下{main_sub}和{second_sub}，哪个更值得买"},
            {"category": category, "question": f"{main_sub}现在有什么优惠活动吗"},
            {"category": category, "question": f"销量最好的{main_sub}是哪一款"},
        ])
    return questions


def detect_violations(answer: str, cards: list[dict]) -> list[HallucinationViolation]:
    """判定器：Layer 1（商品存在性）+ Layer 2（属性事实）。"""
    return check_product_existence(answer, cards) + check_attribute_facts(answer, cards)


async def run_guard_pipeline(answer: str, cards: list[dict], question: str) -> tuple[str, list[dict]]:
    """完整 Guard 管线：检测 → 自动修正 / 纠错重生成（最多 MAX_REGENERATE 次）→ 降级模板。

    返回 (最终答案, 每轮报告摘要列表)。
    """
    chain: list[dict] = []
    current = answer
    for attempt in range(MAX_REGENERATE + 1):
        report = await guard_hallucination(
            answer=current,
            cards=cards,
            user_message=question,
            enable_sql_check=False,
            strict_mode=False,
            auto_correct=True,
            max_regenerate=MAX_REGENERATE,
            regenerate_count=attempt,
        )
        chain.append({
            "attempt": attempt,
            "action": report.action,
            "safe": report.safe,
            "violations": [
                {"layer": v.layer, "type": v.type, "product": v.product,
                 "claimed": v.claimed, "actual": v.actual, "severity": v.severity}
                for v in report.violations
            ],
        })
        if report.action == "pass":
            return current, chain
        if report.action == "auto_correct":
            return report.corrected_answer or current, chain
        if report.action == "fallback":
            return report.corrected_answer or current, chain
        if report.action == "regenerate" and report.regenerate_prompt:
            regen = deepseek_chat(
                [{"role": "user", "content": report.regenerate_prompt}],
                max_tokens=4096,
            )
            current = regen["content"]
    return current, chain


def violation_summary(records: list[dict], arm: str, key: str = "violations") -> dict:
    total = len(records)
    with_violation = sum(1 for r in records if r[arm][key])
    type_counter: dict[str, int] = defaultdict(int)
    for r in records:
        for vtype in {v["type"] for v in r[arm][key]}:
            type_counter[vtype] += 1
    return {
        "total": total,
        "with_violation": with_violation,
        "violation_rate": round(with_violation / total, 4) if total else 0.0,
        "violation_type_counts": dict(sorted(type_counter.items(), key=lambda kv: -kv[1])),
    }


def main() -> None:
    settings = get_settings()
    configure_fact_provider(create_commerce_fact_provider(settings))
    store = LocalJsonVectorStore(settings.product_data_file)
    search_tool = ProductSearchTool(store)

    questions = build_questions(load_products())
    print(f"评测问题数: {len(questions)}")

    records: list[dict] = []
    for index, item in enumerate(questions, 1):
        question = item["question"]
        filters = conditions_to_search_filters(extract_filters(question))
        cards = search_tool.run(question, filters, top_k=5)

        # 组 A：纯 LLM
        pure = deepseek_chat(
            [
                {"role": "system", "content": PURE_SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            max_tokens=4096,
        )
        pure_violations = detect_violations(pure["content"], cards)

        # 组 B：RAG（检索卡片注入）
        rag_messages = build_grounded_messages(user_message=question, cards=cards, intent="recommendation")
        rag = deepseek_chat(rag_messages, max_tokens=4096)
        rag_violations = detect_violations(rag["content"], cards)

        # 组 C：RAG + 三层幻觉守卫完整管线
        final_answer, guard_chain = asyncio.run(run_guard_pipeline(rag["content"], cards, question))
        post_guard_violations = detect_violations(final_answer, cards)

        records.append({
            "question": question,
            "category": item["category"],
            "num_cards": len(cards),
            "pure_llm": {
                "answer": pure["content"],
                "violations": [
                    {"layer": v.layer, "type": v.type, "product": v.product,
                     "claimed": v.claimed, "actual": v.actual}
                    for v in pure_violations
                ],
            },
            "rag": {
                "answer": rag["content"],
                "violations": [
                    {"layer": v.layer, "type": v.type, "product": v.product,
                     "claimed": v.claimed, "actual": v.actual}
                    for v in rag_violations
                ],
            },
            "rag_guard": {
                "guard_chain": guard_chain,
                "guard_triggered": bool(guard_chain and guard_chain[0]["violations"]),
                "final_action": guard_chain[-1]["action"] if guard_chain else "pass",
                "final_answer": final_answer,
                "post_guard_violations": [
                    {"layer": v.layer, "type": v.type, "product": v.product,
                     "claimed": v.claimed, "actual": v.actual}
                    for v in post_guard_violations
                ],
            },
        })
        first_action = guard_chain[0]["action"] if guard_chain else "pass"
        print(
            f"[{index}/{len(questions)}] {question[:20]}… "
            f"pure={len(pure_violations)} rag={len(rag_violations)} "
            f"guard={first_action}→{guard_chain[-1]['action']} post={len(post_guard_violations)}"
        )

    total = len(records)
    action_counter: dict[str, int] = defaultdict(int)
    for r in records:
        action_counter[r["rag_guard"]["final_action"]] += 1

    summary = {
        "pure_llm": violation_summary(records, "pure_llm"),
        "rag": violation_summary(records, "rag"),
        "rag_guard": {
            "guard_trigger_count": sum(1 for r in records if r["rag_guard"]["guard_triggered"]),
            "guard_trigger_rate": round(sum(1 for r in records if r["rag_guard"]["guard_triggered"]) / total, 4) if total else 0.0,
            "final_action_counts": dict(sorted(action_counter.items(), key=lambda kv: -kv[1])),
            "post_guard": violation_summary(records, "rag_guard", "post_guard_violations"),
        },
    }

    print("\n=== 幻觉评测汇总 ===")
    print(f"纯 LLM 违规率:        {summary['pure_llm']['violation_rate']:.1%}")
    print(f"RAG 违规率:           {summary['rag']['violation_rate']:.1%}")
    print(f"Guard 触发率:         {summary['rag_guard']['guard_trigger_rate']:.1%}")
    print(f"Guard 处置分布:       {summary['rag_guard']['final_action_counts']}")
    print(f"Guard 后最终违规率:   {summary['rag_guard']['post_guard']['violation_rate']:.1%}")

    out = save_json(
        {
            "meta": {
                "judge": "hallucination_guard.py Layer1+Layer2（规则确定性判定，与 Guard 同规则集）",
                "arms": ["pure_llm", "rag", "rag_guard"],
                "guard_pipeline": "检测 → 自动修正 → 纠错重生成(≤2) → 确定性模板降级",
                "llm": "DeepSeek（非流式）",
            },
            "summary": summary,
            "records": records,
        },
        "hallucination.json",
    )
    print(f"结果已保存: {out}")


if __name__ == "__main__":
    main()
