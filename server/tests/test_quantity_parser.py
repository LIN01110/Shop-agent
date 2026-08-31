"""
Purpose: 数量解析测试：验证数量、序数词、购买量提取。
"""

from server.nlu.quantity import (
    extract_ordinal_index,
    extract_quantity,
    extract_quantity_delta,
    extract_quantity_parse,
    extract_quantity_unit,
    is_purchase_quantity_expression,
    parse_number_token,
)


def test_parse_chinese_and_arabic_numbers() -> None:
    assert parse_number_token("2") == 2
    assert parse_number_token("两") == 2
    assert parse_number_token("俩") == 2
    assert parse_number_token("十") == 10
    assert parse_number_token("十一") == 11
    assert parse_number_token("二十五") == 25
    assert parse_number_token("一百零二") == 102


def test_extract_quantity_with_common_ecommerce_units() -> None:
    assert extract_quantity("我想买两双安踏") == 2
    assert extract_quantity("买10盒面膜") == 10
    assert extract_quantity("来三瓶咖啡") == 3
    assert extract_quantity("要俩包坚果") == 2
    assert extract_quantity("设置为二十件") == 20


def test_extract_quantity_keeps_unit_for_confirmation_copy() -> None:
    assert extract_quantity_unit("买两双安踏") == "双"
    assert extract_quantity_unit("买10 boxes 面膜") == "盒"


def test_ordinal_reference_is_not_count_quantity() -> None:
    assert extract_ordinal_index("把第二个加到购物车") == 1
    assert extract_ordinal_index("第10款") == 9
    assert extract_quantity("把第二个加到购物车") is None


def test_weight_and_volume_are_not_cart_counts_by_default() -> None:
    assert extract_quantity_parse("买500g咖啡豆", count_only=True) is None
    parsed = extract_quantity_parse("买500g咖啡豆", count_only=False)

    assert parsed is not None
    assert parsed.value == 500
    assert parsed.unit == "g"
    assert parsed.is_count is False


def test_extract_quantity_delta() -> None:
    assert extract_quantity_delta("多加三件") == 3
    assert extract_quantity_delta("少两瓶") == -2
    assert extract_quantity_delta("加1件") == 1


def test_purchase_quantity_expression_is_side_effect_sensitive() -> None:
    assert is_purchase_quantity_expression("我想买两双安踏") is True
    assert is_purchase_quantity_expression("我要2双安踏") is True
    assert is_purchase_quantity_expression("那支给我来两件") is False
    assert is_purchase_quantity_expression("把第二个加到购物车") is False
