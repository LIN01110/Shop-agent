"""
Purpose: 类目治理测试：验证 taxonomy 标注和审计。
"""

import json

from server.rag.taxonomy_governance import (
    annotate_product_record,
    annotate_products,
    build_taxonomy_manifest,
)


def test_annotate_product_record_adds_auditable_taxonomy_fields() -> None:
    product = {
        "id": "p_test_cleanser",
        "name": "\u6c28\u57fa\u9178\u6d01\u9762\u6ce1\u6cab",
        "category": "\u7f8e\u5986\u62a4\u80a4",
        "sub_category": "\u6d01\u9762\u4e73",
        "tags": ["\u6d01\u9762", "\u654f\u611f\u808c"],
    }

    annotated = annotate_product_record(product)
    annotation = annotated["taxonomy_annotation"]

    assert annotated["category_ids"] == ["beauty.skincare"]
    assert annotated["product_types"] == ["beauty.cleanser"]
    assert annotation["product_type"]["source"] == "taxonomy_inference"
    assert "sub_category" in annotation["product_type"]["evidence_fields"]


def test_annotate_products_reports_coverage_and_missing_ids() -> None:
    products = [
        {
            "id": "typed",
            "name": "\u6e29\u548c\u6d17\u9762\u5976",
            "category": "\u7f8e\u5986\u62a4\u80a4",
            "sub_category": "\u6d01\u9762\u4e73",
        },
        {
            "id": "untyped",
            "name": "\u672a\u77e5\u5546\u54c1",
            "category": "\u672a\u77e5\u7c7b\u76ee",
            "sub_category": "\u672a\u77e5",
        },
    ]

    _, report = annotate_products(products)

    assert report["product_count"] == 2
    assert report["product_type_coverage"] == 0.5
    assert report["category_coverage"] == 1.0
    assert report["missing_product_type_ids"] == ["untyped"]


def test_taxonomy_manifest_is_versioned_and_fingerprinted(tmp_path) -> None:
    product_taxonomy = tmp_path / "product_taxonomy.json"
    category_taxonomy = tmp_path / "category_taxonomy.json"
    products = tmp_path / "products.json"
    product_taxonomy.write_text(
        json.dumps(
            {
                "version": "test-product-v1",
                "product_types": [
                    {
                        "id": "beauty.cleanser",
                        "display_name": "\u6d01\u9762\u4e73",
                        "aliases": ["\u6d01\u9762\u4e73"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    category_taxonomy.write_text(
        json.dumps(
            {
                "version": "test-category-v1",
                "categories": [
                    {
                        "id": "beauty.skincare",
                        "display_name": "\u7f8e\u5986\u62a4\u80a4",
                        "catalog_categories": ["\u7f8e\u5986\u62a4\u80a4"],
                        "aliases": ["\u7f8e\u5986\u62a4\u80a4"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    products.write_text("[]", encoding="utf-8")

    manifest = build_taxonomy_manifest(
        product_taxonomy_path=product_taxonomy,
        category_taxonomy_path=category_taxonomy,
        product_data_path=products,
    )

    assert manifest.product_taxonomy_version == "test-product-v1"
    assert manifest.category_taxonomy_version == "test-category-v1"
    assert manifest.product_type_count == 1
    assert manifest.category_count == 1
    assert len(manifest.fingerprint) == 16
