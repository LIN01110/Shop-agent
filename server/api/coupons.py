"""
优惠券 API：领取 / 查询 / 使用
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.jwt_handler import get_current_user
from server.database.mysql_client import get_db
from server.database.redis_client import acquire_lock, get_redis, release_lock
from server.models.coupon import CouponTemplateORM, UserCouponORM
from server.models.user import UserORM

router = APIRouter(prefix="/coupons", tags=["优惠券"])


@router.get("/templates")
async def list_coupon_templates(db: AsyncSession = Depends(get_db)):
    """获取可领取的优惠券模板列表"""
    result = await db.execute(
        select(CouponTemplateORM)
        .where(CouponTemplateORM.status == 1, CouponTemplateORM.is_deleted == False)
        .where(CouponTemplateORM.remaining_count > 0)
    )
    templates = result.scalars().all()
    
    return [
        {
            "template_id": t.template_id,
            "name": t.name,
            "type": t.type,
            "threshold": float(t.threshold) if t.threshold else 0,
            "discount_amount": float(t.discount_amount) if t.discount_amount else None,
            "discount_rate": float(t.discount_rate) if t.discount_rate else None,
            "remaining_count": t.remaining_count,
            "user_limit": t.user_limit,
        }
        for t in templates
    ]


@router.post("/claim/{template_id}")
async def claim_coupon(
    template_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    领取优惠券（带分布式锁 + 库存扣减）
    """
    user_id_str = user.get("sub")
    
    # 查用户
    result = await db.execute(select(UserORM).where(UserORM.user_id == user_id_str))
    user_orm = result.scalar_one_or_none()
    if not user_orm:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 分布式锁
    lock_key = f"lock:coupon:{template_id}:{user_orm.id}"
    acquired = await acquire_lock(lock_key, expire=30)
    if not acquired:
        raise HTTPException(status_code=429, detail="操作太快，请稍后再试")
    
    try:
        # 查模板
        result = await db.execute(
            select(CouponTemplateORM).where(CouponTemplateORM.template_id == template_id)
        )
        template = result.scalar_one_or_none()
        if not template or template.remaining_count <= 0:
            raise HTTPException(status_code=400, detail="券已领完")
        
        # 查已领数量
        result = await db.execute(
            select(UserCouponORM).where(
                UserCouponORM.user_id == user_orm.id,
                UserCouponORM.template_id == template_id,
                UserCouponORM.is_deleted == False,
            )
        )
        claimed_count = len(result.scalars().all())
        if claimed_count >= template.user_limit:
            raise HTTPException(status_code=400, detail="已达领取上限")
        
        # Redis 原子扣减库存
        redis = get_redis()
        remaining = await redis.decr(f"coupon_stock:{template_id}")
        if remaining < 0:
            await redis.incr(f"coupon_stock:{template_id}")  # 回滚
            raise HTTPException(status_code=400, detail="券已领完")
        
        # 生成券码
        import uuid
        coupon_code = f"CP{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6]}"
        
        # 写数据库
        coupon = UserCouponORM(
            coupon_code=coupon_code,
            user_id=user_orm.id,
            template_id=template_id,
            status=1,
            created_at=datetime.utcnow(),
        )
        db.add(coupon)
        
        # 扣减模板库存
        template.remaining_count -= 1
        
        await db.commit()
        
        return {"coupon_code": coupon_code, "message": "领取成功"}
        
    finally:
        await release_lock(lock_key)


@router.get("/my")
async def my_coupons(
    status: int | None = None,  # 1未用 2已冻结 3已使用
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """我的优惠券列表"""
    user_id_str = user.get("sub")
    
    result = await db.execute(select(UserORM).where(UserORM.user_id == user_id_str))
    user_orm = result.scalar_one_or_none()
    if not user_orm:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    query = select(UserCouponORM).where(
        UserCouponORM.user_id == user_orm.id,
        UserCouponORM.is_deleted == False,
    )
    if status:
        query = query.where(UserCouponORM.status == status)
    
    result = await db.execute(query.order_by(UserCouponORM.created_at.desc()))
    coupons = result.scalars().all()
    
    return [
        {
            "coupon_code": c.coupon_code,
            "template_id": c.template_id,
            "status": c.status,
            "freeze_time": c.freeze_time,
            "order_id": c.order_id,
            "created_at": c.created_at,
        }
        for c in coupons
    ]
