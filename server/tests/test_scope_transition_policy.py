"""
Purpose: 范围切换策略测试：验证品类切换时清理旧过滤条件。
"""

from server.agent.scope_transition import ScopeTransitionPolicy
from server.agent.semantic_schema import SemanticPlan
from server.session.state import FilterCondition, SessionState


def fc(kind: str, value: str) -> FilterCondition:
    return FilterCondition(kind=kind, value=value)


def plan(intent: str = "recommend") -> SemanticPlan:
    return SemanticPlan(intent=intent)


def apply_turn(
    policy: ScopeTransitionPolicy,
    session: SessionState,
    message: str,
    semantic_plan: SemanticPlan,
    incoming: list[FilterCondition],
):
    transition = policy.decide(message, semantic_plan, session, incoming)
    policy.apply(session, transition)
    session.merge_filters(incoming, auto_scope_reset=False)
    return transition


def filter_pairs(session: SessionState) -> set[tuple[str, str]]:
    return {(item.kind, item.value) for item in session.filters}


def test_ambiguous_query_without_scope_is_marked_uncertain() -> None:
    policy = ScopeTransitionPolicy()
    session = SessionState(session_id="scope-uncertain")

    transition = apply_turn(
        policy,
        session,
        "\u6709\u6ca1\u6709\u706b\u7bad\u53d1\u52a8\u673a",
        plan(),
        [],
    )

    assert transition.transition_type == "uncertain"
    assert session.filters == []
    assert session.candidate_products == []


def test_same_product_scope_fresh_request_releases_stale_budget() -> None:
    policy = ScopeTransitionPolicy()
    session = SessionState(session_id="scope-same-fresh")
    session.merge_filters(
        [
            fc("product_type", "clothes.sports_shoes"),
            fc("max_price", "200"),
        ]
    )
    session.candidate_products = ["p_clothes_001"]
    session.candidate_product_cards = [{"id": "p_clothes_001"}]

    transition = apply_turn(
        policy,
        session,
        "\u63a8\u8350\u4e00\u6b3e\u8fd0\u52a8\u978b",
        plan(),
        [fc("product_type", "clothes.sports_shoes")],
    )

    assert transition.transition_type == "replace_product_scope"
    assert filter_pairs(session) == {("product_type", "clothes.sports_shoes")}
    assert session.candidate_products == []
    assert session.candidate_product_cards == []


def test_follow_up_constraint_without_new_scope_keeps_current_product_context() -> None:
    policy = ScopeTransitionPolicy()
    session = SessionState(session_id="scope-refine")
    session.merge_filters([fc("product_type", "clothes.sports_shoes")])
    session.candidate_products = ["p_clothes_001"]

    transition = apply_turn(
        policy,
        session,
        "\u4e0d\u8981 lining",
        plan(),
        [fc("exclude_brand", "\u674e\u5b81")],
    )

    assert transition.transition_type == "continue_refinement"
    assert ("product_type", "clothes.sports_shoes") in filter_pairs(session)
    assert ("exclude_brand", "\u674e\u5b81") in filter_pairs(session)
    assert session.candidate_products == ["p_clothes_001"]


def test_unrelated_product_scope_replaces_previous_scope() -> None:
    policy = ScopeTransitionPolicy()
    session = SessionState(session_id="scope-replace")
    session.merge_filters(
        [
            fc("product_type", "beauty.lipstick"),
            fc("brand", "\u5df4\u9ece\u6b27\u83b1\u96c5"),
        ]
    )
    session.candidate_products = ["p_beauty_999"]

    transition = apply_turn(
        policy,
        session,
        "\u63a8\u8350\u4e00\u6b3e\u8fd0\u52a8\u978b",
        plan(),
        [fc("product_type", "clothes.sports_shoes")],
    )

    assert transition.transition_type == "replace_product_scope"
    assert filter_pairs(session) == {("product_type", "clothes.sports_shoes")}
    assert session.candidate_products == []


def test_scenario_single_product_request_does_not_become_bundle_transition() -> None:
    policy = ScopeTransitionPolicy()
    session = SessionState(session_id="scope-scenario-single")
    session.merge_filters([fc("product_type", "beauty.lipstick")])

    transition = apply_turn(
        policy,
        session,
        "\u4e09\u4e9a\u9002\u5408\u4ec0\u4e48\u9632\u6652",
        plan(),
        [fc("product_type", "beauty.sunscreen")],
    )

    assert transition.transition_type == "replace_product_scope"
    assert filter_pairs(session) == {("product_type", "beauty.sunscreen")}


def test_bundle_new_task_clears_previous_single_product_scope() -> None:
    policy = ScopeTransitionPolicy()
    session = SessionState(session_id="scope-bundle-new")
    session.merge_filters(
        [
            fc("product_type", "beauty.lipstick"),
            fc("brand", "\u5df4\u9ece\u6b27\u83b1\u96c5"),
        ]
    )
    session.candidate_products = ["p_beauty_999"]
    session.candidate_product_cards = [{"id": "p_beauty_999"}]

    transition = apply_turn(
        policy,
        session,
        "\u63a8\u8350\u4e00\u5957\u4e09\u4e9a\u7684\u65c5\u884c\u88c5\u5907",
        plan("bundle"),
        [],
    )

    assert transition.transition_type == "bundle_new_task"
    assert session.filters == []
    assert session.exclusions == []
    assert session.candidate_products == []
    assert session.candidate_product_cards == []


def test_seeded_bundle_preserves_previous_candidate_as_seed() -> None:
    policy = ScopeTransitionPolicy()
    session = SessionState(session_id="scope-bundle-seed")
    session.merge_filters([fc("product_type", "clothes.sports_shoes")])
    session.candidate_products = ["p_clothes_001"]
    session.candidate_product_cards = [{"id": "p_clothes_001"}]
    seeded_plan = SemanticPlan(intent="bundle", reference_type="last")

    transition = apply_turn(
        policy,
        session,
        "\u7ed9\u5b83\u642d\u4e00\u5957\u5065\u8eab\u88c5\u5907",
        seeded_plan,
        [],
    )

    assert transition.transition_type == "bundle_with_seed"
    assert session.filters == []
    assert session.exclusions == []
    assert session.candidate_products == ["p_clothes_001"]
    assert session.candidate_product_cards == [{"id": "p_clothes_001"}]


def test_expand_scope_preserves_existing_scope_and_adds_new_scope() -> None:
    policy = ScopeTransitionPolicy()
    session = SessionState(session_id="scope-expand")
    session.merge_filters([fc("product_type", "beauty.face_cream")])
    session.candidate_products = ["p_beauty_001"]

    transition = apply_turn(
        policy,
        session,
        "\u4e5f\u770b\u770b\u7cbe\u534e",
        plan(),
        [fc("product_type", "beauty.serum")],
    )

    assert transition.transition_type == "expand_product_scope"
    assert ("product_type", "beauty.face_cream") in filter_pairs(session)
    assert ("product_type", "beauty.serum") in filter_pairs(session)
    assert session.candidate_products == []


def test_category_to_product_type_refinement_keeps_compatible_constraints() -> None:
    policy = ScopeTransitionPolicy()
    session = SessionState(session_id="scope-category-refine")
    session.merge_filters(
        [
            fc("category", "beauty.skincare"),
            fc("max_price", "500"),
        ]
    )
    session.candidate_products = ["p_beauty_001"]

    transition = apply_turn(
        policy,
        session,
        "\u53ea\u770b\u6d01\u9762\u6ce1\u6cab",
        plan(),
        [fc("product_type", "beauty.cleanser")],
    )

    assert transition.transition_type == "refine_product_scope"
    assert ("category", "beauty.skincare") in filter_pairs(session)
    assert ("product_type", "beauty.cleanser") in filter_pairs(session)
    assert ("max_price", "500") in filter_pairs(session)
    assert session.candidate_products == []


def test_cart_action_preserves_candidates_and_filters() -> None:
    policy = ScopeTransitionPolicy()
    session = SessionState(session_id="scope-cart")
    session.merge_filters([fc("product_type", "clothes.sports_shoes")])
    session.candidate_products = ["p_clothes_001"]
    cart_plan = SemanticPlan(intent="cart", cart_action="add", reference_type="ordinal", reference_index=1)

    transition = apply_turn(
        policy,
        session,
        "\u628a\u7b2c\u4e00\u6b3e\u52a0\u5230\u8d2d\u7269\u8f66",
        cart_plan,
        [],
    )

    assert transition.transition_type == "cart_action"
    assert filter_pairs(session) == {("product_type", "clothes.sports_shoes")}
    assert session.candidate_products == ["p_clothes_001"]


def test_scope_transition_metadata_is_state_machine_shaped() -> None:
    policy = ScopeTransitionPolicy()
    session = SessionState(session_id="scope-metadata")
    session.merge_filters([fc("product_type", "beauty.lipstick")])

    transition = policy.decide(
        "\u63a8\u8350\u4e00\u5957\u4e09\u4e9a\u7684\u65c5\u884c\u88c5\u5907",
        plan("bundle"),
        session,
        [],
    )
    metadata = transition.as_metadata()

    assert metadata["from_scope"] == "single_product_recommendation"
    assert metadata["to_scope"] == "bundle_recommendation"
    assert "filters" in metadata["clear"]
    assert "cart" in metadata["preserve"]
