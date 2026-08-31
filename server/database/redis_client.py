"""
Redis 连接客户端
三层用途：Cache / Session / Rate Limit / Lock
"""

from __future__ import annotations

import redis.asyncio as redis

from server.config import config

# 全局 Redis 客户端（延迟初始化）
_redis_client = None


def get_redis() -> redis.Redis:
    """获取或创建 Redis 客户端"""
    global _redis_client
    if _redis_client is None:
        redis_cfg = config.redis
        _redis_client = redis.Redis(
            host=redis_cfg.host,
            port=redis_cfg.port,
            db=redis_cfg.db,
            password=redis_cfg.password or None,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True,
            health_check_interval=30,
        )
    return _redis_client


async def close_redis():
    """关闭 Redis 连接"""
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None


# 便捷方法

async def cache_get(key: str) -> str | None:
    """获取缓存"""
    r = get_redis()
    return await r.get(key)


async def cache_set(key: str, value: str, ttl: int = 300):
    """设置缓存（默认 5 分钟）"""
    r = get_redis()
    await r.setex(key, ttl, value)


async def cache_delete(key: str):
    """删除缓存"""
    r = get_redis()
    await r.delete(key)


async def acquire_lock(lock_key: str, expire: int = 30) -> bool:
    """获取分布式锁（SET NX EX）"""
    r = get_redis()
    return await r.set(lock_key, "1", nx=True, ex=expire)


async def release_lock(lock_key: str):
    """释放分布式锁"""
    r = get_redis()
    await r.delete(lock_key)


async def rate_limit_check(key: str, limit: int, window: int = 60) -> bool:
    """
    滑动窗口限流
    :param key: 限流键
    :param limit: 窗口内最大次数
    :param window: 窗口大小（秒）
    :return: True 表示通过，False 表示限流
    """
    r = get_redis()
    current = await r.get(key)
    if current is None:
        await r.setex(key, window, 1)
        return True
    if int(current) >= limit:
        return False
    await r.incr(key)
    return True
