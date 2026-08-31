"""
健康检查：/health 端点，返回各组件可用状态。

组件列表：
- milvus: Milvus 向量库连接状态
- chroma: Chroma 向量库（如果 Milvus 不可用时的 fallback）
- vlm: VLM 图片理解服务
- llm: LLM 对话生成服务
- bm25: BM25 倒排索引
- cross_encoder: Cross-Encoder 精排模型
- cache: 查询结果缓存
- feedback: 反馈闭环数据库
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ComponentHealth:
    name: str
    status: str  # "ok", "degraded", "down"
    latency_ms: float = 0.0
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class HealthChecker:
    """健康检查注册中心。每个组件注册一个检查函数，/health 时统一执行。"""

    def __init__(self) -> None:
        self._checks: dict[str, Callable[[], ComponentHealth]] = {}

    def register(self, name: str, check_fn: Callable[[], ComponentHealth]) -> None:
        self._checks[name] = check_fn

    def check_all(self) -> dict[str, ComponentHealth]:
        results: dict[str, ComponentHealth] = {}
        for name, check_fn in self._checks.items():
            try:
                results[name] = check_fn()
            except Exception as exc:
                results[name] = ComponentHealth(
                    name=name, status="down", message=str(exc)
                )
        return results

    def to_dict(self) -> dict[str, Any]:
        results = self.check_all()
        overall = "ok" if all(r.status == "ok" for r in results.values()) else "degraded"
        if any(r.status == "down" for r in results.values()):
            overall = "down"
        return {
            "status": overall,
            "components": {
                name: {
                    "status": r.status,
                    "latency_ms": round(r.latency_ms, 2),
                    "message": r.message,
                    **r.metadata,
                }
                for name, r in results.items()
            },
        }


# 全局单例
_default_checker: HealthChecker | None = None


def get_health_checker() -> HealthChecker:
    global _default_checker
    if _default_checker is None:
        _default_checker = HealthChecker()
    return _default_checker


def register_health_check(name: str, check_fn: Callable[[], ComponentHealth]) -> None:
    get_health_checker().register(name, check_fn)
