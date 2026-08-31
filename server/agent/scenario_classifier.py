"""
Purpose: 场景分类器：混合分类（规则 + 语义相似度），判断用户请求是否命中场景化组合（如三亚度假）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from server.agent.scenario_matching import (
    score_bundle,
    should_allow_default_cross_category_bundle,
)
from server.agent.scenario_models import ScenarioBundleConfig
from server.agent.semantic_schema import SemanticPlan
from server.nlu.taxonomy_classifier import cosine_similarity
from server.rag.post_process import SearchFilters


@dataclass(frozen=True)
class ScenarioCandidate:
    bundle: ScenarioBundleConfig
    score: float
    matched_terms: tuple[str, ...]
    signals: tuple[str, ...]
    context_variables: dict[str, str]
    source: str = "rule_config"

    @property
    def confidence(self) -> float:
        return min(1.0, round(self.score / 100.0, 3))

    def as_metadata(self) -> dict:
        return {
            "bundle_id": self.bundle.id,
            "score": round(self.score, 4),
            "confidence": self.confidence,
            "source": self.source,
            "matched_terms": list(self.matched_terms),
            "signals": list(self.signals),
            "context_variables": dict(self.context_variables),
        }


@dataclass(frozen=True)
class ScenarioClassification:
    candidates: tuple[ScenarioCandidate, ...]
    rejected_reasons: tuple[str, ...] = ()
    used_embedding: bool = False

    @property
    def best(self) -> ScenarioCandidate | None:
        return self.candidates[0] if self.candidates else None

    def as_metadata(self) -> dict:
        return {
            "selected_bundle_id": self.best.bundle.id if self.best else None,
            "used_embedding": self.used_embedding,
            "rejected_reasons": list(self.rejected_reasons),
            "candidates": [candidate.as_metadata() for candidate in self.candidates[:5]],
        }


class RuleScenarioClassifier:
    """Config-driven scenario classifier with conservative routing gates."""

    def classify(
        self,
        bundles: Sequence[ScenarioBundleConfig],
        message: str,
        *,
        plan: SemanticPlan | None,
        filters: SearchFilters | None,
        session_id: str = "",
    ) -> ScenarioClassification:
        candidates: list[ScenarioCandidate] = []
        rejected: list[str] = []
        for bundle in bundles:
            if not bundle.enabled_for(session_id=session_id):
                continue
            score, matched_terms, signals = score_bundle(bundle, message, plan=plan, filters=filters)
            context_variables, context_signals = extract_context_variables(bundle, message)
            if context_variables:
                score += 6.0 + min(len(context_variables), 3) * 2.0
                signals = (*signals, *context_signals)
                matched_terms = tuple(dict.fromkeys((*matched_terms, *context_variables.values())))
            if score < bundle.min_score:
                if matched_terms or context_variables:
                    rejected.append(f"{bundle.id}:score_below_threshold")
                continue
            candidates.append(
                ScenarioCandidate(
                    bundle=bundle,
                    score=score,
                    matched_terms=matched_terms,
                    signals=signals,
                    context_variables=context_variables,
                )
            )

        if not candidates and plan is not None and plan.intent == "bundle":
            fallback = build_default_cross_category_candidate(
                bundles,
                message,
                plan=plan,
                filters=filters,
                session_id=session_id,
            )
            if fallback is not None:
                candidates.append(fallback)
            else:
                rejected.append("default_cross_category_blocked")

        candidates.sort(
            key=lambda candidate: (
                candidate.score,
                max((len(term) for term in candidate.matched_terms), default=0),
            ),
            reverse=True,
        )
        return ScenarioClassification(candidates=tuple(candidates), rejected_reasons=tuple(dict.fromkeys(rejected)))


class EmbeddingScenarioClassifier:
    """Optional semantic classifier for deployments with a managed embedding service.

    It is deliberately confidence-gated. Low-confidence embedding matches only
    produce metadata and must not silently override rule/config routing.
    """

    def __init__(
        self,
        embed_texts: Callable[[list[str]], list[list[float]]],
        *,
        min_similarity: float = 0.72,
        margin: float = 0.06,
    ) -> None:
        self.embed_texts = embed_texts
        self.min_similarity = min_similarity
        self.margin = margin
        self._bundle_vectors: list[tuple[ScenarioBundleConfig, list[float]]] | None = None

    def classify(self, bundles: Sequence[ScenarioBundleConfig], message: str) -> ScenarioClassification:
        profiles = self._profiles_with_vectors(bundles)
        if not profiles:
            return ScenarioClassification(candidates=(), rejected_reasons=("embedding_profiles_empty",))
        query_vector = self.embed_texts([message])[0]
        scored = [
            (cosine_similarity(query_vector, vector), bundle)
            for bundle, vector in profiles
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_bundle = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if best_score < self.min_similarity or (second_score and best_score - second_score < self.margin):
            return ScenarioClassification(
                candidates=(),
                rejected_reasons=("embedding_low_confidence",),
                used_embedding=True,
            )
        context_variables, context_signals = extract_context_variables(best_bundle, message)
        return ScenarioClassification(
            candidates=(
                ScenarioCandidate(
                    bundle=best_bundle,
                    score=best_score * 100.0,
                    matched_terms=tuple(context_variables.values()),
                    signals=("embedding_match", *context_signals),
                    context_variables=context_variables,
                    source="embedding_classifier",
                ),
            ),
            used_embedding=True,
        )

    def _profiles_with_vectors(
        self,
        bundles: Sequence[ScenarioBundleConfig],
    ) -> list[tuple[ScenarioBundleConfig, list[float]]]:
        if self._bundle_vectors is None:
            texts = [scenario_profile_text(bundle) for bundle in bundles]
            vectors = self.embed_texts(texts) if texts else []
            self._bundle_vectors = list(zip(bundles, vectors, strict=False))
        return self._bundle_vectors


class HybridScenarioClassifier:
    def __init__(
        self,
        rule_classifier: RuleScenarioClassifier | None = None,
        embedding_classifier: EmbeddingScenarioClassifier | None = None,
    ) -> None:
        self.rule_classifier = rule_classifier or RuleScenarioClassifier()
        self.embedding_classifier = embedding_classifier

    def classify(
        self,
        bundles: Sequence[ScenarioBundleConfig],
        message: str,
        *,
        plan: SemanticPlan | None,
        filters: SearchFilters | None,
        session_id: str = "",
    ) -> ScenarioClassification:
        rule_result = self.rule_classifier.classify(
            bundles,
            message,
            plan=plan,
            filters=filters,
            session_id=session_id,
        )
        if rule_result.candidates or self.embedding_classifier is None:
            return rule_result
        embedding_result = self.embedding_classifier.classify(bundles, message)
        if not embedding_result.candidates:
            return ScenarioClassification(
                candidates=(),
                rejected_reasons=(*rule_result.rejected_reasons, *embedding_result.rejected_reasons),
                used_embedding=embedding_result.used_embedding,
            )
        return ScenarioClassification(
            candidates=embedding_result.candidates,
            rejected_reasons=rule_result.rejected_reasons,
            used_embedding=True,
        )


def extract_context_variables(bundle: ScenarioBundleConfig, message: str) -> tuple[dict[str, str], tuple[str, ...]]:
    context: dict[str, str] = {}
    signals: list[str] = []
    for key, terms in bundle.context_terms.items():
        for term in sorted((term for term in terms if term), key=len, reverse=True):
            if term in message:
                context[key] = term
                signals.append(f"context:{key}={term}")
                break
    return context, tuple(signals)


def build_default_cross_category_candidate(
    bundles: Sequence[ScenarioBundleConfig],
    message: str,
    *,
    plan: SemanticPlan,
    filters: SearchFilters | None,
    session_id: str,
) -> ScenarioCandidate | None:
    if not should_allow_default_cross_category_bundle(message=message, plan=plan, filters=filters):
        return None
    for bundle in bundles:
        if bundle.id == "cross_category_bundle" and bundle.enabled_for(session_id=session_id):
            return ScenarioCandidate(
                bundle=bundle,
                score=50.0,
                matched_terms=(),
                signals=("plan_intent:bundle", "fallback:cross_category_bundle"),
                context_variables={},
                source="planner_fallback",
            )
    return None


def scenario_profile_text(bundle: ScenarioBundleConfig) -> str:
    pieces: list[str] = [
        bundle.title,
        bundle.summary,
        *bundle.trigger_terms,
        *bundle.semantic_terms,
    ]
    for terms in bundle.context_terms.values():
        pieces.extend(terms)
    for slot in bundle.slots:
        pieces.extend([slot.label, slot.query_template, *slot.match_terms])
        pieces.extend(slot.filters.product_types)
        pieces.extend(slot.filters.keywords)
    return " ".join(piece for piece in pieces if piece)
