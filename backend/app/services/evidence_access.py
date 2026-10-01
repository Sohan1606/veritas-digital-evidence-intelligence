"""V2.2 authorized retrieval and independent integrity verification of PRESERVED EvidenceObjects.

Guarantees shared by both operations:

* Only PRESERVED objects are eligible. There is no quarantine or alternate-location fallback.
* Bytes come only from ``EvidenceStorage.open_preserved_object`` and every read is bounded by
  ``HASH_CHUNK_BYTES``. No byte, path or storage key reaches a log, an AuditEvent or the database.
* Neither operation writes an EvidenceObject value, a custody event or a stored byte, and neither
  takes a row lock. The only write is one append-only AuditEvent.
* Integrity verification compares recomputed values with the immutable intake values. A match is
  an integrity result only; it is never an authenticity determination.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncGenerator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, BinaryIO

import anyio
from sqlalchemy.orm import Session

from app.core.errors import EvidenceConflictError, EvidenceStorageUnavailableError
from app.core.security import Principal
from app.domain.enums import EvidenceAuditAction, EvidenceIntegrityResult, EvidenceObjectState
from app.domain.models import Case
from app.schemas import EvidenceIntegrityVerificationOut
from app.services import queries
from app.services.evidence_hashing import HASH_CHUNK_BYTES, DigestSummary, digest_file
from app.services.evidence_signatures import SUPPORTED_MEDIA_TYPES
from app.services.evidence_storage import EvidenceStorage, EvidenceStorageFailure
from app.services.records import record_audit_event

logger = logging.getLogger("veritas.evidence")

FALLBACK_MEDIA_TYPE = "application/octet-stream"
STORAGE_UNAVAILABLE_MESSAGE = "Private evidence storage is unavailable"
MATCH_MESSAGE = "Preserved bytes match the recorded intake integrity values."
MISMATCH_MESSAGE = (
    "Preserved bytes do not match the recorded intake integrity values. This is an integrity "
    "comparison only; it does not determine authenticity."
)
UNAVAILABLE_MESSAGE = (
    "The preserved object could not be read from private evidence storage, so no integrity "
    "comparison was made."
)

_PUBLIC_OBJECT_ID = re.compile(r"^EOBJ-\d{3,9}$")
_FALLBACK_DOWNLOAD_NAME = "evidence-object.bin"


@dataclass(frozen=True, slots=True)
class PreservedObject:
    """The immutable facts retrieval and verification need; never serialized to a client."""

    evidence_public_id: str
    public_id: str
    storage_key: str = field(repr=False)
    media_type: str
    byte_size: int
    sha256: str
    sha512: str

    @property
    def download_name(self) -> str:
        """Deterministic filename derived only from the public identifier.

        The submitter-declared ``original_filename`` is never used in a response header.
        """
        if _PUBLIC_OBJECT_ID.fullmatch(self.public_id):
            return f"{self.public_id}.bin"
        return _FALLBACK_DOWNLOAD_NAME


def resolve_preserved_object(
    session: Session, *, case: Case, evidence_id: str, object_id: str, operation: str
) -> PreservedObject:
    """Resolve Case -> Evidence (in Case) -> EvidenceObject (in Case and Evidence) -> PRESERVED.

    ``operation`` is the past participle used in conflict messages (``retrieved``/``verified``).
    Authorization has already been decided by the route; this adds the defence-in-depth checks
    that belong to the service: demonstration Cases, scoping by Case and Evidence, and state.
    """
    if case.is_demonstration:
        raise EvidenceConflictError(
            f"Evidence objects of demonstration cases cannot be {operation}"
        )
    evidence, item = queries.get_evidence_object(session, case, evidence_id, object_id)
    if item.state is not EvidenceObjectState.PRESERVED:
        raise EvidenceConflictError(f"Only a preserved evidence object can be {operation}")
    if item.sha256 is None or item.sha512 is None or item.detected_media_type is None:
        # Unreachable while the preserved_metadata_consistency constraint holds.
        raise RuntimeError("preserved EvidenceObject integrity metadata is incomplete")
    media_type = (
        item.detected_media_type
        if item.detected_media_type in SUPPORTED_MEDIA_TYPES
        else FALLBACK_MEDIA_TYPE
    )
    return PreservedObject(
        evidence_public_id=evidence.public_id,
        public_id=item.public_id,
        storage_key=item.storage_key,
        media_type=media_type,
        byte_size=item.byte_size,
        sha256=item.sha256,
        sha512=item.sha512,
    )


# --- Retrieval --------------------------------------------------------------------------


def _stream_size(stream: BinaryIO) -> int | None:
    """Current size of an open stream for Content-Length, or None if it cannot be determined."""
    try:
        if not stream.seekable():
            return None
        size = stream.seek(0, os.SEEK_END)
    except (OSError, ValueError):
        return None
    try:
        stream.seek(0)
    except (OSError, ValueError):
        raise EvidenceStorageFailure("Evidence storage operation failed") from None
    return size


class _BoundedReader:
    """Sanitized chunk reader capped at the size fixed when the object was opened.

    Reading never exceeds ``HASH_CHUNK_BYTES`` per call. If the advertised size is known and the
    file ends early (it shrank while open), the read fails rather than returning a short body
    that looks complete. Bytes beyond the advertised size are never read.
    """

    def __init__(self, stream: BinaryIO, size: int | None) -> None:
        self._stream = stream
        self._remaining = size

    def read(self) -> bytes:
        """Return the next chunk, or ``b""`` at the end of the object."""
        if self._remaining == 0:
            return b""
        want = (
            HASH_CHUNK_BYTES if self._remaining is None else min(HASH_CHUNK_BYTES, self._remaining)
        )
        try:
            chunk = self._stream.read(want)
        except OSError:
            raise EvidenceStorageFailure("Evidence storage operation failed") from None
        if self._remaining is not None:
            if not chunk:
                raise EvidenceStorageFailure("Evidence storage operation failed")
            self._remaining -= len(chunk)
        return chunk


class EvidenceRetrieval:
    """One authorized retrieval: headers are known and the first chunk is already read.

    Opening and reading the first chunk before any response header is sent lets an unreadable
    object become a clean error response instead of a broken 200. The audit event is committed
    *before the final chunk is released*, so no complete copy of the bytes leaves the server
    without a committed ``evidence.object.retrieved`` event, and an interrupted or failed
    transfer never records one. The event records that the final chunk was released to the
    transport; it cannot prove that the client received it.
    """

    def __init__(
        self,
        *,
        session: Session,
        case: Case,
        principal: Principal,
        target: PreservedObject,
        stream: BinaryIO,
        size: int | None,
        reader: _BoundedReader,
        first: bytes,
        following: bytes,
    ) -> None:
        self._session = session
        self._case = case
        self._principal = principal
        self._target = target
        self._stream = stream
        self._reader = reader
        self._first = first
        self._following = following
        self._audited = False
        self.content_length = size
        self.media_type = target.media_type
        self.filename = target.download_name

    def close(self) -> None:
        with suppress(OSError):
            self._stream.close()

    def record_retrieved(self, byte_size: int) -> None:
        """Commit the single retrieval event. Metadata only: never bytes, paths or keys."""
        try:
            record_audit_event(
                self._session,
                actor=self._principal.subject,
                action=EvidenceAuditAction.OBJECT_RETRIEVED.value,
                entity_type="evidence_object",
                entity_public_id=self._target.public_id,
                case=self._case,
                details={
                    "evidence_id": self._target.evidence_public_id,
                    "byte_size": byte_size,
                    "media_type": self._target.media_type,
                    "state": EvidenceObjectState.PRESERVED.value,
                },
            )
            self._session.commit()
        except BaseException:
            self._session.rollback()
            raise
        self._audited = True

    async def chunks(self) -> AsyncGenerator[bytes, None]:
        """Yield the object in bounded chunks, holding back the last one until it is audited."""
        try:
            current, following, sent = self._first, self._following, 0
            while following:
                yield current
                sent += len(current)
                current, following = following, await anyio.to_thread.run_sync(self._reader.read)
            # ``current`` is now the final chunk (empty only for an empty object).
            if not self._audited:
                await anyio.to_thread.run_sync(self.record_retrieved, sent + len(current))
            if current:
                yield current
        except EvidenceStorageFailure as exc:
            logger.warning(
                "preserved evidence read failed while streaming",
                extra={
                    "event": "evidence_retrieval_failed",
                    "entity_id": self._target.public_id,
                    "exception_type": type(exc).__name__,
                },
            )
            raise
        finally:
            self.close()


def open_retrieval(
    session: Session,
    *,
    case: Case,
    evidence_id: str,
    object_id: str,
    principal: Principal,
    storage: EvidenceStorage,
) -> EvidenceRetrieval:
    """Authorize-by-resolution, open the preserved object and read its first chunks."""
    target = resolve_preserved_object(
        session, case=case, evidence_id=evidence_id, object_id=object_id, operation="retrieved"
    )
    # End the read transaction now: no database connection is held while bytes stream.
    session.commit()
    try:
        stream = storage.open_preserved_object(target.storage_key)
    except EvidenceStorageFailure:
        raise EvidenceStorageUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from None
    try:
        size = _stream_size(stream)
        reader = _BoundedReader(stream, size)
        first = reader.read()
        following = reader.read() if first else b""
        retrieval = EvidenceRetrieval(
            session=session,
            case=case,
            principal=principal,
            target=target,
            stream=stream,
            size=size,
            reader=reader,
            first=first,
            following=following,
        )
        if not following:
            # The first chunk is also the last (or the object is empty): audit before the
            # response exists, so a failed audit write returns an error and releases nothing.
            retrieval.record_retrieved(len(first))
    except EvidenceStorageFailure:
        stream.close()
        raise EvidenceStorageUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from None
    except BaseException:
        stream.close()
        raise
    return retrieval


# --- Integrity verification -------------------------------------------------------------


def _recompute(storage: EvidenceStorage, target: PreservedObject) -> DigestSummary | None:
    """Recompute byte count, SHA-256 and SHA-512 with bounded reads; ``None`` if unreadable.

    A comparison is only ever made from a complete, successful read, so a storage failure can
    never be misreported as MISMATCH.
    """
    try:
        with storage.open_preserved_object(target.storage_key) as stream:
            return digest_file(stream)
    except (EvidenceStorageFailure, OSError) as exc:
        logger.warning(
            "preserved evidence object could not be read for verification",
            extra={
                "event": "evidence_verification_unavailable",
                "entity_id": target.public_id,
                "exception_type": type(exc).__name__,
            },
        )
        return None


def verify_preserved_object(
    session: Session,
    *,
    case: Case,
    evidence_id: str,
    object_id: str,
    principal: Principal,
    storage: EvidenceStorage,
) -> EvidenceIntegrityVerificationOut:
    """Independently recompute integrity values and record exactly one AuditEvent.

    Observational: the EvidenceObject row, its custody history and the stored bytes are never
    written. Every request that passes authorization and eligibility yields one result and one
    audit event; if the event cannot be committed the request fails and no result is returned.
    """
    target = resolve_preserved_object(
        session, case=case, evidence_id=evidence_id, object_id=object_id, operation="verified"
    )
    # End the read transaction before slow storage I/O.
    session.commit()
    computed = _recompute(storage, target)

    if computed is None:
        result = EvidenceIntegrityResult.UNAVAILABLE
    elif (computed.byte_size, computed.sha256, computed.sha512) == (
        target.byte_size,
        target.sha256,
        target.sha512,
    ):
        result = EvidenceIntegrityResult.MATCH
    else:
        result = EvidenceIntegrityResult.MISMATCH

    try:
        event = record_audit_event(
            session,
            actor=principal.subject,
            action=EvidenceAuditAction.OBJECT_INTEGRITY_VERIFIED.value,
            entity_type="evidence_object",
            entity_public_id=target.public_id,
            case=case,
            details={
                "evidence_id": target.evidence_public_id,
                "result": result.value,
                "expected_byte_size": target.byte_size,
                "expected_sha256": target.sha256,
                "expected_sha512": target.sha512,
                "computed_byte_size": computed.byte_size if computed else None,
                "computed_sha256": computed.sha256 if computed else None,
                "computed_sha512": computed.sha512 if computed else None,
            },
        )
        session.commit()
    except BaseException:
        session.rollback()
        raise

    common: dict[str, Any] = {
        "evidence_object_id": target.public_id,
        "result": result,
        "byte_size": target.byte_size,
        "sha256": target.sha256,
        "sha512": target.sha512,
        "verified_at": event.occurred_at,
        "verified_by": principal.subject,
    }
    if computed is not None and result is EvidenceIntegrityResult.MISMATCH:
        return EvidenceIntegrityVerificationOut(
            **common,
            message=MISMATCH_MESSAGE,
            expected_byte_size=target.byte_size,
            computed_byte_size=computed.byte_size,
            expected_sha256=target.sha256,
            computed_sha256=computed.sha256,
            expected_sha512=target.sha512,
            computed_sha512=computed.sha512,
        )
    message = MATCH_MESSAGE if result is EvidenceIntegrityResult.MATCH else UNAVAILABLE_MESSAGE
    return EvidenceIntegrityVerificationOut(**common, message=message)
