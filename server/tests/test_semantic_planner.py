"""
Purpose: 语义规划器测试：验证规则 fallback 和 LLM plan 合并。
"""

import asyncio
from collections.abc import AsyncIterator

from server.agent.planning_policy import validate_semantic_plan
from server.agent.semantic_llm import SemanticPlanner, build_semantic_plan_messages, extract_json_object
from server.agent.semantic_rules import build_rule_plan
from server.agent.semantic_schema import SemanticFilter, SemanticPlan
from server.session.state import SessionState


PRODUCT_CARD = {
    "id": "p_beauty_021",
    "name": "科颜氏牛油果保湿眼霜",
    "category": "美妆护肤",
    "brand": "科颜氏",
    "price": 210.0,
}

SECOND_CARD = {
    **PRODUCT_CARD,
    "id": "p_beauty_016",
    "name": "AHC塑颜修护全脸眼霜",
    "brand": "AHC",
    "price": 139.0,
}


class FakePlanningLLM:
    async def stream_messages(self, messages: list[dict]) -> AsyncIterator[str]:
        yield '{"intent":"cart","cart_action":"add","reference_type":"brand",'
        yield '"reference_text":"AHC","quantity":2,"query":"","filters":[],'
        yield '"needs_search":false,"confidence":0.91}'


class FakeBrowsePlanningLLM:
    async def stream_messages(self, messages: list[dict]) -> AsyncIterator[str]:
        yield '{"intent":"browse","cart_action":"none","reference_type":"none",'
        yield '"reference_text":"","quantity":null,"query":"热门好物","filters":[],'
        yield '"needs_search":true,"confidence":0.93}'


class FakeWrongBundlePlanningLLM:
    async def stream_messages(self, messages: list[dict]) -> AsyncIterator[str]:
        yield '{"intent":"bundle","cart_action":"none","reference_type":"none",'
        yield '"reference_text":"","quantity":null,"query":"防晒霜 三亚 防晒方案",'
        yield '"filters":[{"kind":"product_type","value":"beauty.sunscreen"}],'
        yield '"needs_search":true,"confidence":0.95}'


class FakeBundlePlanningLLM:
    async def stream_messages(self, messages: list[dict]) -> AsyncIterator[str]:
        yield '{"intent":"bundle","cart_action":"none","reference_type":"none",'
        yield '"reference_text":"","quantity":null,"query":"三亚度假 防晒 穿搭 出行",'
        yield '"filters":[{"kind":"product_type","value":"beauty.sunscreen"}],'
        yield '"needs_search":true,"confidence":0.92}'


class FakeUnsafeCartPlanningLLM:
    async def stream_messages(self, messages: list[dict]) -> AsyncIterator[str]:
        yield '{"intent":"cart","cart_action":"add","reference_type":"none",'
        yield '"reference_text":"","quantity":1,"query":"帮我买一个好用的","filters":[],'
        yield '"needs_search":false,"confidence":0.96}'


class SlowPlanningLLM:
    async def stream_messages(self, messages: list[dict]) -> AsyncIterator[str]:
        await asyncio.sleep(0.05)
        yield '{"intent":"browse","confidence":0.9}'


class CountingPlanningLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def stream_messages(self, messages: list[dict]) -> AsyncIterator[str]:
        self.calls += 1
        yield '{"intent":"browse","confidence":0.9}'


def make_session() -> SessionState:
    session = SessionState(session_id="pytest-semantic")
    session.candidate_product_cards = [PRODUCT_CARD, SECOND_CARD]
    return session


def test_rule_plan_understands_brand_reference() -> None:
    plan = build_rule_plan("刚才你说的 AHC 那个，熬夜党能用吗？", make_session())

    assert plan.intent == "ask_product_detail"
    assert plan.reference_type == "brand"
    assert plan.reference_text == "AHC"
    assert ("keyword", "修护") in [(item.kind, item.value) for item in plan.filters]


def test_rule_plan_understands_implicit_cart_quantity() -> None:
    plan = build_rule_plan("那支给我来两件", make_session())

    assert plan.intent == "cart"
    assert plan.cart_action == "add"
    assert plan.quantity == 2
    assert plan.reference_type == "last"


def test_rule_plan_does_not_treat_ordinal_reference_as_quantity() -> None:
    plan = build_rule_plan("把第二个加到购物车", make_session())

    assert plan.intent == "cart"
    assert plan.cart_action == "add"
    assert plan.reference_type == "ordinal"
    assert plan.reference_index == 2
    assert plan.quantity is None


def test_rule_plan_treats_buy_two_pairs_as_cart_add() -> None:
    session = SessionState(session_id="pytest-semantic-anta-buy")
    session.candidate_product_cards = [
        {
            "id": "p_clothes_016",
            "name": "安踏 C202 GT Pro 马拉松碳板竞速跑鞋",
            "brand": "安踏",
            "category": "服饰运动",
            "price": 1299.0,
        }
    ]

    plan = build_rule_plan("我想买两双安踏", session)

    assert plan.intent == "cart"
    assert plan.cart_action == "add"
    assert plan.reference_type == "brand"
    assert plan.reference_text == "安踏"
    assert plan.quantity == 2


def test_rule_plan_does_not_treat_ambiguous_purchase_as_cart_add() -> None:
    plan = build_rule_plan("帮我买一个好用的", make_session())

    assert plan.intent == "browse"
    assert plan.cart_action == "none"


def test_rule_plan_adds_candidate_exclusion_and_soft_preference() -> None:
    plan = build_rule_plan("科颜氏先不要了，换个更温和的", make_session())

    values = [(item.kind, item.value) for item in plan.filters]

    assert plan.intent == "recommend"
    assert ("exclude", "科颜氏") in values
    assert ("keyword", "敏感肌") in values


def test_rule_plan_emits_structured_constraints_for_price_negation_and_soft_preference() -> None:
    plan = build_rule_plan("推荐一款1000元以上的跑步鞋，不要耐克，拍照优先", make_session())

    constraints = {(item.mode, item.field, item.operator, item.value) for item in plan.constraints}

    assert ("must", "price", "gte", "1000") in constraints
    assert ("must", "product_type", "eq", "clothes.sports_shoes") in constraints
    assert ("must_not", "attribute", "contains", "耐克") in constraints
    assert ("should", "keyword", "contains", "拍照") in constraints


def test_rule_plan_emits_negated_product_type_constraint() -> None:
    plan = build_rule_plan("推荐运动鞋，不要运动裤", make_session())

    filters = {(item.kind, item.value) for item in plan.filters}
    constraints = {(item.mode, item.field, item.operator, item.value) for item in plan.constraints}

    assert ("product_type", "clothes.sports_shoes") in filters
    assert ("exclude_product_type", "clothes.sports_pants") in filters
    assert ("must_not", "product_type", "eq", "clothes.sports_pants") in constraints


def test_rule_plan_recognizes_cleanser_product_type() -> None:
    plan = build_rule_plan("推荐一款500元以下的洁面乳", make_session())

    filters = {(item.kind, item.value) for item in plan.filters}
    constraints = {(item.mode, item.field, item.operator, item.value) for item in plan.constraints}

    assert ("max_price", "500") in filters
    assert ("product_type", "beauty.cleanser") in filters
    assert ("must", "product_type", "eq", "beauty.cleanser") in constraints


def test_rule_plan_recognizes_generic_skincare_category() -> None:
    plan = build_rule_plan("推荐一款护肤品", make_session())

    filters = {(item.kind, item.value) for item in plan.filters}
    constraints = {(item.mode, item.field, item.operator, item.value) for item in plan.constraints}

    assert plan.intent == "recommend"
    assert plan.presentation_mode == "single"
    assert ("category", "beauty.skincare") in filters
    assert ("must", "category", "eq", "beauty.skincare") in constraints


def test_rule_plan_recognizes_catalog_profile_categories() -> None:
    examples = [
        ("推荐一款精华", "beauty.skincare"),
        ("推荐一台笔记本电脑", "electronics.digital"),
        ("推荐一款酸奶", "food.beverage"),
        ("推荐一个运动背包", "clothes.sports"),
    ]

    for message, category_id in examples:
        plan = build_rule_plan(message, make_session())
        filters = {(item.kind, item.value) for item in plan.filters}
        constraints = {(item.mode, item.field, item.operator, item.value) for item in plan.constraints}

        assert ("category", category_id) in filters
        assert ("must", "category", "eq", category_id) in constraints


def test_rule_plan_recognizes_cleanser_query_aliases_and_typo() -> None:
    examples = [
        "推荐一款洗面乳",
        "推荐一款洗面泡沫",
        "推荐一款洁面啫喱",
        "推荐一款西面泡沫",
    ]

    for message in examples:
        plan = build_rule_plan(message, make_session())
        filters = {(item.kind, item.value) for item in plan.filters}
        constraints = {(item.mode, item.field, item.operator, item.value) for item in plan.constraints}

        assert ("product_type", "beauty.cleanser") in filters
        assert ("must", "product_type", "eq", "beauty.cleanser") in constraints


def test_rule_plan_marks_catalog_listing_for_all_cleanser_foam() -> None:
    plan = build_rule_plan("我想看所有洁面泡沫", make_session())

    filters = {(item.kind, item.value) for item in plan.filters}

    assert plan.intent == "browse"
    assert plan.presentation_mode == "listing"
    assert plan.query_understanding["presentation_mode"] == "listing"
    assert plan.query_understanding["recommended_top_k"] == 20
    assert "catalog_listing" in plan.query_understanding["signals"]
    assert ("product_type", "beauty.cleanser") in filters


def test_rule_plan_marks_single_product_recommendation() -> None:
    plan = build_rule_plan("推荐一款洁面泡沫", make_session())

    assert plan.intent == "recommend"
    assert plan.presentation_mode == "single"
    assert plan.query_understanding["presentation_mode"] == "single"


def test_rule_plan_does_not_promote_unknown_product_combo_to_bundle() -> None:
    plan = build_rule_plan("推荐一款文具组合", make_session())

    assert plan.intent == "recommend"
    assert plan.cart_action == "none"
    assert plan.presentation_mode == "single"


def test_rule_plan_emits_static_stock_and_unsupported_service_constraints() -> None:
    plan = build_rule_plan("推荐一款只看有货、支持企业开票、今天能发货的白色42码运动鞋", make_session())

    filters = {(item.kind, item.value) for item in plan.filters}
    constraints = {(item.mode, item.field, item.operator, item.value) for item in plan.constraints}

    assert ("in_stock", "true") in filters
    assert ("unsupported_service", "invoice_policy") in filters
    assert ("unsupported_service", "same_day_shipping") in filters
    assert ("facet", "color:白色") in filters
    assert ("facet", "shoe_size:42") in filters
    assert ("must", "stock", "gte", "1") in constraints
    assert ("must", "service", "eq", "invoice_policy") in constraints
    assert ("must", "service", "eq", "same_day_shipping") in constraints
    assert ("must", "facet", "eq", "color:白色") in constraints
    assert ("must", "facet", "eq", "shoe_size:42") in constraints


def test_rule_plan_maps_lining_alias_to_candidate_brand_exclusion() -> None:
    session = SessionState(session_id="pytest-semantic")
    session.candidate_product_cards = [
        {
            "id": "p_clothes_013",
            "name": "李宁 韦德之道 全城12 实战篮球专业比赛鞋",
            "brand": "李宁",
            "category": "服饰运动",
            "price": 1199.0,
        },
        {
            "id": "p_clothes_009",
            "name": "HOKA Clifton 9 男子缓震公路跑鞋",
            "brand": "HOKA",
            "category": "服饰运动",
            "price": 1099.0,
        },
    ]

    plan = build_rule_plan("不要 lining", session)

    filters = {(item.kind, item.value) for item in plan.filters}
    constraints = {(item.mode, item.field, item.operator, item.value) for item in plan.constraints}

    assert plan.intent == "recommend"
    assert ("exclude_brand", "李宁") in filters
    assert ("must_not", "brand", "eq", "李宁") in constraints


def test_rule_plan_uses_catalog_aliases_for_other_candidate_brands() -> None:
    session = SessionState(session_id="pytest-semantic-anta")
    session.candidate_product_cards = [
        {
            "id": "p_clothes_016",
            "name": "安踏 C202 GT Pro 马拉松碳板竞速跑鞋",
            "brand": "安踏",
            "category": "服饰运动",
            "price": 1299.0,
        },
        {
            "id": "p_clothes_009",
            "name": "HOKA Clifton 9 男子缓震公路跑鞋",
            "brand": "HOKA",
            "category": "服饰运动",
            "price": 1099.0,
        },
    ]

    plan = build_rule_plan("不要 anta，换个牌子", session)

    filters = {(item.kind, item.value) for item in plan.filters}
    constraints = {(item.mode, item.field, item.operator, item.value) for item in plan.constraints}

    assert ("exclude_brand", "安踏") in filters
    assert ("must_not", "brand", "eq", "安踏") in constraints


def test_extract_json_object_ignores_wrapping_text() -> None:
    assert extract_json_object('```json\n{"intent":"recommend"}\n```') == {"intent": "recommend"}


def test_semantic_plan_prompt_includes_compact_planning_context() -> None:
    session = make_session()
    session.cart.append(
        {
            "product_id": "p_beauty_021",
            "name": "科颜氏牛油果保湿眼霜",
            "brand": "科颜氏",
            "price": 210,
            "quantity": 2,
        }
    )

    messages = build_semantic_plan_messages("把它改成 1 件", session)
    prompt = messages[-1]["content"]

    assert "候选商品" in prompt
    assert "购物车" in prompt
    assert "presentation_mode" in prompt
    assert "科颜氏牛油果保湿眼霜" in prompt
    assert "quantity=2" in prompt


def test_semantic_planner_can_use_llm_json_plan() -> None:
    async def _run():
        return await SemanticPlanner(FakePlanningLLM()).plan("AHC 那个来两件", make_session())

    plan = asyncio.run(_run())

    assert plan.intent == "cart"
    assert plan.cart_action == "add"
    assert plan.reference_type == "brand"
    assert plan.reference_text == "AHC"
    assert plan.quantity == 2


def test_semantic_planner_does_not_downgrade_explicit_product_request_to_browse() -> None:
    async def _run():
        return await SemanticPlanner(FakeBrowsePlanningLLM()).plan("推荐一款运动鞋", make_session())

    plan = asyncio.run(_run())
    values = [(item.kind, item.value) for item in plan.filters]

    assert plan.intent == "recommend"
    assert ("product_type", "clothes.sports_shoes") in values


def test_semantic_planner_policy_blocks_llm_bundle_for_single_product_request() -> None:
    async def _run():
        return await SemanticPlanner(FakeWrongBundlePlanningLLM()).plan("推荐防晒霜，预算300以内", make_session())

    plan = asyncio.run(_run())
    values = [(item.kind, item.value) for item in plan.filters]

    assert plan.intent == "recommend"
    assert ("product_type", "beauty.sunscreen") in values


def test_semantic_planner_policy_keeps_explicit_bundle_request() -> None:
    async def _run():
        return await SemanticPlanner(FakeBundlePlanningLLM()).plan(
            "去三亚玩，帮我搭配一套从防晒到穿搭的方案",
            make_session(),
        )

    plan = asyncio.run(_run())

    assert plan.intent == "bundle"


def test_semantic_planner_blocks_unsafe_llm_cart_mutation() -> None:
    # 单元级校验：LLM 输出"无指代的购物车变更"时，策略层必须拦为 clarify。
    # 注：该消息在端到端路径中被规则层判为 deterministic，不会触发 LLM 规划，
    # 因此这里直接对 validate_semantic_plan 做策略拦截测试（与 B1 修法定点一致）。
    message = "帮我买一个好用的"
    session = make_session()
    fallback = build_rule_plan(message, session)
    unsafe = SemanticPlan(
        intent="cart",
        cart_action="add",
        reference_type="none",
        reference_text="",
        quantity=1,
        query=message,
        filters=[],
        needs_search=False,
        confidence=0.96,
    )

    plan = validate_semantic_plan(message=message, session=session, fallback=fallback, candidate=unsafe)

    assert plan.intent == "clarify"
    assert plan.cart_action == "none"
    assert plan.needs_search is False
    assert plan.confidence < 0.5


def test_semantic_planner_timeout_falls_back_to_rules() -> None:
    async def _run():
        return await SemanticPlanner(SlowPlanningLLM(), timeout_seconds=0.001).plan(
            "推荐一款运动鞋",
            make_session(),
        )

    plan = asyncio.run(_run())
    values = [(item.kind, item.value) for item in plan.filters]

    assert plan.intent == "recommend"
    assert ("product_type", "clothes.sports_shoes") in values


def test_semantic_planner_skips_llm_for_simple_single_product_request() -> None:
    client = CountingPlanningLLM()

    async def _run():
        return await SemanticPlanner(client, timeout_seconds=10).plan(
            "推荐一款运动鞋",
            make_session(),
        )

    plan = asyncio.run(_run())
    values = [(item.kind, item.value) for item in plan.filters]

    assert client.calls == 0
    assert plan.intent == "recommend"
    assert ("product_type", "clothes.sports_shoes") in values


def test_planner_policy_downgrades_invalid_bundle_plan_for_single_product_constraint() -> None:
    fallback = SemanticPlan(
        intent="recommend",
        query="推荐防晒霜",
        filters=[SemanticFilter(kind="product_type", value="beauty.sunscreen")],
        confidence=0.55,
    )
    candidate = SemanticPlan(
        intent="bundle",
        query="三亚 防晒方案",
        filters=[SemanticFilter(kind="product_type", value="beauty.sunscreen")],
        confidence=0.95,
    )

    plan = validate_semantic_plan(
        message="推荐防晒霜，预算300以内",
        session=make_session(),
        fallback=fallback,
        candidate=candidate,
    )

    assert plan.intent == "recommend"
    assert plan.confidence < candidate.confidence


def test_rule_plan_maps_generic_shoe_and_around_budget_to_structured_filters() -> None:
    plan = build_rule_plan("\u63a8\u8350\u4e00\u53cc1000\u5143\u5de6\u53f3\u7684\u978b\u5b50", make_session())

    values = {(item.kind, item.value) for item in plan.filters}

    assert ("product_type", "clothes.sports_shoes") in values
    assert ("min_price", "800") in values
    assert ("max_price", "1200") in values


def test_rule_plan_maps_lipstick_to_product_type() -> None:
    plan = build_rule_plan("\u63a8\u8350\u4e00\u6b3e\u6b27\u83b1\u96c5\u7684\u53e3\u7ea2", make_session())

    values = {(item.kind, item.value) for item in plan.filters}

    assert ("product_type", "beauty.lipstick") in values
    assert ("brand", "\u5df4\u9ece\u6b27\u83b1\u96c5") in values


def test_rule_plan_exposes_field_confidence_and_evidence() -> None:
    plan = build_rule_plan(
        "\u63a8\u8350\u4e00\u6b3e1000\u5143\u4ee5\u4e0a\u7684\u8fd0\u52a8\u978b",
        make_session(),
    )

    assert plan.confidence_by_field["intent"] > 0
    assert plan.confidence_by_field["product_type"] >= 0.9
    assert plan.confidence_by_field["price"] >= 0.9
    assert plan.evidence["product_type"]["value"] == "clothes.sports_shoes"
    assert plan.evidence["product_type"]["span"] == "\u8fd0\u52a8\u978b"
    assert plan.evidence["price"] == [{"kind": "min_price", "value": "1000"}]


def test_rule_plan_marks_cart_action_low_confidence_without_reference() -> None:
    plan = build_rule_plan("\u6211\u60f3\u4e70\u4e24\u53cc\u5b89\u8e0f", make_session())

    assert plan.cart_action == "none"
    assert plan.quantity == 2
    assert plan.confidence_by_field["quantity"] >= 0.9
    assert "cart_action" not in plan.confidence_by_field or plan.confidence_by_field["cart_action"] < 0.75
