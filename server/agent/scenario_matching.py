"""
Purpose: 场景匹配逻辑：计算场景与查询的匹配分数，识别显式组合请求（搭配一套/推荐一套）。
"""

from server.agent.scenario_models import ScenarioBundleConfig
from server.agent.semantic_schema import SemanticPlan
from server.rag.post_process import SearchFilters


BUNDLE_REQUEST_TERMS = (
    "搭配一套",
    "搭一套",
    "配一套",
    "推荐一套",
    "一整套",
    "全套",
    "一套方案",
    "套方案",
    "组合推荐",
    "推荐组合",
    "组合方案",
    "购买方案",
    "清单",
    "购物清单",
    "采购清单",
    "装备清单",
    "一起买",
    "一起搭",
)
DEFAULT_BUNDLE_REQUEST_TERMS = (
    "搭配一套",
    "搭一套",
    "配一套",
    "推荐一套",
    "一整套",
    "全套",
    "一套方案",
    "套方案",
    "组合方案",
    "购买方案",
    "清单",
    "购物清单",
    "采购清单",
    "装备清单",
)
GENERIC_CROSS_CATEGORY_TERMS = frozenset(
    {
        "搭配",
        "一套",
        "一整套",
        "全套",
        "套装",
        "组合",
        "方案",
        "清单",
        "配置",
        "一起",
        "组合推荐",
        "推荐组合",
        "组合方案",
        "购买方案",
    }
)
SINGLE_PRODUCT_REQUEST_PATTERN_TERMS = (
    "一款",
    "一个",
    "一件",
    "一双",
    "一瓶",
    "一支",
    "一盒",
    "一包",
)


def score_bundle(
    bundle: ScenarioBundleConfig,
    message: str,
    *,
    plan: SemanticPlan | None,
    filters: SearchFilters | None,
) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    score = float(bundle.priority) * 0.15
    matched_terms: list[str] = []
    signals: list[str] = []

    trigger_terms = [term for term in bundle.trigger_terms if term and term in message]
    if trigger_terms:
        matched_terms.extend(trigger_terms)
        score += 55.0 + max(len(term) for term in trigger_terms) + min(len(trigger_terms), 3) * 8.0
        signals.append(f"trigger:{','.join(trigger_terms)}")

    semantic_terms = [term for term in bundle.semantic_terms if term and term in message]
    if semantic_terms:
        matched_terms.extend(semantic_terms)
        score += min(len(semantic_terms), 4) * 12.0
        signals.append(f"semantic:{','.join(semantic_terms[:4])}")

    plan_requests_bundle = plan is not None and plan.intent == "bundle"
    if not trigger_terms and not semantic_terms and not plan_requests_bundle:
        return 0.0, (), ()

    slot_terms = collect_slot_term_matches(bundle, message)
    explicit_bundle_request = has_explicit_bundle_request(message)
    product_type_overlap = collect_product_type_overlap(bundle, filters)
    if lacks_required_domain_signal_for_specific_bundle(
        bundle=bundle,
        trigger_terms=trigger_terms,
        semantic_terms=semantic_terms,
        plan_requests_bundle=plan_requests_bundle,
    ):
        return 0.0, (), ()
    if should_suppress_ungrounded_cross_category_bundle(
        bundle=bundle,
        trigger_terms=trigger_terms,
        semantic_terms=semantic_terms,
        slot_terms=slot_terms,
        product_type_overlap=product_type_overlap,
        plan_requests_bundle=plan_requests_bundle,
    ):
        return 0.0, (), ()
    if should_suppress_single_product_request(
        message,
        trigger_terms=trigger_terms,
        semantic_terms=semantic_terms,
        slot_terms=slot_terms,
        plan_requests_bundle=plan_requests_bundle,
        explicit_bundle_request=explicit_bundle_request,
    ):
        return 0.0, (), ()

    if slot_terms:
        matched_terms.extend(slot_terms)
        score += min(len(slot_terms), 5) * 7.0
        signals.append(f"slot_terms:{','.join(slot_terms[:5])}")

    if product_type_overlap:
        score += len(product_type_overlap) * 18.0
        signals.append(f"product_type:{','.join(sorted(product_type_overlap))}")

    if plan_requests_bundle:
        score += 20.0
        signals.append("plan_intent:bundle")

    if explicit_bundle_request:
        score += 18.0
        signals.append("explicit_bundle_request")

    return score, tuple(dict.fromkeys(matched_terms)), tuple(signals)


def has_explicit_bundle_request(message: str) -> bool:
    return any(term in message for term in BUNDLE_REQUEST_TERMS) or ("从" in message and "到" in message)


def has_default_bundle_request(message: str) -> bool:
    return any(term in message for term in DEFAULT_BUNDLE_REQUEST_TERMS) or ("从" in message and "到" in message)


def should_allow_default_cross_category_bundle(
    *,
    message: str,
    plan: SemanticPlan,
    filters: SearchFilters | None,
) -> bool:
    if looks_like_single_product_request(message):
        return False
    if filters is not None and (
        filters.product_types
        or filters.categories
        or filters.brands
        or filters.keywords
        or filters.facets
    ):
        return False
    return plan.intent == "bundle" and has_default_bundle_request(message)


def lacks_required_domain_signal_for_specific_bundle(
    *,
    bundle: ScenarioBundleConfig,
    trigger_terms: list[str],
    semantic_terms: list[str],
    plan_requests_bundle: bool,
) -> bool:
    if bundle.id == "cross_category_bundle":
        return False
    if trigger_terms or semantic_terms:
        return False
    return plan_requests_bundle


def looks_like_single_product_request(message: str) -> bool:
    if "推荐" not in message:
        return False
    return any(term in message for term in SINGLE_PRODUCT_REQUEST_PATTERN_TERMS)


def should_suppress_single_product_request(
    message: str,
    *,
    trigger_terms: list[str],
    semantic_terms: list[str],
    slot_terms: list[str],
    plan_requests_bundle: bool,
    explicit_bundle_request: bool,
) -> bool:
    if plan_requests_bundle or explicit_bundle_request:
        return False

    unique_terms = set(trigger_terms) | set(semantic_terms) | set(slot_terms)
    if looks_like_single_product_request(message):
        return True

    # Semantic/slot terms like "防晒" are shared with ordinary product search.
    # Treat them as a bundle only when the utterance carries at least two distinct
    # scenario signals; a lone slot term should stay in recommendation routing.
    if not trigger_terms and len(unique_terms) < 2:
        return True
    return False


def should_suppress_ungrounded_cross_category_bundle(
    *,
    bundle: ScenarioBundleConfig,
    trigger_terms: list[str],
    semantic_terms: list[str],
    slot_terms: list[str],
    product_type_overlap: set[str],
    plan_requests_bundle: bool,
) -> bool:
    """Avoid turning unsupported product nouns into a generic bundle.

    Terms such as "组合" and "套装" are often part of a product scope
    ("文具组合", "护肤套装") rather than a request for our curated
    cross-category strategy. The fallback bundle should only activate when a
    planner explicitly asks for a bundle, or when the utterance has a grounded
    scenario/product signal beyond those generic nouns.
    """
    if bundle.id != "cross_category_bundle":
        return False
    if plan_requests_bundle or slot_terms or product_type_overlap:
        return False

    matched = {term for term in (*trigger_terms, *semantic_terms) if term}
    if not matched:
        return False
    return matched <= GENERIC_CROSS_CATEGORY_TERMS


def collect_slot_term_matches(bundle: ScenarioBundleConfig, message: str) -> list[str]:
    matched: list[str] = []
    for slot in bundle.slots:
        terms = [slot.label, *slot.match_terms]
        for term in terms:
            if term and term in message:
                matched.append(term)
    return matched


def collect_product_type_overlap(
    bundle: ScenarioBundleConfig,
    filters: SearchFilters | None,
) -> set[str]:
    if filters is None or not filters.product_types:
        return set()
    return set(filters.product_types) & collect_bundle_product_types(bundle)


def collect_bundle_product_types(bundle: ScenarioBundleConfig) -> set[str]:
    product_types: set[str] = set()
    for slot in bundle.slots:
        product_types.update(slot.filters.product_types)
    return product_types
