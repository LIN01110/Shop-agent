"""
Purpose: 用户意图定义：UserIntent 枚举（BROWSING/BUYING/COMPARE/CART）和意图检测函数。
"""

from enum import StrEnum


class UserIntent(StrEnum):
    BROWSING = "browsing"
    BUYING = "buying"
    COMPARE = "compare"
    CART = "cart"


def detect_intent(message: str) -> UserIntent:
    text = message.lower()
    if any(word in text for word in ["对比", "比较", "哪个更", "哪个", "哪款", "谁更", "区别"]):
        return UserIntent.COMPARE
    if any(
        word in text
        for word in [
            "购物车",
            "加购",
            "加到",
            "加入",
            "添加",
            "添加到",
            "放到",
            "放进",
            "下单",
            "购买",
            "结算",
            "删除",
            "删掉",
            "移除",
            "去掉",
            "拿掉",
            "清空",
            "数量",
            "改成",
            "改为",
            "设为",
            "加一件",
            "减一件",
        ]
    ):
        return UserIntent.CART
    if any(word in text for word in ["预算", "以内", "推荐", "适合", "要", "不要"]):
        return UserIntent.BUYING
    return UserIntent.BROWSING
