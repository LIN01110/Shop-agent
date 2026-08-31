"""
Purpose: 组合优化器测试：验证多槽位组合选择和预算约束。
"""

from server.agent.bundle_optimizer import optimize_bundle_selection
from server.agent.scenarios import ScenarioSlot
from server.rag.post_process import SearchFilters


def make_slot(label: str, optional: bool = False) -> ScenarioSlot:
    return ScenarioSlot(label=label, query=label, filters=SearchFilters(), optional=optional)


def make_card(product_id: str, price: float, score: float) -> dict:
    return {"id": product_id, "name": product_id, "price": price, "score": score}


def test_bundle_optimizer_selects_combination_within_total_budget() -> None:
    shoes = make_slot("训练跑鞋")
    pants = make_slot("运动裤")

    result = optimize_bundle_selection(
        [
            (shoes, [make_card("expensive_shoe", 900, 50), make_card("budget_shoe", 399, 30)]),
            (pants, [make_card("pants", 299, 20)]),
        ],
        total_budget=700,
    )

    selected_ids = [cards[0]["id"] for _, cards in result.grouped_cards if cards]

    assert result.within_budget is True
    assert result.total_price <= 700
    assert selected_ids == ["budget_shoe", "pants"]


def test_bundle_optimizer_can_drop_optional_slot_under_budget_pressure() -> None:
    required = make_slot("防晒保护")
    optional = make_slot("拍照记录", optional=True)

    result = optimize_bundle_selection(
        [
            (required, [make_card("sunscreen", 170, 10)]),
            (optional, [make_card("phone", 3999, 100)]),
        ],
        total_budget=500,
    )

    assert result.total_price == 170
    assert result.grouped_cards[0][1][0]["id"] == "sunscreen"
    assert result.grouped_cards[1][1] == []
