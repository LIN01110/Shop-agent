"""
Purpose: 电商数据网关：CommerceDataGateway 商品数据查询服务，本地 JSON 数据源实现。
"""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from server.commerce.models import BusinessFact, ProductFacts, ProductIdentity


class CatalogService(Protocol):
    def field(self, product: dict, field_name: str) -> BusinessFact:
        ...


class PricingService(Protocol):
    def price(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        ...


class InventoryService(Protocol):
    def stock(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        ...


class PromotionService(Protocol):
    def coupon_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        ...


class PolicyService(Protocol):
    def invoice_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        ...

    def after_sales_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        ...


class LogisticsService(Protocol):
    def logistics_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        ...


@dataclass(frozen=True)
class CommerceServices:
    catalog: CatalogService
    pricing: PricingService
    inventory: InventoryService
    promotion: PromotionService
    policy: PolicyService
    logistics: LogisticsService


class CommerceDataGateway:
    """Stable business-data facade used by retrieval, cards, answers and evidence.

    Real deployments can replace each service independently while preserving the
    same gateway contract for the agent layer.
    """

    def __init__(self, services: CommerceServices) -> None:
        self.services = services

    def field(self, product: dict, field_name: str) -> BusinessFact:
        return self.services.catalog.field(product, field_name)

    def price(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        return self.services.pricing.price(product, sku_id)

    def stock(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        return self.services.inventory.stock(product, sku_id)

    def coupon_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        return self.services.promotion.coupon_policy(product, sku_id)

    def invoice_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        return self.services.policy.invoice_policy(product, sku_id)

    def after_sales_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        return self.services.policy.after_sales_policy(product, sku_id)

    def logistics_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        return self.services.logistics.logistics_policy(product, sku_id)

    def enrich_product(self, product: dict, sku_id: str | None = None) -> ProductFacts:
        product_id = str(product.get("id") or product.get("product_id") or "")
        selected_sku_id = sku_id or product.get("sku_id")
        if selected_sku_id is not None:
            selected_sku_id = str(selected_sku_id)

        facts = {
            "product_id": self.field(product, "id"),
            "sku_id": self.field(product, "sku_id"),
            "name": self.field(product, "name"),
            "brand": self.field(product, "brand"),
            "category": self.field(product, "category"),
            "product_types": self.field(product, "product_types"),
            "key_specs": self.field(product, "attributes"),
            "price": self.price(product, selected_sku_id),
            "stock": self.stock(product, selected_sku_id),
            "coupon_policy": self.coupon_policy(product, selected_sku_id),
            "invoice_policy": self.invoice_policy(product, selected_sku_id),
            "after_sales_policy": self.after_sales_policy(product, selected_sku_id),
            "logistics_policy": self.logistics_policy(product, selected_sku_id),
        }
        field_sources = {field: fact.evidence(product_id) for field, fact in facts.items()}
        missing_fields = [field for field, fact in facts.items() if not fact.available]
        return ProductFacts(
            identity=ProductIdentity(product_id=product_id, sku_id=selected_sku_id),
            catalog=dict(product),
            facts=facts,
            field_sources=field_sources,
            missing_fields=missing_fields,
            conflicts=detect_conflicts(product, facts),
        )


class CommerceOverrideStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._products = self._load(self.path)

    def section(self, product_id: str, section: str, sku_id: str | None = None) -> dict:
        product_payload = self._products.get(str(product_id), {})
        section_payload = product_payload.get(section, {})
        if not isinstance(section_payload, dict):
            return {}
        if sku_id:
            sku_payload = section_payload.get("skus", {}).get(str(sku_id), {})
            if isinstance(sku_payload, dict) and sku_payload:
                return sku_payload
        return {key: value for key, value in section_payload.items() if key != "skus"}

    def _load(self, path: Path | None) -> dict[str, dict]:
        if path is None or not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        products = payload.get("products", payload)
        if isinstance(products, list):
            return {
                str(item.get("product_id") or item.get("id")): item
                for item in products
                if isinstance(item, dict) and (item.get("product_id") or item.get("id"))
            }
        if isinstance(products, dict):
            return {str(key): value for key, value in products.items() if isinstance(value, dict)}
        return {}


class LocalCatalogService:
    def field(self, product: dict, field_name: str) -> BusinessFact:
        if field_name not in product or product.get(field_name) in (None, ""):
            return missing_fact(field_name, f"Catalog snapshot has no {field_name} field")
        return BusinessFact(
            field=field_name,
            value=product.get(field_name),
            source="product_catalog_snapshot",
            source_field=field_name,
            freshness="catalog_snapshot",
            authoritative=False,
        )


class LocalMockPricingService:
    def __init__(self, overrides: CommerceOverrideStore, clock: Callable[[], float] = time.time) -> None:
        self.overrides = overrides
        self.clock = clock

    def price(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        override = self.overrides.section(product_id(product), "pricing", sku_id)
        value = override.get("current_price")
        if value is None:
            value = round(parse_float(product.get("price"), default=0.0) * price_factor(product), 2)
        return BusinessFact(
            field="price",
            value=float(value),
            source="mock_pricing_service",
            source_field="current_price",
            freshness="mock_service",
            authoritative=False,
            updated_at=updated_at(override, self.clock),
        )


class LocalMockInventoryService:
    def __init__(self, overrides: CommerceOverrideStore, clock: Callable[[], float] = time.time) -> None:
        self.overrides = overrides
        self.clock = clock

    def stock(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        override = self.overrides.section(product_id(product), "inventory", sku_id)
        if "available_qty" in override:
            value = parse_int(override.get("available_qty"), default=0)
        elif "stock" in product:
            value = parse_int(product.get("stock"), default=0)
        else:
            return missing_fact("stock", "No inventory service value or catalog stock field")
        return BusinessFact(
            field="stock",
            value=value,
            source="mock_inventory_service",
            source_field="available_qty",
            freshness="mock_service",
            authoritative=False,
            updated_at=updated_at(override, self.clock),
        )


class LocalMockPromotionService:
    def __init__(self, overrides: CommerceOverrideStore, clock: Callable[[], float] = time.time) -> None:
        self.overrides = overrides
        self.clock = clock

    def coupon_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        override = self.overrides.section(product_id(product), "promotion", sku_id)
        value = {
            "has_coupon": bool(override.get("has_coupon", False)),
            "description": str(override.get("description", "")),
        } if override else default_promotion(product)
        return BusinessFact(
            field="coupon_policy",
            value=value,
            source="mock_promotion_service",
            source_field="coupon_policy",
            freshness="mock_service",
            authoritative=False,
            updated_at=updated_at(override, self.clock),
        )


class LocalMockPolicyService:
    def __init__(self, overrides: CommerceOverrideStore, clock: Callable[[], float] = time.time) -> None:
        self.overrides = overrides
        self.clock = clock

    def invoice_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        override = self.overrides.section(product_id(product), "invoice", sku_id)
        return BusinessFact(
            field="invoice_policy",
            value=override or default_invoice_policy(product),
            source="mock_invoice_policy_service",
            source_field="invoice_policy",
            freshness="mock_service",
            authoritative=False,
            updated_at=updated_at(override, self.clock),
        )

    def after_sales_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        override = self.overrides.section(product_id(product), "after_sales", sku_id)
        return BusinessFact(
            field="after_sales_policy",
            value=override or default_after_sales_policy(product),
            source="mock_after_sales_policy_service",
            source_field="after_sales_policy",
            freshness="mock_service",
            authoritative=False,
            updated_at=updated_at(override, self.clock),
        )


class LocalMockLogisticsService:
    def __init__(self, overrides: CommerceOverrideStore, clock: Callable[[], float] = time.time) -> None:
        self.overrides = overrides
        self.clock = clock

    def logistics_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        override = self.overrides.section(product_id(product), "logistics", sku_id)
        return BusinessFact(
            field="logistics_policy",
            value=override or default_logistics_policy(product),
            source="mock_logistics_service",
            source_field="logistics_policy",
            freshness="mock_service",
            authoritative=False,
            updated_at=updated_at(override, self.clock),
        )


def create_local_mock_commerce_gateway(
    data_path: str | Path | None = None,
    *,
    clock: Callable[[], float] | None = None,
) -> CommerceDataGateway:
    clock = clock or time.time
    overrides = CommerceOverrideStore(data_path)
    return CommerceDataGateway(
        CommerceServices(
            catalog=LocalCatalogService(),
            pricing=LocalMockPricingService(overrides, clock),
            inventory=LocalMockInventoryService(overrides, clock),
            promotion=LocalMockPromotionService(overrides, clock),
            policy=LocalMockPolicyService(overrides, clock),
            logistics=LocalMockLogisticsService(overrides, clock),
        )
    )


def detect_conflicts(product: dict, facts: dict[str, BusinessFact]) -> list[dict]:
    conflicts: list[dict] = []
    catalog_price = parse_float(product.get("price"), default=None)
    current_price = facts["price"].value if facts["price"].available else None
    if catalog_price is not None and current_price is not None and round(float(catalog_price), 2) != round(float(current_price), 2):
        conflicts.append(
            {
                "field": "price",
                "catalog_value": catalog_price,
                "service_value": current_price,
                "winner": facts["price"].source,
            }
        )
    catalog_stock = product.get("stock")
    current_stock = facts["stock"].value if facts["stock"].available else None
    if catalog_stock is not None and current_stock is not None and parse_int(catalog_stock) != parse_int(current_stock):
        conflicts.append(
            {
                "field": "stock",
                "catalog_value": parse_int(catalog_stock),
                "service_value": parse_int(current_stock),
                "winner": facts["stock"].source,
            }
        )
    return conflicts


def missing_fact(field_name: str, reason: str) -> BusinessFact:
    return BusinessFact(field=field_name, missing_reason=reason)


def product_id(product: dict) -> str:
    return str(product.get("id") or product.get("product_id") or "")


def stable_bucket(value: str, modulo: int) -> int:
    if modulo <= 1:
        return 0
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulo


def price_factor(product: dict) -> float:
    bucket = stable_bucket(product_id(product), 5)
    return (100 - bucket * 2) / 100


def default_promotion(product: dict) -> dict:
    price = parse_float(product.get("price"), default=0.0)
    has_coupon = price >= 300 and stable_bucket(product_id(product), 3) == 0
    return {
        "has_coupon": has_coupon,
        "description": "mock coupon available" if has_coupon else "mock promotion service found no coupon",
    }


def default_invoice_policy(product: dict) -> dict:
    category = str(product.get("category", ""))
    supports_enterprise_invoice = category not in {"food", "beverage"}
    return {
        "supports_invoice": True,
        "supports_enterprise_invoice": supports_enterprise_invoice,
        "types": ["general_invoice", "vat_special_invoice"] if supports_enterprise_invoice else ["general_invoice"],
    }


def default_after_sales_policy(product: dict) -> dict:
    return {
        "warranty": "subject_to_brand_policy",
        "seven_day_return": True,
        "opened_return_policy": "category_policy_required",
    }


def default_logistics_policy(product: dict) -> dict:
    stock = parse_int(product.get("stock"), default=0)
    ships_today = stock > 0 and stable_bucket(product_id(product), 4) != 0
    return {
        "ships_today": ships_today,
        "promise": "ships_today" if ships_today else "ships_in_1_to_2_days",
        "warehouse": f"mock-wh-{stable_bucket(product_id(product), 4) + 1}",
    }


def updated_at(payload: dict, clock: Callable[[], float]) -> float:
    if payload and payload.get("updated_at"):
        return parse_float(payload.get("updated_at"), default=clock())
    return clock()


def parse_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default
