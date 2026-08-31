"""
Purpose: Orchestrator LLM 测试：验证语义规划和 LLM 调用链路。
"""

import asyncio
from collections.abc import AsyncIterator

from server.agent.orchestrator import Orchestrator
from server.agent.semantic_llm import SemanticPlanner
from server.session.state import SQLiteSessionStore, SessionStore
from server.tools.cart import CartTool
from server.tools.product_compare import ProductCompareTool
from server.tools.registry import ToolRegistry


PRODUCT_CARD = {
    "id": "p_beauty_021",
    "name": "科颜氏牛油果保湿眼霜",
    "category": "美妆护肤",
    "brand": "科颜氏",
    "price": 210.0,
    "image_url": "",
    "detail_url": "",
    "reason": "匹配眼霜需求",
    "score": 10.0,
}


class FixedSearchTool:
    name = "search_products"

    def __init__(self, cards: list[dict]) -> None:
        self.cards = cards
        self.calls: list[dict] = []

    def run(self, **kwargs) -> list[dict]:
        self.calls.append(kwargs)
        return self.cards


class FakeLLMClient:
    def __init__(
        self,
        tokens: list[str] | None = None,
        should_fail: bool = False,
        delay_seconds: float = 0.0,
    ) -> None:
        self.tokens = tokens or []
        self.should_fail = should_fail
        self.delay_seconds = delay_seconds
        self.calls: list[dict] = []

    async def stream_answer(
        self,
        user_message: str,
        cards: list[dict],
        intent: str,
    ) -> AsyncIterator[str]:
        self.calls.append({"user_message": user_message, "cards": cards, "intent": intent})
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.should_fail:
            raise RuntimeError("LLM failed")
        for token in self.tokens:
            yield token

    async def stream_messages(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        self.calls.append({"messages": messages, "max_tokens": max_tokens})
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.should_fail:
            raise RuntimeError("LLM failed")
        for token in self.tokens:
            yield token


class FakeUnsafeCartPlanningLLM:
    async def stream_messages(self, messages: list[dict]) -> AsyncIterator[str]:
        yield '{"intent":"cart","cart_action":"add","reference_type":"none",'
        yield '"reference_text":"","quantity":1,"query":"帮我买一个好用的","filters":[],'
        yield '"needs_search":false,"confidence":0.96}'


def make_orchestrator(
    cards: list[dict],
    llm_client: FakeLLMClient | None,
    sessions: SessionStore | SQLiteSessionStore | None = None,
    recommendation_llm_budget_seconds: float = 0.6,
    semantic_planner: SemanticPlanner | None = None,
) -> Orchestrator:
    registry = ToolRegistry()
    search_tool = FixedSearchTool(cards)
    registry.register(search_tool)
    registry.register(ProductCompareTool(search_tool))
    registry.register(CartTool())
    return Orchestrator(
        registry=registry,
        sessions=sessions or SessionStore(),
        llm_client=llm_client,
        recommendation_llm_budget_seconds=recommendation_llm_budget_seconds,
        semantic_planner=semantic_planner,
    )


def collect_events(orchestrator: Orchestrator, message: str) -> list[dict]:
    async def _collect() -> list[dict]:
        return [item async for item in orchestrator.stream_chat("pytest-llm", message)]

    return asyncio.run(_collect())


def token_text(events: list[dict]) -> str:
    return "".join(item["data"]["text"] for item in events if item["event"] == "token")


def test_orchestrator_streams_llm_answer_when_configured() -> None:
    llm = FakeLLMClient(tokens=["【候选1】科颜氏牛油果保湿眼霜", "更适合保湿需求。"])
    orchestrator = make_orchestrator(cards=[PRODUCT_CARD], llm_client=llm)

    events = collect_events(orchestrator, "推荐一款眼霜")

    assert events[0]["event"] == "status"
    assert token_text(events) == "".join(llm.tokens)
    assert any(item["event"] == "product_card" for item in events)
    assert llm.calls
    assert "科颜氏牛油果保湿眼霜" in str(llm.calls[0].get("messages", llm.calls[0]))


def test_orchestrator_binds_product_cards_to_answer_mentions() -> None:
    first = {**PRODUCT_CARD, "id": "alpha", "name": "Alpha Running Shoe", "brand": "Alpha"}
    second = {**PRODUCT_CARD, "id": "beta", "name": "Beta Running Shoe", "brand": "Beta"}
    llm = FakeLLMClient(tokens=["【候选2】Beta Running Shoe更适合你的日常训练需求。"])
    orchestrator = make_orchestrator(cards=[first, second], llm_client=llm)

    events = collect_events(orchestrator, "推荐一款运动鞋")
    product_ids = [item["data"]["id"] for item in events if item["event"] == "product_card"]

    assert product_ids == ["beta"]


def test_orchestrator_suppresses_cards_when_llm_declines_recommendation() -> None:
    cards = [
        {**PRODUCT_CARD, "id": "ahc", "name": "AHC Eye Cream", "brand": "AHC"},
        {**PRODUCT_CARD, "id": "lancome", "name": "Lancome Toner", "brand": "Lancome"},
    ]
    llm = FakeLLMClient(
        tokens=[
            "\u5f53\u524d\u5019\u9009\u5546\u54c1\u4e2d\u6ca1\u6709\u6d17\u53d1\u6db2\u7c7b\u5546\u54c1\uff0c",
            "\u65e0\u6cd5\u4e3a\u4f60\u63a8\u8350\u7b26\u5408\u9700\u6c42\u7684\u5546\u54c1\u3002",
        ]
    )
    orchestrator = make_orchestrator(cards=cards, llm_client=llm)

    events = collect_events(orchestrator, "\u63a8\u8350\u4e00\u6b3e\u6d17\u53d1\u6db2")

    assert "\u6ca1\u6709\u627e\u5230\u8db3\u591f\u5339\u914d\u7684\u5546\u54c1" in token_text(events)
    assert not any(item["event"] == "product_card" for item in events)
    assert events[-1]["event"] == "done"


def test_orchestrator_keeps_filtered_cards_when_answer_has_no_specific_product_mention() -> None:
    first = {**PRODUCT_CARD, "id": "alpha", "name": "Alpha Running Shoe", "brand": "Alpha"}
    second = {**PRODUCT_CARD, "id": "beta", "name": "Beta Running Shoe", "brand": "Beta"}
    orchestrator = make_orchestrator(cards=[first, second], llm_client=None)

    events = collect_events(orchestrator, "推荐一款运动鞋")
    product_ids = [item["data"]["id"] for item in events if item["event"] == "product_card"]

    assert product_ids == ["alpha", "beta"]


def test_orchestrator_catalog_listing_keeps_all_filtered_cards_and_requests_larger_pool() -> None:
    cards = [
        {**PRODUCT_CARD, "id": f"cleanser-{index}", "name": f"Foam Cleanser {index}", "brand": "Demo"}
        for index in range(3)
    ]
    registry = ToolRegistry()
    search_tool = FixedSearchTool(cards)
    registry.register(search_tool)
    registry.register(ProductCompareTool(search_tool))
    registry.register(CartTool())
    orchestrator = Orchestrator(registry=registry, sessions=SessionStore(), llm_client=None)

    events = collect_events(orchestrator, "我想看所有洁面泡沫")
    product_ids = [item["data"]["id"] for item in events if item["event"] == "product_card"]

    assert "当前商品库里按你的条件找到 3 款" in token_text(events)
    assert product_ids == ["cleanser-0", "cleanser-1", "cleanser-2"]
    assert search_tool.calls[-1]["top_k"] == 20


def test_orchestrator_strips_markdown_from_llm_answer() -> None:
    llm = FakeLLMClient(tokens=["**科颜氏牛油果保湿眼霜**更适合保湿需求。"])
    orchestrator = make_orchestrator(cards=[PRODUCT_CARD], llm_client=llm)

    events = collect_events(orchestrator, "推荐一款眼霜")
    answer = token_text(events)

    assert "**" not in answer
    assert "科颜氏牛油果保湿眼霜" in answer


def test_orchestrator_falls_back_to_template_when_llm_fails() -> None:
    llm = FakeLLMClient(should_fail=True)
    orchestrator = make_orchestrator(cards=[PRODUCT_CARD], llm_client=llm)

    events = collect_events(orchestrator, "推荐一款眼霜")

    assert "我会优先推荐这款" in token_text(events)
    assert "首选是" in token_text(events)
    assert any(item["event"] == "product_card" for item in events)


def test_orchestrator_does_not_block_first_screen_when_llm_is_slow() -> None:
    llm = FakeLLMClient(tokens=["这段慢回答不应该进入首屏。"], delay_seconds=0.05)
    orchestrator = make_orchestrator(
        cards=[PRODUCT_CARD],
        llm_client=llm,
        recommendation_llm_budget_seconds=0.001,
    )

    events = collect_events(orchestrator, "推荐一款眼霜")
    answer = token_text(events)

    assert "这段慢回答" not in answer
    assert "我会优先推荐这款" in answer
    assert any(item["event"] == "product_card" for item in events)
    assert llm.calls


def test_orchestrator_guards_unsafe_llm_answer_before_streaming() -> None:
    llm = FakeLLMClient(tokens=["科颜氏牛油果保湿眼霜现在有50元优惠券，只要99元。"])
    orchestrator = make_orchestrator(cards=[PRODUCT_CARD], llm_client=llm)

    events = collect_events(orchestrator, "推荐一款眼霜")
    answer = token_text(events)

    assert "优惠券" not in answer
    assert "99元" not in answer
    assert "我会优先推荐这款" in answer
    guardrail = [item for item in events if item["event"] == "guardrail"]
    assert guardrail
    assert guardrail[0]["data"]["action"] == "fallback"


def test_orchestrator_skips_llm_when_no_cards() -> None:
    llm = FakeLLMClient(tokens=["不会被调用"])
    orchestrator = make_orchestrator(cards=[], llm_client=llm)

    events = collect_events(orchestrator, "推荐一款不存在的商品")

    assert "没有找到足够匹配的商品" in token_text(events)
    assert llm.calls == []


def test_orchestrator_asks_clarification_for_vague_phone_request() -> None:
    orchestrator = make_orchestrator(cards=[PRODUCT_CARD], llm_client=None)

    events = collect_events(orchestrator, "推荐一款手机")

    assert "你更看重拍照、续航、性能还是性价比" in token_text(events)
    assert not any(item["event"] == "product_card" for item in events)
    done = [item for item in events if item["event"] == "done"][-1]
    assert done["data"]["needs_clarification"] is True
    assert done["data"]["pending_subject"] == "手机"


def test_orchestrator_does_not_apply_unsafe_llm_cart_plan() -> None:
    """开放式购物表达不得让 LLM 规划直接改写购物车（双层验证）。

    单元级：无引用依据的加购计划必须被 planning_policy 拦截为澄清；
    端到端：「帮我买一个好用的」不得产生 cart_update。
    """
    from server.agent.planning_policy import validate_semantic_plan
    from server.agent.semantic_rules import build_rule_plan
    from server.agent.semantic_schema import SemanticPlan
    from server.session.state import SessionState

    message = "帮我买一个好用的"
    session = SessionState(session_id="pytest-unsafe-cart")
    fallback = build_rule_plan(message, session)
    unsafe_candidate = SemanticPlan(
        intent="cart",
        cart_action="add",
        reference_type="none",
        quantity=1,
        query=message,
        needs_search=False,
        confidence=0.96,
    )
    blocked = validate_semantic_plan(
        message=message,
        session=session,
        fallback=fallback,
        candidate=unsafe_candidate,
    )
    assert blocked.intent == "clarify"
    assert blocked.cart_action == "none"
    assert blocked.needs_search is False

    orchestrator = make_orchestrator(
        cards=[PRODUCT_CARD],
        llm_client=None,
        semantic_planner=SemanticPlanner(FakeUnsafeCartPlanningLLM()),
    )
    collect_events(orchestrator, "推荐一款眼霜")
    events = collect_events(orchestrator, message)
    assert not any(item["event"] == "cart_update" for item in events)


def test_orchestrator_resets_filters_when_user_switches_product_scope() -> None:
    orchestrator = make_orchestrator(cards=[PRODUCT_CARD], llm_client=None)

    collect_events(orchestrator, "推荐一款保湿眼霜，预算250以内")
    events = collect_events(orchestrator, "推荐一款手机")

    assert "你更看重拍照、续航、性能还是性价比" in token_text(events)
    assert not any(item["event"] == "product_card" for item in events)
    done = [item for item in events if item["event"] == "done"][-1]
    filters = {(item["kind"], item["value"]) for item in done["data"]["filters"]}
    assert done["data"]["needs_clarification"] is True
    assert ("product_type", "electronics.phone") in filters
    assert ("product_type", "beauty.eye_cream") not in filters
    assert ("keyword", "眼霜") not in filters
    assert ("max_price", "250") not in filters


def test_orchestrator_emits_comparison_card_for_compare_intent() -> None:
    other_card = {
        **PRODUCT_CARD,
        "id": "p_beauty_016",
        "name": "AHC塑颜修护全脸眼霜",
        "brand": "AHC",
        "price": 139.0,
        "reason": "匹配眼霜需求",
    }
    llm = FakeLLMClient(tokens=["不应调用"])
    orchestrator = make_orchestrator(cards=[PRODUCT_CARD, other_card], llm_client=llm)

    events = collect_events(orchestrator, "科颜氏和AHC哪个眼霜更适合干皮")

    assert "我先基于当前商品库做对比" in token_text(events)
    assert any(item["event"] == "comparison_card" for item in events)
    assert sum(1 for item in events if item["event"] == "product_card") == 2
    assert llm.calls == []


def test_orchestrator_emits_cart_update_after_add_to_cart() -> None:
    orchestrator = make_orchestrator(cards=[PRODUCT_CARD], llm_client=None)

    collect_events(orchestrator, "推荐一款眼霜")
    events = collect_events(orchestrator, "把刚才那款加到购物车")

    assert "已将 科颜氏牛油果保湿眼霜 加入购物车" in token_text(events)
    cart_update = [item for item in events if item["event"] == "cart_update"][-1]
    assert cart_update["data"]["total_quantity"] == 1
    assert cart_update["data"]["items"][0]["product_id"] == "p_beauty_021"


def test_orchestrator_persists_cart_with_sqlite_session_store(tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    first_store = SQLiteSessionStore(db_path)
    first_orchestrator = make_orchestrator(cards=[PRODUCT_CARD], llm_client=None, sessions=first_store)

    collect_events(first_orchestrator, "推荐一款眼霜")
    collect_events(first_orchestrator, "把刚才那款加到购物车")

    second_store = SQLiteSessionStore(db_path)
    second_orchestrator = make_orchestrator(cards=[PRODUCT_CARD], llm_client=None, sessions=second_store)
    events = collect_events(second_orchestrator, "查看购物车")

    assert "购物车中有：1. 科颜氏牛油果保湿眼霜" in token_text(events)
    cart_update = [item for item in events if item["event"] == "cart_update"][-1]
    assert cart_update["data"]["total_quantity"] == 1
    assert cart_update["data"]["items"][0]["product_id"] == "p_beauty_021"


def test_orchestrator_answers_contextual_second_product_follow_up() -> None:
    other_card = {
        **PRODUCT_CARD,
        "id": "p_beauty_016",
        "name": "AHC塑颜修护全脸眼霜",
        "brand": "AHC",
        "price": 139.0,
        "reason": "匹配眼霜需求",
    }
    orchestrator = make_orchestrator(cards=[PRODUCT_CARD, other_card], llm_client=None)

    collect_events(orchestrator, "推荐一款眼霜")
    events = collect_events(orchestrator, "第二个怎么样")

    assert "你说的是 AHC塑颜修护全脸眼霜" in token_text(events)
    product_card = [item for item in events if item["event"] == "product_card"][-1]
    assert product_card["data"]["id"] == "p_beauty_016"


def test_orchestrator_adds_contextual_price_filter_for_cheaper_follow_up() -> None:
    registry = ToolRegistry()
    search_tool = FixedSearchTool([PRODUCT_CARD])
    registry.register(search_tool)
    registry.register(ProductCompareTool(search_tool))
    registry.register(CartTool())
    orchestrator = Orchestrator(registry=registry, sessions=SessionStore(), llm_client=None)

    collect_events(orchestrator, "推荐一款眼霜")
    collect_events(orchestrator, "再便宜点")

    assert search_tool.calls[-1]["filters"].max_price == 209


def test_orchestrator_answers_contextual_brand_follow_up() -> None:
    other_card = {
        **PRODUCT_CARD,
        "id": "p_beauty_016",
        "name": "AHC塑颜修护全脸眼霜",
        "brand": "AHC",
        "price": 139.0,
        "reason": "匹配眼霜需求",
    }
    orchestrator = make_orchestrator(cards=[PRODUCT_CARD, other_card], llm_client=None)

    collect_events(orchestrator, "推荐一款眼霜")
    events = collect_events(orchestrator, "刚才你说的 AHC 那个，熬夜党能用吗？")

    assert "你说的是 AHC塑颜修护全脸眼霜" in token_text(events)
    product_card = [item for item in events if item["event"] == "product_card"][-1]
    assert product_card["data"]["id"] == "p_beauty_016"


def test_orchestrator_adds_referenced_product_quantity_to_cart() -> None:
    other_card = {
        **PRODUCT_CARD,
        "id": "p_beauty_016",
        "name": "AHC塑颜修护全脸眼霜",
        "brand": "AHC",
        "price": 139.0,
        "reason": "匹配眼霜需求",
    }
    orchestrator = make_orchestrator(cards=[PRODUCT_CARD, other_card], llm_client=None)

    collect_events(orchestrator, "推荐一款眼霜")
    collect_events(orchestrator, "刚才你说的 AHC 那个，熬夜党能用吗？")
    events = collect_events(orchestrator, "那支给我来两件")

    assert "已将 AHC塑颜修护全脸眼霜 加入购物车，数量 2" in token_text(events)
    cart_update = [item for item in events if item["event"] == "cart_update"][-1]
    assert cart_update["data"]["items"][0]["product_id"] == "p_beauty_016"
    assert cart_update["data"]["items"][0]["quantity"] == 2


def test_orchestrator_searches_again_with_soft_exclusion() -> None:
    registry = ToolRegistry()
    search_tool = FixedSearchTool([PRODUCT_CARD])
    registry.register(search_tool)
    registry.register(ProductCompareTool(search_tool))
    registry.register(CartTool())
    orchestrator = Orchestrator(registry=registry, sessions=SessionStore(), llm_client=None)

    collect_events(orchestrator, "推荐一款眼霜")
    collect_events(orchestrator, "科颜氏先不要了，换个更温和的")

    filters = search_tool.calls[-1]["filters"]
    assert "科颜氏" in filters.exclusions
    assert "敏感肌" in filters.keywords


def test_orchestrator_uses_referenced_price_for_not_too_expensive_follow_up() -> None:
    other_card = {
        **PRODUCT_CARD,
        "id": "p_beauty_016",
        "name": "AHC塑颜修护全脸眼霜",
        "brand": "AHC",
        "price": 139.0,
        "reason": "匹配眼霜需求",
    }
    registry = ToolRegistry()
    search_tool = FixedSearchTool([PRODUCT_CARD, other_card])
    registry.register(search_tool)
    registry.register(ProductCompareTool(search_tool))
    registry.register(CartTool())
    orchestrator = Orchestrator(registry=registry, sessions=SessionStore(), llm_client=None)

    collect_events(orchestrator, "推荐一款眼霜")
    collect_events(orchestrator, "有没有比第二款更适合敏感肌但别太贵的")

    filters = search_tool.calls[-1]["filters"]
    assert filters.max_price == 139
    assert "敏感肌" in filters.keywords
