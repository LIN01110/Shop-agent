"""
Purpose: 上传测试：验证图片上传接口和存储。
"""

import json
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

from server.main import app


ROOT_DIR = Path(__file__).resolve().parents[2]
client = TestClient(app)


def test_upload_image_returns_image_id() -> None:
    session_id = f"pytest-upload-{uuid4()}"
    response = client.post(
        "/uploads/images",
        data={"session_id": session_id},
        files={"file": ("test.jpg", make_jpeg(), "image/jpeg")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["image_id"]) == 32
    assert payload["session_id"] == session_id
    assert payload["mime_type"] == "image/jpeg"
    assert payload["size_bytes"] > 0


def test_upload_image_rejects_non_image() -> None:
    response = client.post(
        "/uploads/images",
        files={"file": ("test.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 400


def test_chat_accepts_uploaded_image_id() -> None:
    session_id = f"pytest-image-id-search-{uuid4()}"
    image_bytes = (ROOT_DIR / "data" / "product_images" / "p_clothes_007_live.jpg").read_bytes()
    upload = client.post(
        "/uploads/images",
        data={"session_id": session_id},
        files={"file": ("shoe.jpg", image_bytes, "image/jpeg")},
    )
    image_id = upload.json()["image_id"]

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "find the same item in the image",
            "image_id": image_id,
        },
    )

    assert upload.status_code == 200
    assert response.status_code == 200
    assert "event: image_analysis" in response.text
    assert "p_clothes_007" in response.text
    assert "event: product_card" in response.text


def test_chat_accepts_uploaded_image_id_without_message() -> None:
    session_id = f"pytest-image-only-id-search-{uuid4()}"
    image_bytes = (ROOT_DIR / "data" / "product_images" / "p_clothes_007_live.jpg").read_bytes()
    upload = client.post(
        "/uploads/images",
        data={"session_id": session_id},
        files={"file": ("shoe.jpg", image_bytes, "image/jpeg")},
    )
    image_id = upload.json()["image_id"]

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "",
            "image_id": image_id,
        },
    )

    assert upload.status_code == 200
    assert response.status_code == 200
    assert "event: image_analysis" in response.text
    assert "p_clothes_007" in response.text


def test_chat_adds_first_visual_candidate_to_cart_after_image_turn() -> None:
    session_id = f"pytest-image-then-cart-{uuid4()}"
    image_bytes = (ROOT_DIR / "data" / "product_images" / "p_clothes_007_live.jpg").read_bytes()
    upload = client.post(
        "/uploads/images",
        data={"session_id": session_id},
        files={"file": ("shoe.jpg", image_bytes, "image/jpeg")},
    )
    image_id = upload.json()["image_id"]
    first = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "帮我找图里的同款或相似商品",
            "image_id": image_id,
        },
    )
    second = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "把第一款商品添加到购物车",
        },
    )

    assert upload.status_code == 200
    assert first.status_code == 200
    assert "event: product_card" in first.text
    assert second.status_code == 200
    assert "event: cart_update" in second.text
    assert "p_clothes_007" in second.text


def test_chat_does_not_read_image_uploaded_by_other_session() -> None:
    owner_session_id = f"pytest-image-owner-{uuid4()}"
    intruder_session_id = f"pytest-image-intruder-{uuid4()}"
    image_bytes = (ROOT_DIR / "data" / "product_images" / "p_clothes_007_live.jpg").read_bytes()
    upload = client.post(
        "/uploads/images",
        data={"session_id": owner_session_id},
        files={"file": ("shoe.jpg", image_bytes, "image/jpeg")},
    )
    image_id = upload.json()["image_id"]

    response = client.post(
        "/chat",
        json={
            "session_id": intruder_session_id,
            "message": "find the same item in the image",
            "image_id": image_id,
        },
    )

    assert upload.status_code == 200
    assert response.status_code == 200
    assert "event: image_analysis" not in response.text
    assert "p_clothes_007" not in response.text


def test_chat_rejects_empty_request_without_message_or_image() -> None:
    session_id = f"pytest-empty-chat-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "",
        },
    )

    assert response.status_code == 422


def test_chat_with_missing_image_id_degrades_to_text_chat() -> None:
    session_id = f"pytest-missing-image-id-{uuid4()}"
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "recommend a running shoe",
            "image_id": "0" * 32,
        },
    )

    assert response.status_code == 200
    assert "event: token" in response.text


def make_jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (16, 16), (255, 0, 0)).save(output, format="JPEG")
    return output.getvalue()
