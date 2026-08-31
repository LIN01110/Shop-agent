"""长 session 压缩：当 Append-Only Log 超过阈值时，自动压缩早期历史。"""

from __future__ import annotations

from dataclasses import dataclass

from server.harness.partition import AppendOnlyLog, ImmutablePrefix


@dataclass
class CompactionResult:
    """压缩结果。"""

    summary: str  # 压缩后的摘要文本
    preserved_turns: int  # 保留的最近 turn 数
    dropped_turns: int  # 被压缩掉的 turn 数


class SessionCompactor:
    """Session 压缩器：将早期对话历史压缩为摘要，减少上下文长度。

    策略：
    1. 当 log token 数超过 max_log_tokens 时触发压缩
    2. 始终保留最近 preserve_recent_turns 个 turn
    3. 被压缩的 turn 生成摘要，追加到 prefix 中
    """

    def __init__(
        self,
        max_log_tokens: int = 6000,
        preserve_recent_turns: int = 4,
    ) -> None:
        self.max_log_tokens = max_log_tokens
        self.preserve_recent_turns = preserve_recent_turns

    def maybe_compact(
        self, log: AppendOnlyLog, prefix: ImmutablePrefix
    ) -> CompactionResult | None:
        """如果需要，压缩 log 并更新 prefix 的 session_summary。

        返回 CompactionResult 表示发生了压缩，返回 None 表示无需压缩。
        """
        current_tokens = log.token_estimate()
        if current_tokens <= self.max_log_tokens:
            return None  # 无需压缩

        # 解析 turn 边界
        turns = self._parse_turns(log)
        if len(turns) <= self.preserve_recent_turns:
            return None  # 历史太短，不压缩

        # 保留最近 N 个 turn，压缩前面的
        to_compress = turns[: -self.preserve_recent_turns]
        to_keep = turns[-self.preserve_recent_turns :]

        summary = self._summarize_turns(to_compress)

        # 重建 log：摘要 + 保留的 turns
        log.entries = []
        log.append("system", f"[会话摘要] {summary}")
        for turn in to_keep:
            for msg in turn:
                log.append(msg["role"], msg["content"])

        # 更新 prefix 的 fingerprint（因为 system prompt 变了）
        prefix.system_prompt = (
            prefix.system_prompt.split("\n[会话摘要]")[0]
            + f"\n[会话摘要] {summary}"
        )
        prefix._compute_fingerprint()
        prefix._invalidated = True  # 标记下一轮 cache miss

        return CompactionResult(
            summary=summary,
            preserved_turns=len(to_keep),
            dropped_turns=len(to_compress),
        )

    def _parse_turns(self, log: AppendOnlyLog) -> list[list[dict]]:
        """将 log 解析为 turn 列表（user → assistant 为一个 turn）。"""
        turns: list[list[dict]] = []
        current_turn: list[dict] = []

        for entry in log.entries:
            if entry["role"] == "user" and current_turn:
                turns.append(current_turn)
                current_turn = []
            current_turn.append(entry)

        if current_turn:
            turns.append(current_turn)

        return turns

    def _summarize_turns(self, turns: list[list[dict]]) -> str:
        """生成 turn 摘要（简单版：统计意图和关键信息）。"""
        intents: list[str] = []
        for turn in turns:
            user_msgs = [t for t in turn if t["role"] == "user"]
            if user_msgs:
                # 取用户消息前 20 字 + "..."
                text = user_msgs[0]["content"][:20]
                if len(user_msgs[0]["content"]) > 20:
                    text += "..."
                intents.append(text)

        return f"共{len(turns)}轮对话：" + " → ".join(intents[:5])
