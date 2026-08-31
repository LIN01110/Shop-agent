"""
Purpose: 澄清问题生成：识别用户消息的宽泛主题，生成针对性的追问问题（预算/偏好/场景）。
"""

import re


def build_clarification_question(message: str, session) -> str:
    subject = get_clarification_subject(message, session)
    if not subject:
        return ""

    session.pending_subject = subject
    if subject == "手机":
        return "可以。我先确认一下：你更看重拍照、续航、性能还是性价比？预算大概是多少？"
    return f"可以。我先确认一下：你对{subject}更看重什么场景、预算和品牌偏好吗？"


def get_clarification_subject(message: str, session) -> str:
    subject = detect_clarification_subject(message)
    if not subject:
        return ""

    actionable_filters = [
        item
        for item in session.filters
        if item.kind != "product_type" and not (item.kind == "keyword" and item.value == subject)
    ]
    has_actionable_detail = bool(actionable_filters or session.exclusions)
    if has_actionable_detail:
        return ""
    return subject


def detect_clarification_subject(message: str) -> str:
    normalized = message.strip()
    if "手机" not in normalized:
        return ""
    has_budget = re.search(r"\d+\s*(元|块|以内|以下|左右|预算)", normalized)
    preference_words = ["拍照", "续航", "性能", "性价比", "轻薄", "游戏", "老人", "学生"]
    if has_budget or any(word in normalized for word in preference_words):
        return ""
    if any(word in normalized for word in ["推荐", "买", "想要", "需要"]):
        return "手机"
    return ""
