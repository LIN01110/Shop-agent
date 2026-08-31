"""
满减推荐引擎
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.sku import SKUORM


async def recommend_for_threshold(
    cart_total: float,
    threshold: float,
    user_id: int,
    db: AsyncSession,
    limit: int = 3,
) -> list[dict]:
    """
    凑满减智能推荐
    
    购物车 ¥270，满减门槛 ¥300，差 ¥30
    推荐策略：
    1. 搜索价格在 [gap * 0.8, gap * 1.5] 区间的商品
    2. 按用户偏好排序
    3. 优先推荐高性价比商品
    
    返回：推荐商品列表
    """
    gap = threshold - cart_total
    if gap <= 0:
        return []  # 已满足门槛
    
    min_price = gap * 0.5   # 稍微放宽下限，给用户更多选择
    max_price = gap * 1.5
    
    # 查候选商品
    result = await db.execute(
        select(SKUORM)
        .where(SKUORM.status == 1)
        .where(SKUORM.is_deleted == False)
    )
    all_skus = result.scalars().all()
    
    candidates = []
    for sku in all_skus:
        # TODO: 这里需要接入 pricing_service 查实际价格
        # 暂时用 mock 价格筛选
        price = 100  # 应该从 sku_prices 查
        if min_price <= price <= max_price:
            # 简单打分：价格越接近 gap 越好
            score = 1.0 - abs(price - gap) / gap
            candidates.append({
                "sku_id": sku.sku_id,
                "product_name": sku.product_name,
                "spec_display": sku.spec_display,
                "brand": sku.brand,
                "price": price,
                "score": score,
                "saving": threshold * 0.1,  # 假设省 10%
            })
    
    # 按打分排序
    candidates.sort(key=lambda x: x["score"], reverse=True)
    
    return candidates[:limit]


async def calculate_order_total(
    items: list[dict],
    db: AsyncSession,
) -> dict:
    """
    计算订单金额明细
    
    items: [{"sku_id": "xxx", "quantity": 2}]
    
    返回：
    {
        "subtotal": 1000.0,       # 商品小计
        "discount": 100.0,        # 优惠
        "shipping": 0.0,          # 运费
        "total": 900.0            # 应付
    }
    """
    from server.services.pricing_service import get_current_price
    
    subtotal = 0.0
    for item in items:
        price_info = await get_current_price(item["sku_id"], db)
        subtotal += price_info["price"] * item["quantity"]
    
    # 简单运费规则（后期可配置）
    shipping = 0.0 if subtotal >= 99 else 10.0
    
    return {
        "subtotal": round(subtotal, 2),
        "discount": 0.0,  # 由上层传入
        "shipping": shipping,
        "total": round(subtotal + shipping, 2),
    }
