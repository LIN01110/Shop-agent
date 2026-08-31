"""
Purpose: 购物车工具测试：验证购物车操作解析和执行。
"""

from server.session.state import SessionState
from server.tools.cart import CartTool


PRODUCT_CARD = {
    "id": "p_beauty_021",
    "name": "科颜氏牛油果保湿眼霜",
    "category": "美妆护肤",
    "brand": "科颜氏",
    "price": 210.0,
    "image_url": "http://127.0.0.1:8000/assets/products/p_beauty_021_live.jpg",
    "detail_url": "http://127.0.0.1:8000/products/p_beauty_021",
    "reason": "匹配眼霜需求",
}

SECOND_CARD = {
    **PRODUCT_CARD,
    "id": "p_beauty_016",
    "name": "AHC塑颜修护全脸眼霜",
    "brand": "AHC",
    "price": 139.0,
}

ANTA_CARD = {
    "id": "p_clothes_016",
    "name": "安踏 C202 GT Pro 马拉松碳板竞速跑鞋",
    "category": "服饰运动",
    "brand": "安踏",
    "price": 1299.0,
    "image_url": "http://127.0.0.1:8000/assets/products/p_clothes_016_live.jpg",
    "detail_url": "http://127.0.0.1:8000/products/p_clothes_016",
    "reason": "匹配运动鞋需求",
}


def make_session() -> SessionState:
    session = SessionState(session_id="pytest-cart")
    session.candidate_products = [PRODUCT_CARD["id"], SECOND_CARD["id"]]
    session.candidate_product_cards = [PRODUCT_CARD, SECOND_CARD]
    return session


def test_cart_tool_adds_latest_candidate() -> None:
    session = make_session()
    tool = CartTool()

    result = tool.run(message="把刚才那款加到购物车", session=session)

    assert "已将 科颜氏牛油果保湿眼霜 加入购物车" in result["answer"]
    assert result["cart"]["total_quantity"] == 1
    assert result["cart"]["total_price"] == 210.0
    assert result["cart"]["items"][0]["product_id"] == "p_beauty_021"


def test_cart_tool_adds_second_candidate_and_updates_quantity() -> None:
    session = make_session()
    tool = CartTool()

    tool.run(message="把第二个加到购物车", session=session)
    assert session.cart[0]["quantity"] == 1

    result = tool.run(message="把数量改成2", session=session)

    assert result["cart"]["total_quantity"] == 2
    assert result["cart"]["total_price"] == 278.0
    assert result["cart"]["items"][0]["product_id"] == "p_beauty_016"
    assert result["cart"]["items"][0]["quantity"] == 2


def test_cart_tool_removes_item() -> None:
    session = make_session()
    tool = CartTool()

    tool.run(message="把刚才那款加到购物车", session=session)
    result = tool.run(message="删除第一个", session=session)

    assert "已从购物车删除" in result["answer"]
    assert result["cart"]["is_empty"] is True


def test_cart_tool_updates_quantity_by_brand_reference() -> None:
    session = make_session()
    tool = CartTool()

    tool.run(message="把第一个加到购物车", session=session)
    tool.run(message="把第二个加到购物车", session=session)
    result = tool.run(message="把 AHC 数量改成 2", session=session)

    assert "AHC塑颜修护全脸眼霜 的数量改为 2" in result["answer"]
    assert result["cart"]["items"][0]["quantity"] == 1
    assert result["cart"]["items"][1]["product_id"] == "p_beauty_016"
    assert result["cart"]["items"][1]["quantity"] == 2


def test_cart_tool_requires_target_for_ambiguous_quantity_update() -> None:
    session = make_session()
    tool = CartTool()

    tool.run(message="把第一个加到购物车", session=session)
    tool.run(message="把第二个加到购物车", session=session)
    before = [dict(item) for item in session.cart]
    result = tool.run(message="把数量改成 2", session=session)

    assert "需要确认你要操作哪一个" in result["answer"]
    assert "我还没有修改购物车" in result["answer"]
    assert session.cart == before


def test_cart_tool_executes_multi_step_transaction_with_last_touched_item() -> None:
    session = make_session()
    tool = CartTool()

    tool.run(message="把第一个加到购物车", session=session)
    result = tool.run(message="删除第一个，然后把第二个加到购物车，数量改成 2", session=session)

    assert "已从购物车删除 科颜氏牛油果保湿眼霜" in result["answer"]
    assert "已将 AHC塑颜修护全脸眼霜 加入购物车" in result["answer"]
    assert "AHC塑颜修护全脸眼霜 的数量改为 2" in result["answer"]
    assert result["cart"]["items"][0]["product_id"] == "p_beauty_016"
    assert result["cart"]["items"][0]["quantity"] == 2


def test_cart_tool_does_not_partially_apply_ambiguous_multi_step_command() -> None:
    session = make_session()
    tool = CartTool()

    tool.run(message="把第一个加到购物车", session=session)
    tool.run(message="把第二个加到购物车", session=session)
    before = [dict(item) for item in session.cart]
    result = tool.run(message="把数量改成 2，然后删除第一个", session=session)

    assert "我还没有修改购物车" in result["answer"]
    assert session.cart == before


def test_cart_tool_clears_cart() -> None:
    session = make_session()
    tool = CartTool()

    tool.run(message="把第一个加到购物车", session=session)
    result = tool.run(message="清空购物车", session=session)

    assert "已清空购物车" in result["answer"]
    assert result["cart"]["is_empty"] is True


def test_cart_tool_asks_confirmation_for_purchase_quantity_by_brand() -> None:
    session = make_session()
    session.candidate_product_cards = [PRODUCT_CARD, ANTA_CARD, SECOND_CARD]
    tool = CartTool()

    result = tool.run(message="我想买两双安踏", session=session)

    assert "确认把它加入购物车吗" in result["answer"]
    assert "安踏 C202 GT Pro 马拉松碳板竞速跑鞋" in result["answer"]
    assert result["needs_clarification"] is True
    assert result["cart"]["total_quantity"] == 0
    assert session.pending_cart_action["quantity"] == 2

    confirmed = tool.run(message="确认", session=session)

    assert "已将 安踏 C202 GT Pro 马拉松碳板竞速跑鞋 加入购物车，数量 2" in confirmed["answer"]
    assert confirmed["needs_clarification"] is False
    assert confirmed["cart"]["total_quantity"] == 2
    assert confirmed["cart"]["items"][0]["product_id"] == "p_clothes_016"
    assert confirmed["cart"]["items"][0]["quantity"] == 2


def test_cart_tool_purchase_confirmation_sets_existing_item_quantity() -> None:
    session = make_session()
    session.candidate_product_cards = [ANTA_CARD]
    tool = CartTool()

    tool.run(message="把安踏加入购物车", session=session)
    pending = tool.run(message="我想买两双安踏", session=session)
    result = tool.run(message="确认", session=session)

    assert pending["needs_clarification"] is True
    assert result["cart"]["total_quantity"] == 2
    assert result["cart"]["items"][0]["product_id"] == "p_clothes_016"
    assert result["cart"]["items"][0]["quantity"] == 2


def test_cart_tool_purchase_confirmation_requires_choice_for_multiple_brand_matches() -> None:
    session = make_session()
    second_anta = {**ANTA_CARD, "id": "p_clothes_012", "name": "安踏 KT9 氮科技实战篮球鞋"}
    session.candidate_product_cards = [ANTA_CARD, second_anta]
    tool = CartTool()

    pending = tool.run(message="我想买两双安踏", session=session)
    selected = tool.run(message="第二款", session=session)

    assert "你想买哪一款" in pending["answer"]
    assert pending["cart"]["total_quantity"] == 0
    assert selected["cart"]["total_quantity"] == 2
    assert selected["cart"]["items"][0]["product_id"] == "p_clothes_012"


def test_cart_tool_purchase_confirmation_does_not_offer_unmatched_candidates() -> None:
    session = make_session()
    session.candidate_product_cards = [PRODUCT_CARD, SECOND_CARD]
    tool = CartTool()

    result = tool.run(message="我想买两双安踏", session=session)

    assert "当前推荐列表里没有匹配到 安踏" in result["answer"]
    assert "推荐安踏运动鞋" in result["answer"]
    assert "科颜氏牛油果保湿眼霜" not in result["answer"]
    assert result["needs_clarification"] is True
    assert result["cart"]["total_quantity"] == 0
    assert session.pending_cart_action == {}
