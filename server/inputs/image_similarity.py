"""
Purpose: 图片相似度：基于视觉签名（轻量 12x12 RGB）做相似匹配，推断商品类目。
"""

import base64
import binascii
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from server.rag.taxonomy import infer_product_type_ids, product_type_display_names
from server.rag.vector_store import load_product_documents


class ProductImageSimilarityIndex:
    def __init__(self, product_data_path: Path, product_image_dir: Path) -> None:
        self.entries = build_image_index(product_data_path, product_image_dir)

    def match_base64_image(self, image_base64: str, top_k: int = 3) -> list[dict]:
        image_bytes = decode_base64_image(image_base64)
        if not image_bytes:
            return []
        return self.match_image_bytes(image_bytes, top_k=top_k)

    def match_image_bytes(self, image_bytes: bytes, top_k: int = 3) -> list[dict]:
        try:
            query_signature = image_signature(image_bytes)
        except (OSError, UnidentifiedImageError):
            return []

        scored = []
        for entry in self.entries:
            distance = signature_distance(query_signature, entry["signature"])
            similarity = max(0.0, 1.0 - distance)
            scored.append({**entry["product"], "similarity": similarity})
        scored.sort(key=lambda item: item["similarity"], reverse=True)
        return scored[:top_k]


def build_image_index(product_data_path: Path, product_image_dir: Path) -> list[dict]:
    entries: list[dict] = []
    for document in load_product_documents(product_data_path):
        image_name = Path(str(document.metadata.get("image_url", "")).replace("\\", "/")).name
        image_path = product_image_dir / image_name
        if not image_name or not image_path.exists():
            continue
        try:
            signature = image_signature(image_path.read_bytes())
        except (OSError, UnidentifiedImageError):
            continue
        product_types = infer_product_type_ids(document.metadata)
        entries.append(
            {
                "signature": signature,
                "product": {
                    "id": document.id,
                    "name": document.metadata.get("name", ""),
                    "brand": document.metadata.get("brand", ""),
                    "category": document.metadata.get("category", ""),
                    "product_types": product_types,
                    "product_type_names": product_type_display_names(product_types),
                },
            }
        )
    return entries


def decode_base64_image(image_base64: str) -> bytes:
    payload = image_base64.strip()
    if "," in payload and payload.lower().startswith("data:"):
        payload = payload.split(",", 1)[1]
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return b""


def image_signature(image_bytes: bytes) -> tuple[float, ...]:
    with Image.open(BytesIO(image_bytes)) as image:
        resized = image.convert("RGB").resize((12, 12))
        values: list[float] = []
        for red, green, blue in resized.getdata():
            values.extend([red / 255.0, green / 255.0, blue / 255.0])
        return tuple(values)


def signature_distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        return 1.0
    squared = sum((a - b) ** 2 for a, b in zip(left, right))
    return min(1.0, (squared / len(left)) ** 0.5)
