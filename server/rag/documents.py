"""
Purpose: 商品文档加载：将 JSON 商品数据转换为 VectorDocument（文本 + metadata）。
"""

import json
from pathlib import Path

from server.rag.category_taxonomy import category_display_names, infer_category_ids
from server.rag.taxonomy import infer_product_type_ids, product_type_display_names
from server.rag.types import VectorDocument


def load_product_documents(data_path: Path) -> list[VectorDocument]:
    with data_path.open("r", encoding="utf-8") as file:
        products = json.load(file)

    documents: list[VectorDocument] = []
    for item in products:
        tags = " ".join(str(tag) for tag in item.get("tags", []))
        attributes = json.dumps(item.get("attributes", {}), ensure_ascii=False)
        category_ids = infer_category_ids(item)
        category_names = category_display_names(category_ids)
        product_types = infer_product_type_ids(item)
        product_type_names = product_type_display_names(product_types)
        text = (
            f"{item['name']} {item['category']} {item.get('sub_category', '')} "
            f"{item['brand']} {' '.join(category_names)} {' '.join(product_type_names)} "
            f"{tags} {attributes} {item.get('description', '')}"
        )
        metadata = {
            "id": item["id"],
            "name": item["name"],
            "category": item["category"],
            "category_ids": category_ids,
            "sub_category": item.get("sub_category", ""),
            "brand": item["brand"],
            "product_types": product_types,
            "price": item["price"],
            "stock": item.get("stock", 0),
            "image_url": item.get("image_url", ""),
            "detail_url": item.get("detail_url", ""),
            "tags": item.get("tags", []),
            "attributes": item.get("attributes", {}),
            "description": item.get("description", ""),
        }
        documents.append(VectorDocument(id=item["id"], text=text, metadata=metadata))
    return documents
