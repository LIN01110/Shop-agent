"""
兼容层：server/agent/scenarios.py

原 scenarios.py 已按功能拆分为：
- scenario_catalog.py  — 场景目录管理
- scenario_models.py   — 场景数据模型
- scenario_response.py — 场景响应构建
- scenario_utils.py    — 场景工具函数

本文件保留向后兼容的导入接口，供未迁移的代码引用。
TODO: 逐步将引用迁移到新模块后删除本文件。
"""

from __future__ import annotations

from server.agent.scenario_catalog import (
    ScenarioCatalog,
    detect_scenario_bundle,
    get_default_scenario_catalog,
    has_scenario_match,
    load_scenario_catalog,
)
from server.agent.scenario_models import ScenarioSlot
from server.agent.scenario_response import build_bundle_answer
from server.agent.scenario_utils import render_query_template

__all__ = [
    "ScenarioCatalog",
    "ScenarioSlot",
    "build_bundle_answer",
    "detect_scenario_bundle",
    "get_default_scenario_catalog",
    "has_scenario_match",
    "load_scenario_catalog",
    "render_query_template",
]
