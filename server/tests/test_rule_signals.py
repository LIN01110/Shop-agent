"""
Purpose: 规则信号测试：验证路由决策（确定性/LLM/澄清）。
"""

from server.agent.rule_signals import build_rule_signals
from server.agent.semantic_rules import build_rule_plan
from server.session.state import SessionState


def make_session() -> SessionState:
    return SessionState(session_id="pytest-rule-signals")


def test_rule_signals_routes_clear_product_search_to_deterministic_path() -> None:
    session = make_session()
    plan = build_rule_plan("推荐防晒霜，预算300以内", session)

    signals = build_rule_signals("推荐防晒霜，预算300以内", plan, session)

    assert signals.route == "deterministic"
    assert signals.is_simple_product_search
    assert signals.product_types == ("beauty.sunscreen",)
    assert "simple_product_search" in signals.reasons


def test_rule_signals_routes_ambiguous_budget_to_planner_path() -> None:
    session = make_session()
    plan = build_rule_plan("推荐一款运动鞋，学生党别太贵", session)

    signals = build_rule_signals("推荐一款运动鞋，学生党别太贵", plan, session)

    assert signals.route == "planner"
    assert signals.has_ambiguous_budget
    assert "ambiguous_budget" in signals.reasons


def test_rule_signals_routes_context_reference_to_planner_path() -> None:
    session = make_session()
    session.candidate_product_cards = [
        {"id": "p1", "name": "Demo Shoe", "brand": "Demo", "price": 199}
    ]
    plan = build_rule_plan("这个换成便宜点的", session)

    signals = build_rule_signals("这个换成便宜点的", plan, session)

    assert signals.route == "planner"
    assert signals.has_reference
    assert "context_reference" in signals.reasons
