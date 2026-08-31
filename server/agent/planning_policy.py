"""
Purpose: 规划策略校验：PlannerPolicy 校验 LLM plan 的合理性，购物车写操作缺少证据时改为澄清。
"""

from server.agent.scenario_matching import has_explicit_bundle_request, looks_like_single_product_request
from server.agent.rule_signals import build_rule_signals, product_type_values
from server.agent.semantic_schema import SemanticPlan
from server.session.state import SessionState


MUTATING_CART_ACTIONS = {"add", "remove", "update_quantity", "checkout"}


def should_try_llm_planning(*, message: str, fallback: SemanticPlan, session: SessionState) -> bool:
    signals = build_rule_signals(message, fallback, session)
    return signals.route != "deterministic"


def validate_semantic_plan(
    *,
    message: str,
    session: SessionState,
    fallback: SemanticPlan,
    candidate: SemanticPlan,
) -> SemanticPlan:
    """Apply deterministic business policy to an LLM/rule semantic plan.

    The planner may generalize, but routing must stay product-safe: a single
    product request should not be promoted to a bundle just because it mentions
    a term that also appears in a scenario catalog.
    """
    plan = candidate
    if should_block_unsafe_mutating_cart_action(session=session, fallback=fallback, candidate=plan):
        plan = plan.model_copy(
            update={
                "intent": "clarify",
                "cart_action": "none",
                "needs_search": False,
                "query": plan.query or fallback.query or message,
                "filters": merge_plan_filters(fallback, plan),
                "constraints": merge_plan_constraints(fallback, plan),
                "confidence": min(plan.confidence, 0.49),
            }
        )

    if should_force_single_product_recommendation(message=message, fallback=fallback, candidate=plan):
        plan = plan.model_copy(
            update={
                "intent": "recommend",
                "cart_action": "none",
                "needs_search": True,
                "query": plan.query or fallback.query or message,
                "filters": merge_plan_filters(fallback, plan),
                "constraints": merge_plan_constraints(fallback, plan),
                "confidence": min(plan.confidence, 0.79),
            }
        )

    if plan.intent == "bundle" and not bundle_intent_is_allowed(message=message, fallback=fallback, candidate=plan):
        plan = plan.model_copy(
            update={
                "intent": fallback.intent if fallback.intent != "bundle" else "recommend",
                "needs_search": True,
                "query": plan.query or fallback.query or message,
                "filters": merge_plan_filters(fallback, plan),
                "constraints": merge_plan_constraints(fallback, plan),
                "confidence": min(plan.confidence, 0.69),
            }
        )

    return plan


def should_block_unsafe_mutating_cart_action(
    *,
    session: SessionState,
    fallback: SemanticPlan,
    candidate: SemanticPlan,
) -> bool:
    """Require deterministic evidence before a planner may mutate the cart.

    LLM planning may generalize phrasing, but write operations must not be
    inferred from open-ended shopping language such as "帮我买一个好用的". The
    rule/fallback layer already captures the cart phrases and references we
    can execute safely; otherwise the turn should clarify before mutating
    state.
    """
    action = candidate.cart_action
    if action not in MUTATING_CART_ACTIONS:
        return False
    if session.pending_cart_action:
        return False
    if candidate.reference_type == "none" and action in {"add", "remove", "update_quantity"}:
        return True
    if candidate.confidence_by_field:
        cart_confidence = candidate.confidence_by_field.get("cart_action", 1.0)
        reference_confidence = candidate.confidence_by_field.get("reference", 1.0)
        if cart_confidence < 0.75 or reference_confidence < 0.65:
            return True
    return fallback.intent != "cart" and fallback.cart_action == "none"


def should_force_single_product_recommendation(
    *,
    message: str,
    fallback: SemanticPlan,
    candidate: SemanticPlan,
) -> bool:
    if candidate.intent != "bundle":
        return False
    if fallback.intent in {"cart", "compare", "ask_product_detail"}:
        return False
    if looks_like_single_product_request(message):
        return True
    if not has_explicit_bundle_request(message) and has_single_product_constraint(fallback):
        return True
    return False


def bundle_intent_is_allowed(*, message: str, fallback: SemanticPlan, candidate: SemanticPlan) -> bool:
    if fallback.intent == "bundle":
        return True
    if has_explicit_bundle_request(message):
        return True
    if has_multiple_product_type_constraints(candidate):
        return True
    return False


def has_single_product_constraint(plan: SemanticPlan) -> bool:
    return len(product_type_values(plan)) == 1


def has_multiple_product_type_constraints(plan: SemanticPlan) -> bool:
    return len(product_type_values(plan)) >= 2


def merge_plan_filters(fallback: SemanticPlan, candidate: SemanticPlan):
    from server.agent.semantic_rules import dedupe_semantic_filters

    return dedupe_semantic_filters([*fallback.filters, *candidate.filters])


def merge_plan_constraints(fallback: SemanticPlan, candidate: SemanticPlan):
    from server.agent.semantic_rules import dedupe_semantic_constraints

    return dedupe_semantic_constraints([*fallback.constraints, *candidate.constraints])
