"""
Purpose: 消息过滤条件提取：从用户消息中提取 product_type、品牌、价格、排除条件等结构化过滤条件。
"""

import re

from server.rag.taxonomy import extract_product_type_matches, load_product_taxonomy
from server.session.state import FilterCondition


def extract_filters(message: str) -> list[FilterCondition]:
    filters: list[FilterCondition] = []

    product_type_matches = extract_product_type_matches(message)
    product_type_keyword_aliases = product_type_aliases_for_matches(product_type_matches)
    for match in product_type_matches:
        kind = "exclude_product_type" if is_negated_term(message, match.alias) else "product_type"
        filters.append(FilterCondition(kind=kind, value=match.product_type_id))

    max_price_values: list[str] = []
    for pattern in [
        r"预算\s*(?:是|为|大概|约|在)?\s*(\d+)\s*(元|块)?",
        r"(\d+)\s*(元|块)?\s*(以内|以下|之内)",
    ]:
        for match in re.finditer(pattern, message):
            value = match.group(1)
            if value not in max_price_values:
                max_price_values.append(value)
    around_price_values: list[int] = []
    for pattern in [
        r"(\d+)\s*(?:元|块)\s*(?:左右|上下|附近)",
        r"(?:大概|约|大约|差不多)\s*(\d+)\s*(?:元|块)",
    ]:
        for match in re.finditer(pattern, message):
            value = int(match.group(1))
            if value not in around_price_values:
                around_price_values.append(value)
    for value in around_price_values:
        lower = max(0, int(value * 0.8))
        upper = int(value * 1.2)
        filters.append(FilterCondition(kind="min_price", value=str(lower)))
        filters.append(FilterCondition(kind="max_price", value=str(upper)))

    for value in max_price_values:
        filters.append(FilterCondition(kind="max_price", value=value))

    min_price_values: list[str] = []
    for pattern in [
        r"(?:至少|不低于|高于|超过)\s*(\d+)\s*(元|块)?",
        r"(\d+)\s*(元|块)?\s*(以上|起)",
    ]:
        for match in re.finditer(pattern, message):
            value = match.group(1)
            if value not in min_price_values:
                min_price_values.append(value)
    for value in min_price_values:
        filters.append(FilterCondition(kind="min_price", value=value))

    if any(word in message for word in ["轻量", "轻便", "轻一点"]):
        filters.append(FilterCondition(kind="keyword", value="轻量"))

    if any(word in message for word in ["油皮", "混油"]):
        filters.append(FilterCondition(kind="keyword", value="油皮"))

    if any(word in message for word in ["敏感肌", "低刺激"]):
        filters.append(FilterCondition(kind="keyword", value="敏感肌"))

    for keyword in [
        "眼霜",
        "卸妆油",
        "咖啡",
        "蓝牙耳机",
        "耳机",
        "防晒",
        "防晒霜",
        "面霜",
        "手机",
        "拍照",
        "续航",
        "性能",
        "性价比",
    ]:
        if keyword in message and keyword not in product_type_keyword_aliases:
            filters.append(FilterCondition(kind="keyword", value=keyword))

    exclusion_pattern = r"(?:不要|不想要|除了|排除)([^，。,.]+)"
    for match in re.finditer(exclusion_pattern, message):
        value = normalize_exclusion(match.group(1))
        if value:
            filters.append(FilterCondition(kind="exclude", value=value))

    reverse_exclusion_pattern = r"([A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff\-\s]{0,30}?)(?:先)?(?:不要了|不要|排除|算了)"
    for match in re.finditer(reverse_exclusion_pattern, message):
        value = normalize_exclusion(match.group(1))
        if value and is_safe_reverse_exclusion(value):
            filters.append(FilterCondition(kind="exclude", value=value))

    return filters


def product_type_aliases_for_matches(matches) -> set[str]:
    product_types = load_product_taxonomy()
    aliases: set[str] = set()
    for match in matches:
        aliases.add(match.alias)
        product_type = product_types.get(match.product_type_id)
        if product_type is None:
            continue
        aliases.add(product_type.display_name)
        aliases.update(product_type.aliases)
        aliases.update(product_type.query_aliases)
        aliases.update(product_type.typo_tolerant_aliases)
    return {alias for alias in aliases if alias}


def normalize_exclusion(value: str) -> str:
    value = value.strip().removesuffix("的").strip()
    value = re.sub(r"(这个|这款|那款|牌子)$", "", value).strip()
    value = re.sub(r"(了|吧|啦|呢)$", "", value).strip()
    if value.startswith("含") and len(value) > 1:
        value = value[1:].strip()
    return value


def is_safe_reverse_exclusion(value: str) -> bool:
    if any(word in value for word in ["我", "但", "也", "和", "或者", "以及"]):
        return False
    return len(value) <= 16


def is_negated_term(message: str, term: str) -> bool:
    if not term:
        return False
    negation_words = ["不要", "不想要", "不推荐", "排除", "除了", "不是", "别"]
    for match in re.finditer(re.escape(term), message):
        prefix = message[max(0, match.start() - 8) : match.start()]
        if any(word in prefix for word in negation_words):
            return True
    return False
