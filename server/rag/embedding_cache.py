"""
Purpose: Embedding 缓存：SQLite 持久化缓存，避免重复计算 embedding。
"""

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class EmbeddingCacheKey:
    provider: str
    model: str
    modality: str
    content_hash: str

    @property
    def storage_key(self) -> str:
        raw = f"{self.provider}\0{self.model}\0{self.modality}\0{self.content_hash}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class EmbeddingCache:
    """Small persistent embedding cache used by offline index jobs and query-time image lookup."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def get(self, key: EmbeddingCacheKey) -> list[float] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT vector_json FROM embeddings WHERE cache_key = ?",
                (key.storage_key,),
            ).fetchone()
        if not row:
            return None
        return [float(value) for value in json.loads(row[0])]

    def set(self, key: EmbeddingCacheKey, vector: list[float], metadata: dict[str, Any] | None = None) -> None:
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO embeddings (
                    cache_key, provider, model, modality, content_hash,
                    dimensions, vector_json, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    vector_json = excluded.vector_json,
                    dimensions = excluded.dimensions,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    key.storage_key,
                    key.provider,
                    key.model,
                    key.modality,
                    key.content_hash,
                    len(vector),
                    json.dumps(vector, separators=(",", ":")),
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            connection.commit()

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()
        return int(row[0]) if row else 0

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    cache_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    modality TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_embeddings_lookup
                ON embeddings(provider, model, modality, content_hash)
                """
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()


def content_hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def content_hash_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
