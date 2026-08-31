"""
答辩重点 🟠（防幻觉）
任务：Day 2 任务 2.4 — 理解 GroundingGuard 4 条校验规则。
核心：LLM 回答先缓冲再校验，触发 guardrail 后改用确定性模板回答。
      拦截：未提供的优惠/折扣/库存/销量/好评率；候选/预算外的价格；
            没有引用任何候选商品名称/品牌的回答；绝对化/医疗化承诺（“保证”“根治”“100%”）。
高频追问：
  - “为什么 LLM 回答要先缓冲再校验，而不是直接流式转发？”
    → 需要拿到完整回答才能做事实一致性检查
  - “举例说明什么情况下会触发 guardrail 降级？”
    → LLM 编造库存/价格、引用不存在品牌、绝对化功效承诺
"""

import re
from dataclasses import dataclass, field

# 匹配【候选N】格式，支持【候选 1】有空格的情况
CANDIDATE_INDEX_PATTERN = re.compile(r'【候选\s*(\d+)】')


PROMOTION_TERMS = (
    "优惠",
    "优惠券",
    "券",
    "折扣",
    "满减",
    "促销",
    "活动价",
    "秒杀",
    "立减",
    "返现",
    "赠品",
    "包邮",
)

UNSUPPORTED_BUSINESS_TERMS = (
    "库存充足",
    "现货",
    "销量",
    "月销",
    "好评率",
    "官方认证",
    "正品保障",
)

ABSOLUTE_OR_MEDICAL_CLAIMS = (
    "保证",
    "100%",
    "百分百",
    "根治",
    "治愈",
    "无副作用",
    "永久",
    "全网第一",
    "行业第一",
)

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

PRICE_PATTERN = re.compile(r"(?:[¥￥]\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*元)")


@dataclass(frozen=True)
class GroundingResult:
    answer: str
    safe: bool
    violations: list[str] = field(default_factory=list)
    action: str = "pass"
    citations: list[dict] = field(default_factory=list)


def guard_grounded_answer(
    *,
    answer: str,
    cards: list[dict],
    fallback_answer: str,
    allowed_extra_prices: list[float] | None = None,
) -> GroundingResult:
    violations = detect_grounding_violations(
        answer=answer,
        cards=cards,
        allowed_extra_prices=allowed_extra_prices or [],
    )
    if not violations:
        return GroundingResult(answer=answer, safe=True, citations=build_answer_citations(answer, cards))
    return GroundingResult(
        answer=fallback_answer,
        safe=False,
        violations=violations,
        action="fallback",
        citations=build_answer_citations(fallback_answer, cards),
    )


def detect_grounding_violations(
    *,
    answer: str,
    cards: list[dict],
    allowed_extra_prices: list[float],
) -> list[str]:
    if not answer.strip():
        return ["empty_answer"]

    violations: list[str] = []
    matched_terms = terms_in_text(answer, PROMOTION_TERMS)
    if matched_terms:
        violations.append(f"unsupported_promotion_terms:{','.join(matched_terms)}")

    matched_terms = terms_in_text(answer, UNSUPPORTED_BUSINESS_TERMS)
    if matched_terms:
        violations.append(f"unsupported_business_terms:{','.join(matched_terms)}")

    matched_terms = terms_in_text(answer, ABSOLUTE_OR_MEDICAL_CLAIMS)
    if matched_terms:
        violations.append(f"unsupported_absolute_or_medical_claims:{','.join(matched_terms)}")

    unsupported_prices = prices_not_grounded(answer, cards, allowed_extra_prices)
    if unsupported_prices:
        violations.append(f"unsupported_prices:{','.join(unsupported_prices)}")

    if cards and not references_any_candidate(answer, cards) and not answer_declines_product_recommendation(answer):
        violations.append("missing_candidate_reference")

    return violations


def answer_declines_product_recommendation(answer: str) -> bool:
    normalized = normalize_guard_text(answer)
    if not normalized:
        return False
    has_decline = any(normalize_guard_text(term) in normalized for term in DECLINE_RECOMMENDATION_TERMS)
    has_product_need = any(normalize_guard_text(term) in normalized for term in PRODUCT_NEED_TERMS)
    return has_decline and has_product_need


def normalize_guard_text(value) -> str:
    return re.sub(r"\s+", "", str(value)).lower()


def terms_in_text(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term in text]


def prices_not_grounded(answer: str, cards: list[dict], allowed_extra_prices: list[float]) -> list[str]:
    allowed = {normalize_price(candidate_fact(card, "price", 0)) for card in cards}
    allowed.update(normalize_price(value) for value in allowed_extra_prices)

    unsupported: list[str] = []
    for match in PRICE_PATTERN.finditer(answer):
        raw = match.group(1) or match.group(2) or ""
        if normalize_price(raw) not in allowed:
            unsupported.append(raw)
    return unsupported


def normalize_price(value) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return -1


def references_any_candidate(answer: str, cards: list[dict]) -> bool:
    normalized_answer = answer.lower()
    for card in cards:
        name = str(candidate_fact(card, "name", "")).strip()
        brand = str(candidate_fact(card, "brand", "")).strip()
        if name and name in answer:
            return True
        if brand and brand.lower() in normalized_answer:
            return True
    return False


def candidate_fact(card: dict, field: str, default=None):
    evidence = card.get("evidence")
    if isinstance(evidence, dict) and field in evidence:
        return evidence[field]
    if field == "product_id":
        return card.get("id", default)
    return card.get(field, default)


def build_answer_citations(answer: str, cards: list[dict]) -> list[dict]:
    citations: list[dict] = []
    for card in cards:
        product_id = candidate_fact(card, "product_id", "")
        name = str(candidate_fact(card, "name", "")).strip()
        brand = str(candidate_fact(card, "brand", "")).strip()
        price = candidate_fact(card, "price", None)
        cited_fields: list[str] = []

        if name and name in answer:
            cited_fields.append("name")
        if brand and brand.lower() in answer.lower():
            cited_fields.append("brand")
        if price is not None and normalize_price(price) in mentioned_prices(answer):
            cited_fields.append("price")

        if cited_fields:
            citations.append(
                {
                    "source": "product_catalog",
                    "product_id": product_id,
                    "fields": cited_fields,
                }
            )
    return citations


def mentioned_prices(answer: str) -> set[int]:
    prices: set[int] = set()
    for match in PRICE_PATTERN.finditer(answer):
        raw = match.group(1) or match.group(2) or ""
        prices.add(normalize_price(raw))
    return prices


# ── 序号引用校验（防ID幻觉）──

def parse_indexed_citations(
    answer: str,
    index_to_id: dict[int, str],
    cards: list[dict],
) -> tuple[list[str], list[dict]]:
    """
    解析LLM输出中的【候选N】引用。

    Returns:
        (编造序号列表, 有效引用详情列表)
    """
    max_valid = len(cards)
    hallucinated: list[str] = []
    citations: list[dict] = []
    seen_indices = set()

    for match in CANDIDATE_INDEX_PATTERN.finditer(answer):
        idx = int(match.group(1))

        if idx < 1 or idx > max_valid:
            hallucinated.append(f"候选{idx}(越界，最大{max_valid})")
            continue

        real_id = index_to_id.get(idx)
        if not real_id:
            hallucinated.append(f"候选{idx}(无对应ID)")
            continue

        if idx in seen_indices:
            continue
        seen_indices.add(idx)

        card = cards[idx - 1]
        citations.append({
            "index": idx,
            "product_id": real_id,
            "name": card.get("name", ""),
            "brand": card.get("brand", ""),
            "price": card.get("price"),
        })

    # 如果候选存在但一个都没引用，且不是在拒绝推荐 → 可能是自由发挥
    if cards and not citations and not answer_declines_product_recommendation(answer):
        hallucinated.append("缺少候选引用(未使用【候选N】格式)")

    return hallucinated, citations


def guard_indexed_answer(
    *,
    answer: str,
    index_to_id: dict[int, str],
    cards: list[dict],
    fallback_answer: str,
    allowed_extra_prices: list[float] | None = None,
) -> GroundingResult:
    """
    对使用【候选N】引用的LLM输出做完整校验。
    先跑序号白名单，再跑原有规则。
    """
    # Layer 1: 序号引用校验
    hallucinated, citations = parse_indexed_citations(answer, index_to_id, cards)
    violations = [f"hallucinated_index:{h}" for h in hallucinated]

    # Layer 2: 原有规则（价格、禁用词、品牌引用等）
    existing = detect_grounding_violations(
        answer=answer,
        cards=cards,
        allowed_extra_prices=allowed_extra_prices or [],
    )
    violations.extend(existing)

    if violations:
        return GroundingResult(
            answer=fallback_answer,
            safe=False,
            violations=violations,
            action="fallback",
            citations=citations,
        )

    return GroundingResult(
        answer=answer,
        safe=True,
        citations=citations,
    )
