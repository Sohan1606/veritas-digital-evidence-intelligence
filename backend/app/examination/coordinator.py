"""Durable execution coordinator: the single owner of Analysis Run state changes.

There is no external queue. The ``analysis_runs`` table is the queue and the lifecycle record:

* **Claim.** A worker moves ``QUEUED -> RUNNING`` with a compare-and-set ``UPDATE ... WHERE
  state = 'queued'`` (on PostgreSQL the candidate row is also selected ``FOR UPDATE SKIP
  LOCKED``). Exactly one worker can win a run, on either database.
* **Fencing.** Each claim stamps ``started_at``; every later write of that worker repeats the
  stamp in its ``WHERE``. A worker that stalled and lost its run to recovery therefore cannot
  complete, fail, cancel or publish anything: its compare-and-set matches no row.
* **Heartbeat.** While a Method reads bytes the worker refreshes ``last_heartbeat_at`` and learns
  of a cancellation request in the same short transaction. No transaction is open while bytes
  are read.
* **Atomic publication.** ``RUNNING -> COMPLETED``, the Observations and the audit events are
  one transaction. A completion is refused if a cancellation was requested first, so a cancelled
  run can never become completed and a completed run can never become cancelled.
* **Recovery.** A RUNNING run whose heartbeat is older than the stale threshold goes back to
  QUEUED (or to CANCELLED if cancellation had been requested). The compare-and-set re-checks the
  heartbeat, so a healthy worker's run is never taken.

Every transition goes through :func:`transition`, which first asks ``domain.lifecycle``. Nothing
here reads or writes evidence bytes; the Method sees only the reader built in ``runner``.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import ColumnElement, Table, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import InvalidLifecycleTransitionError, VeritasError
from app.core.logging import request_id_var
from app.db.types import utcnow
from app.domain.enums import (
    AnalysisRunState,
    ExaminationAuditAction,
    ExaminationFailureCode,
)
from app.domain.lifecycle import require_transition
from app.domain.models import AnalysisRun, Case, Evidence, EvidenceObject
from app.examination.contracts import (
    FAILURE_MESSAGES,
    ClaimLost,
    ExecutionCancelled,
    ExecutionInterrupted,
    MethodFailure,
)
from app.examination.registry import MethodRegistry
from app.examination.runner import EvidenceReader, RecordedIntegrity, run_method
from app.services import evidence_access, records
from app.services.evidence_storage import EvidenceStorage, EvidenceStorageFailure

logger = logging.getLogger("veritas.examination")

SYSTEM_ACTOR = "system:examination-worker"

_RUNS = cast(Table, AnalysisRun.__table__)
_CLAIM_ATTEMPTS = 5
_RECOVERY_BATCH = 50
_CANCEL_ATTEMPTS = 3


class RunOutcome(StrEnum):
    """How one execution attempt ended, from the worker's point of view."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RELEASED = "released"  # returned to the queue because the worker is shutting down
    LOST = "lost"  # the claim was lost to recovery; nothing was written


class CancelOutcome(StrEnum):
    CANCELLED = "cancelled"  # a queued run was cancelled immediately
    REQUESTED = "requested"  # a running run will be cancelled by its worker


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    """What a worker needs to know about a run it owns. Carries no path and no storage key."""

    run_id: uuid.UUID
    public_id: str
    case_public_id: str
    evidence_public_id: str
    evidence_object_public_id: str
    method_key: str
    method_version: str
    parameters: dict[str, Any]
    claim: datetime  # this claim's started_at: the fencing token


@dataclass(frozen=True, slots=True)
class RunContext:
    """Static identity of a run (never its mutable lifecycle columns, which Core updates change)."""

    run: AnalysisRun
    case: Case
    evidence: Evidence
    item: EvidenceObject


def run_context(session: Session, run_id: uuid.UUID) -> RunContext:
    row = session.execute(
        select(AnalysisRun, Case, Evidence, EvidenceObject)
        .join(Case, Case.id == AnalysisRun.case_id)
        .join(Evidence, Evidence.id == AnalysisRun.evidence_id)
        .join(EvidenceObject, EvidenceObject.id == AnalysisRun.evidence_object_id)
        .where(AnalysisRun.id == run_id)
    ).one()
    return RunContext(row[0], row[1], row[2], row[3])


def audit_details(context: RunContext, state: AnalysisRunState, **extra: Any) -> dict[str, Any]:
    """Metadata only: public identifiers, Method reference, state. Never content or paths."""
    return {
        "case_id": context.case.public_id,
        "evidence_id": context.evidence.public_id,
        "evidence_object_id": context.item.public_id,
        "method_key": context.run.method_key,
        "method_version": context.run.method_version,
        "state": state.value,
        **extra,
    }


def record_run_event(
    session: Session,
    context: RunContext,
    action: ExaminationAuditAction,
    *,
    actor: str,
    state: AnalysisRunState,
    **extra: Any,
) -> None:
    records.record_audit_event(
        session,
        actor=actor,
        action=action.value,
        entity_type="analysis_run",
        entity_public_id=context.run.public_id,
        case=context.case,
        details=audit_details(context, state, **extra),
    )


def transition(
    session: Session,
    run_id: uuid.UUID,
    *,
    expected: AnalysisRunState,
    target: AnalysisRunState,
    values: dict[str, Any],
    fence: datetime | None = None,
    where: Sequence[ColumnElement[bool]] = (),
) -> bool:
    """Compare-and-set one lifecycle transition; ``True`` only if this call changed the row.

    ``domain.lifecycle`` decides first whether the change is permitted at all. The ``WHERE``
    names the exact state the caller observed (and, for a worker, its claim), so concurrent
    claimers, cancellers and recoverers can never both win.
    """
    require_transition(expected, target)
    conditions: list[ColumnElement[bool]] = [_RUNS.c.id == run_id, _RUNS.c.state == expected]
    if fence is not None:
        conditions.append(_RUNS.c.started_at == fence)
    conditions.extend(where)
    result = cast(
        CursorResult[Any],
        session.execute(update(_RUNS).where(*conditions).values(state=target, **values)),
    )
    return result.rowcount == 1


def cancel_run(session: Session, *, run_id: uuid.UUID, actor: str) -> CancelOutcome:
    """Cancel a QUEUED run now, or request cancellation of a RUNNING one. Caller commits.

    A RUNNING run is never forced into CANCELLED here: only its worker finishes it, at the next
    cooperative checkpoint, so a Method is never torn down mid-write. A finished run is final.
    """
    for _ in range(_CANCEL_ATTEMPTS):
        row = session.execute(
            select(_RUNS.c.state, _RUNS.c.cancel_requested_at).where(_RUNS.c.id == run_id)
        ).one()
        state, requested_at = AnalysisRunState(row.state), row.cancel_requested_at
        now = utcnow()
        if state is AnalysisRunState.QUEUED:
            if transition(
                session,
                run_id,
                expected=AnalysisRunState.QUEUED,
                target=AnalysisRunState.CANCELLED,
                values={"completed_at": now, "updated_at": now, "updated_by": actor},
            ):
                record_run_event(
                    session,
                    run_context(session, run_id),
                    ExaminationAuditAction.RUN_CANCELLED,
                    actor=actor,
                    state=AnalysisRunState.CANCELLED,
                    reason="cancelled_before_start",
                )
                return CancelOutcome.CANCELLED
        elif state is AnalysisRunState.RUNNING:
            if requested_at is not None:
                return CancelOutcome.REQUESTED  # already requested: idempotent, no second event
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(_RUNS)
                    .where(
                        _RUNS.c.id == run_id,
                        _RUNS.c.state == AnalysisRunState.RUNNING,
                        _RUNS.c.cancel_requested_at.is_(None),
                    )
                    .values(cancel_requested_at=now, updated_at=now, updated_by=actor)
                ),
            )
            if result.rowcount == 1:
                record_run_event(
                    session,
                    run_context(session, run_id),
                    ExaminationAuditAction.RUN_CANCEL_REQUESTED,
                    actor=actor,
                    state=AnalysisRunState.RUNNING,
                )
                return CancelOutcome.REQUESTED
        else:
            raise InvalidLifecycleTransitionError(
                f"A {state.value} run is final and cannot be cancelled"
            )
    raise InvalidLifecycleTransitionError(
        "The run changed state while the cancellation was being recorded; try again"
    )


class RunCoordinator:
    """Claims, executes and recovers Analysis Runs using only short database transactions."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        storage: Callable[[], EvidenceStorage],
        registry: Callable[[], MethodRegistry],
        heartbeat_seconds: float,
        stale_seconds: float,
        clock: Callable[[], datetime] = utcnow,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = session_factory
        self._storage = storage
        self._registry = registry
        self._heartbeat_seconds = heartbeat_seconds
        self._stale_seconds = stale_seconds
        self._clock = clock
        self._monotonic = monotonic

    # -- claiming ------------------------------------------------------------------------

    def claim_next(self) -> ClaimedRun | None:
        """Atomically take the oldest queued run, or ``None`` if there is none."""
        with self._factory() as session:
            for _ in range(_CLAIM_ATTEMPTS):
                run_id = session.execute(
                    select(_RUNS.c.id)
                    .where(_RUNS.c.state == AnalysisRunState.QUEUED)
                    .order_by(_RUNS.c.created_at, _RUNS.c.public_id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                ).scalar_one_or_none()
                if run_id is None:
                    break
                now = self._clock()
                if not transition(
                    session,
                    run_id,
                    expected=AnalysisRunState.QUEUED,
                    target=AnalysisRunState.RUNNING,
                    values={
                        "started_at": now,
                        "last_heartbeat_at": now,
                        "updated_at": now,
                        "updated_by": SYSTEM_ACTOR,
                    },
                ):
                    session.rollback()  # another worker won this run; look at the next one
                    continue
                context = run_context(session, run_id)
                record_run_event(
                    session,
                    context,
                    ExaminationAuditAction.RUN_STARTED,
                    actor=SYSTEM_ACTOR,
                    state=AnalysisRunState.RUNNING,
                )
                session.commit()
                return ClaimedRun(
                    run_id=run_id,
                    public_id=context.run.public_id,
                    case_public_id=context.case.public_id,
                    evidence_public_id=context.evidence.public_id,
                    evidence_object_public_id=context.item.public_id,
                    method_key=context.run.method_key,
                    method_version=context.run.method_version,
                    parameters=dict(context.run.parameters),
                    claim=now,
                )
            session.rollback()
        return None

    # -- execution -----------------------------------------------------------------------

    def run_next(self, stop: threading.Event | None = None) -> RunOutcome | None:
        claimed = self.claim_next()
        return None if claimed is None else self.execute(claimed, stop)

    def run_pending(self, limit: int = 1_000) -> list[RunOutcome]:
        """Execute queued runs until none is left (used by tests and one-shot tooling)."""
        outcomes: list[RunOutcome] = []
        while len(outcomes) < limit and (outcome := self.run_next()) is not None:
            outcomes.append(outcome)
        return outcomes

    def execute(self, claimed: ClaimedRun, stop: threading.Event | None = None) -> RunOutcome:
        """Execute a run this worker has claimed and record exactly one outcome for it."""
        token = request_id_var.set(f"examination-{claimed.public_id}")
        try:
            try:
                statements = self._examine(claimed, stop)
            except ExecutionCancelled:
                return self._finish_cancelled(claimed)
            except ExecutionInterrupted:
                return self._release(claimed, reason="worker_shutdown")
            except ClaimLost:
                logger.warning(
                    "examination claim lost",
                    extra={"event": "examination_claim_lost", "entity_id": claimed.public_id},
                )
                return RunOutcome.LOST
            except MethodFailure as failure:
                return self._finish_failed(claimed, failure.code)
            except SQLAlchemyError:
                # The database failed under us: record nothing. The heartbeat goes stale and
                # recovery returns the run to the queue once the database is back.
                logger.warning(
                    "examination aborted by a database error",
                    extra={"event": "examination_database_error", "entity_id": claimed.public_id},
                )
                return RunOutcome.LOST
            except Exception as exc:  # unexpected Method error: fail closed, sanitized
                logger.warning(
                    "examination method raised",
                    extra={
                        "event": "examination_method_error",
                        "entity_id": claimed.public_id,
                        "exception_type": type(exc).__name__,
                    },
                )
                return self._finish_failed(claimed, ExaminationFailureCode.EXECUTION_FAILED)
            return self._finish_completed(claimed, statements)
        finally:
            request_id_var.reset(token)

    def _examine(self, claimed: ClaimedRun, stop: threading.Event | None) -> tuple[str, ...]:
        definition = self._registry().get(claimed.method_key, claimed.method_version)
        if definition is None or not definition.enabled:
            raise MethodFailure(ExaminationFailureCode.METHOD_UNAVAILABLE)
        target = self._resolve_target(claimed)
        try:
            stream = self._storage().open_preserved_object(target.storage_key)
        except EvidenceStorageFailure:
            raise MethodFailure(ExaminationFailureCode.EVIDENCE_UNAVAILABLE) from None
        with stream:
            reader = EvidenceReader(
                stream,
                recorded=RecordedIntegrity(target.byte_size, target.sha256, target.sha512),
                limits=definition.limits,
                checkpoint=self._checkpoint(claimed, stop),
                monotonic=self._monotonic,
            )
            return run_method(definition, claimed.parameters, reader)

    def _resolve_target(self, claimed: ClaimedRun) -> evidence_access.PreservedObject:
        """Re-resolve the exact object at execution time with the same rules as eligibility."""
        with self._factory() as session:
            context = run_context(session, claimed.run_id)
            try:
                target = evidence_access.resolve_preserved_object(
                    session,
                    case=context.case,
                    evidence_id=context.evidence.public_id,
                    object_id=context.item.public_id,
                    operation="examined",
                )
            except VeritasError:
                raise MethodFailure(ExaminationFailureCode.NOT_ELIGIBLE) from None
        if target.public_id != claimed.evidence_object_public_id:
            raise MethodFailure(ExaminationFailureCode.NOT_ELIGIBLE)
        return target

    def _checkpoint(self, claimed: ClaimedRun, stop: threading.Event | None) -> Callable[[], None]:
        last = self._monotonic()

        def check() -> None:
            nonlocal last
            if stop is not None and stop.is_set():
                raise ExecutionInterrupted
            now = self._monotonic()
            if now - last < self._heartbeat_seconds:
                return
            last = now
            self._heartbeat(claimed)

        return check

    def _heartbeat(self, claimed: ClaimedRun) -> None:
        """Refresh the heartbeat and observe a cancellation request; fenced by the claim."""
        try:
            with self._factory() as session:
                now = self._clock()
                result = cast(
                    CursorResult[Any],
                    session.execute(
                        update(_RUNS)
                        .where(
                            _RUNS.c.id == claimed.run_id,
                            _RUNS.c.state == AnalysisRunState.RUNNING,
                            _RUNS.c.started_at == claimed.claim,
                        )
                        .values(last_heartbeat_at=now, updated_at=now)
                    ),
                )
                owned = result.rowcount == 1
                requested = (
                    session.execute(
                        select(_RUNS.c.cancel_requested_at).where(_RUNS.c.id == claimed.run_id)
                    ).scalar_one_or_none()
                    if owned
                    else None
                )
                session.commit()
        except SQLAlchemyError:
            raise ClaimLost from None
        if not owned:
            raise ClaimLost
        if requested is not None:
            raise ExecutionCancelled

    # -- finishing -----------------------------------------------------------------------

    def _finish_completed(self, claimed: ClaimedRun, statements: tuple[str, ...]) -> RunOutcome:
        """Complete the run and publish its Observations in ONE transaction."""
        with self._factory() as session:
            now = self._clock()
            if not transition(
                session,
                claimed.run_id,
                expected=AnalysisRunState.RUNNING,
                target=AnalysisRunState.COMPLETED,
                fence=claimed.claim,
                where=[_RUNS.c.cancel_requested_at.is_(None)],
                values={"completed_at": now, "updated_at": now, "updated_by": SYSTEM_ACTOR},
            ):
                session.rollback()
                return self._after_refused_completion(claimed)
            context = run_context(session, claimed.run_id)
            for statement in statements:
                records.record_observation(
                    session,
                    actor=SYSTEM_ACTOR,
                    case=context.case,
                    evidence=context.evidence,
                    statement=statement,
                    analysis_run=context.run,
                )
            record_run_event(
                session,
                context,
                ExaminationAuditAction.RUN_COMPLETED,
                actor=SYSTEM_ACTOR,
                state=AnalysisRunState.COMPLETED,
                observation_count=len(statements),
            )
            session.commit()
        return RunOutcome.COMPLETED

    def _after_refused_completion(self, claimed: ClaimedRun) -> RunOutcome:
        """The completion did not apply: either cancellation won the race, or the claim is gone."""
        with self._factory() as session:
            row = session.execute(
                select(_RUNS.c.state, _RUNS.c.started_at, _RUNS.c.cancel_requested_at).where(
                    _RUNS.c.id == claimed.run_id
                )
            ).one_or_none()
        if (
            row is not None
            and AnalysisRunState(row.state) is AnalysisRunState.RUNNING
            and row.started_at == claimed.claim
            and row.cancel_requested_at is not None
        ):
            return self._finish_cancelled(claimed)
        return RunOutcome.LOST

    def _finish_cancelled(self, claimed: ClaimedRun) -> RunOutcome:
        with self._factory() as session:
            now = self._clock()
            if not transition(
                session,
                claimed.run_id,
                expected=AnalysisRunState.RUNNING,
                target=AnalysisRunState.CANCELLED,
                fence=claimed.claim,
                values={"completed_at": now, "updated_at": now, "updated_by": SYSTEM_ACTOR},
            ):
                session.rollback()
                return RunOutcome.LOST
            record_run_event(
                session,
                run_context(session, claimed.run_id),
                ExaminationAuditAction.RUN_CANCELLED,
                actor=SYSTEM_ACTOR,
                state=AnalysisRunState.CANCELLED,
                reason="cancellation_requested",
            )
            session.commit()
        return RunOutcome.CANCELLED

    def _finish_failed(self, claimed: ClaimedRun, code: ExaminationFailureCode) -> RunOutcome:
        """Fail the run with a fixed, sanitized message. No Observation is ever published."""
        with self._factory() as session:
            now = self._clock()
            if not transition(
                session,
                claimed.run_id,
                expected=AnalysisRunState.RUNNING,
                target=AnalysisRunState.FAILED,
                fence=claimed.claim,
                values={
                    "completed_at": now,
                    "failure_code": code.value,
                    "failure_message": FAILURE_MESSAGES[code],
                    "updated_at": now,
                    "updated_by": SYSTEM_ACTOR,
                },
            ):
                session.rollback()
                return RunOutcome.LOST
            record_run_event(
                session,
                run_context(session, claimed.run_id),
                ExaminationAuditAction.RUN_FAILED,
                actor=SYSTEM_ACTOR,
                state=AnalysisRunState.FAILED,
                failure_code=code.value,
            )
            session.commit()
        return RunOutcome.FAILED

    # -- returning runs to the queue ----------------------------------------------------------

    def _release(self, claimed: ClaimedRun, *, reason: str) -> RunOutcome:
        """Give a run back to the queue because this worker is stopping (fenced by its claim)."""
        with self._factory() as session:
            outcome = self._abandon(
                session,
                claimed.run_id,
                fence=claimed.claim,
                cancel_requested=None,
                reason=reason,
                cutoff=None,
            )
            session.commit()
        if outcome is None:
            return RunOutcome.LOST
        return (
            RunOutcome.CANCELLED if outcome is AnalysisRunState.CANCELLED else RunOutcome.RELEASED
        )

    def recover_stale(self) -> int:
        """Return runs whose worker stopped heartbeating to the queue; ``count`` recovered.

        Only runs whose heartbeat is older than the stale threshold are touched, and the
        compare-and-set re-checks that, so a live worker's run is never taken.
        """
        cutoff = self._clock() - timedelta(seconds=self._stale_seconds)
        recovered = 0
        with self._factory() as session:
            rows = session.execute(
                select(_RUNS.c.id, _RUNS.c.started_at, _RUNS.c.cancel_requested_at)
                .where(
                    _RUNS.c.state == AnalysisRunState.RUNNING,
                    _RUNS.c.last_heartbeat_at < cutoff,
                )
                .order_by(_RUNS.c.created_at)
                .limit(_RECOVERY_BATCH)
                .with_for_update(skip_locked=True)
            ).all()
            for run_id, started_at, requested_at in rows:
                outcome = self._abandon(
                    session,
                    run_id,
                    fence=started_at,
                    cancel_requested=requested_at is not None,
                    reason="stale_heartbeat",
                    cutoff=cutoff,
                )
                recovered += outcome is not None
            session.commit()
        return recovered

    def _abandon(
        self,
        session: Session,
        run_id: uuid.UUID,
        *,
        fence: datetime,
        cancel_requested: bool | None,
        reason: str,
        cutoff: datetime | None,
    ) -> AnalysisRunState | None:
        """RUNNING -> QUEUED, or -> CANCELLED if cancellation was requested. Caller commits."""
        stale: list[ColumnElement[bool]] = (
            [] if cutoff is None else [_RUNS.c.last_heartbeat_at < cutoff]
        )
        now = self._clock()
        if cancel_requested is None:
            cancel_requested = (
                session.execute(
                    select(_RUNS.c.cancel_requested_at).where(_RUNS.c.id == run_id)
                ).scalar_one_or_none()
                is not None
            )
        if not cancel_requested:
            if transition(
                session,
                run_id,
                expected=AnalysisRunState.RUNNING,
                target=AnalysisRunState.QUEUED,
                fence=fence,
                where=[*stale, _RUNS.c.cancel_requested_at.is_(None)],
                values={
                    "started_at": None,
                    "last_heartbeat_at": None,
                    "updated_at": now,
                    "updated_by": SYSTEM_ACTOR,
                },
            ):
                record_run_event(
                    session,
                    run_context(session, run_id),
                    ExaminationAuditAction.RUN_RECOVERED,
                    actor=SYSTEM_ACTOR,
                    state=AnalysisRunState.QUEUED,
                    reason=reason,
                )
                return AnalysisRunState.QUEUED
            # A cancellation request may have landed after we looked: decide again, once.
            cancel_requested = (
                session.execute(
                    select(_RUNS.c.cancel_requested_at).where(_RUNS.c.id == run_id)
                ).scalar_one_or_none()
                is not None
            )
            if not cancel_requested:
                return None
        if transition(
            session,
            run_id,
            expected=AnalysisRunState.RUNNING,
            target=AnalysisRunState.CANCELLED,
            fence=fence,
            where=stale,
            values={"completed_at": now, "updated_at": now, "updated_by": SYSTEM_ACTOR},
        ):
            record_run_event(
                session,
                run_context(session, run_id),
                ExaminationAuditAction.RUN_CANCELLED,
                actor=SYSTEM_ACTOR,
                state=AnalysisRunState.CANCELLED,
                reason=f"{reason}_with_cancellation_requested",
            )
            return AnalysisRunState.CANCELLED
        return None
