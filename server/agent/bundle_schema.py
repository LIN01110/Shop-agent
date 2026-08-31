"""
Purpose: 组合方案数据结构：定义 BundlePlan、Slot、SlotCandidateSet 等核心数据类。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from server.rag.post_process import SearchFilters


@dataclass(frozen=True)
class Slot:
    name: str
    role: str
    semantic_query: str
    candidate_product_types: tuple[str, ...]
    required: bool
    min_items: int
    max_items: int
    filters: SearchFilters
    candidate_pool_size: int = 3
    budget_weight: float = 1.0

    @property
    def label(self) -> str:
        return self.name

    @property
    def optional(self) -> bool:
        return not self.required

    @property
    def query(self) -> str:
        return self.semantic_query


@dataclass(frozen=True)
class BundlePlan:
    scenario: str
    bundle_type: str
    title: str
    summary: str
    required_slots: tuple[Slot, ...]
    optional_slots: tuple[Slot, ...] = ()
    constraints: dict[str, object] = field(default_factory=dict)
    user_context_used: dict[str, object] = field(default_factory=dict)
    matched_terms: tuple[str, ...] = ()
    source_version: str = ""
    confidence: float = 0.0
    signals: tuple[str, ...] = ()
    governance: dict[str, object] | None = None

    @property
    def slots(self) -> tuple[Slot, ...]:
        return (*self.required_slots, *self.optional_slots)

    def as_metadata(self) -> dict:
        return {
            "scenario": self.scenario,
            "bundle_type": self.bundle_type,
            "title": self.title,
            "required_slots": [slot_metadata(slot) for slot in self.required_slots],
            "optional_slots": [slot_metadata(slot) for slot in self.optional_slots],
            "constraints": self.constraints,
            "user_context_used": self.user_context_used,
            "matched_terms": list(self.matched_terms),
            "source_version": self.source_version,
            "confidence": self.confidence,
            "signals": list(self.signals),
            "governance": self.governance or {},
        }


@dataclass(frozen=True)
class SlotCandidateSet:
    slot: Slot
    candidates: list[dict]
    diagnostics: object | None = None

    def as_metadata(self) -> dict:
        return {
            "slot": slot_metadata(self.slot),
            "candidate_count": len(self.candidates),
            "candidate_product_ids": [str(card.get("id", "")) for card in self.candidates],
            "diagnostics": diagnostics_metadata(self.diagnostics),
        }


def slot_metadata(slot: Slot) -> dict:
    return {
        "name": slot.name,
        "role": slot.role,
        "semantic_query": slot.semantic_query,
        "candidate_product_types": list(slot.candidate_product_types),
        "required": slot.required,
        "min_items": slot.min_items,
        "max_items": slot.max_items,
        "candidate_pool_size": slot.candidate_pool_size,
        "budget_weight": slot.budget_weight,
        "filters": {
            "max_price": slot.filters.max_price,
            "keywords": list(slot.filters.keywords),
            "product_types": list(slot.filters.product_types),
            "exclusions": list(slot.filters.exclusions),
            "in_stock_only": slot.filters.in_stock_only,
        },
    }


def diagnostics_metadata(diagnostics: object | None) -> dict | None:
    if diagnostics is None:
        return None
    if hasattr(diagnostics, "__dataclass_fields__"):
        from dataclasses import asdict

        return asdict(diagnostics)
    if isinstance(diagnostics, dict):
        return diagnostics
    return {"repr": repr(diagnostics)}
