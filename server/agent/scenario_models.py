"""
Purpose: 场景数据模型：ScenarioBundle、ScenarioSlot、ScenarioBundleConfig 等 Pydantic 模型定义。
"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from server.agent.scenario_utils import merge_slot_filters, render_query_template, should_keep_slot, stable_bucket
from server.rag.post_process import SearchFilters


@dataclass(frozen=True)
class ScenarioSlot:
    label: str
    query: str
    filters: SearchFilters
    max_items: int = 1
    candidate_pool_size: int = 3
    budget_weight: float = 1.0
    optional: bool = False
    min_budget: float | None = None
    match_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioBundle:
    id: str
    title: str
    summary: str
    slots: tuple[ScenarioSlot, ...]
    matched_terms: tuple[str, ...] = ()
    source_version: str = ""
    match_confidence: float = 0.0
    match_signals: tuple[str, ...] = ()
    context_variables: dict[str, str] = field(default_factory=dict)
    governance: dict[str, object] | None = None


@dataclass(frozen=True)
class ScenarioMatch:
    bundle: ScenarioBundle
    score: float
    confidence: float
    signals: tuple[str, ...]


class ScenarioFiltersConfig(BaseModel):
    max_price: float | None = None
    keywords: list[str] = Field(default_factory=list)
    product_types: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)

    def to_search_filters(self) -> SearchFilters:
        return SearchFilters(
            max_price=self.max_price,
            keywords=list(dict.fromkeys(item.strip() for item in self.keywords if item.strip())),
            product_types=list(dict.fromkeys(item.strip() for item in self.product_types if item.strip())),
            exclusions=list(dict.fromkeys(item.strip() for item in self.exclusions if item.strip())),
        )


class ScenarioSlotConfig(BaseModel):
    label: str
    query_template: str
    filters: ScenarioFiltersConfig = Field(default_factory=ScenarioFiltersConfig)
    max_items: int = Field(default=1, ge=1, le=5)
    candidate_pool_size: int = Field(default=3, ge=1, le=20)
    budget_weight: float = Field(default=1.0, ge=0.0)
    optional: bool = False
    min_budget: float | None = None
    match_terms: list[str] = Field(default_factory=list)


class ScenarioBundleConfig(BaseModel):
    status: Literal["active", "draft", "disabled"] = "active"
    rollout_percentage: float = Field(default=100.0, ge=0.0, le=100.0)
    owner: str = ""
    reviewed_at: str = ""
    id: str
    priority: int = 0
    trigger_terms: list[str] = Field(default_factory=list)
    semantic_terms: list[str] = Field(default_factory=list)
    context_terms: dict[str, list[str]] = Field(default_factory=dict)
    title_template: str = ""
    summary_template: str = ""
    min_score: float = 20.0
    title: str
    summary: str
    slots: list[ScenarioSlotConfig] = Field(min_length=1)

    def matched_terms(self, message: str) -> tuple[str, ...]:
        return tuple(term for term in self.trigger_terms if term and term in message)

    def to_runtime_bundle(
        self,
        message: str,
        matched_terms: tuple[str, ...],
        source_version: str,
        *,
        confidence: float = 0.0,
        signals: tuple[str, ...] = (),
        filters: SearchFilters | None = None,
        context_variables: dict[str, str] | None = None,
    ) -> ScenarioBundle:
        total_budget = filters.max_price if filters is not None else None
        context_variables = context_variables or {}
        slots = tuple(
            ScenarioSlot(
                label=slot.label,
                query=render_query_template(slot.query_template, message, context_variables),
                filters=merge_slot_filters(slot.filters.to_search_filters(), filters),
                max_items=slot.max_items,
                candidate_pool_size=slot.candidate_pool_size,
                budget_weight=slot.budget_weight,
                optional=slot.optional,
                min_budget=slot.min_budget,
                match_terms=tuple(slot.match_terms),
            )
            for slot in self.slots
            if should_keep_slot(slot, message=message, total_budget=total_budget)
        )
        title = (
            render_context_template(self.title_template, context_variables)
            if self.title_template and context_variables
            else self.title
        )
        summary = (
            render_context_template(self.summary_template, context_variables)
            if self.summary_template and context_variables
            else self.summary
        )
        return ScenarioBundle(
            id=self.id,
            title=title,
            summary=summary,
            slots=slots,
            matched_terms=matched_terms,
            source_version=source_version,
            match_confidence=confidence,
            match_signals=signals,
            context_variables=dict(context_variables),
            governance={
                "status": self.status,
                "rollout_percentage": self.rollout_percentage,
                "owner": self.owner,
                "reviewed_at": self.reviewed_at,
            },
        )

    def enabled_for(self, session_id: str = "") -> bool:
        if self.status != "active":
            return False
        if self.rollout_percentage >= 100:
            return True
        if self.rollout_percentage <= 0:
            return False
        seed = f"{session_id}:{self.id}" if session_id else self.id
        return stable_bucket(seed) < self.rollout_percentage


class ScenarioCatalogConfig(BaseModel):
    version: str = ""
    bundles: list[ScenarioBundleConfig] = Field(default_factory=list)


def render_context_template(template: str, context_variables: dict[str, str]) -> str:
    result = template
    for key, value in context_variables.items():
        result = result.replace("{" + key + "}", value)
    return result
