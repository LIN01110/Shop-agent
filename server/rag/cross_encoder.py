"""
Cross-Encoder 精排模型（Phase 2 Stage 3）。

提供基于 Cross-Encoder 的点对点（Pointwise）重排序能力：
- 输入：查询文本 + 候选商品文本（拼接后传入模型）
- 输出：相关性分数（0-1）

设计要点：
- 支持 Chinese-CLIP 作为 Cross-Encoder（文本-文本交叉注意力）
- 支持轻量模型如 shibing624/text2vec-base-chinese
- 训练数据来自反馈闭环（点击/购买/停留时间）
- 推理时支持 batch 加速

依赖：
  pip install transformers torch

使用方式：
  from server.rag.cross_encoder import CrossEncoderReranker
  reranker = CrossEncoderReranker(model_name="shibing624/text2vec-base-chinese")
  scores = reranker.rerank(
      query="红色连衣裙",
      candidates=[{"text": "..."}, ...]
  )
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from server.rag.bm25_engine import BM25Hit

logger = logging.getLogger(__name__)

# 延迟导入
torch = None
transformers = None


def _ensure_transformers():
    global torch, transformers
    if torch is None:
        try:
            import torch as _torch
            import transformers as _transformers
            torch = _torch
            transformers = _transformers
        except ImportError as exc:
            raise RuntimeError(
                "Cross-Encoder requires torch and transformers. "
                "Install: pip install torch transformers"
            ) from exc
    return torch, transformers


@dataclass
class RerankCandidate:
    """精排候选。"""

    id: str
    text: str
    metadata: dict = field(default_factory=dict)
    bm25_score: float = 0.0
    vector_score: float = 0.0


@dataclass
class RerankResult:
    """精排结果。"""

    id: str
    score: float          # Cross-Encoder 分数（0-1，越高越相关）
    text: str = ""
    metadata: dict = field(default_factory=dict)
    bm25_score: float = 0.0
    vector_score: float = 0.0
    ensemble_score: float = 0.0  # 融合分数（可选）


class CrossEncoderModel(Protocol):
    """Cross-Encoder 模型协议。"""

    def score(self, query: str, texts: list[str]) -> list[float]:
        ...


class CrossEncoderReranker:
    """
    基于 Cross-Encoder 的精排模型。

    模型选择：
    - "shibing624/text2vec-base-chinese" (~400MB, 推荐，中文语义相似度)
    - "BAAI/bge-reranker-base" (~500MB, BGE 重排器)
    - "BAAI/bge-reranker-large" (~1.5GB)
    - 自定义微调模型路径

    融合策略（可选）：
    - 纯 Cross-Encoder：仅使用模型输出
    - 加权融合：ensemble_score = α * ce_score + β * bm25_score + γ * vector_score
    - 级联融合：先 Cross-Encoder 排序，再用其他信号微调
    """

    def __init__(
        self,
        model_name: str = "shibing624/text2vec-base-chinese",
        device: str = "auto",
        max_length: int = 512,
        batch_size: int = 16,
        cache_dir: str | Path | None = None,
        # 融合权重
        ce_weight: float = 0.7,
        bm25_weight: float = 0.15,
        vector_weight: float = 0.15,
    ) -> None:
        self.model_name = model_name
        self.device_str = device
        self.max_length = max_length
        self.batch_size = batch_size
        self.cache_dir = Path(cache_dir) if cache_dir else None

        # 融合权重
        self.ce_weight = ce_weight
        self.bm25_weight = bm25_weight
        self.vector_weight = vector_weight

        # 延迟加载
        self._tokenizer = None
        self._model = None
        self._device = None
        self._available = False
        self._load_error = ""
        self._load_time_ms = 0.0

    @property
    def available(self) -> bool:
        return self._available

    def _ensure_loaded(self) -> bool:
        """延迟加载模型。"""
        if self._available:
            return True
        if self._load_error:
            return False

        started = time.perf_counter()
        try:
            torch, transformers = _ensure_transformers()

            if self.device_str == "auto":
                self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            else:
                self._device = torch.device(self.device_str)

            logger.info(
                "Loading Cross-Encoder: %s on %s",
                self.model_name,
                self._device,
            )

            kwargs = {}
            if self.cache_dir:
                kwargs["cache_dir"] = str(self.cache_dir)

            # 加载 tokenizer 和模型
            # 注意：bge-reranker 等重排模型是序列分类架构，必须用
            # AutoModelForSequenceClassification 才能拿到 classifier 打分头；
            # 用 AutoModel 会丢弃打分头（scores 退化为无意义嵌入）。
            self._tokenizer = transformers.AutoTokenizer.from_pretrained(
                self.model_name,
                **kwargs,
            )
            self._model = transformers.AutoModelForSequenceClassification.from_pretrained(
                self.model_name,
                **kwargs,
            )
            self._model.to(self._device)
            self._model.eval()

            self._load_time_ms = round((time.perf_counter() - started) * 1000, 2)
            self._available = True
            logger.info(
                "Cross-Encoder loaded: %s, device=%s, time=%.1fms",
                self.model_name,
                self._device,
                self._load_time_ms,
            )
            return True

        except Exception as exc:
            self._load_error = str(exc)
            self._load_time_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.error("Failed to load Cross-Encoder: %s", exc)
            return False

    def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        use_ensemble: bool = True,
    ) -> list[RerankResult]:
        """
        对候选列表进行精排。

        Args:
            query: 用户查询
            candidates: 候选商品列表（通常来自 BM25 + 向量检索的前 N 个）
            use_ensemble: 是否使用融合分数

        Returns:
            按相关性排序的结果列表
        """
        if not candidates:
            return []

        if not self._ensure_loaded():
            # 模型不可用，回退到原始分数排序
            logger.warning("Cross-Encoder unavailable, returning candidates as-is")
            return [
                RerankResult(
                    id=c.id,
                    score=c.vector_score or c.bm25_score,
                    text=c.text,
                    metadata=c.metadata,
                    bm25_score=c.bm25_score,
                    vector_score=c.vector_score,
                )
                for c in candidates
            ]

        # 批量推理
        ce_scores = self._batch_score(query, [c.text for c in candidates])

        # 构建结果
        results = []
        for i, candidate in enumerate(candidates):
            ce_score = ce_scores[i]
            result = RerankResult(
                id=candidate.id,
                score=ce_score,
                text=candidate.text,
                metadata=candidate.metadata,
                bm25_score=candidate.bm25_score,
                vector_score=candidate.vector_score,
            )
            if use_ensemble:
                result.ensemble_score = (
                    self.ce_weight * ce_score
                    + self.bm25_weight * self._normalize_score(candidate.bm25_score)
                    + self.vector_weight * self._normalize_score(candidate.vector_score)
                )
            results.append(result)

        # 排序
        if use_ensemble:
            results.sort(key=lambda x: x.ensemble_score, reverse=True)
        else:
            results.sort(key=lambda x: x.score, reverse=True)

        return results

    def _batch_score(self, query: str, texts: list[str]) -> list[float]:
        """批量计算 Cross-Encoder 分数。"""
        torch, _ = _ensure_transformers()

        scores = []
        for i in range(0, len(texts), self.batch_size):
            batch_texts = texts[i : i + self.batch_size]
            batch_scores = self._score_batch(query, batch_texts)
            scores.extend(batch_scores)

        return scores

    def _score_batch(self, query: str, texts: list[str]) -> list[float]:
        """单 batch 推理。"""
        torch, _ = _ensure_transformers()

        # 构建输入：[CLS] query [SEP] text [SEP]
        inputs = self._tokenizer(
            [query] * len(texts),
            texts,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        inputs = {k: v.to(self._device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self._model(**inputs)
            # bge-reranker 为单标签序列分类：logits [batch, 1] 即相关性分数
            logits = outputs.logits.squeeze(-1)
            if logits.dim() == 0:
                logits = logits.unsqueeze(0)

        return logits.float().cpu().tolist()

    @staticmethod
    def _normalize_score(score: float, method: str = "sigmoid") -> float:
        """归一化分数到 [0, 1]。"""
        if method == "sigmoid":
            import math
            return 1 / (1 + math.exp(-score))
        if method == "minmax":
            # 假设 BM25 分数通常在 0-50 之间
            return min(max(score / 50.0, 0.0), 1.0)
        return min(max(score, 0.0), 1.0)


class SimpleCrossEncoderReranker:
    """
    轻量级 Cross-Encoder，使用 SentenceTransformer 风格。
    对中文支持更好，且推理速度更快。
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-base",
        device: str = "auto",
        batch_size: int = 32,
    ) -> None:
        self.model_name = model_name
        self.device_str = device
        self.batch_size = batch_size

        self._model = None
        self._available = False
        self._load_error = ""

    @property
    def available(self) -> bool:
        return self._available

    def _ensure_loaded(self) -> bool:
        if self._available:
            return True
        if self._load_error:
            return False

        try:
            from sentence_transformers import CrossEncoder as SBCrossEncoder

            device = self.device_str
            if device == "auto":
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"

            self._model = SBCrossEncoder(
                self.model_name,
                device=device,
                max_length=512,
            )
            self._available = True
            logger.info("SentenceTransformer CrossEncoder loaded: %s", self.model_name)
            return True
        except ImportError as exc:
            self._load_error = f"sentence-transformers not installed: {exc}"
            logger.error(self._load_error)
            return False
        except Exception as exc:
            self._load_error = str(exc)
            logger.error("Failed to load ST CrossEncoder: %s", exc)
            return False

    def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        use_ensemble: bool = True,
    ) -> list[RerankResult]:
        if not candidates:
            return []
        if not self._ensure_loaded():
            return [
                RerankResult(
                    id=c.id,
                    score=c.vector_score or c.bm25_score,
                    text=c.text,
                    metadata=c.metadata,
                    bm25_score=c.bm25_score,
                    vector_score=c.vector_score,
                )
                for c in candidates
            ]

        # 构建 sentence pairs
        pairs = [[query, c.text] for c in candidates]
        scores = self._model.predict(pairs, batch_size=self.batch_size)

        # 映射分数到 [0, 1]（BGE reranker 输出通常是 raw logits）
        import math
        scores = [1 / (1 + math.exp(-s)) for s in scores]

        results = []
        for i, candidate in enumerate(candidates):
            result = RerankResult(
                id=candidate.id,
                score=scores[i],
                text=candidate.text,
                metadata=candidate.metadata,
                bm25_score=candidate.bm25_score,
                vector_score=candidate.vector_score,
            )
            if use_ensemble:
                result.ensemble_score = (
                    0.6 * scores[i]
                    + 0.2 * min(max(candidate.bm25_score / 50.0, 0.0), 1.0)
                    + 0.2 * min(max(candidate.vector_score, 0.0), 1.0)
                )
            results.append(result)

        if use_ensemble:
            results.sort(key=lambda x: x.ensemble_score, reverse=True)
        else:
            results.sort(key=lambda x: x.score, reverse=True)

        return results
