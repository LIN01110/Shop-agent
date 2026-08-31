"""
三阶段混合检索管道（Phase 2 核心）。

架构：
  Stage 1: BM25 粗排 → 召回 top_n_candidates
  Stage 2: 向量精排 → 从候选中向量检索 top_k_stage2
  Stage 3: Cross-Encoder 精排 → 最终 top_k

设计要点：
- 各阶段可独立开关（通过配置）
- 支持纯文本检索、纯图片检索、混合检索
- 缓存机制：相同查询缓存结果
- 与现有 ProductRetrievalPipeline 接口兼容

使用方式：
  from server.rag.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
  config = HybridRetrieverConfig()
  retriever = HybridRetriever(
      config=config,
      bm25_engine=bm25,
      vector_store=store,
      reranker=reranker,
      feedback_loop=feedback,
  )
  results = retriever.search("红色连衣裙", top_k=10)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from server.rag.bm25_engine import BM25Engine, BM25Hit
from server.rag.cross_encoder import CrossEncoderReranker, RerankCandidate, RerankResult
from server.rag.feedback_loop import FeedbackLoop
from server.rag.milvus_store import MilvusStore
from server.rag.post_process import PostProcessor
from server.rag.query_cache import QueryCache, create_sync_redis_client
from server.rag.types import VectorDocument, VectorSearchFilters, VectorStore

logger = logging.getLogger(__name__)


@dataclass
class HybridRetrieverConfig:
    """三阶段检索配置。"""

    # Stage 1: BM25
    enable_bm25: bool = True
    bm25_top_k: int = 200           # BM25 召回数量

    # Stage 2: 向量检索
    enable_vector: bool = True
    vector_top_k: int = 100         # 向量精排数量
    vector_store_type: str = "milvus"  # milvus / chroma / local

    # Stage 3: Cross-Encoder
    enable_rerank: bool = True
    rerank_top_k: int = 20          # 最终返回数量
    ce_weight: float = 0.7          # Cross-Encoder 融合权重
    bm25_weight: float = 0.15
    vector_weight: float = 0.15

    # 混合检索
    enable_hybrid: bool = True      # 同时用 BM25 + 向量
    hybrid_fusion_method: str = "rrf"  # rrf / weighted / max
    rrf_k: int = 60                 # RRF 参数

    # 缓存
    cache_enabled: bool = True
    cache_ttl_seconds: float = 300.0  # 缓存有效期 5 分钟

    # 反馈
    enable_feedback: bool = True
    min_dwell_ms_for_positive: int = 2000  # 视为正样本的最小停留时间

    # 性能
    timeout_ms: float = 5000.0      # 总超时时间


@dataclass
class RetrievalResult:
    """检索结果（统一格式）。"""

    id: str
    text: str
    metadata: dict = field(default_factory=dict)
    score: float = 0.0              # 最终分数
    stage_scores: dict = field(default_factory=dict)  # {bm25: x, vector: y, ce: z}
    rank: int = 0


class HybridRetriever:
    """
    三阶段混合检索管道。

    流程：
    1. 查询预处理（分词、意图识别）
    2. Stage 1: BM25 召回候选集
    3. Stage 2: 向量检索（在候选集上或全库）
    4. 候选融合（RRF 或加权）
    5. Stage 3: Cross-Encoder 精排
    6. 后处理（去重、过滤、截断）
    7. 记录反馈日志
    """

    def __init__(
        self,
        config: HybridRetrieverConfig | None = None,
        bm25_engine: BM25Engine | None = None,
        vector_store: VectorStore | None = None,
        reranker: CrossEncoderReranker | None = None,
        feedback_loop: FeedbackLoop | None = None,
        post_processors: list[PostProcessor] | None = None,
    ) -> None:
        self.config = config or HybridRetrieverConfig()
        self.bm25 = bm25_engine
        self.vector_store = vector_store
        self.reranker = reranker
        self.feedback = feedback_loop
        self.post_processors = post_processors or []

        # 两级缓存（L1 内存 + L2 Redis）
        self._cache = QueryCache(
            l1_ttl_seconds=self.config.cache_ttl_seconds,
            l2_ttl_seconds=self.config.cache_ttl_seconds,
            enabled=self.config.cache_enabled,
        )

    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: VectorSearchFilters | None = None,
        query_image_bytes: bytes | None = None,
        session_id: str = "",
    ) -> list[RetrievalResult]:
        """
        主检索入口。

        Args:
            query: 查询文本
            top_k: 返回结果数
            filters: 过滤条件
            query_image_bytes: 查询图片（可选）
            session_id: 会话 ID（用于反馈记录）

        Returns:
            按相关性排序的结果列表
        """
        started = time.perf_counter()
        query_id = self._generate_query_id(query, filters, query_image_bytes)

        # 检查缓存
        if self.config.cache_enabled:
            cached = self._get_cached(query_id)
            if cached:
                logger.debug("Cache hit: %s", query[:30])
                return cached

        try:
            # ===== Stage 1: BM25 粗排 =====
            bm25_hits = []
            if self.config.enable_bm25 and self.bm25:
                bm25_hits = self.bm25.search(
                    query=query,
                    top_k=self.config.bm25_top_k,
                    filters=filters,
                )
                logger.debug("BM25 recalled %d docs", len(bm25_hits))

            # ===== Stage 2: 向量精排 =====
            vector_hits = []
            if self.config.enable_vector and self.vector_store:
                vector_hits = self.vector_store.query(
                    query=query,
                    top_k=self.config.vector_top_k,
                    filters=filters,
                    query_image_bytes=query_image_bytes,
                )
                logger.debug("Vector recalled %d docs", len(vector_hits))

            # ===== 候选融合 =====
            candidates = self._fuse_candidates(bm25_hits, vector_hits)
            logger.debug("Fused %d candidates", len(candidates))

            if not candidates:
                return []

            # ===== Stage 3: Cross-Encoder 精排 =====
            results = self._rerank(query, candidates, top_k)

            # 后处理
            results = self._post_process(results, filters)

            # 记录反馈
            if self.config.enable_feedback and self.feedback and session_id:
                latency_ms = (time.perf_counter() - started) * 1000
                self.feedback.log_query(
                    query_id=query_id,
                    session_id=session_id,
                    query_text=query,
                    query_image=query_image_bytes is not None,
                    filters=filters.to_dict() if filters else {},
                    latency_ms=latency_ms,
                    result_count=len(results),
                )

            # 缓存结果
            if self.config.cache_enabled:
                self._set_cached(query_id, results)

            return results

        except Exception as exc:
            logger.error("Search failed: %s", exc, exc_info=True)
            # 失败时尝试回退到纯向量检索
            if self.vector_store:
                return self._fallback_vector_search(query, top_k, filters)
            return []

    def _fuse_candidates(
        self,
        bm25_hits: list[BM25Hit],
        vector_hits: list[dict],
    ) -> dict[str, dict]:
        """
        融合 BM25 和向量检索的候选集。

        方法：
        - RRF (Reciprocal Rank Fusion): 1/(k + rank)
        - weighted: 加权分数
        - max: 取最大分数
        """
        candidates: dict[str, dict] = {}

        # BM25 结果
        for rank, hit in enumerate(bm25_hits, 1):
            doc_id = hit.id
            candidates[doc_id] = {
                "id": doc_id,
                "text": hit.text,
                "metadata": hit.metadata,
                "bm25_score": hit.score,
                "vector_score": 0.0,
                "bm25_rank": rank,
                "vector_rank": 9999,
            }

        # 向量结果
        for rank, hit in enumerate(vector_hits, 1):
            doc_id = hit["id"]
            if doc_id in candidates:
                candidates[doc_id]["vector_score"] = hit.get("score", 0.0)
                candidates[doc_id]["vector_rank"] = rank
            else:
                candidates[doc_id] = {
                    "id": doc_id,
                    "text": hit.get("text", ""),
                    "metadata": hit.get("metadata", {}),
                    "bm25_score": 0.0,
                    "vector_score": hit.get("score", 0.0),
                    "bm25_rank": 9999,
                    "vector_rank": rank,
                }

        # 计算融合分数
        method = self.config.hybrid_fusion_method
        k = self.config.rrf_k

        for doc_id, cand in candidates.items():
            if method == "rrf":
                rrf_score = 0.0
                if cand["bm25_rank"] < 9999:
                    rrf_score += 1.0 / (k + cand["bm25_rank"])
                if cand["vector_rank"] < 9999:
                    rrf_score += 1.0 / (k + cand["vector_rank"])
                cand["fusion_score"] = rrf_score
            elif method == "weighted":
                cand["fusion_score"] = (
                    0.5 * self._normalize_bm25(cand["bm25_score"])
                    + 0.5 * cand["vector_score"]
                )
            else:  # max
                cand["fusion_score"] = max(
                    self._normalize_bm25(cand["bm25_score"]),
                    cand["vector_score"],
                )

        return candidates

    def _rerank(
        self,
        query: str,
        candidates: dict[str, dict],
        top_k: int,
    ) -> list[RetrievalResult]:
        """Cross-Encoder 精排。"""
        if not self.config.enable_rerank or not self.reranker or not self.reranker.available:
            # 不使用精排，按融合分数排序
            sorted_cands = sorted(
                candidates.values(),
                key=lambda x: x["fusion_score"],
                reverse=True,
            )
            return [
                RetrievalResult(
                    id=c["id"],
                    text=c["text"],
                    metadata=c["metadata"],
                    score=c["fusion_score"],
                    stage_scores={"bm25": c["bm25_score"], "vector": c["vector_score"]},
                )
                for c in sorted_cands[:top_k]
            ]

        # 构建 RerankCandidate
        rerank_candidates = []
        for c in candidates.values():
            rerank_candidates.append(
                RerankCandidate(
                    id=c["id"],
                    text=c["text"],
                    metadata=c["metadata"],
                    bm25_score=c["bm25_score"],
                    vector_score=c["vector_score"],
                )
            )

        # 按融合分数预排序，取前 N 给 Cross-Encoder
        rerank_candidates.sort(key=lambda x: x.bm25_score + x.vector_score, reverse=True)
        rerank_candidates = rerank_candidates[: self.config.rerank_top_k * 2]

        # Cross-Encoder 精排
        reranked = self.reranker.rerank(
            query=query,
            candidates=rerank_candidates,
            use_ensemble=True,
        )

        # 构建最终结果
        results = []
        for i, r in enumerate(reranked[:top_k], 1):
            results.append(
                RetrievalResult(
                    id=r.id,
                    text=r.text,
                    metadata=r.metadata,
                    score=r.ensemble_score if r.ensemble_score > 0 else r.score,
                    stage_scores={
                        "bm25": r.bm25_score,
                        "vector": r.vector_score,
                        "ce": r.score,
                    },
                    rank=i,
                )
            )

        return results

    def _post_process(
        self,
        results: list[RetrievalResult],
        filters: VectorSearchFilters | None,
    ) -> list[RetrievalResult]:
        """后处理：去重、过滤、截断。"""
        # 去重（基于 ID）
        seen = set()
        unique = []
        for r in results:
            if r.id not in seen:
                seen.add(r.id)
                unique.append(r)

        # 价格过滤
        if filters:
            if filters.min_price is not None or filters.max_price is not None:
                filtered = []
                for r in unique:
                    price = float(r.metadata.get("price", 0) or 0)
                    if filters.min_price is not None and price < filters.min_price:
                        continue
                    if filters.max_price is not None and price > filters.max_price:
                        continue
                    filtered.append(r)
                unique = filtered

        # 应用额外的 PostProcessor
        for processor in self.post_processors:
            try:
                # PostProcessor 期望 dict 格式，需要转换
                dict_results = [self._to_dict(r) for r in unique]
                processed = processor.process(dict_results)
                unique = [self._from_dict(d) for d in processed]
            except Exception as exc:
                logger.warning("PostProcessor failed: %s", exc)

        return unique

    def _fallback_vector_search(
        self,
        query: str,
        top_k: int,
        filters: VectorSearchFilters | None,
    ) -> list[RetrievalResult]:
        """失败时回退到纯向量检索。"""
        logger.warning("Fallback to pure vector search")
        if not self.vector_store:
            return []
        hits = self.vector_store.query(query, top_k, filters)
        return [
            RetrievalResult(
                id=h["id"],
                text=h.get("text", ""),
                metadata=h.get("metadata", {}),
                score=h.get("score", 0.0),
                stage_scores={"vector": h.get("score", 0.0)},
            )
            for h in hits
        ]

    # ------------------------------------------------------------------
    # 缓存
    # ------------------------------------------------------------------

    def _generate_query_id(self, query: str, filters: VectorSearchFilters | None, image_bytes: bytes | None) -> str:
        """生成查询唯一 ID。"""
        key = query
        if filters:
            key += json.dumps(filters.to_dict(), sort_keys=True, ensure_ascii=False)
        if image_bytes:
            key += hashlib.md5(image_bytes).hexdigest()[:16]
        return hashlib.sha256(key.encode()).hexdigest()[:32]

    def _get_cached(self, query_id: str) -> list[RetrievalResult] | None:
        """从缓存获取结果（L1 内存 → L2 Redis）。"""
        data = self._cache.get(query_id)
        if data is None:
            return None
        return [RetrievalResult(**item) for item in data]

    def _set_cached(self, query_id: str, results: list[RetrievalResult]) -> None:
        """写入缓存（L1 + L2）。"""
        data = [self._result_to_dict(r) for r in results]
        self._cache.set(query_id, data)

    def clear_cache(self) -> None:
        """清空缓存。"""
        self._cache.clear()

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_bm25(score: float) -> float:
        """将 BM25 分数归一化到 [0, 1]。"""
        return min(max(score / 50.0, 0.0), 1.0)

    @staticmethod
    def _to_dict(result: RetrievalResult) -> dict:
        return {
            "id": result.id,
            "text": result.text,
            "metadata": result.metadata,
            "score": result.score,
            "stage_scores": result.stage_scores,
            "rank": result.rank,
        }

    @staticmethod
    def _from_dict(d: dict) -> RetrievalResult:
        return RetrievalResult(
            id=d.get("id", ""),
            text=d.get("text", ""),
            metadata=d.get("metadata", {}),
            score=d.get("score", 0.0),
            stage_scores=d.get("stage_scores", {}),
            rank=d.get("rank", 0),
        )

    @staticmethod
    def _result_to_dict(result: RetrievalResult) -> dict:
        """缓存专用的完整序列化。"""
        return {
            "id": result.id,
            "text": result.text,
            "metadata": result.metadata,
            "score": result.score,
            "stage_scores": result.stage_scores,
            "rank": result.rank,
        }

    def get_stats(self) -> dict:
        """获取检索器统计信息。"""
        return {
            "config": {
                "enable_bm25": self.config.enable_bm25,
                "enable_vector": self.config.enable_vector,
                "enable_rerank": self.config.enable_rerank,
                "cache_enabled": self._cache.enabled,
                "cache_l1_size": len(self._cache._l1),
            },
            "components": {
                "bm25_available": self.bm25 is not None,
                "vector_store_available": self.vector_store is not None,
                "reranker_available": self.reranker is not None if self.reranker else False,
                "feedback_available": self.feedback is not None,
            },
        }
