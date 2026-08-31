"""
L3 长期记忆：跨 Session 的用户画像和购买历史。

职责：
- 持久化存储用户偏好（SQLite）
- 购买记录、浏览历史
- 品牌忠诚度、价格敏感度
- 为检索提供个性化 boost（如优先召回常买品牌）

数据模型：
- user_profiles: user_id, preferences_json, created_at, updated_at
- purchase_history: purchase_id, user_id, product_id, product_name, price, quantity, purchased_at
- browse_history: browse_id, user_id, product_id, query, clicked_at, dwell_time_ms
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class UserProfile:
    """跨 session 的用户画像。"""
    user_id: str
    preferred_brands: list[str] = field(default_factory=list)
    preferred_categories: list[str] = field(default_factory=list)
    price_sensitivity: str = "medium"  # low / medium / high
    avg_order_value: float = 0.0
    total_orders: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "preferred_brands": self.preferred_brands,
            "preferred_categories": self.preferred_categories,
            "price_sensitivity": self.price_sensitivity,
            "avg_order_value": self.avg_order_value,
            "total_orders": self.total_orders,
        }


class LongTermMemoryStore:
    """长期记忆 SQLite 存储。"""

    def __init__(self, db_path: str = "server/runtime/user_memory.sqlite3") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    preferences_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL,
                    updated_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS purchase_history (
                    purchase_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    product_name TEXT,
                    price REAL,
                    quantity INTEGER DEFAULT 1,
                    purchased_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS browse_history (
                    browse_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    product_id TEXT,
                    query TEXT,
                    clicked_at REAL,
                    dwell_time_ms INTEGER DEFAULT 0
                )
            """)
            conn.commit()

    def get_profile(self, user_id: str) -> UserProfile | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT preferences_json FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if not row:
                return None
            data = json.loads(row[0])
            return UserProfile(
                user_id=user_id,
                preferred_brands=data.get("preferred_brands", []),
                preferred_categories=data.get("preferred_categories", []),
                price_sensitivity=data.get("price_sensitivity", "medium"),
                avg_order_value=data.get("avg_order_value", 0.0),
                total_orders=data.get("total_orders", 0),
            )

    def save_profile(self, profile: UserProfile) -> None:
        import time
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO user_profiles (user_id, preferences_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                   preferences_json=excluded.preferences_json, updated_at=excluded.updated_at""",
                (profile.user_id, json.dumps(profile.to_dict(), ensure_ascii=False), time.time(), time.time()),
            )
            conn.commit()

    def record_purchase(self, user_id: str, product_id: str, product_name: str, price: float, quantity: int = 1) -> None:
        import time
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO purchase_history (user_id, product_id, product_name, price, quantity, purchased_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, product_id, product_name, price, quantity, time.time()),
            )
            conn.commit()

    def record_browse(self, user_id: str, product_id: str | None, query: str, dwell_time_ms: int = 0) -> None:
        import time
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO browse_history (user_id, product_id, query, clicked_at, dwell_time_ms) VALUES (?, ?, ?, ?, ?)",
                (user_id, product_id, query, time.time(), dwell_time_ms),
            )
            conn.commit()

    def get_purchase_history(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM purchase_history WHERE user_id = ? ORDER BY purchased_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
