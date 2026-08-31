"""
SKU / 价格 / 库存 Pydantic 模型
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ============ 请求模型 ============

class SKUCreateRequest(BaseModel):
    """SKU 创建请求"""
    sku_id: str
    product_id: str
    product_name: str
    brand: str
    category: str
    spec_json: dict[str, Any]
    spec_display: str
    image_url: str | None = None


class PriceSetRequest(BaseModel):
    """设置价格请求"""
    sku_id: str
    price: float = Field(..., ge=0)
    original_price: float | None = Field(None, ge=0)
    effective_from: datetime | None = None  # 默认现在


class InventoryAdjustRequest(BaseModel):
    """库存调整"""
    sku_id: str
    delta: int  # 正数增加，负数减少


# ============ 响应模型 ============

class SKUResponse(BaseModel):
    """SKU 响应"""
    sku_id: str
    product_id: str
    product_name: str
    brand: str
    category: str
    spec_display: str
    image_url: str | None = None
    status: int


class PriceResponse(BaseModel):
    """价格响应"""
    price_id: str
    sku_id: str
    price: float
    original_price: float | None = None
    effective_from: datetime
    promotion_tag: str | None = None


class InventoryResponse(BaseModel):
    """库存响应"""
    sku_id: str
    available_qty: int
    reserved_qty: int
    sold_qty: int


# ============ SQLAlchemy ORM 模型 ============

from sqlalchemy import BigInteger, Boolean, Column, DateTime, JSON, Numeric, String, UniqueConstraint

from server.database.mysql_client import Base


class SKUORM(Base):
    """SKU 表 ORM"""
    __tablename__ = "skus"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    sku_id = Column(String(64), unique=True, nullable=False)
    product_id = Column(String(32), nullable=False, index=True)
    product_name = Column(String(255), nullable=False)
    brand = Column(String(50), nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)
    spec_json = Column(JSON, nullable=False)
    spec_display = Column(String(100), nullable=False)
    image_url = Column(String(500))
    status = Column(BigInteger, default=1)
    is_deleted = Column(Boolean, default=False)
    deleted_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 关系
    prices = relationship("SKUPriceORM", back_populates="sku")
    inventory = relationship("SKUInventoryORM", back_populates="sku", uselist=False)


class SKUPriceORM(Base):
    """SKU 价格表 ORM"""
    __tablename__ = "sku_prices"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    price_id = Column(String(64), unique=True, nullable=False)
    sku_id = Column(BigInteger, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    original_price = Column(Numeric(10, 2))
    currency = Column(String(3), default="CNY")
    effective_from = Column(DateTime, nullable=False)
    effective_to = Column(DateTime)
    promotion_tag = Column(String(100))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 联合唯一 + 索引
    __table_args__ = (
        UniqueConstraint("sku_id", "effective_from", name="uk_sku_time"),
        {"mysql_charset": "utf8mb4"},
    )
    
    sku = relationship("SKUORM", back_populates="prices")


class SKUInventoryORM(Base):
    """SKU 库存表 ORM"""
    __tablename__ = "sku_inventory"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    sku_id = Column(BigInteger, nullable=False, unique=True)
    available_qty = Column(BigInteger, default=0)
    reserved_qty = Column(BigInteger, default=0)
    sold_qty = Column(BigInteger, default=0)
    warning_level = Column(BigInteger, default=10)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    sku = relationship("SKUORM", back_populates="inventory")
