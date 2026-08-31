"""
Purpose: 品牌别名测试：验证品牌别名识别和归一化逻辑。
"""

from server.rag.brand_aliases import (
    brand_matches,
    compact_text,
    default_brand_catalog,
    expand_alias_terms,
    extract_brand_mentions,
    message_mentions_brand,
    normalize_text,
)


def test_normalization_handles_case_width_and_separators() -> None:
    assert normalize_text("ＬＩ－ＮＩＮＧ") == "li ning"
    assert compact_text("Li Ning") == "lining"
    assert compact_text("Li-Ning") == "lining"


def test_brand_alias_catalog_expands_data_driven_brand_group() -> None:
    aliases = set(expand_alias_terms(["Li Ning"]))

    assert {"李宁", "li ning", "li-ning", "lining"} <= aliases


def test_brand_matching_uses_alias_groups_not_substring_hacks() -> None:
    assert brand_matches("adidas", "阿迪达斯")
    assert brand_matches("Apple", "苹果")
    assert brand_matches("the north face", "北面")
    assert not brand_matches("Apple", "小米")


def test_message_mentions_brand_respects_ascii_token_boundaries() -> None:
    assert message_mentions_brand("这次不要 li-ning，换一双", "李宁")
    assert message_mentions_brand("优先 the north face 的包", "北面")
    assert not message_mentions_brand("这个 outline 设计不错", "李宁")


def test_fuzzy_brand_resolution_is_available_but_tightly_bounded() -> None:
    catalog = default_brand_catalog()

    assert catalog.fuzzy_resolve("adiddas").canonical == "阿迪达斯"
    assert catalog.fuzzy_resolve("li") is None

def test_brand_catalog_extracts_mentions_from_business_aliases() -> None:
    mentions = extract_brand_mentions("\u63a8\u8350\u4e00\u6b3e\u6b27\u83b1\u96c5\u7684\u53e3\u7ea2")

    assert "\u5df4\u9ece\u6b27\u83b1\u96c5" in mentions
