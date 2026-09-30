"""Evidence Intake workflow: quarantine, bounded hashing, validation and preservation."""

from __future__ import annotations

from collections.abc import AsyncIterable
from contextlib import suppress
from datetime import datetime
from uuid import uuid4

import anyio
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from app.core.config import Settings
from app.core.errors import (
    EvidenceConflictError,
    EvidenceStorageUnavailableError,
    EvidenceUploadRequestError,
    EvidenceUploadTooLargeError,
)
from app.core.logging import request_id_var
from app.core.security import Principal
from app.db.types import utcnow
from app.domain.enums import (
    EvidenceAuditAction,
    EvidenceCustodyEventType,
    EvidenceObjectState,
    EvidenceValidationStatus,
)
from app.domain.models import (
    Case,
    Evidence,
    EvidenceCustodyEvent,
    EvidenceObject,
)
from app.schemas import EvidenceIntakeIn
from app.services.evidence_hashing import (
    HASH_CHUNK_BYTES,
    DigestSummary,
    StreamingDigest,
    digest_file,
)
from app.services.evidence_signatures import (
    SIGNATURE_PREFIX_BYTES,
    SignatureResult,
    validate_signature,
)
from app.services.evidence_storage import (
    EvidenceStorage,
    EvidenceStorageFailure,
    QuarantineHandle,
)
from app.services.records import record_audit_event, register_evidence


def _ensure_intake_case(case: Case) -> None:
    if case.is_demonstration:
        raise EvidenceConflictError("Evidence intake is not permitted for demonstration cases")


def _new_object(
    session: Session,
    *,
    case: Case,
    evidence: Evidence,
    principal: Principal,
    original_filename: str,
    declared_media_type: str,
) -> EvidenceObject:
    _ensure_intake_case(case)
    if principal.user_id is None or not principal.authenticated:
        raise EvidenceConflictError("Evidence intake requires an authenticated user")
    actor = principal.subject
    evidence_object = EvidenceObject(
        case_id=case.id,
        organization_id=case.organization_id,
        evidence_id=evidence.id,
        storage_key=uuid4().hex,
        original_filename=original_filename,
        declared_media_type=declared_media_type,
        byte_size=0,
        state=EvidenceObjectState.QUARANTINED,
        validation_status=EvidenceValidationStatus.PENDING,
        acquired_at=utcnow(),
        acquired_by=actor,
        created_by=actor,
        updated_by=actor,
    )
    session.add(evidence_object)
    session.flush()
    custody = EvidenceCustodyEvent(
        case_id=case.id,
        organization_id=case.organization_id,
        evidence_id=evidence.id,
        evidence_object_id=evidence_object.id,
        event_type=EvidenceCustodyEventType.RECEIVED,
        from_state=None,
        to_state=EvidenceObjectState.QUARANTINED.value,
        actor_user_id=principal.user_id,
        counterparty_user_id=None,
        reason="Evidence object registered in private quarantine.",
        request_id=request_id_var.get() or "unknown",
        created_by=actor,
        updated_by=actor,
    )
    session.add(custody)
    record_audit_event(
        session,
        actor=actor,
        action=EvidenceAuditAction.OBJECT_RECEIVED.value,
        entity_type="evidence_object",
        entity_public_id=evidence_object.public_id,
        case=case,
        details={"state": EvidenceObjectState.QUARANTINED.value},
    )
    return evidence_object


def register_evidence_intake(
    session: Session,
    *,
    case: Case,
    principal: Principal,
    payload: EvidenceIntakeIn,
) -> tuple[Evidence, EvidenceObject]:
    """Create one logical Evidence and its first, empty quarantined acquisition."""
    _ensure_intake_case(case)
    evidence = register_evidence(
        session,
        actor=principal.subject,
        case=case,
        label=payload.label,
        evidence_type=payload.evidence_type,
        description=payload.description,
    )
    evidence_object = _new_object(
        session,
        case=case,
        evidence=evidence,
        principal=principal,
        original_filename=payload.original_filename,
        declared_media_type=payload.declared_media_type,
    )
    return evidence, evidence_object


def register_additional_evidence_object(
    session: Session,
    *,
    case: Case,
    evidence: Evidence,
    principal: Principal,
    original_filename: str,
    declared_media_type: str,
) -> EvidenceObject:
    """Create a new acquisition for an existing logical Evidence record."""
    if evidence.case_id != case.id:
        raise EvidenceConflictError("Evidence does not belong to the requested case")
    return _new_object(
        session,
        case=case,
        evidence=evidence,
        principal=principal,
        original_filename=original_filename,
        declared_media_type=declared_media_type,
    )


def _locked_object(
    session: Session, *, case: Case, evidence_id: str, object_id: str
) -> EvidenceObject:
    return (
        session.execute(
            select(EvidenceObject)
            .join(Evidence, Evidence.id == EvidenceObject.evidence_id)
            .where(
                Evidence.public_id == evidence_id,
                Evidence.case_id == case.id,
                EvidenceObject.public_id == object_id,
                EvidenceObject.case_id == case.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        or _raise_not_found()
    )


def _raise_not_found() -> EvidenceObject:
    from app.core.errors import NotFoundError

    raise NotFoundError("Evidence object was not found")


def _check_content_length(content_length: str | None, max_bytes: int) -> None:
    if content_length is None:
        return
    try:
        declared = int(content_length)
    except ValueError:
        raise EvidenceUploadRequestError("Content-Length must be a non-negative integer") from None
    if declared < 0:
        raise EvidenceUploadRequestError("Content-Length must be a non-negative integer")
    if declared > max_bytes:
        raise EvidenceUploadTooLargeError("Evidence upload exceeds the configured byte limit")


def _discard_after_failure(
    storage: EvidenceStorage, storage_key: str, handle: QuarantineHandle | None
) -> None:
    if handle is not None:
        with suppress(EvidenceStorageFailure):
            storage.close_quarantine_object(handle)
    # The object stays QUARANTINED with no completion metadata and is never readable.
    with suppress(EvidenceStorageFailure):
        storage.discard_quarantine_object(storage_key)


async def upload_evidence_content(
    session: Session,
    *,
    case: Case,
    evidence_id: str,
    object_id: str,
    principal: Principal,
    storage: EvidenceStorage,
    settings: Settings,
    body: AsyncIterable[bytes],
    content_length: str | None,
) -> EvidenceObject:
    """Stream raw bytes into exclusive quarantine storage and commit hashes atomically."""
    _check_content_length(content_length, settings.max_evidence_bytes)
    evidence_object = _locked_object(
        session, case=case, evidence_id=evidence_id, object_id=object_id
    )
    if evidence_object.state is not EvidenceObjectState.QUARANTINED:
        raise EvidenceConflictError("Only a quarantined evidence object can receive content")
    if evidence_object.upload_completed_at is not None:
        raise EvidenceConflictError("Evidence object content has already been uploaded")
    handle: QuarantineHandle | None = None
    try:
        if storage.exists(evidence_object.storage_key, preserved=True):
            raise EvidenceConflictError("Evidence object is already present in preserved storage")
        if storage.exists(evidence_object.storage_key, preserved=False):
            # A prior interrupted attempt may have left an incomplete private temp file.
            # The row is still incomplete and locked, so discard it before an exclusive retry.
            storage.discard_quarantine_object(evidence_object.storage_key)
        handle = await anyio.to_thread.run_sync(
            storage.create_quarantine_object, evidence_object.storage_key
        )
        accumulator = StreamingDigest()
        async for received_chunk in body:
            if not isinstance(received_chunk, bytes):
                raise EvidenceUploadRequestError("Evidence upload stream must contain bytes")
            # Split arbitrary ASGI chunks into bounded processing units. Oversize chunks
            # are rejected before they are written or included in either digest.
            for start in range(0, len(received_chunk), HASH_CHUNK_BYTES):
                chunk = received_chunk[start : start + HASH_CHUNK_BYTES]
                if accumulator.byte_size + len(chunk) > settings.max_evidence_bytes:
                    raise EvidenceUploadTooLargeError(
                        "Evidence upload exceeds the configured byte limit"
                    )
                accumulator.update(chunk)
                await anyio.to_thread.run_sync(storage.write_quarantine_chunk, handle, chunk)
        await anyio.to_thread.run_sync(storage.close_quarantine_object, handle)
        summary = accumulator.finish()
        evidence_object.byte_size = summary.byte_size
        evidence_object.sha256 = summary.sha256
        evidence_object.sha512 = summary.sha512
        evidence_object.upload_completed_at = utcnow()
        evidence_object.updated_by = principal.subject
        record_audit_event(
            session,
            actor=principal.subject,
            action=EvidenceAuditAction.UPLOAD_COMPLETED.value,
            entity_type="evidence_object",
            entity_public_id=evidence_object.public_id,
            case=case,
            details={
                "byte_size": summary.byte_size,
                "sha256": summary.sha256,
                "sha512": summary.sha512,
                "state": EvidenceObjectState.QUARANTINED.value,
            },
        )
        session.flush()
        session.commit()
        return evidence_object
    except ClientDisconnect:
        session.rollback()
        _discard_after_failure(storage, evidence_object.storage_key, handle)
        raise EvidenceUploadRequestError("Evidence upload did not complete") from None
    except EvidenceStorageFailure:
        session.rollback()
        _discard_after_failure(storage, evidence_object.storage_key, handle)
        raise EvidenceStorageUnavailableError("Private evidence storage is unavailable") from None
    except OSError:
        session.rollback()
        _discard_after_failure(storage, evidence_object.storage_key, handle)
        raise EvidenceStorageUnavailableError("Private evidence storage is unavailable") from None
    except BaseException:
        session.rollback()
        _discard_after_failure(storage, evidence_object.storage_key, handle)
        raise


def _read_summary_and_signature(
    storage: EvidenceStorage, evidence_object: EvidenceObject, *, preserved: bool
) -> tuple[DigestSummary, bytes, SignatureResult]:
    opener = storage.open_preserved_object if preserved else storage.open_quarantine_object
    try:
        with opener(evidence_object.storage_key) as stream:
            summary = digest_file(stream)
        with opener(evidence_object.storage_key) as stream:
            prefix = stream.read(SIGNATURE_PREFIX_BYTES)
    except (EvidenceStorageFailure, OSError):
        raise EvidenceStorageUnavailableError("Private evidence storage is unavailable") from None
    if (
        summary.byte_size != evidence_object.byte_size
        or summary.sha256 != evidence_object.sha256
        or summary.sha512 != evidence_object.sha512
    ):
        raise EvidenceConflictError("Stored bytes do not match the recorded upload digests")
    return summary, prefix, validate_signature(prefix, evidence_object.declared_media_type)


def _custody_preserved(
    session: Session, *, case: Case, evidence_object: EvidenceObject, principal: Principal
) -> None:
    if principal.user_id is None:
        raise EvidenceConflictError("Custody changes require an authenticated user")
    session.add(
        EvidenceCustodyEvent(
            case_id=case.id,
            organization_id=case.organization_id,
            evidence_id=evidence_object.evidence_id,
            evidence_object_id=evidence_object.id,
            event_type=EvidenceCustodyEventType.PRESERVED,
            from_state=EvidenceObjectState.QUARANTINED.value,
            to_state=EvidenceObjectState.PRESERVED.value,
            actor_user_id=principal.user_id,
            counterparty_user_id=None,
            reason="The quarantined object passed the configured basic signature check.",
            request_id=request_id_var.get() or "unknown",
            created_by=principal.subject,
            updated_by=principal.subject,
        )
    )


def finalize_evidence_object(
    session: Session,
    *,
    case: Case,
    evidence_id: str,
    object_id: str,
    principal: Principal,
    storage: EvidenceStorage,
) -> EvidenceObject:
    """Validate the stored bytes, then atomically preserve or permanently reject them."""
    evidence_object = _locked_object(
        session, case=case, evidence_id=evidence_id, object_id=object_id
    )
    if evidence_object.state is not EvidenceObjectState.QUARANTINED:
        raise EvidenceConflictError("Only a quarantined evidence object can be finalized")
    if evidence_object.upload_completed_at is None:
        raise EvidenceConflictError("Evidence upload must complete before finalization")

    try:
        quarantine_exists = storage.exists(evidence_object.storage_key, preserved=False)
        preserved_exists = storage.exists(evidence_object.storage_key, preserved=True)
    except EvidenceStorageFailure:
        raise EvidenceStorageUnavailableError("Private evidence storage is unavailable") from None
    if not quarantine_exists and not preserved_exists:
        raise EvidenceStorageUnavailableError("Uploaded evidence bytes are unavailable")

    _, prefix, validation = _read_summary_and_signature(
        storage, evidence_object, preserved=not quarantine_exists and preserved_exists
    )
    if not validation.accepted:
        evidence_object.detected_media_type = validation.detected_media_type
        evidence_object.validation_status = EvidenceValidationStatus.REJECTED
        evidence_object.validation_note = validation.note
        evidence_object.state = EvidenceObjectState.REJECTED
        evidence_object.updated_by = principal.subject
        record_audit_event(
            session,
            actor=principal.subject,
            action=EvidenceAuditAction.REJECTED.value,
            entity_type="evidence_object",
            entity_public_id=evidence_object.public_id,
            case=case,
            details={
                "state": EvidenceObjectState.REJECTED.value,
                "validation_status": EvidenceValidationStatus.REJECTED.value,
                "detected_media_type": validation.detected_media_type,
            },
        )
        try:
            session.flush()
            session.commit()
        except BaseException:
            session.rollback()
            raise
        if quarantine_exists:
            try:
                storage.discard_quarantine_object(evidence_object.storage_key)
            except EvidenceStorageFailure:
                raise EvidenceStorageUnavailableError(
                    "Rejected evidence remains unavailable for access pending storage cleanup"
                ) from None
        return evidence_object

    # Validate a final prefix value below too; keeping it local documents that only the
    # configured bounded prefix, not the full contents, reaches the signature detector.
    del prefix
    try:
        storage.finalize_preserved_object(evidence_object.storage_key)
    except EvidenceStorageFailure:
        raise EvidenceStorageUnavailableError("Private evidence storage is unavailable") from None

    preserved_at: datetime = utcnow()
    evidence_object.detected_media_type = validation.detected_media_type
    evidence_object.validation_status = EvidenceValidationStatus.ACCEPTED
    evidence_object.validation_note = validation.note
    evidence_object.state = EvidenceObjectState.PRESERVED
    evidence_object.preserved_at = preserved_at
    evidence_object.preserved_by = principal.subject
    evidence_object.updated_by = principal.subject
    _custody_preserved(session, case=case, evidence_object=evidence_object, principal=principal)
    record_audit_event(
        session,
        actor=principal.subject,
        action=EvidenceAuditAction.PRESERVED.value,
        entity_type="evidence_object",
        entity_public_id=evidence_object.public_id,
        case=case,
        details={
            "byte_size": evidence_object.byte_size,
            "sha256": evidence_object.sha256,
            "sha512": evidence_object.sha512,
            "detected_media_type": validation.detected_media_type,
            "state": EvidenceObjectState.PRESERVED.value,
        },
    )
    try:
        session.flush()
        session.commit()
    except BaseException:
        # The file may now be in preserved storage while the row remains quarantined.
        # Finalization is safely retryable: the next attempt rehashes and validates it.
        session.rollback()
        raise
    return evidence_object
