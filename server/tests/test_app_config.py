"""
Purpose: 应用配置测试：验证依赖容器、CORS、Session 存储等配置正确。
"""

from fastapi.middleware.cors import CORSMiddleware

import json

from server.app_container import create_commerce_fact_provider, create_scenario_classifier, create_session_store
from server.commerce.facts import LocalMockCommerceProvider
from server.config import Settings
from server.gateway.middleware import RequestGovernanceMiddleware
from server.main import create_app
from server.session.state import RedisSessionStore, SQLiteSessionStore, SessionStore


def test_production_settings_disable_debug_api_by_default() -> None:
    settings = Settings(_env_file=None, app_env="production", enable_debug_api=None)

    assert settings.debug_api_enabled is False


def test_create_app_omits_debug_routes_in_production() -> None:
    app = create_app(Settings(_env_file=None, app_env="production", enable_debug_api=None))

    paths = {route.path for route in app.routes}

    assert "/chat" in paths
    assert "/debug/traces" not in paths
    assert "/debug/traces/{trace_id}" not in paths


def test_create_app_omits_admin_routes_in_production_by_default() -> None:
    app = create_app(Settings(_env_file=None, app_env="production", enable_admin_console=None))

    paths = {route.path for route in app.routes}

    assert "/admin" not in paths
    assert "/admin/api/overview" not in paths


def test_create_app_can_enable_admin_routes_explicitly() -> None:
    app = create_app(Settings(_env_file=None, app_env="production", enable_admin_console=True))

    paths = {route.path for route in app.routes}

    assert "/admin" in paths
    assert "/admin/api/overview" in paths


def test_create_app_uses_configured_cors_options() -> None:
    settings = Settings(
        _env_file=None,
        cors_allowed_origins="https://shop.example.com,http://localhost:5173",
        cors_allow_credentials=True,
    )

    app = create_app(settings)
    cors_middleware = next(item for item in app.user_middleware if item.cls is CORSMiddleware)

    assert cors_middleware.kwargs["allow_origins"] == [
        "https://shop.example.com",
        "http://localhost:5173",
    ]
    assert cors_middleware.kwargs["allow_credentials"] is True


def test_create_app_installs_request_governance_middleware() -> None:
    settings = Settings(
        _env_file=None,
        max_concurrent_requests=7,
        request_timeout_seconds=3.5,
    )

    app = create_app(settings)
    middleware = next(item for item in app.user_middleware if item.cls is RequestGovernanceMiddleware)

    assert middleware.kwargs["max_concurrent_requests"] == 7
    assert middleware.kwargs["request_timeout_seconds"] == 3.5


def test_wildcard_cors_disables_credentials_even_if_requested() -> None:
    settings = Settings(
        _env_file=None,
        cors_allowed_origins="*",
        cors_allow_credentials=True,
    )

    app = create_app(settings)
    cors_middleware = next(item for item in app.user_middleware if item.cls is CORSMiddleware)

    assert cors_middleware.kwargs["allow_origins"] == ["*"]
    assert cors_middleware.kwargs["allow_credentials"] is False


def test_create_session_store_uses_sqlite_backend_by_default() -> None:
    settings = Settings(_env_file=None)

    store = create_session_store(settings)

    assert isinstance(store, SQLiteSessionStore)


def test_create_session_store_uses_memory_backend_when_configured() -> None:
    settings = Settings(_env_file=None, session_backend="memory")

    store = create_session_store(settings)

    assert isinstance(store, SessionStore)


def test_settings_resolves_scenario_bundle_path(tmp_path) -> None:
    scenario_path = tmp_path / "bundles.json"
    settings = Settings(_env_file=None, scenario_bundle_path=str(scenario_path))

    assert settings.scenario_bundle_file == scenario_path


def test_scenario_embedding_classifier_requires_api_key() -> None:
    settings = Settings(
        _env_file=None,
        use_scenario_embedding=True,
        ark_api_key="",
    )

    classifier = create_scenario_classifier(settings)

    assert classifier.embedding_classifier is None


def test_settings_resolves_commerce_mock_data_path(tmp_path) -> None:
    commerce_path = tmp_path / "commerce.json"
    settings = Settings(_env_file=None, commerce_mock_data_path=str(commerce_path))

    assert settings.commerce_mock_data_file == commerce_path


def test_create_commerce_fact_provider_uses_mock_backend(tmp_path) -> None:
    commerce_path = tmp_path / "commerce.json"
    commerce_path.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "product_id": "p_test",
                        "pricing": {"current_price": 88},
                        "inventory": {"available_qty": 7},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,
        commerce_fact_backend="mock",
        commerce_mock_data_path=str(commerce_path),
    )

    provider = create_commerce_fact_provider(settings)
    product = {"id": "p_test", "price": 100, "stock": 1}

    assert isinstance(provider, LocalMockCommerceProvider)
    assert provider.price(product).value == 88
    assert provider.stock(product).value == 7


def test_create_session_store_uses_sqlite_backend(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        session_backend="sqlite",
        session_db_path=str(tmp_path / "sessions.sqlite3"),
    )

    store = create_session_store(settings)

    assert isinstance(store, SQLiteSessionStore)


def test_create_session_store_uses_redis_backend(monkeypatch) -> None:
    class FakeRedisSessionStore:
        def __init__(self, redis_url, *, max_items, ttl_seconds) -> None:
            self.redis_url = redis_url
            self.max_items = max_items
            self.ttl_seconds = ttl_seconds

    monkeypatch.setattr("server.app_container.RedisSessionStore", FakeRedisSessionStore)
    settings = Settings(
        _env_file=None,
        session_backend="redis",
        session_redis_url="redis://redis.example:6379/1",
    )

    store = create_session_store(settings)

    assert isinstance(store, FakeRedisSessionStore)
    assert store.redis_url == "redis://redis.example:6379/1"
