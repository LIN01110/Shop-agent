"""
监控埋点：内存滑动窗口统计 + /metrics 端点。

指标：
- request_count_total: 总请求数
- request_latency_ms: P50/P95/P99 延迟（毫秒）
- cache_hit_rate: 查询缓存命中率
- stage_latency_ms: 各阶段耗时（向量检索、精排、LLM 调用）
- error_count_total: 错误数（按组件分类）
- component_health: 各组件健康状态（1=健康，0=异常）
- hallucination_rate: 幻觉检测触发率
- hallucination_by_layer: 各层幻觉分布

设计原则：
- 零外部依赖：纯内存统计，无 Prometheus 也能跑
- 低开销：每个请求只记录几个 float，不阻塞主流程
- 可导出：/metrics 返回 JSON，未来可适配 Prometheus
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class RequestRecord:
    """单次请求记录。"""
    timestamp: float
    latency_ms: float
    cache_hit: bool
    stage_timings: dict[str, float] = field(default_factory=dict)
    error: bool = False
    error_component: str = ""
    hallucination_triggered: bool = False
    hallucination_layers: list[int] = field(default_factory=list)


@dataclass
class HallucinationRecord:
    """单次幻觉事件记录。"""
    timestamp: float
    session_id: str
    trace_id: str
    action: str
    layers: list[int]
    violation_types: list[str]


class SlidingWindowMetrics:
    """滑动窗口统计器，维护最近 N 条请求的数据。"""

    def __init__(self, window_size: int = 1000) -> None:
        self.window_size = window_size
        self._records: deque[RequestRecord] = deque(maxlen=window_size)
        self._hallucination_records: deque[HallucinationRecord] = deque(maxlen=window_size)
        self._lock = Lock()

    def record(self, record: RequestRecord) -> None:
        with self._lock:
            self._records.append(record)

    def record_hallucination(self, record: HallucinationRecord) -> None:
        """记录一次幻觉事件。"""
        with self._lock:
            self._hallucination_records.append(record)

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            records = list(self._records)
            hallucination_records = list(self._hallucination_records)
        return MetricsSnapshot.from_records(records, hallucination_records)


@dataclass(frozen=True)
class MetricsSnapshot:
    """某一时刻的指标快照。"""
    total_requests: int
    error_count: int
    error_rate: float
    cache_hits: int
    cache_hit_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    avg_latency_ms: float
    qps: float  # 基于窗口内数据的估算
    stage_avg_latency_ms: dict[str, float]
    # 幻觉检测指标
    hallucination_count: int
    hallucination_rate: float
    hallucination_by_layer: dict[str, int]
    hallucination_by_action: dict[str, int]

    @classmethod
    def from_records(
        cls,
        records: list[RequestRecord],
        hallucination_records: list[HallucinationRecord] | None = None,
    ) -> MetricsSnapshot:
        if not records:
            return cls(
                total_requests=0, error_count=0, error_rate=0.0,
                cache_hits=0, cache_hit_rate=0.0,
                latency_p50_ms=0.0, latency_p95_ms=0.0, latency_p99_ms=0.0,
                avg_latency_ms=0.0, qps=0.0, stage_avg_latency_ms={},
                hallucination_count=0, hallucination_rate=0.0,
                hallucination_by_layer={}, hallucination_by_action={},
            )

        total = len(records)
        errors = sum(1 for r in records if r.error)
        cache_hits = sum(1 for r in records if r.cache_hit)
        latencies = sorted(r.latency_ms for r in records)
        hallucination_triggers = sum(1 for r in records if r.hallucination_triggered)

        # QPS 估算：窗口时间跨度 / 请求数
        if total > 1:
            time_span = records[-1].timestamp - records[0].timestamp
            qps = total / max(time_span, 0.001)
        else:
            qps = 0.0

        # 分阶段平均耗时
        stage_accum: dict[str, list[float]] = {}
        for r in records:
            for stage, latency in r.stage_timings.items():
                stage_accum.setdefault(stage, []).append(latency)
        stage_avg = {k: sum(v) / len(v) for k, v in stage_accum.items()}

        # 幻觉分层统计
        hallucination_by_layer: dict[str, int] = {}
        hallucination_by_action: dict[str, int] = {}
        h_records = hallucination_records or []
        for h in h_records:
            for layer in h.layers:
                key = f"layer_{layer}"
                hallucination_by_layer[key] = hallucination_by_layer.get(key, 0) + 1
            hallucination_by_action[h.action] = hallucination_by_action.get(h.action, 0) + 1

        def percentile(data: list[float], p: float) -> float:
            if not data:
                return 0.0
            k = (len(data) - 1) * p
            f = int(k)
            c = f + 1 if f + 1 < len(data) else f
            if f == c:
                return data[f]
            return data[f] * (c - k) + data[c] * (k - f)

        return cls(
            total_requests=total,
            error_count=errors,
            error_rate=errors / total,
            cache_hits=cache_hits,
            cache_hit_rate=cache_hits / total,
            latency_p50_ms=percentile(latencies, 0.5),
            latency_p95_ms=percentile(latencies, 0.95),
            latency_p99_ms=percentile(latencies, 0.99),
            avg_latency_ms=sum(latencies) / total,
            qps=round(qps, 2),
            stage_avg_latency_ms=stage_avg,
            hallucination_count=len(h_records),
            hallucination_rate=hallucination_triggers / total if total > 0 else 0.0,
            hallucination_by_layer=hallucination_by_layer,
            hallucination_by_action=hallucination_by_action,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "error_count": self.error_count,
            "error_rate": round(self.error_rate, 4),
            "cache_hits": self.cache_hits,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            "latency_ms": {
                "p50": round(self.latency_p50_ms, 2),
                "p95": round(self.latency_p95_ms, 2),
                "p99": round(self.latency_p99_ms, 2),
                "avg": round(self.avg_latency_ms, 2),
            },
            "qps": self.qps,
            "stage_latency_ms": {k: round(v, 2) for k, v in self.stage_avg_latency_ms.items()},
            "hallucination": {
                "count": self.hallucination_count,
                "rate": round(self.hallucination_rate, 4),
                "by_layer": self.hallucination_by_layer,
                "by_action": self.hallucination_by_action,
            },
        }


# 全局单例
_default_metrics: SlidingWindowMetrics | None = None


def get_metrics(window_size: int = 1000) -> SlidingWindowMetrics:
    global _default_metrics
    if _default_metrics is None:
        _default_metrics = SlidingWindowMetrics(window_size=window_size)
    return _default_metrics


def record_request(
    latency_ms: float,
    *,
    cache_hit: bool = False,
    stage_timings: dict[str, float] | None = None,
    error: bool = False,
    error_component: str = "",
    hallucination_triggered: bool = False,
    hallucination_layers: list[int] | None = None,
) -> None:
    """便捷函数：记录一次请求。"""
    get_metrics().record(
        RequestRecord(
            timestamp=time.time(),
            latency_ms=latency_ms,
            cache_hit=cache_hit,
            stage_timings=stage_timings or {},
            error=error,
            error_component=error_component,
            hallucination_triggered=hallucination_triggered,
            hallucination_layers=hallucination_layers or [],
        )
    )


def record_hallucination_event(
    session_id: str = "",
    trace_id: str = "",
    action: str = "",
    layers: list[int] | None = None,
    violation_types: list[str] | None = None,
) -> None:
    """便捷函数：记录一次幻觉事件。"""
    get_metrics().record_hallucination(
        HallucinationRecord(
            timestamp=time.time(),
            session_id=session_id,
            trace_id=trace_id,
            action=action,
            layers=layers or [],
            violation_types=violation_types or [],
        )
    )
