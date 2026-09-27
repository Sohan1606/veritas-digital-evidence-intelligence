"""Health, readiness, system information and request correlation."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings


def test_health_is_dependency_free(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_database_migrated(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "schema": "ok"}}


def test_not_ready_when_schema_missing(tmp_path: Path) -> None:
    app = create_app(make_settings(f"sqlite:///{tmp_path / 'empty.sqlite3'}"))
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": "ok", "schema": "outdated"},
    }


def test_not_ready_when_database_unreachable() -> None:
    url = "postgresql+psycopg://nobody:placeholder@127.0.0.1:1/none"
    app = create_app(make_settings(url))
    with TestClient(app) as client:
        response = client.get("/ready")
        assert client.get("/health").status_code == 200
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "failed"


def test_system_info_reports_measured_state_only(client: TestClient, database_url: str) -> None:
    body = client.get("/api/v1/system").json()
    assert body["name"] == "VERITAS"
    assert body["api_version"] == "v1"
    assert body["access_mode"] == "demo"
    assert body["principal"] == "demonstration_viewer"
    assert body["database"]["schema_revision"] == body["database"]["expected_revision"]
    assert body["uptime_seconds"] >= 0
    keys = {c["key"]: c["status"] for c in body["capabilities"]}
    assert keys["identity"] == "reserved"
    assert keys["examination"] == "reserved"
    assert keys["evidence_intake"] == "reserved"
    # The database URL (and anything in it) must never be exposed.
    assert database_url not in client.get("/api/v1/system").text


def test_request_id_is_generated_and_returned(client: TestClient) -> None:
    response = client.get("/health")
    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32


def test_valid_incoming_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/api/v1/cases/CASE-999", headers={"X-Request-ID": "trace-abc-12345"})
    assert response.headers["x-request-id"] == "trace-abc-12345"
    assert response.json()["error"]["request_id"] == "trace-abc-12345"


def test_malicious_incoming_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": 'x"><script>alert(1)</script>'})
    assert "script" not in response.headers["x-request-id"]


def test_security_headers_present(client: TestClient) -> None:
    headers = client.get("/api/v1/cases").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    assert "default-src 'none'" in headers["content-security-policy"]


def test_api_docs_disabled_by_default(client: TestClient) -> None:
    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/openapi.json").status_code == 404
