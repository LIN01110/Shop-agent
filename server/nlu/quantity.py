"""
Purpose: 数量解析：从消息中提取数量、单位、序数词（第二个）、购买量表达（来两件）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


COUNT_UNITS = (
    "件套",
    "个",
    "件",
    "份",
    "台",
    "瓶",
    "支",
    "双",
    "款",
    "套",
    "盒",
    "包",
    "袋",
    "杯",
    "罐",
    "片",
    "条",
    "本",
    "只",
    "颗",
    "粒",
    "箱",
    "提",
    "组",
    "对",
    "枚",
)
MEASURE_UNITS = (
    "公斤",
    "千克",
    "毫升",
    "ml",
    "ML",
    "kg",
    "KG",
    "g",
    "G",
    "L",
    "l",
    "升",
    "斤",
    "克",
)
EN_UNIT_CANONICAL = {
    "pc": "件",
    "pcs": "件",
    "piece": "件",
    "pieces": "件",
    "pair": "双",
    "pairs": "双",
    "bottle": "瓶",
    "bottles": "瓶",
    "box": "盒",
    "boxes": "盒",
    "pack": "包",
    "packs": "包",
    "set": "套",
    "sets": "套",
}
EN_COUNT_UNITS = tuple(EN_UNIT_CANONICAL.keys())
ALL_UNITS = tuple(sorted((*COUNT_UNITS, *MEASURE_UNITS, *EN_COUNT_UNITS), key=len, reverse=True))

FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
NUMBER_TOKEN = r"\d+|[零〇一二两俩三四五六七八九十百]+"
ALL_UNIT_PATTERN = "|".join(re.escape(unit) for unit in ALL_UNITS)
COUNT_UNIT_PATTERN = "|".join(re.escape(unit) for unit in sorted((*COUNT_UNITS, *EN_COUNT_UNITS), key=len, reverse=True))
EXPLICIT_CART_ADD_WORDS = ("加到", "加入", "加购", "添加", "添加到", "放到", "放进")


@dataclass(frozen=True)
class QuantityParse:
    value: int
    unit: str | None
    start: int
    end: int
    source: str
    is_count: bool


def normalize_quantity_text(text: str) -> str:
    return text.translate(FULLWIDTH_DIGITS).strip()


def parse_number_token(token: str) -> int | None:
    token = normalize_quantity_text(token)
    if not token:
        return None
    if token.isdigit():
        return int(token)
    return parse_chinese_number(token)


def parse_chinese_number(text: str) -> int | None:
    text = text.strip().replace("〇", "零").replace("两", "二").replace("俩", "二")
    if not text:
        return None
    digits = {
        "零": 0,
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if text in digits:
        return digits[text]

    total = 0
    current = 0
    has_unit = False
    for char in text:
        if char in digits:
            current = digits[char]
            continue
        if char in {"十", "百"}:
            has_unit = True
            unit = 10 if char == "十" else 100
            total += (current or 1) * unit
            current = 0
            continue
        return None
    if not has_unit:
        return None
    return total + current


def canonicalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    normalized = unit.strip()
    if not normalized:
        return None
    return EN_UNIT_CANONICAL.get(normalized.lower(), normalized)


def is_count_unit(unit: str | None) -> bool:
    normalized = canonicalize_unit(unit)
    return normalized is None or normalized in COUNT_UNITS


def extract_quantity_parse(message: str, *, count_only: bool = True) -> QuantityParse | None:
    text = normalize_quantity_text(message)
    matches: list[QuantityParse] = []
    patterns = [
        (
            "quantity_assignment",
            rf"(?:数量|数目|个数|件数|改成|改为|设为|设置为|变成|调整为)\s*({NUMBER_TOKEN})\s*({ALL_UNIT_PATTERN})?",
        ),
        ("verb_quantity", rf"(?:来|要|买|加|购买|拿|补|添)\s*({NUMBER_TOKEN})\s*({ALL_UNIT_PATTERN})"),
        ("quantity_with_unit", rf"({NUMBER_TOKEN})\s*({ALL_UNIT_PATTERN})"),
    ]

    for source, pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            if has_ordinal_prefix(text, match.start()):
                continue
            value = parse_number_token(match.group(1))
            if value is None or value <= 0:
                continue
            unit = canonicalize_unit(match.group(2) if len(match.groups()) >= 2 else None)
            count_like = is_count_unit(unit)
            if count_only and not count_like:
                continue
            matches.append(
                QuantityParse(
                    value=value,
                    unit=unit,
                    start=match.start(),
                    end=match.end(),
                    source=source,
                    is_count=count_like,
                )
            )

    if not matches:
        return None
    return sorted(matches, key=lambda item: (item.start, source_priority(item.source), item.end))[0]


def source_priority(source: str) -> int:
    return {"quantity_assignment": 0, "verb_quantity": 1, "quantity_with_unit": 2}.get(source, 9)


def has_ordinal_prefix(text: str, start: int) -> bool:
    return text[:start].rstrip().endswith("第")


def extract_quantity(message: str) -> int | None:
    parsed = extract_quantity_parse(message, count_only=True)
    return parsed.value if parsed else None


def extract_quantity_unit(message: str) -> str | None:
    parsed = extract_quantity_parse(message, count_only=True)
    return parsed.unit if parsed else None


def extract_ordinal_index(message: str) -> int | None:
    text = normalize_quantity_text(message)
    match = re.search(rf"第\s*({NUMBER_TOKEN})\s*(?:{COUNT_UNIT_PATTERN}|款|项)?", text, flags=re.IGNORECASE)
    if not match:
        return None
    value = parse_number_token(match.group(1))
    if value is None or value <= 0:
        return None
    return value - 1


def extract_quantity_delta(message: str) -> int:
    text = normalize_quantity_text(message)
    minus = re.search(rf"(?:减少|减|少)\s*({NUMBER_TOKEN})\s*(?:{COUNT_UNIT_PATTERN})?", text, flags=re.IGNORECASE)
    if minus:
        value = parse_number_token(minus.group(1))
        return -value if value else 0

    plus = re.search(rf"(?:多加|再加|增加|加)\s*({NUMBER_TOKEN})\s*(?:{COUNT_UNIT_PATTERN})?", text, flags=re.IGNORECASE)
    if plus and not contains_explicit_cart_add(text):
        value = parse_number_token(plus.group(1))
        return value if value else 0
    return 0


def contains_explicit_cart_add(message: str) -> bool:
    return any(word in message for word in EXPLICIT_CART_ADD_WORDS)


def is_add_quantity_expression(message: str) -> bool:
    text = normalize_quantity_text(message)
    if contains_explicit_cart_add(text) or "购买" in text:
        return True
    return bool(re.search(rf"(?:来|要|买|拿|补|添)\s*(?:{NUMBER_TOKEN})?\s*(?:{COUNT_UNIT_PATTERN})", text, flags=re.IGNORECASE))


def is_purchase_quantity_expression(message: str) -> bool:
    text = normalize_quantity_text(message)
    if contains_explicit_cart_add(text):
        return False
    return bool(
        re.search(
            rf"(?:想买|想要|要买|打算买|准备买|我要|买|购买)\s*({NUMBER_TOKEN})\s*(?:{COUNT_UNIT_PATTERN})",
            text,
            flags=re.IGNORECASE,
        )
    )
