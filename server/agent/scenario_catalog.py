"""
Purpose: 场景目录管理：从 data/scenario_bundles.json 加载场景化组合配置，提供场景匹配和检索。
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from server.agent.scenario_classifier import HybridScenarioClassifier, ScenarioClassification
from server.agent.scenario_models import ScenarioBundle, ScenarioBundleConfig, ScenarioCatalogConfig, ScenarioMatch
from server.agent.semantic_schema import SemanticPlan
from server.rag.post_process import SearchFilters


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_BUNDLE_PATH = ROOT_DIR / "data" / "scenario_bundles.json"


@dataclass(frozen=True)
class ScenarioRouteDecision:
    match: ScenarioMatch | None
    classification: ScenarioClassification

    def as_metadata(self) -> dict:
        return self.classification.as_metadata()


class ScenarioCatalog:
    def __init__(
        self,
        bundles: tuple[ScenarioBundleConfig, ...],
        *,
        source_version: str = "",
        source_path: Path | None = None,
        classifier: HybridScenarioClassifier | None = None,
    ) -> None:
        self.bundles = tuple(sorted(bundles, key=lambda item: item.priority, reverse=True))
        self.source_version = source_version
        self.source_path = source_path
        self.classifier = classifier or HybridScenarioClassifier()

    @classmethod
    def from_file(
        cls,
        path: str | Path = DEFAULT_SCENARIO_BUNDLE_PATH,
        *,
        classifier: HybridScenarioClassifier | None = None,
    ) -> "ScenarioCatalog":
        catalog_path = Path(path)
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        config = ScenarioCatalogConfig.model_validate(payload)
        return cls(
            tuple(config.bundles),
            source_version=config.version,
            source_path=catalog_path,
            classifier=classifier,
        )

    def route(
        self,
        message: str,
        *,
        plan: SemanticPlan | None = None,
        filters: SearchFilters | None = None,
        session_id: str = "",
    ) -> ScenarioMatch | None:
        return self.route_decision(
            message,
            plan=plan,
            filters=filters,
            session_id=session_id,
        ).match

    def route_decision(
        self,
        message: str,
        *,
        plan: SemanticPlan | None = None,
        filters: SearchFilters | None = None,
        session_id: str = "",
    ) -> ScenarioRouteDecision:
        message = message.strip()
        if not message:
            return ScenarioRouteDecision(
                match=None,
                classification=ScenarioClassification(candidates=(), rejected_reasons=("empty_message",)),
            )

        classification = self.classifier.classify(
            self.bundles,
            message=message,
            plan=plan,
            filters=filters,
            session_id=session_id,
        )
        candidate = classification.best
        if candidate is None:
            return ScenarioRouteDecision(match=None, classification=classification)

        runtime_bundle = candidate.bundle.to_runtime_bundle(
            message=message,
            matched_terms=candidate.matched_terms,
            source_version=self.source_version,
            confidence=candidate.confidence,
            signals=candidate.signals,
            filters=filters,
            context_variables=candidate.context_variables,
        )
        match = ScenarioMatch(
            bundle=runtime_bundle,
            score=candidate.score,
            confidence=candidate.confidence,
            signals=candidate.signals,
        )
        return ScenarioRouteDecision(match=match, classification=classification)

    def detect(
        self,
        message: str,
        *,
        plan: SemanticPlan | None = None,
        filters: SearchFilters | None = None,
        session_id: str = "",
    ) -> ScenarioBundle | None:
        match = self.route(message, plan=plan, filters=filters, session_id=session_id)
        return match.bundle if match is not None else None

def has_scenario_match(message: str) -> bool:
    return detect_scenario_bundle(message) is not None


def load_scenario_catalog(
    path: str | None = None,
    *,
    classifier: HybridScenarioClassifier | None = None,
) -> ScenarioCatalog:
    if classifier is not None:
        return ScenarioCatalog.from_file(path or DEFAULT_SCENARIO_BUNDLE_PATH, classifier=classifier)
    return _load_scenario_catalog_cached(path or str(DEFAULT_SCENARIO_BUNDLE_PATH))


@lru_cache
def _load_scenario_catalog_cached(path: str) -> ScenarioCatalog:
    return ScenarioCatalog.from_file(path)


def get_default_scenario_catalog() -> ScenarioCatalog:
    return load_scenario_catalog(str(DEFAULT_SCENARIO_BUNDLE_PATH))


def detect_scenario_bundle(
    message: str,
    catalog: ScenarioCatalog | None = None,
    *,
    plan: SemanticPlan | None = None,
    filters: SearchFilters | None = None,
    session_id: str = "",
) -> ScenarioBundle | None:
    return (catalog or get_default_scenario_catalog()).detect(
        message,
        plan=plan,
        filters=filters,
        session_id=session_id,
    )
