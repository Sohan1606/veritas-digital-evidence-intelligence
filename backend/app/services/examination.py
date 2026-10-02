"""V2.3 examination commands used by the API: eligibility, idempotent creation, cancel, retry.

Order of events for every request that creates a run:

1. The route has already authorized the principal (``examination:execute``, authenticated, not a
   demonstration Case) *before* this module runs. Nothing here touches storage until every
   database-level eligibility rule has passed, and storage is only ever *opened and closed* (no
   byte is read) to confirm the object is readable.
2. Eligibility: Evidence in the Case, EvidenceObject in that Evidence and Case, PRESERVED, Method
   exists/enabled/applicable, parameters satisfy the Method's contract.
3. Idempotency, decided by the database: a unique (Case, principal, key). The same key with an
   equivalent request returns the original run; with a different request it is a conflict.
4. One QUEUED run and one audit event are committed. Execution happens later, in the worker.

Creating a run never changes Evidence, an EvidenceObject, custody history or stored bytes.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    DemonstrationExaminationError,
    EvidenceObjectNotPreservedError,
    EvidenceStorageUnavailableError,
    IdempotencyConflictError,
    InvalidLifecycleTransitionError,
    InvalidMethodParametersError,
    MethodInapplicableError,
    MethodUnavailableError,
)
from app.core.security import Principal
from app.domain.enums import (
    AnalysisRunState,
    EvidenceObjectState,
    ExaminationAuditAction,
)
from app.domain.lifecycle import RETRYABLE_RUN_STATES
from app.domain.models import AnalysisRun, Case, Evidence, EvidenceObject
from app.examination.contracts import (
    MethodDefinition,
    ParameterValidationError,
    validate_parameters,
)
from app.examination.coordinator import CancelOutcome, cancel_run, record_run_event, run_context
from app.examination.registry import MethodRegistry
from app.schemas import (
    AnalysisRunCreateIn,
    AnalysisRunOut,
    MethodOut,
)
from app.services import queries
from app.services.evidence_storage import EvidenceStorage, EvidenceStorageFailure

STORAGE_UNAVAILABLE_MESSAGE = "Private evidence storage is unavailable"


def method_catalogue(registry: MethodRegistry) -> list[MethodOut]:
    """The registered Methods, projected from the registry (the only source of this data)."""
    return [MethodOut.model_validate(definition.describe()) for definition in registry.all()]


@dataclass(frozen=True, slots=True)
class _Eligible:
    definition: MethodDefinition
    evidence: Evidence
    item: EvidenceObject
    parameters: dict[str, Any]


def _check_eligibility(
    session: Session,
    *,
    case: Case,
    evidence_id: str,
    object_id: str,
    method_key: str,
    method_version: str,
    raw_parameters: object,
    registry: MethodRegistry,
) -> _Eligible:
    """Every rule that can be decided from the database. No storage access."""
    if case.is_demonstration:
        raise DemonstrationExaminationError("Demonstration cases never execute examination Methods")
    evidence, item = queries.get_evidence_object(session, case, evidence_id, object_id)
    if item.state is not EvidenceObjectState.PRESERVED:
        raise EvidenceObjectNotPreservedError("Only a PRESERVED EvidenceObject can be examined")
    definition = registry.get(method_key, method_version)
    if definition is None or not definition.enabled:
        raise MethodUnavailableError("The requested Method version is not available")
    if evidence.evidence_type not in definition.supported_evidence_types:
        raise MethodInapplicableError(
            f"{definition.reference} does not support {evidence.evidence_type.value} evidence"
        )
    if item.byte_size > definition.limits.max_object_bytes:
        raise MethodInapplicableError(
            f"The EvidenceObject exceeds the resource limit of {definition.reference}"
        )
    try:
        parameters = validate_parameters(definition, raw_parameters)
    except ParameterValidationError as exc:
        raise InvalidMethodParametersError(
            f"Parameters do not satisfy the parameter contract of {definition.reference} "
            f"({', '.join(exc.problems)})"
        ) from None
    return _Eligible(definition, evidence, item, parameters)


def _confirm_readable(storage: EvidenceStorage, item: EvidenceObject) -> None:
    """Open and close the preserved object: proves it is readable without reading any byte."""
    try:
        stream = storage.open_preserved_object(item.storage_key)
    except (EvidenceStorageFailure, OSError):
        raise EvidenceStorageUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from None
    with suppress(OSError):
        stream.close()


def request_fingerprint(case: Case, eligible: _Eligible, *, intent: str) -> str:
    """Deterministic SHA-256 of the canonical execution request (validated parameters, no key)."""
    canonical = json.dumps(
        {
            "case": case.public_id,
            "evidence": eligible.evidence.public_id,
            "evidence_object": eligible.item.public_id,
            "method": eligible.definition.key,
            "version": eligible.definition.version,
            "parameters": eligible.parameters,
            "intent": intent,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _find_by_key(session: Session, case: Case, actor: str, key: str) -> AnalysisRun | None:
    return session.execute(
        select(AnalysisRun).where(
            AnalysisRun.case_id == case.id,
            AnalysisRun.created_by == actor,
            AnalysisRun.idempotency_key == key,
        )
    ).scalar_one_or_none()


def _replay_or_conflict(existing: AnalysisRun, fingerprint: str) -> str:
    """The same key was used before: the same request replays, a different one conflicts."""
    if existing.request_fingerprint != fingerprint:
        raise IdempotencyConflictError(
            "This idempotency key was already used for a different execution request"
        )
    return existing.public_id


def _enqueue(
    session: Session,
    *,
    case: Case,
    principal: Principal,
    eligible: _Eligible,
    idempotency_key: str,
    intent: str,
    storage: EvidenceStorage,
    action: ExaminationAuditAction,
    audit_extra: dict[str, Any],
) -> tuple[str, bool]:
    """Create one QUEUED run (or replay the original). Returns ``(run public id, created)``."""
    actor = principal.subject
    fingerprint = request_fingerprint(case, eligible, intent=intent)
    existing = _find_by_key(session, case, actor, idempotency_key)
    if existing is not None:
        return _replay_or_conflict(existing, fingerprint), False
    _confirm_readable(storage, eligible.item)

    run = AnalysisRun(
        case_id=case.id,
        evidence_id=eligible.evidence.id,
        evidence_object_id=eligible.item.id,
        method_key=eligible.definition.key,
        method_version=eligible.definition.version,
        state=AnalysisRunState.QUEUED,
        parameters=eligible.parameters,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        created_by=actor,
        updated_by=actor,
    )
    session.add(run)
    try:
        session.flush()
    except IntegrityError:
        # A concurrent request with the same key committed first (the database decides).
        session.rollback()
        winner = _find_by_key(session, case, actor, idempotency_key)
        if winner is None:
            raise
        return _replay_or_conflict(winner, fingerprint), False
    record_run_event(
        session,
        run_context(session, run.id),
        action,
        actor=actor,
        state=AnalysisRunState.QUEUED,
        **audit_extra,
    )
    session.commit()
    return run.public_id, True


def create_run(
    session: Session,
    *,
    case: Case,
    principal: Principal,
    payload: AnalysisRunCreateIn,
    registry: MethodRegistry,
    storage: EvidenceStorage,
) -> tuple[AnalysisRunOut, bool]:
    eligible = _check_eligibility(
        session,
        case=case,
        evidence_id=payload.evidence_id,
        object_id=payload.evidence_object_id,
        method_key=payload.method_key,
        method_version=payload.method_version,
        raw_parameters=dict(payload.parameters),
        registry=registry,
    )
    public_id, created = _enqueue(
        session,
        case=case,
        principal=principal,
        eligible=eligible,
        idempotency_key=payload.idempotency_key,
        intent="create",
        storage=storage,
        action=ExaminationAuditAction.RUN_CREATED,
        audit_extra={},
    )
    return queries.analysis_run_out(session, case, public_id), created


def retry_run(
    session: Session,
    *,
    case: Case,
    principal: Principal,
    run_public_id: str,
    idempotency_key: str,
    registry: MethodRegistry,
    storage: EvidenceStorage,
) -> tuple[AnalysisRunOut, bool]:
    """Create a NEW run that copies the intent of a failed or cancelled one.

    The source run is only read: it keeps its identifier, state, timestamps and history. A
    run that is queued, running or completed is not retried.
    """
    source, evidence, item = queries.resolve_analysis_run(session, case, run_public_id)
    if source.state not in RETRYABLE_RUN_STATES:
        raise InvalidLifecycleTransitionError(
            f"A {source.state.value} run cannot be retried; only a failed or cancelled run can"
        )
    eligible = _check_eligibility(
        session,
        case=case,
        evidence_id=evidence.public_id,
        object_id=item.public_id,
        method_key=source.method_key,
        method_version=source.method_version,
        raw_parameters=dict(source.parameters),
        registry=registry,
    )
    public_id, created = _enqueue(
        session,
        case=case,
        principal=principal,
        eligible=eligible,
        idempotency_key=idempotency_key,
        intent=f"retry:{source.public_id}",
        storage=storage,
        action=ExaminationAuditAction.RUN_RETRIED,
        audit_extra={"source_run_id": source.public_id},
    )
    return queries.analysis_run_out(session, case, public_id), created


def cancel(
    session: Session, *, case: Case, principal: Principal, run_public_id: str
) -> tuple[AnalysisRunOut, CancelOutcome]:
    run, _evidence, _item = queries.resolve_analysis_run(session, case, run_public_id)
    outcome = cancel_run(session, run_id=run.id, actor=principal.subject)
    session.commit()
    return queries.analysis_run_out(session, case, run_public_id), outcome
