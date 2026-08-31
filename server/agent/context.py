"""
Purpose: 上下文过滤提取：从消息中提取同价位、别太贵等上下文过滤条件，绑定到已推荐商品。
"""

from server.commerce.facts import get_fact_provider
from server.nlu.quantity import extract_ordinal_index
from server.session.state import FilterCondition


def extract_contextual_filters(message: str, session, plan=None) -> list[FilterCondition]:
    if not session.candidate_product_cards:
        return []

    referenced_card = select_contextual_product(message, session.candidate_product_cards, plan)
    if referenced_card and any(word in message for word in ["别太贵", "不要太贵", "差不多价位", "同价位"]):
        price = int(float(referenced_card.get("price", 0)))
        if price > 1:
            return [FilterCondition(kind="max_price", value=str(price))]

    if is_cheaper_follow_up(message):
        prices = [
            float(card.get("price", 0))
            for card in session.candidate_product_cards
            if float(card.get("price", 0)) > 1
        ]
        if not prices:
            return []

        next_max_price = max(1, int(min(prices)) - 1)
        return [FilterCondition(kind="max_price", value=str(next_max_price))]

    return []


def is_cheaper_follow_up(message: str) -> bool:
    return any(word in message for word in ["再便宜", "更便宜", "便宜点", "便宜一点", "便宜些", "低价一点"])


def select_contextual_product(message: str, cards: list[dict], plan=None) -> dict | None:
    if not cards:
        return None

    plan_match = select_product_from_plan(cards, plan)
    if plan_match is not None:
        return plan_match

    index = extract_referenced_index(message)
    if index is not None:
        if 0 <= index < len(cards):
            return cards[index]
        return None

    text_match = select_product_by_text(message, cards)
    if text_match is not None:
        return text_match

    if any(word in message for word in ["刚才", "上一个", "这个", "这款", "那款", "它", "那支", "那件"]):
        return cards[0]
    return None


def select_product_from_plan(cards: list[dict], plan) -> dict | None:
    if plan is None:
        return None

    reference_type = getattr(plan, "reference_type", "none")
    reference_index = getattr(plan, "reference_index", None)
    reference_text = str(getattr(plan, "reference_text", "") or "")

    if reference_type == "ordinal" and reference_index is not None:
        index = int(reference_index) - 1
        if 0 <= index < len(cards):
            return cards[index]
        return None

    if reference_type == "cheapest":
        return min(cards, key=lambda card: float(card.get("price", 0)))

    if reference_type in {"name", "brand"} and reference_text:
        return select_product_by_text(reference_text, cards)

    if reference_type == "last":
        return cards[0]
    return None


def select_product_by_text(text: str, cards: list[dict]) -> dict | None:
    normalized = text.lower()
    for card in cards:
        brand = str(card.get("brand", "")).strip()
        name = str(card.get("name", "")).strip()
        product_id = str(card.get("id", "")).strip()
        if brand and brand.lower() in normalized:
            return card
        if name and name.lower() in normalized:
            return card
        if product_id and product_id.lower() in normalized:
            return card
    return None


def extract_referenced_index(message: str) -> int | None:
    return extract_ordinal_index(message)


def build_contextual_product_answer(message: str, card: dict) -> str:
    fact_answer = build_business_fact_answer(message, card)
    if fact_answer:
        return fact_answer

    focus_terms = [
        term
        for term in ["干皮", "敏感肌", "拍照", "续航", "性能", "性价比", "保湿", "温和", "熬夜", "修护"]
        if term in message
    ]
    focus_text = f"关于 {'、'.join(focus_terms)}，" if focus_terms else ""
    return (
        f"你说的是 {card['name']}。"
        f"{focus_text}当前商品库里能确认的信息是：品牌 {card['brand']}，"
        f"类目 {card['category']}，价格 {int(float(card['price']))} 元，"
        f"匹配依据是 {card.get('reason', '与当前需求相关')}。"
        "如果你想换一个方向，可以继续说“再便宜点”“看第二个”或“把它加到购物车”。"
    )


def build_business_fact_answer(message: str, card: dict) -> str:
    if asks_stock(message):
        return build_static_stock_answer(card)
    if asks_coupon(message):
        return build_missing_service_answer(card, "优惠券/促销", "coupon_policy")
    if asks_invoice(message):
        return build_missing_service_answer(card, "企业开票", "invoice_policy")
    if asks_after_sales(message):
        return build_missing_service_answer(card, "售后/官方保修", "after_sales_policy")
    if asks_logistics(message):
        return build_missing_service_answer(card, "发货时效/物流承诺", "logistics_policy")
    return ""


def asks_stock(message: str) -> bool:
    return any(word in message for word in ["有货", "现货", "库存", "还能买吗", "可买吗"])


def asks_coupon(message: str) -> bool:
    return any(word in message for word in ["优惠券", "有券", "领券", "优惠", "折扣", "促销"])


def asks_invoice(message: str) -> bool:
    return any(word in message for word in ["发票", "开票", "企业开票", "专票"])


def asks_after_sales(message: str) -> bool:
    return any(word in message for word in ["保修", "质保", "售后", "退换", "退货", "七天无理由"])


def asks_logistics(message: str) -> bool:
    return any(word in message for word in ["发货", "物流", "今天到", "今天能发", "当天发", "时效"])


def build_static_stock_answer(card: dict) -> str:
    name = str(card.get("name", "这款商品")).strip() or "这款商品"
    fact = get_fact_provider().stock(card)
    if not fact.available:
        return (
            f"{name} 当前商品数据未提供库存字段。"
            "我不能替你判断是否有货，需要接入实时库存服务后才能给确定结论。"
        )

    stock_count = parse_int(fact.value, default=0)
    status = "大于 0，静态商品库显示有库存" if stock_count > 0 else "为 0，静态商品库显示无库存"
    return (
        f"{name} 的 mock 库存服务显示可售数量{status}。"
        "这是本地开发用的库存服务，不等同于真实线上库存；"
        "接入生产后应以 inventory_service 或交易系统返回为准。"
    )


def build_missing_service_answer(card: dict, label: str, field_name: str) -> str:
    name = str(card.get("name", "这款商品")).strip() or "这款商品"
    fact = getattr(get_fact_provider(), field_name)(card)
    if fact.available:
        return build_available_service_answer(name, label, field_name, fact)
    reason = fact.missing_reason or f"缺少 {field_name} 字段/服务"
    return (
        f"{name} 当前没有接入可核验的{label}数据源（{reason}）。"
        f"所以我不能给出“支持”或“不支持”的确定结论，只能说明当前商品库未提供这项信息。"
    )


def build_available_service_answer(name: str, label: str, field_name: str, fact) -> str:
    value = fact.value if isinstance(fact.value, dict) else {}
    caveat = "当前是本地 mock 服务结果，接入生产后应以对应业务服务实时返回为准。"
    if field_name == "coupon_policy":
        if value.get("has_coupon"):
            return f"{name} 的 mock 优惠服务显示：{value.get('description', '有可用优惠')}。{caveat}"
        return f"{name} 的 mock 优惠服务显示：当前没有可用优惠券。{caveat}"
    if field_name == "invoice_policy":
        if value.get("supports_enterprise_invoice"):
            return f"{name} 的 mock 开票服务显示支持企业开票，票种包括：{'、'.join(value.get('types', []))}。{caveat}"
        return f"{name} 的 mock 开票服务显示不支持企业开票。{caveat}"
    if field_name == "after_sales_policy":
        return (
            f"{name} 的 mock 售后服务显示：{value.get('warranty', '以品牌官方政策为准')}；"
            f"{value.get('opened_return_policy', '退换规则需按类目审核')}。{caveat}"
        )
    if field_name == "logistics_policy":
        return f"{name} 的 mock 物流服务显示：{value.get('promise', '暂无发货承诺')}。{caveat}"
    return f"{name} 的 mock {label}服务返回了可用信息。{caveat}"


def parse_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default
