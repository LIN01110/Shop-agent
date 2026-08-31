"""
Purpose: 商业事实模型：BusinessFact、ProductFacts、ProductIdentity 等数据类定义。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BusinessFact:
    field: str
    value: Any = None
    source: str | None = None
    source_field: str | None = None
    freshness: str = "missing"
    authoritative: bool = False
    missing_reason: str = ""
    updated_at: float | None = None

    @property
    def available(self) -> bool:
        return self.source is not None

    def evidence(self, product_id: str) -> dict:
        payload = {
            "source": self.source,
            "product_id": product_id,
            "field": self.source_field,
            "freshness": self.freshness,
            "authoritative": self.authoritative,
        }
        if self.updated_at is not None:
            payload["updated_at"] = self.updated_at
        if self.missing_reason:
            payload["reason"] = self.missing_reason
        return payload


@dataclass(frozen=True)
class ProductIdentity:
    product_id: str
    sku_id: str | None = None


@dataclass(frozen=True)
class ProductFacts:
    identity: ProductIdentity
    catalog: dict[str, Any]
    facts: dict[str, BusinessFact]
    field_sources: dict[str, dict] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)

    def fact(self, field_name: str) -> BusinessFact:
        return self.facts.get(
            field_name,
            BusinessFact(field=field_name, missing_reason=f"No fact registered for {field_name}"),
        )

    @property
    def product_id(self) -> str:
        return self.identity.product_id

    @property
    def sku_id(self) -> str | None:
        return self.identity.sku_id
