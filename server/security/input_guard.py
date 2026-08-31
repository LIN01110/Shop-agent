"""
输入安全守卫：Prompt Injection 检测与拦截。

双层防御：
  Layer 1: 正则关键词匹配（内置常见攻击模板）
  Layer 2: Embedding 相似度检测（预留接口，需注入 embedding_client）

使用方式：
    from server.security.input_guard import InputGuard
    guard = InputGuard(enabled=True)
    result = guard.check(user_message)
    if not result.safe:
        # 拦截，返回通用拒绝响应
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# 已知攻击模式（正则，不区分大小写）
ATTACK_PATTERNS = [
    # 中文攻击模式
    r"忽略.*指令",
    r"忽略.*提示词",
    r"忽略之前.*说",
    r"忽略.*规则",
    r"不要.*遵守",
    r"解除.*限制",
    r"关闭.*过滤",
    r"你是.*怎么.*设置",
    r"告诉我.*内部",
    r"你的.*提示词",
    r"你的.*system",
    r"绕过.*限制",
    r"突破.*约束",
    r"假装.*你是",
    r"现在.*你是",
    r"进入.*模式",
    r"开启.*模式",
    r"忘掉.*设定",
    r"重置.*人格",
    # 英文攻击模式
    r"system\s*prompt",
    r"ignore\s*previous",
    r"ignore\s*the\s*above",
    r"ignore\s*your\s*instructions",
    r"forget\s*your\s*instructions",
    r"forget\s*everything",
    r"you\s*are\s*now",
    r"pretend\s*to\s*be",
    r"act\s*as\s*if",
    r"DAN\s*mode",
    r"jailbreak",
    r"developer\s*mode",
    r"disable\s*safety",
    r"turn\s*off\s*filter",
    r"bypass\s*restriction",
    r"ignore\s*restrictions",
    r"no\s*limit",
    r"unrestricted",
    r"do\s*anything",
    r"no\s*rules",
]


@dataclass(frozen=True)
class GuardResult:
    """输入安检结果。"""

    safe: bool
    reason: str = ""
    action: str = "pass"  # pass / block / warn


class InputGuard:
    """Prompt Injection 输入安全守卫。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        similarity_threshold: float = 0.85,
        embedding_client: Any | None = None,
    ) -> None:
        self.enabled = enabled
        self.similarity_threshold = similarity_threshold
        self.embedding_client = embedding_client
        self._patterns = [re.compile(p, re.IGNORECASE) for p in ATTACK_PATTERNS]

    def check(self, text: str) -> GuardResult:
        """检查输入文本是否包含 Prompt Injection 攻击模式。

        Args:
            text: 用户输入文本

        Returns:
            GuardResult: safe=True 表示通过，safe=False 表示拦截
        """
        if not self.enabled or not text or not text.strip():
            return GuardResult(safe=True)

        # Layer 1: 正则关键词匹配
        matched = self._regex_check(text)
        if not matched.safe:
            return matched

        # Layer 2: Embedding 相似度（预留，需要异步 embedding_client）
        # 同步接口暂不实现，避免阻塞主流程
        # 未来可通过注入 embedding_client 启用

        return GuardResult(safe=True)

    def _regex_check(self, text: str) -> GuardResult:
        """Layer 1: 正则匹配检测。"""
        for pattern in self._patterns:
            if pattern.search(text):
                return GuardResult(
                    safe=False,
                    reason="检测到潜在攻击模式",
                    action="block",
                )
        return GuardResult(safe=True)
