"""
Purpose: 购物车 API 测试：验证直接购物车增删改查。
"""

from fastapi.testclient import TestClient
from uuid import uuid4

from server.main import app


client = TestClient(app)


def test_cart_api_adds_catalog_product_without_chat_turn() -> None:
    session_id = f"pytest-cart-api-direct-{uuid4()}"

    response = client.post(
        "/cart/items",
        json={
            "session_id": session_id,
            "product_id": "p_clothes_009",
            "quantity_delta": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_quantity"] == 1
    assert payload["items"][0]["product_id"] == "p_clothes_009"
    assert payload["items"][0]["name"].startswith("HOKA Clifton 9")


def test_cart_api_increments_and_decrements_same_item() -> None:
    session_id = f"pytest-cart-api-quantity-{uuid4()}"

    client.post(
        "/cart/items",
        json={"session_id": session_id, "product_id": "p_clothes_009", "quantity_delta": 1},
    )
    increased = client.post(
        "/cart/items",
        json={"session_id": session_id, "product_id": "p_clothes_009", "quantity_delta": 1},
    )
    decreased = client.post(
        "/cart/items",
        json={"session_id": session_id, "product_id": "p_clothes_009", "quantity_delta": -1},
    )

    assert increased.status_code == 200
    assert increased.json()["items"][0]["quantity"] == 2
    assert decreased.status_code == 200
    assert decreased.json()["items"][0]["quantity"] == 1


def test_cart_api_reads_current_session_cart() -> None:
    session_id = f"pytest-cart-api-read-{uuid4()}"

    add = client.post(
        "/cart/items",
        json={"session_id": session_id, "product_id": "p_beauty_016", "quantity_delta": 1},
    )
    read = client.get("/cart", params={"session_id": session_id})

    assert add.status_code == 200
    assert read.status_code == 200
    payload = read.json()
    assert payload["total_quantity"] == 1
    assert payload["items"][0]["product_id"] == "p_beauty_016"
