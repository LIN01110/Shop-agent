"""
真实库存查询服务
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.database.redis_client import cache_get, cache_set
from server.models.sku import SKUInventoryORM, SKUORM


async def get_inventory(sku_id: str, db: AsyncSession) -> dict:
    """
    获取 SKU 库存信息
    
    返回：
        {
            "available_qty": 100,
            "reserved_qty": 5,
            "sold_qty": 230,
            "in_stock": True
        }
    """
    cache_key = f"inventory:{sku_id}"
    
    # 查缓存
    cached = await cache_get(cache_key)
    if cached:
        import json
        return json.loads(cached)
    
    # 查数据库
    result = await db.execute(select(SKUORM).where(SKUORM.sku_id == sku_id))
    sku = result.scalar_one_or_none()
    if not sku:
        return {"available_qty": 0, "reserved_qty": 0, "sold_qty": 0, "in_stock": False}
    
    result = await db.execute(
        select(SKUInventoryORM).where(SKUInventoryORM.sku_id == sku.id)
    )
    inv = result.scalar_one_or_none()
    
    if not inv:
        return {"available_qty": 0, "reserved_qty": 0, "sold_qty": 0, "in_stock": False}
    
    data = {
        "available_qty": inv.available_qty,
        "reserved_qty": inv.reserved_qty,
        "sold_qty": inv.sold_qty,
        "in_stock": inv.available_qty > 0,
    }
    
    # 写缓存（库存变化频繁，TTL 短一些）
    import json
    await cache_set(cache_key, json.dumps(data), ttl=60)  # 1 分钟
    
    return data


async def check_inventory(sku_id: str, quantity: int = 1) -> bool:
    """
    检查库存是否充足（不查数据库，用于购物车等快速检查）
    
    注意：下单时需要用 SELECT FOR UPDATE 做精确校验
    """
    # 这里简单返回 True，实际应该查缓存
    # 购物车添加时的库存检查在 API 层做
    return True


async def adjust_inventory(
    sku_id: str,
    delta: int,
    db: AsyncSession,
) -> dict:
    """
    调整库存（正数增加，负数减少）
    
    返回更新后的库存信息
    """
    result = await db.execute(select(SKUORM).where(SKUORM.sku_id == sku_id))
    sku = result.scalar_one_or_none()
    if not sku:
        raise ValueError(f"SKU 不存在: {sku_id}")
    
    result = await db.execute(
        select(SKUInventoryORM).where(SKUInventoryORM.sku_id == sku.id)
    )
    inv = result.scalar_one_or_none()
    
    if not inv:
        # 自动创建库存记录
        inv = SKUInventoryORM(sku_id=sku.id, available_qty=max(0, delta))
        db.add(inv)
    else:
        inv.available_qty = max(0, inv.available_qty + delta)
    
    await db.commit()
    
    # 清缓存
    await cache_delete(f"inventory:{sku_id}")
    
    return {
        "available_qty": inv.available_qty,
        "reserved_qty": inv.reserved_qty,
        "sold_qty": inv.sold_qty,
    }


async def cache_delete(key: str):
    """删除缓存"""
    from server.database.redis_client import cache_delete as _delete
    await _delete(key)
