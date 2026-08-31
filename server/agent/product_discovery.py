"""
Purpose: 商品发现策略：根据 SemanticPlan 决定展示模式（单件/列表）、候选池大小、是否允许 LLM 回答。
"""

from dataclasses import dataclass, field

from server.agent.card_binding import bind_cards_to_answer
from server.agent.semantic_schema import SemanticPlan


DEFAULT_RECOMMENDATION_TOP_K = 5
DEFAULT_LISTING_TOP_K = 20


@dataclass(frozen=True)
class ProductDiscoveryPolicy:
    presentation_mode: str = "auto"
    top_k: int = DEFAULT_RECOMMENDATION_TOP_K
    allow_llm_answer: bool = True
    bind_cards_to_answer: bool = True
    policy_reasons: tuple[str, ...] = ()

    def as_metadata(self) -> dict[str, object]:
        return {
            "presentation_mode": self.presentation_mode,
            "top_k": self.top_k,
            "allow_llm_answer": self.allow_llm_answer,
            "bind_cards_to_answer": self.bind_cards_to_answer,
            "policy_reasons": list(self.policy_reasons),
        }


@dataclass(frozen=True)
class ProductCardResolution:
    cards: list[dict]
    metadata: dict[str, object] = field(default_factory=dict)


def build_product_discovery_policy(plan: SemanticPlan) -> ProductDiscoveryPolicy:
    mode = plan.presentation_mode or "auto"
    query_understanding = plan.query_understanding if isinstance(plan.query_understanding, dict) else {}
    if mode == "listing":
        return ProductDiscoveryPolicy(
            presentation_mode="listing",
            top_k=coerce_top_k(query_understanding.get("recommended_top_k"), DEFAULT_LISTING_TOP_K),
            allow_llm_answer=False,
            bind_cards_to_answer=False,
            policy_reasons=("catalog_listing",),
        )
    if mode == "single":
        return ProductDiscoveryPolicy(
            presentation_mode="single",
            top_k=DEFAULT_RECOMMENDATION_TOP_K,
            allow_llm_answer=True,
            bind_cards_to_answer=True,
            policy_reasons=("single_product_discovery",),
        )
    return ProductDiscoveryPolicy(policy_reasons=("default_product_discovery",))


def resolve_product_cards_for_answer(
    *,
    answer: str,
    cards: list[dict],
    policy: ProductDiscoveryPolicy,
) -> ProductCardResolution:
    ordered_cards = order_cards_by_answer_mentions(answer, cards)
    binding = bind_cards_to_answer(answer, ordered_cards)
    if not policy.bind_cards_to_answer:
        return ProductCardResolution(
            cards=ordered_cards,
            metadata={
                "reason": "policy_keeps_filtered_cards",
                "answer_product_ids": binding.answer_product_ids,
                "emitted_product_ids": product_ids(ordered_cards),
                "dropped_product_ids": [],
            },
        )
    return ProductCardResolution(
        cards=binding.cards,
        metadata={
            "reason": binding.reason,
            "answer_product_ids": binding.answer_product_ids,
            "emitted_product_ids": product_ids(binding.cards),
            "dropped_product_ids": binding.dropped_product_ids,
            "card_binding_mismatch": bool(binding.dropped_product_ids),
        },
    )


def order_cards_by_answer_mentions(answer: str, cards: list[dict]) -> list[dict]:
    if not answer.strip() or len(cards) <= 1:
        return cards

    normalized_answer = answer.lower()

    def mention_position(card: dict, index: int) -> tuple[int, int]:
        positions = []
        for value in [str(card.get("name", "")).strip(), str(card.get("brand", "")).strip()]:
            if not value:
                continue
            position = normalized_answer.find(value.lower())
            if position >= 0:
                positions.append(position)
        if not positions:
            return (10_000_000, index)
        return (min(positions), index)

    return [
        card
        for _, card in sorted(
            enumerate(cards),
            key=lambda item: mention_position(item[1], item[0]),
        )
    ]


def coerce_top_k(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, 50))


def product_ids(cards: list[dict]) -> list[str]:
    return [str(card.get("id", "")) for card in cards]
