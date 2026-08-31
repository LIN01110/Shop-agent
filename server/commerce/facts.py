"""
Purpose: 商业事实提供者：CommerceFactProvider 协议定义价格/库存/促销获取接口，LocalMockCommerceProvider 本地实现。
"""

from pathlib import Path
from typing import Protocol

from server.commerce.models import BusinessFact
from server.commerce.services import CommerceDataGateway, create_local_mock_commerce_gateway


class CommerceFactProvider(Protocol):
    def price(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        ...

    def stock(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        ...

    def coupon_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        ...

    def invoice_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        ...

    def after_sales_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        ...

    def logistics_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        ...

    def field(self, product: dict, field_name: str) -> BusinessFact:
        ...


class GatewayFactProvider:
    """Compatibility facade for older modules that still ask for facts directly."""

    def __init__(self, gateway: CommerceDataGateway) -> None:
        self.gateway = gateway

    def price(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        return self.gateway.price(product, sku_id)

    def stock(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        return self.gateway.stock(product, sku_id)

    def coupon_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        return self.gateway.coupon_policy(product, sku_id)

    def invoice_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        return self.gateway.invoice_policy(product, sku_id)

    def after_sales_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        return self.gateway.after_sales_policy(product, sku_id)

    def logistics_policy(self, product: dict, sku_id: str | None = None) -> BusinessFact:
        return self.gateway.logistics_policy(product, sku_id)

    def field(self, product: dict, field_name: str) -> BusinessFact:
        return self.gateway.field(product, field_name)


class LocalMockCommerceProvider(GatewayFactProvider):
    """Local adapter with production-shaped service boundaries.

    It is intentionally non-authoritative. Replace any of the underlying
    services in `server.commerce.services` with real catalog, pricing,
    inventory, promotion or policy clients without changing agent code.
    """

    def __init__(self, data_path: str | Path | None = None, clock=None) -> None:
        super().__init__(create_local_mock_commerce_gateway(data_path, clock=clock))


_GATEWAY: CommerceDataGateway = create_local_mock_commerce_gateway()
_FACT_PROVIDER: CommerceFactProvider = GatewayFactProvider(_GATEWAY)


def get_commerce_gateway() -> CommerceDataGateway:
    return _GATEWAY


def configure_commerce_gateway(gateway: CommerceDataGateway) -> None:
    global _GATEWAY, _FACT_PROVIDER
    _GATEWAY = gateway
    _FACT_PROVIDER = GatewayFactProvider(gateway)


def get_fact_provider() -> CommerceFactProvider:
    return _FACT_PROVIDER


def configure_fact_provider(provider: CommerceFactProvider) -> None:
    global _GATEWAY, _FACT_PROVIDER
    _FACT_PROVIDER = provider
    gateway = getattr(provider, "gateway", None)
    if isinstance(gateway, CommerceDataGateway):
        _GATEWAY = gateway


__all__ = [
    "BusinessFact",
    "CommerceDataGateway",
    "CommerceFactProvider",
    "GatewayFactProvider",
    "LocalMockCommerceProvider",
    "configure_commerce_gateway",
    "configure_fact_provider",
    "get_commerce_gateway",
    "get_fact_provider",
]
