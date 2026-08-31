"""
Purpose: Agent Trace 追踪：InMemoryTraceStore 记录每次对话的完整链路，用于调试和离线评估。
"""

import time
from collections import Counter, deque
from threading import Lock
from uuid import uuid4

from pydantic import BaseModel, Field

from server.agent.semantic_schema import SemanticPlan
from server.rag.post_process import SearchFilters


class AgentTrace(BaseModel):
    trace_id: str
    session_id: str
    message: str
    handler: str = ""
    plan: dict
    query: str
    filters: dict
    event_counts: dict[str, int] = Field(default_factory=dict)
    product_ids: list[str] = Field(default_factory=list)
    comparison_product_ids: list[str] = Field(default_factory=list)
    cart_total_quantity: int | None = None
    token_chars: int = 0
    metadata: dict = Field(default_factory=dict)
    duration_ms: float = 0.0
    started_at: float
    completed_at: float


class InMemoryTraceStore:
    def __init__(self, max_items: int = 200) -> None:
        self._items: deque[AgentTrace] = deque(maxlen=max_items)
        self._lock = Lock()

    def add(self, trace: AgentTrace) -> None:
        with self._lock:
            self._items.append(trace)

    def list(self, session_id: str | None = None, limit: int = 50) -> list[AgentTrace]:
        with self._lock:
            items = list(reversed(self._items))
        if session_id:
            items = [item for item in items if item.session_id == session_id]
        return items[:limit]

    def get(self, trace_id: str) -> AgentTrace | None:
        with self._lock:
            for item in self._items:
                if item.trace_id == trace_id:
                    return item
        return None


def build_trace(
    *,
    trace_id: str | None = None,
    session_id: str,
    message: str,
    handler: str,
    plan: SemanticPlan,
    query: str,
    filters: SearchFilters,
    events: list[dict],
    started_at: float,
    metadata: dict | None = None,
) -> AgentTrace:
    completed_at = time.time()
    event_counts = Counter(item.get("event", "") for item in events)
    product_ids = [
        str(item.get("data", {}).get("id", ""))
        for item in events
        if item.get("event") == "product_card" and item.get("data", {}).get("id")
    ]
    comparison_product_ids = collect_comparison_product_ids(events)
    cart_total_quantity = collect_latest_cart_quantity(events)
    token_chars = sum(
        len(str(item.get("data", {}).get("text", "")))
        for item in events
        if item.get("event") == "token"
    )

    return AgentTrace(
        trace_id=trace_id or uuid4().hex,
        session_id=session_id,
        message=message,
        handler=handler,
        plan=plan.model_dump(),
        query=query,
        filters={
            "max_price": filters.max_price,
            "min_price": filters.min_price,
            "keywords": filters.keywords,
            "should_keywords": filters.should_keywords,
            "categories": filters.categories,
            "excluded_categories": filters.excluded_categories,
            "product_types": filters.product_types,
            "excluded_product_types": filters.excluded_product_types,
            "brands": filters.brands,
            "preferred_brands": filters.preferred_brands,
            "excluded_brands": filters.excluded_brands,
            "exclusions": filters.exclusions,
            "in_stock_only": filters.in_stock_only,
            "facets": filters.facets,
            "unsupported_constraints": filters.unsupported_constraints,
        },
        event_counts=dict(event_counts),
        product_ids=product_ids,
        comparison_product_ids=comparison_product_ids,
        cart_total_quantity=cart_total_quantity,
        token_chars=token_chars,
        metadata=metadata or {},
        duration_ms=round((completed_at - started_at) * 1000, 2),
        started_at=started_at,
        completed_at=completed_at,
    )


def collect_comparison_product_ids(events: list[dict]) -> list[str]:
    for item in reversed(events):
        if item.get("event") != "comparison_card":
            continue
        products = item.get("data", {}).get("products", [])
        return [str(product.get("id", "")) for product in products if product.get("id")]
    return []


def collect_latest_cart_quantity(events: list[dict]) -> int | None:
    for item in reversed(events):
        if item.get("event") == "cart_update":
            return int(item.get("data", {}).get("total_quantity", 0))
    return None
