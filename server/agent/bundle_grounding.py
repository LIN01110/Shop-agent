"""
Purpose: 组合方案事实校验：校验场景组合方案中的每个商品是否 grounded 在真实商品库中。
"""

from __future__ import annotations

from dataclasses import dataclass

from server.agent.bundle_schema import SlotCandidateSet
from server.agent.bundle_ranker import BundleRankingResult, card_stock


@dataclass(frozen=True)
class BundleGroundingResult:
    safe: bool
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    product_ids: tuple[str, ...] = ()

    def as_metadata(self) -> dict:
        return {
            "safe": self.safe,
            "violations": list(self.violations),
            "warnings": list(self.warnings),
            "product_ids": list(self.product_ids),
        }


class BundleGroundingValidator:
    """Validate that bundle cards are grounded in retrieved catalog candidates."""

    def __init__(self, *, inventory_required: bool = True) -> None:
        self.inventory_required = inventory_required

    def validate(
        self,
        *,
        candidates_by_slot: tuple[SlotCandidateSet, ...],
        ranking: BundleRankingResult,
    ) -> BundleGroundingResult:
        allowed_by_slot = {
            item.slot.name: {str(card.get("id", "")) for card in item.candidates}
            for item in candidates_by_slot
        }
        violations: list[str] = []
        warnings: list[str] = []
        product_ids: list[str] = []
        for slot, cards in ranking.grouped_cards:
            allowed_ids = allowed_by_slot.get(slot.name, set())
            if slot.required and not cards:
                warnings.append(f"missing_required_slot:{slot.name}")
                continue
            if len(cards) < slot.min_items:
                warnings.append(f"slot_min_items_not_met:{slot.name}")
            if len(cards) > slot.max_items:
                violations.append(f"slot_max_items_exceeded:{slot.name}")
            for card in cards:
                product_id = str(card.get("id", "")).strip()
                product_ids.append(product_id)
                if not product_id:
                    violations.append(f"missing_product_id:{slot.name}")
                    continue
                if product_id not in allowed_ids:
                    violations.append(f"product_not_in_slot_candidates:{slot.name}:{product_id}")
                card_product_types = {str(item) for item in card.get("product_types", []) if str(item).strip()}
                if slot.candidate_product_types and not (card_product_types & set(slot.candidate_product_types)):
                    violations.append(f"product_type_not_in_slot:{slot.name}:{product_id}")
                if self.inventory_required and card_stock(card) <= 0:
                    violations.append(f"unavailable_product:{slot.name}:{product_id}")
                evidence = card.get("evidence")
                if not isinstance(evidence, dict):
                    violations.append(f"missing_evidence:{slot.name}:{product_id}")
                elif str(evidence.get("product_id", "")) != product_id:
                    violations.append(f"evidence_product_mismatch:{slot.name}:{product_id}")
                elif not evidence_supports_slot(evidence, slot):
                    warnings.append(f"weak_slot_evidence:{slot.name}:{product_id}")
        if len(product_ids) != len(set(product_ids)):
            violations.append("duplicate_product_in_bundle")
        return BundleGroundingResult(
            safe=not violations,
            violations=tuple(dict.fromkeys(violations)),
            warnings=tuple(dict.fromkeys(warnings)),
            product_ids=tuple(product_ids),
        )


def evidence_supports_slot(evidence: dict, slot) -> bool:
    if not slot.candidate_product_types:
        return True
    evidence_types = set()
    for value in evidence.get("product_types", []) or []:
        if str(value).strip():
            evidence_types.add(str(value).strip())
    if evidence_types & set(slot.candidate_product_types):
        return True
    field_sources = evidence.get("field_sources")
    return isinstance(field_sources, dict) and bool(field_sources)
