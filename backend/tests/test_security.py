"""Security baseline: access boundary, CORS, hosts, limits, error hygiene, configuration."""

from __future__ import annotations

import io
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.logging import JsonFormatter
from app.db.session import get_session
from app.main import create_app
from app.services import records
from tests.conftest import make_settings

CASE_ROUTES = [
    "/api/v1/cases",
    "/api/v1/cases/CASE-001",
    "/api/v1/cases/CASE-001/evidence",
    "/api/v1/cases/CASE-001/evidence/EVD-001/profile",
    "/api/v1/cases/CASE-001/analysis-runs",
    "/api/v1/cases/CASE-001/findings",
    "/api/v1/cases/CASE-001/findings/FND-001",
    "/api/v1/cases/CASE-001/claims",
    "/api/v1/cases/CASE-001/graph",
    "/api/v1/cases/CASE-001/audit-events",
]


# --- Access boundary --------------------------------------------------------------------


@pytest.mark.parametrize("path", CASE_ROUTES)
def test_restricted_mode_fails_closed(database_url: str, path: str) -> None:
    app = create_app(make_settings(database_url, access_mode="restricted"))
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_unavailable"


def test_default_access_mode_is_restricted(database_url: str) -> None:
    settings = make_settings(database_url)
    assert type(settings).model_fields["access_mode"].default == "restricted"


def test_restricted_mode_keeps_operational_endpoints(database_url: str) -> None:
    app = create_app(make_settings(database_url, access_mode="restricted"))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/system").json()["principal"] is None


def test_non_demonstration_case_is_invisible_in_demo_mode(app: FastAPI, db: Session) -> None:
    restricted = records.create_case(
        db, actor="test:fixture", title="Restricted test case", summary=None, is_demonstration=False
    )
    # Serve requests from the test's rolled-back transaction so nothing is committed.
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as client:
        listed = [c["id"] for c in client.get("/api/v1/cases").json()["items"]]
        assert restricted.public_id not in listed
        assert "CASE-001" in listed
        # Indistinguishable from a nonexistent case.
        hidden = client.get(f"/api/v1/cases/{restricted.public_id}")
        missing = client.get("/api/v1/cases/CASE-999")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"]["code"] == missing.json()["error"]["code"]


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_no_write_api_exists(client: TestClient, method: str) -> None:
    response = getattr(client, method)("/api/v1/cases")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


# --- CORS / hosts / limits --------------------------------------------------------------


def test_cors_allows_configured_origin_only(client: TestClient) -> None:
    allowed = client.get("/api/v1/cases", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = client.get("/api/v1/cases", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in denied.headers


def test_cors_preflight_rejects_write_methods(client: TestClient) -> None:
    response = client.options(
        "/api/v1/cases",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 400


def test_untrusted_host_rejected(client: TestClient) -> None:
    response = client.get("/health", headers={"Host": "attacker.example"})
    assert response.status_code == 400


def test_oversized_body_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/cases", content=b"x" * (1_048_576 + 1))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


# --- Error hygiene ----------------------------------------------------------------------


@pytest.fixture
def failing_app(database_url: str) -> FastAPI:
    app = create_app(make_settings(database_url))

    @app.get("/api/v1/_test_failure")
    def _boom() -> None:
        raise RuntimeError("secret-internal-detail password=hunter2")

    return app


def test_unhandled_error_is_generic_and_correlated(failing_app: FastAPI) -> None:
    with TestClient(failing_app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/_test_failure")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    assert "secret-internal-detail" not in response.text
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"


def test_validation_errors_never_echo_input(client: TestClient) -> None:
    probe = "CASE-SECRET-TOKEN-abc123"
    response = client.get(f"/api/v1/cases/{probe}")
    assert response.status_code == 422
    assert probe not in response.text
    response = client.get("/api/v1/cases/CASE-001/audit-events", params={"limit": "DROP TABLE"})
    assert response.status_code == 422
    assert "DROP TABLE" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/cases/%00",
        "/api/v1/cases/CASE-001/evidence/../../etc/passwd",
        "/api/v1/cases/" + "9" * 5000,
    ],
)
def test_hostile_paths_do_not_crash(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code in (404, 422)
    assert "error" in response.json()


# --- Configuration ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"debug": True}, "VERITAS_DEBUG"),
        ({"cors_origins": ["*"]}, "VERITAS_CORS_ORIGINS"),
        ({"allowed_hosts": ["*"]}, "VERITAS_ALLOWED_HOSTS"),
        ({"database_url": "sqlite:///prod.db"}, "PostgreSQL"),
    ],
)
def test_production_rejects_unsafe_configuration(
    overrides: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://u:p@db/veritas",
        "environment": "production",
    }
    values.update(overrides)
    with pytest.raises(ValidationError, match=message):
        make_settings(**values)  # type: ignore[arg-type]


def test_database_url_is_secret_in_repr() -> None:
    settings = make_settings("postgresql+psycopg://user:s3cr3t-value@db/veritas")
    assert "s3cr3t-value" not in repr(settings)
    assert "s3cr3t-value" not in str(settings.model_dump())


def test_database_url_has_no_default() -> None:
    with pytest.raises(ValidationError):
        from app.core.config import Settings

        Settings.model_validate({})


def test_production_disables_api_docs() -> None:
    settings = make_settings(
        "postgresql+psycopg://u:p@db/veritas", environment="production", expose_api_docs=True
    )
    assert settings.api_docs_enabled is False


# --- Logging ----------------------------------------------------------------------------


def test_log_formatter_drops_unapproved_fields() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)
    record.request_body = "evidence content"
    record.password = "hunter2"
    record.route = "/api/v1/cases/{case_id}"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["route"] == "/api/v1/cases/{case_id}"
    assert "request_body" not in payload and "password" not in payload


def test_access_log_uses_route_template_without_query(client: TestClient) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("veritas.access")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        client.get("/api/v1/cases/CASE-001/audit-events?limit=7&token=abc")
    finally:
        logger.removeHandler(handler)
    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert line["route"] == "/api/v1/cases/{case_id}/audit-events"
    assert "token" not in stream.getvalue()
    assert line["request_id"]
