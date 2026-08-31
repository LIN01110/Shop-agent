"""
答辩重点 🟠（会话状态）
任务：Day 2 任务 2.5 — 理解 SessionState 核心字段与持久化。
核心：messages 对话历史；filters/exclusions 已确认的正向/排除条件；
      candidate_product_cards 上一轮候选（用于上下文引用）；pending_subject 澄清待确认主题；
      cart 购物车；product_type_scope 当前商品范围（防止旧品类污染新需求）。
      merge_filters() 与 scope_transition 实现条件累积与品类切换清理。
高频追问：
  - “为什么长会话中要清理旧过滤条件？”
    → 避免旧品类/旧预算污染新需求，scope_transition 检测到品类切换时重置
  - “‘第二个怎么样’为什么能取到上一轮商品？”
    → candidate_product_cards 保存上一轮结果，ContextFollowUpHandler 按 ordinal 索引
"""

import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Protocol

from pydantic import BaseModel, Field


class FilterCondition(BaseModel):
    kind: str
    value: str


class ConversationTurn(BaseModel):
    role: str
    content: str


class UserPreferenceProfile(BaseModel):
    product_types: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    preferred_brands: list[str] = Field(default_factory=list)
    disliked_terms: list[str] = Field(default_factory=list)
    budget_ceiling: float | None = None


class SessionState(BaseModel):
    session_id: str
    history: list[ConversationTurn] = Field(default_factory=list)
    history_summary: str = ""
    user_profile: UserPreferenceProfile = Field(default_factory=UserPreferenceProfile)
    filters: list[FilterCondition] = Field(default_factory=list)
    exclusions: list[FilterCondition] = Field(default_factory=list)
    candidate_products: list[str] = Field(default_factory=list)
    candidate_product_cards: list[dict] = Field(default_factory=list)
    pending_subject: str = ""
    pending_cart_action: dict = Field(default_factory=dict)
    cart: list[dict] = Field(default_factory=list)

    def add_user_message(self, content: str) -> None:
        self.history.append(ConversationTurn(role="user", content=content))

    def add_assistant_message(self, content: str) -> None:
        self.history.append(ConversationTurn(role="assistant", content=content))

    def merge_filters(self, filters: list[FilterCondition], *, auto_scope_reset: bool = True) -> None:
        if auto_scope_reset and self.starts_new_product_scope(filters):
            self.reset_product_scope()
        for item in filters:
            target = self.exclusions if item.kind == "exclude" else self.filters
            if item not in target:
                target.append(item)

    def starts_new_product_scope(self, incoming_filters: list[FilterCondition]) -> bool:
        incoming_scopes = product_scope_values(incoming_filters)
        if not incoming_scopes:
            return False

        existing_scopes = product_scope_values(self.filters)
        if not existing_scopes:
            return self.has_product_scoped_state()

        return True

    def has_product_scoped_state(self) -> bool:
        if self.exclusions or self.candidate_products or self.candidate_product_cards or self.pending_subject:
            return True
        return any(item.kind == "keyword" for item in self.filters)

    def reset_product_scope(self) -> None:
        """答辩追问：品类切换时清空所有条件，用户再说回旧品类会丢失之前的限定吗？
        答：会的。例如用户先聊跑鞋（预算500+轻量），再聊衣服（触发 replace_product_scope），
        再切回跑鞋时，预算500和轻量已经丢了。这是设计上的有意权衡：
        ① 不同品类的条件通常不通用（鞋的"轻量"≠衣服的"轻量"）；
        ② 宁可让用户重新说，也不让旧条件错误污染新检索；
        ③ 对话历史保留在 session.history 中，LLM planner 可从文本中恢复上下文；
        ④ 按品类分组保存条件属于过度设计，超出当前 Demo 范围。
        如果要改进：保留通用条件（如预算）、或增强 planner 从历史重建 filters。
        """
        self.filters = []
        self.exclusions = []
        self.candidate_products = []
        self.candidate_product_cards = []
        self.pending_subject = ""
        self.pending_cart_action = {}

    def clear_product_constraints(self) -> None:
        self.filters = []
        self.exclusions = []
        self.pending_subject = ""
        self.pending_cart_action = {}

    def clear_product_candidates(self) -> None:
        self.candidate_products = []
        self.candidate_product_cards = []
        self.pending_subject = ""
        self.pending_cart_action = {}


class SessionRecord(BaseModel):
    session_id: str
    state: SessionState
    last_seen: float
    updated_at: float


class SessionStore:
    def __init__(
        self,
        *,
        max_items: int = 500,
        ttl_seconds: int = 43200,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.max_items = max(1, max_items)
        self.ttl_seconds = max(1, ttl_seconds)
        self._clock = clock or time.time
        self._sessions: dict[str, tuple[SessionState, float]] = {}

    def get(self, session_id: str) -> SessionState:
        now = self._clock()
        self.prune_expired(now)
        entry = self._sessions.get(session_id)
        if entry is None:
            self.evict_oldest_if_full()
            state = SessionState(session_id=session_id)
        else:
            state = entry[0]
        self._sessions[session_id] = (state, now)
        return state

    def save(self, state: SessionState) -> None:
        self._sessions[state.session_id] = (state, self._clock())

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def list_recent(self, limit: int = 20) -> list[SessionRecord]:
        self.prune_expired(self._clock())
        items = sorted(self._sessions.items(), key=lambda item: item[1][1], reverse=True)
        return [
            SessionRecord(
                session_id=session_id,
                state=entry[0],
                last_seen=entry[1],
                updated_at=entry[1],
            )
            for session_id, entry in items[: max(1, limit)]
        ]

    def count(self) -> int:
        self.prune_expired(self._clock())
        return len(self._sessions)

    def prune_expired(self, now: float) -> None:
        expired = [
            session_id
            for session_id, (_, last_seen) in self._sessions.items()
            if now - last_seen > self.ttl_seconds
        ]
        for session_id in expired:
            del self._sessions[session_id]

    def evict_oldest_if_full(self) -> None:
        if len(self._sessions) < self.max_items:
            return
        oldest_session_id = min(self._sessions.items(), key=lambda item: item[1][1])[0]
        del self._sessions[oldest_session_id]


class PersistentSessionStore(Protocol):
    def get(self, session_id: str) -> SessionState:
        ...

    def save(self, state: SessionState) -> None:
        ...

    def delete(self, session_id: str) -> None:
        ...

    def list_recent(self, limit: int = 20) -> list[SessionRecord]:
        ...

    def count(self) -> int:
        ...


class SQLiteSessionStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        max_items: int = 500,
        ttl_seconds: int = 43200,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.max_items = max(1, max_items)
        self.ttl_seconds = max(1, ttl_seconds)
        self._clock = clock or time.time
        self._lock = RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    last_seen REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_sessions_last_seen ON sessions(last_seen)")

    def get(self, session_id: str) -> SessionState:
        now = self._clock()
        with self._lock:
            self.prune_expired(now)
            row = self._fetch_session(session_id)
            if row is None:
                self.evict_oldest_if_full()
                state = SessionState(session_id=session_id)
            else:
                state = self._load_state(session_id, row[0])

            self._upsert_state(state, now)
            return state

    def save(self, state: SessionState) -> None:
        with self._lock:
            self.prune_expired(self._clock())
            self._upsert_state(state, self._clock())

    def delete(self, session_id: str) -> None:
        with self._lock:
            with self._connect() as connection:
                connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

    def list_recent(self, limit: int = 20) -> list[SessionRecord]:
        now = self._clock()
        with self._lock:
            self.prune_expired(now)
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT session_id, state_json, last_seen, updated_at
                    FROM sessions
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (max(1, limit),),
                ).fetchall()
        records: list[SessionRecord] = []
        for session_id, state_json, last_seen, updated_at in rows:
            records.append(
                SessionRecord(
                    session_id=session_id,
                    state=self._load_state(session_id, state_json),
                    last_seen=float(last_seen),
                    updated_at=float(updated_at),
                )
            )
        return records

    def count(self) -> int:
        with self._lock:
            self.prune_expired(self._clock())
            with self._connect() as connection:
                row = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()
        return int(row[0]) if row else 0

    def prune_expired(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE last_seen < ?", (cutoff,))

    def evict_oldest_if_full(self) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()
            if row is None or int(row[0]) < self.max_items:
                return
            oldest = connection.execute(
                "SELECT session_id FROM sessions ORDER BY last_seen ASC LIMIT 1"
            ).fetchone()
            if oldest is not None:
                connection.execute("DELETE FROM sessions WHERE session_id = ?", (oldest[0],))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _fetch_session(self, session_id: str) -> tuple[str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return row

    def _load_state(self, session_id: str, state_json: str) -> SessionState:
        try:
            state = SessionState.model_validate_json(state_json)
        except ValueError:
            return SessionState(session_id=session_id)
        if state.session_id != session_id:
            return state.model_copy(update={"session_id": session_id})
        return state

    def _upsert_state(self, state: SessionState, last_seen: float) -> None:
        now = self._clock()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (session_id, state_json, last_seen, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    last_seen = excluded.last_seen,
                    updated_at = excluded.updated_at
                """,
                (state.session_id, state.model_dump_json(), last_seen, now),
            )


class RedisSessionStore:
    def __init__(
        self,
        redis_url: str,
        *,
        max_items: int = 500,
        ttl_seconds: int = 43200,
        key_prefix: str = "rag:session",
        clock: Callable[[], float] | None = None,
        client=None,
    ) -> None:
        self.redis_url = redis_url
        self.max_items = max(1, max_items)
        self.ttl_seconds = max(1, ttl_seconds)
        self.key_prefix = key_prefix.rstrip(":")
        self.index_key = f"{self.key_prefix}:index"
        self._clock = clock or time.time
        self.client = client or self._create_client(redis_url)

    def get(self, session_id: str) -> SessionState:
        raw = self.client.get(self._key(session_id))
        if raw is None:
            state = SessionState(session_id=session_id)
        else:
            state = self._load_state(session_id, raw)
        self._touch(state)
        return state

    def save(self, state: SessionState) -> None:
        self._touch(state)

    def delete(self, session_id: str) -> None:
        self.client.delete(self._key(session_id))
        self.client.zrem(self.index_key, session_id)

    def list_recent(self, limit: int = 20) -> list[SessionRecord]:
        self._prune_stale_index_entries()
        raw_items = self.client.zrange(self.index_key, 0, -1)
        session_ids = [self._decode(item) for item in raw_items]
        session_ids.reverse()

        records: list[SessionRecord] = []
        for session_id in session_ids[: max(1, limit)]:
            raw = self.client.get(self._key(session_id))
            if raw is None:
                continue
            state = self._load_state(session_id, raw)
            updated_at = float(self.client.zscore(self.index_key, session_id) or 0)
            records.append(
                SessionRecord(
                    session_id=session_id,
                    state=state,
                    last_seen=updated_at,
                    updated_at=updated_at,
                )
            )
        return records

    def count(self) -> int:
        self._prune_stale_index_entries()
        return int(self.client.zcard(self.index_key))

    def _touch(self, state: SessionState) -> None:
        now = self._clock()
        self.client.setex(self._key(state.session_id), self.ttl_seconds, state.model_dump_json())
        self.client.zadd(self.index_key, {state.session_id: now})
        self._evict_oldest_if_full()

    def _evict_oldest_if_full(self) -> None:
        while int(self.client.zcard(self.index_key)) > self.max_items:
            popped = self.client.zpopmin(self.index_key, 1)
            if not popped:
                return
            session_id = self._decode(popped[0][0])
            self.client.delete(self._key(session_id))

    def _prune_stale_index_entries(self) -> None:
        for raw_session_id in self.client.zrange(self.index_key, 0, -1):
            session_id = self._decode(raw_session_id)
            if not self.client.exists(self._key(session_id)):
                self.client.zrem(self.index_key, session_id)

    def _key(self, session_id: str) -> str:
        return f"{self.key_prefix}:{session_id}"

    def _load_state(self, session_id: str, raw: str | bytes) -> SessionState:
        payload = self._decode(raw)
        try:
            state = SessionState.model_validate_json(payload)
        except ValueError:
            return SessionState(session_id=session_id)
        if state.session_id != session_id:
            return state.model_copy(update={"session_id": session_id})
        return state

    def _decode(self, value) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    def _create_client(self, redis_url: str):
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError(
                "SESSION_BACKEND=redis requires the redis package. "
                "Install server requirements before starting the service."
            ) from exc
        return redis.Redis.from_url(redis_url, decode_responses=True)


def product_type_values(filters: list[FilterCondition]) -> list[str]:
    values: list[str] = []
    for item in filters:
        if item.kind == "product_type" and item.value not in values:
            values.append(item.value)
    return values


def product_scope_values(filters: list[FilterCondition]) -> list[str]:
    values: list[str] = []
    for item in filters:
        if item.kind not in {"product_type", "category"}:
            continue
        value = f"{item.kind}:{item.value}"
        if item.value and value not in values:
            values.append(value)
    return values
