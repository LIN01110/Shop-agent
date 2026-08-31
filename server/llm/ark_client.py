"""
答辩重点 🟡（LLM 客户端）
任务：Day 3 高频问题 5/15。
核心：封装 Doubao/Ark（OpenAI-compatible）流式调用与降级逻辑；
      处理超时、重试、异常，返回异步迭代 token。
高频追问：
  - “Doubao/Ark 是怎么接入的？”
    → 使用 OpenAI-compatible 接口，兼容 Chat Completions + SSE
  - “LLM 调用失败或超时怎么办？”
    → 捕获异常 / asyncio.timeout → 上层 fallback 或模板回答
"""

import json
import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

import httpx

from server.gateway.resilience import CircuitBreaker, RetryPolicy
from server.llm.prompt import build_grounded_messages

# 评测用途：记录每次流式调用服务端返回的 usage（prompt/completion/total tokens）。
# 生产路径不读取；benchmark 脚本清空后统计。
USAGE_LOG: list[dict] = []


class LLMClient(Protocol):
    async def stream_answer(
        self,
        user_message: str,
        cards: list[dict],
        intent: str,
    ) -> AsyncIterator[str]:
        ...


class ArkChatClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 45.0,
        retry_attempts: int = 2,
        circuit_breaker_failures: int = 3,
        circuit_breaker_reset_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        max_tokens: int = 0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.retry_policy = RetryPolicy(attempts=retry_attempts)
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=circuit_breaker_failures,
            reset_seconds=circuit_breaker_reset_seconds,
        )
        self.transport = transport
        self.max_tokens = max_tokens

    async def stream_answer(
        self,
        user_message: str,
        cards: list[dict],
        intent: str,
    ) -> AsyncIterator[str]:
        messages = build_grounded_messages(user_message=user_message, cards=cards, intent=intent)
        async for token in self.stream_messages(messages):
            yield token

    async def stream_messages(self, messages: list[dict], *, max_tokens: int | None = None) -> AsyncIterator[str]:
        if not self.api_key:
            raise RuntimeError("ARK_API_KEY is not configured.")

        attempts = max(1, self.retry_policy.attempts)
        for attempt in range(1, attempts + 1):
            yielded_token = False
            await self.circuit_breaker.before_call()
            try:
                async for token in self._stream_messages_once(messages, max_tokens=max_tokens):
                    yielded_token = True
                    yield token
                await self.circuit_breaker.record_success()
                return
            except Exception as exc:
                await self.circuit_breaker.record_failure()
                if yielded_token or attempt >= attempts or not is_retryable_llm_error(exc):
                    raise
                await asyncio.sleep(self.retry_policy.delay_for_attempt(attempt))

    async def _stream_messages_once(self, messages: list[dict], *, max_tokens: int | None = None) -> AsyncIterator[str]:
        timeout = httpx.Timeout(self.timeout_seconds, read=None)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # 应用 max_tokens 限制
        effective_max = max_tokens if max_tokens is not None else self.max_tokens
        if effective_max > 0:
            payload["max_tokens"] = effective_max
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data:") and '"usage"' in line:
                        try:
                            chunk = json.loads(line[len("data:"):].strip())
                            if chunk.get("usage"):
                                USAGE_LOG.append(chunk["usage"])
                        except (ValueError, AttributeError):
                            pass
                    token = parse_openai_sse_line(line)
                    if token:
                        yield token


def is_retryable_llm_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or status_code >= 500
    return False


def parse_openai_sse_line(line: str) -> str:
    if not line.startswith("data:"):
        return ""

    data = line.removeprefix("data:").strip()
    if not data or data == "[DONE]":
        return ""

    payload = json.loads(data)
    choices = payload.get("choices") or []
    if not choices:
        return ""

    delta = choices[0].get("delta") or {}
    return delta.get("content") or ""
