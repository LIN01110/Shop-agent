"""
订单 API：创建 / 查询 / 取消 / 支付
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.jwt_handler import get_current_user
from server.database.mysql_client import get_db
from server.database.redis_client import get_redis
from server.models.coupon import UserCouponORM
from server.models.order import OrderItemORM, OrderORM
from server.models.sku import SKUInventoryORM, SKUORM, SKUPriceORM
from server.models.user import UserORM

router = APIRouter(prefix="/orders", tags=["订单"])


@router.post("/create")
async def create_order(
    items: list[dict],  # [{"sku_id": "xxx", "quantity": 2}]
    coupon_code: str | None = None,
    idempotency_key: str | None = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    创建订单（带事务 + 幂等 + 库存扣减）
    """
    user_id_str = user.get("sub")
    
    # 1. 幂等检查
    if idempotency_key:
        result = await db.execute(
            select(OrderORM).where(OrderORM.idempotency_key == idempotency_key)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return {"order_id": existing.order_id, "message": "订单已存在"}
    
    # 2. 查用户
    result = await db.execute(select(UserORM).where(UserORM.user_id == user_id_str))
    user_orm = result.scalar_one_or_none()
    if not user_orm:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 3. 事务：校验库存 + 扣减 + 创建订单
    async with db.begin():
        # 3.1 校验并锁定库存
        sku_items = []
        for item in items:
            sku_id_str = item["sku_id"]
            qty = item["quantity"]
            
            result = await db.execute(
                select(SKUORM).where(SKUORM.sku_id == sku_id_str)
            )
            sku = result.scalar_one_or_none()
            if not sku:
                raise HTTPException(status_code=404, detail=f"SKU 不存在: {sku_id_str}")
            
            result = await db.execute(
                select(SKUInventoryORM).where(SKUInventoryORM.sku_id == sku.id)
            )
            inv = result.scalar_one_or_none()
            if not inv or inv.available_qty < qty:
                raise HTTPException(status_code=400, detail=f"库存不足: {sku_id_str}")
            
            sku_items.append((sku, qty))
        
        # 3.2 扣减库存
        for sku, qty in sku_items:
            result = await db.execute(
                select(SKUInventoryORM).where(SKUInventoryORM.sku_id == sku.id)
            )
            inv = result.scalar_one()
            inv.available_qty -= qty
            inv.reserved_qty += qty
        
        # 3.3 计算金额
        total_amount = 0
        for sku, qty in sku_items:
            # 查当前价格
            result = await db.execute(
                select(SKUPriceORM)
                .where(SKUPriceORM.sku_id == sku.id, SKUPriceORM.is_active == True)
                .order_by(SKUPriceORM.effective_from.desc())
                .limit(1)
            )
            price_row = result.scalar_one_or_none()
            unit_price = float(price_row.price) if price_row else 0
            total_amount += unit_price * qty
        
        # 3.4 优惠券处理
        discount_amount = 0
        if coupon_code:
            result = await db.execute(
                select(UserCouponORM).where(
                    UserCouponORM.coupon_code == coupon_code,
                    UserCouponORM.user_id == user_orm.id,
                    UserCouponORM.status == 1,
                )
            )
            coupon = result.scalar_one_or_none()
            if coupon:
                # 简单满减逻辑（后续接入 coupon_engine）
                discount_amount = min(50, total_amount * 0.1)  # 示例
        
        pay_amount = total_amount - discount_amount
        
        # 3.5 创建订单
        import uuid
        order_id = f"O{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6]}"
        
        order = OrderORM(
            order_id=order_id,
            user_id=user_orm.id,
            total_amount=total_amount,
            discount_amount=discount_amount,
            pay_amount=pay_amount,
            status=1,
            coupon_code=coupon_code,
            idempotency_key=idempotency_key,
        )
        db.add(order)
        await db.flush()
        
        # 3.6 创建订单项
        for sku, qty in sku_items:
            result = await db.execute(
                select(SKUPriceORM)
                .where(SKUPriceORM.sku_id == sku.id, SKUPriceORM.is_active == True)
                .order_by(SKUPriceORM.effective_from.desc())
                .limit(1)
            )
            price_row = result.scalar_one_or_none()
            unit_price = float(price_row.price) if price_row else 0
            
            item = OrderItemORM(
                order_id=order.id,
                sku_id=sku.id,
                product_name=sku.product_name,
                spec_display=sku.spec_display,
                quantity=qty,
                unit_price=unit_price,
                total_price=unit_price * qty,
            )
            db.add(item)
        
        # 3.7 冻结券
        if coupon_code and coupon:
            coupon.status = 2
            coupon.freeze_time = datetime.utcnow()
            coupon.order_id = order_id
            
            redis = get_redis()
            await redis.setex(f"coupon_freeze:{order_id}", 1800, coupon_code)
    
    return {"order_id": order_id, "pay_amount": pay_amount}


@router.get("/{order_id}")
async def get_order(
    order_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询订单详情"""
    result = await db.execute(select(OrderORM).where(OrderORM.order_id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    
    return {
        "order_id": order.order_id,
        "status": order.status,
        "total_amount": float(order.total_amount),
        "discount_amount": float(order.discount_amount),
        "pay_amount": float(order.pay_amount),
        "created_at": order.created_at,
    }


@router.post("/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    取消订单：释放库存 + 释放券
    """
    result = await db.execute(select(OrderORM).where(OrderORM.order_id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    
    if order.status != 1:
        raise HTTPException(status_code=400, detail="订单不可取消")
    
    async with db.begin():
        # 1. 恢复库存
        result = await db.execute(
            select(OrderItemORM).where(OrderItemORM.order_id == order.id)
        )
        for item in result.scalars().all():
            result = await db.execute(
                select(SKUInventoryORM).where(SKUInventoryORM.sku_id == item.sku_id)
            )
            inv = result.scalar_one_or_none()
            if inv:
                inv.available_qty += item.quantity
                inv.reserved_qty -= item.quantity
        
        # 2. 释放券
        if order.coupon_code:
            result = await db.execute(
                select(UserCouponORM).where(UserCouponORM.coupon_code == order.coupon_code)
            )
            coupon = result.scalar_one_or_none()
            if coupon:
                coupon.status = 1
                coupon.freeze_time = None
                coupon.order_id = None
            
            redis = get_redis()
            await redis.delete(f"coupon_freeze:{order_id}")
        
        # 3. 更新订单状态
        order.status = 5
        order.cancelled_at = datetime.utcnow()
    
    return {"message": "订单已取消"}


@router.post("/{order_id}/pay")
async def pay_order(
    order_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    支付成功回调：券正式核销
    """
    result = await db.execute(select(OrderORM).where(OrderORM.order_id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    
    if order.status != 1:
        raise HTTPException(status_code=400, detail="订单状态错误")
    
    async with db.begin():
        # 1. 更新订单状态
        order.status = 2
        order.paid_at = datetime.utcnow()
        
        # 2. 释放库存预占（实际扣减）
        result = await db.execute(
            select(OrderItemORM).where(OrderItemORM.order_id == order.id)
        )
        for item in result.scalars().all():
            result = await db.execute(
                select(SKUInventoryORM).where(SKUInventoryORM.sku_id == item.sku_id)
            )
            inv = result.scalar_one_or_none()
            if inv:
                inv.reserved_qty -= item.quantity
                inv.sold_qty += item.quantity
        
        # 3. 券正式核销
        if order.coupon_code:
            result = await db.execute(
                select(UserCouponORM).where(UserCouponORM.coupon_code == order.coupon_code)
            )
            coupon = result.scalar_one_or_none()
            if coupon:
                coupon.status = 3
                coupon.use_time = datetime.utcnow()
            
            # 写核销记录
            from server.models.coupon import CouponUsageLogORM
            log = CouponUsageLogORM(
                coupon_code=order.coupon_code,
                user_id=order.user_id,
                order_id=order_id,
                order_amount=float(order.total_amount),
                discount_amount=float(order.discount_amount),
                use_time=datetime.utcnow(),
            )
            db.add(log)
            
            # 删 Redis 倒计时
            redis = get_redis()
            await redis.delete(f"coupon_freeze:{order_id}")
    
    return {"message": "支付成功"}
