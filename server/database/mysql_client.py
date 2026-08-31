"""
MySQL 连接池 + SQLAlchemy 基础配置
使用 SQLAlchemy 2.0 异步模式
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from server.config import config

# 声明基类
Base = declarative_base()

# 全局引擎和会话工厂（延迟初始化）
_engine = None
_async_session_maker = None


def get_engine():
    """获取或创建异步引擎"""
    global _engine
    if _engine is None:
        mysql_cfg = config.mysql
        database_url = (
            f"mysql+aiomysql://{mysql_cfg.user}:{mysql_cfg.password}"
            f"@{mysql_cfg.host}:{mysql_cfg.port}/{mysql_cfg.database}"
            f"?charset=utf8mb4"
        )
        _engine = create_async_engine(
            database_url,
            pool_size=mysql_cfg.pool_size,
            max_overflow=mysql_cfg.max_overflow,
            pool_pre_ping=True,  # 连接前 ping，自动回收失效连接
            echo=mysql_cfg.echo,  # 调试时打印 SQL
        )
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """获取异步会话工厂"""
    global _async_session_maker
    if _async_session_maker is None:
        _async_session_maker = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _async_session_maker


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库会话（上下文管理器）
    用法：
        async with get_db_session() as session:
            result = await session.execute(...)
    """
    session = get_session_maker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI 依赖注入用
    用法：
        @router.get("/users")
        async def list_users(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with get_db_session() as session:
        yield session


async def init_db():
    """初始化数据库（创建所有表）"""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """关闭数据库连接池"""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
