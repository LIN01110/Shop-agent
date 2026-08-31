"""
持久化购物车 API
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.jwt_handler import get_current_user
from server.database.mysql_client import get_db
from server.models.cart import CartItemORM
from server.models.sku import SKUORM
from server.services.inventory_service import check_inventory

router = APIRouter(prefix="/cart", tags=["购物车"])


@router.get("/")
async def get_cart(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取购物车列表"""
    from server.models.user import UserORM
    result = await db.execute(select(UserORM).where(UserORM.user_id == user.get("sub")))
    user_orm = result.scalar_one_or_none()
    if not user_orm:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    result = await db.execute(
        select(CartItemORM, SKUORM)
        .join(SKUORM, CartItemORM.sku_id == SKUORM.id)
        .where(CartItemORM.user_id == user_orm.id)
    )
    items = []
    for cart_item, sku in result.all():
        items.append({
            "sku_id": sku.sku_id,
            "product_name": sku.product_name,
            "spec_display": sku.spec_display,
            "price": None,  # 需查当前价格
            "quantity": cart_item.quantity,
            "selected": cart_item.selected,
            "invalid_reason": cart_item.invalid_reason,
            "added_at": cart_item.added_at,
        })
    
    return {"items": items, "total_count": len(items)}


@router.post("/add")
async def add_to_cart(
    sku_id: str,
    quantity: int = 1,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """添加商品到购物车"""
    from server.models.user import UserORM
    result = await db.execute(select(UserORM).where(UserORM.user_id == user.get("sub")))
    user_orm = result.scalar_one_or_none()
    if not user_orm:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 查 SKU
    result = await db.execute(select(SKUORM).where(SKUORM.sku_id == sku_id))
    sku = result.scalar_one_or_none()
    if not sku:
        raise HTTPException(status_code=404, detail="商品不存在")
    
    # 检查库存
    has_stock = await check_inventory(sku_id, quantity)
    if not has_stock:
        raise HTTPException(status_code=400, detail="库存不足")
    
    # 查是否已存在
    result = await db.execute(
        select(CartItemORM).where(
            CartItemORM.user_id == user_orm.id,
            CartItemORM.sku_id == sku.id,
        )
    )
    existing = result.scalar_one_or_none()
    
    from datetime import datetime
    if existing:
        existing.quantity += quantity
        existing.updated_at = datetime.utcnow()
    else:
        new_item = CartItemORM(
            user_id=user_orm.id,
            sku_id=sku.id,
            quantity=quantity,
            added_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(new_item)
    
    await db.commit()
    return {"message": "已添加到购物车"}


@router.post("/update")
async def update_cart_item(
    sku_id: str,
    quantity: int,
    selected: bool | None = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新购物车商品数量/选中状态"""
    from server.models.user import UserORM
    result = await db.execute(select(UserORM).where(UserORM.user_id == user.get("sub")))
    user_orm = result.scalar_one_or_none()
    if not user_orm:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    result = await db.execute(select(SKUORM).where(SKUORM.sku_id == sku_id))
    sku = result.scalar_one_or_none()
    if not sku:
        raise HTTPException(status_code=404, detail="商品不存在")
    
    result = await db.execute(
        select(CartItemORM).where(
            CartItemORM.user_id == user_orm.id,
            CartItemORM.sku_id == sku.id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="购物车中无此商品")
    
    if quantity <= 0:
        await db.delete(item)
    else:
        item.quantity = quantity
        if selected is not None:
            item.selected = selected
        from datetime import datetime
        item.updated_at = datetime.utcnow()
    
    await db.commit()
    return {"message": "已更新"}


@router.post("/remove")
async def remove_from_cart(
    sku_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """从购物车移除商品"""
    from server.models.user import UserORM
    result = await db.execute(select(UserORM).where(UserORM.user_id == user.get("sub")))
    user_orm = result.scalar_one_or_none()
    if not user_orm:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    result = await db.execute(select(SKUORM).where(SKUORM.sku_id == sku_id))
    sku = result.scalar_one_or_none()
    if not sku:
        raise HTTPException(status_code=404, detail="商品不存在")
    
    result = await db.execute(
        select(CartItemORM).where(
            CartItemORM.user_id == user_orm.id,
            CartItemORM.sku_id == sku.id,
        )
    )
    item = result.scalar_one_or_none()
    if item:
        await db.delete(item)
        await db.commit()
    
    return {"message": "已移除"}
