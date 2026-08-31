"""
Purpose: 对比展示器：构建对比维度和推荐建议，格式化对比结果。
"""

import re
from typing import Any


def build_comparison_card(query: str, cards: list[dict]) -> dict[str, Any]:
    products = [build_comparison_product(card, query) for card in cards]
    recommendation = choose_recommendation(query, cards)
    return {
        "title": "商品对比",
        "query": query,
        "products": products,
        "dimensions": build_dimensions(cards),
        "recommendation": recommendation,
    }


def build_comparison_product(card: dict, query: str) -> dict[str, Any]:
    return {
        "id": card.get("id", ""),
        "name": card.get("name", ""),
        "brand": card.get("brand", ""),
        "category": card.get("category", ""),
        "price": card.get("price", 0),
        "reason": card.get("reason", ""),
        "strengths": infer_strengths(card),
        "tradeoffs": infer_tradeoffs(card, query),
    }


def build_dimensions(cards: list[dict]) -> list[dict[str, Any]]:
    if not cards:
        return []

    cheapest = min(cards, key=lambda card: float(card.get("price", 0)))
    return [
        {
            "name": "价格",
            "values": {card["id"]: f"¥{int(float(card.get('price', 0)))}" for card in cards},
            "winner_id": cheapest["id"],
        },
        {
            "name": "匹配依据",
            "values": {card["id"]: card.get("reason", "") for card in cards},
            "winner_id": choose_recommendation("", cards).get("product_id", ""),
        },
    ]


def choose_recommendation(query: str, cards: list[dict]) -> dict[str, str]:
    if not cards:
        return {"product_id": "", "summary": "当前候选不足，无法给出可靠对比结论。"}

    focus_groups = [
        ("预算/性价比", ["预算", "性价比", "便宜", "价格", "省钱"]),
        ("拍照/影像", ["拍照", "影像", "人像", "摄影"]),
        ("保湿/干皮", ["保湿", "补水", "干皮", "滋润"]),
        ("敏感肌", ["敏感", "低刺激", "维稳", "修护"]),
        ("续航", ["续航", "电池"]),
        ("性能", ["性能", "游戏", "芯片"]),
    ]
    focus = next((name for name, terms in focus_groups if any(term in query for term in terms)), "综合匹配")

    budget = extract_budget(query)
    if focus == "预算/性价比":
        in_budget = [card for card in cards if budget is None or float(card.get("price", 0)) <= budget]
        selected = min(in_budget or cards, key=lambda card: float(card.get("price", 0)))
    else:
        terms = next((terms for name, terms in focus_groups if name == focus), [])
        selected = max(cards, key=lambda card: focus_score(card, terms))
        if terms and focus_score(selected, terms) == 0:
            selected = max(cards, key=lambda card: float(card.get("score", 0)))

    return {
        "product_id": selected.get("id", ""),
        "product_name": selected.get("name", ""),
        "focus": focus,
        "summary": f"更偏向 {focus} 时，优先看 {selected.get('name', '')}。",
    }


def focus_score(card: dict, terms: list[str]) -> int:
    haystack = " ".join(
        [
            str(card.get("name", "")),
            str(card.get("brand", "")),
            str(card.get("category", "")),
            str(card.get("reason", "")),
        ]
    )
    return sum(1 for term in terms if term in haystack)


def infer_strengths(card: dict) -> list[str]:
    haystack = " ".join(
        [
            str(card.get("name", "")),
            str(card.get("brand", "")),
            str(card.get("category", "")),
            str(card.get("reason", "")),
        ]
    )
    labels = [
        "保湿",
        "补水",
        "敏感肌",
        "修护",
        "控油",
        "防晒",
        "拍照",
        "影像",
        "人像",
        "续航",
        "性能",
        "轻薄",
        "性价比",
    ]
    strengths = [label for label in labels if label in haystack]
    return strengths[:4] or ["与当前需求相关"]


def infer_tradeoffs(card: dict, query: str) -> list[str]:
    tradeoffs: list[str] = []
    if "敏感" in query and focus_score(card, ["敏感", "低刺激", "维稳", "修护"]) == 0:
        tradeoffs.append("敏感肌适配信息不足")
    budget = extract_budget(query)
    if budget is not None:
        price = float(card.get("price", 0))
        if price > budget:
            tradeoffs.append(f"超过预算 {int(price - budget)} 元")
        else:
            tradeoffs.append("符合预算")
    return tradeoffs or ["需结合个人偏好确认"]


def build_comparison_answer(comparison: dict[str, Any]) -> str:
    products = comparison.get("products", [])
    if len(products) < 2:
        if products:
            return (
                f"当前商品库只找到 {products[0]['name']}，"
                "还不足以做可靠的两款商品对比。你可以补充另一个商品名或品牌。"
            )
        return "当前商品库没有找到足够候选，暂时无法做商品对比。"

    first, second = products[0], products[1]
    recommendation = comparison.get("recommendation", {})
    return (
        "我先基于当前商品库做对比："
        f"{first['name']}（{first['brand']}，¥{int(float(first['price']))}）"
        f" vs {second['name']}（{second['brand']}，¥{int(float(second['price']))}）。"
        f"{first['brand']} 的主要匹配点是{format_list(first['strengths'])}，"
        f"{second['brand']} 的主要匹配点是{format_list(second['strengths'])}。"
        f"{recommendation.get('summary', '')}"
        "价格、品牌和结论都只来自当前候选商品。"
    )


def format_list(items: list[str]) -> str:
    return "、".join(items[:4]) if items else "与需求相关"


def extract_budget(query: str) -> float | None:
    patterns = [
        r"预算\s*(?:是|为|大概|约|在)?\s*(\d+)\s*(元|块)?",
        r"(\d+)\s*(元|块)?\s*(以内|以下|之内)",
    ]
    for pattern in patterns:
        match = re.search(pattern, query)
        if match:
            return float(match.group(1))
    return None
