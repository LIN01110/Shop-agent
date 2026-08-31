"""
Purpose: 会话状态测试：验证 SessionState 字段和持久化。
"""

from server.session.state import FilterCondition, RedisSessionStore, SQLiteSessionStore, SessionState, SessionStore


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.sorted_sets.setdefault(key, {}).update(mapping)

    def zcard(self, key: str) -> int:
        return len(self.sorted_sets.get(key, {}))

    def zpopmin(self, key: str, count: int) -> list[tuple[str, float]]:
        items = sorted(self.sorted_sets.get(key, {}).items(), key=lambda item: item[1])[:count]
        for member, _ in items:
            self.sorted_sets[key].pop(member, None)
        return items

    def delete(self, key: str) -> None:
        self.values.pop(key, None)

    def zrange(self, key: str, start: int, end: int) -> list[str]:
        items = [member for member, _ in sorted(self.sorted_sets.get(key, {}).items(), key=lambda item: item[1])]
        if end == -1:
            return items[start:]
        return items[start : end + 1]

    def exists(self, key: str) -> bool:
        return key in self.values

    def zrem(self, key: str, member: str) -> None:
        self.sorted_sets.get(key, {}).pop(member, None)

    def zscore(self, key: str, member: str) -> float | None:
        return self.sorted_sets.get(key, {}).get(member)


def test_session_resets_product_scoped_filters_when_product_type_changes() -> None:
    session = SessionState(session_id="pytest-session")
    session.merge_filters(
        [
            FilterCondition(kind="product_type", value="beauty.eye_cream"),
            FilterCondition(kind="keyword", value="眼霜"),
            FilterCondition(kind="max_price", value="250"),
            FilterCondition(kind="exclude", value="科颜氏"),
        ]
    )

    session.merge_filters(
        [
            FilterCondition(kind="product_type", value="electronics.phone"),
            FilterCondition(kind="keyword", value="手机"),
        ]
    )

    values = {(item.kind, item.value) for item in session.filters}
    assert values == {("product_type", "electronics.phone"), ("keyword", "手机")}
    assert session.exclusions == []


def test_session_resets_keyword_only_product_scope_when_explicit_product_type_arrives() -> None:
    session = SessionState(session_id="pytest-session")
    session.merge_filters(
        [
            FilterCondition(kind="keyword", value="\u9632\u6652"),
            FilterCondition(kind="exclude", value="\u6cb9\u817b"),
        ]
    )
    session.candidate_products = ["p_beauty_006"]
    session.candidate_product_cards = [{"id": "p_beauty_006"}]
    session.pending_subject = "\u9632\u6652"

    session.merge_filters([FilterCondition(kind="product_type", value="clothes.sports_shoes")])

    values = {(item.kind, item.value) for item in session.filters}
    assert values == {("product_type", "clothes.sports_shoes")}
    assert session.exclusions == []
    assert session.candidate_products == []
    assert session.candidate_product_cards == []
    assert session.pending_subject == ""


def test_session_resets_same_product_type_scope_when_explicit_product_type_arrives() -> None:
    session = SessionState(session_id="pytest-session")
    session.merge_filters(
        [
            FilterCondition(kind="product_type", value="clothes.sports_shoes"),
            FilterCondition(kind="max_price", value="200"),
        ]
    )
    session.candidate_products = []
    session.candidate_product_cards = []

    session.merge_filters([FilterCondition(kind="product_type", value="clothes.sports_shoes")])

    values = {(item.kind, item.value) for item in session.filters}
    assert values == {("product_type", "clothes.sports_shoes")}


def test_session_resets_product_scope_when_explicit_category_arrives() -> None:
    session = SessionState(session_id="pytest-session")
    session.merge_filters(
        [
            FilterCondition(kind="product_type", value="clothes.sports_shoes"),
            FilterCondition(kind="max_price", value="200"),
        ]
    )
    session.candidate_products = ["p_clothes_001"]
    session.candidate_product_cards = [{"id": "p_clothes_001"}]

    session.merge_filters([FilterCondition(kind="category", value="beauty.skincare")])

    values = {(item.kind, item.value) for item in session.filters}
    assert values == {("category", "beauty.skincare")}
    assert session.candidate_products == []
    assert session.candidate_product_cards == []


def test_session_keeps_filters_for_contextual_follow_up_without_new_product_type() -> None:
    session = SessionState(session_id="pytest-session")
    session.merge_filters(
        [
            FilterCondition(kind="product_type", value="beauty.eye_cream"),
            FilterCondition(kind="keyword", value="眼霜"),
        ]
    )

    session.merge_filters([FilterCondition(kind="keyword", value="敏感肌")])

    values = {(item.kind, item.value) for item in session.filters}
    assert ("product_type", "beauty.eye_cream") in values
    assert ("keyword", "眼霜") in values
    assert ("keyword", "敏感肌") in values


def test_session_store_prunes_expired_sessions() -> None:
    now = 1000.0

    def clock() -> float:
        return now

    store = SessionStore(max_items=5, ttl_seconds=10, clock=clock)
    store.get("old")

    now = 1011.0
    store.get("new")

    assert store.count() == 1


def test_session_store_evicts_oldest_session_when_full() -> None:
    now = 1000.0

    def clock() -> float:
        return now

    store = SessionStore(max_items=2, ttl_seconds=100, clock=clock)
    store.get("first")
    now = 1001.0
    store.get("second")
    now = 1002.0
    store.get("third")

    assert store.count() == 2
    assert "first" not in store._sessions
    assert "second" in store._sessions
    assert "third" in store._sessions


def test_session_store_deletes_session() -> None:
    store = SessionStore(max_items=5, ttl_seconds=100)
    session = store.get("to-delete")
    session.cart.append({"product_id": "p_beauty_021", "quantity": 1})
    store.save(session)

    store.delete("to-delete")

    assert store.get("to-delete").cart == []


def test_session_store_lists_recent_sessions() -> None:
    now = 1000.0

    def clock() -> float:
        return now

    store = SessionStore(max_items=5, ttl_seconds=1000, clock=clock)
    first = store.get("first")
    first.add_user_message("first message")
    store.save(first)
    now = 1001.0
    second = store.get("second")
    second.add_user_message("second message")
    store.save(second)

    records = store.list_recent()

    assert [record.session_id for record in records] == ["second", "first"]
    assert records[0].state.history[0].content == "second message"


def test_sqlite_session_store_persists_cart_between_instances(tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    store = SQLiteSessionStore(db_path, max_items=10, ttl_seconds=1000)
    session = store.get("persisted")
    session.cart.append(
        {
            "product_id": "p_beauty_021",
            "name": "科颜氏牛油果保湿眼霜",
            "price": 210,
            "quantity": 2,
        }
    )
    session.candidate_product_cards = [{"id": "p_beauty_021", "name": "科颜氏牛油果保湿眼霜"}]
    store.save(session)

    reloaded_store = SQLiteSessionStore(db_path, max_items=10, ttl_seconds=1000)
    reloaded = reloaded_store.get("persisted")

    assert reloaded.cart[0]["product_id"] == "p_beauty_021"
    assert reloaded.cart[0]["quantity"] == 2
    assert reloaded.candidate_product_cards[0]["id"] == "p_beauty_021"


def test_sqlite_session_store_deletes_session(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite3", max_items=10, ttl_seconds=1000)
    session = store.get("to-delete")
    session.cart.append({"product_id": "p_beauty_021", "quantity": 1})
    store.save(session)

    store.delete("to-delete")

    assert store.get("to-delete").cart == []


def test_sqlite_session_store_lists_recent_sessions(tmp_path) -> None:
    now = 1000.0

    def clock() -> float:
        return now

    store = SQLiteSessionStore(tmp_path / "sessions.sqlite3", max_items=10, ttl_seconds=1000, clock=clock)
    first = store.get("first")
    first.add_user_message("first message")
    store.save(first)
    now = 1001.0
    second = store.get("second")
    second.add_user_message("second message")
    store.save(second)

    records = store.list_recent()

    assert [record.session_id for record in records] == ["second", "first"]
    assert records[0].state.history[0].content == "second message"


def test_sqlite_session_store_prunes_expired_sessions(tmp_path) -> None:
    now = 1000.0

    def clock() -> float:
        return now

    store = SQLiteSessionStore(tmp_path / "sessions.sqlite3", max_items=5, ttl_seconds=10, clock=clock)
    store.get("old")
    now = 1011.0
    store.get("new")

    assert store.count() == 1


def test_sqlite_session_store_evicts_oldest_session_when_full(tmp_path) -> None:
    now = 1000.0

    def clock() -> float:
        return now

    store = SQLiteSessionStore(tmp_path / "sessions.sqlite3", max_items=2, ttl_seconds=100, clock=clock)
    store.get("first")
    now = 1001.0
    store.get("second")
    now = 1002.0
    store.get("third")

    assert store.count() == 2
    assert store.get("second").session_id == "second"
    assert store.get("third").session_id == "third"


def test_redis_session_store_persists_cart_between_instances() -> None:
    client = FakeRedis()
    store = RedisSessionStore("redis://example", client=client)
    session = store.get("redis-session")
    session.cart.append({"product_id": "p_beauty_021", "name": "科颜氏牛油果保湿眼霜", "quantity": 1})
    store.save(session)

    reloaded_store = RedisSessionStore("redis://example", client=client)
    reloaded = reloaded_store.get("redis-session")

    assert reloaded.cart[0]["product_id"] == "p_beauty_021"


def test_redis_session_store_deletes_session() -> None:
    client = FakeRedis()
    store = RedisSessionStore("redis://example", client=client)
    session = store.get("to-delete")
    session.cart.append({"product_id": "p_beauty_021", "quantity": 1})
    store.save(session)

    store.delete("to-delete")

    assert store.get("to-delete").cart == []


def test_redis_session_store_lists_recent_sessions() -> None:
    now = 1000.0

    def clock() -> float:
        return now

    client = FakeRedis()
    store = RedisSessionStore("redis://example", clock=clock, client=client)
    first = store.get("first")
    first.add_user_message("first message")
    store.save(first)
    now = 1001.0
    second = store.get("second")
    second.add_user_message("second message")
    store.save(second)

    records = store.list_recent()

    assert [record.session_id for record in records] == ["second", "first"]
    assert records[0].state.history[0].content == "second message"


def test_redis_session_store_evicts_oldest_session_when_full() -> None:
    now = 1000.0

    def clock() -> float:
        return now

    client = FakeRedis()
    store = RedisSessionStore("redis://example", max_items=2, clock=clock, client=client)
    store.get("first")
    now = 1001.0
    store.get("second")
    now = 1002.0
    store.get("third")

    assert store.count() == 2
    assert not client.exists("rag:session:first")
    assert client.exists("rag:session:second")
    assert client.exists("rag:session:third")
