"""
Purpose: 语义规划数据模型：SemanticPlan、SemanticFilter、CartAction、ReferenceType 等 Pydantic 定义。
"""

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from server.agent.intent import UserIntent
from server.session.state import FilterCondition


SemanticIntent = Literal["recommend", "compare", "cart", "ask_product_detail", "clarify", "browse", "bundle"]
CartAction = Literal["none", "add", "remove", "update_quantity", "view", "checkout"]
ReferenceType = Literal["none", "last", "ordinal", "name", "brand", "cheapest"]
PresentationMode = Literal["auto", "single", "listing"]
FilterKind = Literal[
    "keyword",
    "should_keyword",
    "max_price",
    "min_price",
    "brand",
    "preferred_brand",
    "exclude_brand",
    "exclude",
    "category",
    "exclude_category",
    "product_type",
    "exclude_product_type",
    "in_stock",
    "facet",
    "unsupported_service",
]
ConstraintMode = Literal["must", "must_not", "should"]
ConstraintField = Literal[
    "product_type",
    "category",
    "keyword",
    "brand",
    "price",
    "attribute",
    "stock",
    "facet",
    "service",
]
ConstraintOperator = Literal["eq", "contains", "lte", "gte"]


class ChatMessageClient(Protocol):
    async def stream_messages(self, messages: list[dict]) -> AsyncIterator[str]:
        ...


class SemanticFilter(BaseModel):
    kind: FilterKind
    value: str


class SemanticConstraint(BaseModel):
    mode: ConstraintMode
    field: ConstraintField
    operator: ConstraintOperator = "contains"
    value: str
    source: str = "planner"


class SemanticPlan(BaseModel):
    intent: SemanticIntent = "recommend"
    cart_action: CartAction = "none"
    reference_type: ReferenceType = "none"
    reference_index: int | None = None
    reference_text: str = ""
    quantity: int | None = None
    query: str = ""
    presentation_mode: PresentationMode = "auto"
    query_understanding: dict[str, Any] = Field(default_factory=dict)
    filters: list[SemanticFilter] = Field(default_factory=list)
    constraints: list[SemanticConstraint] = Field(default_factory=list)
    needs_search: bool = True
    confidence: float = 0.0
    confidence_by_field: dict[str, float] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)

    def to_user_intent(self) -> UserIntent:
        if self.intent == "compare":
            return UserIntent.COMPARE
        if self.intent == "cart":
            return UserIntent.CART
        if self.intent == "browse":
            return UserIntent.BROWSING
        return UserIntent.BUYING

    def to_filter_conditions(self) -> list[FilterCondition]:
        filters = [FilterCondition(kind=item.kind, value=item.value) for item in self.filters if item.value.strip()]
        filters.extend(constraint_to_filter_condition(item) for item in self.constraints if item.value.strip())
        return dedupe_filter_conditions(filters)


def constraint_to_filter_condition(constraint: SemanticConstraint) -> FilterCondition:
    if constraint.field == "price" and constraint.mode == "must":
        if constraint.operator == "lte":
            return FilterCondition(kind="max_price", value=constraint.value)
        if constraint.operator == "gte":
            return FilterCondition(kind="min_price", value=constraint.value)

    if constraint.field == "product_type" and constraint.mode == "must":
        return FilterCondition(kind="product_type", value=constraint.value)
    if constraint.field == "product_type" and constraint.mode == "must_not":
        return FilterCondition(kind="exclude_product_type", value=constraint.value)

    if constraint.field == "category" and constraint.mode == "must":
        return FilterCondition(kind="category", value=constraint.value)
    if constraint.field == "category" and constraint.mode == "must_not":
        return FilterCondition(kind="exclude_category", value=constraint.value)

    if constraint.field == "brand":
        if constraint.mode == "must":
            return FilterCondition(kind="brand", value=constraint.value)
        if constraint.mode == "must_not":
            return FilterCondition(kind="exclude_brand", value=constraint.value)
        return FilterCondition(kind="preferred_brand", value=constraint.value)

    if constraint.field in {"keyword", "attribute"}:
        if constraint.mode == "must_not":
            return FilterCondition(kind="exclude", value=constraint.value)
        if constraint.mode == "should":
            return FilterCondition(kind="should_keyword", value=constraint.value)
        return FilterCondition(kind="keyword", value=constraint.value)

    if constraint.field == "stock" and constraint.mode == "must":
        return FilterCondition(kind="in_stock", value="true")

    if constraint.field == "facet":
        return FilterCondition(kind="facet", value=constraint.value)

    if constraint.field == "service":
        return FilterCondition(kind="unsupported_service", value=constraint.value)

    return FilterCondition(kind="keyword", value=constraint.value)


def dedupe_filter_conditions(filters: list[FilterCondition]) -> list[FilterCondition]:
    seen: set[tuple[str, str]] = set()
    result: list[FilterCondition] = []
    for item in filters:
        key = (item.kind, item.value.strip())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
