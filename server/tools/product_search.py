"""
答辩重点 🟠（商品搜索工具）
任务：Day 2 任务 2.6 — 与 registry.py 一起读。
核心：ProductSearchTool 调用 ProductRetrievalPipeline，把 RetrievalPipelineResult 转换成商品卡片，
      并附加 evidence（检索依据）用于后续 GroundingGuard 校验与 UI 展示。
高频追问：
  - “检索结果怎么变成 UI 上的商品卡片？”
    → 在这里格式化并补充 evidence
  - “evidence 有什么用？”
    → 给 Guard 做事实检查，也方便前端展示匹配原因
"""

from dataclasses import dataclass

from server.commerce.facts import get_commerce_gateway
from server.commerce.services import CommerceDataGateway
from server.rag.category_taxonomy import category_display_names, infer_category_ids
from server.rag.post_process import SearchFilters
from server.rag.retrieval_pipeline import ProductRetrievalPipeline, RetrievalDiagnostics
from server.rag.taxonomy import enrich_product_type_metadata, product_type_display_names
from server.rag.vector_store import VectorStore
from server.tools.product_evidence import build_product_evidence, build_reason
from server.tools.product_urls import build_product_detail_url, build_product_image_url


@dataclass(frozen=True)
class ProductSearchResult:
    cards: list[dict]
    diagnostics: RetrievalDiagnostics


class ProductSearchTool:
    name = "search_products"

    def __init__(
        self,
        store: VectorStore,
        public_base_url: str = "",
        commerce_gateway: CommerceDataGateway | None = None,
        candidate_multiplier: int = 10,
        min_candidate_pool: int = 50,
    ) -> None:
        self.store = store
        self.public_base_url = public_base_url
        self.commerce_gateway = commerce_gateway or get_commerce_gateway()
        self.pipeline = ProductRetrievalPipeline(
            store,
            candidate_multiplier=candidate_multiplier,
            min_candidate_pool=min_candidate_pool,
        )

    def run(self, query: str, filters: SearchFilters, top_k: int = 5) -> list[dict]:
        return self.run_with_diagnostics(query=query, filters=filters, top_k=top_k).cards

    def run_with_diagnostics(self, query: str, filters: SearchFilters, top_k: int = 5) -> ProductSearchResult:
        result = self.pipeline.run(query=query, filters=filters, top_k=top_k)
        cards = [
            to_product_card(
                hit,
                query,
                public_base_url=self.public_base_url,
                commerce_gateway=self.commerce_gateway,
            )
            for hit in result.hits
        ]
        return ProductSearchResult(cards=cards, diagnostics=result.diagnostics)


def to_product_card(
    hit: dict,
    query: str,
    public_base_url: str = "",
    commerce_gateway: CommerceDataGateway | None = None,
) -> dict:
    metadata = enrich_product_type_metadata(hit["metadata"])
    product_facts = (commerce_gateway or get_commerce_gateway()).enrich_product(metadata)
    price_fact = product_facts.fact("price")
    stock_fact = product_facts.fact("stock")
    promotion_fact = product_facts.fact("coupon_policy")
    invoice_fact = product_facts.fact("invoice_policy")
    after_sales_fact = product_facts.fact("after_sales_policy")
    logistics_fact = product_facts.fact("logistics_policy")
    category_ids = infer_category_ids(metadata)
    reason = build_reason(metadata, query)
    return {
        "id": product_facts.product_id,
        "sku_id": product_facts.sku_id,
        "name": metadata["name"],
        "category": metadata["category"],
        "category_ids": category_ids,
        "category_names": category_display_names(category_ids),
        "product_types": metadata.get("product_types", []),
        "product_type_names": product_type_display_names(metadata.get("product_types", [])),
        "brand": metadata["brand"],
        "price": price_fact.value if price_fact.available else metadata["price"],
        "stock": stock_fact.value if stock_fact.available else metadata.get("stock"),
        "promotion": promotion_fact.value if promotion_fact.available else None,
        "invoice_policy": invoice_fact.value if invoice_fact.available else None,
        "after_sales_policy": after_sales_fact.value if after_sales_fact.available else None,
        "logistics_policy": logistics_fact.value if logistics_fact.available else None,
        "image_url": build_product_image_url(metadata.get("image_url", ""), public_base_url),
        "detail_url": build_product_detail_url(
            product_id=metadata["id"],
            raw_detail_url=metadata.get("detail_url", ""),
            public_base_url=public_base_url,
        ),
        "reason": reason,
        "score": hit.get("score", 0),
        "evidence": build_product_evidence(
            metadata,
            hit,
            query=query,
            reason=reason,
            product_facts=product_facts,
        ),
    }


__all__ = [
    "ProductSearchResult",
    "ProductSearchTool",
    "build_product_detail_url",
    "build_product_image_url",
    "build_product_evidence",
    "build_reason",
    "to_product_card",
]
