"""
Purpose: 检索评分：tokenize、哈希 embedding、多维度加权打分（product_type+20, category+12, keyword+6, brand+5, 预算内+2）。
"""

import hashlib
import math
import re

from server.rag.taxonomy import product_type_display_names
from server.rag.types import DocumentFeatures, VectorDocument


def tokenize(text: str) -> set[str]:
    lowered = text.lower()
    chunks = re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", lowered)
    tokens: set[str] = set()
    for chunk in chunks:
        tokens.add(chunk)
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            tokens.update(chunk[index : index + 2] for index in range(max(len(chunk) - 1, 0)))
            tokens.update(chunk[index : index + 3] for index in range(max(len(chunk) - 2, 0)))
    return tokens


def build_document_feature_index(documents: list[VectorDocument]) -> dict[str, DocumentFeatures]:
    return {document.id: build_document_features(document) for document in documents}


def build_document_features(document: VectorDocument) -> DocumentFeatures:
    haystack = document.text.lower()
    title_text = " ".join(
        [
            str(document.metadata.get("name", "")),
            str(document.metadata.get("category", "")),
            str(document.metadata.get("sub_category", "")),
            str(document.metadata.get("brand", "")),
            " ".join(product_type_display_names(document.metadata.get("product_types", []))),
            " ".join(str(tag) for tag in document.metadata.get("tags", [])[:12]),
        ]
    ).lower()
    return DocumentFeatures(
        haystack=haystack,
        title_text=title_text,
        tokens=frozenset(tokenize(haystack)),
    )


def score_document(query_tokens: set[str], doc: VectorDocument) -> float:
    return score_document_features(query_tokens, build_document_features(doc))


def score_document_features(query_tokens: set[str], features: DocumentFeatures) -> float:
    overlap = len(query_tokens & features.tokens)
    phrase_bonus = sum(2 for token in query_tokens if len(token) > 1 and token in features.haystack)
    title_bonus = sum(6 for token in query_tokens if len(token) > 1 and token in features.title_text)
    return float(overlap + phrase_bonus + title_bonus)


def score_chroma_hit(query: str, metadata: dict, text: str, distance: float) -> float:
    lexical_score = score_document(
        tokenize(query),
        VectorDocument(id=str(metadata.get("id", "")), text=text, metadata=metadata),
    )
    vector_score = 1.0 / (1.0 + distance)
    return lexical_score + vector_score


def hashing_embedding(text: str, dimensions: int = 384) -> list[float]:
    vector = [0.0] * dimensions
    tokens = tokenize(text)
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "little") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        weight = 1.0 + min(len(token), 8) / 8.0
        vector[bucket] += sign * weight

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]
