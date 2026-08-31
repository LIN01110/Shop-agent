"""
Purpose: 商品详情 API：/products/{id} 返回商品详情页 HTML，/assets/products/ 提供静态图片资源。
"""

import html
import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from server.commerce.facts import get_fact_provider
from server.config import get_settings
from server.tools.product_urls import build_product_image_url


router = APIRouter(tags=["products"])


@router.get("/products/{product_id}", response_class=HTMLResponse)
async def product_detail(product_id: str) -> HTMLResponse:
    product = get_product_by_id(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    settings = get_settings()
    image_url = build_product_image_url(product.get("image_url", ""), settings.public_base_url)
    body = render_product_detail(product, image_url)
    return HTMLResponse(content=body)


def get_product_by_id(product_id: str) -> dict | None:
    return load_product_index().get(product_id)


@lru_cache
def load_product_index() -> dict[str, dict]:
    settings = get_settings()
    path: Path = settings.product_data_file
    products = json.loads(path.read_text(encoding="utf-8"))
    return {item["id"]: item for item in products}


def render_product_detail(product: dict, image_url: str) -> str:
    facts = get_fact_provider()
    price_fact = facts.price(product)
    stock_fact = facts.stock(product)
    invoice_fact = facts.invoice_policy(product)
    logistics_fact = facts.logistics_policy(product)
    after_sales_fact = facts.after_sales_policy(product)
    name = escape(str(product.get("name", "")))
    brand = escape(str(product.get("brand", "")))
    category = escape(str(product.get("category", "")))
    sub_category = escape(str(product.get("sub_category", "")))
    price = format_price(price_fact.value if price_fact.available else product.get("price", ""))
    stock = escape(str(stock_fact.value if stock_fact.available else product.get("stock", "")))
    description = escape(str(product.get("description", "")))
    tags = product.get("tags", [])
    attributes = product.get("attributes", {})
    sku_options = attributes.get("sku_options", {}) if isinstance(attributes, dict) else {}
    product_types = product.get("product_type_names", []) or product.get("product_types", [])
    invoice_text = escape(format_invoice_policy(invoice_fact.value if invoice_fact.available else {}))
    logistics_text = escape(format_logistics_policy(logistics_fact.value if logistics_fact.available else {}))
    after_sales_text = escape(format_after_sales_policy(after_sales_fact.value if after_sales_fact.available else {}))

    tag_items = "".join(f"<span>{escape(str(tag))}</span>" for tag in tags[:12]) or "<em>暂无标签</em>"
    type_items = "".join(f"<span>{escape(str(item))}</span>" for item in product_types[:6]) or "<em>暂无细分类目</em>"
    sku_items = "".join(
        f"<li><b>{escape(str(key))}</b><span>{escape(format_sku_values(values))}</span></li>"
        for key, values in sku_options.items()
    )
    sku_section = f"<ul class=\"spec-list\">{sku_items}</ul>" if sku_items else "<p class=\"muted\">暂无规格信息</p>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{name}</title>
  <style>
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f4f6f8;
      color: #182026;
    }}
    main {{
      max-width: 1080px;
      margin: 0 auto;
      padding: 20px;
    }}
    .hero {{
      background: white;
      border-radius: 8px;
      border: 1px solid #e3e6ea;
      display: grid;
      grid-template-columns: minmax(280px, 42%) 1fr;
      overflow: hidden;
    }}
    .media {{
      background: #eef1f4;
      display: grid;
      place-items: center;
      min-height: 360px;
      padding: 18px;
    }}
    .media img {{
      width: 100%;
      max-height: 520px;
      object-fit: contain;
      display: block;
      border-radius: 8px;
      background: white;
    }}
    .content {{
      padding: 22px;
      display: grid;
      align-content: start;
      gap: 14px;
    }}
    h1 {{
      font-size: 24px;
      line-height: 1.35;
      margin: 0;
    }}
    .price {{
      color: #d23f31;
      font-size: 30px;
      font-weight: 700;
    }}
    .meta {{
      font-size: 14px;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}
    .meta div {{
      border: 1px solid #e3e6ea;
      border-radius: 8px;
      padding: 10px;
      background: #fafbfc;
    }}
    .meta b {{
      display: block;
      color: #687480;
      font-size: 12px;
      font-weight: 500;
      margin-bottom: 4px;
    }}
    section {{
      background: white;
      border: 1px solid #e3e6ea;
      border-radius: 8px;
      margin-top: 14px;
      padding: 18px;
    }}
    h2 {{
      font-size: 17px;
      margin: 0 0 12px;
    }}
    p, li {{
      font-size: 14px;
      line-height: 1.65;
    }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .chips span {{
      background: #eef3ff;
      color: #254a8f;
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
    }}
    .spec-list {{
      margin: 0;
      padding: 0;
      display: grid;
      gap: 10px;
      list-style: none;
    }}
    .spec-list li {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid #edf0f2;
      padding-bottom: 10px;
    }}
    .spec-list li:last-child {{
      border-bottom: 0;
      padding-bottom: 0;
    }}
    .spec-list span {{
      color: #3f4b55;
      text-align: right;
    }}
    .description {{
      white-space: pre-wrap;
      margin: 0;
    }}
    .muted, em {{
      color: #687480;
      font-style: normal;
    }}
    @media (max-width: 760px) {{
      main {{
        padding: 12px;
      }}
      .hero {{
        grid-template-columns: 1fr;
      }}
      .media {{
        min-height: 260px;
      }}
      .content {{
        padding: 16px;
      }}
      h1 {{
        font-size: 20px;
      }}
      .meta {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <article class="hero">
      <div class="media">
        <img src="{escape(image_url)}" alt="{name}" />
      </div>
      <div class="content">
        <h1>{name}</h1>
        <div class="price">¥{escape(price)}</div>
        <div class="meta">
          <div><b>品牌</b>{brand}</div>
          <div><b>类目</b>{category} / {sub_category}</div>
          <div><b>库存</b>{stock}</div>
          <div><b>数据来源</b>mock 业务服务</div>
        </div>
      </div>
    </article>
    <section>
      <h2>细分类目</h2>
      <div class="chips">{type_items}</div>
    </section>
    <section>
      <h2>商品标签</h2>
      <div class="chips">{tag_items}</div>
    </section>
    <section>
      <h2>规格信息</h2>
      {sku_section}
    </section>
    <section>
      <h2>交易与服务信息</h2>
      <ul class="spec-list">
        <li><b>开票</b><span>{invoice_text}</span></li>
        <li><b>物流</b><span>{logistics_text}</span></li>
        <li><b>售后</b><span>{after_sales_text}</span></li>
      </ul>
      <p class="muted">当前为本地 mock 业务服务结果，生产环境应替换为实时 pricing/inventory/policy 服务。</p>
    </section>
    <section>
      <h2>商品说明</h2>
      <p class="description">{description}</p>
    </section>
  </main>
</body>
</html>"""


def escape(value: str) -> str:
    return html.escape(value, quote=True)


def format_price(value) -> str:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return str(value)
    if price.is_integer():
        return str(int(price))
    return f"{price:.2f}".rstrip("0").rstrip(".")


def format_sku_values(values) -> str:
    if isinstance(values, (list, tuple)):
        return " / ".join(str(value) for value in values)
    return str(values)


def format_invoice_policy(value: dict) -> str:
    if not value:
        return "暂无开票信息"
    if value.get("supports_enterprise_invoice"):
        return "支持企业开票：" + " / ".join(str(item) for item in value.get("types", []))
    if value.get("supports_invoice"):
        return "支持普通发票"
    return "不支持开票"


def format_logistics_policy(value: dict) -> str:
    if not value:
        return "暂无物流承诺"
    return str(value.get("promise") or "暂无物流承诺")


def format_after_sales_policy(value: dict) -> str:
    if not value:
        return "暂无售后政策"
    pieces = [
        str(value.get("warranty") or ""),
        str(value.get("opened_return_policy") or ""),
    ]
    return "；".join(piece for piece in pieces if piece) or "暂无售后政策"
