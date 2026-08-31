"""
Purpose: 商品对比工具：对比两个或多个商品，生成对比回答和对比卡片。
"""

from typing import Any

from server.rag.post_process import SearchFilters
from server.tools.product_compare_presenter import (
    build_comparison_answer,
    build_comparison_card,
    build_comparison_product,
    build_dimensions,
    choose_recommendation,
    extract_budget,
    focus_score,
    format_list,
    infer_strengths,
    infer_tradeoffs,
)
from server.tools.product_compare_selection import (
    collect_comparison_cards,
    comparison_term_matches_card,
    first_matching_card,
    first_unseen_card,
    first_within_budget,
)
from server.tools.product_compare_terms import clean_comparison_term, cut_comparison_tail, extract_comparison_terms
from server.tools.product_search import ProductSearchTool


class ProductCompareTool:
    name = "compare_products"

    def __init__(self, search_tool: ProductSearchTool) -> None:
        self.search_tool = search_tool

    def run(self, query: str, filters: SearchFilters, top_k: int = 2) -> dict[str, Any]:
        cards = collect_comparison_cards(
            search_tool=self.search_tool,
            query=query,
            filters=filters,
            top_k=top_k,
        )
        return {
            "cards": cards,
            "comparison": build_comparison_card(query=query, cards=cards),
        }


__all__ = [
    "ProductCompareTool",
    "build_comparison_answer",
    "build_comparison_card",
    "build_comparison_product",
    "build_dimensions",
    "choose_recommendation",
    "clean_comparison_term",
    "collect_comparison_cards",
    "comparison_term_matches_card",
    "cut_comparison_tail",
    "extract_budget",
    "extract_comparison_terms",
    "first_matching_card",
    "first_unseen_card",
    "first_within_budget",
    "focus_score",
    "format_list",
    "infer_strengths",
    "infer_tradeoffs",
]
