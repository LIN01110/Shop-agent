"""
购物车 ORM 模型
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Column, DateTime, String

from server.database.mysql_client import Base


class CartItemORM(Base):
    __tablename__ = "cart_items"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    sku_id = Column(BigInteger, nullable=False)
    quantity = Column(BigInteger, default=1)
    selected = Column(Boolean, default=True)
    invalid_reason = Column(String(100))
    added_at = Column(DateTime)
    updated_at = Column(DateTime)

    __table_args__ = ({"mysql_charset": "utf8mb4"},)
