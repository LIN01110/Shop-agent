"""
Purpose: 商品卡片绑定：将 LLM 回答中提到的商品与候选商品卡片做绑定匹配。
"""

import re
import unicodedata
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CardBindingResult:
    cards: list[dict]
    answer_product_ids: list[str] = field(default_factory=list)
    dropped_product_ids: list[str] = field(default_factory=list)
    reason: str = "no_specific_answer_mentions"


DECLINE_RECOMMENDATION_TERMS = (
    "没有",
    "未找到",
    "暂未",
    "无法",
    "不能",
    "不支持",
    "无可用",
)

PRODUCT_NEED_TERMS = (
    "商品",
    "产品",
    "候选",
    "推荐",
    "匹配",
    "类",
)


def bind_cards_to_answer(answer: str, cards: list[dict]) -> CardBindingResult:
    """Keep product cards aligned with the products actually recommended in text."""
    if not cards:
        return CardBindingResult(cards=[])

    answer_product_ids = extract_answer_product_ids(answer, cards)
    if not answer_product_ids:
        if answer_declines_product_recommendation(answer):
            return CardBindingResult(
                cards=[],
                answer_product_ids=[],
                dropped_product_ids=[str(card.get("id", "")) for card in cards],
                reason="answer_declines_recommendation",
            )
        return CardBindingResult(
            cards=cards,
            answer_product_ids=[],
            dropped_product_ids=[],
            reason="no_specific_answer_mentions",
        )

    id_to_card = {str(card.get("id", "")): card for card in cards}
    bound_cards = [id_to_card[item] for item in answer_product_ids if item in id_to_card]
    dropped_product_ids = [
        str(card.get("id", ""))
        for card in cards
        if str(card.get("id", "")) not in answer_product_ids
    ]
    return CardBindingResult(
        cards=bound_cards or cards,
        answer_product_ids=answer_product_ids,
        dropped_product_ids=dropped_product_ids if bound_cards else [],
        reason="answer_product_mentions" if bound_cards else "mentions_not_in_cards",
    )


def extract_answer_product_ids(answer: str, cards: list[dict]) -> list[str]:
    normalized_answer = normalize_match_text(answer)
    if not normalized_answer:
        return []

    product_mentions: list[tuple[int, int, str]] = []
    brand_to_ids = build_unique_brand_map(cards)
    for index, card in enumerate(cards):
        product_id = str(card.get("id", "")).strip()
        if not product_id:
            continue
        positions = mention_positions(normalized_answer, product_mention_terms(card))
        if not positions:
            brand = normalize_match_text(card.get("brand", ""))
            if brand and brand_to_ids.get(brand) == [product_id]:
                positions = mention_positions(normalized_answer, [brand])
        if positions:
            product_mentions.append((min(positions), index, product_id))

    product_ids: list[str] = []
    for _, _, product_id in sorted(product_mentions):
        if product_id not in product_ids:
            product_ids.append(product_id)
    return product_ids


def answer_declines_product_recommendation(answer: str) -> bool:
    normalized_answer = normalize_match_text(answer)
    if not normalized_answer:
        return False
    has_decline = any(normalize_match_text(term) in normalized_answer for term in DECLINE_RECOMMENDATION_TERMS)
    has_product_need = any(normalize_match_text(term) in normalized_answer for term in PRODUCT_NEED_TERMS)
    return has_decline and has_product_need


def product_mention_terms(card: dict) -> list[str]:
    terms = [
        str(card.get("id", "")).strip(),
        str(card.get("name", "")).strip(),
    ]
    evidence = card.get("evidence")
    if isinstance(evidence, dict):
        terms.extend(
            [
                str(evidence.get("product_id", "")).strip(),
                str(evidence.get("name", "")).strip(),
            ]
        )
    return [normalize_match_text(term) for term in terms if term]


def mention_positions(normalized_answer: str, normalized_terms: list[str]) -> list[int]:
    positions: list[int] = []
    for term in normalized_terms:
        if not term:
            continue
        position = normalized_answer.find(term)
        if position >= 0:
            positions.append(position)
    return positions


def build_unique_brand_map(cards: list[dict]) -> dict[str, list[str]]:
    brand_to_ids: dict[str, list[str]] = {}
    for card in cards:
        brand = normalize_match_text(card.get("brand", ""))
        product_id = str(card.get("id", "")).strip()
        if brand and product_id:
            brand_to_ids.setdefault(brand, []).append(product_id)
    return brand_to_ids


def normalize_match_text(value) -> str:
    text = unicodedata.normalize("NFKC", str(value)).lower()
    return re.sub(r"[\s\-_·・/\\]+", "", text)
