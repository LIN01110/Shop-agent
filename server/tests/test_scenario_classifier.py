"""
Purpose: 场景分类器测试：验证混合分类器置信度阈值。
"""

from server.agent.scenario_classifier import EmbeddingScenarioClassifier, HybridScenarioClassifier
from server.agent.scenarios import get_default_scenario_catalog


def test_embedding_scenario_classifier_is_optional_and_confidence_gated() -> None:
    catalog = get_default_scenario_catalog()

    def embed_texts(texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            if any(
                term in text
                for term in [
                    "\u6d77\u5c9b",
                    "\u6c99\u6ee9",
                    "\u9a6c\u5c14\u4ee3\u592b",
                    "\u6d6e\u6f5c",
                ]
            ):
                vectors.append([1.0, 0.0])
            elif any(term in text for term in ["\u901a\u52e4", "\u529e\u516c"]):
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.5, 0.5])
        return vectors

    classifier = HybridScenarioClassifier(
        embedding_classifier=EmbeddingScenarioClassifier(
            embed_texts,
            min_similarity=0.9,
            margin=0.1,
        )
    )
    result = classifier.classify(
        catalog.bundles,
        "\u6d6e\u6f5c\u88c5\u5907",
        plan=None,
        filters=None,
    )

    assert result.used_embedding is True
    assert result.best is not None
    assert result.best.bundle.id == "sanya_trip"
    assert result.best.source == "embedding_classifier"
