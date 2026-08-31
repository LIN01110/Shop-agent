"""
BM25 倒排索引引擎（Phase 2 Stage 1）。

提供基于 jieba 中文分词的 BM25 检索能力，作为三阶段检索的第一阶段（粗排）。

设计要点：
- 纯本地实现，零外部依赖（除 jieba 分词外）
- 倒排索引 + 正排索引，支持毫秒级查询
- 增量更新：支持 add/delete 操作
- 与现有 VectorStore 接口兼容，可独立使用

BM25 公式：
  score(q, d) = Σ IDF(qi) * (f(qi, d) * (k1 + 1)) / (f(qi, d) + k1 * (1 - b + b * |d| / avgdl))

依赖：
  pip install jieba rank-bm25

使用方式：
  from server.rag.bm25_engine import BM25Engine
  engine = BM25Engine(documents)
  hits = engine.search("红色连衣裙", top_k=100)
"""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from server.rag.types import VectorDocument, VectorSearchFilters

logger = logging.getLogger(__name__)

# 延迟导入 jieba
_jieba = None


def _ensure_jieba():
    global _jieba
    if _jieba is None:
        try:
            import jieba as _jb
            _jieba = _jb
            # 加载自定义词典（如果有）
            _jb.setLogLevel(logging.WARNING)
        except ImportError as exc:
            raise RuntimeError(
                "BM25 engine requires jieba. Install: pip install jieba"
            ) from exc
    return _jieba


@dataclass
class BM25Config:
    """BM25 超参数。"""

    k1: float = 1.5    # 词频饱和参数
    b: float = 0.75    # 文档长度归一化参数
    epsilon: float = 0.25  # IDF 平滑参数


@dataclass
class Posting:
    """倒排列表项。"""

    doc_id: str
    term_freq: int = 0
    positions: list[int] = field(default_factory=list)


@dataclass
class BM25Hit:
    """BM25 检索结果。"""

    id: str
    score: float
    text: str = ""
    metadata: dict = field(default_factory=dict)
    matched_terms: list[str] = field(default_factory=list)
    term_freqs: dict = field(default_factory=dict)


class BM25Engine:
    """
    BM25 倒排索引引擎。

    内部数据结构：
    - inverted_index: {term: [Posting, ...]}
    - forward_index: {doc_id: {text, metadata, length, tokens}}
    - doc_freq: {term: 出现该词的文档数}
    - total_docs: 总文档数
    - avg_doc_len: 平均文档长度
    """

    def __init__(
        self,
        documents: list[VectorDocument] | None = None,
        config: BM25Config | None = None,
    ) -> None:
        self.config = config or BM25Config()

        # 索引结构
        self.inverted_index: dict[str, list[Posting]] = defaultdict(list)
        self.forward_index: dict[str, dict] = {}
        self.doc_freq: dict[str, int] = defaultdict(int)

        # 统计量
        self.total_docs: int = 0
        self.total_doc_len: int = 0
        self.avg_doc_len: float = 0.0

        # 停用词
        self.stopwords: set[str] = self._load_stopwords()

        # 构建索引
        if documents:
            self.build(documents)

    # ------------------------------------------------------------------
    # 索引构建
    # ------------------------------------------------------------------

    def build(self, documents: list[VectorDocument]) -> None:
        """从文档列表构建索引。"""
        self.clear()
        for doc in documents:
            self._index_document(doc)
        self._finalize_stats()
        logger.info(
            "BM25 index built: %d docs, %d unique terms, avg_len=%.1f",
            self.total_docs,
            len(self.doc_freq),
            self.avg_doc_len,
        )

    def add(self, documents: list[VectorDocument]) -> None:
        """增量添加文档。"""
        for doc in documents:
            if doc.id in self.forward_index:
                # 先删除旧版本
                self.delete([doc.id])
            self._index_document(doc)
        self._finalize_stats()

    def delete(self, ids: list[str]) -> None:
        """删除文档。"""
        for doc_id in ids:
            if doc_id not in self.forward_index:
                continue
            doc_data = self.forward_index.pop(doc_id)
            tokens = doc_data.get("tokens", [])
            # 从倒排索引中移除
            for term in set(tokens):
                postings = self.inverted_index.get(term, [])
                self.inverted_index[term] = [p for p in postings if p.doc_id != doc_id]
                if not self.inverted_index[term]:
                    del self.inverted_index[term]
                self.doc_freq[term] = max(0, self.doc_freq.get(term, 0) - 1)

            self.total_docs -= 1
            self.total_doc_len -= len(tokens)

        self._finalize_stats()

    def clear(self) -> None:
        """清空索引。"""
        self.inverted_index.clear()
        self.forward_index.clear()
        self.doc_freq.clear()
        self.total_docs = 0
        self.total_doc_len = 0
        self.avg_doc_len = 0.0

    def _index_document(self, doc: VectorDocument) -> None:
        """索引单个文档。"""
        # 合并文本：name + category + tags + description
        text_parts = [
            str(doc.metadata.get("name", "")),
            str(doc.metadata.get("category", "")),
            str(doc.metadata.get("sub_category", "")),
            str(doc.metadata.get("brand", "")),
            " ".join(str(t) for t in doc.metadata.get("tags", [])),
            str(doc.metadata.get("description", "")),
            doc.text,
        ]
        full_text = " ".join(text_parts)

        # 分词
        tokens = self._tokenize(full_text)
        doc_len = len(tokens)

        # 记录正排
        self.forward_index[doc.id] = {
            "text": doc.text,
            "metadata": doc.metadata,
            "length": doc_len,
            "tokens": tokens,
        }

        # 构建倒排
        term_positions: dict[str, list[int]] = defaultdict(list)
        for pos, token in enumerate(tokens):
            term_positions[token].append(pos)

        for term, positions in term_positions.items():
            self.inverted_index[term].append(
                Posting(
                    doc_id=doc.id,
                    term_freq=len(positions),
                    positions=positions,
                )
            )
            self.doc_freq[term] += 1

        self.total_docs += 1
        self.total_doc_len += doc_len

    def _finalize_stats(self) -> None:
        """计算最终统计量。"""
        if self.total_docs > 0:
            self.avg_doc_len = self.total_doc_len / self.total_docs
        else:
            self.avg_doc_len = 0.0

    # ------------------------------------------------------------------
    # 分词
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> list[str]:
        """中文分词。"""
        jieba = _ensure_jieba()
        # 预处理：去标点、转小写
        text = re.sub(r'[^\u4e00-\u9fff\w\s]', ' ', text)
        tokens = list(jieba.cut_for_search(text.lower()))
        # 过滤停用词和空串
        return [t.strip() for t in tokens if t.strip() and t.strip() not in self.stopwords and len(t.strip()) > 1]

    def _load_stopwords(self) -> set[str]:
        """加载中文停用词。"""
        # 基础停用词表
        basic_stopwords = {
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
            "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
            "你", "会", "着", "没有", "看", "好", "自己", "这", "那",
            "个", "之", "与", "及", "等", "或", "但", "而", "因为",
            "所以", "如果", "虽然", "但是", "然而", "因此", "从而", "而且",
            "或者", "还是", "要么", "假如", "假设", "即使", "尽管",
            "不管", "无论", "不论", "不只", "不仅", "不但", "不光",
            "产品", "商品", "这款", "这个", "那个", "什么", "怎么",
            "怎样", "如何", "为什么", "多少", "几", "哪些", "哪里",
            "推荐", "喜欢", "想要", "需要", "适合", "觉得", "感觉",
            "一下", "一些", "一点", "比较", "非常", "特别", "挺",
            "蛮", "挺", "还算", "大概", "大约", "左右", "差不多",
            "请问", "帮忙", "帮我", "给我", "给我找", "有没有",
        }
        return basic_stopwords

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 100, filters: VectorSearchFilters | None = None) -> list[BM25Hit]:
        """
        BM25 检索。

        流程：
        1. 查询分词
        2. 取倒排列表的并集
        3. 对每个候选文档计算 BM25 分数
        4. 按分数排序，返回 top_k
        """
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        # 收集候选文档
        candidate_scores: dict[str, float] = defaultdict(float)
        candidate_terms: dict[str, list[str]] = defaultdict(list)
        candidate_term_freqs: dict[str, dict] = defaultdict(dict)

        for term in query_tokens:
            postings = self.inverted_index.get(term, [])
            idf = self._idf(term)
            for posting in postings:
                doc_id = posting.doc_id
                doc_data = self.forward_index.get(doc_id)
                if not doc_data:
                    continue

                # 应用过滤器
                if filters and not self._matches_filters(doc_data["metadata"], filters):
                    continue

                score = self._bm25_score(term, posting.term_freq, doc_data["length"], idf)
                candidate_scores[doc_id] += score
                candidate_terms[doc_id].append(term)
                candidate_term_freqs[doc_id][term] = posting.term_freq

        # 构建结果
        hits = []
        for doc_id, score in candidate_scores.items():
            doc_data = self.forward_index[doc_id]
            hits.append(
                BM25Hit(
                    id=doc_id,
                    score=score,
                    text=doc_data["text"],
                    metadata=doc_data["metadata"],
                    matched_terms=candidate_terms[doc_id],
                    term_freqs=candidate_term_freqs[doc_id],
                )
            )

        # 按分数排序
        hits.sort(key=lambda x: x.score, reverse=True)
        return hits[:top_k]

    def _idf(self, term: str) -> float:
        """计算 IDF（逆文档频率）。"""
        df = self.doc_freq.get(term, 0)
        if df == 0:
            return 0.0
        # BM25 IDF: log((N - df + 0.5) / (df + 0.5) + 1)
        idf = math.log(
            (self.total_docs - df + 0.5) / (df + 0.5) + 1.0
        )
        return max(idf, self.config.epsilon)

    def _bm25_score(self, term: str, term_freq: int, doc_len: int, idf: float) -> float:
        """计算单个词的 BM25 分数。"""
        k1 = self.config.k1
        b = self.config.b
        avgdl = self.avg_doc_len if self.avg_doc_len > 0 else 1.0

        numerator = term_freq * (k1 + 1)
        denominator = term_freq + k1 * (1 - b + b * (doc_len / avgdl))

        return idf * (numerator / denominator)

    def _matches_filters(self, metadata: dict, filters: VectorSearchFilters) -> bool:
        """检查 metadata 是否匹配过滤条件。"""
        if filters.categories:
            cat = str(metadata.get("category", ""))
            if cat not in filters.categories:
                return False
        if filters.product_types:
            sub = str(metadata.get("sub_category", ""))
            if sub not in filters.product_types:
                return False
        # 价格过滤（简化版，BM25 阶段通常不严格过滤价格）
        if filters.min_price is not None or filters.max_price is not None:
            price = float(metadata.get("price", 0) or 0)
            if filters.min_price is not None and price < filters.min_price:
                return False
            if filters.max_price is not None and price > filters.max_price:
                return False
        return True

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """获取索引统计信息。"""
        return {
            "total_docs": self.total_docs,
            "total_terms": len(self.inverted_index),
            "avg_doc_len": round(self.avg_doc_len, 2),
            "total_doc_len": self.total_doc_len,
        }

    def get_term_stats(self, term: str) -> dict:
        """获取指定词的统计信息。"""
        postings = self.inverted_index.get(term, [])
        return {
            "term": term,
            "doc_freq": len(postings),
            "total_freq": sum(p.term_freq for p in postings),
            "idf": round(self._idf(term), 4),
        }

    def save(self, path: str | Path) -> None:
        """保存索引到文件。"""
        import json
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "config": {"k1": self.config.k1, "b": self.config.b},
            "forward_index": {
                doc_id: {
                    "text": d["text"],
                    "metadata": d["metadata"],
                    "length": d["length"],
                }
                for doc_id, d in self.forward_index.items()
            },
            "inverted_index": {
                term: [
                    {"doc_id": p.doc_id, "term_freq": p.term_freq, "positions": p.positions}
                    for p in postings
                ]
                for term, postings in self.inverted_index.items()
            },
            "doc_freq": dict(self.doc_freq),
            "stats": {
                "total_docs": self.total_docs,
                "total_doc_len": self.total_doc_len,
            },
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("BM25 index saved: %s", path)

    @classmethod
    def load(cls, path: str | Path) -> "BM25Engine":
        """从文件加载索引。"""
        import json
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        config = BM25Config(**data.get("config", {}))
        engine = cls(config=config)

        # 恢复正排索引
        for doc_id, doc_data in data["forward_index"].items():
            engine.forward_index[doc_id] = {
                "text": doc_data["text"],
                "metadata": doc_data["metadata"],
                "length": doc_data["length"],
                "tokens": [],  # tokens 会在搜索时重新分词
            }

        # 恢复倒排索引
        for term, postings_data in data["inverted_index"].items():
            engine.inverted_index[term] = [
                Posting(
                    doc_id=p["doc_id"],
                    term_freq=p["term_freq"],
                    positions=p.get("positions", []),
                )
                for p in postings_data
            ]
            engine.doc_freq[term] = len(postings_data)

        # 恢复统计量
        stats = data.get("stats", {})
        engine.total_docs = stats.get("total_docs", 0)
        engine.total_doc_len = stats.get("total_doc_len", 0)
        engine._finalize_stats()

        logger.info("BM25 index loaded: %s", path)
        return engine
