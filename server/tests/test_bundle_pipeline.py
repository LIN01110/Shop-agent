"""
Purpose: 组合 Pipeline 测试：验证场景组合端到端流程。
"""

from server.agent.bundle_grounding import BundleGroundingValidator
from server.agent.bundle_planner import BundlePlanner
from server.agent.bundle_ranker import BundleRanker
from server.agent.bundle_retriever import BundleRetriever
from server.agent.scenarios import detect_scenario_bundle
from server.rag.post_process import SearchFilters


def card(
    product_id: str,
    *,
    price: float = 100,
    stock: int = 5,
    score: float = 1.0,
    product_types: list[str] | None = None,
) -> dict:
    return {
        "id": product_id,
        "name": product_id,
        "price": price,
        "stock": stock,
        "product_types": product_types or [],
        "score": score,
        "evidence": {"product_id": product_id, "product_types": product_types or []},
    }


class FakeSearchTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, *, query: str, filters: SearchFilters, top_k: int) -> list[dict]:
        self.calls.append({"query": query, "filters": filters, "top_k": top_k})
        return [card(f"{len(self.calls)}-a"), card(f"{len(self.calls)}-b")]


def test_bundle_planner_builds_slot_schema_from_scenario_bundle() -> None:
    bundle = detect_scenario_bundle("马尔代夫度假，帮我配一套")
    assert bundle is not None

    plan = BundlePlanner().plan(bundle, user_filters=SearchFilters(max_price=1500))

    assert plan.scenario == "sanya_trip"
    assert plan.bundle_type == "scenario_bundle"
    assert plan.title == "马尔代夫度假组合方案"
    assert plan.constraints["max_price"] == 1500
    assert plan.user_context_used["context_variables"]["destination"] == "马尔代夫"
    assert plan.required_slots
    assert all(slot.required for slot in plan.required_slots)
    assert plan.required_slots[0].candidate_product_types == ("beauty.sunscreen",)


def test_bundle_retriever_runs_independent_search_per_slot() -> None:
    bundle = detect_scenario_bundle("马尔代夫度假，帮我配一套")
    assert bundle is not None
    plan = BundlePlanner().plan(bundle, user_filters=SearchFilters())
    tool = FakeSearchTool()

    candidate_sets = BundleRetriever(tool).retrieve(plan)

    assert len(candidate_sets) == len(plan.slots)
    assert len(tool.calls) == len(plan.slots)
    assert all(item.candidates for item in candidate_sets)
    assert tool.calls[0]["query"] == plan.slots[0].semantic_query
    assert tool.calls[0]["top_k"] >= plan.slots[0].max_items


def test_bundle_ranker_filters_unavailable_candidates_before_optimization() -> None:
    bundle = detect_scenario_bundle("马尔代夫度假，帮我配一套")
    assert bundle is not None
    plan = BundlePlanner().plan(bundle, user_filters=SearchFilters(max_price=500))
    candidate_sets = []
    for slot in plan.slots[:2]:
        candidate_sets.append(
            type("SlotCandidateSetLike", (), {})()
        )
        candidate_sets[-1].slot = slot
        candidate_sets[-1].candidates = [
            card(f"{slot.name}-oos", price=50, stock=0, score=100),
            card(f"{slot.name}-ok", price=50, stock=2, score=1),
        ]
        candidate_sets[-1].diagnostics = None

    ranking = BundleRanker(inventory_required=True).rank(plan, tuple(candidate_sets))
    selected_ids = [cards[0]["id"] for _, cards in ranking.grouped_cards if cards]

    assert all(item.endswith("-ok") for item in selected_ids)
    assert ranking.filtered_unavailable_product_ids


def test_bundle_grounding_validator_blocks_products_outside_slot_candidates() -> None:
    bundle = detect_scenario_bundle("马尔代夫度假，帮我配一套")
    assert bundle is not None
    plan = BundlePlanner().plan(bundle, user_filters=SearchFilters())
    slot = plan.slots[0]
    candidates = type("SlotCandidateSetLike", (), {})()
    candidates.slot = slot
    candidates.candidates = [card("allowed")]
    ranking = type("RankingLike", (), {})()
    ranking.grouped_cards = [(slot, [card("outside")])]

    result = BundleGroundingValidator().validate(
        candidates_by_slot=(candidates,),
        ranking=ranking,
    )

    assert result.safe is False
    assert "product_not_in_slot_candidates:防晒保护:outside" in result.violations


def test_bundle_grounding_validator_blocks_slot_product_type_drift() -> None:
    bundle = detect_scenario_bundle("马尔代夫度假，帮我配一套")
    assert bundle is not None
    plan = BundlePlanner().plan(bundle, user_filters=SearchFilters())
    slot = plan.slots[0]
    wrong_card = card("wrong-type", product_types=["food.coffee"])
    candidates = type("SlotCandidateSetLike", (), {})()
    candidates.slot = slot
    candidates.candidates = [wrong_card]
    ranking = type("RankingLike", (), {})()
    ranking.grouped_cards = [(slot, [wrong_card])]

    result = BundleGroundingValidator().validate(
        candidates_by_slot=(candidates,),
        ranking=ranking,
    )

    assert result.safe is False
    assert f"product_type_not_in_slot:{slot.name}:wrong-type" in result.violations
