"""
结果合并器：把多个任务的执行结果合并成一条连贯的回复。

策略：
- 相同类型的结果合并（如多个商品搜索结果合并为一个列表）
- 不同类型的结果分段展示
- 失败的任务简要提及或忽略
"""

from __future__ import annotations

from typing import Any

from server.task.intent_decomposer import Task
from server.task.task_scheduler import TaskResult


class ResultMerger:
    """结果合并器。"""

    def merge(self, results: list[TaskResult]) -> dict[str, Any]:
        """合并多路结果。"""
        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]

        merged: dict[str, Any] = {
            "has_multiple_results": len(successes) > 1,
            "sections": [],
            "failed_tasks": [],
        }

        # 按任务类型分组
        by_type: dict[str, list[TaskResult]] = {}
        for r in successes:
            by_type.setdefault(r.task.task_type, []).append(r)

        # 商品搜索：合并为一个列表
        if "product_search" in by_type:
            all_hits = []
            for r in by_type["product_search"]:
                if isinstance(r.result, dict) and "hits" in r.result:
                    all_hits.extend(r.result["hits"])
                elif isinstance(r.result, list):
                    all_hits.extend(r.result)
            # 去重
            seen = set()
            unique_hits = []
            for h in all_hits:
                item_id = h.get("id") or h.get("metadata", {}).get("id", "")
                if item_id and item_id not in seen:
                    seen.add(item_id)
                    unique_hits.append(h)
            merged["sections"].append({
                "type": "product_search",
                "title": "搜索结果",
                "items": unique_hits[:10],
            })

        # 趋势分析
        if "trend_analysis" in by_type:
            analyses = []
            for r in by_type["trend_analysis"]:
                if isinstance(r.result, str):
                    analyses.append(r.result)
                elif isinstance(r.result, dict) and "analysis" in r.result:
                    analyses.append(r.result["analysis"])
            merged["sections"].append({
                "type": "trend_analysis",
                "title": "趋势分析",
                "content": "\n".join(analyses),
            })

        # 商品对比
        if "compare" in by_type:
            compares = []
            for r in by_type["compare"]:
                if isinstance(r.result, dict):
                    compares.append(r.result)
            merged["sections"].append({
                "type": "compare",
                "title": "商品对比",
                "comparisons": compares,
            })

        # 失败的任务
        for r in failures:
            merged["failed_tasks"].append({
                "task_type": r.task.task_type,
                "query": r.task.query,
                "error": r.error,
            })

        return merged

    def to_text(self, merged: dict[str, Any]) -> str:
        """把合并结果转为自然语言文本（供 LLM 进一步润色）。"""
        parts = []
        for section in merged.get("sections", []):
            if section["type"] == "product_search":
                items = section.get("items", [])
                if items:
                    parts.append(f"为您找到 {len(items)} 款相关商品：")
                    for i, item in enumerate(items[:5], 1):
                        name = item.get("metadata", {}).get("name", "未知商品")
                        parts.append(f"  {i}. {name}")
            elif section["type"] == "trend_analysis":
                parts.append(f"【{section['title']}】")
                parts.append(section.get("content", ""))
            elif section["type"] == "compare":
                parts.append(f"【{section['title']}】")
                parts.append("已为您整理对比结果。")

        if merged.get("failed_tasks"):
            parts.append("\n（部分请求未能完成，请稍后重试）")

        return "\n".join(parts)
