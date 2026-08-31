"""
优惠券相关 Pydantic 模型
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ============ 请求模型 ============

class CouponTemplateCreateRequest(BaseModel):
    """创建优惠券模板"""
    name: str
    type: int = Field(..., ge=1, le=3)  # 1满减 2折扣 3立减
    threshold: float = Field(default=0, ge=0)
    discount_amount: float | None = Field(None, ge=0)
    discount_rate: float | None = Field(None, ge=0, le=1)
    total_count: int = Field(..., ge=1)
    user_limit: int = Field(default=1, ge=1)


class ClaimCouponRequest(BaseModel):
    """领取优惠券"""
    template_id: str


class UseCouponRequest(BaseModel):
    """使用优惠券"""
    coupon_code: str
    order_id: str


# ============ 响应模型 ============

class CouponTemplateResponse(BaseModel):
    """优惠券模板响应"""
    template_id: str
    name: str
    type: int
    threshold: float
    discount_amount: float | None
    discount_rate: float | None
    remaining_count: int
    user_limit: int


class UserCouponResponse(BaseModel):
    """用户优惠券响应"""
    coupon_code: str
    template_name: str
    type: int
    status: int  # 1未用 2已冻结 3已使用
    created_at: datetime


# ============ SQLAlchemy ORM 模型 ============

from sqlalchemy import BigInteger, Boolean, Column, DateTime, JSON, Numeric, String

from server.database.mysql_client import Base


class CouponTemplateORM(Base):
    """优惠券模板 ORM"""
    __tablename__ = "coupon_templates"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    template_id = Column(String(32), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(String(255))
    type = Column(BigInteger, nullable=False)
    scope = Column(BigInteger, default=1)
    threshold = Column(Numeric(10, 2), default=0)
    discount_amount = Column(Numeric(10, 2))
    discount_rate = Column(Numeric(3, 2))
    max_discount = Column(Numeric(10, 2))
    total_count = Column(BigInteger, nullable=False)
    remaining_count = Column(BigInteger, nullable=False)
    user_limit = Column(BigInteger, default=1)
    applicable_skus = Column(JSON)
    applicable_categories = Column(JSON)
    stackable = Column(Boolean, default=False)
    status = Column(BigInteger, default=1)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserCouponORM(Base):
    """用户优惠券 ORM"""
    __tablename__ = "user_coupons"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    coupon_code = Column(String(32), unique=True, nullable=False)
    user_id = Column(BigInteger, nullable=False, index=True)
    template_id = Column(String(32), nullable=False)
    status = Column(BigInteger, default=1)  # 1未用 2已冻结 3已使用
    freeze_time = Column(DateTime)
    order_id = Column(String(32), index=True)
    use_time = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_deleted = Column(Boolean, default=False)


class CouponUsageLogORM(Base):
    """优惠券使用记录 ORM"""
    __tablename__ = "coupon_usage_logs"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    coupon_code = Column(String(32), nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False)
    order_id = Column(String(32), nullable=False, index=True)
    order_amount = Column(Numeric(12, 2), nullable=False)
    discount_amount = Column(Numeric(12, 2), nullable=False)
    use_time = Column(DateTime, nullable=False)
