"""
Purpose: 类目分类器：混合分类（规则 + embedding 相似度），判断查询属于哪个商品类目。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from server.rag.category_taxonomy import CategoryMatch, extract_category_matches
from server.rag.scoring import tokenize
from server.rag.taxonomy import ProductTypeMatch, extract_product_type_matches, load_product_taxonomy, query_aliases


TaxonomyKind = Literal["product_type", "category"]


@dataclass(frozen=True)
class TaxonomyCandidate:
    kind: TaxonomyKind
    value: str
    label: str
    source: str
    confidence: float
    evidence: str

    def as_metadata(self) -> dict:
        return {
            "kind": self.kind,
            "value": self.value,
            "label": self.label,
            "source": self.source,
            "confidence": round(self.confidence, 4),
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class TaxonomyClassification:
    product_types: tuple[TaxonomyCandidate, ...] = ()
    categories: tuple[TaxonomyCandidate, ...] = ()
    used_embedding: bool = False
    notes: tuple[str, ...] = ()

    def as_metadata(self) -> dict:
        return {
            "product_types": [item.as_metadata() for item in self.product_types],
            "categories": [item.as_metadata() for item in self.categories],
            "used_embedding": self.used_embedding,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ProductTypeProfile:
    product_type_id: str
    label: str
    aliases: tuple[str, ...]
    tokens: frozenset[str]


class RuleProfileTaxonomyClassifier:
    """Deterministic taxonomy classifier with versioned aliases and profile fallback.

    The profile fallback is intentionally conservative. It only fills gaps when
    the query has strong overlap with a product-type profile; otherwise the
    planner/retrieval layers can continue without an unsafe hard filter.
    """

    def __init__(self, *, profile_min_score: float = 8.0, profile_margin: float = 2.0) -> None:
        self.profile_min_score = profile_min_score
        self.profile_margin = profile_margin

    def classify(self, message: str) -> TaxonomyClassification:
        product_candidates = self._rule_product_candidates(message)
        category_candidates = self._rule_category_candidates(message)
        notes: list[str] = []

        if not product_candidates:
            profile_candidate = self._profile_product_candidate(message)
            if profile_candidate:
                product_candidates = (profile_candidate,)
                notes.append("product_type_profile_fallback")

        return TaxonomyClassification(
            product_types=product_candidates,
            categories=category_candidates,
            notes=tuple(notes),
        )

    def _rule_product_candidates(self, message: str) -> tuple[TaxonomyCandidate, ...]:
        product_types = load_product_taxonomy()
        candidates: list[TaxonomyCandidate] = []
        for match in extract_product_type_matches(message):
            product_type = product_types.get(match.product_type_id)
            label = product_type.display_name if product_type else match.product_type_id
            candidates.append(
                TaxonomyCandidate(
                    kind="product_type",
                    value=match.product_type_id,
                    label=label,
                    source="taxonomy_alias",
                    confidence=0.98,
                    evidence=match.alias,
                )
            )
        return dedupe_candidates(candidates)

    def _rule_category_candidates(self, message: str) -> tuple[TaxonomyCandidate, ...]:
        candidates: list[TaxonomyCandidate] = []
        for match in extract_category_matches(message):
            candidates.append(
                TaxonomyCandidate(
                    kind="category",
                    value=match.category_id,
                    label=match.category_id,
                    source=f"category_{match.source}",
                    confidence=0.92 if match.source == "taxonomy" else 0.78,
                    evidence=match.alias,
                )
            )
        return dedupe_candidates(candidates)

    def _profile_product_candidate(self, message: str) -> TaxonomyCandidate | None:
        query_tokens = tokenize(message)
        if not query_tokens:
            return None
        scored: list[tuple[float, ProductTypeProfile, str]] = []
        normalized_message = message.lower()
        for profile in load_product_type_profiles():
            score, evidence = score_product_type_profile(normalized_message, query_tokens, profile)
            if score >= self.profile_min_score:
                scored.append((score, profile, evidence))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_profile, evidence = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if second_score and best_score - second_score < self.profile_margin:
            return None
        return TaxonomyCandidate(
            kind="product_type",
            value=best_profile.product_type_id,
            label=best_profile.label,
            source="product_type_profile",
            confidence=min(0.86, 0.55 + best_score / 40.0),
            evidence=evidence,
        )


class EmbeddingTaxonomyClassifier:
    """Optional embedding classifier for taxonomy labels.

    Production deployments can pass an embedding function backed by a managed
    embedding service. The default app path does not enable this class unless a
    caller wires it in, because low-quality embeddings should never silently
    convert open queries into hard category filters.
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
        self._profile_vectors: list[tuple[ProductTypeProfile, list[float]]] | None = None

    def classify(self, message: str) -> TaxonomyClassification:
        profiles = self._profiles_with_vectors()
        if not profiles:
            return TaxonomyClassification(notes=("embedding_profiles_empty",))
        query_vector = self.embed_texts([message])[0]
        scored = [
            (cosine_similarity(query_vector, vector), profile)
            for profile, vector in profiles
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_profile = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if best_score < self.min_similarity or (second_score and best_score - second_score < self.margin):
            return TaxonomyClassification(used_embedding=True, notes=("embedding_low_confidence",))
        return TaxonomyClassification(
            product_types=(
                TaxonomyCandidate(
                    kind="product_type",
                    value=best_profile.product_type_id,
                    label=best_profile.label,
                    source="embedding_classifier",
                    confidence=min(0.94, best_score),
                    evidence=best_profile.label,
                ),
            ),
            used_embedding=True,
        )

    def _profiles_with_vectors(self) -> list[tuple[ProductTypeProfile, list[float]]]:
        if self._profile_vectors is None:
            profiles = load_product_type_profiles()
            texts = [profile_text(profile) for profile in profiles]
            vectors = self.embed_texts(texts) if texts else []
            self._profile_vectors = list(zip(profiles, vectors, strict=False))
        return self._profile_vectors


class HybridTaxonomyClassifier:
    def __init__(
        self,
        rule_classifier: RuleProfileTaxonomyClassifier | None = None,
        embedding_classifier: EmbeddingTaxonomyClassifier | None = None,
    ) -> None:
        self.rule_classifier = rule_classifier or RuleProfileTaxonomyClassifier()
        self.embedding_classifier = embedding_classifier

    def classify(self, message: str) -> TaxonomyClassification:
        rule_result = self.rule_classifier.classify(message)
        if rule_result.product_types or self.embedding_classifier is None:
            return rule_result

        embedding_result = self.embedding_classifier.classify(message)
        if not embedding_result.product_types:
            return TaxonomyClassification(
                product_types=rule_result.product_types,
                categories=rule_result.categories,
                used_embedding=embedding_result.used_embedding,
                notes=(*rule_result.notes, *embedding_result.notes),
            )
        return TaxonomyClassification(
            product_types=embedding_result.product_types,
            categories=rule_result.categories,
            used_embedding=True,
            notes=(*rule_result.notes, "embedding_product_type_fallback"),
        )


@lru_cache
def default_taxonomy_classifier() -> HybridTaxonomyClassifier:
    return HybridTaxonomyClassifier()


def classify_taxonomy_query(message: str) -> TaxonomyClassification:
    return default_taxonomy_classifier().classify(message)


@lru_cache
def load_product_type_profiles() -> tuple[ProductTypeProfile, ...]:
    profiles: list[ProductTypeProfile] = []
    for product_type in load_product_taxonomy().values():
        aliases = query_aliases(product_type)
        profile_terms = [
            product_type.display_name,
            *aliases,
            *("+".join(group) for group in product_type.compound_aliases),
        ]
        profile = ProductTypeProfile(
            product_type_id=product_type.id,
            label=product_type.display_name,
            aliases=tuple(term for term in profile_terms if term),
            tokens=frozenset(token for term in profile_terms for token in tokenize(term)),
        )
        profiles.append(profile)
    return tuple(profiles)


def score_product_type_profile(
    normalized_message: str,
    query_tokens: set[str],
    profile: ProductTypeProfile,
) -> tuple[float, str]:
    score = 0.0
    evidence = ""
    for alias in sorted(profile.aliases, key=len, reverse=True):
        normalized_alias = alias.lower().replace(" ", "")
        if normalized_alias and normalized_alias in normalized_message:
            score += 8.0 + min(len(normalized_alias), 8) * 0.5
            evidence = evidence or alias
    overlap = query_tokens & profile.tokens
    if overlap:
        score += min(len(overlap), 6) * 1.0
        evidence = evidence or sorted(overlap, key=len, reverse=True)[0]
    return score, evidence or profile.label


def profile_text(profile: ProductTypeProfile) -> str:
    return " ".join(profile.aliases)


def dedupe_candidates(candidates: list[TaxonomyCandidate]) -> tuple[TaxonomyCandidate, ...]:
    by_value: dict[tuple[str, str], TaxonomyCandidate] = {}
    for candidate in candidates:
        key = (candidate.kind, candidate.value)
        current = by_value.get(key)
        if current is None or candidate.confidence > current.confidence:
            by_value[key] = candidate
    return tuple(by_value.values())


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
