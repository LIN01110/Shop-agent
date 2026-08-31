"""
Purpose: RAG 类型定义：VectorDocument、VectorSearchFilters 等数据类。
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VectorDocument:
    id: str
    text: str
    metadata: dict


@dataclass(frozen=True)
class DocumentFeatures:
    haystack: str
    title_text: str
    tokens: frozenset[str]


@dataclass(frozen=True)
class VectorSearchFilters:
    min_price: float | None = None
    max_price: float | None = None
    categories: tuple[str, ...] = ()
    product_types: tuple[str, ...] = ()
    in_stock_only: bool = False


class VectorStore(Protocol):
    def add(self, documents: list[VectorDocument]) -> None:
        ...

    def query(self, query: str, top_k: int = 5, filters: VectorSearchFilters | None = None) -> list[dict]:
        ...

    def delete(self, ids: list[str]) -> None:
        ...
