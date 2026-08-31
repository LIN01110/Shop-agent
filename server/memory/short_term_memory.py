"""
L2 短期记忆：Session 级别的用户偏好和状态。

职责：
- 记录当前 session 内用户表达的偏好（品牌、预算、品类、排除项）
- 记录购物车状态
- 记录当前搜索上下文（正在找什么、筛选条件）
- TTL：session 结束或 30-120 分钟后过期

与现有系统的对应：
- 替代/扩展 `server/session/memory.py` 的 `update_user_profile` 和 `compact_history`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserPreference:
    """用户在当前 session 中表达的偏好。"""
    product_types: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    preferred_brands: list[str] = field(default_factory=list)
    excluded_brands: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    budget_min: float | None = None
    budget_max: float | None = None
    disliked_terms: list[str] = field(default_factory=list)

    def update_from_query(self, filters: dict[str, Any]) -> None:
        """从检索过滤条件中提取偏好。"""
        if "product_types" in filters:
            self._append_unique(self.product_types, filters["product_types"])
        if "categories" in filters:
            self._append_unique(self.categories, filters["categories"])
        if "preferred_brands" in filters:
            self._append_unique(self.preferred_brands, filters["preferred_brands"])
        if "excluded_brands" in filters:
            self._append_unique(self.excluded_brands, filters["excluded_brands"])
        if "keywords" in filters:
            self._append_unique(self.keywords, filters["keywords"])
        if "min_price" in filters:
            self.budget_min = filters["min_price"]
        if "max_price" in filters:
            self.budget_max = filters["max_price"]

    @staticmethod
    def _append_unique(target: list[str], values: Any) -> None:
        if isinstance(values, str):
            values = [values]
        for v in values:
            v = str(v).strip()
            if v and v not in target:
                target.append(v)


@dataclass
class ShortTermMemory:
    """短期记忆：一个 session 的用户画像 + 状态。"""
    session_id: str
    preferences: UserPreference = field(default_factory=UserPreference)
    cart: list[dict[str, Any]] = field(default_factory=list)
    current_task: str = ""  # 当前在做什么："searching", "comparing", "checkout"
    last_query: str = ""
    last_results: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: __import__("time").time())
    last_active: float = field(default_factory=lambda: __import__("time").time())

    def touch(self) -> None:
        import time
        self.last_active = time.time()

    def is_expired(self, ttl_seconds: float = 7200) -> bool:
        """默认 2 小时过期。"""
        import time
        return (time.time() - self.last_active) > ttl_seconds

    def update_preferences(self, filters: dict[str, Any]) -> None:
        self.preferences.update_from_query(filters)
        self.touch()

    def add_to_cart(self, item: dict[str, Any]) -> None:
        self.cart.append(item)
        self.touch()

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "preferences": {
                "product_types": self.preferences.product_types,
                "categories": self.preferences.categories,
                "preferred_brands": self.preferences.preferred_brands,
                "budget_max": self.preferences.budget_max,
                "budget_min": self.preferences.budget_min,
            },
            "cart_count": len(self.cart),
            "current_task": self.current_task,
            "last_query": self.last_query,
            "created_at": self.created_at,
            "last_active": self.last_active,
        }
