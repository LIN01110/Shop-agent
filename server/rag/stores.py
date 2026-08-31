"""
答辩重点 🟠（存储实现）
任务：Day 2 任务 2.7 — 与 vector_store.py 一起读。
核心：ChromaStore 与 LocalJsonVectorStore 的具体实现，metadata 过滤、embedding 缓存、
      本地 JSON 倒排索引等细节都在这里。
高频追问：
  - “Chroma 的 where 过滤是怎么下推的？”
    → query() 将 SearchFilters 转成 Chroma metadata where 表达式
  - “LocalJsonVectorStore 怎么保证没有 Chroma 也能跑？”
    → 本地倒排索引 + 预计算 embedding 缓存
"""

import heapq
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from server.rag.chroma_metadata import (
    build_chroma_where,
    from_chroma_metadata,
    metadata_matches_vector_filters,
    to_chroma_metadata,
)
from server.rag.documents import load_product_documents
from server.rag.embeddings import HashingEmbeddingFunction
from server.rag.scoring import (
    build_document_feature_index,
    build_document_features,
    score_chroma_hit,
    score_document_features,
    tokenize,
)
from server.rag.category_taxonomy import canonicalize_category, infer_category_ids
from server.rag.taxonomy import canonicalize_product_type, infer_product_type_ids
from server.rag.types import DocumentFeatures, VectorDocument, VectorSearchFilters


class LocalJsonVectorStore:
    def __init__(self, data_path: Path) -> None:
        self.data_path = data_path
        self.documents = load_product_documents(data_path)
        self.category_index = build_category_index(self.documents)
        self.product_type_index = build_product_type_index(self.documents)
        self.document_features = build_document_feature_index(self.documents)

    @classmethod
    def from_documents(cls, documents: list[VectorDocument]) -> "LocalJsonVectorStore":
        store = cls.__new__(cls)
        store.data_path = None
        store.documents = list(documents)
        store.category_index = build_category_index(store.documents)
        store.product_type_index = build_product_type_index(store.documents)
        store.document_features = build_document_feature_index(store.documents)
        return store

    def add(self, documents: list[VectorDocument]) -> None:
        self.documents.extend(documents)
        for document in documents:
            for category in infer_category_ids(document.metadata):
                self.category_index.setdefault(category, []).append(document)
            for product_type in infer_product_type_ids(document.metadata):
                self.product_type_index.setdefault(product_type, []).append(document)
            self.document_features[document.id] = build_document_features(document)

    def query(self, query: str, top_k: int = 5, filters: VectorSearchFilters | None = None) -> list[dict]:
        query_tokens = tokenize(query)
        hits: list[dict] = []
        for doc in self.candidate_documents(filters):
            if not metadata_matches_vector_filters(doc.metadata, filters):
                continue
            score = score_document_features(query_tokens, self.document_features[doc.id])
            if score > 0:
                hits.append({"id": doc.id, "text": doc.text, "metadata": doc.metadata, "score": score})

        return heapq.nlargest(top_k, hits, key=lambda item: item["score"])

    def delete(self, ids: list[str]) -> None:
        id_set = set(ids)
        self.documents = [doc for doc in self.documents if doc.id not in id_set]
        self.category_index = build_category_index(self.documents)
        self.product_type_index = build_product_type_index(self.documents)
        self.document_features = build_document_feature_index(self.documents)

    def candidate_documents(self, filters: VectorSearchFilters | None) -> Iterable[VectorDocument]:
        if not filters or (not filters.product_types and not filters.categories):
            yield from self.documents
            return

        seen: set[str] = set()
        if filters.categories:
            for category in filters.categories:
                canonical = canonicalize_category(category)
                for document in self.category_index.get(canonical, []):
                    if document.id in seen:
                        continue
                    seen.add(document.id)
                    yield document
            if not filters.product_types:
                return
        for product_type in filters.product_types:
            canonical = canonicalize_product_type(product_type)
            for document in self.product_type_index.get(canonical, []):
                if document.id in seen:
                    continue
                seen.add(document.id)
                yield document

    def count(self) -> int:
        return len(self.documents)


class ChromaStore:
    def __init__(
        self,
        persist_path: Path,
        collection_name: str = "products",
        embedding_function: Any | None = None,
    ) -> None:
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError as exc:
            raise RuntimeError(
                "chromadb is not installed. Run `pip install -r server/requirements.txt`."
            ) from exc

        self.persist_path = persist_path
        self.persist_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(self.persist_path),
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=embedding_function or HashingEmbeddingFunction(),
        )

    def add(self, documents: list[VectorDocument]) -> None:
        if not documents:
            return

        self.collection.upsert(
            ids=[document.id for document in documents],
            documents=[document.text for document in documents],
            metadatas=[to_chroma_metadata(document.metadata) for document in documents],
        )

    def query(self, query: str, top_k: int = 5, filters: VectorSearchFilters | None = None) -> list[dict]:
        if self.count() == 0:
            return []

        candidate_count = min(self.count(), max(top_k * 10, 50))
        where = build_chroma_where(filters)
        query_kwargs: dict[str, Any] = {
            "query_texts": [query],
            "n_results": candidate_count,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            query_kwargs["where"] = where

        result = self.collection.query(**query_kwargs)
        ids = result.get("ids", [[]])[0]
        texts = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]

        hits: list[dict] = []
        for index, item_id in enumerate(ids):
            metadata = from_chroma_metadata(metadatas[index])
            if not metadata_matches_vector_filters(metadata, filters):
                continue
            distance = float(distances[index]) if index < len(distances) else 0.0
            hits.append(
                {
                    "id": item_id,
                    "text": texts[index] if index < len(texts) else "",
                    "metadata": metadata,
                    "score": score_chroma_hit(query, metadata, texts[index], distance),
                }
            )
        hits.sort(key=lambda item: item["score"], reverse=True)
        return hits[:top_k]

    def delete(self, ids: list[str]) -> None:
        if ids:
            self.collection.delete(ids=ids)

    def count(self) -> int:
        return int(self.collection.count())


def build_product_type_index(documents: list[VectorDocument]) -> dict[str, list[VectorDocument]]:
    index: dict[str, list[VectorDocument]] = {}
    for document in documents:
        for product_type in infer_product_type_ids(document.metadata):
            index.setdefault(product_type, []).append(document)
    return index


def build_category_index(documents: list[VectorDocument]) -> dict[str, list[VectorDocument]]:
    index: dict[str, list[VectorDocument]] = {}
    for document in documents:
        for category in infer_category_ids(document.metadata):
            index.setdefault(category, []).append(document)
    return index
