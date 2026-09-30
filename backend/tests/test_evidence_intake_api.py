"""Synthetic V2.1 intake, integrity, authorization and custody regression tests."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.errors import EvidenceConflictError
from app.core.security import Principal, hash_password
from app.db.types import utcnow
from app.domain.enums import (
    EvidenceCustodyEventType,
    EvidenceObjectState,
    EvidenceValidationStatus,
    ProfileStatus,
)
from app.domain.models import (
    AuditEvent,
    Case,
    Evidence,
    EvidenceCustodyEvent,
    EvidenceCustodyEventImmutableError,
    EvidenceObject,
    EvidenceObjectImmutableError,
    Organization,
    OrganizationMembership,
    Role,
    RoleAssignment,
    User,
)
from app.domain.values import PROFILE_SECTIONS, ProfileSection
from app.main import create_app
from app.schemas import EvidenceIntakeIn
from app.services.evidence_intake import (
    register_additional_evidence_object,
    register_evidence_intake,
)
from app.services.evidence_storage import (
    EvidenceStorageFailure,
    LocalEvidenceStorage,
    QuarantineHandle,
)
from app.services.records import set_evidence_profile
from tests.conftest import make_settings

PASSWORD = "Synthetic-Test-Password-2026!"
BASE = "/api/v1/cases"
OPERATIONAL_CASE_ID = "CASE-900"


@pytest.fixture
def evidence_client(database_url: str, tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        make_settings(
            database_url,
            access_mode="restricted",
            max_evidence_bytes=2_000_000,
            max_request_bytes=1_048_576,
        )
    )
    with app.state.session_factory() as session:
        operational_case = session.execute(
            select(Case).where(Case.public_id == OPERATIONAL_CASE_ID)
        ).scalar_one_or_none()
        if operational_case is None:
            organization = session.execute(
                select(Organization).where(Organization.public_id == "ORG-001")
            ).scalar_one()
            session.add(
                Case(
                    public_id=OPERATIONAL_CASE_ID,
                    organization_id=organization.id,
                    title="Synthetic restricted-mode Evidence test Case",
                    summary="Non-demonstration fixture used only with deterministic test bytes.",
                    is_demonstration=False,
                    created_by="test:provisioning",
                    updated_by="test:provisioning",
                )
            )
            session.commit()
    app.state.evidence_storage = LocalEvidenceStorage(tmp_path / "private-evidence")
    with TestClient(app) as client:
        yield client


def application(client: TestClient) -> FastAPI:
    return cast(FastAPI, client.app)


def provision(client: TestClient, username: str, role_name: str, case_id: str | None) -> str:
    factory = application(client).state.session_factory
    with factory() as session:
        organization = session.execute(
            select(Organization).where(Organization.public_id == "ORG-001")
        ).scalar_one()
        user = User(
            username=username,
            display_name=f"Synthetic {username}",
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
        case = (
            session.execute(select(Case).where(Case.public_id == case_id)).scalar_one()
            if case_id
            else None
        )
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


def login(client: TestClient, username: str) -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    csrf = client.cookies.get("veritas_csrf")
    assert csrf is not None
    return csrf


def csrf_header(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def register(
    client: TestClient,
    csrf: str,
    *,
    path: str = f"{BASE}/CASE-900/evidence/intake",
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "label": "Synthetic intake sample",
        "evidence_type": "document",
        "description": "Deterministic test bytes only.",
        "original_filename": "synthetic.pdf",
        "declared_media_type": "application/pdf",
    }
    payload.update(overrides)
    response = client.post(path, json=payload, headers=csrf_header(csrf))
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


def first_object(detail: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], cast(list[object], detail["objects"])[0])


def object_id(detail: dict[str, object]) -> str:
    return cast(str, first_object(detail)["id"])


def content_path(evidence_id: str, item_id: str) -> str:
    return f"{BASE}/CASE-900/evidence/{evidence_id}/objects/{item_id}/content"


def finalize_path(evidence_id: str, item_id: str) -> str:
    return f"{BASE}/CASE-900/evidence/{evidence_id}/objects/{item_id}/finalize"


def storage_key(client: TestClient, item_id: str) -> str:
    with application(client).state.session_factory() as session:
        return session.execute(
            select(EvidenceObject.storage_key).where(EvidenceObject.public_id == item_id)
        ).scalar_one()


def storage_root(client: TestClient) -> Path:
    return cast(Path, application(client).state.evidence_storage._configured_root)


def seed_preserved_demo_object(client: TestClient) -> tuple[str, str, str, str]:
    """Seed synthetic V2.1 metadata under a demo Case to test anonymous projections."""
    with application(client).state.session_factory() as session:
        case = session.execute(select(Case).where(Case.public_id == "CASE-001")).scalar_one()
        evidence = session.execute(
            select(Evidence).where(Evidence.case_id == case.id, Evidence.public_id == "EVD-001")
        ).scalar_one()
        actor = User(
            username=f"demo-object-fixture-{uuid4().hex}",
            display_name="Synthetic demo-object fixture actor",
            status="active",
            password_hash=hash_password(PASSWORD),
            auth_provider=None,
            auth_subject=None,
            created_by="test:fixture",
            updated_by="test:fixture",
        )
        session.add(actor)
        session.flush()
        now = utcnow()
        digest256 = hashlib.sha256(b"synthetic demo-object digest fixture").hexdigest()
        digest512 = hashlib.sha512(b"synthetic demo-object digest fixture").hexdigest()
        item = EvidenceObject(
            case_id=case.id,
            organization_id=case.organization_id,
            evidence_id=evidence.id,
            storage_key=uuid4().hex,
            original_filename="private-synthetic-demo-name.txt",
            declared_media_type="text/plain",
            detected_media_type="text/plain",
            byte_size=37,
            sha256=digest256,
            sha512=digest512,
            state=EvidenceObjectState.PRESERVED,
            validation_status=EvidenceValidationStatus.ACCEPTED,
            validation_note="Synthetic test fixture only.",
            acquired_at=now,
            acquired_by=actor.public_id,
            upload_completed_at=now,
            preserved_at=now,
            preserved_by=actor.public_id,
            created_by=actor.public_id,
            updated_by=actor.public_id,
        )
        session.add(item)
        session.flush()
        for event_type, from_state, to_state in (
            (EvidenceCustodyEventType.RECEIVED, None, EvidenceObjectState.QUARANTINED.value),
            (
                EvidenceCustodyEventType.PRESERVED,
                EvidenceObjectState.QUARANTINED.value,
                EvidenceObjectState.PRESERVED.value,
            ),
        ):
            session.add(
                EvidenceCustodyEvent(
                    case_id=case.id,
                    organization_id=case.organization_id,
                    evidence_id=evidence.id,
                    evidence_object_id=item.id,
                    event_type=event_type,
                    from_state=from_state,
                    to_state=to_state,
                    actor_user_id=actor.id,
                    counterparty_user_id=None,
                    reason="Synthetic test fixture custody record.",
                    request_id="req-synthetic-demo-fixture",
                    created_by=actor.public_id,
                    updated_by=actor.public_id,
                )
            )
        session.commit()
        return evidence.public_id, item.public_id, digest256, digest512


def upload(client: TestClient, csrf: str, evidence_id: str, item_id: str, body: bytes):
    return client.put(
        content_path(evidence_id, item_id),
        content=body,
        headers={**csrf_header(csrf), "Content-Type": "application/octet-stream"},
    )


def test_demo_cases_cannot_create_v21_evidence_objects(client: TestClient) -> None:
    evidence_id, item_id, _, _ = seed_preserved_demo_object(client)
    payload = {
        "label": "Synthetic prohibited demo intake",
        "evidence_type": "document",
        "original_filename": "synthetic-demo.txt",
        "declared_media_type": "text/plain",
    }
    with application(client).state.session_factory() as session:
        before_evidence = session.query(Evidence).count()
        before_objects = session.query(EvidenceObject).count()
    anonymous = client.post(f"{BASE}/CASE-001/evidence/intake", json=payload)
    assert anonymous.status_code == 404

    username = f"demo-case-intake-{uuid4().hex}"
    provision(client, username, "INVESTIGATOR", "CASE-001")
    csrf = login(client, username)
    authenticated = client.post(
        f"{BASE}/CASE-001/evidence/intake", json=payload, headers=csrf_header(csrf)
    )
    assert authenticated.status_code == 404
    assert client.get(f"{BASE}/CASE-001/evidence/{evidence_id}/intake").status_code == 404
    assert (
        client.get(f"{BASE}/CASE-001/evidence/{evidence_id}/objects/{item_id}/custody").status_code
        == 404
    )
    with application(client).state.session_factory() as session:
        case = session.execute(select(Case).where(Case.public_id == "CASE-001")).scalar_one()
        user = session.execute(select(User).where(User.username == username)).scalar_one()
        principal = Principal(
            subject=user.public_id,
            kind="user",
            authenticated=True,
            user_id=user.id,
        )
        payload_model = EvidenceIntakeIn.model_validate(payload)
        with pytest.raises(EvidenceConflictError, match="demonstration cases"):
            register_evidence_intake(session, case=case, principal=principal, payload=payload_model)
        existing_evidence = session.execute(
            select(Evidence).where(Evidence.case_id == case.id, Evidence.public_id == "EVD-001")
        ).scalar_one()
        with pytest.raises(EvidenceConflictError, match="demonstration cases"):
            register_additional_evidence_object(
                session,
                case=case,
                evidence=existing_evidence,
                principal=principal,
                original_filename="extra-synthetic.txt",
                declared_media_type="text/plain",
            )
        assert session.query(Evidence).count() == before_evidence
        assert session.query(EvidenceObject).count() == before_objects


def test_demo_viewer_cannot_read_v21_intake_metadata(client: TestClient) -> None:
    evidence_id, item_id, _, _ = seed_preserved_demo_object(client)
    response = client.get(f"{BASE}/CASE-001/evidence/{evidence_id}/intake")
    assert response.status_code == 404
    assert item_id not in response.text
    assert "private-synthetic-demo-name.txt" not in response.text


def test_demo_viewer_cannot_read_v21_custody_events(client: TestClient) -> None:
    evidence_id, item_id, _, _ = seed_preserved_demo_object(client)
    response = client.get(f"{BASE}/CASE-001/evidence/{evidence_id}/objects/{item_id}/custody")
    assert response.status_code == 404
    assert "RECEIVED" not in response.text and "PRESERVED" not in response.text


def test_demo_profile_omits_v21_object_hashes(client: TestClient) -> None:
    evidence_id, _, digest256, digest512 = seed_preserved_demo_object(client)
    response = client.get(f"{BASE}/CASE-001/evidence/{evidence_id}/profile")
    assert response.status_code == 200, response.text
    assert digest256 not in response.text and digest512 not in response.text
    assert "private-synthetic-demo-name.txt" not in response.text


def test_v1_synthetic_demo_evidence_and_profile_remain_available(client: TestClient) -> None:
    assert client.get("/api/v1/cases/CASE-001").status_code == 200
    evidence_response = client.get(f"{BASE}/CASE-001/evidence")
    assert evidence_response.status_code == 200
    assert evidence_response.json()["count"] > 0
    profile_response = client.get(f"{BASE}/CASE-001/evidence/EVD-001/profile")
    assert profile_response.status_code == 200
    assert profile_response.json()["evidence"]["id"] == "EVD-001"


def test_restricted_non_demo_case_retains_v21_intake_workflow(
    evidence_client: TestClient,
) -> None:
    assert application(evidence_client).state.settings.access_mode == "restricted"
    with application(evidence_client).state.session_factory() as session:
        case = session.execute(
            select(Case).where(Case.public_id == OPERATIONAL_CASE_ID)
        ).scalar_one()
        assert not case.is_demonstration
    provision(evidence_client, "restricted-intake-user", "INVESTIGATOR", OPERATIONAL_CASE_ID)
    provision(evidence_client, "restricted-finalizer", "CUSTODIAN", OPERATIONAL_CASE_ID)
    csrf = login(evidence_client, "restricted-intake-user")
    detail = register(evidence_client, csrf)
    evidence_id = cast(str, cast(dict[str, object], detail["evidence"])["id"])
    item_id = object_id(detail)
    uploaded = upload(
        evidence_client,
        csrf,
        evidence_id,
        item_id,
        b"%PDF-1.7\\nRestricted synthetic bytes",
    )
    assert uploaded.status_code == 200
    custodian_csrf = login(evidence_client, "restricted-finalizer")
    finalized = evidence_client.post(
        finalize_path(evidence_id, item_id), headers=csrf_header(custodian_csrf)
    )
    assert finalized.status_code == 200 and finalized.json()["state"] == "PRESERVED"
    intake = evidence_client.get(f"{BASE}/{OPERATIONAL_CASE_ID}/evidence/{evidence_id}/intake")
    assert intake.status_code == 200 and item_id in intake.text


def test_investigator_uploads_and_custodian_preserves_with_audited_custody(
    evidence_client: TestClient,
) -> None:
    investigator_id = provision(evidence_client, "intake-investigator", "INVESTIGATOR", "CASE-900")
    provision(evidence_client, "intake-custodian", "CUSTODIAN", "CASE-900")
    csrf = login(evidence_client, "intake-investigator")
    detail = register(evidence_client, csrf)
    evidence_id = cast(str, cast(dict[str, object], detail["evidence"])["id"])
    item_id = object_id(detail)
    item = first_object(detail)
    assert item["state"] == "QUARANTINED"
    assert item["byte_size"] == 0 and item["sha256"] is None and item["sha512"] is None
    assert "storage_key" not in str(detail) and "organization_id" not in str(detail)
    assert "uuid" not in str(detail).lower() and "/private-evidence" not in str(detail)

    body = b"%PDF-1.7\nSynthetic V2.1 upload.\n" + (b"bounded-chunk-" * 12_000)
    response = upload(evidence_client, csrf, evidence_id, item_id, body)
    assert response.status_code == 200, response.text
    uploaded = response.json()
    assert uploaded["state"] == "QUARANTINED" and uploaded["byte_size"] == len(body)
    assert uploaded["sha256"] == hashlib.sha256(body).hexdigest()
    assert uploaded["sha512"] == hashlib.sha512(body).hexdigest()
    key = storage_key(evidence_client, item_id)
    assert (storage_root(evidence_client) / "quarantine" / f"{key}.part").is_file()

    received = evidence_client.get(
        f"{BASE}/CASE-900/evidence/{evidence_id}/objects/{item_id}/custody"
    )
    assert received.status_code == 200
    assert [event["event_type"] for event in received.json()["items"]] == ["RECEIVED"]
    assert received.json()["items"][0]["actor"] == investigator_id
    assert (
        evidence_client.get(
            f"{BASE}/CASE-900/evidence/{evidence_id}/objects/EOBJ-999/custody"
        ).status_code
        == 404
    )
    assert (
        evidence_client.post(
            finalize_path(evidence_id, item_id), headers=csrf_header(csrf)
        ).status_code
        == 404
    )  # Investigator has intake/read, not custody:write.

    custodian_csrf = login(evidence_client, "intake-custodian")
    finalized = evidence_client.post(
        finalize_path(evidence_id, item_id), headers=csrf_header(custodian_csrf)
    )
    assert finalized.status_code == 200, finalized.text
    preserved = finalized.json()
    assert preserved["state"] == "PRESERVED"
    assert preserved["validation_status"] == "accepted"
    assert preserved["detected_media_type"] == "application/pdf"
    assert preserved["preserved_by"].startswith("USR-")
    assert not (storage_root(evidence_client) / "quarantine" / f"{key}.part").exists()
    assert (storage_root(evidence_client) / "preserved" / f"{key}.bin").is_file()

    history = evidence_client.get(
        f"{BASE}/CASE-900/evidence/{evidence_id}/objects/{item_id}/custody"
    ).json()["items"]
    assert [event["event_type"] for event in history] == ["RECEIVED", "PRESERVED"]
    assert (history[1]["from_state"], history[1]["to_state"]) == (
        "QUARANTINED",
        "PRESERVED",
    )
    with application(evidence_client).state.session_factory() as session:
        actions = set(
            session.execute(
                select(AuditEvent.action).where(AuditEvent.entity_public_id == item_id)
            ).scalars()
        )
        assert {
            "evidence.object.received",
            "evidence.upload.completed",
            "evidence.preserved",
        } <= actions
        custody = session.execute(
            select(EvidenceCustodyEvent).where(EvidenceCustodyEvent.public_id == history[0]["id"])
        ).scalar_one()
        assert custody.actor_user_id is not None
        stored = session.execute(
            select(EvidenceObject).where(EvidenceObject.public_id == item_id)
        ).scalar_one()
        stored.original_filename = "rewritten.pdf"
        with pytest.raises(EvidenceObjectImmutableError):
            session.flush()
        session.rollback()

    assert evidence_client.get(content_path(evidence_id, item_id)).status_code == 405


def test_reacquisition_adds_a_new_object_and_projects_integrity_into_existing_profile(
    evidence_client: TestClient,
) -> None:
    provision(evidence_client, "reacquire-investigator", "INVESTIGATOR", "CASE-900")
    provision(evidence_client, "reacquire-custodian", "CUSTODIAN", "CASE-900")
    csrf = login(evidence_client, "reacquire-investigator")
    detail = register(evidence_client, csrf)
    evidence_id = cast(str, cast(dict[str, object], detail["evidence"])["id"])
    with application(evidence_client).state.session_factory() as session:
        case = session.execute(
            select(Case).where(Case.public_id == OPERATIONAL_CASE_ID)
        ).scalar_one()
        evidence = session.execute(
            select(Evidence).where(Evidence.public_id == evidence_id)
        ).scalar_one()
        actor = session.execute(
            select(User).where(User.username == "reacquire-investigator")
        ).scalar_one()
        set_evidence_profile(
            session,
            actor=actor.public_id,
            case=case,
            evidence=evidence,
            sections={
                name: ProfileSection(
                    status=ProfileStatus.UNKNOWN,
                    note="Synthetic V2.1 integration-test profile.",
                )
                for name in PROFILE_SECTIONS
            },
        )
        session.commit()
    before = evidence_client.get(f"{BASE}/CASE-900/evidence").json()["count"]
    response = evidence_client.post(
        f"{BASE}/CASE-900/evidence/{evidence_id}/objects",
        json={"original_filename": "new-synthetic.txt", "declared_media_type": "text/plain"},
        headers=csrf_header(csrf),
    )
    assert response.status_code == 201, response.text
    item_id = response.json()["id"]
    upload_response = upload(
        evidence_client, csrf, evidence_id, item_id, b"Synthetic re-acquisition.\n"
    )
    assert upload_response.status_code == 200
    custodian_csrf = login(evidence_client, "reacquire-custodian")
    finalized = evidence_client.post(
        finalize_path(evidence_id, item_id), headers=csrf_header(custodian_csrf)
    )
    assert finalized.status_code == 200 and finalized.json()["state"] == "PRESERVED"
    after = evidence_client.get(f"{BASE}/CASE-900/evidence").json()["count"]
    assert after == before
    profile = evidence_client.get(f"{BASE}/CASE-900/evidence/{evidence_id}/profile").json()
    integrity = profile["integrity"]
    assert integrity["status"] == "verified"
    expected = hashlib.sha256(b"Synthetic re-acquisition.\n").hexdigest()
    assert any(attribute["value"] == expected for attribute in integrity["attributes"])
    assert "authenticity" in integrity["note"].lower()


def test_invalid_signature_is_rejected_and_quarantine_is_discarded(
    evidence_client: TestClient,
) -> None:
    provision(evidence_client, "reject-investigator", "INVESTIGATOR", "CASE-900")
    provision(evidence_client, "reject-custodian", "CUSTODIAN", "CASE-900")
    csrf = login(evidence_client, "reject-investigator")
    detail = register(evidence_client, csrf)
    evidence_id = cast(str, cast(dict[str, object], detail["evidence"])["id"])
    item_id = object_id(detail)
    upload_response = upload(
        evidence_client, csrf, evidence_id, item_id, b"plain text is not a PDF"
    )
    assert upload_response.status_code == 200
    key = storage_key(evidence_client, item_id)
    custodian_csrf = login(evidence_client, "reject-custodian")
    rejected = evidence_client.post(
        finalize_path(evidence_id, item_id), headers=csrf_header(custodian_csrf)
    )
    assert rejected.status_code == 200
    assert rejected.json()["state"] == "REJECTED"
    assert rejected.json()["validation_status"] == "rejected"
    assert rejected.json()["detected_media_type"] == "text/plain"
    assert not (storage_root(evidence_client) / "quarantine" / f"{key}.part").exists()
    assert not (storage_root(evidence_client) / "preserved" / f"{key}.bin").exists()
    events = evidence_client.get(
        f"{BASE}/CASE-900/evidence/{evidence_id}/objects/{item_id}/custody"
    ).json()["items"]
    assert [event["event_type"] for event in events] == ["RECEIVED"]


def test_zero_byte_upload_has_standard_hashes_and_finalization_is_one_way(
    evidence_client: TestClient,
) -> None:
    provision(evidence_client, "empty-custodian", "CUSTODIAN", "CASE-900")
    csrf = login(evidence_client, "empty-custodian")
    detail = register(
        evidence_client, csrf, original_filename="empty.txt", declared_media_type="text/plain"
    )
    evidence_id = cast(str, cast(dict[str, object], detail["evidence"])["id"])
    item_id = object_id(detail)
    premature = evidence_client.post(finalize_path(evidence_id, item_id), headers=csrf_header(csrf))
    assert premature.status_code == 409
    assert premature.json()["error"]["code"] == "evidence_state_conflict"
    empty = upload(evidence_client, csrf, evidence_id, item_id, b"")
    assert empty.status_code == 200 and empty.json()["byte_size"] == 0
    assert empty.json()["sha256"] == hashlib.sha256(b"").hexdigest()
    assert empty.json()["sha512"] == hashlib.sha512(b"").hexdigest()
    preserved = evidence_client.post(finalize_path(evidence_id, item_id), headers=csrf_header(csrf))
    assert preserved.status_code == 200 and preserved.json()["state"] == "PRESERVED"
    repeated = evidence_client.post(finalize_path(evidence_id, item_id), headers=csrf_header(csrf))
    assert repeated.status_code == 409


def test_streaming_limit_uses_actual_bytes_and_cleans_partial_upload(
    evidence_client: TestClient,
) -> None:
    provision(evidence_client, "limit-investigator", "INVESTIGATOR", "CASE-900")
    csrf = login(evidence_client, "limit-investigator")
    detail = register(evidence_client, csrf)
    evidence_id = cast(str, cast(dict[str, object], detail["evidence"])["id"])
    item_id = object_id(detail)
    # Deliberately lie about Content-Length; the streamed-byte counter must still reject it.
    response = evidence_client.put(
        content_path(evidence_id, item_id),
        content=b"%PDF-1.7\n" + b"x" * 2_000_100,
        headers={
            **csrf_header(csrf),
            "Content-Type": "application/octet-stream",
            "Content-Length": "1",
        },
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "evidence_too_large"
    key = storage_key(evidence_client, item_id)
    assert not (storage_root(evidence_client) / "quarantine" / f"{key}.part").exists()
    current = evidence_client.get(f"{BASE}/CASE-900/evidence/{evidence_id}/intake").json()
    obj = next(value for value in current["objects"] if value["id"] == item_id)
    assert obj["state"] == "QUARANTINED" and obj["upload_completed_at"] is None
    assert obj["sha256"] is None and obj["sha512"] is None


def test_storage_failure_and_invalid_metadata_are_sanitized(
    evidence_client: TestClient,
) -> None:
    class FailingStorage(LocalEvidenceStorage):
        def write_quarantine_chunk(self, handle: QuarantineHandle, chunk: bytes) -> None:
            raise EvidenceStorageFailure("private path /secret/path and synthetic bytes")

    provision(evidence_client, "storage-investigator", "INVESTIGATOR", "CASE-900")
    csrf = login(evidence_client, "storage-investigator")
    detail = register(evidence_client, csrf)
    evidence_id = cast(str, cast(dict[str, object], detail["evidence"])["id"])
    item_id = object_id(detail)
    original = evidence_client.app.state.evidence_storage
    evidence_client.app.state.evidence_storage = FailingStorage(storage_root(evidence_client))
    try:
        failed = upload(evidence_client, csrf, evidence_id, item_id, b"%PDF-1.7\nsecret").json()
    finally:
        evidence_client.app.state.evidence_storage = original
    assert failed["error"]["code"] == "evidence_storage_unavailable"
    assert "/secret/path" not in str(failed) and "secret" not in str(failed)
    key = storage_key(evidence_client, item_id)
    assert not (storage_root(evidence_client) / "quarantine" / f"{key}.part").exists()

    invalid = evidence_client.post(
        f"{BASE}/CASE-900/evidence/intake",
        json={
            "label": "Traversal",
            "evidence_type": "document",
            "original_filename": "../secret/file.txt",
            "declared_media_type": "text/plain",
            "sha256": "client-controlled",
            "acquired_by": "USR-999",
        },
        headers=csrf_header(csrf),
    )
    assert invalid.status_code == 422
    assert "secret" not in invalid.text and "client-controlled" not in invalid.text


def test_read_only_roles_and_identity_admin_do_not_gain_evidence_access(
    evidence_client: TestClient,
) -> None:
    for role in ("REVIEWER", "RESEARCHER"):
        username = f"readonly-{role.lower()}"
        provision(evidence_client, username, role, "CASE-900")
        csrf = login(evidence_client, username)
        denied = evidence_client.post(
            f"{BASE}/CASE-900/evidence/intake",
            json={
                "label": "Denied",
                "evidence_type": "document",
                "original_filename": "denied.txt",
                "declared_media_type": "text/plain",
            },
            headers=csrf_header(csrf),
        )
        assert denied.status_code == 404
    provision(evidence_client, "identity-admin-only", "ADMINISTRATOR", None)
    provision(evidence_client, "admin-separation-investigator", "INVESTIGATOR", OPERATIONAL_CASE_ID)
    investigator_csrf = login(evidence_client, "admin-separation-investigator")
    detail = register(evidence_client, investigator_csrf)
    evidence_id = cast(str, cast(dict[str, object], detail["evidence"])["id"])
    item_id = object_id(detail)

    login(evidence_client, "identity-admin-only")
    assert evidence_client.get(f"{BASE}/CASE-900/evidence").status_code == 404
    assert evidence_client.get(f"{BASE}/CASE-900/evidence/{evidence_id}/intake").status_code == 404
    assert (
        evidence_client.get(
            f"{BASE}/CASE-900/evidence/{evidence_id}/objects/{item_id}/custody"
        ).status_code
        == 404
    )
    denied = evidence_client.post(
        f"{BASE}/CASE-900/evidence/intake",
        json={
            "label": "Administrator cannot intake",
            "evidence_type": "document",
            "original_filename": "admin-denied.txt",
            "declared_media_type": "text/plain",
        },
    )
    assert denied.status_code == 404


def test_inactive_user_and_wrong_case_are_rejected(evidence_client: TestClient) -> None:
    provision(evidence_client, "inactive-intake-user", "INVESTIGATOR", "CASE-900")
    login(evidence_client, "inactive-intake-user")
    with application(evidence_client).state.session_factory() as session:
        user = session.execute(
            select(User).where(User.username == "inactive-intake-user")
        ).scalar_one()
        user.status = "disabled"
        session.commit()
    inactive = evidence_client.post(f"{BASE}/CASE-900/evidence/intake", json={})
    assert inactive.status_code == 401

    provision(evidence_client, "case-limited-intake", "INVESTIGATOR", "CASE-900")
    csrf = login(evidence_client, "case-limited-intake")
    denied = evidence_client.post(
        f"{BASE}/CASE-002/evidence/intake",
        json={
            "label": "Wrong case",
            "evidence_type": "document",
            "original_filename": "wrong.txt",
            "declared_media_type": "text/plain",
        },
        headers=csrf_header(csrf),
    )
    assert denied.status_code == 404


def test_organization_membership_is_required_even_with_a_case_assignment(
    evidence_client: TestClient,
) -> None:
    provision(evidence_client, "cross-org-user", "INVESTIGATOR", "CASE-900")
    with application(evidence_client).state.session_factory() as session:
        other_org = Organization(
            name="Synthetic isolated organization",
            status="active",
            created_by="test:provisioning",
            updated_by="test:provisioning",
        )
        session.add(other_org)
        session.flush()
        other_case = Case(
            organization_id=other_org.id,
            title="Synthetic isolated case",
            summary=None,
            is_demonstration=False,
            created_by="test:provisioning",
            updated_by="test:provisioning",
        )
        session.add(other_case)
        session.flush()
        user = session.execute(select(User).where(User.username == "cross-org-user")).scalar_one()
        membership = session.execute(
            select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
        ).scalar_one()
        role = session.execute(select(Role).where(Role.name == "INVESTIGATOR")).scalar_one()
        session.add(
            RoleAssignment(
                membership_id=membership.id,
                role_id=role.id,
                case_id=other_case.id,
                created_by="test:provisioning",
                updated_by="test:provisioning",
            )
        )
        session.commit()
        other_case_id = other_case.public_id
    csrf = login(evidence_client, "cross-org-user")
    denied = evidence_client.post(
        f"{BASE}/{other_case_id}/evidence/intake",
        json={
            "label": "Cross organization",
            "evidence_type": "document",
            "original_filename": "other.txt",
            "declared_media_type": "text/plain",
        },
        headers=csrf_header(csrf),
    )
    assert denied.status_code == 404


def test_custody_and_preserved_object_orm_records_are_immutable(
    evidence_client: TestClient,
) -> None:
    provision(evidence_client, "immutable-investigator", "INVESTIGATOR", "CASE-900")
    provision(evidence_client, "immutable-custodian", "CUSTODIAN", "CASE-900")
    csrf = login(evidence_client, "immutable-investigator")
    detail = register(evidence_client, csrf)
    evidence_id = cast(str, cast(dict[str, object], detail["evidence"])["id"])
    item_id = object_id(detail)
    upload_response = upload(evidence_client, csrf, evidence_id, item_id, b"%PDF-1.7\nimmutable")
    assert upload_response.status_code == 200
    custodian_csrf = login(evidence_client, "immutable-custodian")
    assert (
        evidence_client.post(
            finalize_path(evidence_id, item_id), headers=csrf_header(custodian_csrf)
        ).status_code
        == 200
    )
    with application(evidence_client).state.session_factory() as session:
        stored = session.execute(
            select(EvidenceObject).where(EvidenceObject.public_id == item_id)
        ).scalar_one()
        stored.sha256 = "0" * 64
        with pytest.raises(EvidenceObjectImmutableError):
            session.flush()
        session.rollback()
        custody = (
            session.execute(
                select(EvidenceCustodyEvent)
                .join(EvidenceObject, EvidenceObject.id == EvidenceCustodyEvent.evidence_object_id)
                .where(EvidenceObject.public_id == item_id)
            )
            .scalars()
            .first()
        )
        assert custody is not None
        event_id = custody.public_id
        custody.reason = "tampered"
        with pytest.raises(EvidenceCustodyEventImmutableError):
            session.flush()
        session.rollback()
        custody = session.execute(
            select(EvidenceCustodyEvent).where(EvidenceCustodyEvent.public_id == event_id)
        ).scalar_one()
        session.delete(custody)
        with pytest.raises(EvidenceCustodyEventImmutableError):
            session.flush()
        session.rollback()


def test_postgresql_custody_trigger_rejects_update_and_delete(
    evidence_client: TestClient,
) -> None:
    provision(evidence_client, "custody-trigger-sql", "INVESTIGATOR", "CASE-900")
    csrf = login(evidence_client, "custody-trigger-sql")
    item_id = object_id(register(evidence_client, csrf))
    with application(evidence_client).state.session_factory() as session:
        event = session.execute(
            select(EvidenceCustodyEvent)
            .join(EvidenceObject, EvidenceObject.id == EvidenceCustodyEvent.evidence_object_id)
            .where(EvidenceObject.public_id == item_id)
        ).scalar_one()
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("database-level custody trigger exists on PostgreSQL only")
        event_id = event.public_id
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(
                text("UPDATE evidence_custody_events SET reason='tampered' WHERE public_id=:id"),
                {"id": event_id},
            )
        session.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(
                text("DELETE FROM evidence_custody_events WHERE public_id=:id"),
                {"id": event_id},
            )


def test_invalid_digest_length_is_rejected_by_database_constraint(
    evidence_client: TestClient,
) -> None:
    provision(evidence_client, "digest-check-user", "INVESTIGATOR", "CASE-900")
    csrf = login(evidence_client, "digest-check-user")
    item_id = object_id(register(evidence_client, csrf))
    with application(evidence_client).state.session_factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                EvidenceObject.__table__.update()
                .where(EvidenceObject.public_id == item_id)
                .values(sha256="too-short")
            )
        session.rollback()
