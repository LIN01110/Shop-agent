"""
Purpose: 请求治理中间件：RequestGovernanceMiddleware 限制并发数和请求超时，流式路径豁免超时。
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any


class RequestGovernanceMiddleware:
    """ASGI middleware for bounded concurrency and coarse request timeout.

    Streaming chat paths are exempt from the outer timeout because their
    dependency calls have their own budgets and the connection can stay open.
    """

    def __init__(
        self,
        app: Callable,
        *,
        max_concurrent_requests: int = 100,
        request_timeout_seconds: float = 30.0,
        timeout_exempt_paths: tuple[str, ...] = ("/chat", "/ws/chat"),
    ) -> None:
        self.app = app
        self.max_concurrent_requests = max(1, max_concurrent_requests)
        self.request_timeout_seconds = max(0.0, request_timeout_seconds)
        self.timeout_exempt_paths = timeout_exempt_paths
        self._active_requests = 0
        self._lock = asyncio.Lock()

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        scope_type = scope.get("type")
        if scope_type not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        acquired = await self._try_acquire()
        if not acquired:
            await self._reject(scope, send)
            return

        try:
            if self._should_apply_timeout(scope):
                await asyncio.wait_for(
                    self.app(scope, receive, send),
                    timeout=self.request_timeout_seconds,
                )
            else:
                await self.app(scope, receive, send)
        except TimeoutError:
            await self._send_timeout(scope, send)
        finally:
            await self._release()

    async def _try_acquire(self) -> bool:
        async with self._lock:
            if self._active_requests >= self.max_concurrent_requests:
                return False
            self._active_requests += 1
            return True

    async def _release(self) -> None:
        async with self._lock:
            self._active_requests = max(0, self._active_requests - 1)

    def _should_apply_timeout(self, scope: dict[str, Any]) -> bool:
        if self.request_timeout_seconds <= 0:
            return False
        path = str(scope.get("path", ""))
        return not any(path == item or path.startswith(f"{item}/") for item in self.timeout_exempt_paths)

    async def _reject(self, scope: dict[str, Any], send: Callable) -> None:
        if scope.get("type") == "websocket":
            await send({"type": "websocket.close", "code": 1013, "reason": "server_busy"})
            return
        await send_json_response(
            send,
            status=503,
            payload={"detail": "server_busy", "message": "系统繁忙，请稍后再试。"},
        )

    async def _send_timeout(self, scope: dict[str, Any], send: Callable) -> None:
        if scope.get("type") == "websocket":
            await send({"type": "websocket.close", "code": 1011, "reason": "request_timeout"})
            return
        await send_json_response(
            send,
            status=504,
            payload={"detail": "request_timeout", "message": "请求处理超时，请稍后重试。"},
        )


async def send_json_response(send: Callable, *, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
