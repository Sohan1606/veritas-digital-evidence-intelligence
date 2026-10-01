"""V2.2 authorized retrieval of PRESERVED bytes: authorization, exactness, headers, audit."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.security import SESSION_COOKIE
from app.db.types import utcnow
from app.domain.models import (
    AuditEvent,
    AuditEventImmutableError,
    Case,
    Evidence,
    EvidenceObject,
    User,
    UserSession,
)
from app.services.evidence_hashing import HASH_CHUNK_BYTES
from tests.evidence_support import (
    BASE,
    OPERATIONAL_CASE_ID,
    assert_only_an_audit_event_was_written,
    audit_events,
    captured_sql,
    content_url,
    csrf_header,
    deterministic_bytes,
    foreign_organization_client,
    login_as,
    object_snapshot,
    preserve,
    preserved_file,
    second_case,
)
from tests.test_evidence_intake_api import (
    application,
    evidence_client,  # noqa: F401  (pytest fixture shared with the V2.1 intake tests)
    login,
    provision,
    seed_preserved_demo_object,
    storage_root,
)

RETRIEVED = "evidence.object.retrieved"
ALL_CASE_ROLES = ["INVESTIGATOR", "REVIEWER", "CUSTODIAN", "RESEARCHER", "AUDITOR"]


def _assert_error_envelope(response: Any, status: int, code: str | None = None) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert {"code", "message", "request_id"} <= set(error)
    assert error["request_id"] == response.headers["x-request-id"]
    if code is not None:
        assert error["code"] == code
    return cast(dict[str, Any], error)


# --- Authorization ------------------------------------------------------------------------


@pytest.mark.parametrize("role", ALL_CASE_ROLES)
def test_every_authorized_role_retrieves_the_exact_bytes(
    evidence_client: TestClient,  # noqa: F811
    role: str,
) -> None:
    item = preserve(evidence_client)
    reader, _ = login_as(evidence_client, role)
    response = reader.get(item.content)
    assert response.status_code == 200, response.text
    assert response.content == item.body
    assert hashlib.sha256(response.content).hexdigest() == hashlib.sha256(item.body).hexdigest()


def test_organization_scoped_auditor_can_retrieve_within_its_organization(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    auditor, _ = login_as(evidence_client, "AUDITOR", None)
    response = auditor.get(item.content)
    assert response.status_code == 200 and response.content == item.body


def test_unauthenticated_request_is_rejected_before_any_storage_access(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    anonymous = TestClient(application(evidence_client))
    before = len(audit_events(evidence_client, action=RETRIEVED))
    response = anonymous.get(item.content)
    _assert_error_envelope(response, 401)
    assert response.content != item.body and item.storage_key not in response.text
    # Malformed identifiers must not turn an authentication failure into a 422.
    malformed = anonymous.get(f"{BASE}/{OPERATIONAL_CASE_ID}/evidence/EVD-x/objects/EOBJ-y/content")
    _assert_error_envelope(malformed, 401)
    assert len(audit_events(evidence_client, action=RETRIEVED)) == before


def test_administrator_is_denied_and_masked_as_not_found(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    admin, _ = login_as(evidence_client, "ADMINISTRATOR", None)
    denied = admin.get(item.content)
    _assert_error_envelope(denied, 404, "not_found")
    missing = admin.get(content_url("EVD-999", "EOBJ-999"))
    # A denied caller cannot distinguish an existing object from a nonexistent one.
    assert denied.json()["error"]["message"] == missing.json()["error"]["message"]
    assert denied.content != item.body


def test_disabled_user_expired_and_revoked_sessions_are_rejected(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)

    disabled, _ = login_as(evidence_client, "INVESTIGATOR")
    assert disabled.get(item.content).status_code == 200
    with application(evidence_client).state.session_factory() as session:
        token_owner = (
            session.execute(select(UserSession).order_by(UserSession.created_at.desc()))
            .scalars()
            .first()
        )
        assert token_owner is not None
        user = session.execute(select(User).where(User.id == token_owner.user_id)).scalar_one()
        user.status = "disabled"
        session.commit()
    _assert_error_envelope(disabled.get(item.content), 401)

    expired, _ = login_as(evidence_client, "REVIEWER")
    with application(evidence_client).state.session_factory() as session:
        record = (
            session.execute(select(UserSession).order_by(UserSession.created_at.desc()))
            .scalars()
            .first()
        )
        assert record is not None
        record.expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
    _assert_error_envelope(expired.get(item.content), 401)

    revoked, csrf = login_as(evidence_client, "RESEARCHER")
    assert revoked.get(item.content).status_code == 200
    token = revoked.cookies.get(SESSION_COOKIE)
    assert revoked.post("/api/v1/auth/logout", headers=csrf_header(csrf)).status_code in (200, 204)
    revoked.cookies.set(SESSION_COOKIE, str(token))
    _assert_error_envelope(revoked.get(item.content), 401)


def test_wrong_case_wrong_organization_and_guessed_identifiers_are_not_found(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    other_case = second_case(evidence_client)
    other_item = preserve(evidence_client, case_id=other_case)
    reader, _ = login_as(evidence_client, "INVESTIGATOR")

    # Role held on CASE-900 only: the same object id under another Case is denied.
    _assert_error_envelope(
        reader.get(content_url(item.evidence_id, item.object_id, other_case)), 404
    )
    # A valid object of another Case requested through this Case cannot resolve.
    cross_case = reader.get(content_url(item.evidence_id, other_item.object_id))
    _assert_error_envelope(cross_case, 404)
    # A valid Evidence id of another Case, and an object id belonging to a different Evidence.
    _assert_error_envelope(reader.get(content_url(other_item.evidence_id, item.object_id)), 404)
    second = preserve(evidence_client)
    mismatched = reader.get(content_url(item.evidence_id, second.object_id))
    _assert_error_envelope(mismatched, 404)
    # Guessed, well-formed but nonexistent identifiers.
    for guessed in (
        content_url("EVD-999", item.object_id),
        content_url(item.evidence_id, "EOBJ-999"),
        content_url("EVD-999999999", "EOBJ-999999999"),
    ):
        _assert_error_envelope(reader.get(guessed), 404)
    for response in (cross_case, mismatched):
        assert item.body not in response.content and other_item.body not in response.content

    # Membership of another Organization does not help, even with a role on its own Case.
    outsider, _ = foreign_organization_client(evidence_client)
    _assert_error_envelope(outsider.get(item.content), 404)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/cases/CASE-XYZ/evidence/EVD-001/objects/EOBJ-001/content",
        "/api/v1/cases/CASE-900/evidence/EVD-1/objects/EOBJ-001/content",
        "/api/v1/cases/CASE-900/evidence/EVD-001/objects/EOBJ-abc/content",
        "/api/v1/cases/CASE-900/evidence/EVD-001/objects/EOBJ-0000000000001/content",
        "/api/v1/cases/CASE-900/evidence/EVD-001/objects/EOBJ-001%2F..%2F..%2Fetc%2Fpasswd/content",
        "/api/v1/cases/CASE-900/evidence/EVD-001/objects/..%5C..%5Csecret/content",
        "/api/v1/cases/CASE-900/evidence/EVD-001/objects/%00/content",
    ],
)
def test_malformed_identifiers_never_reach_storage(
    evidence_client: TestClient,  # noqa: F811
    path: str,
) -> None:
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    before = len(audit_events(evidence_client, action=RETRIEVED))
    response = reader.get(path)
    assert response.status_code in (404, 422), response.text
    assert response.status_code != 200 and "traceback" not in response.text.lower()
    assert len(audit_events(evidence_client, action=RETRIEVED)) == before


def test_demonstration_cases_expose_no_retrieval_to_anyone(
    client: TestClient,
) -> None:
    evidence_id, item_id, _, _ = seed_preserved_demo_object(client)
    url = f"{BASE}/CASE-001/evidence/{evidence_id}/objects/{item_id}/content"
    anonymous = client.get(url)  # anonymous demonstration viewer
    _assert_error_envelope(anonymous, 404)
    username = f"v22-demo-inv-{uuid4().hex[:8]}"
    provision(client, username, "INVESTIGATOR", "CASE-001")
    login(client, username)
    _assert_error_envelope(client.get(url), 404)


@pytest.mark.parametrize("stage", ["registered", "uploaded"])
def test_quarantined_objects_are_never_retrievable(
    evidence_client: TestClient,  # noqa: F811
    stage: str,
) -> None:
    item = preserve(evidence_client, finalize=False)
    if stage == "registered":
        pass
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    response = reader.get(item.content)
    error = _assert_error_envelope(response, 409, "evidence_state_conflict")
    assert "preserved" in error["message"].lower()
    assert item.body not in response.content
    assert (storage_root(evidence_client) / "quarantine" / f"{item.storage_key}.part").is_file()


def test_rejected_objects_are_never_retrievable(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=b"plain text is not a PDF", finalize=False)
    custodian, csrf = login_as(evidence_client, "CUSTODIAN")
    finalized = custodian.post(
        f"{BASE}/{item.case_id}/evidence/{item.evidence_id}/objects/{item.object_id}/finalize",
        headers=csrf_header(csrf),
    )
    assert finalized.json()["state"] == "REJECTED"
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    _assert_error_envelope(reader.get(item.content), 409, "evidence_state_conflict")


def test_denied_attempts_record_no_retrieval_event_and_leave_the_object_untouched(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    before = object_snapshot(evidence_client, item)
    count = len(audit_events(evidence_client, action=RETRIEVED))
    for role in ("ADMINISTRATOR",):
        denied, _ = login_as(evidence_client, role, None)
        assert denied.get(item.content).status_code == 404
    assert TestClient(application(evidence_client)).get(item.content).status_code == 401
    assert len(audit_events(evidence_client, action=RETRIEVED)) == count
    assert object_snapshot(evidence_client, item) == before


# --- Bytes and headers --------------------------------------------------------------------


@pytest.mark.parametrize(
    "size",
    [
        0,
        1,
        HASH_CHUNK_BYTES - 1,
        HASH_CHUNK_BYTES,
        HASH_CHUNK_BYTES + 1,
        2 * HASH_CHUNK_BYTES,
        3 * HASH_CHUNK_BYTES + 17,
        1_500_000,
    ],
)
def test_bytes_are_exact_for_exact_chunk_boundaries(
    evidence_client: TestClient,  # noqa: F811
    size: int,
) -> None:
    if size <= 1:
        body, name, media = b"x" * size, "tiny.txt", "text/plain"
    else:
        body, name, media = deterministic_bytes(size, size), "synthetic.pdf", "application/pdf"
    assert len(body) == size
    item = preserve(evidence_client, body=body, filename=name, media_type=media)
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    response = reader.get(item.content)
    assert response.status_code == 200
    assert response.content == body
    assert response.headers["content-length"] == str(len(body))
    assert len(audit_events(evidence_client, action=RETRIEVED, entity_id=item.object_id)) == 1


def test_response_headers_are_safe_and_deterministic(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, filename="my report (final).pdf")
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    response = reader.get(item.content)
    assert response.status_code == 200
    headers = response.headers
    assert headers["content-type"] == "application/pdf"
    assert headers["content-length"] == str(len(item.body))
    assert headers["content-disposition"] == f'attachment; filename="{item.object_id}.bin"'
    assert "my report" not in headers["content-disposition"]
    assert headers["cache-control"] == "no-store"
    assert "default-src 'none'" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-request-id"]
    # nosniff is added by the global security-headers middleware and must not be duplicated.
    assert headers["x-content-type-options"] == "nosniff"
    assert [k for k, _ in response.headers.multi_items()].count("x-content-type-options") == 1
    assert "charset" not in headers["content-type"]
    # Byte ranges are not supported; a Range header is ignored and the whole object is returned.
    ranged = reader.get(item.content, headers={"Range": "bytes=0-9"})
    assert ranged.status_code == 200 and ranged.content == item.body
    assert "accept-ranges" not in ranged.headers


def test_text_objects_do_not_assert_a_charset(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(
        evidence_client,
        body=b"plain \xe2\x9c\x93 text\n",
        filename="note.txt",
        media_type="text/plain",
    )
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    response = reader.get(item.content)
    assert response.headers["content-type"] == "text/plain"
    assert response.content == item.body


def test_hostile_database_metadata_never_reaches_response_headers(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, filename="safe-name.pdf")
    hostile_name = 'evil"\r\nX-Injected: 1/../../etc/passwd.html'
    hostile_type = "text/html\r\nSet-Cookie: pwn=1"
    with application(evidence_client).state.session_factory() as session:
        # Core UPDATE bypasses the ORM immutability guard: simulates corrupted metadata.
        session.execute(
            update(EvidenceObject)
            .where(EvidenceObject.public_id == item.object_id)
            .values(original_filename=hostile_name, detected_media_type=hostile_type)
        )
        session.commit()
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    response = reader.get(item.content)
    assert response.status_code == 200 and response.content == item.body
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == f'attachment; filename="{item.object_id}.bin"'
    names = {k.lower() for k in response.headers}
    assert "x-injected" not in names and "set-cookie" not in names
    blob = "".join(f"{k}:{v}" for k, v in response.headers.items())
    assert "evil" not in blob and "passwd" not in blob and "pwn" not in blob


def test_no_path_storage_key_or_credential_leaks_in_any_response(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    root = str(storage_root(evidence_client))
    reader, csrf = login_as(evidence_client, "INVESTIGATOR")
    ok = reader.get(item.content)
    denied = TestClient(application(evidence_client)).get(item.content)
    missing = reader.get(content_url(item.evidence_id, "EOBJ-999"))
    preserved_file(evidence_client, item).unlink()
    unavailable = reader.get(item.content)
    assert unavailable.status_code == 503
    for response in (ok, denied, missing, unavailable):
        surface = response.text if response is not ok else str(response.headers)
        for secret in (item.storage_key, root, "preserved", "quarantine", csrf):
            assert secret not in surface


# --- Audit --------------------------------------------------------------------------------


def test_successful_retrieval_records_exactly_one_metadata_only_event(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(300_000, 7))
    reader, csrf = login_as(evidence_client, "INVESTIGATOR")
    actor = reader.get("/api/v1/auth/session").json()["user_id"]
    assert audit_events(evidence_client, action=RETRIEVED, entity_id=item.object_id) == []

    response = reader.get(item.content, headers={"X-Request-ID": "v22-trace-retrieval-0001"})
    assert response.status_code == 200
    events = audit_events(evidence_client, action=RETRIEVED, entity_id=item.object_id)
    assert len(events) == 1
    event = events[0]
    assert event.actor == actor
    assert event.entity_type == "evidence_object" and event.entity_public_id == item.object_id
    assert event.request_id == "v22-trace-retrieval-0001" == response.headers["x-request-id"]
    with application(evidence_client).state.session_factory() as session:
        case = session.execute(
            select(Case).where(Case.public_id == OPERATIONAL_CASE_ID)
        ).scalar_one()
        assert event.case_id == case.id and event.organization_id == case.organization_id
        evidence = session.execute(
            select(Evidence).where(Evidence.public_id == item.evidence_id)
        ).scalar_one()
        evidence_public = evidence.public_id
    assert event.details == {
        "evidence_id": evidence_public,
        "byte_size": len(item.body),
        "media_type": "application/pdf",
        "state": "PRESERVED",
    }
    serialized = json.dumps(event.details) + str(event.request_id) + event.actor
    root = str(storage_root(evidence_client))
    for secret in (item.storage_key, root, csrf, reader.cookies.get(SESSION_COOKIE) or "-"):
        assert secret not in serialized
    assert item.body[20:60].decode("latin-1") not in serialized


def test_each_retrieval_is_its_own_event_and_events_are_append_only(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    reader, _ = login_as(evidence_client, "REVIEWER")
    for _ in range(3):
        assert reader.get(item.content).content == item.body
    events = audit_events(evidence_client, action=RETRIEVED, entity_id=item.object_id)
    assert len(events) == 3 and len({e.public_id for e in events}) == 3

    with application(evidence_client).state.session_factory() as session:
        stored = session.execute(
            select(AuditEvent).where(AuditEvent.public_id == events[0].public_id)
        ).scalar_one()
        stored.actor = "USR-tampered"
        with pytest.raises(AuditEventImmutableError):
            session.flush()
        session.rollback()
        stored = session.execute(
            select(AuditEvent).where(AuditEvent.public_id == events[0].public_id)
        ).scalar_one()
        session.delete(stored)
        with pytest.raises(AuditEventImmutableError):
            session.flush()
        session.rollback()


def test_retrieval_never_modifies_the_object_custody_or_stored_bytes(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(200_000, 11))
    path = preserved_file(evidence_client, item)
    stat_before = path.stat()
    bytes_before = path.read_bytes()
    snapshot = object_snapshot(evidence_client, item)
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    for _ in range(2):
        assert reader.get(item.content).status_code == 200
    assert object_snapshot(evidence_client, item) == snapshot
    assert path.read_bytes() == bytes_before
    assert (path.stat().st_size, path.stat().st_mtime_ns) == (
        stat_before.st_size,
        stat_before.st_mtime_ns,
    )
    assert [p.name for p in (storage_root(evidence_client) / "preserved").iterdir()].count(
        f"{item.storage_key}.bin"
    ) == 1


def test_retrieval_does_not_create_temporary_copies(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(400_000, 13))
    root = storage_root(evidence_client)
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    assert reader.get(item.content).content == item.body
    assert sorted(str(p.relative_to(root)) for p in root.rglob("*")) == before


def test_uploaded_bytes_of_other_objects_are_not_confused(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    first = preserve(evidence_client, body=deterministic_bytes(5_000, 1))
    second = preserve(evidence_client, body=deterministic_bytes(5_000, 2))
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    assert reader.get(first.content).content == first.body
    assert reader.get(second.content).content == second.body
    assert first.body != second.body


def test_retrieval_writes_only_one_audit_event_and_takes_no_lock(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    """SQL-level immutability proof for both the single-chunk and the streamed path."""
    streamed = preserve(evidence_client, body=deterministic_bytes(300_000, 21))
    single = preserve(evidence_client, body=deterministic_bytes(3_000, 22))
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    for target in (streamed, single):
        with captured_sql(evidence_client) as statements:
            assert reader.get(target.content).status_code == 200
        assert_only_an_audit_event_was_written(statements)


def test_only_get_is_routed_for_retrieval(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    """HEAD and every other method are refused; nothing is read and nothing is audited."""
    item = preserve(evidence_client)
    reader, csrf = login_as(evidence_client, "INVESTIGATOR")
    before = len(audit_events(evidence_client, action=RETRIEVED))
    assert reader.head(item.content).status_code == 405
    for method in ("post", "patch", "delete"):
        response = getattr(reader, method)(item.content, headers=csrf_header(csrf))
        assert response.status_code == 405, method
        assert response.json()["error"]["code"] == "method_not_allowed"
    assert len(audit_events(evidence_client, action=RETRIEVED)) == before
