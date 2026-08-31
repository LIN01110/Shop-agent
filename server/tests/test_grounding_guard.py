"""
Purpose: Grounding Guard 测试：验证防幻觉 4 条规则拦截。
"""

from server.agent.grounding import guard_grounded_answer


CARD = {
    "id": "p_beauty_021",
    "name": "科颜氏牛油果保湿眼霜",
    "brand": "科颜氏",
    "price": 210.0,
}


def test_guard_allows_candidate_name_and_price() -> None:
    result = guard_grounded_answer(
        answer="推荐科颜氏牛油果保湿眼霜，价格210元，适合关注保湿的人。",
        cards=[CARD],
        fallback_answer="fallback",
    )

    assert result.safe is True
    assert result.answer.startswith("推荐科颜氏")
    assert result.citations == [
        {"source": "product_catalog", "product_id": "p_beauty_021", "fields": ["name", "brand", "price"]}
    ]


def test_guard_uses_structured_evidence_as_fact_source() -> None:
    card = {
        **CARD,
        "name": "展示层名称",
        "price": 999.0,
        "evidence": {
            "source": "product_catalog",
            "product_id": "p_beauty_021",
            "name": "科颜氏牛油果保湿眼霜",
            "brand": "科颜氏",
            "price": 210.0,
        },
    }

    result = guard_grounded_answer(
        answer="推荐科颜氏牛油果保湿眼霜，价格210元。",
        cards=[card],
        fallback_answer="fallback",
    )

    assert result.safe is True
    assert result.citations


def test_guard_falls_back_on_unsupported_promotion() -> None:
    result = guard_grounded_answer(
        answer="推荐科颜氏牛油果保湿眼霜，现在有50元优惠券。",
        cards=[CARD],
        fallback_answer="fallback",
    )

    assert result.safe is False
    assert result.answer == "fallback"
    assert any("unsupported_promotion_terms" in item for item in result.violations)


def test_guard_falls_back_on_unsupported_price() -> None:
    result = guard_grounded_answer(
        answer="推荐科颜氏牛油果保湿眼霜，今天只要99元。",
        cards=[CARD],
        fallback_answer="fallback",
    )

    assert result.safe is False
    assert result.answer == "fallback"
    assert any("unsupported_prices" in item for item in result.violations)


def test_guard_falls_back_when_no_candidate_is_referenced() -> None:
    result = guard_grounded_answer(
        answer="这款很适合你，质地滋润。",
        cards=[CARD],
        fallback_answer="fallback",
    )

    assert result.safe is False
    assert result.answer == "fallback"
    assert "missing_candidate_reference" in result.violations
