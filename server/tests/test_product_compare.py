"""
Purpose: 商品对比测试：验证对比工具返回对比卡片和推荐。
"""

from pathlib import Path

from server.rag.post_process import SearchFilters
from server.rag.vector_store import LocalJsonVectorStore
from server.tools.product_compare import (
    ProductCompareTool,
    build_comparison_answer,
    extract_comparison_terms,
)
from server.tools.product_search import ProductSearchTool


ROOT_DIR = Path(__file__).resolve().parents[2]


def make_compare_tool() -> ProductCompareTool:
    store = LocalJsonVectorStore(ROOT_DIR / "data" / "products_ref.json")
    search_tool = ProductSearchTool(store)
    return ProductCompareTool(search_tool)


def test_extract_comparison_terms_handles_brand_pair() -> None:
    terms = extract_comparison_terms("科颜氏和AHC哪个眼霜更适合干皮")

    assert terms == ["科颜氏", "AHC"]


def test_compare_tool_searches_each_side_of_brand_pair() -> None:
    tool = make_compare_tool()

    result = tool.run(
        query="科颜氏和AHC哪个眼霜更适合干皮",
        filters=SearchFilters(keywords=["眼霜"]),
    )

    brands = {card["brand"] for card in result["cards"]}
    assert {"科颜氏", "AHC"} <= brands
    assert result["comparison"]["recommendation"]["product_id"]


def test_build_comparison_answer_is_grounded_in_cards() -> None:
    tool = make_compare_tool()
    result = tool.run(
        query="小米和OPPO哪个拍照手机更适合预算4000",
        filters=SearchFilters(max_price=4000, keywords=["手机", "拍照"]),
    )

    answer = build_comparison_answer(result["comparison"])

    assert "当前商品库" in answer
    assert "价格、品牌和结论都只来自当前候选商品" in answer
    assert any(card["price"] <= 4000 for card in result["cards"])
    recommended_id = result["comparison"]["recommendation"]["product_id"]
    recommended = next(card for card in result["cards"] if card["id"] == recommended_id)
    assert recommended["price"] <= 4000


def test_compare_tool_preserves_each_side_with_synonym_filters() -> None:
    tool = make_compare_tool()

    result = tool.run(
        query="小米和OPPO哪个拍照手机更适合预算4000",
        filters=SearchFilters(max_price=4000, keywords=["手机", "拍照"]),
    )

    brands = {card["brand"] for card in result["cards"]}
    assert any("小米" in brand for brand in brands)
    assert "OPPO" in brands
