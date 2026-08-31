"""
Purpose: 电商业务 Handler：实现购物车操作、商品对比、场景组合推荐的具体处理逻辑。
"""

from collections.abc import AsyncIterator

from server.agent.bundle_grounding import BundleGroundingValidator
from server.agent.bundle_planner import BundlePlanner
from server.agent.bundle_ranker import BundleRanker
from server.agent.bundle_retriever import BundleRetriever
from server.agent.intent import UserIntent
from server.agent.responses import build_done_payload, stream_text
from server.agent.scenarios import ScenarioCatalog, build_bundle_answer, get_default_scenario_catalog
from server.agent.workflow import AgentTurnContext
from server.tools.product_compare import build_comparison_answer


class CartHandler:
    def matches(self, context: AgentTurnContext) -> bool:
        return bool(context.session.pending_cart_action) or context.plan.intent == "cart" or context.intent == UserIntent.CART

    async def handle(self, context: AgentTurnContext) -> AsyncIterator[dict]:
        result = context.registry.execute(
            "manage_cart",
            message=context.message,
            session=context.session,
            plan=context.plan,
        )
        async for item in stream_text(result["answer"]):
            yield item
        context.session.add_assistant_message(result["answer"])
        yield {"event": "cart_update", "data": result["cart"]}
        yield {
            "event": "done",
            "data": build_done_payload(
                context.session_id,
                context.session,
                needs_clarification=bool(result.get("needs_clarification", False)),
                plan=context.plan,
                trace_id=context.trace_id,
            ),
        }


class CompareHandler:
    def matches(self, context: AgentTurnContext) -> bool:
        return context.plan.intent == "compare" or context.intent == UserIntent.COMPARE

    async def handle(self, context: AgentTurnContext) -> AsyncIterator[dict]:
        result = context.registry.execute("compare_products", query=context.query, filters=context.filters)
        cards = result["cards"]
        comparison = result["comparison"]
        context.session.candidate_products = [card["id"] for card in cards]
        context.session.candidate_product_cards = cards

        answer = build_comparison_answer(comparison)
        async for item in stream_text(answer):
            yield item
        context.session.add_assistant_message(answer)
        for card in cards:
            yield {"event": "product_card", "data": card}
        yield {"event": "comparison_card", "data": comparison}
        yield {
            "event": "done",
            "data": build_done_payload(
                context.session_id,
                context.session,
                needs_clarification=False,
                plan=context.plan,
                trace_id=context.trace_id,
            ),
        }


class ScenarioBundleHandler:
    def __init__(self, catalog: ScenarioCatalog | None = None) -> None:
        self.catalog = catalog or get_default_scenario_catalog()
        self.bundle_planner = BundlePlanner()
        self.bundle_ranker = BundleRanker(inventory_required=True)
        self.bundle_grounding_validator = BundleGroundingValidator(inventory_required=True)

    def matches(self, context: AgentTurnContext) -> bool:
        return self.match(context) is not None

    async def handle(self, context: AgentTurnContext) -> AsyncIterator[dict]:
        match = self.match(context)
        if match is None:
            return
        bundle = match.bundle

        bundle_plan = self.bundle_planner.plan(bundle, user_filters=context.filters)
        retriever = BundleRetriever(context.registry.get("search_products"))
        candidates_by_slot = retriever.retrieve(bundle_plan)
        ranking = self.bundle_ranker.rank(bundle_plan, candidates_by_slot)
        grounding = self.bundle_grounding_validator.validate(
            candidates_by_slot=candidates_by_slot,
            ranking=ranking,
        )
        grouped_cards = ranking.grouped_cards
        cards: list[dict] = []
        for _, slot_cards in grouped_cards:
            cards.extend(slot_cards)

        context.session.candidate_products = [card["id"] for card in cards]
        context.session.candidate_product_cards = cards
        context.metadata["strategy"] = {
            "bundle_id": bundle.id,
            "catalog_version": bundle.source_version,
            "confidence": bundle.match_confidence,
            "signals": list(bundle.match_signals),
            "matched_terms": list(bundle.matched_terms),
            "governance": bundle.governance or {},
            "bundle_plan": bundle_plan.as_metadata(),
            "slot_retrieval": [item.as_metadata() for item in candidates_by_slot],
            "slot_count": len(bundle_plan.slots),
            "filled_slots": ranking.filled_slots,
            "missing_required_slots": ranking.missing_required_slots,
            "total_budget": context.filters.max_price,
            "selected_total_price": round(ranking.total_price, 2),
            "within_budget": ranking.within_budget,
            "bundle_ranking": ranking.as_metadata(),
            "bundle_grounding": grounding.as_metadata(),
        }
        if not grounding.safe:
            yield {
                "event": "guardrail",
                "data": {
                    "action": "bundle_grounding_warning",
                    "violations": list(grounding.violations),
                },
            }

        answer = build_bundle_answer(bundle_plan, grouped_cards)
        async for item in stream_text(answer):
            yield item
        context.session.add_assistant_message(answer)
        for card in cards:
            yield {"event": "product_card", "data": card}
        yield {
            "event": "done",
            "data": build_done_payload(
                context.session_id,
                context.session,
                needs_clarification=False,
                plan=context.plan,
                trace_id=context.trace_id,
            ),
        }

    def match(self, context: AgentTurnContext):
        if context.scenario_match is not None:
            return context.scenario_match

        decision = self.catalog.route_decision(
            context.message,
            plan=context.plan,
            filters=context.filters,
            session_id=context.session_id,
        )
        context.metadata["scenario_routing"] = decision.as_metadata()
        if decision.match is not None:
            context.scenario_match = decision.match
            return decision.match
        if context.query and context.query != context.message:
            decision = self.catalog.route_decision(
                context.query,
                plan=context.plan,
                filters=context.filters,
                session_id=context.session_id,
            )
            context.metadata["scenario_routing_query"] = decision.as_metadata()
            if decision.match is not None:
                context.scenario_match = decision.match
                return decision.match
        return None
