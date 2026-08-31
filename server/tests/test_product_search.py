"""
Purpose: 商品搜索测试：验证检索 Pipeline 和商品卡片格式化。
"""

import asyncio
import time
from pathlib import Path

from server.agent.recommendation_handler import execute_product_search_with_budget
from server.rag.post_process import SearchFilters
from server.rag.taxonomy import infer_product_type_ids
from server.rag.vector_store import LocalJsonVectorStore, VectorDocument
from server.tools.product_search import ProductSearchTool, build_reason, to_product_card


ROOT_DIR = Path(__file__).resolve().parents[2]


def make_tool() -> ProductSearchTool:
    store = LocalJsonVectorStore(ROOT_DIR / "data" / "products_ref.json")
    return ProductSearchTool(store)


def test_product_search_respects_budget_and_keyword() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐一款保湿眼霜",
        filters=SearchFilters(max_price=250, keywords=["保湿", "眼霜"]),
        top_k=3,
    )

    assert cards
    assert cards[0]["id"] == "p_beauty_021"
    assert cards[0]["image_url"].endswith("/assets/products/p_beauty_021_live.jpg")
    assert cards[0]["detail_url"].endswith("/products/p_beauty_021")
    assert all(card["price"] <= 250 for card in cards)
    assert cards[0]["evidence"]["source"] == "product_catalog"
    assert cards[0]["evidence"]["product_id"] == cards[0]["id"]


def test_product_search_exposes_pipeline_diagnostics() -> None:
    tool = make_tool()

    result = tool.run_with_diagnostics(
        query="推荐一款保湿眼霜",
        filters=SearchFilters(max_price=250, keywords=["保湿", "眼霜"]),
        top_k=3,
    )

    step_names = [step.name for step in result.diagnostics.steps]

    assert result.cards
    assert result.diagnostics.requested_top_k == 3
    assert result.diagnostics.candidate_top_k >= 30
    assert "store_prefilter_recall" in step_names
    assert "dedupe_and_rerank" in step_names
    assert result.diagnostics.applied_filters["keywords"] == ["保湿", "眼霜"]


def test_product_search_gateway_degrades_on_retrieval_timeout() -> None:
    class SlowSearchTool:
        def run_with_diagnostics(self, *, query, filters, top_k):
            time.sleep(0.05)
            return type("Result", (), {"cards": [{"id": "late"}], "diagnostics": None})()

    result = asyncio.run(
        execute_product_search_with_budget(
            product_search=SlowSearchTool(),
            query="推荐一款眼霜",
            filters=SearchFilters(),
            top_k=3,
            timeout_seconds=0.001,
        )
    )

    assert result.cards == []
    assert result.degraded is True
    assert result.reason == "retrieval_timeout"


def test_product_search_filters_static_in_stock_metadata() -> None:
    store = LocalJsonVectorStore.from_documents(
        [
            VectorDocument(
                id="shoe_in_stock",
                text="跑步鞋 缓震 有货",
                metadata={
                    "id": "shoe_in_stock",
                    "name": "有货缓震跑步鞋",
                    "category": "服饰运动",
                    "sub_category": "运动鞋",
                    "brand": "Demo",
                    "price": 399,
                    "stock": 3,
                    "tags": ["跑步鞋"],
                    "description": "适合日常跑步。",
                },
            ),
            VectorDocument(
                id="shoe_out_of_stock",
                text="跑步鞋 缓震 无货",
                metadata={
                    "id": "shoe_out_of_stock",
                    "name": "无货缓震跑步鞋",
                    "category": "服饰运动",
                    "sub_category": "运动鞋",
                    "brand": "Demo",
                    "price": 299,
                    "stock": 0,
                    "tags": ["跑步鞋"],
                    "description": "适合日常跑步。",
                },
            ),
        ]
    )
    tool = ProductSearchTool(store)

    cards = tool.run(
        query="推荐有货跑步鞋",
        filters=SearchFilters(product_types=["clothes.sports_shoes"], in_stock_only=True),
        top_k=5,
    )

    assert [card["id"] for card in cards] == ["shoe_in_stock"]
    assert cards[0]["stock"] == 3


def test_product_evidence_contains_field_level_business_sources() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐一款保湿眼霜",
        filters=SearchFilters(max_price=250, keywords=["保湿", "眼霜"]),
        top_k=1,
    )
    evidence = cards[0]["evidence"]

    assert evidence["field_sources"]["price"]["source"] == "mock_pricing_service"
    assert evidence["field_sources"]["price"]["freshness"] == "mock_service"
    assert evidence["field_sources"]["price"]["authoritative"] is False
    assert evidence["field_sources"]["stock"]["source"] == "mock_inventory_service"
    assert evidence["field_sources"]["invoice_policy"]["source"] == "mock_invoice_policy_service"
    assert "sku_id" in evidence["missing_fields"]
    assert "invoice_policy" not in evidence["missing_fields"]
    assert "coupon_policy" not in evidence["missing_fields"]
    assert "retrieval_score" in evidence
    assert "rerank_score" in evidence


def test_product_card_and_evidence_share_same_business_identity() -> None:
    store = LocalJsonVectorStore.from_documents(
        [
            VectorDocument(
                id="shoe_demo",
                text="sports shoe running cushioned",
                metadata={
                    "id": "shoe_demo",
                    "name": "Demo running shoe",
                    "category": "Sports",
                    "sub_category": "Shoes",
                    "brand": "Demo",
                    "price": 399,
                    "stock": 8,
                    "tags": ["running shoe"],
                    "description": "Cushioned daily running shoe.",
                    "product_types": ["clothes.sports_shoes"],
                },
            )
        ]
    )
    tool = ProductSearchTool(store)

    cards = tool.run(
        query="sports shoes under 1000",
        filters=SearchFilters(max_price=1000, product_types=["clothes.sports_shoes"]),
        top_k=3,
    )

    assert cards
    for card in cards:
        evidence = card["evidence"]
        assert evidence["product_id"] == card["id"]
        assert evidence["sku_id"] == card["sku_id"]
        assert evidence["price"] == float(card["price"])
        assert evidence["stock"] == card["stock"]
        assert evidence["field_sources"]["price"]["source"] == "mock_pricing_service"
        assert evidence["field_sources"]["stock"]["source"] == "mock_inventory_service"
        assert evidence["field_sources"]["after_sales_policy"]["source"] == "mock_after_sales_policy_service"
        assert evidence["field_sources"]["invoice_policy"]["source"] == "mock_invoice_policy_service"


def test_product_search_excludes_brand() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐敏感肌可以用的卸妆油，不要芳珂",
        filters=SearchFilters(max_price=300, keywords=["敏感肌", "卸妆油"], exclusions=["芳珂"]),
        top_k=5,
    )

    assert all(card["brand"] != "芳珂" for card in cards)


def test_product_search_does_not_relax_required_keywords_to_unrelated_items() -> None:
    tool = make_tool()

    cards = tool.run(
        query="预算500以内 跑鞋 轻量",
        filters=SearchFilters(max_price=500, keywords=["跑鞋", "轻量"]),
        top_k=5,
    )

    assert cards == []


def test_product_search_filters_sports_shoes_without_sports_pants() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐一款运动鞋",
        filters=SearchFilters(product_types=["clothes.sports_shoes"]),
        top_k=5,
    )

    assert cards
    assert all("鞋" in card["name"] for card in cards)
    assert not any("裤" in card["name"] for card in cards)
    assert all("clothes.sports_shoes" in card["product_types"] for card in cards)


def test_product_search_filters_cleanser_request_to_cleanser_cards() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐一款500元以下的洁面乳",
        filters=SearchFilters(max_price=500, product_types=["beauty.cleanser"]),
        top_k=5,
    )

    assert cards
    assert all("beauty.cleanser" in card["product_types"] for card in cards)
    assert all("洁面" in card["name"] or "洗面奶" in card["name"] for card in cards)
    assert not any("食品饮料" in card["category"] for card in cards)


def test_product_search_filters_generic_skincare_category() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐一款护肤品",
        filters=SearchFilters(categories=["beauty.skincare"]),
        top_k=5,
    )

    assert cards
    assert all("beauty.skincare" in card["category_ids"] for card in cards)
    assert all(card["category"] == "美妆护肤" for card in cards)
    assert not any("服饰运动" in card["category"] or "食品饮料" in card["category"] for card in cards)


def test_product_search_filters_other_business_categories() -> None:
    tool = make_tool()

    electronics = tool.run(
        query="推荐一款数码产品",
        filters=SearchFilters(categories=["electronics.digital"]),
        top_k=5,
    )
    food = tool.run(
        query="推荐一款食品",
        filters=SearchFilters(categories=["food.beverage"]),
        top_k=5,
    )

    assert electronics
    assert all("electronics.digital" in card["category_ids"] for card in electronics)
    assert all(card["category"] == "数码电子" for card in electronics)
    assert food
    assert all("food.beverage" in card["category_ids"] for card in food)
    assert all(card["category"] == "食品饮料" for card in food)


def test_product_search_filters_cleanser_aliases_to_cleanser_cards() -> None:
    tool = make_tool()

    for query in ["推荐一款洗面乳", "推荐一款洗面泡沫", "推荐一款西面泡沫"]:
        cards = tool.run(
            query=query,
            filters=SearchFilters(product_types=["beauty.cleanser"]),
            top_k=5,
        )

        assert cards
        assert all("beauty.cleanser" in card["product_types"] for card in cards)
        assert not any("食品饮料" in card["category"] for card in cards)


def test_product_search_retries_when_vector_type_prefilter_is_stale() -> None:
    store = LocalJsonVectorStore.from_documents(
        [
            VectorDocument(
                id="cleanser_without_indexed_type",
                text="洁面乳 泡沫洁面 温和清洁",
                metadata={
                    "id": "cleanser_without_indexed_type",
                    "name": "温和泡沫洁面乳",
                    "category": "美妆护肤",
                    "sub_category": "洁面乳",
                    "brand": "Demo",
                    "price": 88,
                    "stock": 3,
                    "tags": ["洁面乳"],
                    "description": "温和清洁。",
                },
            )
        ]
    )
    original_query = store.query

    def stale_prefilter_query(query, top_k=5, filters=None):
        if filters is not None and filters.product_types:
            return []
        return original_query(query, top_k=top_k, filters=filters)

    store.query = stale_prefilter_query  # type: ignore[method-assign]
    tool = ProductSearchTool(store)

    cards = tool.run(
        query="推荐一款洁面乳",
        filters=SearchFilters(product_types=["beauty.cleanser"]),
        top_k=3,
    )

    assert [card["id"] for card in cards] == ["cleanser_without_indexed_type"]


def test_product_search_budgeted_sports_shoes_do_not_include_other_sports_goods() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐一款1000元以下的运动鞋",
        filters=SearchFilters(max_price=1000, product_types=["clothes.sports_shoes"]),
        top_k=5,
    )

    assert cards
    assert all(card["price"] <= 1000 for card in cards)
    assert all("clothes.sports_shoes" in card["product_types"] for card in cards)
    assert not any("裤" in card["name"] or "帽" in card["name"] for card in cards)


def test_product_search_applies_negated_product_type_as_hard_exclusion() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐运动鞋，不要运动裤",
        filters=SearchFilters(
            product_types=["clothes.sports_shoes", "clothes.sports_pants"],
            excluded_product_types=["clothes.sports_pants"],
        ),
        top_k=10,
    )

    assert cards
    assert all("clothes.sports_shoes" in card["product_types"] for card in cards)
    assert not any("clothes.sports_pants" in card["product_types"] for card in cards)


def test_product_search_applies_structured_price_floor_and_brand_exclusion() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐一款1000元以上的跑步鞋，不要耐克",
        filters=SearchFilters(
            min_price=1000,
            product_types=["clothes.sports_shoes"],
            excluded_brands=["耐克", "Nike"],
        ),
        top_k=5,
    )

    assert cards
    assert all(card["price"] >= 1000 for card in cards)
    assert all("clothes.sports_shoes" in card["product_types"] for card in cards)
    assert all(card["brand"] not in {"耐克", "Nike"} for card in cards)


def test_product_search_expands_brand_aliases_for_negative_constraints() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐1000元以上的运动鞋，不要耐克",
        filters=SearchFilters(
            min_price=1000,
            product_types=["clothes.sports_shoes"],
            exclusions=["耐克"],
        ),
        top_k=10,
    )

    assert cards
    assert all(card["brand"].lower() != "nike" for card in cards)
    assert all(card["brand"] != "耐克" for card in cards)


def test_product_search_excludes_lining_alias_for_li_ning_brand() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐1000元以上的运动鞋，不要 lining",
        filters=SearchFilters(
            min_price=1000,
            product_types=["clothes.sports_shoes"],
            exclusions=["lining"],
        ),
        top_k=10,
    )

    assert cards
    assert all(card["brand"] != "李宁" for card in cards)


def test_product_search_applies_required_brand_as_hard_constraint() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐一款阿迪达斯跑步鞋",
        filters=SearchFilters(product_types=["clothes.sports_shoes"], brands=["阿迪达斯"]),
        top_k=5,
    )

    assert cards
    assert {card["brand"] for card in cards} == {"阿迪达斯"}


def test_product_search_applies_required_brand_english_alias() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐一款 adidas 跑步鞋",
        filters=SearchFilters(product_types=["clothes.sports_shoes"], brands=["adidas"]),
        top_k=5,
    )

    assert cards
    assert {card["brand"] for card in cards} == {"阿迪达斯"}


def test_product_search_exclusion_treats_known_alias_as_brand_not_text_blob() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐一款1000元以上的运动鞋，不要 li-ning",
        filters=SearchFilters(
            min_price=1000,
            product_types=["clothes.sports_shoes"],
            exclusions=["li-ning"],
        ),
        top_k=10,
    )

    assert cards
    assert all(card["brand"] != "李宁" for card in cards)


def test_product_search_soft_keywords_rerank_without_filtering_everything() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐一款运动鞋，拍照优先",
        filters=SearchFilters(product_types=["clothes.sports_shoes"], should_keywords=["拍照"]),
        top_k=5,
    )

    assert cards
    assert all("clothes.sports_shoes" in card["product_types"] for card in cards)


def test_product_search_handles_compound_sports_shoe_expression() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐一款适合跑步的鞋",
        filters=SearchFilters(product_types=["clothes.sports_shoes"]),
        top_k=5,
    )

    assert cards
    assert all("clothes.sports_shoes" in card["product_types"] for card in cards)
    assert not any("裤" in card["name"] for card in cards)


def test_product_type_filters_are_or_within_same_facet() -> None:
    tool = make_tool()

    cards = tool.run(
        query="推荐运动鞋或运动裤",
        filters=SearchFilters(product_types=["clothes.sports_shoes", "clothes.sports_pants"]),
        top_k=10,
    )
    returned_types = {product_type for card in cards for product_type in card["product_types"]}

    assert cards
    assert "clothes.sports_shoes" in returned_types
    assert "clothes.sports_pants" in returned_types
    assert all(
        set(card["product_types"]) & {"clothes.sports_shoes", "clothes.sports_pants"}
        for card in cards
    )


def test_product_card_enriches_product_type_from_legacy_metadata() -> None:
    card = to_product_card(
        {
            "metadata": {
                "id": "legacy_shoe",
                "name": "缓震跑步鞋",
                "category": "服饰运动",
                "sub_category": "运动鞋",
                "brand": "Demo",
                "price": 399,
                "image_url": "",
                "detail_url": "",
                "tags": ["跑鞋"],
                "description": "",
            }
        },
        query="推荐一款运动鞋",
    )

    assert card["product_types"] == ["clothes.sports_shoes"]
    assert card["product_type_names"] == ["运动鞋"]


def test_product_type_taxonomy_does_not_treat_sun_hat_as_sunscreen_lotion() -> None:
    product_types = infer_product_type_ids(
        {
            "name": "轻量速干运动鸭舌帽防晒透气户外遮阳帽",
            "category": "服饰运动",
            "sub_category": "帽子",
            "tags": ["防晒帽", "遮阳帽"],
        }
    )

    assert "beauty.sunscreen" not in product_types


def test_product_card_recomputes_stale_generated_product_types() -> None:
    card = to_product_card(
        {
            "metadata": {
                "id": "legacy_sun_hat",
                "name": "轻量速干运动鸭舌帽防晒透气户外遮阳帽",
                "category": "服饰运动",
                "sub_category": "帽子",
                "brand": "Demo",
                "price": 199,
                "image_url": "",
                "detail_url": "",
                "tags": ["防晒帽", "遮阳帽"],
                "description": "",
                "product_types": ["beauty.sunscreen"],
            }
        },
        query="推荐一款防晒霜",
    )

    assert "beauty.sunscreen" not in card["product_types"]


def test_product_reason_uses_complete_catalog_sentences_without_hard_cutoff() -> None:
    reason = build_reason(
        {
            "tags": ["跑步鞋", "缓震"],
            "description": (
                "这双跑步鞋主打缓震反馈和日常训练支撑，适合3-10公里路跑。"
                "核心卖点是工程网布透气，通勤和小区夜跑都比较合适。"
                " 问：这款怎么清洁？ 答：用软刷清洁。"
            ),
        },
        query="推荐一款缓震跑步鞋",
    )

    assert "匹配你提到的跑步鞋、缓震" in reason
    assert reason.endswith("。")
    assert "问：" not in reason
    assert reason != "这双跑步鞋主打缓震反馈和日常训练支撑，适合3-10公里"
