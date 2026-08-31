"""
Purpose: FastAPI 应用入口：create_app() 创建应用，注册 CORS、静态文件、路由、生命周期管理。
新增：/metrics 监控端点、/health 健康检查增强、请求耗时埋点。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from server.api.admin import router as admin_router
from server.api.cart_session import router as cart_session_router
from server.api.chat import router as chat_router
from server.api.debug import router as debug_router
from server.api.products import router as products_router
from server.api.sessions import router as sessions_router
from server.api.uploads import router as uploads_router
from server.config import Settings
from server.config import get_settings
from server.app_container import create_commerce_fact_provider, get_orchestrator
from server.commerce.facts import configure_fact_provider
from server.gateway.middleware import RequestGovernanceMiddleware
from server.monitoring.health import get_health_checker
from server.monitoring.metrics import record_request, get_metrics
from server.monitoring.eval_tracker import get_quality_metrics


class MetricsMiddleware(BaseHTTPMiddleware):
    """请求耗时埋点中间件。"""

    async def dispatch(self, request: Request, call_next):
        import time
        started = time.perf_counter()
        response = await call_next(request)
        latency_ms = (time.perf_counter() - started) * 1000

        # 只记录 API 请求，跳过静态文件
        path = request.url.path
        if path.startswith("/assets/"):
            return response

        cache_hit = response.headers.get("X-Cache") == "HIT"
        error = response.status_code >= 500

        record_request(
            latency_ms=latency_ms,
            cache_hit=cache_hit,
            error=error,
            error_component="http" if error else "",
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    get_orchestrator()

    if settings.enable_user_system:
        from server.database.mysql_client import init_db, close_db
        from server.database.redis_client import close_redis
        from server.commerce.mysql_bridge import refresh_commerce_cache
        try:
            await init_db()
            await refresh_commerce_cache()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "User system DB init failed: %s", exc
            )

    yield

    if settings.enable_user_system:
        try:
            await close_db()
            await close_redis()
        except Exception:
            pass


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_fact_provider(create_commerce_fact_provider(settings))
    app = FastAPI(title="RAG Shopping Agent", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        RequestGovernanceMiddleware,
        max_concurrent_requests=settings.max_concurrent_requests,
        request_timeout_seconds=settings.request_timeout_seconds,
    )

    app.add_middleware(MetricsMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_credentials_enabled,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(chat_router)
    app.include_router(cart_session_router)
    if settings.enable_user_system:
        from server.api.auth import router as auth_router
        from server.api.cart_db import router as cart_db_router
        from server.api.coupons import router as coupons_router
        from server.api.orders import router as orders_router
        from server.api.user import router as user_router

        app.include_router(auth_router)
        app.include_router(cart_db_router)
        app.include_router(orders_router)
        app.include_router(coupons_router)
        app.include_router(user_router)
        app.mount("/login", StaticFiles(directory="server/static", html=True), name="login")
    app.include_router(products_router)
    app.include_router(sessions_router)
    app.include_router(uploads_router)
    if settings.debug_api_enabled:
        app.include_router(debug_router)
    if settings.admin_console_enabled:
        app.include_router(admin_router)
    settings.product_image_path.mkdir(parents=True, exist_ok=True)
    settings.upload_image_path.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/assets/products",
        StaticFiles(directory=settings.product_image_path, check_dir=False),
        name="product_images",
    )

    @app.get("/health")
    async def health() -> dict:
        checker = get_health_checker()
        base = {
            "status": "ok",
            "env": settings.app_env,
            "debug_api_enabled": settings.debug_api_enabled,
            "session_backend": settings.normalized_session_backend,
        }
        if settings.enable_metrics:
            base.update(checker.to_dict())
        return base

    @app.get("/metrics")
    async def metrics() -> dict:
        if not settings.enable_metrics:
            return {"enabled": False}
        snapshot = get_metrics().snapshot()
        return snapshot.to_dict()

    @app.get("/metrics/quality")
    async def quality_metrics() -> dict:
        if not settings.enable_metrics:
            return {"enabled": False}
        snapshot = get_quality_metrics().snapshot()
        return snapshot.to_dict()

    return app


settings = get_settings()
app = create_app(settings)
