"""Harness LLM Client：统一封装 DeepSeek / llama.cpp / Mock 后端。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Protocol

import httpx

from server.harness.partition import ContextPartition


class LLMBackend(Protocol):
    """后端协议：DeepSeek / llama.cpp / Mock 都实现此接口。"""

    async def stream_turn(
        self,
        partition: ContextPartition,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        ...

    def supports_prefix_cache(self) -> bool:
        ...


class DeepSeekBackend:
    """DeepSeek API 后端：原生支持 prefix cache。

    DeepSeek 的自动 prefix cache 按字节前缀匹配：
    - 前缀（system + 历史消息）不变时，cache hit
    - cache hit 后 input token 价格降至 25%（促销期）
    - 只追加新消息可保持高命中率
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        timeout_seconds: float = 45.0,
        retry_attempts: int = 2,
    ) -> None:
        if not api_key:
            raise RuntimeError("DeepSeek API key is required. Set DEEPSEEK_API_KEY in .env")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = httpx.Timeout(timeout_seconds, read=None)
        self.retry_attempts = max(1, retry_attempts)

    def supports_prefix_cache(self) -> bool:
        return True

    async def stream_turn(
        self,
        partition: ContextPartition,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        messages = partition.build_provider_messages()

        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}/chat/completions"

        for attempt in range(1, self.retry_attempts + 1):
            yielded = False
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    async with client.stream(
                        "POST",
                        url,
                        headers=headers,
                        json=payload,
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            token = _parse_sse_line(line)
                            if token is not None:
                                yielded = True
                                yield token
                return
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                if attempt >= self.retry_attempts:
                    raise RuntimeError(f"DeepSeek API failed after {attempt} attempts: {exc}") from exc
                await asyncio.sleep(0.5 * attempt)


class LlamaCppBackend:
    """llama.cpp 本地后端：OpenAI-compatible，但不支持 prefix cache。

    适合离线场景或隐私敏感场景。
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080/v1",
        model: str = "",
        timeout_seconds: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = httpx.Timeout(timeout_seconds, read=None)

    def supports_prefix_cache(self) -> bool:
        return False

    async def stream_turn(
        self,
        partition: ContextPartition,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        messages = partition.build_provider_messages()

        payload: dict[str, object] = {
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if self.model:
            payload["model"] = self.model
        if max_tokens:
            payload["max_tokens"] = max_tokens

        url = f"{self.base_url}/chat/completions"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                url,
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    token = _parse_sse_line(line)
                    if token is not None:
                        yield token


class MockBackend:
    """Mock 后端：用于单元测试和离线演示。"""

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or ["Mock response"]
        self.call_count = 0

    def supports_prefix_cache(self) -> bool:
        return False

    async def stream_turn(
        self,
        partition: ContextPartition,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        text = self.responses[self.call_count % len(self.responses)]
        self.call_count += 1
        # 模拟流式：逐字输出
        for char in text:
            yield char
            await asyncio.sleep(0.001)


def _parse_sse_line(line: str) -> str | None:
    """解析 OpenAI SSE 流式响应行。"""
    if not line.startswith("data:"):
        return None
    data = line.removeprefix("data:").strip()
    if not data or data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
        choices = payload.get("choices") or []
        if not choices:
            return None
        delta = choices[0].get("delta") or {}
        return delta.get("content") or ""
    except json.JSONDecodeError:
        return None


class HarnessLLMClient:
    """Harness 层统一 LLM 客户端。

    职责：
    1. 代理 backend 的流式调用
    2. 统计 cache hit 率（仅参考）
    3. 统一错误处理
    """

    def __init__(self, backend: LLMBackend) -> None:
        self.backend = backend
        self.total_turns = 0
        self.cache_hits = 0

    async def stream_turn(
        self,
        partition: ContextPartition,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        self.total_turns += 1

        # 统计 cache hit（仅理论估计）
        if self.backend.supports_prefix_cache() and not partition.prefix.is_invalidated():
            self.cache_hits += 1

        async for token in self.backend.stream_turn(partition, temperature, max_tokens):
            yield token

    def get_cache_hit_rate(self) -> float:
        """获取 cache hit 率（0.0 ~ 1.0）。"""
        if self.total_turns == 0:
            return 0.0
        return self.cache_hits / self.total_turns

    def get_metrics(self) -> dict:
        """获取完整指标。"""
        return {
            "total_turns": self.total_turns,
            "cache_hits": self.cache_hits,
            "cache_hit_rate": self.get_cache_hit_rate(),
            "backend_supports_cache": self.backend.supports_prefix_cache(),
        }
