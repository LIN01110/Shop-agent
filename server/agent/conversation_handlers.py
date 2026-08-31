"""
答辩重点 🟠（澄清/追问 Handler）
任务：Day 1 任务 1.7 — 理解 ClarificationHandler 与 ContextFollowUpHandler。
核心：ClarificationHandler：plan.intent == "clarify" 或判断宽泛 → 生成追问问题。
      ContextFollowUpHandler：plan.intent == "ask_product_detail" + select_contextual_product() 解析引用
      → 取引用商品 → build_contextual_product_answer() → yield token + product_card + done。
高频追问：
  - “澄清和上下文追问的区别与触发条件？”
    → 澄清是需求太宽泛先问范围；追问是已锁定上一轮商品再问细节
"""

from collections.abc import AsyncIterator

from server.agent.clarification import build_clarification_question, get_clarification_subject
from server.agent.context import (
    build_contextual_product_answer,
    is_cheaper_follow_up,
    select_contextual_product,
)
from server.agent.responses import build_done_payload, stream_text
from server.agent.workflow import AgentTurnContext


class ClarificationHandler:
    def matches(self, context: AgentTurnContext) -> bool:
        return context.plan.intent == "clarify" or bool(get_clarification_subject(context.message, context.session))

    async def handle(self, context: AgentTurnContext) -> AsyncIterator[dict]:
        answer = build_clarification_question(context.message, context.session)
        if not answer:
            answer = build_plan_clarification_question(context)
        async for item in stream_text(answer):
            yield item
        context.session.add_assistant_message(answer)
        yield {
            "event": "done",
            "data": build_done_payload(
                context.session_id,
                context.session,
                needs_clarification=True,
                plan=context.plan,
                trace_id=context.trace_id,
            ),
        }


def build_plan_clarification_question(context: AgentTurnContext) -> str:
    if context.session.pending_cart_action:
        return "我还需要你确认刚才的购物车操作。你可以回复“确认”“取消”，或说明具体是哪一款。"

    if context.session.candidate_product_cards:
        options = "；".join(
            f"{index + 1}. {card.get('brand', '')} {card.get('name', '')}".strip()
            for index, card in enumerate(context.session.candidate_product_cards[:5])
        )
        return f"我需要先确认你指的是哪一款：{options}。你可以回复“第一款”“第二款”，或补充品牌/预算。"

    return "我需要先确认一下：你想买哪类商品、预算大概多少，以及有没有品牌或使用场景偏好？"


class ContextFollowUpHandler:
    def matches(self, context: AgentTurnContext) -> bool:
        return (
            context.plan.intent == "ask_product_detail"
            and select_contextual_product(context.message, context.session.candidate_product_cards, context.plan)
            is not None
            and not is_cheaper_follow_up(context.message)
        )

    async def handle(self, context: AgentTurnContext) -> AsyncIterator[dict]:
        referenced_card = select_contextual_product(
            context.message,
            context.session.candidate_product_cards,
            context.plan,
        )
        if referenced_card is None:
            return

        answer = build_contextual_product_answer(context.message, referenced_card)
        async for item in stream_text(answer):
            yield item
        context.session.add_assistant_message(answer)
        context.session.candidate_products = [referenced_card["id"]]
        context.session.candidate_product_cards = [referenced_card]
        yield {"event": "product_card", "data": referenced_card}
        yield {
            "event": "done",
            "data": build_done_payload(
                context.session_id,
                context.session,
                needs_clarification=False,
                plan=context.plan,
                trace_id=context.trace_id,
            ),
        }
