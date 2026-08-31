"""
Purpose: 视觉 Embedding 索引：将商品图片转换为向量索引，支持多模态 embedding 检索。
"""

import json
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol

from PIL import Image, UnidentifiedImageError

from server.inputs.image_similarity import decode_base64_image
from server.rag.embedding_cache import EmbeddingCache, EmbeddingCacheKey, content_hash_bytes
from server.rag.taxonomy import infer_product_type_ids, product_type_display_names
from server.rag.vector_store import load_product_documents


VISUAL_INDEX_SCHEMA_VERSION = 1
DEFAULT_PROVIDER = "ark_multimodal"


class ImageEmbeddingProvider(Protocol):
    model: str

    def embed_image_bytes(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> list[float]:
        ...


@dataclass(frozen=True)
class ProductImageVectorEntry:
    product: dict
    image_name: str
    content_hash: str
    vector: list[float]


@dataclass(frozen=True)
class VisualIndexBuildSummary:
    index_path: str
    products_seen: int
    indexed_count: int
    skipped_existing: int
    failed_count: int
    cache_count: int
    elapsed_ms: float


class ProductVisualEmbeddingIndex:
    """Persistent product-image vector index with query-time image embedding lookup."""

    def __init__(
        self,
        index_path: Path,
        *,
        embedder: ImageEmbeddingProvider | None = None,
        cache: EmbeddingCache | None = None,
        provider: str = DEFAULT_PROVIDER,
        model: str = "",
    ) -> None:
        self.index_path = index_path
        self.embedder = embedder
        self.cache = cache
        self.provider = provider
        self.model = model or getattr(embedder, "model", "")
        self.entries = load_visual_index(index_path)
        self.last_error = ""

    @property
    def available(self) -> bool:
        return bool(self.entries and self.embedder and self.cache and self.model)

    def match_base64_image(
        self,
        image_base64: str,
        *,
        image_mime_type: str = "image/jpeg",
        top_k: int = 3,
    ) -> list[dict]:
        image_bytes = decode_base64_image(image_base64)
        if not image_bytes:
            return []
        return self.match_image_bytes(image_bytes, image_mime_type=image_mime_type, top_k=top_k)

    def match_image_bytes(
        self,
        image_bytes: bytes,
        *,
        image_mime_type: str = "image/jpeg",
        top_k: int = 3,
    ) -> list[dict]:
        self.last_error = ""
        if not self.available:
            return []
        try:
            prepared_bytes, prepared_mime = prepare_image_for_embedding(image_bytes, image_mime_type)
            query_vector = cached_embed_image(
                self.embedder,
                self.cache,
                provider=self.provider,
                model=self.model,
                image_bytes=prepared_bytes,
                mime_type=prepared_mime,
                metadata={"role": "query_image"},
            )
        except (OSError, RuntimeError, UnidentifiedImageError) as exc:
            self.last_error = str(exc)
            return []

        scored: list[dict] = []
        for entry in self.entries:
            similarity = cosine_similarity(query_vector, entry.vector)
            scored.append(
                {
                    **entry.product,
                    "similarity": similarity,
                    "visual_match_source": "multimodal_embedding",
                    "visual_embedding_model": self.model,
                    "image_name": entry.image_name,
                }
            )
        scored.sort(key=lambda item: item["similarity"], reverse=True)
        return scored[: max(int(top_k), 0)]


def build_product_visual_index(
    *,
    product_data_path: Path,
    product_image_dir: Path,
    index_path: Path,
    embedder: ImageEmbeddingProvider,
    cache: EmbeddingCache,
    provider: str = DEFAULT_PROVIDER,
    model: str = "",
    limit: int = 0,
    checkpoint_interval: int = 10,
    force: bool = False,
) -> VisualIndexBuildSummary:
    started = time.perf_counter()
    model = model or getattr(embedder, "model", "")
    existing = {} if force else existing_entry_lookup(load_visual_index(index_path), provider=provider, model=model)
    entries = [] if force else load_visual_index(index_path)
    products_seen = 0
    skipped_existing = 0
    failed_count = 0

    for document in load_product_documents(product_data_path):
        image_name = Path(str(document.metadata.get("image_url", "")).replace("\\", "/")).name
        if not image_name:
            continue
        image_path = product_image_dir / image_name
        if not image_path.exists():
            continue

        if limit and products_seen >= limit:
            break
        products_seen += 1

        try:
            prepared_bytes, prepared_mime = prepare_image_for_embedding(image_path.read_bytes())
            content_hash = content_hash_bytes(prepared_bytes)
            existing_key = f"{document.id}:{image_name}:{content_hash}"
            if existing_key in existing:
                skipped_existing += 1
                continue

            vector = cached_embed_image(
                embedder,
                cache,
                provider=provider,
                model=model,
                image_bytes=prepared_bytes,
                mime_type=prepared_mime,
                metadata={"role": "product_image", "product_id": document.id, "image_name": image_name},
            )
            product_types = infer_product_type_ids(document.metadata)
            entries.append(
                ProductImageVectorEntry(
                    product={
                        "id": document.id,
                        "name": document.metadata.get("name", ""),
                        "brand": document.metadata.get("brand", ""),
                        "category": document.metadata.get("category", ""),
                        "product_types": product_types,
                        "product_type_names": product_type_display_names(product_types),
                    },
                    image_name=image_name,
                    content_hash=content_hash,
                    vector=vector,
                )
            )
        except (OSError, RuntimeError, UnidentifiedImageError):
            failed_count += 1

        if products_seen % max(checkpoint_interval, 1) == 0:
            save_visual_index(index_path, entries, provider=provider, model=model)

    save_visual_index(index_path, entries, provider=provider, model=model)
    return VisualIndexBuildSummary(
        index_path=str(index_path),
        products_seen=products_seen,
        indexed_count=len(entries),
        skipped_existing=skipped_existing,
        failed_count=failed_count,
        cache_count=cache.count(),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
    )


def cached_embed_image(
    embedder: ImageEmbeddingProvider,
    cache: EmbeddingCache,
    *,
    provider: str,
    model: str,
    image_bytes: bytes,
    mime_type: str,
    metadata: dict,
) -> list[float]:
    content_hash = content_hash_bytes(image_bytes)
    key = EmbeddingCacheKey(provider=provider, model=model, modality="image", content_hash=content_hash)
    cached = cache.get(key)
    if cached is not None:
        return cached
    vector = embedder.embed_image_bytes(image_bytes, mime_type=mime_type)
    cache.set(key, vector, metadata={**metadata, "mime_type": mime_type})
    return vector


def prepare_image_for_embedding(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    *,
    max_side: int = 1024,
    quality: int = 85,
) -> tuple[bytes, str]:
    with Image.open(BytesIO(image_bytes)) as image:
        image = image.convert("RGB")
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        return output.getvalue(), "image/jpeg"


def load_visual_index(index_path: Path) -> list[ProductImageVectorEntry]:
    if not index_path.exists():
        return []
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    entries = []
    for item in payload.get("entries", []):
        entries.append(
            ProductImageVectorEntry(
                product=dict(item.get("product", {})),
                image_name=str(item.get("image_name", "")),
                content_hash=str(item.get("content_hash", "")),
                vector=[float(value) for value in item.get("vector", [])],
            )
        )
    return entries


def save_visual_index(
    index_path: Path,
    entries: list[ProductImageVectorEntry],
    *,
    provider: str,
    model: str,
) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": VISUAL_INDEX_SCHEMA_VERSION,
        "generated_at": time.time(),
        "provider": provider,
        "model": model,
        "entries": [
            {
                "product": entry.product,
                "image_name": entry.image_name,
                "content_hash": entry.content_hash,
                "vector": entry.vector,
            }
            for entry in entries
        ],
    }
    tmp_path = index_path.with_suffix(index_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp_path.replace(index_path)


def existing_entry_lookup(entries: list[ProductImageVectorEntry], *, provider: str, model: str) -> set[str]:
    return {f"{entry.product.get('id', '')}:{entry.image_name}:{entry.content_hash}" for entry in entries}


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
