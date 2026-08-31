"""
L4 工作记忆：当前任务的状态和中间结果。

职责：
- 记录用户当前在做什么任务（搜索、对比、购物车结算）
- 记录任务的中间状态（正在对比哪几个商品、还差什么信息）
- 任务完成后自动清理

示例场景：
- 用户："帮我对比这三款口红"
  - 工作记忆：task="comparing", items=["p_beauty_001", "p_beauty_002", "p_beauty_003"], stage="waiting_user_choice"
- 用户："第一个加入购物车"
  - 工作记忆：task="add_to_cart", item="p_beauty_001", pending_confirmation=True
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkingMemory:
    """工作记忆：当前任务的状态机。"""
    task_type: str = ""  # "searching", "comparing", "checkout", "idle"
    items: list[str] = field(default_factory=list)  # 涉及的商品 ID
    stage: str = ""  # 任务阶段
    pending_question: str = ""  # 等待用户回答的问题
    context: dict[str, Any] = field(default_factory=dict)  # 任意上下文

    def start_task(self, task_type: str, items: list[str] | None = None, **context: Any) -> None:
        self.task_type = task_type
        self.items = items or []
        self.stage = "started"
        self.pending_question = ""
        self.context = context

    def advance(self, stage: str, pending_question: str = "", **context: Any) -> None:
        self.stage = stage
        self.pending_question = pending_question
        self.context.update(context)

    def clear(self) -> None:
        self.task_type = ""
        self.items = []
        self.stage = ""
        self.pending_question = ""
        self.context = {}

    def is_active(self) -> bool:
        return self.task_type != ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "items": self.items,
            "stage": self.stage,
            "pending_question": self.pending_question,
            "context": self.context,
        }
