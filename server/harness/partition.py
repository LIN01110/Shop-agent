"""上下文分区：ImmutablePrefix / AppendOnlyLog / VolatileScratch

基于 DeepSeek Reasonix 的三分区模型设计：
- Immutable Prefix: system + tool_specs + few_shots，整个 session 不变
- Append-Only Log: 对话历史只追加，从不改写中间
- Volatile Scratch: 临时草稿，轮末丢弃
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol


class Message(Protocol):
    """Message 协议。"""

    role: str
    content: str


@dataclass
class ImmutablePrefix:
    """不可变前缀：system + tool_specs + few_shots。

    整个 session 期间字节级不变。任何变更必须通过显式方法，
    并自动触发 cache invalidation。
    """

    system_prompt: str
    few_shots: list[dict] = field(default_factory=list)
    tool_specs: list[dict] = field(default_factory=list)
    _fingerprint: str = field(default="", repr=False)
    _invalidated: bool = field(default=False, repr=False)

    def __post_init__(self):
        self._compute_fingerprint()

    def to_messages(self) -> list[dict]:
        """生成 provider-visible messages。顺序必须稳定！"""
        msgs = [{"role": "system", "content": self.system_prompt}]
        msgs.extend(self.few_shots)
        for tool in self.tool_specs:
            msgs.append(
                {
                    "role": "system",
                    "content": f"Tool: {tool['name']}\nSchema: {json.dumps(tool['schema'], ensure_ascii=False)}",
                }
            )
        return msgs

    def _compute_fingerprint(self) -> str:
        """计算前缀指纹（SHA-256）。"""
        content = json.dumps(self.to_messages(), ensure_ascii=False, sort_keys=True)
        self._fingerprint = hashlib.sha256(content.encode()).hexdigest()[:16]
        return self._fingerprint

    def verify_fingerprint(self) -> bool:
        """校验指纹是否漂移。

        开发/测试模式下，指纹漂移应被当成 bug 处理（throw）。
        生产模式下返回 bool 供日志记录。
        """
        old = self._fingerprint
        self._compute_fingerprint()
        return old == self._fingerprint

    def add_tool(self, tool: dict) -> None:
        """添加工具 → 前缀变化 → 标记 invalidated。"""
        self.tool_specs.append(tool)
        self._invalidated = True
        self._compute_fingerprint()

    def remove_tool(self, tool_name: str) -> None:
        """移除工具 → 前缀变化 → 标记 invalidated。"""
        self.tool_specs = [t for t in self.tool_specs if t["name"] != tool_name]
        self._invalidated = True
        self._compute_fingerprint()

    def is_invalidated(self) -> bool:
        """前缀是否已被标记为失效（下一轮 cache miss）。"""
        return self._invalidated

    def clear_invalidated(self) -> None:
        """下一轮 cache miss 后清除标记。"""
        self._invalidated = False


@dataclass
class AppendOnlyLog:
    """只追加日志：对话历史永不修改，只在尾部追加。"""

    entries: list[dict] = field(default_factory=list)

    def append(self, role: str, content: str, **metadata) -> None:
        """追加一条消息。"""
        entry = {"role": role, "content": content}
        entry.update(metadata)
        self.entries.append(entry)

    def append_turn(
        self,
        user_msg: str,
        assistant_msg: str,
        tool_results: list[dict] | None = None,
    ) -> None:
        """追加一个完整 turn。"""
        self.append("user", user_msg)
        if tool_results:
            for tr in tool_results:
                self.append(
                    "tool",
                    json.dumps(tr, ensure_ascii=False),
                    name=tr.get("name"),
                )
        self.append("assistant", assistant_msg)

    def to_messages(self) -> list[dict]:
        """生成 provider-visible messages（不含 metadata）。"""
        return [{"role": e["role"], "content": e["content"]} for e in self.entries]

    def token_estimate(self) -> int:
        """粗略估计 token 数。

        中文 1 token ≈ 1 字，英文 1 token ≈ 4 chars。
        这里用 0.6 作为混合语言的粗略系数。
        """
        total_chars = sum(len(e["content"]) for e in self.entries)
        return int(total_chars * 0.6)


@dataclass
class VolatileScratch:
    """易失草稿：轮内使用，轮末丢弃。"""

    reasoning_content: str = ""  # DeepSeek R1 的思考内容
    plan_draft: dict = field(default_factory=dict)
    temp_notes: list[str] = field(default_factory=list)

    def clear(self) -> None:
        """清空所有草稿内容。"""
        self.reasoning_content = ""
        self.plan_draft = {}
        self.temp_notes = []


@dataclass
class ContextPartition:
    """三分区管理器：组合 ImmutablePrefix + AppendOnlyLog + VolatileScratch。"""

    prefix: ImmutablePrefix
    log: AppendOnlyLog = field(default_factory=AppendOnlyLog)
    scratch: VolatileScratch = field(default_factory=VolatileScratch)

    def build_provider_messages(self, include_scratch: bool = False) -> list[dict]:
        """构建发送到 LLM provider 的完整 messages。

        注意：scratch 默认不包含（DeepSeek 的 reasoning_content 也不应发送回去）。
        """
        msgs = self.prefix.to_messages()
        msgs.extend(self.log.to_messages())
        if include_scratch and self.scratch.reasoning_content:
            # 仅在特定调试场景包含 scratch
            msgs.append(
                {
                    "role": "system",
                    "content": f"[scratch] {self.scratch.reasoning_content}",
                }
            )
        return msgs

    def compute_cache_hit_probability(self) -> float:
        """计算理论 cache hit 概率（仅参考）。

        前缀未变 → 前缀部分 100% hit
        追加部分（最后一轮）是新内容，必然 miss
        """
        if self.prefix.is_invalidated():
            return 0.0
        prefix_tokens = len(self.prefix.to_messages()) * 50  # 粗略估计
        log_tokens = self.log.token_estimate()
        if log_tokens == 0:
            return 1.0
        return prefix_tokens / (prefix_tokens + log_tokens)
