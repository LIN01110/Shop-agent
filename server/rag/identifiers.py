"""
Purpose: 标识符安全处理：将字符串转换为 Chroma 集合安全的标识符。
"""

import hashlib
import re

from server.rag.taxonomy import canonicalize_product_type
from server.rag.category_taxonomy import canonicalize_category


def safe_identifier(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()


def bounded_chroma_collection_name(value: str, max_length: int = 63) -> str:
    safe_name = safe_identifier(value)
    if len(safe_name) <= max_length:
        return safe_name

    digest = hashlib.sha1(safe_name.encode("utf-8")).hexdigest()[:8]
    prefix_length = max_length - len(digest) - 1
    prefix = safe_name[:prefix_length].rstrip("_")
    return f"{prefix}_{digest}"


def product_type_filter_key(product_type: str) -> str:
    return f"pt_{safe_identifier(canonicalize_product_type(product_type))}"


def category_filter_key(category: str) -> str:
    return f"cat_{safe_identifier(canonicalize_category(category))}"
