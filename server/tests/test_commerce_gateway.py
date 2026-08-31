"""
Purpose: 电商网关测试：验证商业事实提供者协议。
"""

import json

from server.commerce.facts import (
    LocalMockCommerceProvider,
    configure_fact_provider,
    get_commerce_gateway,
    get_fact_provider,
)


def test_local_commerce_gateway_splits_business_sources(tmp_path) -> None:
    payload = {
        "products": [
            {
                "product_id": "p_test",
                "pricing": {"current_price": 88, "updated_at": 1000},
                "inventory": {"available_qty": 7, "updated_at": 1001},
                "promotion": {"has_coupon": True, "description": "coupon", "updated_at": 1002},
                "invoice": {"supports_invoice": True, "supports_enterprise_invoice": True},
                "after_sales": {"seven_day_return": True},
                "logistics": {"ships_today": True, "promise": "today"},
            }
        ]
    }
    data_path = tmp_path / "commerce.json"
    data_path.write_text(json.dumps(payload), encoding="utf-8")
    provider = LocalMockCommerceProvider(data_path, clock=lambda: 2000)
    configure_fact_provider(provider)

    product = {
        "id": "p_test",
        "name": "Demo product",
        "brand": "Demo",
        "category": "Demo category",
        "price": 100,
        "stock": 1,
        "attributes": {"size": "M"},
    }

    facts = get_commerce_gateway().enrich_product(product)

    assert get_fact_provider().price(product).value == 88
    assert facts.fact("price").source == "mock_pricing_service"
    assert facts.fact("stock").source == "mock_inventory_service"
    assert facts.fact("coupon_policy").source == "mock_promotion_service"
    assert facts.fact("invoice_policy").source == "mock_invoice_policy_service"
    assert facts.fact("after_sales_policy").source == "mock_after_sales_policy_service"
    assert facts.fact("logistics_policy").source == "mock_logistics_service"
    assert facts.field_sources["price"]["field"] == "current_price"
    assert "sku_id" in facts.missing_fields
    assert facts.conflicts == [
        {
            "field": "price",
            "catalog_value": 100.0,
            "service_value": 88.0,
            "winner": "mock_pricing_service",
        },
        {
            "field": "stock",
            "catalog_value": 1,
            "service_value": 7,
            "winner": "mock_inventory_service",
        },
    ]
