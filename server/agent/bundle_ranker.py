"""
Purpose: 组合方案排序器：按预算内、槽位覆盖度、总价等维度对组合方案排序。
"""

from __future__ import annotations

from dataclasses import dataclass

from server.agent.bundle_optimizer import BundleOptimizationResult, optimize_bundle_selection
from server.agent.bundle_schema import BundlePlan, Slot, SlotCandidateSet


@dataclass(frozen=True)
class BundleRankingResult:
    grouped_cards: list[tuple[Slot, list[dict]]]
    total_price: float
    within_budget: bool
    filled_slots: int
    missing_required_slots: list[str]
    filtered_unavailable_product_ids: tuple[str, ...] = ()

    def as_metadata(self) -> dict:
        return {
            "filled_slots": self.filled_slots,
            "missing_required_slots": list(self.missing_required_slots),
            "total_price": round(self.total_price, 2),
            "within_budget": self.within_budget,
            "filtered_unavailable_product_ids": list(self.filtered_unavailable_product_ids),
        }


class BundleRanker:
    """Bundle-level optimizer for diversity, budget, and inventory-safe selection."""

    def __init__(self, *, inventory_required: bool = True) -> None:
        self.inventory_required = inventory_required

    def rank(
        self,
        plan: BundlePlan,
        candidates_by_slot: tuple[SlotCandidateSet, ...],
    ) -> BundleRankingResult:
        filtered_sets, unavailable_ids = filter_unavailable_candidates(
            candidates_by_slot,
            enabled=self.inventory_required,
        )
        optimization = optimize_bundle_selection(
            [(item.slot, item.candidates) for item in filtered_sets],
            total_budget=parse_budget(plan.constraints.get("max_price")),
        )
        return ranking_result_from_optimization(
            optimization,
            filtered_unavailable_product_ids=tuple(unavailable_ids),
        )


def filter_unavailable_candidates(
    candidates_by_slot: tuple[SlotCandidateSet, ...],
    *,
    enabled: bool,
) -> tuple[tuple[SlotCandidateSet, ...], list[str]]:
    if not enabled:
        return candidates_by_slot, []
    filtered: list[SlotCandidateSet] = []
    unavailable_ids: list[str] = []
    for item in candidates_by_slot:
        kept: list[dict] = []
        for card in item.candidates:
            if card_stock(card) <= 0:
                unavailable_ids.append(str(card.get("id", "")))
                continue
            kept.append(card)
        filtered.append(SlotCandidateSet(slot=item.slot, candidates=kept, diagnostics=item.diagnostics))
    return tuple(filtered), unavailable_ids


def ranking_result_from_optimization(
    optimization: BundleOptimizationResult,
    *,
    filtered_unavailable_product_ids: tuple[str, ...] = (),
) -> BundleRankingResult:
    return BundleRankingResult(
        grouped_cards=optimization.grouped_cards,
        total_price=optimization.total_price,
        within_budget=optimization.within_budget,
        filled_slots=optimization.filled_slots,
        missing_required_slots=optimization.missing_required_slots,
        filtered_unavailable_product_ids=filtered_unavailable_product_ids,
    )


def parse_budget(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def card_stock(card: dict) -> int:
    try:
        return int(float(card.get("stock", 0) or 0))
    except (TypeError, ValueError):
        return 0
