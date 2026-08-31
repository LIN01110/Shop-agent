"""
Purpose: 聊天 API 测试：验证 SSE 聊天接口返回正确事件。
"""

import json
from uuid import uuid4

from fastapi.testclient import TestClient

from server.main import app


client = TestClient(app)


def collect_token_text(stream_text: str) -> str:
    parts: list[str] = []
    for line in stream_text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line.removeprefix("data: "))
        if "text" in payload:
            parts.append(payload["text"])
    return "".join(parts)


def collect_event_payloads(stream_text: str, event_name: str) -> list[dict]:
    payloads: list[dict] = []
    current_event = ""
    for line in stream_text.splitlines():
        if line.startswith("event: "):
            current_event = line.removeprefix("event: ")
            continue
        if not line.startswith("data: ") or current_event != event_name:
            continue
        payloads.append(json.loads(line.removeprefix("data: ")))
    return payloads


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_product_image_asset_is_served() -> None:
    response = client.get("/assets/products/p_beauty_021_live.jpg")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8")


def test_product_detail_page_is_served() -> None:
    response = client.get("/products/p_beauty_021")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "科颜氏牛油果保湿眼霜" in response.text
    assert "/assets/products/p_beauty_021_live.jpg" in response.text


def test_product_detail_page_returns_404_for_missing_product() -> None:
    response = client.get("/products/missing-product")

    assert response.status_code == 404


def test_chat_stream_returns_tokens_and_product_card() -> None:
    session_id = f"pytest-chat-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款保湿眼霜，预算250以内",
        },
    )

    assert response.status_code == 200
    assert "event: status" in response.text
    assert "event: token" in response.text
    assert "event: product_card" in response.text
    assert "科颜氏牛油果保湿眼霜" in response.text
    assert "http://127.0.0.1:8000/assets/products/p_beauty_021_live.jpg" in response.text
    assert "http://127.0.0.1:8000/products/p_beauty_021" in response.text
    assert "event: done" in response.text
    done = collect_event_payloads(response.text, "done")[0]
    assert done["trace_id"]


def test_chat_websocket_streams_chat_events() -> None:
    session_id = f"pytest-ws-chat-{uuid4()}"
    with client.websocket_connect("/ws/chat") as websocket:
        websocket.send_json(
            {
                "session_id": session_id,
                "message": "推荐一款保湿眼霜，预算250以内",
            }
        )
        events = []
        for _ in range(30):
            item = websocket.receive_json()
            events.append(item)
            if item["event"] == "done":
                break

    event_names = [item["event"] for item in events]
    product_cards = [item["data"] for item in events if item["event"] == "product_card"]
    done = next(item["data"] for item in events if item["event"] == "done")

    assert "status" in event_names
    assert "token" in event_names
    assert product_cards
    assert product_cards[0]["id"] == "p_beauty_021"
    assert done["trace_id"]


def test_chat_bundle_request_clears_previous_single_product_scope() -> None:
    session_id = f"pytest-scope-reset-{uuid4()}"
    first = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "\u63a8\u8350\u4e00\u6b3e\u6b27\u83b1\u96c5\u7684\u53e3\u7ea2",
        },
    )
    second = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "\u63a8\u8350\u4e00\u5957\u4e09\u4e9a\u7684\u65c5\u884c\u88c5\u5907",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert "event: product_card" in second.text
    done = collect_event_payloads(second.text, "done")[0]
    trace = client.get(f"/debug/traces/{done['trace_id']}")
    active_filters = {(item["kind"], item["value"]) for item in done["filters"]}

    assert ("product_type", "beauty.lipstick") not in active_filters
    assert ("brand", "\u5df4\u9ece\u6b27\u83b1\u96c5") not in active_filters
    assert trace.status_code == 200
    assert trace.json()["metadata"]["scope_transition"]["type"] == "bundle_new_task"


def test_chat_empty_result_degrades_without_product_card() -> None:
    session_id = f"pytest-empty-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款预算1元以内的无人机",
        },
    )

    assert response.status_code == 200
    assert "没有找到足够匹配的商品" in collect_token_text(response.text)
    assert "event: product_card" not in response.text


def test_chat_out_of_catalog_unscoped_product_request_does_not_emit_weak_cards() -> None:
    session_id = f"pytest-out-of-catalog-shampoo-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款洗发液",
        },
    )

    assert response.status_code == 200
    assert "没有找到足够匹配的商品" in collect_token_text(response.text)
    assert "event: product_card" not in response.text


def test_chat_unknown_product_combo_does_not_fall_back_to_generic_bundle() -> None:
    session_id = f"pytest-out-of-catalog-stationery-bundle-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款文具组合",
        },
    )

    assert response.status_code == 200
    answer = collect_token_text(response.text)
    assert "没有找到足够匹配的商品" in answer
    assert "跨类目组合方案" not in answer
    assert "event: product_card" not in response.text
    done = collect_event_payloads(response.text, "done")[0]
    trace = client.get(f"/debug/traces/{done['trace_id']}")

    assert trace.status_code == 200
    assert trace.json()["handler"] == "RecommendationHandler"


def test_chat_compare_returns_comparison_card() -> None:
    session_id = f"pytest-compare-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "科颜氏和AHC哪个眼霜更适合干皮",
        },
    )

    assert response.status_code == 200
    assert "event: comparison_card" in response.text
    assert "event: product_card" in response.text
    assert "我先基于当前商品库做对比" in collect_token_text(response.text)


def test_chat_cart_update_after_product_recommendation() -> None:
    session_id = f"pytest-cart-api-{uuid4()}"
    first = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款保湿眼霜，预算250以内",
        },
    )
    second = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "把刚才那款加到购物车",
        },
    )

    assert first.status_code == 200
    assert "event: product_card" in first.text
    assert second.status_code == 200
    assert "event: cart_update" in second.text
    assert "科颜氏牛油果保湿眼霜" in second.text
    assert '"total_quantity": 1' in second.text


def test_chat_contextual_stock_answer_marks_catalog_stock_as_static() -> None:
    session_id = f"pytest-static-stock-answer-{uuid4()}"
    first = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款保湿眼霜，预算250以内",
        },
    )
    second = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "这款现在有货吗",
        },
    )

    text = collect_token_text(second.text)

    assert first.status_code == 200
    assert second.status_code == 200
    assert "mock 库存服务" in text
    assert "不等同于真实线上库存" in text
    assert "inventory_service" in text


def test_chat_contextual_coupon_answer_refuses_unsupported_fact() -> None:
    session_id = f"pytest-coupon-unsupported-{uuid4()}"
    first = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款保湿眼霜，预算250以内",
        },
    )
    second = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "这款有没有优惠券",
        },
    )

    text = collect_token_text(second.text)

    assert first.status_code == 200
    assert second.status_code == 200
    assert "mock 优惠服务" in text
    assert "当前没有可用优惠券" in text
    assert "接入生产后应以对应业务服务实时返回为准" in text


def test_chat_handles_complex_cart_operation_sequence() -> None:
    session_id = f"pytest-complex-cart-api-{uuid4()}"
    first = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款保湿眼霜，预算250以内",
        },
    )
    add_first = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "把第一个加到购物车",
        },
    )
    complex_update = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "删除第一个，然后把第二个加到购物车，数量改成 2",
        },
    )

    payloads = collect_event_payloads(complex_update.text, "cart_update")
    cart = payloads[-1]

    assert first.status_code == 200
    assert add_first.status_code == 200
    assert complex_update.status_code == 200
    assert cart["total_quantity"] == 2
    assert len(cart["items"]) == 1
    assert cart["items"][0]["quantity"] == 2
    assert cart["items"][0]["product_id"] != "p_beauty_021"


def test_chat_asks_confirmation_then_buys_two_pairs_by_brand() -> None:
    session_id = f"pytest-buy-two-pairs-brand-{uuid4()}"
    first = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款安踏运动鞋",
        },
    )
    second = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "我想买两双安踏",
        },
    )
    third = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "确认",
        },
    )

    second_cart_updates = collect_event_payloads(second.text, "cart_update")
    second_done = collect_event_payloads(second.text, "done")[0]
    third_cart_updates = collect_event_payloads(third.text, "cart_update")
    assert first.status_code == 200
    assert "安踏" in first.text
    assert second.status_code == 200
    assert "event: product_card" not in second.text
    assert second_cart_updates
    assert second_cart_updates[-1]["total_quantity"] == 0
    assert second_done["needs_clarification"] is True
    assert "确认把它加入购物车吗" in second.text

    assert third.status_code == 200
    assert third_cart_updates[-1]["total_quantity"] == 2
    assert third_cart_updates[-1]["items"][0]["brand"] == "安踏"
    assert third_cart_updates[-1]["items"][0]["quantity"] == 2
    assert "已将 安踏" in third.text


def test_chat_sports_shoe_request_does_not_return_pants() -> None:
    session_id = f"pytest-sports-shoes-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款运动鞋",
        },
    )

    assert response.status_code == 200
    assert "event: product_card" in response.text
    assert "跑鞋" in response.text or "跑步鞋" in response.text
    assert "运动长裤" not in response.text
    assert "运动短裤" not in response.text


def test_chat_budget_cleanser_returns_only_cleanser_cards() -> None:
    session_id = f"pytest-budget-cleanser-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款500元以下的洁面乳",
        },
    )

    cards = collect_event_payloads(response.text, "product_card")
    done = collect_event_payloads(response.text, "done")[0]

    assert response.status_code == 200
    assert cards
    assert all("beauty.cleanser" in card["product_types"] for card in cards)
    assert all(card["price"] <= 500 for card in cards)
    assert all("食品饮料" not in card["category"] for card in cards)
    assert any(item["kind"] == "product_type" and item["value"] == "beauty.cleanser" for item in done["filters"])


def test_chat_catalog_listing_cleanser_foam_uses_listing_answer_and_cleanser_cards() -> None:
    session_id = f"pytest-list-cleanser-foam-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "我想看所有洁面泡沫",
        },
    )

    text = collect_token_text(response.text)
    cards = collect_event_payloads(response.text, "product_card")
    done = collect_event_payloads(response.text, "done")[0]

    assert response.status_code == 200
    assert "当前商品库里按你的条件找到" in text
    assert "这些商品卡片已在下方展示" in text
    assert "缩到一两款" not in text
    assert cards
    assert all("beauty.cleanser" in card["product_types"] for card in cards)
    assert done["presentation_mode"] == "listing"
    assert any(item["kind"] == "product_type" and item["value"] == "beauty.cleanser" for item in done["filters"])


def test_chat_generic_skincare_request_returns_skincare_cards() -> None:
    session_id = f"pytest-generic-skincare-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款护肤品",
        },
    )

    cards = collect_event_payloads(response.text, "product_card")
    done = collect_event_payloads(response.text, "done")[0]

    assert response.status_code == 200
    assert cards
    assert all("beauty.skincare" in card["category_ids"] for card in cards)
    assert all(card["category"] == "美妆护肤" for card in cards)
    assert any(item["kind"] == "category" and item["value"] == "beauty.skincare" for item in done["filters"])


def test_chat_catalog_profile_category_requests_return_matching_cards() -> None:
    cases = [
        ("推荐一台笔记本电脑", "electronics.digital", "数码电子"),
        ("推荐一款酸奶", "food.beverage", "食品饮料"),
    ]

    for message, category_id, display_name in cases:
        session_id = f"pytest-profile-category-{uuid4()}"
        response = client.post(
            "/chat",
            json={
                "session_id": session_id,
                "message": message,
            },
        )

        cards = collect_event_payloads(response.text, "product_card")
        done = collect_event_payloads(response.text, "done")[0]

        assert response.status_code == 200
        assert cards
        assert all(category_id in card["category_ids"] for card in cards)
        assert all(card["category"] == display_name for card in cards)
        assert any(item["kind"] == "category" and item["value"] == category_id for item in done["filters"])


def test_chat_budget_sports_shoes_returns_only_shoe_cards() -> None:
    session_id = f"pytest-budget-shoes-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款1000元以下的运动鞋",
        },
    )

    cards = collect_event_payloads(response.text, "product_card")
    done = collect_event_payloads(response.text, "done")[0]

    assert response.status_code == 200
    assert cards
    assert all("clothes.sports_shoes" in card["product_types"] for card in cards)
    assert all(card["price"] <= 1000 for card in cards)
    assert not any("裤" in card["name"] or "帽" in card["name"] for card in cards)
    assert any(item["kind"] == "product_type" and item["value"] == "clothes.sports_shoes" for item in done["filters"])


def test_chat_same_product_type_new_request_releases_stale_budget() -> None:
    session_id = f"pytest-sports-shoes-release-budget-{uuid4()}"
    first = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "\u63a8\u8350\u4e00\u6b3e200\u4ee5\u4e0b\u7684\u8fd0\u52a8\u978b",
        },
    )
    second = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "\u63a8\u8350\u4e00\u6b3e\u8fd0\u52a8\u978b",
        },
    )

    assert first.status_code == 200
    assert "\u6ca1\u6709\u627e\u5230\u8db3\u591f\u5339\u914d" in collect_token_text(first.text)
    assert "event: product_card" not in first.text

    cards = collect_event_payloads(second.text, "product_card")
    done = collect_event_payloads(second.text, "done")[0]

    assert second.status_code == 200
    assert cards
    assert all("clothes.sports_shoes" in card["product_types"] for card in cards)
    assert done["filters"] == [{"kind": "product_type", "value": "clothes.sports_shoes"}]
    trace = client.get(f"/debug/traces/{done['trace_id']}")
    assert trace.status_code == 200
    assert trace.json()["metadata"]["scope_transition"]["type"] == "replace_product_scope"


def test_chat_generic_skincare_request_releases_stale_sports_filters() -> None:
    session_id = f"pytest-skincare-release-stale-sports-{uuid4()}"
    first = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款200以下的运动鞋",
        },
    )
    second = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款护肤品",
        },
    )

    assert first.status_code == 200
    assert "没有找到足够匹配" in collect_token_text(first.text)

    cards = collect_event_payloads(second.text, "product_card")
    done = collect_event_payloads(second.text, "done")[0]

    assert second.status_code == 200
    assert cards
    assert all("beauty.skincare" in card["category_ids"] for card in cards)
    assert done["filters"] == [{"kind": "category", "value": "beauty.skincare"}]


def test_chat_follow_up_excludes_lining_alias_and_refreshes_product_cards() -> None:
    session_id = f"pytest-lining-exclusion-{uuid4()}"
    first = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "\u63a8\u83501000\u4ee5\u4e0a\u7684\u8fd0\u52a8\u978b",
        },
    )
    second = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "\u4e0d\u8981 lining",
        },
    )

    first_cards = collect_event_payloads(first.text, "product_card")
    second_cards = collect_event_payloads(second.text, "product_card")
    done = collect_event_payloads(second.text, "done")[0]

    assert first.status_code == 200
    assert second.status_code == 200
    assert first_cards
    assert second_cards
    assert any(card["brand"] == "\u674e\u5b81" for card in first_cards)
    assert all(card["brand"] != "\u674e\u5b81" for card in second_cards)
    assert any(
        item["kind"] == "exclude_brand" and item["value"] == "\u674e\u5b81"
        for item in done["filters"]
    )


def test_debug_traces_capture_chat_turn() -> None:
    session_id = f"pytest-debug-trace-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "科颜氏和AHC哪个眼霜更适合干皮",
        },
    )
    traces = client.get(f"/debug/traces?session_id={session_id}")

    assert response.status_code == 200
    assert traces.status_code == 200
    payload = traces.json()
    assert payload
    latest = payload[0]
    assert latest["session_id"] == session_id
    assert latest["handler"] == "CompareHandler"
    assert latest["plan"]["intent"] == "compare"
    assert "comparison_card" in latest["event_counts"]
    assert latest["comparison_product_ids"]


def test_debug_trace_exposes_retrieval_filters_and_unsupported_constraints() -> None:
    session_id = f"pytest-debug-filter-trace-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款只看有货、支持企业开票的运动鞋",
        },
    )
    done = collect_event_payloads(response.text, "done")[0]
    trace = client.get(f"/debug/traces/{done['trace_id']}")

    assert response.status_code == 200
    assert trace.status_code == 200
    payload = trace.json()
    assert payload["trace_id"] == done["trace_id"]
    assert payload["filters"]["in_stock_only"] is True
    assert "invoice_policy" in payload["filters"]["unsupported_constraints"]
    assert payload["metadata"]["retrieval"]["applied_filters"]["in_stock_only"] is True
    assert "invoice_policy" in payload["metadata"]["retrieval"]["applied_filters"]["unsupported_constraints"]


def test_debug_trace_exposes_query_understanding_for_catalog_listing() -> None:
    session_id = f"pytest-query-understanding-trace-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "我想看所有洁面泡沫",
        },
    )
    done = collect_event_payloads(response.text, "done")[0]
    trace = client.get(f"/debug/traces/{done['trace_id']}")

    assert response.status_code == 200
    assert trace.status_code == 200
    payload = trace.json()
    query_understanding = payload["metadata"]["query_understanding"]
    product_discovery = payload["metadata"]["product_discovery"]
    assert query_understanding["presentation_mode"] == "listing"
    assert query_understanding["recommended_top_k"] == 20
    assert "catalog_listing" in query_understanding["signals"]
    assert product_discovery["presentation_mode"] == "listing"
    assert product_discovery["top_k"] == 20
    assert product_discovery["allow_llm_answer"] is False
    assert payload["metadata"]["catalog_listing"] is True


def test_debug_traces_capture_strategy_metadata_for_scenario_bundle() -> None:
    session_id = f"pytest-strategy-trace-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "去海岛玩，预算1000以内，帮我配一套防晒和穿搭",
        },
    )
    traces = client.get(f"/debug/traces?session_id={session_id}")

    assert response.status_code == 200
    payload = traces.json()
    assert payload
    latest = payload[0]
    assert latest["handler"] == "ScenarioBundleHandler"
    assert latest["metadata"]["strategy"]["bundle_id"] == "sanya_trip"
    assert latest["metadata"]["strategy"]["catalog_version"] == "2026-06-08"
    assert latest["metadata"]["strategy"]["within_budget"] is True
    assert latest["metadata"]["strategy"]["signals"]
    assert latest["metadata"]["strategy"]["bundle_plan"]["scenario"] == "sanya_trip"
    assert latest["metadata"]["strategy"]["slot_retrieval"]
    assert latest["metadata"]["strategy"]["bundle_grounding"]["safe"] is True


def test_debug_trace_exposes_scenario_routing_decision() -> None:
    session_id = f"pytest-scenario-routing-trace-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "马尔代夫度假，帮我配一套",
        },
    )
    done = collect_event_payloads(response.text, "done")[0]
    trace = client.get(f"/debug/traces/{done['trace_id']}")

    assert response.status_code == 200
    assert trace.status_code == 200
    payload = trace.json()
    routing = payload["metadata"]["scenario_routing"]
    assert routing["selected_bundle_id"] == "sanya_trip"
    assert routing["candidates"][0]["context_variables"]["destination"] == "马尔代夫"
    assert "context:destination=马尔代夫" in routing["candidates"][0]["signals"]


def test_debug_trace_detail_returns_404_for_missing_trace() -> None:
    response = client.get("/debug/traces/missing-trace-id")

    assert response.status_code == 404
