"""
JWT 处理：签发、验证、黑名单
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from server.config import config

# JWT 配置
JWT_SECRET = config.jwt.secret_key
JWT_ALGORITHM = config.jwt.algorithm
JWT_EXPIRE_DAYS = config.jwt.expire_days

# 安全方案
security = HTTPBearer(auto_error=False)


def create_access_token(user_id: str, username: str, **extra_claims) -> str:
    """
    创建 JWT Token
    
    Payload:
    {
        "sub": "user_abc123",      # user_id
        "username": "lin",
        "iat": 1723012200,          # 签发时间
        "exp": 1723617000,          # 过期时间
        "jti": "uuid-xxxx"          # 唯一标识，用于黑名单
    }
    """
    import uuid
    
    now = datetime.utcnow()
    payload = {
        "sub": user_id,
        "username": username,
        "iat": now,
        "exp": now + timedelta(days=JWT_EXPIRE_DAYS),
        "jti": str(uuid.uuid4()),
        **extra_claims,
    }
    
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """
    解码并验证 JWT Token
    
    抛出:
        HTTPException(401): Token 无效或过期
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 Token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    """
    FastAPI 依赖：获取当前登录用户
    
    用法：
        @router.get("/me")
        async def get_me(user: dict = Depends(get_current_user)):
            return {"user_id": user["sub"]}
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证信息",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = credentials.credentials
    payload = decode_token(token)
    
    # 检查黑名单（可选，登出时加入黑名单）
    from server.database.redis_client import get_redis
    redis = get_redis()
    jti = payload.get("jti")
    if jti and await redis.get(f"jwt:blacklist:{jti}"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 已被注销",
        )
    
    return payload


async def blacklist_token(jti: str, expire_days: int = 7):
    """
    将 Token 加入黑名单（登出时使用）
    
    TTL 设为 7 天（超过 Token 有效期即可）
    """
    from server.database.redis_client import get_redis
    redis = get_redis()
    await redis.setex(f"jwt:blacklist:{jti}", expire_days * 86400, "1")


def get_user_id_from_token(token: str) -> str:
    """从 Token 中提取 user_id（不验证，仅解析）"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"verify_exp": False})
        return payload.get("sub", "")
    except jwt.InvalidTokenError:
        return ""
