"""
答辩重点 🟠（工具注册）
任务：Day 2 任务 2.6 — 理解 ToolRegistry 与 ProductSearchTool。
核心：ToolRegistry 注册 search_products / compare_products / manage_cart；
      Handler 通过 registry 调用工具，新增工具不改动编排逻辑。
高频追问：
  - “新增一个‘查看订单’功能需要改哪些文件？”
    → 实现 Tool → 在 registry 注册 → Handler 中使用，workflow/orchestrator 不变
  - “Tool 抽象的意义？” → 解耦 Agent 编排与具体能力
"""

from typing import Protocol


class Tool(Protocol):
    name: str

    def run(self, **kwargs):
        ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def execute(self, name: str, **kwargs):
        if name not in self._tools:
            raise KeyError(f"Tool not registered: {name}")
        return self._tools[name].run(**kwargs)

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Tool not registered: {name}")
        return self._tools[name]
