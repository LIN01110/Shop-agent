"""
Purpose: 类目治理：为商品记录打 taxonomy 标签、生成审计 manifest，保证类目变更可追溯。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from server.rag.category_taxonomy import (
    DEFAULT_CATEGORY_TAXONOMY_PATH,
    category_display_names,
    infer_category_ids,
    load_category_taxonomy,
)
from server.rag.taxonomy import (
    DEFAULT_TAXONOMY_PATH,
    infer_product_type_ids,
    load_product_taxonomy,
    product_type_display_names,
)


@dataclass(frozen=True)
class FieldAnnotation:
    values: tuple[str, ...]
    display_names: tuple[str, ...]
    source: str
    confidence: float
    evidence_fields: tuple[str, ...] = ()

    def as_metadata(self) -> dict:
        return {
            "values": list(self.values),
            "display_names": list(self.display_names),
            "source": self.source,
            "confidence": round(self.confidence, 4),
            "evidence_fields": list(self.evidence_fields),
        }


@dataclass(frozen=True)
class ProductTaxonomyAnnotation:
    product_id: str
    category: FieldAnnotation
    product_type: FieldAnnotation
    missing: tuple[str, ...] = ()

    def as_metadata(self) -> dict:
        return {
            "product_id": self.product_id,
            "category": self.category.as_metadata(),
            "product_type": self.product_type.as_metadata(),
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class TaxonomyManifest:
    product_taxonomy_version: str
    category_taxonomy_version: str
    product_type_count: int
    category_count: int
    fingerprint: str
    product_data_path: str = ""
    product_count: int = 0
    product_type_coverage: float = 0.0
    category_coverage: float = 0.0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_metadata(self) -> dict:
        return {
            "product_taxonomy_version": self.product_taxonomy_version,
            "category_taxonomy_version": self.category_taxonomy_version,
            "product_type_count": self.product_type_count,
            "category_count": self.category_count,
            "fingerprint": self.fingerprint,
            "product_data_path": self.product_data_path,
            "product_count": self.product_count,
            "product_type_coverage": round(self.product_type_coverage, 4),
            "category_coverage": round(self.category_coverage, 4),
            "warnings": list(self.warnings),
        }


def annotate_product_taxonomy(product: dict[str, Any]) -> ProductTaxonomyAnnotation:
    category_ids = tuple(infer_category_ids(product))
    product_type_ids = tuple(infer_product_type_ids(product))
    missing: list[str] = []

    if not category_ids:
        missing.append("category")
    if not product_type_ids:
        missing.append("product_type")

    return ProductTaxonomyAnnotation(
        product_id=str(product.get("id", "")),
        category=FieldAnnotation(
            values=category_ids,
            display_names=tuple(category_display_names(list(category_ids))),
            source=annotation_source(product, "category_ids"),
            confidence=0.95 if product.get("category_ids") else 0.82 if category_ids else 0.0,
            evidence_fields=evidence_fields(product, ["category", "sub_category", "tags", "category_ids"]),
        ),
        product_type=FieldAnnotation(
            values=product_type_ids,
            display_names=tuple(product_type_display_names(list(product_type_ids))),
            source=annotation_source(product, "product_types"),
            confidence=0.95 if product.get("product_types") else 0.78 if product_type_ids else 0.0,
            evidence_fields=evidence_fields(product, ["name", "sub_category", "tags", "product_types"]),
        ),
        missing=tuple(missing),
    )


def annotate_product_record(product: dict[str, Any]) -> dict[str, Any]:
    annotation = annotate_product_taxonomy(product)
    enriched = dict(product)
    if annotation.category.values:
        enriched["category_ids"] = list(annotation.category.values)
    if annotation.product_type.values:
        enriched["product_types"] = list(annotation.product_type.values)
    enriched["taxonomy_annotation"] = annotation.as_metadata()
    return enriched


def annotate_products(products: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict]:
    annotated = [annotate_product_record(product) for product in products]
    annotations = [item["taxonomy_annotation"] for item in annotated]
    product_count = len(annotated)
    typed = sum(1 for item in annotations if item["product_type"]["values"])
    categorized = sum(1 for item in annotations if item["category"]["values"])
    return annotated, {
        "product_count": product_count,
        "product_type_coverage": typed / product_count if product_count else 0.0,
        "category_coverage": categorized / product_count if product_count else 0.0,
        "missing_product_type_ids": [
            item["product_id"]
            for item in annotations
            if not item["product_type"]["values"]
        ],
        "missing_category_ids": [
            item["product_id"]
            for item in annotations
            if not item["category"]["values"]
        ],
    }


def build_taxonomy_manifest(
    *,
    product_taxonomy_path: Path = DEFAULT_TAXONOMY_PATH,
    category_taxonomy_path: Path = DEFAULT_CATEGORY_TAXONOMY_PATH,
    product_data_path: Path | None = None,
) -> TaxonomyManifest:
    product_payload = load_json(product_taxonomy_path)
    category_payload = load_json(category_taxonomy_path)
    product_types = load_product_taxonomy(str(product_taxonomy_path))
    categories = load_category_taxonomy(str(category_taxonomy_path))
    product_count = 0
    product_type_coverage = 0.0
    category_coverage = 0.0
    warnings: list[str] = []

    if product_data_path is not None and product_data_path.exists():
        products = load_json(product_data_path)
        if isinstance(products, list):
            _, report = annotate_products([item for item in products if isinstance(item, dict)])
            product_count = int(report["product_count"])
            product_type_coverage = float(report["product_type_coverage"])
            category_coverage = float(report["category_coverage"])
            if product_type_coverage < 0.7:
                warnings.append("product_type_coverage_below_70_percent")
            if category_coverage < 0.95:
                warnings.append("category_coverage_below_95_percent")

    fingerprint = taxonomy_fingerprint(product_taxonomy_path, category_taxonomy_path)
    return TaxonomyManifest(
        product_taxonomy_version=str(product_payload.get("version", "unknown")),
        category_taxonomy_version=str(category_payload.get("version", "unknown")),
        product_type_count=len(product_types),
        category_count=len(categories),
        fingerprint=fingerprint,
        product_data_path=str(product_data_path or ""),
        product_count=product_count,
        product_type_coverage=product_type_coverage,
        category_coverage=category_coverage,
        warnings=tuple(warnings),
    )


def taxonomy_fingerprint(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.name).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def annotation_source(product: dict[str, Any], explicit_field: str) -> str:
    if product.get(explicit_field):
        return "explicit_field"
    return "taxonomy_inference"


def evidence_fields(product: dict[str, Any], fields: list[str]) -> tuple[str, ...]:
    return tuple(field for field in fields if product.get(field))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
