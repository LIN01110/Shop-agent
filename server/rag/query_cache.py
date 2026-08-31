"""
查询结果缓存：L1 内存（秒级 TTL）+ L2 Redis（分钟级 TTL）两级缓存。

设计要点：
- L1 内存缓存：防同一秒内重复请求抖动，重启即失
- L2 Redis 缓存：跨实例共享，重启不丢，默认 5 分钟 TTL
- 数据压缩：可选 gzip，减少 Redis 内存和网络开销
- 降级策略：Redis 不可用时自动降级为纯内存缓存，不抛错
- 序列化：RetrievalResult 等对象转 dict 后 JSON 序列化

使用方式：
    from server.rag.query_cache import QueryCache
    cache = QueryCache(redis_client=redis_conn, enabled=True)
    cache.set(query_id, results)
    cached = cache.get(query_id)
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from typing import Any

try:
    import redis as _redis_sync
    _HAS_SYNC_REDIS = True
except ImportError:
    _HAS_SYNC_REDIS = False

logger = logging.getLogger(__name__)


def create_sync_redis_client(
    host: str = "localhost",
    port: int = 6379,
    db: int = 0,
    password: str = "",
    socket_timeout: float = 2.0,
) -> Any | None:
    """创建同步 Redis 客户端。如果 redis 包不可用则返回 None。"""
    if not _HAS_SYNC_REDIS:
        return None
    try:
        return _redis_sync.Redis(
            host=host,
            port=port,
            db=db,
            password=password or None,
            decode_responses=False,
            socket_connect_timeout=socket_timeout,
            socket_timeout=socket_timeout,
            health_check_interval=30,
        )
    except Exception as exc:
        logger.warning("Failed to create sync Redis client: %s", exc)
        return None


class QueryCache:
    """两级查询缓存（L1 内存 + L2 Redis）。"""

    def __init__(
        self,
        *,
        l1_ttl_seconds: float = 5.0,
        l2_ttl_seconds: float = 300.0,
        compress: bool = True,
        redis_client: Any | None = None,
        enabled: bool = True,
    ) -> None:
        self.l1_ttl = max(0.0, l1_ttl_seconds)
        self.l2_ttl = max(1, int(l2_ttl_seconds))
        self.compress = compress
        self.enabled = enabled
        self._redis = redis_client
        self._l1: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None

        # L1: 内存缓存
        if key in self._l1:
            data, ts = self._l1[key]
            if time.time() - ts < self.l1_ttl:
                return data
            del self._l1[key]

        # L2: Redis 缓存
        if self._redis:
            try:
                raw = self._redis.get(f"rag:cache:{key}")
                if raw:
                    data = self._deserialize(raw)
                    # 回填 L1
                    self._l1[key] = (data, time.time())
                    return data
            except Exception as exc:
                logger.debug("Redis cache get failed: %s", exc)

        return None

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return

        # 写 L1
        self._l1[key] = (value, time.time())

        # 写 L2
        if self._redis:
            try:
                raw = self._serialize(value)
                self._redis.setex(f"rag:cache:{key}", self.l2_ttl, raw)
            except Exception as exc:
                logger.debug("Redis cache set failed: %s", exc)

    def clear(self) -> None:
        """清空 L1 内存缓存（不清 Redis）。"""
        self._l1.clear()

    def _serialize(self, data: Any) -> bytes:
        json_bytes = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        if self.compress:
            return gzip.compress(json_bytes)
        return json_bytes

    def _deserialize(self, raw: bytes) -> Any:
        if self.compress:
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))
