"""
Purpose: 规则信号生成：build_rule_signals 分析消息特征，决定走确定性规则、LLM planner 还是澄清路径。
"""

from dataclasses import dataclass
from typing import Literal

from server.agent.scenario_matching import has_explicit_bundle_request
from server.agent.semantic_schema import SemanticPlan
from server.session.state import SessionState


RouteKind = Literal["deterministic", "planner", "clarification"]


@dataclass(frozen=True)
class RuleSignals:
    route: RouteKind
    reasons: tuple[str, ...]
    categories: tuple[str, ...]
    product_types: tuple[str, ...]
    has_cart_action: bool
    has_reference: bool
    has_bundle_signal: bool
    has_complex_preference: bool
    has_ambiguous_budget: bool
    is_simple_product_search: bool


def build_rule_signals(message: str, fallback: SemanticPlan, session: SessionState) -> RuleSignals:
    categories = tuple(sorted(category_values(fallback)))
    product_types = tuple(sorted(product_type_values(fallback)))
    has_cart_action = fallback.intent == "cart" or fallback.cart_action != "none"
    has_reference = fallback.reference_type != "none" or (
        bool(session.candidate_product_cards) and has_reference_language(message)
    )
    has_bundle_signal = fallback.intent == "bundle" or has_explicit_bundle_request(message)
    has_complex_preference = has_complex_preference_signal(fallback, message)
    has_ambiguous_budget = has_ambiguous_budget_signal(message)
    is_simple_product_search = (
        fallback.intent == "recommend"
        and (len(product_types) == 1 or len(categories) == 1)
        and not has_reference
        and not has_complex_preference
        and not has_ambiguous_budget
    )

    route, reasons = choose_route(
        fallback=fallback,
        has_cart_action=has_cart_action,
        has_reference=has_reference,
        has_bundle_signal=has_bundle_signal,
        has_complex_preference=has_complex_preference,
        has_ambiguous_budget=has_ambiguous_budget,
        is_simple_product_search=is_simple_product_search,
    )
    return RuleSignals(
        route=route,
        reasons=reasons,
        categories=categories,
        product_types=product_types,
        has_cart_action=has_cart_action,
        has_reference=has_reference,
        has_bundle_signal=has_bundle_signal,
        has_complex_preference=has_complex_preference,
        has_ambiguous_budget=has_ambiguous_budget,
        is_simple_product_search=is_simple_product_search,
    )


def choose_route(
    *,
    fallback: SemanticPlan,
    has_cart_action: bool,
    has_reference: bool,
    has_bundle_signal: bool,
    has_complex_preference: bool,
    has_ambiguous_budget: bool,
    is_simple_product_search: bool,
) -> tuple[RouteKind, tuple[str, ...]]:
    reasons: list[str] = []
    if has_cart_action:
        return "planner", ("cart_action",)
    if fallback.intent == "compare":
        return "planner", ("compare_intent",)
    if has_reference:
        return "planner", ("context_reference",)
    if fallback.intent == "clarify":
        return "clarification", ("rule_clarification",)
    if fallback.intent == "bundle":
        return "deterministic", ("catalog_bundle",)
    if is_simple_product_search:
        return "deterministic", ("simple_product_search",)
    if has_bundle_signal:
        reasons.append("bundle_signal")
    if has_complex_preference:
        reasons.append("complex_preference")
    if has_ambiguous_budget:
        reasons.append("ambiguous_budget")
    return "planner", tuple(reasons or ["not_certainly_simple"])


def product_type_values(plan: SemanticPlan) -> set[str]:
    values = {item.value for item in plan.filters if item.kind == "product_type"}
    values.update(
        item.value
        for item in plan.constraints
        if item.mode == "must" and item.field == "product_type" and item.value
    )
    return {value for value in values if value}


def category_values(plan: SemanticPlan) -> set[str]:
    values = {item.value for item in plan.filters if item.kind == "category"}
    values.update(
        item.value
        for item in plan.constraints
        if item.mode == "must" and item.field == "category" and item.value
    )
    return {value for value in values if value}


def has_reference_language(message: str) -> bool:
    return any(
        term in message
        for term in [
            "刚才",
            "上一个",
            "这个",
            "那款",
            "那个",
            "它",
            "第二个",
            "第一个",
            "第三个",
        ]
    )


def has_complex_preference_signal(plan: SemanticPlan, message: str) -> bool:
    if any(
        item.kind in {"exclude", "exclude_brand", "exclude_category", "exclude_product_type", "preferred_brand"}
        for item in plan.filters
    ):
        return True
    if any(item.mode in {"must_not", "should"} for item in plan.constraints):
        return True
    return any(
        term in message
        for term in [
            "不要",
            "排除",
            "别太",
            "更适合",
            "偏",
            "倾向",
            "优先",
            "看起来",
            "风格",
            "别",
        ]
    )


def has_ambiguous_budget_signal(message: str) -> bool:
    return any(
        term in message
        for term in [
            "别太贵",
            "不要太贵",
            "价位压低",
            "预算别爆",
            "学生党",
            "性价比",
            "左右",
            "出头",
            "多",
        ]
    )
