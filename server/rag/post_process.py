"""
答辩重点 🟠（检索后处理）
任务：Day 3 任务 3.5 — 不熟时重读。
核心：7 个 PostProcessor：RangeFilter(价格)、ProductTypeFilter、CategoryFilter、BrandFilter、
      StockFilter、KeywordFilter、ExclusionFilter(反选)。
      程序化过滤保证“不要日系品牌”等反选不依赖 Prompt 兜底。
高频追问：
  - “PostProcessor 有哪些？各自作用？”
  - “反选为什么不交给 LLM 处理？” → 结构化过滤更稳定、可解释、可测试
"""

from dataclasses import dataclass, field
from typing import Protocol

from server.commerce.facts import get_fact_provider
from server.rag.brand_aliases import brand_matches, expand_alias_terms, is_known_brand_alias
from server.rag.category_taxonomy import category_matches
from server.rag.taxonomy import product_type_matches
from server.session.state import SessionState


KEYWORD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "拍照": ("拍照", "影像", "摄影", "相机", "人像", "长焦", "镜头"),
    "影像": ("影像", "拍照", "摄影", "相机", "人像", "长焦", "镜头"),
    "续航": ("续航", "电池", "长续航", "快充"),
    "性能": ("性能", "芯片", "处理器", "游戏", "流畅"),
    "性价比": ("性价比", "预算", "价格", "划算", "实惠"),
    "轻量": ("轻量", "轻便", "轻薄"),
    "保湿": ("保湿", "补水", "滋润"),
    "敏感肌": ("敏感肌", "敏感", "低刺激", "维稳", "修护"),
}

@dataclass(frozen=True)
class SearchFilters:
    min_price: float | None = None
    max_price: float | None = None
    keywords: list[str] = field(default_factory=list)
    should_keywords: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    excluded_categories: list[str] = field(default_factory=list)
    product_types: list[str] = field(default_factory=list)
    excluded_product_types: list[str] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)
    preferred_brands: list[str] = field(default_factory=list)
    excluded_brands: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    in_stock_only: bool = False
    facets: list[str] = field(default_factory=list)
    unsupported_constraints: list[str] = field(default_factory=list)

    @classmethod
    def from_session(cls, session: SessionState) -> "SearchFilters":
        min_price: float | None = None
        max_price: float | None = None
        keywords: list[str] = []
        should_keywords: list[str] = []
        categories: list[str] = []
        excluded_categories: list[str] = []
        product_types: list[str] = []
        excluded_product_types: list[str] = []
        brands: list[str] = []
        preferred_brands: list[str] = []
        excluded_brands: list[str] = []
        exclusions: list[str] = []
        in_stock_only = False
        facets: list[str] = []
        unsupported_constraints: list[str] = []

        for item in session.filters:
            if item.kind == "max_price":
                max_price = parse_float(item.value, default=max_price)
            elif item.kind == "min_price":
                min_price = parse_float(item.value, default=min_price)
            elif item.kind == "keyword":
                keywords.append(item.value)
            elif item.kind == "should_keyword":
                append_unique(should_keywords, item.value)
            elif item.kind == "category":
                append_unique(categories, item.value)
            elif item.kind == "exclude_category":
                append_unique(excluded_categories, item.value)
            elif item.kind == "product_type":
                if item.value not in product_types:
                    product_types.append(item.value)
            elif item.kind == "exclude_product_type":
                append_unique(excluded_product_types, item.value)
            elif item.kind == "brand":
                append_unique(brands, item.value)
            elif item.kind == "preferred_brand":
                append_unique(preferred_brands, item.value)
            elif item.kind == "exclude_brand":
                append_unique(excluded_brands, item.value)
            elif item.kind == "in_stock":
                in_stock_only = parse_bool(item.value)
            elif item.kind == "facet":
                append_unique(facets, item.value)
            elif item.kind == "unsupported_service":
                append_unique(unsupported_constraints, item.value)

        for item in session.exclusions:
            if item.kind == "exclude_brand":
                append_unique(excluded_brands, item.value)
            else:
                append_unique(exclusions, item.value)

        return cls(
            min_price=min_price,
            max_price=max_price,
            keywords=keywords,
            should_keywords=should_keywords,
            categories=categories,
            excluded_categories=excluded_categories,
            product_types=product_types,
            excluded_product_types=excluded_product_types,
            brands=brands,
            preferred_brands=preferred_brands,
            excluded_brands=excluded_brands,
            exclusions=exclusions,
            in_stock_only=in_stock_only,
            facets=facets,
            unsupported_constraints=unsupported_constraints,
        )


class PostProcessor(Protocol):
    def apply(self, hits: list[dict], filters: SearchFilters) -> list[dict]:
        ...


class RangeFilter:
    def apply(self, hits: list[dict], filters: SearchFilters) -> list[dict]:
        if filters.min_price is None and filters.max_price is None:
            return hits
        kept: list[dict] = []
        facts = get_fact_provider()
        for hit in hits:
            price_fact = facts.price(hit.get("metadata", {}))
            price = float(price_fact.value if price_fact.available else hit["metadata"].get("price", 0))
            if filters.min_price is not None and price < filters.min_price:
                continue
            if filters.max_price is not None and price > filters.max_price:
                continue
            kept.append(hit)
        return kept


class BrandFilter:
    def apply(self, hits: list[dict], filters: SearchFilters) -> list[dict]:
        if not filters.brands and not filters.excluded_brands:
            return hits

        kept: list[dict] = []
        for hit in hits:
            brand = str(hit["metadata"].get("brand", ""))
            if filters.brands and not any(brand_matches(item, brand) for item in filters.brands):
                continue
            if filters.excluded_brands and any(brand_matches(item, brand) for item in filters.excluded_brands):
                continue
            kept.append(hit)
        return kept


class StockFilter:
    def apply(self, hits: list[dict], filters: SearchFilters) -> list[dict]:
        if not filters.in_stock_only:
            return hits
        facts = get_fact_provider()
        kept: list[dict] = []
        for hit in hits:
            metadata = hit.get("metadata", {})
            stock_fact = facts.stock(metadata)
            stock = stock_fact.value if stock_fact.available else metadata.get("stock")
            if parse_int(stock, default=0) > 0:
                kept.append(hit)
        return kept


class ExclusionFilter:
    def apply(self, hits: list[dict], filters: SearchFilters) -> list[dict]:
        if not filters.exclusions:
            return hits

        brand_exclusions = [item for item in filters.exclusions if is_known_brand_alias(item)]
        non_brand_exclusions = [item for item in filters.exclusions if not is_known_brand_alias(item)]
        attribute_exclusions = expand_alias_terms(non_brand_exclusions)
        kept: list[dict] = []
        for hit in hits:
            metadata = hit["metadata"]
            brand = str(metadata.get("brand", ""))
            if brand_exclusions and any(brand_matches(exclusion, brand) for exclusion in brand_exclusions):
                continue

            haystack = " ".join(
                [
                    str(metadata.get("name", "")),
                    str(metadata.get("brand", "")),
                    str(metadata.get("category", "")),
                    str(metadata.get("tags", "")),
                    str(metadata.get("attributes", "")),
                    hit.get("text", ""),
                ]
            ).lower()
            if not any(exclusion in haystack for exclusion in attribute_exclusions):
                kept.append(hit)
        return kept


class ProductTypeFilter:
    def apply(self, hits: list[dict], filters: SearchFilters) -> list[dict]:
        if not filters.product_types and not filters.excluded_product_types:
            return hits
        kept: list[dict] = []
        for hit in hits:
            metadata = hit["metadata"]
            if filters.product_types and not any(
                product_type_matches(product_type, metadata)
                for product_type in filters.product_types
            ):
                continue
            if filters.excluded_product_types and any(
                product_type_matches(product_type, metadata)
                for product_type in filters.excluded_product_types
            ):
                continue
            kept.append(hit)
        return kept


class CategoryFilter:
    def apply(self, hits: list[dict], filters: SearchFilters) -> list[dict]:
        if not filters.categories and not filters.excluded_categories:
            return hits
        kept: list[dict] = []
        for hit in hits:
            metadata = hit["metadata"]
            if filters.categories and not any(
                category_matches(category, metadata)
                for category in filters.categories
            ):
                continue
            if filters.excluded_categories and any(
                category_matches(category, metadata)
                for category in filters.excluded_categories
            ):
                continue
            kept.append(hit)
        return kept


class KeywordFilter:
    def apply(self, hits: list[dict], filters: SearchFilters) -> list[dict]:
        if not filters.keywords:
            return hits

        required = [keyword.lower() for keyword in filters.keywords if keyword.strip()]
        if not required:
            return hits

        filtered: list[dict] = []
        for hit in hits:
            metadata = hit["metadata"]
            broad_haystack = " ".join(
                [
                    str(metadata.get("name", "")),
                    str(metadata.get("category", "")),
                    str(metadata.get("sub_category", "")),
                    str(metadata.get("brand", "")),
                    str(metadata.get("tags", "")),
                    str(metadata.get("description", "")),
                ]
            ).lower()
            if all(keyword_matches(keyword, broad_haystack) for keyword in required):
                filtered.append(hit)

        return filtered


def keyword_matches(keyword: str, broad_haystack: str) -> bool:
    variants = KEYWORD_SYNONYMS.get(keyword, (keyword,))
    return any(variant in broad_haystack for variant in variants)


def append_unique(target: list[str], value: str) -> None:
    value = value.strip()
    if value and value not in target:
        target.append(value)


def parse_float(value: str, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "有货", "in_stock"}
