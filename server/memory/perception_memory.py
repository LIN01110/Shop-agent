"""
L1 感知记忆：最近 3-5 轮对话的上下文窗口。

职责：
- 存储当前 session 的最近对话轮次（user / assistant）
- 提供压缩摘要（当轮次过多时，压缩为一句话摘要）
- 为检索和回复生成提供即时上下文

与缓存的关系：
- 感知记忆是"对话内容"，缓存是"查询结果"
- 检索时：先查缓存（有没有同样的 query 搜过），再读感知记忆（用户刚才说了什么）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConversationTurn:
    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PerceptionMemory:
    """感知记忆：滑动窗口保存最近 N 轮对话。"""
    max_turns: int = 5
    _turns: list[ConversationTurn] = field(default_factory=list, repr=False)

    def add_turn(self, role: str, content: str, **metadata: Any) -> None:
        import time
        self._turns.append(ConversationTurn(
            role=role, content=content, timestamp=time.time(), metadata=metadata
        ))
        if len(self._turns) > self.max_turns:
            self._turns = self._turns[-self.max_turns:]

    def get_recent_turns(self, n: int | None = None) -> list[ConversationTurn]:
        if n is None:
            return list(self._turns)
        return list(self._turns[-n:])

    def get_summary(self) -> str:
        """把最近对话压缩成摘要，供 LLM 上下文使用。"""
        if not self._turns:
            return ""
        parts = []
        for turn in self._turns:
            prefix = "用户" if turn.role == "user" else "助手"
            parts.append(f"{prefix}：{turn.content[:100]}")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_turns": self.max_turns,
            "turns": [
                {"role": t.role, "content": t.content, "timestamp": t.timestamp}
                for t in self._turns
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerceptionMemory:
        mem = cls(max_turns=data.get("max_turns", 5))
        for t in data.get("turns", []):
            mem._turns.append(ConversationTurn(**t))
        return mem
