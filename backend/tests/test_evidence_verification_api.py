"""V2.2 independent integrity verification: results, tamper matrix, audit, immutability."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.security import SESSION_COOKIE
from app.db.base import Base
from app.db.types import utcnow
from app.domain.models import (
    AuditEvent,
    AuditEventImmutableError,
    Case,
    EvidenceObject,
    User,
    UserSession,
)
from app.services.evidence_access import MATCH_MESSAGE, MISMATCH_MESSAGE, UNAVAILABLE_MESSAGE
from app.services.evidence_hashing import HASH_CHUNK_BYTES
from app.services.evidence_storage import EvidenceStorageFailure, LocalEvidenceStorage
from tests.evidence_support import (
    BASE,
    SECRET_PATH,
    Preserved,
    assert_only_an_audit_event_was_written,
    audit_events,
    captured_sql,
    csrf_header,
    deterministic_bytes,
    foreign_organization_client,
    install_recording_storage,
    login_as,
    object_snapshot,
    preserve,
    preserved_file,
    second_case,
    verify_url,
)
from tests.test_evidence_intake_api import (
    application,
    evidence_client,  # noqa: F401  (pytest fixture shared with the V2.1 intake tests)
    login,
    provision,
    seed_preserved_demo_object,
    storage_root,
)

VERIFIED = "evidence.object.integrity_verified"
ROLES = ["INVESTIGATOR", "REVIEWER", "CUSTODIAN", "RESEARCHER", "AUDITOR"]
MISMATCH_ONLY_KEYS = {
    "expected_byte_size",
    "computed_byte_size",
    "expected_sha256",
    "computed_sha256",
    "expected_sha512",
    "computed_sha512",
}
BASE_KEYS = {
    "evidence_object_id",
    "result",
    "byte_size",
    "sha256",
    "sha512",
    "verified_at",
    "verified_by",
    "message",
}


def _verify(client: TestClient, csrf: str, item: Preserved) -> Any:
    return client.post(item.verify, headers=csrf_header(csrf))


def _digests(data: bytes) -> tuple[int, str, str]:
    return len(data), hashlib.sha256(data).hexdigest(), hashlib.sha512(data).hexdigest()


def _events(client: TestClient, item: Preserved) -> list[AuditEvent]:
    return audit_events(client, action=VERIFIED, entity_id=item.object_id)


@pytest.fixture
def subject(evidence_client: TestClient) -> tuple[TestClient, str, Preserved]:  # noqa: F811
    """A PRESERVED multi-chunk object plus an authorized Investigator session."""
    item = preserve(evidence_client, body=deterministic_bytes(3 * HASH_CHUNK_BYTES + 11, seed=41))
    client, csrf = login_as(evidence_client, "INVESTIGATOR")
    return client, csrf, item


# --- Authorization and CSRF ---------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_every_authorized_role_can_verify(
    evidence_client: TestClient,  # noqa: F811
    role: str,
) -> None:
    item = preserve(evidence_client)
    client, csrf = login_as(evidence_client, role)
    response = _verify(client, csrf, item)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == "MATCH"


def test_organization_scoped_auditor_can_verify(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    auditor, csrf = login_as(evidence_client, "AUDITOR", None)
    assert _verify(auditor, csrf, item).json()["result"] == "MATCH"


def test_csrf_is_required_and_a_failure_changes_nothing(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
) -> None:
    client, csrf, item = subject
    snapshot = object_snapshot(evidence_client, item)
    missing = client.post(item.verify)
    wrong = client.post(item.verify, headers=csrf_header("not-the-session-csrf-token"))
    for response in (missing, wrong):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "access_denied"
    assert _events(evidence_client, item) == []
    assert object_snapshot(evidence_client, item) == snapshot
    assert _verify(client, csrf, item).status_code == 200
    assert client.get(item.verify).status_code == 405  # verification is never a GET


def test_unauthenticated_request_is_rejected_even_when_storage_is_unavailable(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
) -> None:
    _, _, item = subject
    preserved_file(evidence_client, item).unlink()
    anonymous = TestClient(application(evidence_client))
    response = anonymous.post(item.verify)
    assert response.status_code == 401 and "UNAVAILABLE" not in response.text
    assert _events(evidence_client, item) == []


def test_denied_callers_get_not_found_never_unavailable(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
) -> None:
    _, _, item = subject
    preserved_file(evidence_client, item).unlink()  # storage failure must not leak to outsiders
    admin, admin_csrf = login_as(evidence_client, "ADMINISTRATOR", None)
    denied = _verify(admin, admin_csrf, item)
    assert denied.status_code == 404 and denied.json()["error"]["code"] == "not_found"
    assert "UNAVAILABLE" not in denied.text and "MISMATCH" not in denied.text
    reader, reader_csrf = login_as(evidence_client, "INVESTIGATOR")
    guessed = Preserved(item.evidence_id, "EOBJ-999", b"", item.storage_key)
    assert _verify(reader, reader_csrf, guessed).status_code == 404
    assert _events(evidence_client, item) == []


def test_demo_quarantined_and_rejected_objects_cannot_be_verified(
    evidence_client: TestClient,  # noqa: F811
    client: TestClient,
) -> None:
    quarantined = preserve(evidence_client, finalize=False)
    rejected = preserve(evidence_client, body=b"plain text is not a PDF", finalize=False)
    custodian, csrf = login_as(evidence_client, "CUSTODIAN")
    final = custodian.post(
        f"{BASE}/{rejected.case_id}/evidence/{rejected.evidence_id}/objects/"
        f"{rejected.object_id}/finalize",
        headers=csrf_header(csrf),
    )
    assert final.json()["state"] == "REJECTED"
    reader, reader_csrf = login_as(evidence_client, "INVESTIGATOR")
    for item in (quarantined, rejected):
        response = _verify(reader, reader_csrf, item)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "evidence_state_conflict"
    assert _events(evidence_client, quarantined) == []
    assert _events(evidence_client, rejected) == []
    # Demonstration Cases: anonymous viewer and an authenticated investigator are both refused.
    evidence_id, item_id, _, _ = seed_preserved_demo_object(client)
    url = f"/api/v1/cases/CASE-001/evidence/{evidence_id}/objects/{item_id}/verify"
    assert client.post(url).status_code == 404
    username = f"v22-demo-verify-{time.monotonic_ns()}"
    provision(client, username, "INVESTIGATOR", "CASE-001")
    token = login(client, username)
    assert client.post(url, headers=csrf_header(token)).status_code == 404


def _latest_session_user(client: TestClient) -> tuple[UserSession, User]:
    with application(client).state.session_factory() as session:
        record = (
            session.execute(select(UserSession).order_by(UserSession.created_at.desc()))
            .scalars()
            .first()
        )
        assert record is not None
        user = session.execute(select(User).where(User.id == record.user_id)).scalar_one()
        return record, user


def _denied(response: Any, status: int) -> None:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert {"code", "message", "request_id"} <= set(error)
    assert "MATCH" not in response.text and "UNAVAILABLE" not in response.text


def test_verification_enforces_the_same_boundaries_as_retrieval(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(50_000, seed=81))
    other_case = second_case(evidence_client)
    other_item = preserve(evidence_client, case_id=other_case)
    sibling = preserve(evidence_client)  # another Evidence item (and object) in the same Case
    before = len(audit_events(evidence_client, action=VERIFIED))

    # Disabled user, expired session and revoked session are all authentication failures.
    disabled, disabled_csrf = login_as(evidence_client, "INVESTIGATOR")
    assert _verify(disabled, disabled_csrf, item).status_code == 200  # the only event below
    _, user = _latest_session_user(evidence_client)
    with application(evidence_client).state.session_factory() as session:
        stored = session.execute(select(User).where(User.id == user.id)).scalar_one()
        stored.status = "disabled"
        session.commit()
    _denied(_verify(disabled, disabled_csrf, item), 401)

    expired, expired_csrf = login_as(evidence_client, "REVIEWER")
    record, _ = _latest_session_user(evidence_client)
    with application(evidence_client).state.session_factory() as session:
        stored_session = session.execute(
            select(UserSession).where(UserSession.id == record.id)
        ).scalar_one()
        stored_session.expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
    _denied(_verify(expired, expired_csrf, item), 401)

    revoked, revoked_csrf = login_as(evidence_client, "RESEARCHER")
    token = revoked.cookies.get(SESSION_COOKIE)
    assert revoked.post("/api/v1/auth/logout", headers=csrf_header(revoked_csrf)).status_code in (
        200,
        204,
    )
    revoked.cookies.set(SESSION_COOKIE, str(token))
    _denied(_verify(revoked, revoked_csrf, item), 401)

    # Wrong Case, cross-Case and mismatched identifiers, guessed identifiers: all not found.
    reader, csrf = login_as(evidence_client, "INVESTIGATOR")
    for url in (
        verify_url(item.evidence_id, item.object_id, other_case),  # role is on CASE-900 only
        verify_url(item.evidence_id, other_item.object_id),  # another Case's object, this Case
        verify_url(other_item.evidence_id, item.object_id),  # another Case's Evidence
        verify_url(item.evidence_id, sibling.object_id),  # an object of a different Evidence
        verify_url("EVD-999", item.object_id),
        verify_url(item.evidence_id, "EOBJ-999"),
    ):
        _denied(reader.post(url, headers=csrf_header(csrf)), 404)

    # Another Organization does not help, even with a role on its own Case.
    outsider, _ = foreign_organization_client(evidence_client)
    outsider_csrf = outsider.cookies.get("veritas_csrf") or ""
    _denied(outsider.post(item.verify, headers=csrf_header(outsider_csrf)), 404)

    assert len(audit_events(evidence_client, action=VERIFIED)) == before + 1


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/cases/CASE-900/evidence/EVD-1/objects/EOBJ-001/verify",
        "/api/v1/cases/CASE-900/evidence/EVD-001/objects/EOBJ-x/verify",
        "/api/v1/cases/CASE-900/evidence/EVD-001/objects/..%2F..%2Fsecret/verify",
    ],
)
def test_malformed_identifiers_are_rejected_without_a_result(
    evidence_client: TestClient,  # noqa: F811
    path: str,
) -> None:
    client, csrf = login_as(evidence_client, "INVESTIGATOR")
    before = len(audit_events(evidence_client, action=VERIFIED))
    response = client.post(path, headers=csrf_header(csrf))
    assert response.status_code in (404, 422)
    assert "MATCH" not in response.text and "traceback" not in response.text.lower()
    assert len(audit_events(evidence_client, action=VERIFIED)) == before


# --- MATCH --------------------------------------------------------------------------------


def test_match_response_contains_exactly_the_documented_fields(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
) -> None:
    client, csrf, item = subject
    actor = client.get("/api/v1/auth/session").json()["user_id"]
    size, sha256, sha512 = _digests(item.body)

    response = _verify(client, csrf, item)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == BASE_KEYS  # no expected_/computed_ keys on MATCH
    assert body["evidence_object_id"] == item.object_id
    assert body["result"] == "MATCH"
    assert (body["byte_size"], body["sha256"], body["sha512"]) == (size, sha256, sha512)
    assert body["verified_by"] == actor
    assert (
        body["message"]
        == MATCH_MESSAGE
        == ("Preserved bytes match the recorded intake integrity values.")
    )
    verified_at = datetime.fromisoformat(body["verified_at"])
    assert verified_at.tzinfo is not None
    (event,) = _events(evidence_client, item)
    assert verified_at == event.occurred_at  # the response and the audit event share one instant


def test_a_zero_byte_object_matches(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=b"", filename="empty.txt", media_type="text/plain")
    client, csrf = login_as(evidence_client, "REVIEWER")
    body = _verify(client, csrf, item).json()
    assert body["result"] == "MATCH" and body["byte_size"] == 0
    assert body["sha256"] == hashlib.sha256(b"").hexdigest()
    assert body["sha512"] == hashlib.sha512(b"").hexdigest()


# --- MISMATCH: tamper matrix --------------------------------------------------------------


def _modify(path: Path, original: bytes) -> bytes:
    changed = bytearray(original)
    changed[-1] ^= 0x01
    path.write_bytes(bytes(changed))
    return bytes(changed)


def _truncate(path: Path, original: bytes) -> bytes:
    path.write_bytes(original[:-10])
    return original[:-10]


def _truncate_to_zero(path: Path, original: bytes) -> bytes:
    path.write_bytes(b"")
    return b""


def _expand(path: Path, original: bytes) -> bytes:
    path.write_bytes(original + b"EXTRA BYTES")
    return original + b"EXTRA BYTES"


def _replace_same_size(path: Path, original: bytes) -> bytes:
    replacement = deterministic_bytes(len(original), seed=999)
    path.write_bytes(replacement)
    return replacement


def _replace_other_size(path: Path, original: bytes) -> bytes:
    replacement = b"%PDF-1.4 a completely different document"
    path.write_bytes(replacement)
    return replacement


def _replace_by_rename(path: Path, original: bytes) -> bytes:
    replacement = deterministic_bytes(len(original) + 100, seed=998)
    staging = path.with_name("replacement.tmp")
    staging.write_bytes(replacement)
    os.replace(staging, path)  # a different inode under the same name
    return replacement


TAMPERS: dict[str, Callable[[Path, bytes], bytes]] = {
    "modified": _modify,
    "truncated": _truncate,
    "truncated_to_zero": _truncate_to_zero,
    "expanded": _expand,
    "replaced_same_size": _replace_same_size,
    "replaced_other_size": _replace_other_size,
    "replaced_by_rename": _replace_by_rename,
}


@pytest.mark.parametrize("tamper", sorted(TAMPERS))
def test_every_tamper_is_a_mismatch_with_a_full_comparison(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
    tamper: str,
) -> None:
    client, csrf, item = subject
    changed = TAMPERS[tamper](preserved_file(evidence_client, item), item.body)
    expected, computed = _digests(item.body), _digests(changed)

    response = _verify(client, csrf, item)

    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "MISMATCH"
    assert set(body) == BASE_KEYS | MISMATCH_ONLY_KEYS
    assert body["message"] == MISMATCH_MESSAGE
    assert (
        body["expected_byte_size"],
        body["expected_sha256"],
        body["expected_sha512"],
    ) == expected
    assert (
        body["computed_byte_size"],
        body["computed_sha256"],
        body["computed_sha512"],
    ) == computed
    # The recorded reference values are reported unchanged alongside the comparison.
    assert (body["byte_size"], body["sha256"], body["sha512"]) == expected
    assert computed != expected


@pytest.mark.parametrize("field", ["sha256", "sha512", "both_hashes", "byte_size"])
def test_any_single_recorded_value_disagreeing_is_a_mismatch(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
    field: str,
) -> None:
    """The file is untouched; one recorded reference value is corrupted in the database."""
    client, csrf, item = subject
    size, sha256, sha512 = _digests(item.body)
    corruptions: dict[str, dict[str, Any]] = {
        "sha256": {"sha256": "f" * 64},
        "sha512": {"sha512": "e" * 128},
        "both_hashes": {"sha256": "f" * 64, "sha512": "e" * 128},
        "byte_size": {"byte_size": size + 1},
    }
    corrupted = corruptions[field]
    with application(evidence_client).state.session_factory() as session:
        # Core UPDATE bypasses the ORM guard: simulates a corrupted reference record.
        session.execute(
            update(EvidenceObject)
            .where(EvidenceObject.public_id == item.object_id)
            .values(**corrupted)
        )
        session.commit()

    body = _verify(client, csrf, item).json()

    assert body["result"] == "MISMATCH"
    computed = (body["computed_byte_size"], body["computed_sha256"], body["computed_sha512"])
    assert computed == (size, sha256, sha512)  # the bytes themselves are intact
    differing = {
        key
        for key in ("byte_size", "sha256", "sha512")
        if body[f"expected_{key}"] != body[f"computed_{key}"]
    }
    assert (
        differing
        == {
            "sha256": {"sha256"},
            "sha512": {"sha512"},
            "both_hashes": {"sha256", "sha512"},
            "byte_size": {"byte_size"},
        }[field]
    )


def test_mismatch_leaves_retrieval_enabled_and_is_not_sticky(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
) -> None:
    client, csrf, item = subject
    path = preserved_file(evidence_client, item)
    changed = _modify(path, item.body)
    assert _verify(client, csrf, item).json()["result"] == "MISMATCH"

    # Retrieval is a separate operation: it stays enabled and serves the bytes as they are.
    retrieved = client.get(item.content)
    assert retrieved.status_code == 200 and retrieved.content == changed
    assert retrieved.headers["content-length"] == str(len(changed))

    # Nothing about the earlier result persists: restoring the bytes verifies again.
    path.write_bytes(item.body)
    assert _verify(client, csrf, item).json()["result"] == "MATCH"
    assert [e.details["result"] for e in _events(evidence_client, item)] == ["MISMATCH", "MATCH"]


# --- UNAVAILABLE: storage conditions, never MISMATCH --------------------------------------


def _expect_unavailable(
    client: TestClient,
    csrf: str,
    item: Preserved,
    evidence_client: TestClient,  # noqa: F811
) -> dict[str, Any]:
    response = _verify(client, csrf, item)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "UNAVAILABLE", body
    assert set(body) == BASE_KEYS  # nothing was computed, so no comparison fields
    assert body["message"] == UNAVAILABLE_MESSAGE
    assert "MISMATCH" not in response.text
    for secret in (SECRET_PATH, item.storage_key, str(storage_root(evidence_client))):
        assert secret not in response.text
    return body  # type: ignore[no-any-return]


def _unlink(path: Path) -> None:
    path.unlink()


def _directory_in_place(path: Path) -> None:
    path.unlink()
    path.mkdir()


def _symlink_to_identical_bytes(path: Path) -> None:
    outside = path.parent.parent / "outside-copy.bin"
    outside.write_bytes(path.read_bytes())  # byte-identical, yet must not be followed
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError as exc:  # pragma: no cover - only where symlinks are unavailable
        pytest.skip(f"symlink creation is unavailable on this platform: {exc}")


def _fifo_in_place(path: Path) -> None:
    path.unlink()
    if not hasattr(os, "mkfifo"):  # pragma: no cover - Windows
        pytest.skip("named pipes are unavailable on this platform")
    os.mkfifo(path)


def _unreadable(path: Path) -> None:
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        pytest.skip("file permissions are not enforced for root or on this platform")
    path.chmod(0)


def _preserved_directory_missing(path: Path) -> None:
    path.parent.rename(path.parent.with_name("preserved-moved-away"))


def _preserved_directory_symlinked(path: Path) -> None:
    moved = path.parent.with_name("preserved-elsewhere")
    path.parent.rename(moved)
    try:
        path.parent.symlink_to(moved, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - only where symlinks are unavailable
        pytest.skip(f"symlink creation is unavailable on this platform: {exc}")


def _root_replaced_by_file(path: Path) -> None:
    root = path.parent.parent
    moved = root.with_name("private-moved")
    root.rename(moved)
    root.write_bytes(b"not a directory")


UNAVAILABLE_CAUSES: dict[str, Callable[[Path], None]] = {
    "missing_file": _unlink,
    "directory_in_place_of_file": _directory_in_place,
    "symlinked_file_even_with_identical_bytes": _symlink_to_identical_bytes,
    "named_pipe_in_place_of_file": _fifo_in_place,
    "unreadable_permissions": _unreadable,
    "preserved_directory_missing": _preserved_directory_missing,
    "preserved_directory_is_a_symlink": _preserved_directory_symlinked,
    "storage_root_is_a_file": _root_replaced_by_file,
}


@pytest.mark.parametrize("cause", sorted(UNAVAILABLE_CAUSES))
def test_unreadable_preserved_objects_are_unavailable_never_a_mismatch(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
    cause: str,
) -> None:
    client, csrf, item = subject
    path = preserved_file(evidence_client, item)
    UNAVAILABLE_CAUSES[cause](path)
    started = time.monotonic()

    body = _expect_unavailable(client, csrf, item, evidence_client)

    assert time.monotonic() - started < 10, "verification must not block on a hostile file type"
    (event,) = _events(evidence_client, item)
    assert event.details["result"] == "UNAVAILABLE"
    assert event.details["computed_byte_size"] is None
    assert event.details["computed_sha256"] is None and event.details["computed_sha512"] is None
    assert body["evidence_object_id"] == item.object_id
    # Retrieval follows the same rule: an unreadable object is a sanitized 503, never bytes.
    retrieval = client.get(item.content)
    assert retrieval.status_code == 503
    assert retrieval.json()["error"]["code"] == "evidence_storage_unavailable"
    assert item.body not in retrieval.content


def test_a_quarantine_copy_is_never_a_fallback_for_a_missing_preserved_object(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
) -> None:
    client, csrf, item = subject
    path = preserved_file(evidence_client, item)
    quarantine = storage_root(evidence_client) / "quarantine"
    quarantine.mkdir(parents=True, exist_ok=True)
    (quarantine / f"{item.storage_key}.part").write_bytes(item.body)  # identical bytes
    (storage_root(evidence_client) / "alternate-location.bin").write_bytes(item.body)
    path.unlink()

    _expect_unavailable(client, csrf, item, evidence_client)
    retrieval = client.get(item.content)
    assert retrieval.status_code == 503 and item.body not in retrieval.content


def test_a_storage_failure_reporting_a_private_path_is_sanitized(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
) -> None:
    client, csrf, item = subject

    class LeakyStorage(LocalEvidenceStorage):
        def open_preserved_object(self, storage_key: str) -> Any:
            raise EvidenceStorageFailure(f"cannot open {SECRET_PATH}/{storage_key}")

    application(evidence_client).state.evidence_storage = LeakyStorage(
        storage_root(evidence_client)
    )
    _expect_unavailable(client, csrf, item, evidence_client)


def test_a_read_failure_after_partial_data_is_unavailable_not_a_mismatch(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
) -> None:
    client, csrf, item = subject
    storage = install_recording_storage(evidence_client)
    storage.fail_on_read = 3  # two chunks are read (and differ from the whole), then EIO
    _expect_unavailable(client, csrf, item, evidence_client)
    assert storage.all_closed() and max(s for s in storage.read_sizes if s) <= HASH_CHUNK_BYTES
    # Once the storage works again the very same object verifies cleanly.
    storage.fail_on_read = None
    assert _verify(client, csrf, item).json()["result"] == "MATCH"


def test_a_premature_end_of_file_without_an_error_is_a_truncation_mismatch(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
) -> None:
    client, csrf, item = subject
    storage = install_recording_storage(evidence_client)
    storage.eof_after_bytes = HASH_CHUNK_BYTES + 1
    body = _verify(client, csrf, item).json()
    assert body["result"] == "MISMATCH"
    assert body["computed_byte_size"] == HASH_CHUNK_BYTES + 1


# --- Audit --------------------------------------------------------------------------------


def test_each_verification_records_its_own_complete_metadata_only_event(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
) -> None:
    client, csrf, item = subject
    actor = client.get("/api/v1/auth/session").json()["user_id"]
    size, sha256, sha512 = _digests(item.body)

    first = client.post(
        item.verify, headers={**csrf_header(csrf), "X-Request-ID": "v22-trace-verify-0001"}
    )
    assert first.headers["x-request-id"] == "v22-trace-verify-0001"
    _modify(preserved_file(evidence_client, item), item.body)
    assert _verify(client, csrf, item).json()["result"] == "MISMATCH"
    preserved_file(evidence_client, item).unlink()
    assert _verify(client, csrf, item).json()["result"] == "UNAVAILABLE"

    events = _events(evidence_client, item)
    assert [e.details["result"] for e in events] == ["MATCH", "MISMATCH", "UNAVAILABLE"]
    assert len({e.public_id for e in events}) == 3  # each verification has its own event
    with application(evidence_client).state.session_factory() as session:
        case = session.execute(select(Case).where(Case.public_id == item.case_id)).scalar_one()
        case_keys = (case.id, case.organization_id)
    for event in events:
        assert event.actor == actor
        assert event.entity_type == "evidence_object" and event.entity_public_id == item.object_id
        assert (event.case_id, event.organization_id) == case_keys
        assert set(event.details) == {
            "evidence_id",
            "result",
            "expected_byte_size",
            "expected_sha256",
            "expected_sha512",
            "computed_byte_size",
            "computed_sha256",
            "computed_sha512",
        }
        assert (
            event.details["expected_byte_size"],
            event.details["expected_sha256"],
            event.details["expected_sha512"],
        ) == (size, sha256, sha512)
    assert events[0].request_id == "v22-trace-verify-0001"
    assert all(e.request_id for e in events)
    match, mismatch, unavailable = events
    assert match.details["computed_sha256"] == sha256
    assert mismatch.details["computed_sha256"] not in (None, sha256)
    assert unavailable.details["computed_sha256"] is None

    serialized = json.dumps([e.details for e in events])
    for secret in (item.storage_key, str(storage_root(evidence_client)), csrf, SECRET_PATH):
        assert secret not in serialized
    assert (client.cookies.get(SESSION_COOKIE) or "-") not in serialized
    assert item.body[10:40].decode("latin-1") not in serialized


def test_failed_requests_record_no_verification_event(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
) -> None:
    client, csrf, item = subject
    anonymous = TestClient(application(evidence_client))
    admin, admin_csrf = login_as(evidence_client, "ADMINISTRATOR", None)
    assert anonymous.post(item.verify).status_code == 401
    assert _verify(admin, admin_csrf, item).status_code == 404
    assert client.post(item.verify).status_code == 403  # no CSRF token
    missing = Preserved(item.evidence_id, "EOBJ-999", b"", item.storage_key)
    assert _verify(client, csrf, missing).status_code == 404
    assert _events(evidence_client, item) == []


def test_verification_events_are_append_only(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
) -> None:
    client, csrf, item = subject
    _verify(client, csrf, item)
    (event,) = _events(evidence_client, item)
    with application(evidence_client).state.session_factory() as session:
        stored = session.execute(
            select(AuditEvent).where(AuditEvent.public_id == event.public_id)
        ).scalar_one()
        stored.details = {"result": "MATCH", "forged": True}
        with pytest.raises(AuditEventImmutableError):
            session.flush()
        session.rollback()


# --- Immutability proofs ------------------------------------------------------------------


@pytest.mark.parametrize("scenario", ["MATCH", "MISMATCH", "UNAVAILABLE"])
def test_verification_is_observational_only(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
    scenario: str,
) -> None:
    client, csrf, item = subject
    path = preserved_file(evidence_client, item)
    if scenario == "MISMATCH":
        _modify(path, item.body)
    elif scenario == "UNAVAILABLE":
        path.unlink()
    files_before = sorted(
        str(p.relative_to(storage_root(evidence_client)))
        for p in storage_root(evidence_client).rglob("*")
    )
    row_before = object_snapshot(evidence_client, item)
    file_state = (path.stat().st_size, path.stat().st_mtime_ns) if path.exists() else None

    with captured_sql(evidence_client) as statements:
        response = _verify(client, csrf, item)
    assert response.json()["result"] == scenario

    assert_only_an_audit_event_was_written(statements)
    row_after = object_snapshot(evidence_client, item)
    assert row_after == row_before  # byte_size, digests, state, preserved_*, validation, updated_at
    assert row_after["state"].value == "PRESERVED"  # type: ignore[attr-defined]
    assert row_after["_custody_events"] == row_before["_custody_events"] == 2
    assert row_after["_case_object_count"] == row_before["_case_object_count"]
    assert (
        sorted(
            str(p.relative_to(storage_root(evidence_client)))
            for p in storage_root(evidence_client).rglob("*")
        )
        == files_before
    )
    if file_state is not None:
        assert (path.stat().st_size, path.stat().st_mtime_ns) == file_state


def test_no_verification_table_or_stored_result_exists() -> None:
    assert not [name for name in Base.metadata.tables if "verif" in name]
    columns = {c.name for c in EvidenceObject.__table__.columns}
    assert not [name for name in columns if "verif" in name]


def test_repeated_verifications_always_recompute_from_the_current_bytes(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
) -> None:
    client, csrf, item = subject
    results = []
    for _ in range(3):
        results.append(_verify(client, csrf, item).json()["result"])
    _modify(preserved_file(evidence_client, item), item.body)
    for _ in range(2):
        results.append(_verify(client, csrf, item).json()["result"])
    assert results == ["MATCH"] * 3 + ["MISMATCH"] * 2
    assert len(_events(evidence_client, item)) == 5


# --- Concurrency --------------------------------------------------------------------------


def test_concurrent_verification_and_retrieval_are_safe(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(500_000, seed=51))
    storage = install_recording_storage(evidence_client)
    workers = [login_as(evidence_client, "INVESTIGATOR") for _ in range(8)]
    snapshot = object_snapshot(evidence_client, item)

    def work(session: tuple[TestClient, str]) -> list[tuple[str, int, bool]]:
        client, csrf = session
        outcomes: list[tuple[str, int, bool]] = []
        first = _verify(client, csrf, item)
        outcomes.append(("verify", first.status_code, first.json().get("result") == "MATCH"))
        fetched = client.get(item.content)
        outcomes.append(("retrieve", fetched.status_code, fetched.content == item.body))
        second = _verify(client, csrf, item)
        outcomes.append(("verify", second.status_code, second.json().get("result") == "MATCH"))
        return outcomes

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = [o for batch in pool.map(work, workers) for o in batch]

    assert len(outcomes) == 24
    assert all(status == 200 and ok for _, status, ok in outcomes), outcomes
    assert len(_events(evidence_client, item)) == 16
    assert len(audit_events(evidence_client, action="evidence.object.retrieved")) >= 8
    assert object_snapshot(evidence_client, item) == snapshot
    assert storage.all_closed() and len(storage.streams) == 24


@pytest.mark.skipif(
    os.name == "nt",
    reason="atomic replacement of an open file is not portable on native Windows",
)
def test_verification_racing_an_atomic_replacement_never_errors(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(400_000, seed=52))
    path = preserved_file(evidence_client, item)
    workers = [login_as(evidence_client, "REVIEWER") for _ in range(6)]
    good, bad = item.body, deterministic_bytes(400_000, seed=53)
    stop = threading.Event()

    def flip() -> None:
        staging = path.with_name("race.tmp")
        toggle = False
        while not stop.is_set():
            staging.write_bytes(bad if toggle else good)
            os.replace(staging, path)  # atomic: a reader sees the old or the new object whole
            toggle = not toggle

    def verify_many(session: tuple[TestClient, str]) -> list[str]:
        client, csrf = session
        return [_verify(client, csrf, item).json()["result"] for _ in range(4)]

    flipper = threading.Thread(target=flip)
    flipper.start()
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = [r for batch in pool.map(verify_many, workers) for r in batch]
    finally:
        stop.set()
        flipper.join()

    assert set(results) <= {"MATCH", "MISMATCH"}, results
    assert len(_events(evidence_client, item)) == 24
    path.write_bytes(good)
    assert _verify(*workers[0], item).json()["result"] == "MATCH"


def test_no_database_connection_is_held_during_storage_io(
    subject: tuple[TestClient, str, Preserved],
    evidence_client: TestClient,  # noqa: F811
) -> None:
    """Slow storage must never pin a pooled database connection (verify or retrieve)."""
    client, csrf, item = subject
    pool = application(evidence_client).state.engine.pool
    checked_out: list[int] = []
    storage = install_recording_storage(evidence_client)
    storage.on_io = lambda: checked_out.append(pool.checkedout())

    assert _verify(client, csrf, item).json()["result"] == "MATCH"
    verify_probes = len(checked_out)
    assert client.get(item.content).content == item.body

    assert verify_probes >= 2 and len(checked_out) > verify_probes
    assert set(checked_out) == {0}, f"connections were held during storage I/O: {checked_out}"


def test_openapi_publishes_the_retrieval_and_verification_contract(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    spec = application(evidence_client).openapi()
    base = "/api/v1/cases/{case_id}/evidence/{evidence_id}/objects/{object_id}"
    content = spec["paths"][f"{base}/content"]
    assert set(content) == {"get", "put"}  # retrieval shares the V2.1 upload path
    assert "application/octet-stream" in content["get"]["responses"]["200"]["content"]
    assert {"409", "503"} <= set(content["get"]["responses"])

    verify = spec["paths"][f"{base}/verify"]
    assert set(verify) == {"post"}
    ok = verify["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    schemas = spec["components"]["schemas"]
    schema = schemas[ok["$ref"].rsplit("/", 1)[1]]
    assert set(schema["properties"]) == BASE_KEYS | MISMATCH_ONLY_KEYS
    assert (
        set(schema["required"]) == BASE_KEYS
    )  # the comparison fields are optional (MISMATCH only)
    result = schema["properties"]["result"]
    result_schema = schemas[result["$ref"].rsplit("/", 1)[1]]
    assert set(result_schema["enum"]) == {"MATCH", "MISMATCH", "UNAVAILABLE"}
