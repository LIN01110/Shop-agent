"""
Purpose: 组合方案优化器：从各槽位候选商品中选出最优组合，满足预算和必需槽位约束。
"""

from dataclasses import dataclass
from itertools import product

from server.agent.scenarios import ScenarioSlot


@dataclass(frozen=True)
class BundleOptimizationResult:
    grouped_cards: list[tuple[ScenarioSlot, list[dict]]]
    total_price: float
    within_budget: bool
    filled_slots: int
    missing_required_slots: list[str]


def optimize_bundle_selection(
    grouped_candidates: list[tuple[ScenarioSlot, list[dict]]],
    *,
    total_budget: float | None = None,
) -> BundleOptimizationResult:
    if not grouped_candidates:
        return BundleOptimizationResult([], 0.0, True, 0, [])

    choices: list[list[dict | None]] = []
    for _, candidates in grouped_candidates:
        unique_candidates = dedupe_cards(candidates)
        choices.append([None, *unique_candidates])

    best_score: float | None = None
    best_selection: tuple[dict | None, ...] | None = None
    best_total = 0.0

    for selection in product(*choices):
        if has_duplicate_products(selection):
            continue
        total_price = sum(card_price(card) for card in selection if card is not None)
        if total_budget is not None and total_price > total_budget:
            continue
        score = score_selection(grouped_candidates, selection, total_price=total_price, total_budget=total_budget)
        if best_score is None or score > best_score:
            best_score = score
            best_selection = selection
            best_total = total_price

    if best_selection is None:
        best_selection = cheapest_feasible_fallback(grouped_candidates, total_budget=total_budget)
        best_total = sum(card_price(card) for card in best_selection if card is not None)

    grouped_cards: list[tuple[ScenarioSlot, list[dict]]] = []
    missing_required_slots: list[str] = []
    filled_slots = 0
    for (slot, _), card in zip(grouped_candidates, best_selection, strict=True):
        if card is None:
            grouped_cards.append((slot, []))
            if not slot.optional:
                missing_required_slots.append(slot.label)
            continue
        grouped_cards.append((slot, [card]))
        filled_slots += 1

    return BundleOptimizationResult(
        grouped_cards=grouped_cards,
        total_price=best_total,
        within_budget=total_budget is None or best_total <= total_budget,
        filled_slots=filled_slots,
        missing_required_slots=missing_required_slots,
    )


def score_selection(
    grouped_candidates: list[tuple[ScenarioSlot, list[dict]]],
    selection: tuple[dict | None, ...],
    *,
    total_price: float,
    total_budget: float | None,
) -> float:
    score = 0.0
    for (slot, _), card in zip(grouped_candidates, selection, strict=True):
        if card is None:
            if not slot.optional:
                score -= 2500.0
            continue
        score += 10000.0 if not slot.optional else 3500.0
        score += float(card.get("score", 0.0))
        score += max(slot.budget_weight, 0.0) * 10.0

    if total_budget is not None and total_budget > 0:
        budget_usage = total_price / total_budget
        if 0.55 <= budget_usage <= 0.98:
            score += 60.0
        score -= abs(0.8 - budget_usage) * 20.0
    return score


def cheapest_feasible_fallback(
    grouped_candidates: list[tuple[ScenarioSlot, list[dict]]],
    *,
    total_budget: float | None,
) -> tuple[dict | None, ...]:
    remaining = total_budget
    selection: list[dict | None] = []
    seen: set[str] = set()
    for _, candidates in grouped_candidates:
        selected = None
        for card in sorted(dedupe_cards(candidates), key=card_price):
            if card_id(card) in seen:
                continue
            price = card_price(card)
            if remaining is not None and price > remaining:
                continue
            selected = card
            seen.add(card_id(card))
            if remaining is not None:
                remaining -= price
            break
        selection.append(selected)
    return tuple(selection)


def has_duplicate_products(selection: tuple[dict | None, ...]) -> bool:
    ids = [card_id(card) for card in selection if card is not None]
    return len(ids) != len(set(ids))


def dedupe_cards(cards: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for card in cards:
        item_id = card_id(card)
        if item_id in seen:
            continue
        seen.add(item_id)
        unique.append(card)
    return unique


def card_id(card: dict | None) -> str:
    if card is None:
        return ""
    return str(card.get("id", ""))


def card_price(card: dict | None) -> float:
    if card is None:
        return 0.0
    try:
        return float(card.get("price", 0.0))
    except (TypeError, ValueError):
        return 0.0
