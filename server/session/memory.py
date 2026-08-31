"""
Purpose: 会话记忆刷新：维护用户画像摘要，压缩历史对话（保留最近 12 轮），更新会话内存。
"""

from server.rag.taxonomy import product_type_display_names
from server.session.state import ConversationTurn, SessionState


MAX_HISTORY_TURNS = 12
MAX_PROFILE_ITEMS = 12


def refresh_session_memory(session: SessionState, *, max_history_turns: int = MAX_HISTORY_TURNS) -> None:
    update_user_profile(session)
    compact_history(session, max_history_turns=max_history_turns)


def update_user_profile(session: SessionState) -> None:
    profile = session.user_profile
    for item in session.filters:
        if item.kind == "product_type":
            append_unique(profile.product_types, item.value)
        elif item.kind == "keyword":
            append_unique(profile.keywords, item.value)
        elif item.kind == "max_price":
            profile.budget_ceiling = parse_budget(item.value) or profile.budget_ceiling

    for item in session.exclusions:
        append_unique(profile.disliked_terms, item.value)

    for item in session.cart:
        brand = str(item.get("brand", "")).strip()
        if brand:
            append_unique(profile.preferred_brands, brand)

    for card in session.candidate_product_cards[:5]:
        for product_type in card.get("product_types", []) or []:
            append_unique(profile.product_types, str(product_type))

    trim_list(profile.product_types)
    trim_list(profile.keywords)
    trim_list(profile.preferred_brands)
    trim_list(profile.disliked_terms)


def compact_history(session: SessionState, *, max_history_turns: int) -> None:
    if len(session.history) <= max_history_turns:
        session.history_summary = build_history_summary(session)
        return

    session.history = session.history[-max_history_turns:]
    session.history_summary = build_history_summary(session)


def build_history_summary(session: SessionState) -> str:
    parts: list[str] = []
    recent_user_messages = [turn.content.strip() for turn in session.history if turn.role == "user" and turn.content.strip()]
    if recent_user_messages:
        parts.append("最近需求：" + " / ".join(recent_user_messages[-3:]))

    profile = session.user_profile
    profile_parts: list[str] = []
    if profile.product_types:
        names = product_type_display_names(profile.product_types)
        profile_parts.append("品类偏好 " + "、".join(names or profile.product_types))
    if profile.keywords:
        profile_parts.append("关注点 " + "、".join(profile.keywords[-5:]))
    if profile.preferred_brands:
        profile_parts.append("互动品牌 " + "、".join(profile.preferred_brands[-5:]))
    if profile.budget_ceiling is not None:
        profile_parts.append(f"预算上限约 {int(profile.budget_ceiling)}")
    if profile.disliked_terms:
        profile_parts.append("排除 " + "、".join(profile.disliked_terms[-5:]))
    if profile_parts:
        parts.append("偏好画像：" + "；".join(profile_parts))

    if session.cart:
        cart_items = [
            f"{item.get('name', '')} x{int(item.get('quantity', 0))}"
            for item in session.cart[-3:]
            if item.get("name")
        ]
        if cart_items:
            parts.append("购物车：" + "；".join(cart_items))

    return "。".join(parts)


def append_unique(values: list[str], value: str) -> None:
    normalized = value.strip()
    if normalized and normalized not in values:
        values.append(normalized)


def trim_list(values: list[str]) -> None:
    if len(values) > MAX_PROFILE_ITEMS:
        del values[:-MAX_PROFILE_ITEMS]


def parse_budget(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def reset_session_state(session_id: str) -> SessionState:
    return SessionState(session_id=session_id)
