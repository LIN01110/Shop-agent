"""
Purpose: 组合方案规划器：将场景化请求（如三亚度假）转换为可执行的多槽位购物计划。
"""

from __future__ import annotations

from server.agent.bundle_schema import BundlePlan, Slot
from server.agent.scenario_models import ScenarioBundle, ScenarioSlot
from server.rag.post_process import SearchFilters


class BundlePlanner:
    """Convert a routed scenario into an executable multi-slot shopping plan."""

    def plan(self, bundle: ScenarioBundle, *, user_filters: SearchFilters) -> BundlePlan:
        slots = tuple(slot_from_scenario_slot(slot) for slot in bundle.slots)
        required_slots = tuple(slot for slot in slots if slot.required)
        optional_slots = tuple(slot for slot in slots if not slot.required)
        return BundlePlan(
            scenario=bundle.id,
            bundle_type="scenario_bundle",
            title=bundle.title,
            summary=bundle.summary,
            required_slots=required_slots,
            optional_slots=optional_slots,
            constraints=build_bundle_constraints(user_filters),
            user_context_used={
                "matched_terms": list(bundle.matched_terms),
                "signals": list(bundle.match_signals),
                "context_variables": dict(bundle.context_variables),
            },
            matched_terms=bundle.matched_terms,
            source_version=bundle.source_version,
            confidence=bundle.match_confidence,
            signals=bundle.match_signals,
            governance=bundle.governance,
        )


def slot_from_scenario_slot(slot: ScenarioSlot) -> Slot:
    return Slot(
        name=slot.label,
        role=slot.label,
        semantic_query=slot.query,
        candidate_product_types=tuple(slot.filters.product_types),
        required=not slot.optional,
        min_items=0 if slot.optional else 1,
        max_items=slot.max_items,
        filters=slot.filters,
        candidate_pool_size=slot.candidate_pool_size,
        budget_weight=slot.budget_weight,
    )


def build_bundle_constraints(filters: SearchFilters) -> dict[str, object]:
    return {
        "min_price": filters.min_price,
        "max_price": filters.max_price,
        "excluded_brands": list(filters.excluded_brands),
        "exclusions": list(filters.exclusions),
        "in_stock_only": filters.in_stock_only,
        "unsupported_constraints": list(filters.unsupported_constraints),
    }
