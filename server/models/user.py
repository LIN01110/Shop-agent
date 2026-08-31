"""
用户相关 Pydantic 模型
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ============ 请求模型 ============

class UserRegisterRequest(BaseModel):
    """用户注册请求"""
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)
    phone: str | None = Field(None, pattern=r"^1[3-9]\d{9}$")


class UserLoginRequest(BaseModel):
    """用户登录请求"""
    username: str
    password: str
    session_id: str | None = Field(None, description="匿名会话ID，用于合并购物车")


class UserProfileUpdateRequest(BaseModel):
    """用户画像更新"""
    preferred_brands: list[str] | None = None
    price_range_min: float | None = Field(None, ge=0)
    price_range_max: float | None = Field(None, ge=0)
    skin_type: str | None = None
    age_group: str | None = None


# ============ 响应模型 ============

class UserResponse(BaseModel):
    """用户基本信息响应"""
    user_id: str
    username: str
    phone: str | None = None
    avatar_url: str | None = None
    created_at: datetime | None = None


class UserProfileResponse(BaseModel):
    """用户画像响应"""
    user_id: str
    preferred_brands: list[str] | None = None
    price_range_min: float | None = None
    price_range_max: float | None = None
    skin_type: str | None = None
    age_group: str | None = None


class TokenResponse(BaseModel):
    """Token 响应"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # 秒


# ============ SQLAlchemy ORM 模型 ============

from sqlalchemy import BigInteger, Boolean, Column, DateTime, JSON, String
from sqlalchemy.orm import relationship

from server.database.mysql_client import Base


class UserORM(Base):
    """用户表 ORM"""
    __tablename__ = "users"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(32), unique=True, nullable=False)
    username = Column(String(50), nullable=False)
    password_hash = Column(String(255), nullable=False)
    phone = Column(String(20))
    email = Column(String(100))
    avatar_url = Column(String(500))
    status = Column(BigInteger, default=1)
    is_deleted = Column(Boolean, default=False)
    deleted_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login_at = Column(DateTime)
    
    # 关系
    profile = relationship("UserProfileORM", back_populates="user", uselist=False)
    cart_items = relationship("CartItemORM", back_populates="user")


class UserProfileORM(Base):
    """用户画像表 ORM"""
    __tablename__ = "user_profiles"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, unique=True)
    preferred_brands = Column(JSON)
    price_range_min = Column(BigInteger)  # 存分，避免浮点
    price_range_max = Column(BigInteger)
    skin_type = Column(String(20))
    age_group = Column(String(20))
    search_history = Column(JSON)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = relationship("UserORM", back_populates="profile")
