"""Analysis Run lifecycle rules and database integrity (ORM guard, constraints, PG trigger)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from itertools import count
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db.types import utcnow
from app.domain.enums import (
    AnalysisRunState,
    EvidenceObjectState,
    EvidenceValidationStatus,
)
from app.domain.lifecycle import (
    RETRYABLE_RUN_STATES,
    RUN_TRANSITIONS,
    TERMINAL_RUN_STATES,
    InvalidRunTransitionError,
    is_valid_transition,
    require_transition,
)
from app.domain.models import (
    AnalysisRun,
    AnalysisRunImmutableError,
    Case,
    Evidence,
    EvidenceObject,
)
from app.examination.coordinator import transition

S = AnalysisRunState
_sequence = count(1)


# --- The lifecycle table ------------------------------------------------------------------------


def test_the_transition_table_is_exactly_the_documented_lifecycle() -> None:
    assert {
        S.QUEUED: {S.RUNNING, S.CANCELLED},
        S.RUNNING: {S.COMPLETED, S.FAILED, S.CANCELLED, S.QUEUED},
        S.COMPLETED: set(),
        S.FAILED: set(),
        S.CANCELLED: set(),
    } == RUN_TRANSITIONS
    assert {S.COMPLETED, S.FAILED, S.CANCELLED} == TERMINAL_RUN_STATES
    assert {S.FAILED, S.CANCELLED} == RETRYABLE_RUN_STATES  # never queued, running or completed
    assert set(RUN_TRANSITIONS) == set(AnalysisRunState)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (current, target)
        for current in AnalysisRunState
        for target in AnalysisRunState
        if target not in RUN_TRANSITIONS[current]
    ],
)
def test_every_transition_outside_the_table_is_refused(
    current: AnalysisRunState, target: AnalysisRunState
) -> None:
    assert not is_valid_transition(current, target)
    with pytest.raises(InvalidRunTransitionError):
        require_transition(current, target)


def test_a_finished_run_can_never_change_state() -> None:
    for finished in TERMINAL_RUN_STATES:
        for target in AnalysisRunState:
            assert not is_valid_transition(finished, target)


def test_the_coordinator_asks_the_lifecycle_before_it_writes(db: Session) -> None:
    """``transition`` raises before issuing any SQL for an impossible change."""
    with pytest.raises(InvalidRunTransitionError):
        transition(
            db,
            uuid4(),
            expected=S.COMPLETED,
            target=S.RUNNING,
            values={},
        )


# --- Fixtures --------------------------------------------------------------------------------


@pytest.fixture
def parents(db: Session) -> Iterator[tuple[Case, Evidence, Evidence]]:
    """Two Evidence items of one Case (the seeded demonstration Case; rolled back)."""
    case = db.execute(select(Case).where(Case.public_id == "CASE-001")).scalar_one()
    first, second = (
        db.execute(select(Evidence).where(Evidence.case_id == case.id).order_by(Evidence.public_id))
        .scalars()
        .all()[:2]
    )
    yield case, first, second


def make_object(
    db: Session,
    case: Case,
    evidence: Evidence,
    *,
    state: EvidenceObjectState = EvidenceObjectState.PRESERVED,
) -> EvidenceObject:
    now = utcnow()
    item = EvidenceObject(
        case_id=case.id,
        organization_id=case.organization_id,
        evidence_id=evidence.id,
        storage_key=uuid4().hex,
        original_filename="synthetic.bin",
        declared_media_type="application/octet-stream",
        detected_media_type="application/pdf",
        byte_size=10,
        sha256="a" * 64,
        sha512="b" * 128,
        state=state,
        validation_status=EvidenceValidationStatus.ACCEPTED,
        acquired_at=now,
        acquired_by="test:lifecycle",
        upload_completed_at=now,
        preserved_at=now,
        preserved_by="test:lifecycle",
        created_by="test:lifecycle",
        updated_by="test:lifecycle",
    )
    db.add(item)
    db.flush()
    return item


def make_run(
    db: Session, case: Case, evidence: Evidence, item: EvidenceObject, **overrides: Any
) -> AnalysisRun:
    values: dict[str, Any] = {
        "case_id": case.id,
        "evidence_id": evidence.id,
        "evidence_object_id": item.id,
        "method_key": "core.binary_characteristics",
        "method_version": "1.0",
        "state": S.QUEUED,
        "parameters": {},
        "idempotency_key": f"lifecycle-key-{next(_sequence)}",
        "request_fingerprint": "f" * 64,
        "created_by": "test:lifecycle",
        "updated_by": "test:lifecycle",
    }
    values.update(overrides)
    run = AnalysisRun(**values)
    db.add(run)
    db.flush()
    return run


def refused(db: Session, build: Any) -> None:
    """The database must refuse ``build``; a savepoint keeps the session usable afterwards."""
    with pytest.raises((IntegrityError, DBAPIError)), db.begin_nested():
        build()


# --- Exact provenance, enforced by the database ---------------------------------------------


def test_a_run_for_a_matching_object_is_accepted(
    db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, _other = parents
    item = make_object(db, case, evidence)
    run = make_run(db, case, evidence, item)
    assert run.public_id.startswith("ANL-") and run.state is S.QUEUED


def test_a_run_cannot_name_an_object_of_another_evidence_item(
    db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, other_evidence = parents
    item = make_object(db, case, evidence)
    refused(db, lambda: make_run(db, case, other_evidence, item))


def test_a_run_cannot_name_an_object_of_another_case(
    db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, _other = parents
    other_case = db.execute(select(Case).where(Case.public_id == "CASE-002")).scalar_one()
    item = make_object(db, case, evidence)
    refused(db, lambda: make_run(db, other_case, evidence, item))


def test_a_run_must_name_an_existing_object(
    db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, _other = parents
    ghost = EvidenceObject(id=uuid4())
    refused(db, lambda: make_run(db, case, evidence, ghost))


def test_an_object_that_has_runs_cannot_be_deleted_from_the_database(
    db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, _other = parents
    item = make_object(db, case, evidence)
    make_run(db, case, evidence, item)
    # A bulk DELETE bypasses the ORM guard, so this proves the foreign key itself.
    refused(db, lambda: db.execute(delete(EvidenceObject).where(EvidenceObject.id == item.id)))


# --- Idempotency uniqueness -------------------------------------------------------------------


def test_one_key_for_one_principal_and_case_exists_once(
    db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, _other = parents
    item = make_object(db, case, evidence)
    make_run(db, case, evidence, item, idempotency_key="shared-key-0001")
    refused(db, lambda: make_run(db, case, evidence, item, idempotency_key="shared-key-0001"))
    # Another principal may use the same key text; it is a different request context.
    other = make_run(
        db, case, evidence, item, idempotency_key="shared-key-0001", created_by="test:other"
    )
    assert other.created_by == "test:other"


def test_internal_runs_without_a_key_never_collide(
    db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, _other = parents
    item = make_object(db, case, evidence)
    make_run(db, case, evidence, item, idempotency_key=None)
    make_run(db, case, evidence, item, idempotency_key=None)


# --- CHECK constraints -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"started_at": utcnow()},  # queued with a start
        {"completed_at": utcnow()},  # queued with an end
        {"last_heartbeat_at": utcnow()},  # queued with a heartbeat
        {"cancel_requested_at": utcnow()},  # queued with a cancellation request
        {"state": S.RUNNING},  # running with no start or heartbeat
        {"state": S.RUNNING, "started_at": utcnow()},  # running with no heartbeat
        {
            "state": S.RUNNING,
            "started_at": utcnow(),
            "last_heartbeat_at": utcnow(),
            "completed_at": utcnow(),
        },
        {"state": S.COMPLETED, "started_at": utcnow()},  # completed with no end
        {
            "state": S.COMPLETED,
            "started_at": utcnow(),
            "completed_at": utcnow(),
            "cancel_requested_at": utcnow(),
        },
        {"state": S.FAILED, "started_at": utcnow(), "completed_at": utcnow()},  # no failure code
        {
            "state": S.FAILED,
            "started_at": utcnow(),
            "completed_at": utcnow(),
            "failure_code": "execution_failed",
        },
        {"failure_code": "execution_failed", "failure_message": "x"},  # failure on a queued run
        {"state": S.CANCELLED},  # cancelled with no end
        {"request_fingerprint": "short"},
    ],
)
def test_inconsistent_lifecycle_rows_are_refused(
    db: Session, parents: tuple[Case, Evidence, Evidence], overrides: dict[str, Any]
) -> None:
    case, evidence, _other = parents
    item = make_object(db, case, evidence)
    refused(db, lambda: make_run(db, case, evidence, item, **overrides))


def test_every_consistent_state_is_accepted(
    db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, _other = parents
    item = make_object(db, case, evidence)
    now = utcnow()
    make_run(db, case, evidence, item)
    make_run(db, case, evidence, item, state=S.RUNNING, started_at=now, last_heartbeat_at=now)
    make_run(db, case, evidence, item, state=S.COMPLETED, started_at=now, completed_at=now)
    make_run(
        db, case, evidence, item, state=S.FAILED, started_at=now, completed_at=now,
        failure_code="execution_failed", failure_message="The Method did not complete.",
    )  # fmt: skip
    make_run(db, case, evidence, item, state=S.CANCELLED, completed_at=now)  # cancelled in queue
    make_run(
        db, case, evidence, item, state=S.CANCELLED, started_at=now, completed_at=now,
        cancel_requested_at=now,
    )  # fmt: skip


# --- ORM guard (every database) ----------------------------------------------------------------


def test_what_a_run_was_asked_to_do_is_immutable(
    db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, other = parents
    item = make_object(db, case, evidence)
    other_item = make_object(db, case, other)
    run = make_run(db, case, evidence, item)
    for attribute, value in [
        ("method_version", "9.9"),
        ("method_key", "core.other"),
        ("parameters", {"changed": True}),
        ("evidence_id", other.id),
        ("evidence_object_id", other_item.id),
        ("idempotency_key", "rewritten-key-1"),
        ("request_fingerprint", "e" * 64),
        ("created_by", "someone-else"),
    ]:
        original = getattr(run, attribute)
        with pytest.raises(AnalysisRunImmutableError, match="immutable"), db.begin_nested():
            setattr(run, attribute, value)
            db.flush()
        assert getattr(run, attribute) == original


def test_the_orm_refuses_state_changes_outside_the_lifecycle(
    db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, _other = parents
    item = make_object(db, case, evidence)
    run = make_run(db, case, evidence, item)
    with pytest.raises(AnalysisRunImmutableError, match="cannot change"), db.begin_nested():
        run.state = S.COMPLETED  # queued -> completed skips running
        db.flush()
    assert run.state is S.QUEUED


def test_a_finished_run_is_immutable_through_the_orm(
    db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, _other = parents
    item = make_object(db, case, evidence)
    now = utcnow()
    run = make_run(db, case, evidence, item, state=S.COMPLETED, started_at=now, completed_at=now)
    with pytest.raises(AnalysisRunImmutableError, match="finished"), db.begin_nested():
        run.updated_by = "rewriter"
        db.flush()
    assert run.updated_by == "test:lifecycle"


def test_runs_cannot_be_deleted_through_the_orm(
    db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, _other = parents
    item = make_object(db, case, evidence)
    run = make_run(db, case, evidence, item)
    with pytest.raises(AnalysisRunImmutableError, match="cannot be deleted"), db.begin_nested():
        db.delete(run)
        db.flush()


def test_a_valid_progression_is_accepted_through_the_orm(
    db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, _other = parents
    item = make_object(db, case, evidence)
    run = make_run(db, case, evidence, item)
    now = utcnow()
    run.state, run.started_at, run.last_heartbeat_at = S.RUNNING, now, now
    db.flush()
    run.state, run.completed_at = S.COMPLETED, now + timedelta(seconds=1)
    db.flush()
    assert run.state is S.COMPLETED


# --- PostgreSQL trigger (the database refuses even raw SQL) ----------------------------------


@pytest.fixture
def postgres_only(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("the analysis_runs guard trigger exists on PostgreSQL only (SQLite: ORM guard)")


def _raw_update(db: Session, run: AnalysisRun, assignments: str, **params: Any) -> None:
    statement = text(
        f"UPDATE analysis_runs SET {assignments} WHERE id = :id"  # noqa: S608 - test constants
    )
    db.execute(statement, {"id": run.id, **params})


def test_postgres_refuses_deleting_a_run(
    postgres_only: None, db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, _other = parents
    run = make_run(db, case, evidence, make_object(db, case, evidence))
    with pytest.raises(DBAPIError, match="cannot be deleted"), db.begin_nested():
        db.execute(text("DELETE FROM analysis_runs WHERE id = :id"), {"id": run.id})


@pytest.mark.parametrize(
    "assignments",
    [
        "method_version = '9.9'",
        "method_key = 'core.other'",
        "parameters = '{\"x\": 1}'::jsonb",
        "idempotency_key = 'rewritten-key-9'",
        "request_fingerprint = repeat('e', 64)",
        "created_by = 'rewriter'",
        "public_id = 'ANL-9999'",
    ],
)
def test_postgres_refuses_rewriting_what_a_run_was_asked_to_do(
    postgres_only: None,
    db: Session,
    parents: tuple[Case, Evidence, Evidence],
    assignments: str,
) -> None:
    case, evidence, _other = parents
    run = make_run(db, case, evidence, make_object(db, case, evidence))
    with pytest.raises(DBAPIError, match="immutable"), db.begin_nested():
        _raw_update(db, run, assignments)


def test_postgres_refuses_invalid_transitions_and_any_change_to_a_finished_run(
    postgres_only: None, db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, _other = parents
    item = make_object(db, case, evidence)
    queued = make_run(db, case, evidence, item)
    # queued -> completed is not in the lifecycle (it would also violate a CHECK; the trigger
    # is what names the transition rule).
    with pytest.raises(DBAPIError, match="lifecycle transition"), db.begin_nested():
        _raw_update(db, queued, "state = 'completed', started_at = now(), completed_at = now()")
    now = utcnow()
    finished = make_run(
        db, case, evidence, item, state=S.COMPLETED, started_at=now, completed_at=now
    )
    for assignments in ("updated_by = 'rewriter'", "state = 'running'", "state = 'queued'"):
        with pytest.raises(DBAPIError, match="immutable"), db.begin_nested():
            _raw_update(db, finished, assignments)


def test_postgres_allows_the_documented_transitions(
    postgres_only: None, db: Session, parents: tuple[Case, Evidence, Evidence]
) -> None:
    case, evidence, _other = parents
    run = make_run(db, case, evidence, make_object(db, case, evidence))
    _raw_update(db, run, "state = 'running', started_at = now(), last_heartbeat_at = now()")
    _raw_update(db, run, "last_heartbeat_at = now()")  # a heartbeat is not a state change
    _raw_update(
        db, run, "state = 'queued', started_at = NULL, last_heartbeat_at = NULL"
    )  # recovery
    _raw_update(db, run, "state = 'running', started_at = now(), last_heartbeat_at = now()")
    _raw_update(db, run, "state = 'completed', completed_at = now()")
    state: str = db.execute(
        text("SELECT state FROM analysis_runs WHERE id = :id"), {"id": run.id}
    ).scalar_one()
    assert state == "completed"
