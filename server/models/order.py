"""
订单相关 Pydantic 模型
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ============ 请求模型 ============

class OrderCreateRequest(BaseModel):
    """创建订单请求"""
    items: list[OrderItemRequest]
    coupon_code: str | None = None
    idempotency_key: str | None = None  # 幂等键（客户端生成 UUID）
    address_id: str | None = None


class OrderItemRequest(BaseModel):
    """订单项请求"""
    sku_id: str
    quantity: int = Field(..., ge=1, le=99)


class OrderCancelRequest(BaseModel):
    """取消订单"""
    order_id: str
    reason: str | None = None


# ============ 响应模型 ============

class OrderItemResponse(BaseModel):
    """订单项响应"""
    product_name: str
    spec_display: str
    quantity: int
    unit_price: float
    total_price: float


class OrderResponse(BaseModel):
    """订单响应"""
    order_id: str
    status: int  # 1待付 2已付 3发货 4完成 5取消
    total_amount: float
    discount_amount: float
    pay_amount: float
    coupon_code: str | None = None
    items: list[OrderItemResponse]
    created_at: datetime
    paid_at: datetime | None = None


# ============ SQLAlchemy ORM 模型 ============

from sqlalchemy import BigInteger, Boolean, Column, DateTime, JSON, Numeric, String
from sqlalchemy.orm import relationship

from server.database.mysql_client import Base


class OrderORM(Base):
    """订单表 ORM"""
    __tablename__ = "orders"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    order_id = Column(String(32), unique=True, nullable=False)
    user_id = Column(BigInteger, nullable=False, index=True)
    total_amount = Column(Numeric(12, 2), nullable=False)
    discount_amount = Column(Numeric(12, 2), default=0)
    pay_amount = Column(Numeric(12, 2), nullable=False)
    status = Column(BigInteger, default=1)
    coupon_code = Column(String(32))
    idempotency_key = Column(String(64), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime)
    cancelled_at = Column(DateTime)
    is_deleted = Column(Boolean, default=False)
    
    # 关系
    items = relationship("OrderItemORM", back_populates="order", cascade="all, delete-orphan")


class OrderItemORM(Base):
    """订单明细表 ORM"""
    __tablename__ = "order_items"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    order_id = Column(BigInteger, nullable=False, index=True)
    sku_id = Column(BigInteger, nullable=False)
    product_name = Column(String(255))
    spec_display = Column(String(100))
    quantity = Column(BigInteger, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    total_price = Column(Numeric(12, 2), nullable=False)
    
    order = relationship("OrderORM", back_populates="items")
