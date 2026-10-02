"""V2.3 authorization matrix: who may read, who may execute, and that storage stays untouched."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app as application_package
from app.core.security import SESSION_COOKIE
from app.db.types import utcnow
from app.domain.models import User, UserSession
from app.domain.roles import ROLE_CAPABILITIES
from tests.evidence_support import (
    BASE,
    OPERATIONAL_CASE_ID,
    RecordingStorage,
    audit_events,
    csrf_header,
    foreign_organization_client,
    install_recording_storage,
    preserve,
    second_case,
)
from tests.examination_support import (
    Examiner,
    add_preserved_object,
    coordinator_of,
    error_of,
    examiner,
    payload,
    quiesce_runs,  # noqa: F401  (autouse isolation fixture)
    run_row,
    runs_url,
    start,
)
from tests.test_evidence_intake_api import (
    application,
    evidence_client,  # noqa: F401  (pytest fixture shared with the V2.1 intake tests)
)

EXECUTE_ROLES = ("INVESTIGATOR", "RESEARCHER")
READ_ONLY_ROLES = ("REVIEWER", "AUDITOR")
NO_EXAMINATION_ROLES = ("CUSTODIAN",)


@dataclass
class World:
    """A PRESERVED object, a queued run, and a recording storage that must stay untouched."""

    client: TestClient
    item: Any
    run_id: str
    storage: RecordingStorage

    def request(self, who: Examiner, kind: str) -> Any:
        methods = f"{BASE}/{OPERATIONAL_CASE_ID}/examination/methods"
        return {
            "methods": lambda: who.get(methods),
            "list": lambda: who.get(runs_url()),
            "detail": lambda: who.get(runs_url(f"/{self.run_id}")),
            "create": lambda: who.start(self.item),
            "cancel": lambda: who.post(runs_url(f"/{self.run_id}/cancel")),
            "retry": lambda: who.post(
                runs_url(f"/{self.run_id}/retry"), {"idempotency_key": "matrix-retry-0001"}
            ),
        }[kind]()


READS = ("methods", "list", "detail")
WRITES = ("create", "cancel", "retry")


@pytest.fixture
def world(evidence_client: TestClient) -> World:  # noqa: F811
    item = preserve(evidence_client)
    run = start(examiner(evidence_client), item)
    storage = install_recording_storage(evidence_client)
    return World(evidence_client, item, run["id"], storage)


def storage_untouched(world: World) -> None:
    assert world.storage.streams == [] and world.storage.read_sizes == []


# --- Roles (centralized in domain.roles) ----------------------------------------------------


def test_examination_capabilities_are_assigned_only_by_the_central_role_map() -> None:
    can_read = {r for r, caps in ROLE_CAPABILITIES.items() if "examination:read" in caps}
    can_execute = {r for r, caps in ROLE_CAPABILITIES.items() if "examination:execute" in caps}
    assert can_read == {"INVESTIGATOR", "RESEARCHER", "REVIEWER", "AUDITOR"}
    assert can_execute == {"INVESTIGATOR", "RESEARCHER"}
    # An examination reads the evidence bytes, so no role may examine what it may not read.
    assert all("evidence:read" in ROLE_CAPABILITIES[role] for role in can_execute)
    assert (
        "examination:execute" not in ROLE_CAPABILITIES["ADMINISTRATOR"]
    )  # identity != examination
    assert not ROLE_CAPABILITIES["CUSTODIAN"] & {"examination:read", "examination:execute"}
    # Nothing implies it: no wildcard capability exists.
    assert not any("*" in cap for caps in ROLE_CAPABILITIES.values() for cap in caps)


def test_the_execute_capability_is_named_only_where_it_is_defined_and_enforced() -> None:
    """Exactly two code sites: the role map (definition) and the route guard (enforcement)."""
    root = Path(application_package.__file__).parent
    sites = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if any(
            isinstance(node, ast.Constant) and node.value == "examination:execute"
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    )
    assert sites == ["api/examination.py", "domain/roles.py"]


def test_the_session_reports_execute_only_for_roles_that_hold_it(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    for role, expected in [("INVESTIGATOR", True), ("RESEARCHER", True), ("REVIEWER", False)]:
        who = examiner(evidence_client, role)
        session = who.get("/api/v1/auth/session").json()
        scoped = session["case_capabilities"][OPERATIONAL_CASE_ID]
        assert ("examination:execute" in scoped) is expected, role
        assert ("examination:read" in scoped) is True


# --- The matrix -----------------------------------------------------------------------------


@pytest.mark.parametrize("role", [*EXECUTE_ROLES, *READ_ONLY_ROLES])
@pytest.mark.parametrize("kind", READS)
def test_examination_readers_may_read(world: World, role: str, kind: str) -> None:
    case_id = None if role == "AUDITOR" else OPERATIONAL_CASE_ID
    who = examiner(world.client, role, case_id)
    response = world.request(who, kind)
    assert response.status_code == 200, response.text
    storage_untouched(world)


@pytest.mark.parametrize("role", EXECUTE_ROLES)
def test_executors_may_queue_cancel_and_retry(world: World, role: str) -> None:
    who = examiner(world.client, role)
    created = world.request(who, "create")
    assert created.status_code == 201, created.text
    cancel = who.post(runs_url(f"/{created.json()['id']}/cancel"))
    assert cancel.status_code == 200 and cancel.json()["state"] == "cancelled"
    retry = who.post(
        runs_url(f"/{created.json()['id']}/retry"),
        {"idempotency_key": f"{role.lower()}-retry-00001"},
    )
    assert retry.status_code == 201, retry.text


@pytest.mark.parametrize("role", [*READ_ONLY_ROLES, *NO_EXAMINATION_ROLES, "ADMINISTRATOR"])
@pytest.mark.parametrize("kind", WRITES)
def test_everyone_else_cannot_change_a_run(world: World, role: str, kind: str) -> None:
    case_id = None if role in {"AUDITOR", "ADMINISTRATOR"} else OPERATIONAL_CASE_ID
    who = examiner(world.client, role, case_id)
    runs_before = run_row(world.client, world.run_id)
    denials_before = len(audit_events(world.client, action="authorization.denied"))
    response = world.request(who, kind)
    error_of(response, 404, "not_found")  # masked: indistinguishable from a missing resource
    assert response.json()["error"]["message"] in {"Resource was not found", "Case was not found"}
    assert run_row(world.client, world.run_id) == runs_before
    assert len(audit_events(world.client, action="authorization.denied")) >= denials_before
    storage_untouched(world)


@pytest.mark.parametrize("kind", READS)
def test_custodians_and_administrators_cannot_read_examination(world: World, kind: str) -> None:
    for role, case_id in [("CUSTODIAN", OPERATIONAL_CASE_ID), ("ADMINISTRATOR", None)]:
        who = examiner(world.client, role, case_id)
        error_of(world.request(who, kind), 404, "not_found")
    storage_untouched(world)


def test_denied_execution_is_audited_as_an_authorization_failure(world: World) -> None:
    reviewer = examiner(world.client, "REVIEWER")
    before = len(audit_events(world.client, action="authorization.denied"))
    error_of(world.request(reviewer, "create"), 404)
    denials = audit_events(world.client, action="authorization.denied")
    assert len(denials) == before + 1
    assert denials[-1].details == {"capability": "examination:execute"}
    assert denials[-1].actor.startswith("USR-")


# --- Anonymous, demonstration, and broken sessions ---------------------------------------------


@pytest.mark.parametrize("kind", [*READS, *WRITES])
def test_unauthenticated_requests_are_rejected_before_anything_else(
    world: World, kind: str
) -> None:
    anonymous = Examiner(TestClient(application(world.client)), "no-session")
    response = world.request(anonymous, kind)
    error_of(response, 401)
    storage_untouched(world)


def test_the_anonymous_demonstration_viewer_can_read_but_never_execute(client: TestClient) -> None:
    demo = f"{BASE}/CASE-001"
    assert client.get(f"{demo}/analysis-runs").json() == {"items": [], "count": 0}
    assert client.get(f"{demo}/examination/methods").status_code == 200
    body = {
        "evidence_id": "EVD-001",
        "evidence_object_id": "EOBJ-001",
        "method_key": "core.binary_characteristics",
        "method_version": "1.0",
        "parameters": {},
        "idempotency_key": "demo-viewer-key-0001",
    }
    error_of(client.post(f"{demo}/analysis-runs", json=body), 404, "not_found")
    error_of(client.post(f"{demo}/analysis-runs/ANL-001/cancel"), 404, "not_found")
    session = client.get("/api/v1/auth/session").json()
    assert "examination:execute" not in session["capabilities"]
    assert "examination:execute" not in session["case_capabilities"]["*"]
    assert "examination:read" in session["capabilities"]


@pytest.mark.parametrize("role", EXECUTE_ROLES)
def test_an_authorized_executor_still_cannot_execute_on_a_demonstration_case(
    evidence_client: TestClient,  # noqa: F811
    role: str,
) -> None:
    storage = install_recording_storage(evidence_client)
    who = examiner(evidence_client, role, "CASE-001")
    assert who.get(f"{BASE}/CASE-001/analysis-runs").status_code == 200  # may read
    body = {
        "evidence_id": "EVD-001",
        "evidence_object_id": "EOBJ-001",
        "method_key": "core.binary_characteristics",
        "method_version": "1.0",
        "parameters": {},
        "idempotency_key": f"demo-executor-{role.lower()}-1",
    }
    error_of(who.post(runs_url(case_id="CASE-001"), body), 404, "not_found")
    assert storage.streams == []
    assert who.get(f"{BASE}/CASE-001/analysis-runs").json()["count"] == 0


def test_the_service_refuses_demonstration_cases_even_if_the_route_were_bypassed(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    from sqlalchemy import select as sql_select

    from app.core.errors import DemonstrationExaminationError
    from app.core.security import Principal
    from app.domain.models import Case
    from app.schemas import AnalysisRunCreateIn
    from app.services import examination

    request = AnalysisRunCreateIn(
        evidence_id="EVD-001",
        evidence_object_id="EOBJ-001",
        method_key="core.binary_characteristics",
        method_version="1.0",
        parameters={},
        idempotency_key="bypass-attempt-0001",
    )
    with application(evidence_client).state.session_factory() as session:
        case = session.execute(sql_select(Case).where(Case.public_id == "CASE-001")).scalar_one()
        with pytest.raises(DemonstrationExaminationError):
            examination.create_run(
                session,
                case=case,
                principal=Principal(subject="USR-001", kind="user", authenticated=True),
                payload=request,
                registry=application(evidence_client).state.method_registry,
                storage=application(evidence_client).state.evidence_storage,
            )


def test_disabled_expired_and_revoked_sessions_cannot_execute_or_read(world: World) -> None:
    disabled = examiner(world.client)
    with application(world.client).state.session_factory() as session:
        record = (
            session.execute(select(UserSession).order_by(UserSession.created_at.desc()))
            .scalars()
            .first()
        )
        assert record is not None
        user = session.execute(select(User).where(User.id == record.user_id)).scalar_one()
        user.status = "disabled"
        session.commit()
    error_of(world.request(disabled, "create"), 401)
    error_of(world.request(disabled, "list"), 401)

    expired = examiner(world.client)
    with application(world.client).state.session_factory() as session:
        record = (
            session.execute(select(UserSession).order_by(UserSession.created_at.desc()))
            .scalars()
            .first()
        )
        assert record is not None
        record.expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
    error_of(world.request(expired, "create"), 401)

    revoked = examiner(world.client)
    token = revoked.client.cookies.get(SESSION_COOKIE)
    assert revoked.client.post(
        "/api/v1/auth/logout", headers=csrf_header(revoked.csrf)
    ).status_code in (200, 204)
    revoked.client.cookies.set(SESSION_COOKIE, str(token))
    error_of(world.request(revoked, "create"), 401)
    storage_untouched(world)


# --- Wrong Case, organization and identifiers ---------------------------------------------------


def test_a_role_on_another_case_or_organization_grants_nothing_here(world: World) -> None:
    other_case = second_case(world.client)
    elsewhere = examiner(world.client, "INVESTIGATOR", other_case)
    for kind in (*READS, *WRITES):
        error_of(world.request(elsewhere, kind), 404, "not_found")
    outsider, foreign_case = foreign_organization_client(world.client)
    outsider_who = Examiner(outsider, outsider.cookies.get("veritas_csrf") or "")
    for kind in (*READS, *WRITES):
        error_of(world.request(outsider_who, kind), 404, "not_found")
    # The outsider cannot touch their own Case with this Case's objects either.
    error_of(outsider_who.post(runs_url(case_id=foreign_case), payload(world.item)), 404)
    storage_untouched(world)
    assert world.client is not None


def test_malformed_identifiers_never_reach_storage_or_the_database(world: World) -> None:
    who = examiner(world.client)
    for suffix in ["/ANL-1", "/anl-001", "/ANL-ABC", "/../x", "/ANL-0012345678"]:
        response = who.get(runs_url(suffix))
        assert response.status_code in (404, 422), (suffix, response.status_code)
    for bad_case in ["CASE-1", "case-900", "CASE-900/../CASE-901"]:
        assert who.get(runs_url(case_id=bad_case)).status_code in (404, 422)
    storage_untouched(world)


# --- CSRF ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", WRITES)
def test_every_state_changing_request_requires_the_session_csrf_token(
    world: World, kind: str
) -> None:
    who = examiner(world.client)
    before = run_row(world.client, world.run_id)
    bodies: dict[str, Any] = {
        "create": ("POST", runs_url(), payload(world.item)),
        "cancel": ("POST", runs_url(f"/{world.run_id}/cancel"), None),
        "retry": (
            "POST",
            runs_url(f"/{world.run_id}/retry"),
            {"idempotency_key": "csrf-retry-0001"},
        ),
    }
    method, url, body = bodies[kind]
    for headers in ({}, {"X-CSRF-Token": "not-the-session-token"}):
        response = who.client.request(method, url, json=body, headers=headers)
        error_of(response, 403, "access_denied")
    assert run_row(world.client, world.run_id) == before
    storage_untouched(world)
    # Reads never need a token.
    assert who.client.get(runs_url()).status_code == 200


# --- Storage is untouched until an authorized request is found eligible --------------------------


def test_ineligible_requests_from_authorized_users_do_not_touch_storage(world: World) -> None:
    who = examiner(world.client)
    quarantined = preserve(world.client, finalize=False)
    install = world.storage
    for body in [
        payload(quarantined),  # quarantined: not PRESERVED
        payload(world.item, method_key="core.unknown"),  # unknown method
        payload(world.item, parameters={"bogus": 1}),  # invalid parameters
        {**payload(world.item), "evidence_object_id": "EOBJ-998"},  # not found
    ]:
        assert who.post(runs_url(), body).status_code in (404, 409, 422)
    assert install.streams == [] and install.read_sizes == []
    # Only an authorized AND eligible creation opens the object (once, to confirm it is readable).
    created = who.start(world.item, key="storage-probe-0001")
    assert created.status_code == 201
    assert len(install.streams) == 1 and install.read_sizes == []  # opened and closed, never read
    assert install.all_closed()
    coordinator_of(world.client).run_pending()


def test_a_reviewers_attempt_on_a_real_object_leaves_every_other_object_alone(
    world: World,
) -> None:
    second = add_preserved_object(world.client, world.item, b"%PDF-1.7\nsecond object")
    reviewer = examiner(world.client, "REVIEWER")
    error_of(reviewer.start(second), 404)
    error_of(reviewer.start(world.item), 404)
    storage_untouched(world)
