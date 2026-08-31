"""
认证 API：注册 / 登录 / 登出
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.jwt_handler import blacklist_token, create_access_token, get_current_user
from server.auth.password import hash_password, verify_password
from server.database.mysql_client import get_db
from server.database.redis_client import get_redis
from server.models.cart import CartItemORM
from server.models.sku import SKUORM
from server.models.user import (
    TokenResponse,
    UserLoginRequest,
    UserORM,
    UserProfileORM,
    UserRegisterRequest,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(req: UserRegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    用户注册
    
    1. 检查用户名是否已存在
    2. 生成 user_id (UUID)
    3. bcrypt 哈希密码
    4. 创建用户记录
    """
    # 检查用户名
    result = await db.execute(select(UserORM).where(UserORM.username == req.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")
    
    # 生成 user_id
    user_id = f"user_{uuid.uuid4().hex[:16]}"
    
    # 创建用户
    user = UserORM(
        user_id=user_id,
        username=req.username,
        password_hash=hash_password(req.password),
        phone=req.phone,
    )
    db.add(user)
    await db.flush()  # 获取自增 id
    
    # 创建空画像
    profile = UserProfileORM(user_id=user.id)
    db.add(profile)
    await db.commit()
    
    return UserResponse(
        user_id=user.user_id,
        username=user.username,
        phone=user.phone,
    )


@router.post("/login", response_model=TokenResponse)
async def login(req: UserLoginRequest, db: AsyncSession = Depends(get_db)):
    """
    用户登录
    
    1. 查用户
    2. bcrypt 验证密码
    3. 签发 JWT
    """
    result = await db.execute(select(UserORM).where(UserORM.username == req.username))
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    
    if user.status != 1:
        raise HTTPException(status_code=403, detail="账号已被禁用")
    
    # 更新最后登录时间
    from datetime import datetime
    user.last_login_at = datetime.utcnow()
    await db.commit()
    
    # 签发 Token
    token = create_access_token(user_id=user.user_id, username=user.username)
    
    # Phase 5: 合并匿名购物车（如有 session_id）
    if req.session_id:
        await _merge_anon_cart(req.session_id, user.id, db)
    
    return TokenResponse(
        access_token=token,
        expires_in=7 * 86400,  # 7 天
    )


@router.post("/logout")
async def logout(user: dict = Depends(get_current_user)):
    """
    登出：将当前 Token 加入黑名单
    
    前端需要删除本地存储的 Token
    """
    jti = user.get("jti")
    if jti:
        await blacklist_token(jti)
    return {"message": "登出成功"}


@router.get("/me", response_model=UserResponse)
async def get_me(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """获取当前登录用户信息"""
    user_id = user.get("sub")
    result = await db.execute(select(UserORM).where(UserORM.user_id == user_id))
    user_orm = result.scalar_one_or_none()
    
    if not user_orm:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    return UserResponse(
        user_id=user_orm.user_id,
        username=user_orm.username,
        phone=user_orm.phone,
        avatar_url=user_orm.avatar_url,
        created_at=user_orm.created_at,
    )


# ═══════════════════════════════════════════════════
# 匿名购物车合并
# ═══════════════════════════════════════════════════

async def _merge_anon_cart(
    session_id: str,
    user_id: int,
    db: AsyncSession,
) -> None:
    """将匿名 session 的内存购物车合并到用户持久化购物车。"""
    from server.session.state import SessionStore
    from datetime import datetime

    store = SessionStore()
    try:
        state = store.get(session_id)
    except Exception:
        return

    anon_cart = state.cart if state else []
    if not anon_cart:
        return

    for item in anon_cart:
        sku_code = str(item.get("product_id", ""))
        quantity = int(item.get("quantity", 1))
        if quantity <= 0 or not sku_code:
            continue

        # 查找 SKU 对应的数据库 ID
        result = await db.execute(
            select(SKUORM.id).where(SKUORM.sku_code == sku_code)
        )
        sku_db_id = result.scalar_one_or_none()
        if sku_db_id is None:
            continue  # 商品不在数据库中，跳过

        # 检查是否已存在
        result = await db.execute(
            select(CartItemORM).where(
                CartItemORM.user_id == user_id,
                CartItemORM.sku_id == sku_db_id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.quantity += quantity
            existing.updated_at = datetime.utcnow()
        else:
            db.add(
                CartItemORM(
                    user_id=user_id,
                    sku_id=sku_db_id,
                    quantity=quantity,
                    added_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )

    await db.commit()
