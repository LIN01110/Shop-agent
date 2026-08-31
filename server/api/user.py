"""
用户 API：信息 / 画像
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.jwt_handler import get_current_user
from server.database.mysql_client import get_db
from server.models.user import (
    UserProfileORM,
    UserProfileResponse,
    UserProfileUpdateRequest,
)

router = APIRouter(prefix="/user", tags=["用户"])


@router.get("/profile", response_model=UserProfileResponse)
async def get_profile(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取用户画像"""
    user_id_str = user.get("sub")
    
    # 查 users.id
    from server.models.user import UserORM
    result = await db.execute(select(UserORM).where(UserORM.user_id == user_id_str))
    user_orm = result.scalar_one_or_none()
    if not user_orm:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 查画像
    result = await db.execute(select(UserProfileORM).where(UserProfileORM.user_id == user_orm.id))
    profile = result.scalar_one_or_none()
    
    if not profile:
        raise HTTPException(status_code=404, detail="画像未找到")
    
    return UserProfileResponse(
        user_id=user_id_str,
        preferred_brands=profile.preferred_brands,
        price_range_min=profile.price_range_min / 100 if profile.price_range_min else None,
        price_range_max=profile.price_range_max / 100 if profile.price_range_max else None,
        skin_type=profile.skin_type,
        age_group=profile.age_group,
    )


@router.patch("/profile", response_model=UserProfileResponse)
async def update_profile(
    req: UserProfileUpdateRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新用户画像"""
    user_id_str = user.get("sub")
    
    from server.models.user import UserORM
    result = await db.execute(select(UserORM).where(UserORM.user_id == user_id_str))
    user_orm = result.scalar_one_or_none()
    if not user_orm:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    result = await db.execute(select(UserProfileORM).where(UserProfileORM.user_id == user_orm.id))
    profile = result.scalar_one_or_none()
    
    if not profile:
        profile = UserProfileORM(user_id=user_orm.id)
        db.add(profile)
    
    # 更新字段
    if req.preferred_brands is not None:
        profile.preferred_brands = req.preferred_brands
    if req.price_range_min is not None:
        profile.price_range_min = int(req.price_range_min * 100)  # 存分
    if req.price_range_max is not None:
        profile.price_range_max = int(req.price_range_max * 100)
    if req.skin_type is not None:
        profile.skin_type = req.skin_type
    if req.age_group is not None:
        profile.age_group = req.age_group
    
    await db.commit()
    
    return UserProfileResponse(
        user_id=user_id_str,
        preferred_brands=profile.preferred_brands,
        price_range_min=profile.price_range_min / 100 if profile.price_range_min else None,
        price_range_max=profile.price_range_max / 100 if profile.price_range_max else None,
        skin_type=profile.skin_type,
        age_group=profile.age_group,
    )
