"""AgentHarness：Harness 主控，管理 session 生命周期。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from server.harness.compactor import CompactionResult, SessionCompactor
from server.harness.llm_client import HarnessLLMClient
from server.harness.partition import ContextPartition, ImmutablePrefix


@dataclass
class TurnResult:
    """一次 turn 的结果。"""

    text: str
    handler_name: str
    latency_ms: float
    cache_hit: bool
    tokens_used: int
    compaction: CompactionResult | None = None


class AgentHarness:
    """Agent Harness 主控。

    职责：
    1. 管理 ContextPartition（三分区）
    2. 代理 LLM 调用（通过 HarnessLLMClient）
    3. 长 session 压缩
    4. 指标暴露
    """

    def __init__(
        self,
        llm_client: HarnessLLMClient,
        system_prompt: str,
        few_shots: list[dict] | None = None,
        compactor: SessionCompactor | None = None,
        on_cache_miss: Callable[[str], None] | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.compactor = compactor or SessionCompactor()
        self.on_cache_miss = on_cache_miss

        # 初始化三分区
        prefix = ImmutablePrefix(
            system_prompt=system_prompt,
            few_shots=few_shots or [],
        )
        self.partition = ContextPartition(prefix=prefix)

        # 指标
        self.session_start_time = time.time()
        self.turn_count = 0
        self.compaction_count = 0

    async def stream_turn(
        self,
        user_message: str,
        handler_fn: Callable[[ContextPartition, HarnessLLMClient], AsyncIterator[str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """执行一次 turn，流式输出 token。

        这是核心入口：Orchestrator 每轮调用此方法来执行用户请求。
        """
        self.turn_count += 1
        start = time.time()

        # 1. 检查是否需要压缩
        compaction = self.compactor.maybe_compact(
            self.partition.log, self.partition.prefix
        )
        if compaction:
            self.compaction_count += 1

        # 2. 追加用户消息到 Append-Only Log
        self.partition.log.append("user", user_message)

        # 3. 调用 handler（handler 内部通过 llm_client 流式输出）
        text_parts: list[str] = []
        async for token in handler_fn(self.partition, self.llm_client):
            text_parts.append(token)
            yield token

        text = "".join(text_parts)

        # 4. 追加 assistant 回复到 Append-Only Log
        self.partition.log.append("assistant", text)

        # 5. 清理 scratch（轮末丢弃）
        self.partition.scratch.clear()

        # 6. 如果 prefix 被标记 invalidated，miss 一次后清除标记
        if self.partition.prefix.is_invalidated():
            if self.on_cache_miss:
                self.on_cache_miss("prefix_invalidation")
            self.partition.prefix.clear_invalidated()

    async def execute_turn(
        self,
        user_message: str,
        handler_fn: Callable[[ContextPartition, HarnessLLMClient], AsyncIterator[str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> TurnResult:
        """执行一次 turn，返回完整 TurnResult（非流式）。

        用于需要完整结果的场景（如测试、后台任务）。
        """
        start = time.time()
        text_parts: list[str] = []

        async for token in self.stream_turn(
            user_message, handler_fn, temperature, max_tokens
        ):
            text_parts.append(token)

        text = "".join(text_parts)
        latency = (time.time() - start) * 1000

        # 判断 cache hit（理论值）
        cache_hit = self.llm_client.backend.supports_prefix_cache() and not (
            self.compaction_count > 0 and self.turn_count == 1
        )

        return TurnResult(
            text=text,
            handler_name=handler_fn.__name__,
            latency_ms=latency,
            cache_hit=cache_hit,
            tokens_used=self.partition.log.token_estimate(),
        )

    def get_metrics(self) -> dict[str, Any]:
        """获取 harness 完整指标。"""
        return {
            "turn_count": self.turn_count,
            "compaction_count": self.compaction_count,
            "cache_hit_rate": self.llm_client.get_cache_hit_rate(),
            "session_duration_seconds": int(time.time() - self.session_start_time),
            "log_tokens": self.partition.log.token_estimate(),
            "prefix_fingerprint": self.partition.prefix._fingerprint,
            "prefix_invalidated": self.partition.prefix.is_invalidated(),
            "backend": self.llm_client.backend.__class__.__name__,
            "backend_supports_cache": self.llm_client.backend.supports_prefix_cache(),
        }
