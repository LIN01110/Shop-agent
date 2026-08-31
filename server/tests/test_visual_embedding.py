"""
Purpose: 视觉 Embedding 测试：验证图片向量化索引。
"""

import base64
import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from server.inputs.visual_embedding import (
    ProductImageVectorEntry,
    ProductVisualEmbeddingIndex,
    build_product_visual_index,
    prepare_image_for_embedding,
    save_visual_index,
)
from server.rag.embedding_cache import EmbeddingCache, EmbeddingCacheKey, content_hash_bytes


class FakeImageEmbedder:
    model = "fake-visual-model"

    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = vector or [1.0, 0.0]
        self.calls = 0

    def embed_image_bytes(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> list[float]:
        self.calls += 1
        return list(self.vector)


def test_embedding_cache_round_trip(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embeddings.sqlite3")
    key = EmbeddingCacheKey(
        provider="test",
        model="fake",
        modality="image",
        content_hash=content_hash_bytes(b"image"),
    )

    assert cache.get(key) is None
    cache.set(key, [1.0, 0.5], metadata={"role": "test"})

    assert cache.get(key) == [1.0, 0.5]
    assert cache.count() == 1


def test_product_visual_embedding_index_matches_query_image(tmp_path: Path) -> None:
    index_path = tmp_path / "visual_index.json"
    cache = EmbeddingCache(tmp_path / "embeddings.sqlite3")
    save_visual_index(
        index_path,
        [
            ProductImageVectorEntry(
                product={"id": "shoe", "name": "Shoe", "brand": "Brand", "category": "Shoes"},
                image_name="shoe.jpg",
                content_hash="shoe-hash",
                vector=[1.0, 0.0],
            ),
            ProductImageVectorEntry(
                product={"id": "pants", "name": "Pants", "brand": "Brand", "category": "Pants"},
                image_name="pants.jpg",
                content_hash="pants-hash",
                vector=[0.0, 1.0],
            ),
        ],
        provider="test",
        model="fake-visual-model",
    )
    matcher = ProductVisualEmbeddingIndex(
        index_path,
        embedder=FakeImageEmbedder([1.0, 0.0]),
        cache=cache,
        provider="test",
        model="fake-visual-model",
    )
    image_base64 = base64.b64encode(make_jpeg()).decode("ascii")

    matches = matcher.match_base64_image(image_base64, top_k=2)

    assert matches[0]["id"] == "shoe"
    assert matches[0]["visual_match_source"] == "multimodal_embedding"


def test_build_product_visual_index_is_cached_and_resumable(tmp_path: Path) -> None:
    product_data = tmp_path / "products.json"
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "shoe.jpg").write_bytes(make_jpeg())
    product_data.write_text(
        json.dumps(
            [
                {
                    "id": "shoe",
                    "name": "Running Shoe",
                    "category": "Shoes",
                    "sub_category": "Shoes",
                    "brand": "Brand",
                    "product_types": [],
                    "price": 99.0,
                    "stock": 10,
                    "image_url": "shoe.jpg",
                    "tags": [],
                    "attributes": {},
                    "description": "A running shoe.",
                }
            ]
        ),
        encoding="utf-8",
    )
    cache = EmbeddingCache(tmp_path / "embeddings.sqlite3")
    embedder = FakeImageEmbedder([1.0, 0.0])
    index_path = tmp_path / "visual_index.json"

    first = build_product_visual_index(
        product_data_path=product_data,
        product_image_dir=image_dir,
        index_path=index_path,
        embedder=embedder,
        cache=cache,
        provider="test",
        model="fake-visual-model",
    )
    second = build_product_visual_index(
        product_data_path=product_data,
        product_image_dir=image_dir,
        index_path=index_path,
        embedder=embedder,
        cache=cache,
        provider="test",
        model="fake-visual-model",
    )

    assert first.indexed_count == 1
    assert first.failed_count == 0
    assert second.skipped_existing == 1
    assert cache.count() == 1
    assert index_path.exists()


def test_prepare_image_for_embedding_compresses_to_jpeg() -> None:
    image_bytes, mime_type = prepare_image_for_embedding(make_jpeg(size=(1600, 1200)))

    assert mime_type == "image/jpeg"
    assert image_bytes.startswith(b"\xff\xd8")


def make_jpeg(size: tuple[int, int] = (32, 32), color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="JPEG")
    return output.getvalue()
