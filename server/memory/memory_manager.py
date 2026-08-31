"""
记忆管理器：统一接口，对外暴露用户记忆的所有操作。

四层记忆整合：
- L1 感知记忆：最近对话（内存）
- L2 短期记忆：Session 偏好 + 购物车（内存，可持久化）
- L3 长期记忆：跨 Session 画像 + 购买历史（SQLite）
- L4 工作记忆：当前任务状态（内存）

使用方式：
    manager = MemoryManager()
    session_mem = manager.get_session_memory(session_id, user_id="u_123")
    session_mem.perception.add_turn("user", "我想买红色连衣裙")
    session_mem.short_term.update_preferences({"categories": ["连衣裙"], "keywords": ["红色"]})
    
    # 检索时读取长期记忆做个性化 boost
    profile = manager.get_long_term_profile("u_123")
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from server.memory.long_term_memory import LongTermMemoryStore, UserProfile
from server.memory.perception_memory import PerceptionMemory
from server.memory.short_term_memory import ShortTermMemory
from server.memory.working_memory import WorkingMemory


@dataclass
class SessionMemory:
    """一个 session 的完整记忆集合。"""
    session_id: str
    user_id: str | None = None
    perception: PerceptionMemory = field(default_factory=lambda: PerceptionMemory(max_turns=5))
    short_term: ShortTermMemory | None = None
    working: WorkingMemory = field(default_factory=WorkingMemory)

    def __post_init__(self) -> None:
        if self.short_term is None:
            self.short_term = ShortTermMemory(session_id=self.session_id)


class MemoryManager:
    """记忆管理器：按 session_id 分桶，每个 session 包含四层记忆。"""

    def __init__(self, long_term_db_path: str = "server/runtime/user_memory.sqlite3") -> None:
        self._sessions: dict[str, SessionMemory] = {}
        self._lock = threading.Lock()
        self._long_term = LongTermMemoryStore(db_path=long_term_db_path)

    def get_session_memory(self, session_id: str, user_id: str | None = None) -> SessionMemory:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionMemory(
                    session_id=session_id, user_id=user_id
                )
            mem = self._sessions[session_id]
            if user_id and not mem.user_id:
                mem.user_id = user_id
            return mem

    def get_long_term_profile(self, user_id: str) -> UserProfile | None:
        return self._long_term.get_profile(user_id)

    def save_long_term_profile(self, profile: UserProfile) -> None:
        self._long_term.save_profile(profile)

    def record_purchase(self, user_id: str, product_id: str, product_name: str, price: float, quantity: int = 1) -> None:
        self._long_term.record_purchase(user_id, product_id, product_name, price, quantity)

    def record_browse(self, user_id: str, product_id: str | None, query: str, dwell_time_ms: int = 0) -> None:
        self._long_term.record_browse(user_id, product_id, query, dwell_time_ms)

    def cleanup_expired_sessions(self, ttl_seconds: float = 7200) -> int:
        """清理过期 session，返回清理数量。"""
        expired = []
        with self._lock:
            for sid, mem in self._sessions.items():
                if mem.short_term and mem.short_term.is_expired(ttl_seconds):
                    expired.append(sid)
            for sid in expired:
                del self._sessions[sid]
        return len(expired)

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_sessions": len(self._sessions),
                "session_ids": list(self._sessions.keys()),
            }
