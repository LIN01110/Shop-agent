"""
Purpose: 查询反馈记录：记录检索失败、规划失败等事件，用于离线分析和持续优化。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from server.agent.semantic_schema import SemanticPlan
from server.rag.post_process import SearchFilters


@dataclass(frozen=True)
class QueryFeedbackEvent:
    trace_id: str
    session_id: str
    message: str
    reasons: tuple[str, ...]
    plan_intent: str
    filters: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def as_json(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "message": self.message,
            "reasons": list(self.reasons),
            "plan_intent": self.plan_intent,
            "filters": self.filters,
            "metadata": self.metadata,
        }


class QueryFeedbackStore:
    def __init__(
        self,
        path: str | Path,
        *,
        enabled: bool = True,
        max_message_chars: int = 300,
    ) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self.max_message_chars = max(20, max_message_chars)
        self._lock = Lock()
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: QueryFeedbackEvent) -> None:
        if not self.enabled:
            return
        payload = event.as_json()
        payload["message"] = payload["message"][: self.max_message_chars]
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")


def build_query_feedback_event(
    *,
    trace_id: str,
    session_id: str,
    message: str,
    plan: SemanticPlan,
    filters: SearchFilters,
    events: list[dict],
    metadata: dict[str, Any],
) -> QueryFeedbackEvent | None:
    reasons = query_feedback_reasons(plan=plan, events=events, metadata=metadata)
    if not reasons:
        return None
    return QueryFeedbackEvent(
        trace_id=trace_id,
        session_id=session_id,
        message=message,
        reasons=tuple(reasons),
        plan_intent=plan.intent,
        filters={
            "max_price": filters.max_price,
            "min_price": filters.min_price,
            "keywords": filters.keywords,
            "categories": filters.categories,
            "product_types": filters.product_types,
            "brands": filters.brands,
            "excluded_brands": filters.excluded_brands,
            "in_stock_only": filters.in_stock_only,
            "unsupported_constraints": filters.unsupported_constraints,
        },
        metadata={
            "scope_transition": metadata.get("scope_transition", {}),
            "query_understanding": metadata.get("query_understanding", {}),
            "product_discovery": metadata.get("product_discovery", {}),
            "scenario_routing": metadata.get("scenario_routing", {}),
            "scenario_routing_query": metadata.get("scenario_routing_query", {}),
        },
    )


def query_feedback_reasons(*, plan: SemanticPlan, events: list[dict], metadata: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    event_names = [str(item.get("event", "")) for item in events]
    has_product_card = "product_card" in event_names
    has_comparison_card = "comparison_card" in event_names
    if plan.needs_search and not has_product_card and not has_comparison_card:
        reasons.append("no_retrieval_output")
        reasons.extend(no_retrieval_root_causes(plan=plan, metadata=metadata))

    transition = metadata.get("scope_transition")
    if isinstance(transition, dict) and transition.get("type") == "uncertain":
        reasons.append("uncertain_product_scope")

    retrieval = metadata.get("retrieval")
    if isinstance(retrieval, dict):
        filtered_out = retrieval.get("filtered_out")
        if isinstance(filtered_out, list) and filtered_out:
            reasons.append("candidates_filtered_out")

    product_discovery = metadata.get("product_discovery")
    if isinstance(product_discovery, dict) and product_discovery.get("card_binding_mismatch"):
        reasons.append("card_binding_mismatch")

    card_binding = metadata.get("card_binding")
    if isinstance(card_binding, dict):
        reason = str(card_binding.get("reason", "")).strip()
        if reason == "answer_declines_recommendation":
            reasons.append("answer_declined_product_recommendation")
            reasons.append("catalog_gap_or_unsupported_scope")

    catalog_gap = metadata.get("catalog_gap")
    if isinstance(catalog_gap, dict):
        reason = str(catalog_gap.get("reason", "")).strip()
        if reason:
            reasons.append(f"catalog_gap:{reason}")

    scenario_routing = metadata.get("scenario_routing")
    if isinstance(scenario_routing, dict):
        rejected = scenario_routing.get("rejected_reasons")
        if isinstance(rejected, list):
            reasons.extend(f"scenario:{reason}" for reason in rejected if isinstance(reason, str) and reason)

    return dedupe_reasons(reasons)


def no_retrieval_root_causes(*, plan: SemanticPlan, metadata: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    retrieval_degraded = metadata.get("retrieval_degraded")
    if isinstance(retrieval_degraded, dict):
        reason = str(retrieval_degraded.get("reason", "")).strip()
        if reason:
            reasons.append(reason)

    retrieval = metadata.get("retrieval")
    applied_filters = {}
    steps = []
    if isinstance(retrieval, dict):
        applied_filters = retrieval.get("applied_filters") if isinstance(retrieval.get("applied_filters"), dict) else {}
        steps = retrieval.get("steps") if isinstance(retrieval.get("steps"), list) else []

    if lacks_structured_scope(plan=plan, applied_filters=applied_filters):
        reasons.append("no_structured_scope")

    card_binding = metadata.get("card_binding")
    if isinstance(card_binding, dict) and card_binding.get("reason") == "answer_declines_recommendation":
        reasons.append("answer_declined_product_recommendation")
        reasons.append("catalog_gap_or_unsupported_scope")

    catalog_gap = metadata.get("catalog_gap")
    if isinstance(catalog_gap, dict):
        reason = str(catalog_gap.get("reason", "")).strip()
        if reason:
            reasons.append(f"catalog_gap:{reason}")

    if steps:
        reasons.extend(retrieval_step_root_causes(steps))
    elif not retrieval_degraded:
        reasons.append("retrieval_diagnostics_missing")
    return reasons


def lacks_structured_scope(*, plan: SemanticPlan, applied_filters: dict[str, Any]) -> bool:
    has_filter_scope = any(
        applied_filters.get(key)
        for key in [
            "categories",
            "product_types",
            "brands",
            "keywords",
            "should_keywords",
            "facets",
        ]
    )
    has_plan_scope = bool(plan.filters or plan.constraints)
    return not has_filter_scope and not has_plan_scope


def retrieval_step_root_causes(steps: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    previous_count: int | None = None
    store_prefilter_count: int | None = None
    relaxed_count: int | None = None

    for raw_step in steps:
        if not isinstance(raw_step, dict):
            continue
        name = str(raw_step.get("name", ""))
        count = parse_count(raw_step.get("count"))
        if name == "store_prefilter_recall":
            store_prefilter_count = count
        if name == "store_recall_without_type_prefilter":
            relaxed_count = count
        if count == 0 and previous_count is not None and previous_count > 0:
            reasons.append(f"filtered_to_zero_by_{name}")
        previous_count = count

    if store_prefilter_count == 0:
        reasons.append("store_prefilter_empty")
    if relaxed_count == 0:
        reasons.append("relaxed_store_recall_empty")
    if previous_count == 0:
        reasons.append("final_candidate_empty")
    return reasons


def parse_count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def dedupe_reasons(reasons: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            result.append(reason)
    return result
