"""
Purpose: 会话记忆测试：验证历史对话压缩和用户画像更新。
"""

from server.session.memory import refresh_session_memory
from server.session.state import ConversationTurn, FilterCondition, SessionState


def test_refresh_session_memory_updates_profile_and_summary() -> None:
    session = SessionState(session_id="pytest-memory")
    session.history = [
        ConversationTurn(role="user", content=f"第 {index} 轮需求")
        for index in range(20)
    ]
    session.filters = [
        FilterCondition(kind="product_type", value="clothes.sports_shoes"),
        FilterCondition(kind="keyword", value="缓震"),
        FilterCondition(kind="max_price", value="1200"),
    ]
    session.exclusions = [FilterCondition(kind="exclude", value="太硬")]
    session.cart = [
        {
            "product_id": "p_clothes_009",
            "name": "HOKA Clifton 9",
            "brand": "HOKA",
            "quantity": 1,
        }
    ]

    refresh_session_memory(session, max_history_turns=12)

    assert len(session.history) == 12
    assert "最近需求" in session.history_summary
    assert "购物车" in session.history_summary
    assert session.user_profile.product_types == ["clothes.sports_shoes"]
    assert session.user_profile.keywords == ["缓震"]
    assert session.user_profile.preferred_brands == ["HOKA"]
    assert session.user_profile.disliked_terms == ["太硬"]
    assert session.user_profile.budget_ceiling == 1200
