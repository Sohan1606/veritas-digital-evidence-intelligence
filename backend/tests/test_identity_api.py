"""Automated identity/session lifecycle and backend authorization boundary tests."""

from __future__ import annotations

from datetime import timedelta
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import SESSION_COOKIE, hash_password
from app.db.types import utcnow
from app.domain.models import (
    AuditEvent,
    Case,
    Organization,
    OrganizationMembership,
    Role,
    RoleAssignment,
    User,
    UserSession,
)
from app.services.records import record_security_event

PASSWORD = "Correct-Horse-Battery-2026!"


def _application(client: TestClient) -> FastAPI:
    return cast(FastAPI, client.app)


def provision(
    client: TestClient, username: str, role_name: str, case_id: str | None = "CASE-001"
) -> str:
    factory = _application(client).state.session_factory
    with factory() as session:
        organization = session.execute(
            select(Organization).where(Organization.public_id == "ORG-001")
        ).scalar_one()
        user = User(
            username=username,
            display_name=f"Test {username}",
            status="active",
            password_hash=hash_password(PASSWORD),
            auth_provider=None,
            auth_subject=None,
            created_by="test:provisioning",
            updated_by="test:provisioning",
        )
        session.add(user)
        session.flush()
        membership = OrganizationMembership(
            user_id=user.id,
            organization_id=organization.id,
            status="active",
            created_by="test:provisioning",
            updated_by="test:provisioning",
        )
        session.add(membership)
        session.flush()
        role = session.execute(select(Role).where(Role.name == role_name)).scalar_one()
        case = None
        if case_id:
            case = session.execute(select(Case).where(Case.public_id == case_id)).scalar_one()
        session.add(
            RoleAssignment(
                membership_id=membership.id,
                role_id=role.id,
                case_id=case.id if case else None,
                created_by="test:provisioning",
                updated_by="test:provisioning",
            )
        )
        session.commit()
        return user.public_id


def login(client: TestClient, username: str) -> dict[str, object]:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return cast(dict[str, object], response.json())


def test_invalid_disabled_and_unknown_users_have_same_login_error(client: TestClient) -> None:
    user_id = provision(client, "disabled-user", "INVESTIGATOR")
    with _application(client).state.session_factory() as session:
        user = session.execute(select(User).where(User.public_id == user_id)).scalar_one()
        user.status = "disabled"
        session.commit()

    unknown = client.post(
        "/api/v1/auth/login", json={"username": "not-provisioned", "password": PASSWORD}
    )
    disabled = client.post(
        "/api/v1/auth/login", json={"username": "disabled-user", "password": PASSWORD}
    )
    wrong_password = client.post(
        "/api/v1/auth/login", json={"username": "disabled-user", "password": "wrong-password"}
    )
    assert unknown.status_code == disabled.status_code == wrong_password.status_code == 401
    assert (
        unknown.json()["error"]["code"] == disabled.json()["error"]["code"] == "invalid_credentials"
    )
    assert unknown.json()["error"]["message"] == disabled.json()["error"]["message"]
    assert PASSWORD not in unknown.text and "not-provisioned" not in unknown.text


def test_login_uses_opaque_http_only_session_cookie_and_audits_success(client: TestClient) -> None:
    user_id = provision(client, "auth-user", "INVESTIGATOR")
    response = client.post(
        "/api/v1/auth/login", json={"username": "auth-user", "password": PASSWORD}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is True
    assert payload["user_id"] == user_id
    assert payload["roles"] == ["INVESTIGATOR"]
    assert payload["case_capabilities"]["CASE-001"]
    assert "csrf_token" not in payload
    assert PASSWORD not in response.text
    token = client.cookies.get(SESSION_COOKIE)
    assert token and token not in response.text
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie
    assert client.get("/api/v1/cases/CASE-001").status_code == 200
    with _application(client).state.session_factory() as session:
        db_session = session.execute(
            select(UserSession).where(UserSession.public_id == payload["session_id"])
        ).scalar_one()
        assert db_session.token_hash != token
        assert len(db_session.token_hash) == 64
        event = (
            session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "authentication.success", AuditEvent.actor == user_id
                )
            )
            .scalars()
            .first()
        )
        assert event is not None and event.actor == user_id
        assert token not in str(event.details)


def test_expired_and_revoked_sessions_are_rejected(client: TestClient) -> None:
    provision(client, "expiry-user", "INVESTIGATOR")
    body = login(client, "expiry-user")
    session_id = body["session_id"]
    with _application(client).state.session_factory() as session:
        record = session.execute(
            select(UserSession).where(UserSession.public_id == session_id)
        ).scalar_one()
        record.expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
    response = client.get("/api/v1/cases/CASE-001")
    assert response.status_code == 401
    assert "token" not in response.text.lower()
    with _application(client).state.session_factory() as session:
        record = session.execute(
            select(UserSession).where(UserSession.public_id == session_id)
        ).scalar_one()
        assert record.revoked_at is not None
        assert session.execute(
            select(AuditEvent).where(AuditEvent.action == "session.invalidated")
        ).first()


def test_logout_requires_csrf_and_invalidates_session_replay(client: TestClient) -> None:
    provision(client, "logout-user", "INVESTIGATOR")
    login(client, "logout-user")
    token = client.cookies.get(SESSION_COOKIE)
    csrf = client.cookies.get("veritas_csrf")
    assert token and csrf
    missing_csrf = client.post("/api/v1/auth/logout")
    assert missing_csrf.status_code == 403
    assert client.get("/api/v1/cases/CASE-001").status_code == 200
    logout_response = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout_response.status_code == 200
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/api/v1/cases/CASE-001").status_code == 401


def test_case_scoped_role_grant_prevents_idor_cross_case_and_unknown_case(
    client: TestClient,
) -> None:
    provision(client, "case-one-only", "INVESTIGATOR", case_id="CASE-001")
    login(client, "case-one-only")
    assert client.get("/api/v1/cases/CASE-001").status_code == 200
    assert client.get("/api/v1/cases/CASE-001/evidence/EVD-001/profile").status_code == 200
    denied = client.get("/api/v1/cases/CASE-002")
    missing = client.get("/api/v1/cases/CASE-999")
    cross_case_evidence = client.get("/api/v1/cases/CASE-002/evidence/EVD-001/profile")
    assert denied.status_code == missing.status_code == cross_case_evidence.status_code == 404
    assert denied.json()["error"]["code"] == missing.json()["error"]["code"]
    assert client.get("/api/v1/cases/CASE-002/graph").status_code == 404


def test_role_boundary_denies_ungranted_resource_capability(client: TestClient) -> None:
    provision(client, "custodian-only", "CUSTODIAN", case_id="CASE-001")
    login(client, "custodian-only")
    assert client.get("/api/v1/cases/CASE-001/evidence").status_code == 200
    response = client.get("/api/v1/cases/CASE-001/claims")
    assert response.status_code == 404
    with _application(client).state.session_factory() as session:
        denial = session.execute(
            select(AuditEvent).where(
                AuditEvent.action == "authorization.denied",
                AuditEvent.actor == "USR-" + "999999999",
            )
        ).first()
        assert (
            denial is None
        )  # the denial itself is audited without exposing the protected claim data
        assert session.execute(
            select(AuditEvent).where(AuditEvent.action == "authorization.denied")
        ).first()


def test_case_audit_requires_its_explicit_case_capability(client: TestClient) -> None:
    researcher = provision(client, "audit-researcher", "RESEARCHER", case_id="CASE-001")
    login(client, "audit-researcher")
    assert client.get("/api/v1/cases/CASE-001").status_code == 200
    assert client.get("/api/v1/cases/CASE-001/audit-events").status_code == 404

    # Preserve case-audit reads for the roles whose canonical map grants the capability.
    for role, case_id in (
        ("INVESTIGATOR", "CASE-001"),
        ("REVIEWER", "CASE-001"),
        ("CUSTODIAN", "CASE-001"),
        ("AUDITOR", None),
    ):
        username = f"audit-allowed-{role.lower()}"
        provision(client, username, role, case_id=case_id)
        login(client, username)
        assert client.get("/api/v1/cases/CASE-001/audit-events").status_code == 200
    assert researcher.startswith("USR-")


def test_security_audit_is_organization_scoped_and_excludes_global_unknown_login(
    client: TestClient,
) -> None:
    provision(client, "org-one-auditor", "AUDITOR", case_id=None)
    login(client, "org-one-auditor")
    application = _application(client)
    with application.state.session_factory() as session:
        org_one = session.execute(
            select(Organization).where(Organization.public_id == "ORG-001")
        ).scalar_one()
        org_two = Organization(
            name="Second Organization",
            status="active",
            created_by="test:provisioning",
            updated_by="test:provisioning",
        )
        session.add(org_two)
        session.flush()
        org_two_id = org_two.id
        record_security_event(
            session,
            actor="system:test",
            action="security.configuration_changed",
            entity_type="configuration",
            details={"marker": "org-one-only"},
            organization_id=org_one.id,
        )
        record_security_event(
            session,
            actor="system:test",
            action="security.configuration_changed",
            entity_type="configuration",
            details={"marker": "org-two-secret"},
            organization_id=org_two.id,
        )
        org_two_user = User(
            username="org-two-known-login",
            display_name="ORG-002 User",
            status="active",
            password_hash=hash_password(PASSWORD),
            auth_provider=None,
            auth_subject=None,
            created_by="test:provisioning",
            updated_by="test:provisioning",
        )
        session.add(org_two_user)
        session.flush()
        org_two_user_public_id = org_two_user.public_id
        session.add(
            OrganizationMembership(
                user_id=org_two_user.id,
                organization_id=org_two.id,
                status="active",
                created_by="test:provisioning",
                updated_by="test:provisioning",
            )
        )
        session.commit()

    wrong_password = client.post(
        "/api/v1/auth/login",
        json={"username": "org-two-known-login", "password": "incorrect-password"},
    )
    assert wrong_password.status_code == 401
    # No principal or organization can be inferred for an unknown username. Its event
    # remains NULL-scoped rather than being attached to another tenant by default.
    unknown = client.post(
        "/api/v1/auth/login", json={"username": "unknown-cross-org-user", "password": PASSWORD}
    )
    assert unknown.status_code == 401
    with application.state.session_factory() as session:
        unknown_failure = session.execute(
            select(AuditEvent).where(
                AuditEvent.action == "authentication.failure",
                AuditEvent.entity_public_id.is_(None),
                AuditEvent.request_id == unknown.headers["x-request-id"],
            )
        ).scalar_one()
        assert unknown_failure.organization_id is None
        unknown_event_public_id = unknown_failure.public_id
        org_two_failure = session.execute(
            select(AuditEvent).where(
                AuditEvent.action == "authentication.failure",
                AuditEvent.entity_public_id == org_two_user_public_id,
            )
        ).scalar_one()
        assert org_two_failure.organization_id == org_two_id
        org_two_event_public_id = org_two_failure.public_id

    response = client.get("/api/v1/admin/security-audit")
    assert response.status_code == 200
    body = response.text
    assert "org-one-only" in body
    assert "org-two-secret" not in body
    assert "unknown-cross-org-user" not in body
    assert unknown_event_public_id not in body
    assert org_two_event_public_id not in body
    assert org_two_user_public_id not in body


def test_administrator_role_management_is_scoped_csrf_protected_and_audited(
    client: TestClient,
) -> None:
    admin_id = provision(client, "console-admin", "ADMINISTRATOR", case_id=None)
    target_id = provision(client, "console-target", "RESEARCHER", case_id="CASE-001")
    login(client, "console-admin")
    assert client.get("/api/v1/admin/users").status_code == 200
    assert client.get("/api/v1/admin/security-audit").status_code == 200
    assert (
        client.get("/api/v1/cases/CASE-001").status_code == 404
    )  # admin is not implicitly a case reader

    investigator_role = client.get("/api/v1/admin/roles").json()
    role_id = next(
        item["id"] for item in investigator_role["items"] if item["name"] == "INVESTIGATOR"
    )
    csrf = client.cookies.get("veritas_csrf")
    assert csrf
    no_csrf = client.post(
        f"/api/v1/admin/users/{target_id}/roles",
        json={"role_id": role_id, "case_id": "CASE-001"},
    )
    assert no_csrf.status_code == 403
    grant = client.post(
        f"/api/v1/admin/users/{target_id}/roles",
        json={"role_id": role_id, "case_id": "CASE-001"},
        headers={"X-CSRF-Token": csrf},
    )
    assert grant.status_code == 201
    assert "INVESTIGATOR" in grant.json()["roles"]
    assert grant.json()["id"] == target_id
    self_grant = client.post(
        f"/api/v1/admin/users/{admin_id}/roles",
        json={"role_id": role_id, "case_id": "CASE-001"},
        headers={"X-CSRF-Token": csrf},
    )
    assert self_grant.status_code == 403
    privileged_id = next(
        item["id"] for item in investigator_role["items"] if item["name"] == "ADMINISTRATOR"
    )
    denied = client.post(
        f"/api/v1/admin/users/{target_id}/roles",
        json={"role_id": privileged_id, "case_id": "CASE-001"},
        headers={"X-CSRF-Token": csrf},
    )
    assert denied.status_code == 403
    assert PASSWORD not in denied.text
    # Only actors with the security-audit capability can see global security events.
    with _application(client).state.session_factory() as session:
        events = (
            session.execute(select(AuditEvent).where(AuditEvent.action == "role.assigned"))
            .scalars()
            .all()
        )
        assert events and events[-1].actor == admin_id
        assert session.execute(
            select(AuditEvent).where(
                AuditEvent.action == "authorization.denied",
                AuditEvent.entity_type == "role",
            )
        ).first()


def test_non_admin_cannot_view_or_mutate_identity_directory(client: TestClient) -> None:
    provision(client, "regular-case-user", "INVESTIGATOR")
    login(client, "regular-case-user")
    assert client.get("/api/v1/admin/users").status_code == 403
    assert client.get("/api/v1/admin/security-audit").status_code == 403
    assert (
        client.patch("/api/v1/admin/users/USR-001/status", json={"status": "disabled"}).status_code
        == 403
    )


def test_disabled_user_session_is_revoked_immediately(client: TestClient) -> None:
    admin_id = provision(client, "disable-admin", "ADMINISTRATOR", case_id=None)
    target_id = provision(client, "session-to-disable", "INVESTIGATOR")
    # The admin session disables the other active User through the protected API.
    login(client, "disable-admin")
    regular_session_client = TestClient(client.app)
    regular_session_client.__enter__()
    try:
        login(regular_session_client, "session-to-disable")
        token = regular_session_client.cookies.get(SESSION_COOKIE)
        csrf = client.cookies.get("veritas_csrf")
        response = client.patch(
            f"/api/v1/admin/users/{target_id}/status",
            json={"status": "disabled"},
            headers={"X-CSRF-Token": str(csrf)},
        )
        assert response.status_code == 200
        regular_session_client.cookies.set(SESSION_COOKIE, str(token))
        assert regular_session_client.get("/api/v1/cases/CASE-001").status_code == 401
    finally:
        regular_session_client.__exit__(None, None, None)
    with _application(client).state.session_factory() as session:
        user = session.execute(select(User).where(User.public_id == target_id)).scalar_one()
        assert user.status == "disabled"
        assert session.execute(
            select(AuditEvent).where(
                AuditEvent.actor == admin_id, AuditEvent.action == "user.status_changed"
            )
        ).first()


def test_auth_validation_errors_do_not_echo_password_or_username(client: TestClient) -> None:
    sensitive = "password-token-secret" * 20
    response = client.post(
        "/api/v1/auth/login",
        json={"username": sensitive, "password": sensitive},
    )
    assert response.status_code == 422
    assert sensitive not in response.text
    assert "password" not in str(response.json().get("error", {}).get("details", []))


def test_repeated_invalid_credentials_trigger_persistent_rate_limit(client: TestClient) -> None:
    provision(client, "throttled-user", "INVESTIGATOR")
    attempts = [
        client.post(
            "/api/v1/auth/login",
            json={"username": "throttled-user", "password": "incorrect-password"},
        )
        for _ in range(5)
    ]
    assert all(response.status_code == 401 for response in attempts)
    locked = client.post(
        "/api/v1/auth/login", json={"username": "throttled-user", "password": PASSWORD}
    )
    assert locked.status_code == 401
    assert locked.json()["error"]["message"] == attempts[0].json()["error"]["message"]
    with _application(client).state.session_factory() as session:
        events = (
            session.execute(select(AuditEvent).where(AuditEvent.action == "authentication.failure"))
            .scalars()
            .all()
        )
        assert len(events) >= 6
        assert PASSWORD not in str([event.details for event in events])
