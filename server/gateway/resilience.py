"""
Purpose: 弹性设计：重试、降级、熔断等弹性原语。
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar


T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 2
    base_delay_seconds: float = 0.15
    max_delay_seconds: float = 1.0

    def delay_for_attempt(self, attempt_index: int) -> float:
        delay = self.base_delay_seconds * (2 ** max(0, attempt_index - 1))
        return min(delay, self.max_delay_seconds)


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        reset_seconds: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.reset_seconds = max(0.1, reset_seconds)
        self._clock = clock or time.monotonic
        self._failure_count = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    async def before_call(self) -> None:
        async with self._lock:
            if self._opened_at is None:
                return
            if self._clock() - self._opened_at >= self.reset_seconds:
                self._failure_count = 0
                self._opened_at = None
                return
            raise CircuitOpenError("Circuit breaker is open.")

    def before_call_sync(self) -> None:
        # 同步版本：简单检查，不竞争锁（VLM 客户端单线程调用）
        if self._opened_at is None:
            return
        if self._clock() - self._opened_at >= self.reset_seconds:
            self._failure_count = 0
            self._opened_at = None
            return
        raise CircuitOpenError("Circuit breaker is open.")

    async def record_success(self) -> None:
        async with self._lock:
            self._failure_count = 0
            self._opened_at = None

    def record_success_sync(self) -> None:
        self._failure_count = 0
        self._opened_at = None

    async def record_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._opened_at = self._clock()

    def record_failure_sync(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self.failure_threshold:
            self._opened_at = self._clock()

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None


async def run_with_timeout(
    operation: Callable[[], Awaitable[T]],
    *,
    timeout_seconds: float,
) -> T:
    if timeout_seconds <= 0:
        return await operation()
    return await asyncio.wait_for(operation(), timeout=timeout_seconds)


async def retry_async_operation(
    operation: Callable[[], Awaitable[T]],
    *,
    retry_policy: RetryPolicy,
    retryable_exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    attempts = max(1, retry_policy.attempts)
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except retryable_exceptions as exc:
            last_error = exc
            if attempt >= attempts:
                break
            await asyncio.sleep(retry_policy.delay_for_attempt(attempt))
    assert last_error is not None
    raise last_error
