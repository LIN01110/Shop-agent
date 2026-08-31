"""
Purpose: 场景工具函数：模板渲染、槽位过滤合并、稳定分桶等辅助函数。
"""

import hashlib
import re
from typing import Any

from server.rag.post_process import SearchFilters


def render_query_template(template: str, message: str, context_variables: dict[str, str] | None = None) -> str:
    result = template.replace("{message}", message)
    for key, value in (context_variables or {}).items():
        result = result.replace("{" + key + "}", value)
    result = re.sub(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "", result)
    return " ".join(result.split())


def merge_slot_filters(slot_filters: SearchFilters, global_filters: SearchFilters | None) -> SearchFilters:
    if global_filters is None:
        return slot_filters
    max_price = slot_filters.max_price
    if max_price is None:
        max_price = global_filters.max_price
    elif global_filters.max_price is not None:
        max_price = min(max_price, global_filters.max_price)

    return SearchFilters(
        max_price=max_price,
        keywords=dedupe([*slot_filters.keywords]),
        product_types=dedupe(slot_filters.product_types),
        exclusions=dedupe([*slot_filters.exclusions, *global_filters.exclusions]),
    )


def should_keep_slot(slot: Any, *, message: str, total_budget: float | None) -> bool:
    if total_budget is not None and slot.min_budget is not None and total_budget < slot.min_budget:
        return False
    if not slot.optional:
        return True
    if not slot.match_terms:
        return True
    return any(term in message for term in slot.match_terms)


def stable_bucket(seed: str) -> float:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF * 100.0


def dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in items if item.strip()))
