"""
答辩重点 🟠（Handler 装配）
任务：Day 1 任务 1.5 — 与 workflow.py 一起读。
核心：build_default_workflow() 把 6 个 Handler 按优先级组装成 AgentWorkflow。
      是调度层与业务 Handler 的“胶水”，修改优先级或新增 Handler 从这里入手。
高频追问：
  - “新增一个场景 Handler 需要改哪些文件？”
    → 继承 AgentHandler → 在 default_handlers.py 注册 → 无需改 workflow/orchestrator
"""

from server.agent.commerce_handlers import CartHandler, CompareHandler, ScenarioBundleHandler
from server.agent.conversation_handlers import ClarificationHandler, ContextFollowUpHandler
from server.agent.recommendation_handler import RecommendationHandler
from server.agent.scenarios import ScenarioCatalog
from server.agent.workflow import AgentWorkflow


def build_default_workflow(scenario_catalog: ScenarioCatalog | None = None) -> AgentWorkflow:
    return AgentWorkflow(
        handlers=[
            ClarificationHandler(),
            CartHandler(),
            CompareHandler(),
            ScenarioBundleHandler(scenario_catalog),
            ContextFollowUpHandler(),
            RecommendationHandler(),
        ]
    )
