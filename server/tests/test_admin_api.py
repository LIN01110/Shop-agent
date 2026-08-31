"""
Purpose: Admin API 测试：验证管理后台页面可访问。
"""

from fastapi.testclient import TestClient

from server.main import app


client = TestClient(app)


def test_admin_index_is_served() -> None:
    response = client.get("/admin")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "RAG Shopping Agent Admin" in response.text
    assert "/admin/api/overview" in response.text


def test_admin_overview_returns_service_and_governance_summary() -> None:
    response = client.get("/admin/api/overview")

    payload = response.json()

    assert response.status_code == 200
    assert payload["service"]["status"] == "ok"
    assert "admin_console_enabled" in payload["service"]
    assert "readiness_checks" in payload
    assert payload["readiness_checks"]
    assert "recent_count" in payload["trace"]
    assert "reason_counts" in payload["query_feedback"]
    assert payload["taxonomy"]["product_type_count"] >= 1
    assert payload["taxonomy"]["fingerprint"]
    assert ":\\" not in payload["service"]["product_data"]["path"]
    assert ":\\" not in payload["query_feedback"]["path"]


def test_admin_taxonomy_returns_annotation_coverage() -> None:
    response = client.get("/admin/api/taxonomy")

    payload = response.json()

    assert response.status_code == 200
    assert payload["manifest"]["category_coverage"] >= 0
    assert ":\\" not in payload["manifest"]["product_data_path"]
    assert payload["annotation"]["product_count"] >= 1
    assert "missing_product_type_preview" in payload["annotation"]


def test_admin_taxonomy_eval_returns_pass_rate() -> None:
    response = client.get("/admin/api/eval/taxonomy")

    payload = response.json()

    assert response.status_code == 200
    assert payload["total"] >= 1
    assert payload["passed"] <= payload["total"]
    assert "failures" in payload


def test_admin_query_failures_handles_empty_or_existing_log() -> None:
    response = client.get("/admin/api/query-failures?limit=5")

    payload = response.json()

    assert response.status_code == 200
    assert "path" in payload
    assert isinstance(payload["items"], list)
    assert isinstance(payload["reason_counts"], dict)
