"""
Purpose: 商品 URL 生成：生成商品详情页和主图 URL。
"""

from pathlib import PurePosixPath
from urllib.parse import quote, urlparse


def build_product_image_url(raw_image_url: str, public_base_url: str = "") -> str:
    filename = PurePosixPath(str(raw_image_url).replace("\\", "/")).name
    if not filename:
        return ""

    path = f"/assets/products/{quote(filename)}"
    if not public_base_url:
        return path
    return f"{public_base_url.rstrip('/')}{path}"


def build_product_detail_url(product_id: str, raw_detail_url: str = "", public_base_url: str = "") -> str:
    raw_detail_url = str(raw_detail_url).strip()
    if raw_detail_url and urlparse(raw_detail_url).scheme in {"http", "https"}:
        return raw_detail_url

    path = f"/products/{quote(str(product_id))}"
    if not public_base_url:
        return path
    return f"{public_base_url.rstrip('/')}{path}"
