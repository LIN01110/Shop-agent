"""
答辩重点 🔴（语义规划）
任务：Day 1 任务 1.6 — 理解“规则+LLM+校验”三层。
核心：SemanticPlanner.plan() 双层设计：
      1) build_rule_plan() 快速规则 fallback
      2) should_try_llm_planning() 判断是否需要 LLM
      3) _try_llm_plan() 短预算内（默认 0.8s）请求 LLM JSON plan
      4) merge_semantic_plans() LLM plan + fallback 合并
      5) validate_semantic_plan() Pydantic 校验 + PlannerPolicy 策略校验
高频追问：
  - “为什么不用纯 LLM 做规划？” → 延迟不可控 + 安全风险（如直接输出购物车操作）
  - “为什么不用纯规则？” → 覆盖不了长尾表达（“适合熬夜党用的”）
  - “合并逻辑是什么？” → confidence < 0.5 全用 fallback；cart/compare/bundle 优先规则；filters 去重合并
  - “LLM 超时怎么办？” → asyncio.timeout + 异常捕获 → 自动 fallback
"""

import json
import asyncio

from pydantic import ValidationError

from server.agent.planning_policy import should_try_llm_planning, validate_semantic_plan
from server.agent.planning_context import (
    format_candidate_context,
    format_cart_context,
    format_recent_turns,
    load_planning_context,
)
from server.agent.semantic_rules import build_rule_plan, dedupe_semantic_constraints, dedupe_semantic_filters
from server.agent.semantic_schema import SemanticPlan
from server.session.state import SessionState


class SemanticPlanner:
    def __init__(self, llm_client: object | None = None, *, timeout_seconds: float = 0.8) -> None:
        self.llm_client = llm_client
        self.timeout_seconds = timeout_seconds

    async def plan(self, message: str, session: SessionState) -> SemanticPlan:
        # SemanticPlanner.plan() 双层设计 5 步：
        # 1) build_rule_plan：快速规则 fallback（延迟低、确定性高）
        # 2) should_try_llm_planning：判断是否需要 LLM（如长尾表达）
        # 3) _try_llm_plan：短预算内请求 LLM JSON plan
        # 4) merge_semantic_plans：规则 + LLM 去重合并
        # 5) validate_semantic_plan：Pydantic + PlannerPolicy 校验
        fallback = build_rule_plan(message, session)
        if not should_try_llm_planning(message=message, fallback=fallback, session=session):
            return fallback
        llm_plan = await self._try_llm_plan(message, session)
        if llm_plan is None:
            return fallback  # LLM 失败/超时自动回退规则结果
        merged = merge_semantic_plans(fallback=fallback, llm_plan=llm_plan)
        return validate_semantic_plan(
            message=message,
            session=session,
            fallback=fallback,
            candidate=merged,
        )

    async def _try_llm_plan(self, message: str, session: SessionState) -> SemanticPlan | None:
        # 防御：没有 LLM 客户端或超时设为 0 时直接返回 None，走规则 fallback
        if self.llm_client is None or not hasattr(self.llm_client, "stream_messages"):
            return None
        if self.timeout_seconds <= 0:
            return None

        try:
            chunks = await self._collect_llm_plan_tokens(message, session)
            payload = extract_json_object("".join(chunks))
            if payload is None:
                return None
            return SemanticPlan.model_validate(payload)
        except (RuntimeError, ValueError, TypeError, TimeoutError, ValidationError, json.JSONDecodeError):
            # LLM 超时或返回非法 JSON：静默 fallback，不影响主链路
            return None

    async def _collect_llm_plan_tokens(self, message: str, session: SessionState) -> list[str]:
        client = self.llm_client
        chunks: list[str] = []
        # 短预算控制：默认 0.8s，避免规划阶段拖慢首 token
        async with asyncio.timeout(self.timeout_seconds):
            from server.config import get_settings
            settings = get_settings()
            max_tokens = settings.llm_planning_max_tokens if settings.llm_planning_max_tokens > 0 else None
            async for token in client.stream_messages(
                build_semantic_plan_messages(message, session),
                max_tokens=max_tokens,
            ):  # type: ignore[attr-defined]
                chunks.append(token)
        return chunks


def build_semantic_plan_messages(message: str, session: SessionState) -> list[dict]:
    context = load_planning_context(session)
    # 精简 schema：只保留核心字段，去掉冗长注释
    schema_hint = {
        "intent": "recommend|compare|cart|ask_product_detail|clarify|browse|bundle",
        "cart_action": "none|add|remove|update_quantity|view|checkout",
        "reference_type": "none|last|ordinal|name|brand|cheapest",
        "reference_index": "integer or null",
        "reference_text": "string",
        "quantity": "integer or null",
        "query": "search query in Chinese",
        "presentation_mode": "auto|single|listing",
        "filters": [{"kind": "keyword|max_price|min_price|brand|category|product_type|in_stock|facet|unsupported_service", "value": "string"}],
        "constraints": [{"mode": "must|must_not|should", "field": "string", "operator": "eq|contains|lte|gte", "value": "string"}],
        "needs_search": "boolean",
        "confidence": "0-1",
    }
    return [
        {
            "role": "system",
            "content": (
                "你是电商导购 Agent 的语义解析器，只输出 JSON。"
                "不要输出解释、Markdown 或多余文本。"
                "不得编造商品事实、价格、库存或优惠。"
                "发票/优惠券/售后政策如无证据，输出 unsupported_service。"
                "单品推荐用 recommend，组合方案用 bundle，信息不足用 clarify。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Schema：{json.dumps(schema_hint, ensure_ascii=False)}\n\n"
                f"最近对话：\n{format_recent_turns(context)}\n\n"
                f"会话摘要：{context.history_summary or '无'}\n\n"
                f"用户偏好：{json.dumps(context.user_profile, ensure_ascii=False)}\n\n"
                f"候选商品：\n{format_candidate_context(context)}\n\n"
                f"购物车：{format_cart_context(context)}\n\n"
                f"当前输入：{message}\n\n"
                "示例：\"推荐一款防晒霜\"=>intent=recommend,presentation_mode=single"
                "；\"去三亚配一套防晒到穿搭\"=>intent=bundle"
                "；facet:16GB内存=>memory:16GB,42码=>shoe_size:42"
                "\n请输出 JSON。"
            ),
        },
    ]


def merge_semantic_plans(fallback: SemanticPlan, llm_plan: SemanticPlan) -> SemanticPlan:
    """合并规则 fallback 与 LLM plan：LLM confidence 低时全用规则；
    cart/compare/bundle 等敏感意图优先规则；filters/constraints 去重合并。"""
    if llm_plan.confidence < 0.5:
        return fallback

    merged_filters = dedupe_semantic_filters([*fallback.filters, *llm_plan.filters])
    merged_constraints = dedupe_semantic_constraints([*fallback.constraints, *llm_plan.constraints])
    # 意图合并：默认取 LLM，但敏感动作以规则为准
    intent = llm_plan.intent or fallback.intent
    if fallback.intent in {"cart", "compare", "bundle"}:
        intent = fallback.intent
    # browse 与 recommend 的消歧：规则侧有 product_type 时优先 recommend
    if llm_plan.intent == "browse" and (
        fallback.intent == "recommend"
        or any(item.kind == "product_type" for item in fallback.filters)
    ):
        intent = fallback.intent
    presentation_mode = (
        llm_plan.presentation_mode
        if llm_plan.presentation_mode != "auto"
        else fallback.presentation_mode
    )

    return llm_plan.model_copy(
        update={
            "intent": intent,
            "cart_action": llm_plan.cart_action if llm_plan.cart_action != "none" else fallback.cart_action,
            "reference_type": llm_plan.reference_type
            if llm_plan.reference_type != "none"
            else fallback.reference_type,
            "reference_index": llm_plan.reference_index or fallback.reference_index,
            "reference_text": llm_plan.reference_text or fallback.reference_text,
            "quantity": llm_plan.quantity or fallback.quantity,
            "query": llm_plan.query or fallback.query,
            "presentation_mode": presentation_mode,
            "query_understanding": fallback.query_understanding or llm_plan.query_understanding,
            "filters": merged_filters,
            "constraints": merged_constraints,
            "confidence_by_field": merge_confidence_by_field(fallback, llm_plan),
            "evidence": merge_plan_evidence(fallback, llm_plan),
        }
    )


def merge_confidence_by_field(fallback: SemanticPlan, llm_plan: SemanticPlan) -> dict[str, float]:
    merged = dict(fallback.confidence_by_field)
    for field, value in llm_plan.confidence_by_field.items():
        current = merged.get(field, 0.0)
        merged[field] = max(float(current), float(value))
    if llm_plan.confidence:
        merged["llm_plan"] = round(float(llm_plan.confidence), 4)
    return merged


def merge_plan_evidence(fallback: SemanticPlan, llm_plan: SemanticPlan) -> dict:
    merged = dict(fallback.evidence)
    if llm_plan.evidence:
        merged["llm"] = llm_plan.evidence
    if llm_plan.confidence:
        merged["llm_plan"] = {"confidence": round(float(llm_plan.confidence), 4)}
    return merged


def extract_json_object(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start >= 0:
        try:
            payload, _ = decoder.raw_decode(text[start:])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        break
    return None
