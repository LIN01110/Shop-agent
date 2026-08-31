"""
Purpose: 会话制购物车 API（匿名，免登录）——以 session_id 直接对会话购物车
         做增/减/查，无需经过 /chat 对话轮次。

与 server/api/cart_db.py 的区别：cart_db 是「用户系统」（JWT + MySQL 持久化，
enable_user_system=true 时注册）；本模块是「会话购物车」，基于 SessionStore，
与 /chat 流程中的购物车状态完全同源——对话里加购的商品通过本 API 也能读到，
反之亦然。

接口:
- POST /cart/items  {session_id, product_id, quantity_delta}  增/减商品数量
- GET  /cart?session_id=...                                   读取购物车

行为规格（server/tests/test_cart_api.py、test_sessions_api.py）:
- 同一商品重复加购数量累加；quantity_delta 为负时递减，减到 0 及以下移除该商品；
- 加购成功后将商品品牌写入 user_profile.preferred_brands（供后续推荐个性化）；
- 返回 build_cart_payload 结构（items/total_quantity/total_price/is_empty）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from server.agent.orchestrator import Orchestrator
from server.api.products import get_product_by_id
from server.app_container import get_orchestrator
from server.tools.cart import build_cart_payload

router = APIRouter(prefix="/cart", tags=["会话购物车"])


class CartItemDelta(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    product_id: str = Field(min_length=1, max_length=128)
    quantity_delta: int = 1


@router.post("/items")
async def adjust_cart_item(
    body: CartItemDelta,
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    sessions = orchestrator.sessions
    session = sessions.get(body.session_id)

    item = next(
        (entry for entry in session.cart if entry.get("product_id") == body.product_id),
        None,
    )

    if item is None:
        if body.quantity_delta <= 0:
            raise HTTPException(status_code=404, detail="购物车中不存在该商品")
        product = get_product_by_id(body.product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="商品不存在")
        session.cart.append(
            {
                "product_id": product["id"],
                "name": product.get("name", ""),
                "brand": product.get("brand", ""),
                "category": product.get("category", ""),
                "price": product.get("price", 0),
                "quantity": int(body.quantity_delta),
                "image_url": product.get("image_url", ""),
                "detail_url": product.get("detail_url", ""),
            }
        )
        brand = product.get("brand")
        if brand and brand not in session.user_profile.preferred_brands:
            session.user_profile.preferred_brands.append(brand)
    else:
        item["quantity"] = int(item.get("quantity", 0)) + body.quantity_delta
        if item["quantity"] <= 0:
            session.cart.remove(item)

    sessions.save(session)
    return build_cart_payload(session.cart)


@router.get("")
async def read_cart(
    session_id: str = Query(min_length=1, max_length=128),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    session = orchestrator.sessions.get(session_id)
    return build_cart_payload(session.cart)
