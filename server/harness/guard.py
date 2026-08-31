"""前缀守卫：监控 prefix 稳定性，暴露 cache 指标。"""

from __future__ import annotations

from dataclasses import dataclass, field

from server.harness.partition import ImmutablePrefix


@dataclass
class PrefixGuard:
    """Prefix Guard：监控 ImmutablePrefix 的稳定性。

    职责：
    1. 定期校验 prefix 指纹
    2. 记录 cache hit/miss 事件
    3. 暴露指标供外部监控
    """

    prefix: ImmutablePrefix
    strict_mode: bool = False  # True = 指纹漂移时 throw
    _violations: list[dict] = field(default_factory=list, repr=False)
    _miss_events: int = field(default=0, repr=False)

    def check(self) -> bool:
        """校验 prefix 指纹。

        返回 True 表示正常，False 表示检测到漂移。
        strict_mode 下会抛出 AssertionError。
        """
        ok = self.prefix.verify_fingerprint()
        if not ok:
            event = {
                "fingerprint_expected": self.prefix._fingerprint,
                "action": "prefix_drift_detected",
            }
            self._violations.append(event)
            self._miss_events += 1

            if self.strict_mode:
                raise AssertionError(
                    f"ImmutablePrefix fingerprint drift detected! "
                    f"A mutation path bypassed explicit invalidation. "
                    f"This breaks DeepSeek prefix cache stability."
                )
        return ok

    def record_miss(self, reason: str = "unknown") -> None:
        """记录一次 cache miss（如 prefix invalidated、新 session 等）。"""
        self._miss_events += 1
        self._violations.append({
            "reason": reason,
            "action": "cache_miss_recorded",
        })

    def get_metrics(self) -> dict:
        """获取 guard 指标。"""
        return {
            "prefix_fingerprint": self.prefix._fingerprint,
            "prefix_invalidated": self.prefix.is_invalidated(),
            "miss_events": self._miss_events,
            "violations_count": len(self._violations),
            "violations": self._violations[-10:],  # 最近 10 条
        }
