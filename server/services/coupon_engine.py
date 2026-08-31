"""
优惠券计算引擎
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.coupon import CouponTemplateORM, UserCouponORM


async def calculate_discount(
    total_amount: float,
    coupon_code: str,
    user_id: int,
    db: AsyncSession,
) -> tuple[float, str | None]:
    """
    计算优惠券抵扣金额
    
    返回：(discount_amount, error_message)
    error_message 不为空表示券不可用
    """
    # 查券
    result = await db.execute(
        select(UserCouponORM, CouponTemplateORM)
        .join(CouponTemplateORM, UserCouponORM.template_id == CouponTemplateORM.template_id)
        .where(
            UserCouponORM.coupon_code == coupon_code,
            UserCouponORM.user_id == user_id,
            UserCouponORM.status == 1,  # 未使用
            UserCouponORM.is_deleted == False,
        )
    )
    row = result.one_or_none()
    if not row:
        return 0, "优惠券不存在或不可用"
    
    coupon, template = row
    
    # 检查门槛
    if total_amount < float(template.threshold):
        return 0, f"订单金额未满 {template.threshold} 元"
    
    # 计算优惠
    discount = 0.0
    if template.type == 1:  # 满减
        discount = float(template.discount_amount) if template.discount_amount else 0
    elif template.type == 2:  # 折扣
        rate = float(template.discount_rate) if template.discount_rate else 1
        discount = total_amount * (1 - rate)
        if template.max_discount:
            discount = min(discount, float(template.max_discount))
    elif template.type == 3:  # 立减
        discount = float(template.discount_amount) if template.discount_amount else 0
    
    # 优惠不能超过订单金额
    discount = min(discount, total_amount)
    
    return discount, None


async def find_best_coupon(
    total_amount: float,
    user_id: int,
    db: AsyncSession,
) -> tuple[str | None, float]:
    """
    为用户查找最优可用优惠券
    
    返回：(coupon_code, discount_amount)
    """
    result = await db.execute(
        select(UserCouponORM, CouponTemplateORM)
        .join(CouponTemplateORM, UserCouponORM.template_id == CouponTemplateORM.template_id)
        .where(
            UserCouponORM.user_id == user_id,
            UserCouponORM.status == 1,
            UserCouponORM.is_deleted == False,
        )
    )
    
    best_coupon = None
    best_discount = 0
    
    for coupon, template in result.all():
        if total_amount < float(template.threshold):
            continue
        
        discount = 0.0
        if template.type == 1:
            discount = float(template.discount_amount) if template.discount_amount else 0
        elif template.type == 2:
            rate = float(template.discount_rate) if template.discount_rate else 1
            discount = total_amount * (1 - rate)
            if template.max_discount:
                discount = min(discount, float(template.max_discount))
        elif template.type == 3:
            discount = float(template.discount_amount) if template.discount_amount else 0
        
        discount = min(discount, total_amount)
        
        if discount > best_discount:
            best_discount = discount
            best_coupon = coupon.coupon_code
    
    return best_coupon, best_discount
