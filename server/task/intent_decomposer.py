"""
意图拆解器：把用户的多意图 query 拆成独立任务。

示例：
- 输入："帮我找红色连衣裙，顺便分析一下最近口红什么色号流行"
- 输出：[Task(type="product_search", query="红色连衣裙"), Task(type="trend_analysis", query="口红色号流行")]

当前实现：基于规则 + 关键词分句，未来可升级为 LLM 拆解。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Task:
    task_type: str  # "product_search", "compare", "trend_analysis", "general_qa"
    query: str
    priority: int = 0  # 0=并行，1=先执行，2=后执行
    metadata: dict[str, Any] | None = None


class IntentDecomposer:
    """意图拆解器。"""

    # 分隔词：遇到这些词，前后拆成不同任务
    SPLIT_KEYWORDS = ["顺便", "还有", "另外", "以及", "同时", "然后", "接着", "并且"]

    # 任务类型关键词映射
    TASK_PATTERNS = [
        ("compare", r"对比|比较|哪个好|vs|versus|差别|区别"),
        ("trend_analysis", r"流行|趋势|最近.*火|热门|推荐|什么.*好"),
        ("product_search", r"找|买|搜|推荐.*给我|有没有|想要|需要"),
        ("general_qa", r".*"),  # 兜底
    ]

    def decompose(self, user_message: str) -> list[Task]:
        """把用户消息拆成多个任务。"""
        # 1. 按分隔词分句
        segments = self._split_message(user_message)
        if len(segments) <= 1:
            # 单意图，直接分类
            return [self._classify_segment(user_message)]

        # 2. 每句分类
        tasks = []
        for seg in segments:
            seg = seg.strip()
            if seg:
                tasks.append(self._classify_segment(seg))
        return tasks

    def _split_message(self, message: str) -> list[str]:
        """按分隔词拆分。"""
        pattern = "(" + "|".join(re.escape(k) for k in self.SPLIT_KEYWORDS) + ")"
        parts = re.split(pattern, message)
        # 合并：分隔词归属后一段
        result = []
        current = parts[0] if parts else ""
        i = 1
        while i < len(parts):
            if parts[i] in self.SPLIT_KEYWORDS:
                result.append(current.strip())
                current = parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")
                i += 2
            else:
                current += parts[i]
                i += 1
        if current.strip():
            result.append(current.strip())
        return result if len(result) > 1 else [message]

    def _classify_segment(self, segment: str) -> Task:
        for task_type, pattern in self.TASK_PATTERNS:
            if re.search(pattern, segment, re.IGNORECASE):
                return Task(task_type=task_type, query=segment)
        return Task(task_type="general_qa", query=segment)
