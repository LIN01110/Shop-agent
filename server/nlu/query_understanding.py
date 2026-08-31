"""
Purpose: 查询理解：PresentationMode（展示模式：单件/列表），识别列表类请求（所有/全部）。
"""

from dataclasses import dataclass, field
from typing import Literal


PresentationMode = Literal["auto", "single", "listing"]

LISTING_TERMS = (
    "所有",
    "全部",
    "全都",
    "有哪些",
    "都有哪些",
    "有哪几",
    "看一下",
    "看看",
    "浏览",
    "列表",
    "清单",
    "展示",
    "show all",
    "list",
)
SINGLE_ITEM_TERMS = (
    "一款",
    "一个",
    "一件",
    "一双",
    "一瓶",
    "一支",
    "一台",
    "推荐一",
    "最推荐",
    "首选",
)
ACTION_TERMS = (
    "购物车",
    "加到",
    "加入",
    "加购",
    "添加",
    "下单",
    "结算",
    "删除",
    "移除",
    "对比",
    "比较",
)


@dataclass(frozen=True)
class QueryUnderstanding:
    presentation_mode: PresentationMode = "auto"
    signals: tuple[str, ...] = ()
    listing_terms: tuple[str, ...] = ()
    single_item_terms: tuple[str, ...] = ()
    normalized_query: str = ""
    recommended_top_k: int = 5
    debug: dict[str, object] = field(default_factory=dict)

    def as_metadata(self) -> dict[str, object]:
        return {
            "presentation_mode": self.presentation_mode,
            "signals": list(self.signals),
            "listing_terms": list(self.listing_terms),
            "single_item_terms": list(self.single_item_terms),
            "normalized_query": self.normalized_query,
            "recommended_top_k": self.recommended_top_k,
            "debug": dict(self.debug),
        }


def understand_query(message: str) -> QueryUnderstanding:
    text = normalize_query(message)
    listing_terms = tuple(term for term in LISTING_TERMS if term in text)
    single_item_terms = tuple(term for term in SINGLE_ITEM_TERMS if term in text)
    action_terms = tuple(term for term in ACTION_TERMS if term in text)

    signals: list[str] = []
    presentation_mode: PresentationMode = "auto"
    if listing_terms and not single_item_terms and not action_terms:
        presentation_mode = "listing"
        signals.append("catalog_listing")
    elif single_item_terms:
        presentation_mode = "single"
        signals.append("single_product_request")

    return QueryUnderstanding(
        presentation_mode=presentation_mode,
        signals=tuple(signals),
        listing_terms=listing_terms,
        single_item_terms=single_item_terms,
        normalized_query=text,
        recommended_top_k=20 if presentation_mode == "listing" else 5,
        debug={
            "action_terms": list(action_terms),
        },
    )


def is_catalog_listing_request(message: str) -> bool:
    return understand_query(message).presentation_mode == "listing"


def normalize_query(message: str) -> str:
    return str(message).strip().lower()
