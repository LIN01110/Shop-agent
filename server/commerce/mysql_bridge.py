"""
MySQL → CommerceGateway 桥接

启动时从 MySQL 加载 SKU 价格/库存到内存缓存，
供 Agent（同步接口）查询真实数据。

注：此为过渡方案。高频场景下建议：
1. 后台定时任务刷新缓存（见 scripts/warmup.py）
2. 或长期将 CommerceGateway 接口异步化
"""

from __future__ import annotations

import logging
from typing import Any

from server.commerce.models import BusinessFact
from server.commerce.services import (
    CommerceDataGateway,
    CommerceServices,
    InventoryService,
    PricingService,
    PromotionService,
)

logger = logging.getLogger(__name__)

# 全局内存缓存（启动时加载）
_price_cache: dict[str, dict[str, Any]] = {}
_inventory_cache: dict[str, dict[str, Any]] = {}


async def refresh_commerce_cache() -> None:
    """从 MySQL 刷新价格/库存缓存。在 lifespan 或定时任务中调用。"""
    global _price_cache, _inventory_cache

    try:
        from server.database.mysql_client import get_engine
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy import select
        from server.models.sku import SKUORM, SKUPriceORM, SKUInventoryORM

        engine = get_engine()
        async with AsyncSession(engine) as session:
            # 加载所有 SKU
            result = await session.execute(select(SKUORM))
            skus = result.scalars().all()

            new_price_cache: dict[str, dict[str, Any]] = {}
            new_inventory_cache: dict[str, dict[str, Any]] = {}

            for sku in skus:
                sku_code = sku.sku_code

                # 最新价格
                price_result = await session.execute(
                    select(SKUPriceORM)
                    .where(SKUPriceORM.sku_id == sku.id, SKUPriceORM.is_active == True)
                    .order_by(SKUPriceORM.effective_from.desc())
                    .limit(1)
                )
                price_row = price_result.scalar_one_or_none()
                if price_row:
                    new_price_cache[sku_code] = {
                        "price": float(price_row.price),
                        "effective_from": price_row.effective_from,
                    }

                # 库存
                inv_result = await session.execute(
                    select(SKUInventoryORM).where(SKUInventoryORM.sku_id == sku.id)
                )
                inv_row = inv_result.scalar_one_or_none()
                if inv_row:
                    new_inventory_cache[sku_code] = {
                        "available_qty": inv_row.available_qty,
                        "reserved_qty": inv_row.reserved_qty,
                        "sold_qty": inv_row.sold_qty,
                    }

            _price_cache = new_price_cache
            _inventory_cache = new_inventory_cache

        logger.info(
            "Commerce cache refreshed: %d prices, %d inventories",
            len(_price_cache),
            len(_inventory_cache),
        )

    except Exception as exc:
        logger.warning("Failed to refresh commerce cache: %s", exc)


def get_cached_price(sku_code: str) -> float | None:
    entry = _price_cache.get(sku_code)
    return entry["price"] if entry else None


def get_cached_inventory(sku_code: str) -> dict[str, int] | None:
    return _inventory_cache.get(sku_code)


class CachedPricingService:
    """基于内存缓存的定价服务（同步接口，供 Agent 使用）。"""

    def __init__(self, fallback: PricingService | None = None) -> None:
        self.fallback = fallback

    def price(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        sku_code = sku_id or product.get("sku_id") or product.get("id", "")
        cached = get_cached_price(str(sku_code))

        if cached is not None:
            return BusinessFact(
                field="price",
                value=cached,
                source="mysql_pricing_cache",
                source_field="current_price",
                freshness="cached",
                authoritative=True,
            )

        if self.fallback:
            return self.fallback.price(product, sku_id)

        return BusinessFact(
            field="price",
            value=float(product.get("price", 0)),
            source="product_catalog",
            source_field="price",
            freshness="catalog_snapshot",
            authoritative=False,
        )


class CachedInventoryService:
    """基于内存缓存的库存服务（同步接口，供 Agent 使用）。"""

    def __init__(self, fallback: InventoryService | None = None) -> None:
        self.fallback = fallback

    def stock(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        sku_code = sku_id or product.get("sku_id") or product.get("id", "")
        cached = get_cached_inventory(str(sku_code))

        if cached is not None:
            return BusinessFact(
                field="stock",
                value=cached["available_qty"],
                source="mysql_inventory_cache",
                source_field="available_qty",
                freshness="cached",
                authoritative=True,
            )

        if self.fallback:
            return self.fallback.stock(product, sku_id)

        catalog_stock = product.get("stock")
        if catalog_stock is not None:
            return BusinessFact(
                field="stock",
                value=int(catalog_stock),
                source="product_catalog",
                source_field="stock",
                freshness="catalog_snapshot",
                authoritative=False,
            )

        return BusinessFact(
            field="stock",
            missing_reason="No inventory data available",
        )


def create_mysql_backed_commerce_gateway(
    mock_overrides_path: str | None = None,
) -> CommerceDataGateway:
    """创建以 MySQL 为真实数据来源、mock 为回退的 CommerceGateway。"""
    from server.commerce.services import (
        CommerceServices,
        LocalCatalogService,
        LocalMockLogisticsService,
        LocalMockPolicyService,
        LocalMockPromotionService,
        CommerceOverrideStore,
    )
    import time

    overrides = CommerceOverrideStore(mock_overrides_path)
    clock = time.time

    # mock 服务作为回退
    from server.commerce.services import LocalMockPricingService, LocalMockInventoryService
    mock_pricing = LocalMockPricingService(overrides, clock)
    mock_inventory = LocalMockInventoryService(overrides, clock)

    return CommerceDataGateway(
        CommerceServices(
            catalog=LocalCatalogService(),
            pricing=CachedPricingService(fallback=mock_pricing),
            inventory=CachedInventoryService(fallback=mock_inventory),
            promotion=LocalMockPromotionService(overrides, clock),
            policy=LocalMockPolicyService(overrides, clock),
            logistics=LocalMockLogisticsService(overrides, clock),
        )
    )
