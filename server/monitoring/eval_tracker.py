"""
业务质量指标追踪：幻觉率、检索准确率、缓存命中率等。

与 SlidingWindowMetrics（性能指标）互补，聚焦业务质量而非系统性能。

指标：
- hallucination_rate: 幻觉检测标记数 / 总回答数
- ndcg_at_k: 基于点击位置的简化 NDCG（需要反馈数据）
- retrieval_coverage: 有结果的查询 / 总查询

设计原则：
- 纯内存统计，零外部依赖
- 每个 turn 只更新几个计数器，不阻塞主流程
- 可导出：/metrics/quality 返回 JSON
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class QualityRecord:
    """单次对话turn的质量记录。"""
    timestamp: float
    hallucination_flag: bool = False      # 是否被标记幻觉
    cache_hit: bool = False               # 查询缓存是否命中
    has_result: bool = False              # 检索是否有结果
    click_position: int = -1              # 用户点击位置（-1表示无点击）
    retrieval_result_count: int = 0       # 检索返回结果数


class QualityMetrics:
    """业务质量指标滑动窗口统计。"""

    def __init__(self, window_size: int = 500) -> None:
        self.window_size = window_size
        self._records: deque[QualityRecord] = deque(maxlen=window_size)
        self._lock = Lock()

    def record(self, record: QualityRecord) -> None:
        with self._lock:
            self._records.append(record)

    def snapshot(self) -> QualitySnapshot:
        with self._lock:
            records = list(self._records)
        return QualitySnapshot.from_records(records)


@dataclass(frozen=True)
class QualitySnapshot:
    """业务质量指标快照。"""
    total_turns: int
    hallucination_count: int
    hallucination_rate: float
    cache_hits: int
    cache_hit_rate: float
    coverage_count: int          # 有结果的查询数
    coverage_rate: float         # 覆盖率
    avg_click_position: float    # 平均点击位置（越小越好）
    ndcg_at_5: float             # 简化 NDCG@5

    @classmethod
    def from_records(cls, records: list[QualityRecord]) -> QualitySnapshot:
        if not records:
            return cls(
                total_turns=0, hallucination_count=0, hallucination_rate=0.0,
                cache_hits=0, cache_hit_rate=0.0,
                coverage_count=0, coverage_rate=0.0,
                avg_click_position=0.0, ndcg_at_5=0.0,
            )

        total = len(records)
        hallucinations = sum(1 for r in records if r.hallucination_flag)
        cache_hits = sum(1 for r in records if r.cache_hit)
        covered = sum(1 for r in records if r.has_result)

        # 点击位置统计（只统计有点击的）
        click_positions = [r.click_position for r in records if r.click_position >= 0]
        avg_click = sum(click_positions) / len(click_positions) if click_positions else 0.0

        # 简化 NDCG@5：基于点击位置计算
        # DCG = sum(1 / log2(pos + 2)) for each click
        # IDCG = 1 / log2(2) = 1 (理想情况：点击第1个)
        # NDCG = DCG / IDCG
        ndcg = 0.0
        if click_positions:
            dcg = sum(1.0 / __import__('math').log2(pos + 2) for pos in click_positions)
            idcg = len(click_positions) * 1.0  # 理想情况下每次都点第1个
            ndcg = dcg / idcg if idcg > 0 else 0.0

        return cls(
            total_turns=total,
            hallucination_count=hallucinations,
            hallucination_rate=hallucinations / total,
            cache_hits=cache_hits,
            cache_hit_rate=cache_hits / total,
            coverage_count=covered,
            coverage_rate=covered / total,
            avg_click_position=round(avg_click, 2),
            ndcg_at_5=round(ndcg, 4),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_turns": self.total_turns,
            "hallucination": {
                "count": self.hallucination_count,
                "rate": round(self.hallucination_rate, 4),
            },
            "cache": {
                "hits": self.cache_hits,
                "hit_rate": round(self.cache_hit_rate, 4),
            },
            "retrieval": {
                "coverage_count": self.coverage_count,
                "coverage_rate": round(self.coverage_rate, 4),
            },
            "engagement": {
                "avg_click_position": self.avg_click_position,
                "ndcg_at_5": self.ndcg_at_5,
            },
        }


# 全局单例
_default_quality: QualityMetrics | None = None


def get_quality_metrics(window_size: int = 500) -> QualityMetrics:
    global _default_quality
    if _default_quality is None:
        _default_quality = QualityMetrics(window_size=window_size)
    return _default_quality


def record_quality(
    *,
    hallucination_flag: bool = False,
    cache_hit: bool = False,
    has_result: bool = False,
    click_position: int = -1,
    retrieval_result_count: int = 0,
) -> None:
    """便捷函数：记录一次 turn 的质量数据。"""
    get_quality_metrics().record(
        QualityRecord(
            timestamp=time.time(),
            hallucination_flag=hallucination_flag,
            cache_hit=cache_hit,
            has_result=has_result,
            click_position=click_position,
            retrieval_result_count=retrieval_result_count,
        )
    )
