"""
Purpose: Sessions API 测试：验证会话状态获取和恢复。
"""

from fastapi.testclient import TestClient
from uuid import uuid4

from server.app_container import get_orchestrator
from server.main import app


client = TestClient(app)


def test_session_memory_endpoint_and_reset_clear_cart() -> None:
    session_id = f"pytest-session-reset-{uuid4()}"
    added = client.post(
        "/cart/items",
        json={
            "session_id": session_id,
            "product_id": "p_clothes_009",
            "quantity_delta": 1,
        },
    )
    memory_before = client.get(f"/sessions/{session_id}/memory")
    reset = client.delete(f"/sessions/{session_id}")
    memory_after = client.get(f"/sessions/{session_id}/memory")

    assert added.status_code == 200
    assert memory_before.status_code == 200
    assert memory_before.json()["cart"]["total_quantity"] == 1
    assert "HOKA" in memory_before.json()["user_profile"]["preferred_brands"]
    assert reset.status_code == 200
    assert reset.json()["cart"]["is_empty"] is True
    assert memory_after.status_code == 200
    assert memory_after.json()["cart"]["total_quantity"] == 0
    assert memory_after.json()["history_summary"] == ""


def test_sessions_list_and_snapshot_restore_history() -> None:
    session_id = f"pytest-session-history-{uuid4()}"
    orchestrator = get_orchestrator()
    session = orchestrator.sessions.get(session_id)
    session.add_user_message("recommend a running shoe")
    session.add_assistant_message("Here is a grounded recommendation.")
    session.history_summary = "User is looking for running shoes."
    session.cart.append(
        {
            "product_id": "p_clothes_009",
            "name": "HOKA Clifton 9",
            "brand": "HOKA",
            "category": "服饰运动",
            "price": 1099,
            "quantity": 1,
            "image_url": "",
            "detail_url": "",
        }
    )
    orchestrator.sessions.save(session)

    sessions = client.get("/sessions")
    snapshot = client.get(f"/sessions/{session_id}")

    assert sessions.status_code == 200
    summaries = sessions.json()["sessions"]
    assert any(item["session_id"] == session_id for item in summaries)
    summary = next(item for item in summaries if item["session_id"] == session_id)
    assert summary["title"] == "recommend a running shoe"
    assert summary["cart_quantity"] == 1

    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload["session_id"] == session_id
    assert payload["messages"][0] == {"role": "user", "content": "recommend a running shoe"}
    assert payload["cart"]["total_quantity"] == 1
