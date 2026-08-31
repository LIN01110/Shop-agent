"""
答辩重点 🔴（Agent 调度）
任务：Day 1 任务 1.5 — 理解 Handler 分派机制。
核心：AgentWorkflow 按优先级遍历 handlers，第一个 matches() 返回 True 的 handler 执行。
      build_default_workflow() 装配 6 个 Handler 优先级：
      1) ClarificationHandler  2) CartHandler  3) CompareHandler
      4) ScenarioBundleHandler  5) ContextFollowUpHandler  6) RecommendationHandler（兜底）
高频追问：
  - “为什么 ClarificationHandler 排第一？” → 防止宽泛请求直接硬推商品
  - “用户说‘把第二个加到购物车’走哪个 Handler？” → CartHandler，不是 ContextFollowUpHandler
  - “后续想换 LangGraph 怎么做？” → 替换 AgentWorkflow 实现，API 与 Android 协议不变
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from server.agent.intent import UserIntent
from server.agent.semantic_schema import SemanticPlan
from server.llm.ark_client import LLMClient
from server.rag.post_process import SearchFilters
from server.session.state import SessionState
from server.tools.registry import ToolRegistry


@dataclass
class AgentTurnContext:
    """一次 Agent Turn 的完整上下文，Orchestrator 打包后传给 workflow。"""

    trace_id: str
    session_id: str
    message: str          # 用户原始消息（经输入处理后）
    intent: UserIntent    # 用户意图（来自 SemanticPlan）
    query: str            # 改写后的搜索查询
    filters: SearchFilters
    plan: SemanticPlan    # 语义规划结果
    session: SessionState # 会话状态（含历史、过滤条件、候选商品等）
    registry: ToolRegistry
    llm_client: LLMClient | None
    lite_llm_client: LLMClient | None = None  # 小模型客户端（降级用）
    recommendation_llm_budget_seconds: float = 0.0  # LLM 生成预算，0 表示不限制
    retrieval_timeout_seconds: float = 5.0
    selected_handler: str = ""  # workflow 填充，用于 trace/debug
    scenario_match: object | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class AgentHandler(Protocol):
    """Agent Handler 协议：每个 Handler 只需实现 matches + handle。

    新增 Handler 时无需修改 workflow/orchestrator，符合开闭原则。
    """

    def matches(self, context: AgentTurnContext) -> bool:
        ...

    async def handle(self, context: AgentTurnContext) -> AsyncIterator[dict]:
        ...


class AgentWorkflow:
    """Agent 工作流：按优先级串行匹配 Handler，第一个 matches 的执行。"""

    def __init__(self, handlers: Sequence[AgentHandler]) -> None:
        # handlers 顺序即优先级，排在前面的先判断
        self.handlers = list(handlers)

    async def stream(self, context: AgentTurnContext) -> AsyncIterator[dict]:
        # 按优先级遍历：Clarification → Cart → Compare → Bundle → ContextFollowUp → Recommendation
        for handler in self.handlers:
            if handler.matches(context):
                context.selected_handler = handler.__class__.__name__
                async for item in handler.handle(context):
                    yield item
                return
        raise RuntimeError("No agent handler matched the turn.")
