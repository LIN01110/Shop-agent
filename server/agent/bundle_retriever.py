"""
Purpose: 组合方案检索器：为每个槽位独立检索候选商品，返回 SlotCandidateSet。
"""

from __future__ import annotations

from server.agent.bundle_schema import BundlePlan, Slot, SlotCandidateSet


class BundleRetriever:
    """Retrieve grounded candidates independently for every bundle slot."""

    def __init__(self, product_search_tool) -> None:
        self.product_search_tool = product_search_tool

    def retrieve(self, plan: BundlePlan) -> tuple[SlotCandidateSet, ...]:
        return tuple(self.retrieve_slot(slot) for slot in plan.slots)

    def retrieve_slot(self, slot: Slot) -> SlotCandidateSet:
        top_k = max(slot.candidate_pool_size, slot.max_items)
        if hasattr(self.product_search_tool, "run_with_diagnostics"):
            result = self.product_search_tool.run_with_diagnostics(
                query=slot.semantic_query,
                filters=slot.filters,
                top_k=top_k,
            )
            return SlotCandidateSet(slot=slot, candidates=result.cards, diagnostics=result.diagnostics)
        cards = self.product_search_tool.run(
            query=slot.semantic_query,
            filters=slot.filters,
            top_k=top_k,
        )
        return SlotCandidateSet(slot=slot, candidates=cards)
