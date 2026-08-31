"""
Purpose: Chroma metadata 过滤：将 SearchFilters 转换为 Chroma where 表达式，实现 metadata 下推过滤。
"""

import json
from typing import Any

from server.rag.category_taxonomy import canonicalize_category, infer_category_ids
from server.rag.identifiers import category_filter_key, product_type_filter_key
from server.rag.taxonomy import canonicalize_product_type, enrich_product_type_metadata, infer_product_type_ids
from server.rag.types import VectorSearchFilters


def metadata_matches_vector_filters(metadata: dict[str, Any], filters: VectorSearchFilters | None) -> bool:
    if filters is None:
        return True

    if filters.max_price is not None and float(metadata.get("price", 0)) > filters.max_price:
        return False
    if filters.min_price is not None and float(metadata.get("price", 0)) < filters.min_price:
        return False
    if filters.in_stock_only and int(float(metadata.get("stock", 0) or 0)) <= 0:
        return False

    if filters.categories:
        metadata_categories = set(infer_category_ids(metadata))
        expected_categories = {canonicalize_category(category) for category in filters.categories}
        if not metadata_categories & expected_categories:
            return False

    if filters.product_types:
        metadata_types = set(infer_product_type_ids(metadata))
        expected = {canonicalize_product_type(product_type) for product_type in filters.product_types}
        if not metadata_types & expected:
            return False

    return True


def build_chroma_where(filters: VectorSearchFilters | None) -> dict[str, Any] | None:
    if filters is None:
        return None

    clauses: list[dict[str, Any]] = []
    if filters.min_price is not None:
        clauses.append({"price": {"$gte": float(filters.min_price)}})
    if filters.max_price is not None:
        clauses.append({"price": {"$lte": float(filters.max_price)}})
    if filters.in_stock_only:
        clauses.append({"stock": {"$gt": 0}})

    if filters.categories:
        category_clauses = [
            {category_filter_key(category): True}
            for category in filters.categories
        ]
        if len(category_clauses) == 1:
            clauses.append(category_clauses[0])
        elif category_clauses:
            clauses.append({"$or": category_clauses})

    if filters.product_types:
        product_type_clauses = [
            {product_type_filter_key(product_type): True}
            for product_type in filters.product_types
        ]
        if len(product_type_clauses) == 1:
            clauses.append(product_type_clauses[0])
        elif product_type_clauses:
            clauses.append({"$or": product_type_clauses})

    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def to_chroma_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": metadata["id"],
        "name": metadata["name"],
        "category": metadata.get("category", ""),
        "sub_category": metadata.get("sub_category", ""),
        "brand": metadata.get("brand", ""),
        "category_ids": ",".join(str(item) for item in infer_category_ids(metadata)),
        "product_type_ids": ",".join(str(item) for item in metadata.get("product_types", [])),
        "price": float(metadata.get("price", 0)),
        "stock": int(metadata.get("stock", 0)),
        "image_url": metadata.get("image_url", ""),
        "detail_url": metadata.get("detail_url", ""),
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
    }
    for product_type in metadata.get("product_types", []):
        payload[product_type_filter_key(str(product_type))] = True
    for category in infer_category_ids(metadata):
        payload[category_filter_key(str(category))] = True
    return payload


def from_chroma_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    raw = metadata.get("metadata_json")
    if raw:
        return enrich_product_type_metadata(json.loads(raw))
    return enrich_product_type_metadata(metadata)
