"""
Purpose: 规则层语义规划：build_rule_plan 基于规则快速生成 plan，作为 LLM 的 fallback。
"""

import re
from typing import Any

from server.agent.context import extract_referenced_index, select_contextual_product
from server.agent.filters import extract_filters, is_negated_term
from server.agent.intent import UserIntent, detect_intent
from server.agent.scenarios import has_scenario_match
from server.agent.semantic_schema import CartAction, SemanticConstraint, SemanticFilter, SemanticIntent, SemanticPlan
from server.nlu.quantity import (
    QuantityParse,
    contains_explicit_cart_add,
    extract_quantity,
    extract_quantity_parse,
    is_add_quantity_expression,
)
from server.nlu.query_understanding import understand_query
from server.nlu.taxonomy_classifier import TaxonomyClassification, classify_taxonomy_query
from server.rag.brand_aliases import extract_brand_mentions, matched_brand_aliases, message_mentions_brand, normalize_text
from server.session.state import SessionState


IN_STOCK_PATTERNS = ("只看有货", "仅看有货", "有货", "现货", "有现货", "库存充足")
PRESALE_NEGATION_PATTERNS = ("不要预售", "不看预售", "非预售", "不是预售", "排除预售")
SERVICE_PATTERNS: dict[str, tuple[str, ...]] = {
    "invoice_policy": ("支持企业开票", "企业开票", "开发票", "发票"),
    "after_sales_policy": ("官方保修", "保修", "质保", "售后"),
    "same_day_shipping": ("今天能发货", "当天发货", "今日发货", "今天发", "现货发"),
    "coupon_policy": ("优惠券", "有券", "领券", "优惠", "折扣", "促销"),
}

FACET_PATTERNS: tuple[tuple[str, str, re.Pattern], ...] = (
    ("memory", "内存", re.compile(r"(?P<value>\d{1,3})\s*(?:g|gb|GB|G)\s*内存")),
    ("storage", "硬盘", re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?:t|tb|TB|T)\s*(?:硬盘|存储|固态)")),
    ("storage", "硬盘", re.compile(r"(?P<value>\d{2,4})\s*(?:g|gb|GB|G)\s*(?:硬盘|存储|固态)")),
    ("color", "颜色", re.compile(r"(?P<value>白色|黑色|蓝色|绿色|粉色|灰色|银色|金色|红色|紫色)")),
    ("shoe_size", "尺码", re.compile(r"(?P<value>\d{2})\s*(?:码|yards?)")),
)


def build_rule_plan(message: str, session: SessionState) -> SemanticPlan:
    understanding = understand_query(message)
    taxonomy = classify_taxonomy_query(message)
    filters = [SemanticFilter(kind=item.kind, value=item.value) for item in extract_filters(message)]
    filters.extend(build_semantic_filters(message, session, taxonomy=taxonomy))
    filters = dedupe_semantic_filters(filters)
    constraints = build_semantic_constraints(message, filters, session)

    reference = infer_reference(message, session.candidate_product_cards)
    cart_action = infer_cart_action(
        message,
        has_reference=reference["reference_type"] != "none",
    )
    quantity_parse = extract_quantity_parse(message)
    quantity = quantity_parse.value if quantity_parse else None
    user_intent = detect_intent(message)
    intent = map_rule_intent(
        message=message,
        user_intent=user_intent,
        has_reference=reference["reference_type"] != "none",
        cart_action=cart_action,
    )
    if understanding.presentation_mode == "listing" and intent == "recommend":
        intent = "browse"
    needs_search = intent not in {"ask_product_detail", "cart", "clarify"}
    query = build_rule_query(message, filters)
    if session.pending_subject and session.pending_subject not in query:
        query = f"{session.pending_subject} {query}"

    confidence_by_field, evidence = build_nlu_metadata(
        message=message,
        intent=intent,
        cart_action=cart_action,
        reference=reference,
        quantity_parse=quantity_parse,
        filters=filters,
        constraints=constraints,
        taxonomy=taxonomy,
        query_understanding=understanding.as_metadata(),
    )

    return SemanticPlan(
        intent=intent,
        cart_action=cart_action,
        reference_type=reference["reference_type"],
        reference_index=reference["reference_index"],
        reference_text=reference["reference_text"],
        quantity=quantity,
        query=query,
        presentation_mode=understanding.presentation_mode,
        query_understanding=understanding.as_metadata(),
        filters=filters,
        constraints=dedupe_semantic_constraints(constraints),
        needs_search=needs_search,
        confidence=estimate_plan_confidence(confidence_by_field),
        confidence_by_field=confidence_by_field,
        evidence=evidence,
    )


def build_semantic_filters(
    message: str,
    session: SessionState,
    *,
    taxonomy: TaxonomyClassification | None = None,
) -> list[SemanticFilter]:
    filters: list[SemanticFilter] = []
    taxonomy = taxonomy or classify_taxonomy_query(message)
    for match in taxonomy.product_types:
        kind = "exclude_product_type" if is_negated_term(message, match.evidence) else "product_type"
        filters.append(SemanticFilter(kind=kind, value=match.value))
    for match in taxonomy.categories:
        kind = "exclude_category" if is_negated_term(message, match.evidence) else "category"
        filters.append(SemanticFilter(kind=kind, value=match.value))
    for brand in extract_brand_mentions(message):
        matched_aliases = matched_brand_aliases(message, brand)
        negated = is_negated_term(message, brand) or any(is_negated_term(message, alias) for alias in matched_aliases)
        kind = "exclude_brand" if negated else "brand"
        filters.append(SemanticFilter(kind=kind, value=brand))

    keyword_aliases = {
        "干皮": "保湿",
        "补水": "保湿",
        "滋润": "保湿",
        "温和": "敏感肌",
        "更温和": "敏感肌",
        "低刺激": "敏感肌",
        "维稳": "敏感肌",
        "熬夜": "修护",
        "淡纹": "修护",
    }
    for word, keyword in keyword_aliases.items():
        if word in message:
            filters.append(SemanticFilter(kind="keyword", value=keyword))

    filters.extend(build_inventory_filters(message))
    filters.extend(build_service_filters(message))
    filters.extend(build_facet_filters(message))

    referenced_card = select_contextual_product(message, session.candidate_product_cards)
    if referenced_card and any(word in message for word in ["别太贵", "不要太贵", "差不多价位", "同价位"]):
        filters.append(SemanticFilter(kind="max_price", value=str(int(float(referenced_card.get("price", 0))))))

    filters.extend(build_candidate_exclusions(message, session.candidate_product_cards))
    return filters


def build_nlu_metadata(
    *,
    message: str,
    intent: SemanticIntent,
    cart_action: CartAction,
    reference: dict[str, Any],
    quantity_parse: QuantityParse | None,
    filters: list[SemanticFilter],
    constraints: list[SemanticConstraint],
    taxonomy: TaxonomyClassification,
    query_understanding: dict[str, Any],
) -> tuple[dict[str, float], dict[str, Any]]:
    confidence: dict[str, float] = {}
    evidence: dict[str, Any] = {
        "raw_query": message,
        "query_understanding": query_understanding,
        "taxonomy": taxonomy.as_metadata(),
        "filters": [{"kind": item.kind, "value": item.value} for item in filters],
        "constraints": [
            {
                "mode": item.mode,
                "field": item.field,
                "operator": item.operator,
                "value": item.value,
                "source": item.source,
            }
            for item in constraints
        ],
    }

    if intent == "cart":
        confidence["intent"] = 0.94 if cart_action != "none" and reference["reference_type"] != "none" else 0.68
    elif intent in {"compare", "bundle"}:
        confidence["intent"] = 0.88
    elif intent == "browse":
        confidence["intent"] = 0.84
    else:
        confidence["intent"] = 0.76
    evidence["intent"] = {"value": intent, "cart_action": cart_action}

    if taxonomy.product_types:
        best = max(taxonomy.product_types, key=lambda item: item.confidence)
        confidence["product_type"] = round(best.confidence, 4)
        evidence["product_type"] = {
            "value": best.value,
            "label": best.label,
            "source": best.source,
            "span": best.evidence,
        }

    if taxonomy.categories:
        best_category = max(taxonomy.categories, key=lambda item: item.confidence)
        confidence["category"] = round(best_category.confidence, 4)
        evidence["category"] = {
            "value": best_category.value,
            "label": best_category.label,
            "source": best_category.source,
            "span": best_category.evidence,
        }

    price_filters = [item for item in filters if item.kind in {"min_price", "max_price"}]
    if price_filters:
        confidence["price"] = 0.94
        evidence["price"] = [{"kind": item.kind, "value": item.value} for item in price_filters]

    brand_filters = [item for item in filters if item.kind in {"brand", "preferred_brand", "exclude_brand"}]
    if brand_filters:
        confidence["brand"] = 0.9
        evidence["brand"] = [{"kind": item.kind, "value": item.value} for item in brand_filters]

    service_filters = [item for item in filters if item.kind == "unsupported_service"]
    if service_filters:
        confidence["service"] = 0.82
        evidence["service"] = [{"value": item.value, "source": "rule.service_not_integrated"} for item in service_filters]

    facet_filters = [item for item in filters if item.kind == "facet"]
    if facet_filters:
        confidence["facet"] = 0.86
        evidence["facet"] = [{"value": item.value, "source": "rule.facet"} for item in facet_filters]

    if any(item.kind == "in_stock" for item in filters):
        confidence["stock"] = 0.82
        evidence["stock"] = {"value": "in_stock_only", "source": "rule.static_stock"}

    if quantity_parse is not None:
        confidence["quantity"] = 0.99 if quantity_parse.source == "quantity_assignment" else 0.94
        evidence["quantity"] = {
            "value": quantity_parse.value,
            "unit": quantity_parse.unit,
            "span": message[quantity_parse.start : quantity_parse.end],
            "source": quantity_parse.source,
            "is_count": quantity_parse.is_count,
        }

    if reference["reference_type"] != "none":
        reference_confidence = {
            "ordinal": 0.99,
            "brand": 0.9,
            "name": 0.92,
            "cheapest": 0.88,
            "last": 0.72,
        }.get(str(reference["reference_type"]), 0.7)
        confidence["reference"] = reference_confidence
        evidence["reference"] = dict(reference)

    if cart_action != "none":
        confidence["cart_action"] = 0.95 if reference["reference_type"] != "none" else 0.62
        evidence["cart_action"] = {"value": cart_action, "requires_reference": cart_action in {"add", "remove", "update_quantity"}}

    return confidence, evidence


def estimate_plan_confidence(confidence_by_field: dict[str, float]) -> float:
    if not confidence_by_field:
        return 0.55
    values = list(confidence_by_field.values())
    core_fields = [confidence_by_field[key] for key in ("intent", "product_type", "category", "reference", "cart_action") if key in confidence_by_field]
    if core_fields:
        return round(sum(core_fields) / len(core_fields), 4)
    return round(sum(values) / len(values), 4)


def build_inventory_filters(message: str) -> list[SemanticFilter]:
    filters: list[SemanticFilter] = []
    if any(pattern in message for pattern in IN_STOCK_PATTERNS) and not any(
        pattern in message for pattern in ["不要求有货", "不看库存", "无所谓库存"]
    ):
        filters.append(SemanticFilter(kind="in_stock", value="true"))
    if any(pattern in message for pattern in PRESALE_NEGATION_PATTERNS):
        filters.append(SemanticFilter(kind="unsupported_service", value="presale_status"))
    return filters


def build_service_filters(message: str) -> list[SemanticFilter]:
    filters: list[SemanticFilter] = []
    for service, patterns in SERVICE_PATTERNS.items():
        if any(pattern in message for pattern in patterns):
            filters.append(SemanticFilter(kind="unsupported_service", value=service))
    return filters


def build_facet_filters(message: str) -> list[SemanticFilter]:
    filters: list[SemanticFilter] = []
    for field, label, pattern in FACET_PATTERNS:
        for match in pattern.finditer(message):
            value = match.group("value")
            if not value:
                continue
            normalized = normalize_facet_value(field, value)
            filters.append(SemanticFilter(kind="facet", value=f"{field}:{normalized}"))
    return filters


def normalize_facet_value(field: str, value: str) -> str:
    value = value.strip()
    if field == "memory":
        return f"{int(float(value))}GB"
    if field == "storage":
        if re.fullmatch(r"\d+(?:\.\d+)?", value):
            number = float(value)
            if number < 10:
                return f"{int(number) if number.is_integer() else number}TB"
            return f"{int(number)}GB"
        return value.upper()
    return value


def build_candidate_exclusions(message: str, cards: list[dict]) -> list[SemanticFilter]:
    filters: list[SemanticFilter] = []
    if not any(word in message for word in ["先不要", "不要", "不要了", "排除", "换个", "别"]):
        return filters

    message_lower = normalize_text(message)
    for card in cards:
        brand = str(card.get("brand", "")).strip()
        if brand and message_mentions_brand(message_lower, brand):
            filters.append(SemanticFilter(kind="exclude_brand", value=brand))
            continue

        name = str(card.get("name", "")).strip()
        if name and name in message:
            filters.append(SemanticFilter(kind="exclude", value=name))
    return filters


def build_semantic_constraints(
    message: str,
    filters: list[SemanticFilter],
    session: SessionState,
) -> list[SemanticConstraint]:
    constraints: list[SemanticConstraint] = []
    preference_mode = has_soft_preference_marker(message)

    for item in filters:
        if item.kind == "max_price":
            constraints.append(
                SemanticConstraint(
                    mode="must",
                    field="price",
                    operator="lte",
                    value=item.value,
                    source="rule.price_ceiling",
                )
            )
        elif item.kind == "min_price":
            constraints.append(
                SemanticConstraint(
                    mode="must",
                    field="price",
                    operator="gte",
                    value=item.value,
                    source="rule.price_floor",
                )
            )
        elif item.kind == "product_type":
            constraints.append(
                SemanticConstraint(
                    mode="must",
                    field="product_type",
                    operator="eq",
                    value=item.value,
                    source="rule.product_type",
                )
            )
        elif item.kind == "exclude_product_type":
            constraints.append(
                SemanticConstraint(
                    mode="must_not",
                    field="product_type",
                    operator="eq",
                    value=item.value,
                    source="rule.exclude_product_type",
                )
            )
        elif item.kind == "category":
            constraints.append(
                SemanticConstraint(
                    mode="must",
                    field="category",
                    operator="eq",
                    value=item.value,
                    source="rule.category",
                )
            )
        elif item.kind == "exclude_category":
            constraints.append(
                SemanticConstraint(
                    mode="must_not",
                    field="category",
                    operator="eq",
                    value=item.value,
                    source="rule.exclude_category",
                )
            )
        elif item.kind == "keyword":
            constraints.append(
                SemanticConstraint(
                    mode="should" if preference_mode else "must",
                    field="keyword",
                    operator="contains",
                    value=item.value,
                    source="rule.keyword",
                )
            )
        elif item.kind == "should_keyword":
            constraints.append(
                SemanticConstraint(
                    mode="should",
                    field="keyword",
                    operator="contains",
                    value=item.value,
                    source="rule.soft_keyword",
                )
            )
        elif item.kind == "brand":
            constraints.append(
                SemanticConstraint(
                    mode="must",
                    field="brand",
                    operator="eq",
                    value=item.value,
                    source="rule.brand",
                )
            )
        elif item.kind == "preferred_brand":
            constraints.append(
                SemanticConstraint(
                    mode="should",
                    field="brand",
                    operator="eq",
                    value=item.value,
                    source="rule.preferred_brand",
                )
            )
        elif item.kind == "exclude_brand":
            constraints.append(
                SemanticConstraint(
                    mode="must_not",
                    field="brand",
                    operator="eq",
                    value=item.value,
                    source="rule.exclude_brand",
                )
            )
        elif item.kind == "exclude":
            constraints.append(
                SemanticConstraint(
                    mode="must_not",
                    field="attribute",
                    operator="contains",
                    value=item.value,
                    source="rule.exclude",
                )
            )
        elif item.kind == "in_stock":
            constraints.append(
                SemanticConstraint(
                    mode="must",
                    field="stock",
                    operator="gte",
                    value="1",
                    source="rule.static_stock",
                )
            )
        elif item.kind == "facet":
            constraints.append(
                SemanticConstraint(
                    mode="must",
                    field="facet",
                    operator="eq",
                    value=item.value,
                    source="rule.facet",
                )
            )
        elif item.kind == "unsupported_service":
            constraints.append(
                SemanticConstraint(
                    mode="must",
                    field="service",
                    operator="eq",
                    value=item.value,
                    source="rule.service_not_integrated",
                )
            )

    constraints.extend(build_brand_constraints_from_message(message, session))
    return constraints


def has_soft_preference_marker(message: str) -> bool:
    return any(word in message for word in ["优先", "最好", "更看重", "偏好", "倾向", "可以的话", "尽量"])


def build_brand_constraints_from_message(message: str, session: SessionState) -> list[SemanticConstraint]:
    constraints: list[SemanticConstraint] = []
    message_lower = normalize_text(message)
    known_brands = {
        str(card.get("brand", "")).strip()
        for card in session.candidate_product_cards
        if str(card.get("brand", "")).strip()
    }
    for brand in known_brands:
        matched_aliases = matched_brand_aliases(message_lower, brand)
        if not matched_aliases:
            continue
        if any(is_negated_brand_alias(message_lower, alias) for alias in matched_aliases):
            constraints.append(
                SemanticConstraint(
                    mode="must_not",
                    field="brand",
                    operator="eq",
                    value=brand,
                    source="rule.brand_negation",
                )
            )
        elif has_soft_preference_marker(message):
            constraints.append(
                SemanticConstraint(
                    mode="should",
                    field="brand",
                    operator="eq",
                    value=brand,
                    source="rule.brand_preference",
                )
            )
    return constraints


def is_negated_brand_alias(message_lower: str, alias: str) -> bool:
    alias = normalize_text(alias)
    for match in re.finditer(re.escape(alias), message_lower):
        prefix = message_lower[max(0, match.start() - 10) : match.start()]
        suffix = message_lower[match.end() : match.end() + 10]
        if any(word in prefix for word in ["不要", "不想要", "排除", "除了", "先不要", "别"]):
            return True
        if any(word in suffix for word in ["不要", "不想要", "排除", "先不要", "不要了", "算了"]):
            return True
    return False


def infer_reference(message: str, cards: list[dict]) -> dict[str, Any]:
    index = extract_referenced_index(message)
    if index is not None:
        return {"reference_type": "ordinal", "reference_index": index + 1, "reference_text": ""}

    if any(word in message for word in ["最便宜", "便宜的那个", "低价那个"]):
        return {"reference_type": "cheapest", "reference_index": None, "reference_text": ""}

    for card in cards:
        brand = str(card.get("brand", "")).strip()
        name = str(card.get("name", "")).strip()
        if brand and brand.lower() in message.lower():
            return {"reference_type": "brand", "reference_index": None, "reference_text": brand}
        if name and name in message:
            return {"reference_type": "name", "reference_index": None, "reference_text": name}

    if any(word in message for word in ["刚才", "上一个", "这个", "这款", "那款", "它", "那支", "那件"]):
        return {"reference_type": "last", "reference_index": None, "reference_text": ""}
    return {"reference_type": "none", "reference_index": None, "reference_text": ""}


def infer_cart_action(message: str, *, has_reference: bool = False) -> CartAction:
    if any(word in message for word in ["下单", "结算", "提交订单"]):
        return "checkout"
    if any(word in message for word in ["删除", "删掉", "移除", "去掉", "拿掉", "清空", "不要这个"]):
        return "remove"
    if any(word in message for word in ["数量", "改成", "改为", "设为", "加一件", "减一件", "多加", "少一件"]):
        return "update_quantity"
    if "购物车" in message and not any(word in message for word in ["加到", "加入", "加购", "添加", "添加到", "放到", "放进"]):
        return "view"
    if is_add_to_cart_expression(message) and (
        has_reference or contains_explicit_cart_add(message)
    ):
        return "add"
    return "none"


def is_add_to_cart_expression(message: str) -> bool:
    return is_add_quantity_expression(message)


def map_rule_intent(
    message: str,
    user_intent: UserIntent,
    has_reference: bool,
    cart_action: CartAction,
) -> SemanticIntent:
    if cart_action != "none":
        return "cart"
    if asks_for_bundle(message):
        return "bundle"
    if user_intent == UserIntent.COMPARE:
        return "compare"
    if user_intent == UserIntent.CART:
        return "cart"
    if has_reference and not asks_for_new_search(message):
        return "ask_product_detail"
    if user_intent == UserIntent.BROWSING:
        return "browse"
    return "recommend"


def asks_for_bundle(message: str) -> bool:
    return has_scenario_match(message)


def asks_for_new_search(message: str) -> bool:
    if looks_like_contextual_fact_question(message):
        return False
    return any(word in message for word in ["有没有", "换个", "换一款", "再推荐", "更适合", "再看看", "还有"])


def looks_like_contextual_fact_question(message: str) -> bool:
    has_context_reference = any(word in message for word in ["这款", "这个", "那款", "它", "刚才", "第一款", "第二款"])
    if not has_context_reference:
        return False
    fact_terms = [
        *IN_STOCK_PATTERNS,
        *(term for patterns in SERVICE_PATTERNS.values() for term in patterns),
        "库存",
        "退货",
        "七天无理由",
        "物流",
    ]
    return any(term in message for term in fact_terms)


def build_rule_query(message: str, filters: list[SemanticFilter]) -> str:
    keywords = [item.value for item in filters if item.kind == "keyword"]
    excludes = [item.value for item in filters if item.kind == "exclude"]
    pieces = [message, *keywords]
    if excludes:
        pieces.append("排除 " + " ".join(excludes))
    return " ".join(piece for piece in pieces if piece).strip()


def dedupe_semantic_filters(filters: list[SemanticFilter]) -> list[SemanticFilter]:
    seen: set[tuple[str, str]] = set()
    result: list[SemanticFilter] = []
    for item in filters:
        key = (item.kind, item.value.strip())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def dedupe_semantic_constraints(constraints: list[SemanticConstraint]) -> list[SemanticConstraint]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[SemanticConstraint] = []
    for item in constraints:
        key = (item.mode, item.field, item.operator, item.value.strip())
        if not key[3] or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
