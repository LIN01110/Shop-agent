"""
Purpose: 向量存储测试：验证 ChromaStore 和 LocalJsonVectorStore。
"""

from pathlib import Path

import httpx
import pytest

from server.rag.vector_store import (
    ArkEmbeddingFunction,
    ArkMultimodalEmbeddingFunction,
    ChromaStore,
    HashingEmbeddingFunction,
    LocalJsonVectorStore,
    VectorSearchFilters,
    build_chroma_embedding_function,
    build_chroma_where,
    load_product_documents,
    parse_embedding_response,
    parse_multimodal_embedding_response,
    product_type_filter_key,
    to_chroma_metadata,
)


ROOT_DIR = Path(__file__).resolve().parents[2]


def test_hashing_embedding_is_deterministic() -> None:
    embedder = HashingEmbeddingFunction(dimensions=32)

    first = embedder(["保湿眼霜"])[0]
    second = embedder(["保湿眼霜"])[0]

    assert first == second
    assert len(first) == 32


def test_chroma_store_round_trip(tmp_path: Path) -> None:
    pytest.importorskip("chromadb")
    documents = load_product_documents(ROOT_DIR / "data" / "products_ref.json")
    store = ChromaStore(tmp_path / "chroma")
    store.add(documents)

    hits = store.query("推荐一款保湿眼霜，预算250以内", top_k=5)

    assert store.count() == len(documents)
    assert hits
    assert any(hit["metadata"]["id"] == "p_beauty_021" for hit in hits)


def test_chroma_store_applies_metadata_filters(tmp_path: Path) -> None:
    pytest.importorskip("chromadb")
    documents = load_product_documents(ROOT_DIR / "data" / "products_ref.json")
    store = ChromaStore(tmp_path / "chroma")
    store.add(documents)

    hits = store.query(
        "推荐运动",
        top_k=10,
        filters=VectorSearchFilters(product_types=("clothes.sports_pants",)),
    )

    assert hits
    assert all("clothes.sports_pants" in hit["metadata"]["product_types"] for hit in hits)


def test_product_documents_include_normalized_product_types() -> None:
    documents = load_product_documents(ROOT_DIR / "data" / "products_ref.json")
    by_id = {document.id: document.metadata for document in documents}

    assert "clothes.sports_shoes" in by_id["p_clothes_007"]["product_types"]
    assert "clothes.sports_shoes" not in by_id["p_clothes_004"]["product_types"]
    assert "clothes.sports_pants" in by_id["p_clothes_004"]["product_types"]


def test_local_json_store_applies_metadata_prefilters() -> None:
    store = LocalJsonVectorStore(ROOT_DIR / "data" / "products_ref.json")

    hits = store.query(
        "跑鞋",
        top_k=20,
        filters=VectorSearchFilters(max_price=900, product_types=("clothes.sports_shoes",)),
    )

    assert hits
    assert all(float(hit["metadata"]["price"]) <= 900 for hit in hits)
    assert all("clothes.sports_shoes" in hit["metadata"]["product_types"] for hit in hits)


def test_local_json_store_can_be_built_from_documents() -> None:
    all_documents = load_product_documents(ROOT_DIR / "data" / "products_ref.json")
    documents = [document for document in all_documents if document.id in {"p_beauty_021", "p_beauty_016"}]
    store = LocalJsonVectorStore.from_documents(documents)

    assert store.documents == documents
    assert store.query("眼霜", top_k=3)


def test_chroma_metadata_includes_product_type_filter_flags() -> None:
    documents = load_product_documents(ROOT_DIR / "data" / "products_ref.json")
    shoe = next(document for document in documents if document.id == "p_clothes_007")

    metadata = to_chroma_metadata(shoe.metadata)

    assert metadata[product_type_filter_key("clothes.sports_shoes")] is True


def test_build_chroma_where_combines_price_and_product_type_filters() -> None:
    where = build_chroma_where(
        VectorSearchFilters(
            max_price=900,
            product_types=("clothes.sports_shoes", "clothes.sports_pants"),
        )
    )

    assert where == {
        "$and": [
            {"price": {"$lte": 900.0}},
            {
                "$or": [
                    {product_type_filter_key("clothes.sports_shoes"): True},
                    {product_type_filter_key("clothes.sports_pants"): True},
                ]
            },
        ]
    }


def test_chroma_collection_name_includes_index_schema_version() -> None:
    _, collection_name = build_chroma_embedding_function(
        use_ark_embedding=False,
        api_key="",
        base_url="https://example.test",
        model="embedding-model",
        timeout_seconds=1,
        batch_size=1,
        collection_name="products",
    )

    assert collection_name == "products_v2_metadata_filters"


def test_chroma_collection_name_is_bounded_for_embedding_models() -> None:
    _, collection_name = build_chroma_embedding_function(
        use_ark_embedding=True,
        api_key="test-key",
        base_url="https://example.test",
        model="doubao-embedding-text-240515-with-a-very-long-production-suffix",
        timeout_seconds=1,
        batch_size=1,
        collection_name="esci_small_products",
    )

    assert len(collection_name) <= 63
    assert collection_name[0].isalnum()
    assert collection_name[-1].isalnum()


def test_build_chroma_embedding_function_supports_multimodal_api() -> None:
    embedder, collection_name = build_chroma_embedding_function(
        use_ark_embedding=True,
        embedding_api="multimodal",
        api_key="test-key",
        base_url="https://example.test",
        model="ep-20260608193302-qztft",
        timeout_seconds=1,
        batch_size=1,
        collection_name="esci_small_products",
    )

    assert isinstance(embedder, ArkMultimodalEmbeddingFunction)
    assert "ark_multimodal" in collection_name
    assert len(collection_name) <= 63


def test_parse_embedding_response_orders_vectors_by_index() -> None:
    payload = {
        "data": [
            {"index": 1, "object": "embedding", "embedding": [2, 0.5]},
            {"index": 0, "object": "embedding", "embedding": [1, 0.25]},
        ]
    }

    assert parse_embedding_response(payload, expected_count=2) == [[1.0, 0.25], [2.0, 0.5]]


def test_ark_embedding_function_calls_openai_compatible_endpoint() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json_from_bytes(request.content)
        requests.append(payload)
        data = [
            {"index": index, "object": "embedding", "embedding": [float(len(text)), float(index)]}
            for index, text in enumerate(payload["input"])
        ]
        return httpx.Response(200, json={"object": "list", "data": data})

    embedder = ArkEmbeddingFunction(
        api_key="test-key",
        base_url="https://ark.example.test/api/v3",
        model="doubao-embedding-text-240515",
        batch_size=2,
        transport=httpx.MockTransport(handler),
    )

    vectors = embedder(["a", "bb", "ccc"])

    assert len(requests) == 2
    assert requests[0]["model"] == "doubao-embedding-text-240515"
    assert requests[0]["input"] == ["a", "bb"]
    assert requests[1]["input"] == ["ccc"]
    assert vectors == [[1.0, 0.0], [2.0, 1.0], [3.0, 0.0]]


def test_ark_multimodal_embedding_function_calls_endpoint() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json_from_bytes(request.content)
        requests.append(payload)
        return httpx.Response(200, json={"data": {"embedding": [1.0, 0.5]}})

    embedder = ArkMultimodalEmbeddingFunction(
        api_key="test-key",
        base_url="https://ark.example.test/api/v3",
        model="ep-test",
        batch_size=1,
        transport=httpx.MockTransport(handler),
    )

    vectors = embedder(["running shoes"])

    assert requests[0]["model"] == "ep-test"
    assert requests[0]["input"] == [{"type": "text", "text": "running shoes"}]
    assert vectors == [[1.0, 0.5]]


def test_parse_multimodal_embedding_response_reads_embedding() -> None:
    payload = {"data": {"embedding": [1, 0.25]}}

    assert parse_multimodal_embedding_response(payload) == [1.0, 0.25]


def test_ark_embedding_function_reports_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "model not found"}})

    embedder = ArkEmbeddingFunction(
        api_key="test-key",
        base_url="https://ark.example.test/api/v3",
        model="doubao-embedding-text-240515",
        batch_size=1,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="ARK embedding request failed: HTTP 404"):
        embedder(["ping"])


def json_from_bytes(content: bytes) -> dict:
    import json

    return json.loads(content.decode("utf-8"))
