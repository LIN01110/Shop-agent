"""
Purpose: 商品发现测试：验证展示模式和候选池策略。
"""

from server.agent.product_discovery import (
    build_product_discovery_policy,
    resolve_product_cards_for_answer,
)
from server.agent.semantic_schema import SemanticPlan


def test_listing_policy_disables_llm_and_uses_larger_candidate_pool() -> None:
    plan = SemanticPlan(
        intent="browse",
        presentation_mode="listing",
        query_understanding={"recommended_top_k": 20},
    )

    policy = build_product_discovery_policy(plan)

    assert policy.presentation_mode == "listing"
    assert policy.top_k == 20
    assert policy.allow_llm_answer is False
    assert policy.bind_cards_to_answer is False
    assert policy.as_metadata()["policy_reasons"] == ["catalog_listing"]


def test_single_policy_allows_llm_and_answer_card_binding() -> None:
    plan = SemanticPlan(intent="recommend", presentation_mode="single")

    policy = build_product_discovery_policy(plan)

    assert policy.presentation_mode == "single"
    assert policy.top_k == 5
    assert policy.allow_llm_answer is True
    assert policy.bind_cards_to_answer is True


def test_listing_resolution_keeps_all_filtered_cards_even_if_answer_mentions_first() -> None:
    cards = [
        {"id": "alpha", "name": "Alpha Foam Cleanser", "brand": "Alpha"},
        {"id": "beta", "name": "Beta Foam Cleanser", "brand": "Beta"},
    ]
    policy = build_product_discovery_policy(
        SemanticPlan(intent="browse", presentation_mode="listing", query_understanding={"recommended_top_k": 20})
    )

    result = resolve_product_cards_for_answer(
        answer="当前找到 2 款洁面乳。Alpha Foam Cleanser 是第一款。",
        cards=cards,
        policy=policy,
    )

    assert [card["id"] for card in result.cards] == ["alpha", "beta"]
    assert result.metadata["reason"] == "policy_keeps_filtered_cards"
    assert result.metadata["dropped_product_ids"] == []


def test_single_resolution_binds_cards_to_answer_mentions() -> None:
    cards = [
        {"id": "alpha", "name": "Alpha Running Shoe", "brand": "Alpha"},
        {"id": "beta", "name": "Beta Running Shoe", "brand": "Beta"},
    ]
    policy = build_product_discovery_policy(SemanticPlan(intent="recommend", presentation_mode="single"))

    result = resolve_product_cards_for_answer(
        answer="Beta Running Shoe 更适合你的日常训练需求。",
        cards=cards,
        policy=policy,
    )

    assert [card["id"] for card in result.cards] == ["beta"]
    assert result.metadata["reason"] == "answer_product_mentions"
    assert result.metadata["dropped_product_ids"] == ["alpha"]


def test_single_resolution_suppresses_cards_when_answer_declines_recommendation() -> None:
    cards = [
        {"id": "ahc", "name": "AHC Eye Cream", "brand": "AHC"},
        {"id": "lancome", "name": "Lancome Toner", "brand": "Lancome"},
    ]
    policy = build_product_discovery_policy(SemanticPlan(intent="recommend", presentation_mode="single"))

    result = resolve_product_cards_for_answer(
        answer="\u5f53\u524d\u5019\u9009\u5546\u54c1\u4e2d\u6ca1\u6709\u6d17\u53d1\u6db2\u7c7b\u5546\u54c1\uff0c\u65e0\u6cd5\u4e3a\u4f60\u63a8\u8350\u7b26\u5408\u9700\u6c42\u7684\u5546\u54c1\u3002",
        cards=cards,
        policy=policy,
    )

    assert result.cards == []
    assert result.metadata["reason"] == "answer_declines_recommendation"
    assert result.metadata["dropped_product_ids"] == ["ahc", "lancome"]
