"""
Purpose: Embedding 实现：HashingEmbeddingFunction（本地哈希）和 ArkEmbeddingFunction（Ark/Doubao API）。
"""

import base64
from typing import Any

import httpx

from server.rag.identifiers import bounded_chroma_collection_name, safe_identifier
from server.rag.scoring import hashing_embedding


INDEX_SCHEMA_VERSION = "v2_metadata_filters"


class HashingEmbeddingFunction:
    """Small local embedding hook for Chroma when external embeddings are disabled."""

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def __call__(self, input: list[str]) -> list[list[float]]:
        return [hashing_embedding(text, self.dimensions) for text in input]

    @staticmethod
    def name() -> str:
        return "local_hashing_embedding"


class ArkEmbeddingFunction:
    """Chroma embedding hook backed by Ark's OpenAI-compatible embeddings API."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        batch_size: int = 64,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if batch_size <= 0 or batch_size > 256:
            raise ValueError("ARK embedding batch_size must be between 1 and 256.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.batch_size = batch_size
        self.transport = transport

    def __call__(self, input: list[str]) -> list[list[float]]:
        if not input:
            return []
        if not self.api_key:
            raise RuntimeError("ARK_API_KEY is not configured for embedding.")

        embeddings: list[list[float]] = []
        timeout = httpx.Timeout(self.timeout_seconds)
        with httpx.Client(timeout=timeout, transport=self.transport) as client:
            for start in range(0, len(input), self.batch_size):
                batch = input[start : start + self.batch_size]
                embeddings.extend(self._embed_batch(client, batch))
        return embeddings

    def _embed_batch(self, client: httpx.Client, batch: list[str]) -> list[list[float]]:
        url = f"{self.base_url}/embeddings"
        payload = {
            "model": self.model,
            "input": [text if text.strip() else "<empty>" for text in batch],
            "encoding_format": "float",
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response = client.post(url, headers=headers, json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = response.text[:500].replace("\n", " ")
            raise RuntimeError(
                "ARK embedding request failed: "
                f"HTTP {response.status_code} for {url}; model={self.model}; body={body}"
            ) from exc
        return parse_embedding_response(response.json(), expected_count=len(batch))

    def name(self) -> str:
        return f"ark_embedding_{safe_identifier(self.model)}"


class ArkMultimodalEmbeddingFunction:
    """Ark multimodal embedding hook for Doubao vision/text-image embedding endpoints."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        batch_size: int = 1,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if batch_size <= 0 or batch_size > 32:
            raise ValueError("ARK multimodal embedding batch_size must be between 1 and 32.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.batch_size = batch_size
        self.transport = transport

    def __call__(self, input: list[str]) -> list[list[float]]:
        if not input:
            return []
        if not self.api_key:
            raise RuntimeError("ARK_API_KEY is not configured for embedding.")

        embeddings: list[list[float]] = []
        timeout = httpx.Timeout(self.timeout_seconds)
        with httpx.Client(timeout=timeout, transport=self.transport) as client:
            for text in input:
                embeddings.append(self._embed_text(client, text))
        return embeddings

    def embed_text(self, text: str) -> list[float]:
        if not self.api_key:
            raise RuntimeError("ARK_API_KEY is not configured for embedding.")
        timeout = httpx.Timeout(self.timeout_seconds)
        with httpx.Client(timeout=timeout, transport=self.transport) as client:
            return self._embed_text(client, text)

    def embed_image_bytes(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> list[float]:
        if not self.api_key:
            raise RuntimeError("ARK_API_KEY is not configured for embedding.")
        data_url = image_data_url(image_bytes, mime_type)
        timeout = httpx.Timeout(self.timeout_seconds)
        with httpx.Client(timeout=timeout, transport=self.transport) as client:
            return self._embed_input(
                client,
                [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_url,
                        },
                    }
                ],
            )

    def _embed_text(self, client: httpx.Client, text: str) -> list[float]:
        return self._embed_input(
            client,
            [
                {
                    "type": "text",
                    "text": text if text.strip() else "<empty>",
                }
            ],
        )

    def _embed_input(self, client: httpx.Client, input_items: list[dict[str, Any]]) -> list[float]:
        url = f"{self.base_url}/embeddings/multimodal"
        payload = {
            "model": self.model,
            "encoding_format": "float",
            "input": input_items,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response = client.post(url, headers=headers, json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = response.text[:500].replace("\n", " ")
            raise RuntimeError(
                "ARK multimodal embedding request failed: "
                f"HTTP {response.status_code} for {url}; model={self.model}; body={body}"
            ) from exc
        return parse_multimodal_embedding_response(response.json())

    def name(self) -> str:
        return f"ark_multimodal_embedding_{safe_identifier(self.model)}"


def image_data_url(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    safe_mime = mime_type if mime_type.startswith("image/") else "image/jpeg"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{safe_mime};base64,{encoded}"


def parse_multimodal_embedding_response(payload: dict[str, Any]) -> list[float]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("ARK multimodal embedding response is missing a data object.")

    embedding = data.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        raise RuntimeError("ARK multimodal embedding response contains an invalid embedding.")
    return [float(value) for value in embedding]


def parse_embedding_response(payload: dict[str, Any], expected_count: int) -> list[list[float]]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("ARK embedding response is missing a data list.")

    by_index: dict[int, list[float]] = {}
    for fallback_index, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        index = int(item.get("index", fallback_index))
        embedding = item.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise RuntimeError("ARK embedding response contains an invalid embedding.")
        by_index[index] = [float(value) for value in embedding]

    missing = [index for index in range(expected_count) if index not in by_index]
    if missing:
        raise RuntimeError(f"ARK embedding response missing indexes: {missing}")

    return [by_index[index] for index in range(expected_count)]


def build_chroma_embedding_function(
    *,
    use_ark_embedding: bool,
    embedding_api: str = "text",
    api_key: str,
    base_url: str,
    model: str,
    timeout_seconds: float,
    batch_size: int,
    collection_name: str,
) -> tuple[Any, str]:
    collection_name = f"{collection_name}_{INDEX_SCHEMA_VERSION}"
    if use_ark_embedding and api_key:
        normalized_api = embedding_api.strip().lower()
        if normalized_api == "multimodal":
            embedder = ArkMultimodalEmbeddingFunction(
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout_seconds=timeout_seconds,
                batch_size=batch_size,
            )
        elif normalized_api == "text":
            embedder = ArkEmbeddingFunction(
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout_seconds=timeout_seconds,
                batch_size=batch_size,
            )
        else:
            raise ValueError("ARK embedding_api must be 'text' or 'multimodal'.")
        return embedder, bounded_chroma_collection_name(f"{collection_name}_{safe_identifier(embedder.name())}")
    return HashingEmbeddingFunction(), bounded_chroma_collection_name(collection_name)
