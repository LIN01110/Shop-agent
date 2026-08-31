"""
真实价格查询服务
替换原有的 mock 价格服务
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.database.redis_client import cache_get, cache_set
from server.models.sku import SKUORM, SKUPriceORM


async def get_current_price(sku_id: str, db: AsyncSession) -> dict:
    """
    获取 SKU 当前生效价格
    
    1. 先查 Redis 缓存
    2. 缓存未命中查 MySQL
    3. 写入缓存（5 分钟 TTL）
    
    返回：
        {
            "price": 720.0,
            "original_price": 760.0,
            "promotion_tag": "618大促",
            "currency": "CNY"
        }
    """
    cache_key = f"price:{sku_id}"
    
    # 1. 查缓存
    cached = await cache_get(cache_key)
    if cached:
        import json
        return json.loads(cached)
    
    # 2. 查数据库
    result = await db.execute(select(SKUORM).where(SKUORM.sku_id == sku_id))
    sku = result.scalar_one_or_none()
    if not sku:
        return {"price": 0, "original_price": None, "promotion_tag": None}
    
    result = await db.execute(
        select(SKUPriceORM)
        .where(SKUPriceORM.sku_id == sku.id, SKUPriceORM.is_active == True)
        .order_by(SKUPriceORM.effective_from.desc())
        .limit(1)
    )
    price_row = result.scalar_one_or_none()
    
    if not price_row:
        return {"price": 0, "original_price": None, "promotion_tag": None}
    
    data = {
        "price": float(price_row.price),
        "original_price": float(price_row.original_price) if price_row.original_price else None,
        "promotion_tag": price_row.promotion_tag,
        "currency": price_row.currency,
    }
    
    # 3. 写缓存
    import json
    await cache_set(cache_key, json.dumps(data), ttl=300)  # 5 分钟
    
    return data


async def set_price(
    sku_id: str,
    price: float,
    original_price: float | None = None,
    promotion_tag: str | None = None,
    db: AsyncSession = None,
) -> str:
    """
    设置 SKU 价格（新增价格记录）
    
    返回新创建的 price_id
    """
    result = await db.execute(select(SKUORM).where(SKUORM.sku_id == sku_id))
    sku = result.scalar_one_or_none()
    if not sku:
        raise ValueError(f"SKU 不存在: {sku_id}")
    
    # 旧价格失效
    from datetime import datetime
    now = datetime.utcnow()
    
    result = await db.execute(
        select(SKUPriceORM)
        .where(SKUPriceORM.sku_id == sku.id, SKUPriceORM.is_active == True)
    )
    for old_price in result.scalars().all():
        old_price.is_active = False
        old_price.effective_to = now
    
    # 创建新价格
    price_id = f"{sku_id}_{now.strftime('%Y%m%d%H%M%S')}"
    new_price = SKUPriceORM(
        price_id=price_id,
        sku_id=sku.id,
        price=price,
        original_price=original_price,
        promotion_tag=promotion_tag,
        effective_from=now,
        is_active=True,
    )
    db.add(new_price)
    await db.commit()
    
    # 清缓存
    await cache_delete(f"price:{sku_id}")
    
    return price_id


async def cache_delete(key: str):
    """删除缓存"""
    from server.database.redis_client import cache_delete as _delete
    await _delete(key)
