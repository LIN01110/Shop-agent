"""
答辩重点 🔴（RAG 核心）
任务：Day 1 任务 1.4 — 必须能画出 5 阶段流程图。
核心：ProductRetrievalPipeline.run() 的 5 阶段：
      1) store_prefilter_recall（先召回 candidate_top_k/50 条）
      2-7) PostProcessors：Range/Type/Category/Brand/Stock/Keyword/Exclusion
      8) dedupe_hits
      9) rerank_hits（product_type +20, category +12, keyword +6, brand +5, 预算内 +2）
高频追问：
  - “为什么先召回 50 条再过滤到 5 条？”
    → candidate_multiplier=10, min_candidate_pool=50，给后处理留素材
  - “反选‘不要日系品牌’怎么实现？”
    → ExclusionFilter 在检索结果上程序化过滤
  - “rerank 加分逻辑？”
    → 结构化匹配 > 关键词匹配 > 预算匹配
"""

import time
from dataclasses import dataclass, field

from server.commerce.facts import get_fact_provider
from server.rag.post_process import (
    BrandFilter,
    CategoryFilter,
    ExclusionFilter,
    KeywordFilter,
    ProductTypeFilter,
    RangeFilter,
    SearchFilters,
    StockFilter,
)
from server.rag.brand_aliases import message_mentions_brand
from server.rag.category_taxonomy import category_matches
from server.rag.taxonomy import product_type_matches
from server.rag.vector_store import VectorSearchFilters, VectorStore


@dataclass(frozen=True)
class RetrievalStep:
    name: str
    count: int
    elapsed_ms: float


@dataclass(frozen=True)
class RetrievalDiagnostics:
    query: str
    requested_top_k: int
    candidate_top_k: int
    steps: tuple[RetrievalStep, ...] = ()
    applied_filters: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalPipelineResult:
    hits: list[dict]
    diagnostics: RetrievalDiagnostics


class ProductRetrievalPipeline:
    """Production-shaped retrieval chain: prefilter, post-filter, rerank, truncate."""

    def __init__(
        self,
        store: VectorStore,
        *,
        candidate_multiplier: int = 10,
        min_candidate_pool: int = 50,
    ) -> None:
        self.store = store
        # 先召回 top_k * candidate_multiplier 条，但不少于 min_candidate_pool（默认 50）
        # 给后续 7 个 PostProcessor 留足过滤素材
        self.candidate_multiplier = max(candidate_multiplier, 1)
        self.min_candidate_pool = max(min_candidate_pool, 1)
        # Stage 2-7：7 个 PostProcessor 依次过滤
        self.post_processors = [
            RangeFilter(),        # 价格区间
            ProductTypeFilter(),  # 商品类型
            CategoryFilter(),     # 类目
            BrandFilter(),        # 品牌
            StockFilter(),        # 库存
            KeywordFilter(),      # 关键词
            ExclusionFilter(),    # 反选（如“不要日系品牌”）
        ]

    def run(self, query: str, filters: SearchFilters, top_k: int = 5) -> RetrievalPipelineResult:
        top_k = max(int(top_k), 0)
        if top_k == 0:
            return RetrievalPipelineResult(
                hits=[],
                diagnostics=RetrievalDiagnostics(
                    query=query,
                    requested_top_k=0,
                    candidate_top_k=0,
                    applied_filters=describe_filters(filters),
                ),
            )

        # Stage 1：粗召回。先按 candidate_top_k 召回，保证过滤池充足
        candidate_top_k = max(top_k * self.candidate_multiplier, self.min_candidate_pool)
        steps: list[RetrievalStep] = []

        started = time.perf_counter()
        # 下推 product_type / category 过滤到向量库，减少无效召回
        hits = self.store.query(
            query=query,
            top_k=candidate_top_k,
            filters=VectorSearchFilters(
                categories=tuple(filters.categories),
                product_types=tuple(filters.product_types),
            ),
        )
        steps.append(RetrievalStep("store_prefilter_recall", len(hits), elapsed_ms(started)))
        # 如果带过滤没结果，自动放宽过滤再查一次，避免条件太严导致空结果
        if not hits and (filters.product_types or filters.categories):
            started = time.perf_counter()
            hits = self.store.query(
                query=query,
                top_k=candidate_top_k,
                filters=VectorSearchFilters(),
            )
            steps.append(RetrievalStep("store_recall_without_type_prefilter", len(hits), elapsed_ms(started)))

        # Stage 2-7：依次用 7 个 PostProcessor 过滤，结构化过滤不依赖 Prompt 兜底
        for processor in self.post_processors:
            started = time.perf_counter()
            hits = processor.apply(hits, filters)
            steps.append(RetrievalStep(processor.__class__.__name__, len(hits), elapsed_ms(started)))

        # Stage 8-9：去重 + 重排序。按结构化匹配度加分，再截断到 top_k
        started = time.perf_counter()
        hits = dedupe_hits(hits)
        hits = rerank_hits(hits, query=query, filters=filters)
        steps.append(RetrievalStep("dedupe_and_rerank", len(hits), elapsed_ms(started)))

        return RetrievalPipelineResult(
            hits=hits[:top_k],
            diagnostics=RetrievalDiagnostics(
                query=query,
                requested_top_k=top_k,
                candidate_top_k=candidate_top_k,
                steps=tuple(steps),
                applied_filters=describe_filters(filters),
            ),
        )


def dedupe_hits(hits: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique_hits: list[dict] = []
    for hit in hits:
        item_id = str(hit.get("id") or hit.get("metadata", {}).get("id", ""))
        if item_id in seen:
            continue
        seen.add(item_id)
        unique_hits.append(hit)
    return unique_hits


def rerank_hits(hits: list[dict], *, query: str, filters: SearchFilters) -> list[dict]:
    query = query.lower()
    scored_hits: list[tuple[float, int, dict]] = []
    for index, hit in enumerate(hits):
        metadata = hit.get("metadata", {})
        score = float(hit.get("score", 0.0))
        raw_score = score
        score += relevance_bonus(metadata, query=query, filters=filters)
        enriched_hit = dict(hit)
        enriched_hit["raw_score"] = raw_score
        enriched_hit["score"] = score
        enriched_hit["retrieval_rank"] = index + 1
        scored_hits.append((score, -index, enriched_hit))

    scored_hits.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [hit for _, _, hit in scored_hits]


def relevance_bonus(metadata: dict, *, query: str, filters: SearchFilters) -> float:
    """Stage 9 重排序加分逻辑：结构化匹配 > 关键词匹配 > 预算匹配。"""
    bonus = 0.0
    haystack = build_metadata_haystack(metadata)
    price_fact = get_fact_provider().price(metadata)
    current_price = float(price_fact.value if price_fact.available else metadata.get("price", 0) or 0)
    # product_type 匹配：最高 +20，结构化信号最强
    if filters.product_types and any(product_type_matches(item, metadata) for item in filters.product_types):
        bonus += 20.0
    # category 匹配：+12
    if filters.categories and any(category_matches(item, metadata) for item in filters.categories):
        bonus += 12.0
    # 关键词 must 匹配：+6；should 匹配：+4
    for keyword in filters.keywords:
        if keyword and keyword.lower() in haystack:
            bonus += 6.0
    for keyword in filters.should_keywords:
        if keyword and keyword.lower() in haystack:
            bonus += 4.0
    # 偏好品牌匹配：+5
    for brand in filters.preferred_brands:
        if brand and brand.lower() in str(metadata.get("brand", "")).lower():
            bonus += 5.0
    # 预算内：+2，越便宜额外小幅加分
    if filters.max_price is not None:
        if current_price <= filters.max_price:
            bonus += 2.0
            bonus += max(0.0, min(filters.max_price - current_price, 500.0) / 500.0)
    if filters.min_price is not None:
        if current_price >= filters.min_price:
            bonus += 1.0
    # 用户查询中直接出现商品名/品牌名：额外加分
    name = str(metadata.get("name", "")).lower()
    brand = str(metadata.get("brand", "")).lower()
    if name and name in query:
        bonus += 8.0
    if brand and message_mentions_brand(query, brand):
        bonus += 4.0
    return bonus


def build_metadata_haystack(metadata: dict) -> str:
    parts = [
        str(metadata.get("name", "")),
        str(metadata.get("category", "")),
        str(metadata.get("sub_category", "")),
        str(metadata.get("brand", "")),
        " ".join(str(item) for item in metadata.get("tags", [])),
        str(metadata.get("description", "")),
    ]
    return " ".join(parts).lower()


def describe_filters(filters: SearchFilters) -> dict[str, object]:
    return {
        "min_price": filters.min_price,
        "max_price": filters.max_price,
        "keywords": list(filters.keywords),
        "should_keywords": list(filters.should_keywords),
        "categories": list(filters.categories),
        "excluded_categories": list(filters.excluded_categories),
        "product_types": list(filters.product_types),
        "excluded_product_types": list(filters.excluded_product_types),
        "brands": list(filters.brands),
        "preferred_brands": list(filters.preferred_brands),
        "excluded_brands": list(filters.excluded_brands),
        "exclusions": list(filters.exclusions),
        "in_stock_only": filters.in_stock_only,
        "facets": list(filters.facets),
        "unsupported_constraints": list(filters.unsupported_constraints),
    }


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)
