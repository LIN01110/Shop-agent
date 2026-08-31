"""
Purpose: 类目分类器测试：验证别名匹配和 embedding 分类。
"""

from server.nlu.taxonomy_classifier import EmbeddingTaxonomyClassifier, HybridTaxonomyClassifier, classify_taxonomy_query


def values(result, kind: str) -> set[str]:
    if kind == "product_type":
        return {item.value for item in result.product_types}
    return {item.value for item in result.categories}


def test_taxonomy_classifier_handles_common_aliases_and_typos() -> None:
    cases = [
        ("\u63a8\u8350\u4e00\u6b3e\u6d01\u9762\u4e73", "beauty.cleanser"),
        ("\u63a8\u8350\u4e00\u6b3e\u6d17\u9762\u5976", "beauty.cleanser"),
        ("\u6211\u60f3\u770b\u6240\u6709\u6d01\u9762\u6ce1\u6cab", "beauty.cleanser"),
        ("\u6211\u60f3\u770b\u897f\u9762\u6ce1\u6cab", "beauty.cleanser"),
        ("\u63a8\u8350\u4e00\u53cc\u978b\u5b50", "clothes.sports_shoes"),
        ("\u63a8\u8350\u4e00\u6761\u8fd0\u52a8\u88e4", "clothes.sports_pants"),
    ]

    for query, product_type in cases:
        result = classify_taxonomy_query(query)
        assert product_type in values(result, "product_type")


def test_taxonomy_classifier_keeps_out_of_catalog_queries_unscoped() -> None:
    for query in ["\u63a8\u8350\u4e00\u6b3e\u9999\u6c34", "\u6709\u6ca1\u6709\u706b\u7bad\u53d1\u52a8\u673a"]:
        result = classify_taxonomy_query(query)

        assert result.product_types == ()
        assert result.categories == ()


def test_taxonomy_classifier_detects_generic_category() -> None:
    result = classify_taxonomy_query("\u63a8\u8350\u4e00\u6b3e\u62a4\u80a4\u54c1")

    assert "beauty.skincare" in values(result, "category")
    assert result.product_types == ()


def test_embedding_classifier_is_optional_and_confidence_gated() -> None:
    vectors = {
        "\u672a\u77e5\u67e5\u8be2": [1.0, 0.0, 0.0],
    }

    def embed(texts: list[str]) -> list[list[float]]:
        output = []
        for text in texts:
            output.append(vectors.get(text, [0.0, 1.0, 0.0]))
        return output

    classifier = HybridTaxonomyClassifier(
        embedding_classifier=EmbeddingTaxonomyClassifier(
            embed,
            min_similarity=0.99,
            margin=0.5,
        )
    )

    result = classifier.classify("\u672a\u77e5\u67e5\u8be2")

    assert result.product_types == ()
    assert result.used_embedding is True
    assert "embedding_low_confidence" in result.notes
