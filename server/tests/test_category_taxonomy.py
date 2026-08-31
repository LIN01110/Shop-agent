"""
Purpose: 分类体系测试：验证分类匹配和推断。
"""

from server.rag.category_taxonomy import (
    category_display_names,
    category_matches,
    extract_category_matches,
    infer_category_ids,
)


def match_ids(message: str) -> set[str]:
    return {match.category_id for match in extract_category_matches(message)}


def test_category_taxonomy_uses_configured_business_aliases() -> None:
    assert "beauty.skincare" in match_ids("推荐一款护肤品")
    assert "electronics.digital" in match_ids("推荐一款电子产品")
    assert "food.beverage" in match_ids("推荐一款食品")


def test_category_taxonomy_uses_catalog_profiles_for_subcategory_language() -> None:
    assert "beauty.skincare" in match_ids("推荐一款精华")
    assert "electronics.digital" in match_ids("推荐一台笔记本电脑")
    assert "food.beverage" in match_ids("推荐一款酸奶")
    assert "clothes.sports" in match_ids("推荐一个运动背包")


def test_category_taxonomy_maps_catalog_category_name_to_canonical_id() -> None:
    metadata = {"category": "数码电子", "sub_category": "智能手机"}

    assert infer_category_ids(metadata) == ["electronics.digital"]
    assert category_display_names(["electronics.digital"]) == ["数码电子"]
    assert category_matches("数码产品", metadata)


def test_category_taxonomy_supports_dynamic_catalog_categories() -> None:
    metadata = {"category": "家居日用", "sub_category": "收纳盒"}
    category_ids = infer_category_ids(metadata)

    assert len(category_ids) == 1
    assert category_ids[0].startswith("catalog.")
    assert category_matches("家居日用", metadata)
