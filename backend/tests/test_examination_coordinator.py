"""Worker behavior: claiming, execution outcomes, atomic publication, cancellation, races."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select, text

from app.core.logging import JsonFormatter
from app.domain.enums import ExaminationFailureCode as Failure
from app.domain.models import AnalysisRun, Observation
from app.examination.contracts import FAILURE_MESSAGES, EvidenceInput
from app.examination.coordinator import (
    SYSTEM_ACTOR,
    CancelOutcome,
    RunCoordinator,
    RunOutcome,
    cancel_run,
)
from tests.evidence_support import (
    SECRET_PATH,
    RecordingStorage,
    deterministic_bytes,
    install_recording_storage,
    preserve,
)
from tests.examination_support import (
    METHOD_KEY,
    Examiner,
    case_totals,
    consume_all,
    coordinator_of,
    corrupt_file,
    examiner,
    fresh_coordinator,
    install_methods,
    make_method,
    quiesce_runs,  # noqa: F401  (autouse isolation fixture)
    run_events,
    run_row,
    runs_url,
    start,
)
from tests.test_evidence_intake_api import (
    application,
    evidence_client,  # noqa: F401  (pytest fixture shared with the V2.1 intake tests)
)
from tests.test_examination_methods import statements_for

BODY = deterministic_bytes(200_000, seed=11)  # three full chunks and a partial one


def production_log(caplog: pytest.LogCaptureFixture) -> str:
    """Captured records rendered by the application's JSON formatter (what operators see)."""
    formatter = JsonFormatter()
    return "\n".join(formatter.format(record) for record in caplog.records)


def actions(client: TestClient, run_id: str) -> list[str]:
    return [e.action.removeprefix("examination.run.") for e in run_events(client, run_id)]


def queued_run(
    client: TestClient, *, body: bytes = BODY, method_key: str = METHOD_KEY
) -> tuple[Any, Examiner, dict[str, Any], RecordingStorage]:
    item = preserve(client, body=body)
    who = examiner(client)
    storage = install_recording_storage(client)
    return item, who, start(who, item, method_key=method_key, method_version="1.0"), storage


# --- Claiming ---------------------------------------------------------------------------------


def test_claiming_starts_the_run_and_stamps_the_claim(evidence_client: TestClient) -> None:  # noqa: F811
    _item, _who, run, _storage = queued_run(evidence_client)
    coordinator = fresh_coordinator(evidence_client)
    claimed = coordinator.claim_next()
    assert claimed is not None and claimed.public_id == run["id"]
    row = run_row(evidence_client, run["id"])
    assert row["state"].value == "running"
    assert row["started_at"] == claimed.claim == row["last_heartbeat_at"]
    assert row["completed_at"] is None and row["updated_by"] == SYSTEM_ACTOR
    assert actions(evidence_client, run["id"]) == ["created", "started"]
    started = run_events(evidence_client, run["id"])[-1]
    assert started.actor == SYSTEM_ACTOR and started.details["state"] == "running"
    assert coordinator.claim_next() is None  # nothing else is queued


def test_runs_are_claimed_oldest_first(evidence_client: TestClient) -> None:  # noqa: F811
    item = preserve(evidence_client, body=deterministic_bytes(1_000))
    who = examiner(evidence_client)
    ids = [start(who, item)["id"] for _ in range(4)]
    coordinator = fresh_coordinator(evidence_client)
    claimed = []
    while (run := coordinator.claim_next()) is not None:
        claimed.append(run.public_id)
    assert claimed == ids


def test_concurrent_workers_never_claim_the_same_run(evidence_client: TestClient) -> None:  # noqa: F811
    item = preserve(evidence_client, body=deterministic_bytes(1_000))
    who = examiner(evidence_client)
    ids = [start(who, item)["id"] for _ in range(24)]
    claimed: list[str] = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def worker() -> None:
        coordinator = fresh_coordinator(evidence_client)  # a distinct worker instance
        try:
            while (run := coordinator.claim_next()) is not None:
                with guard:
                    claimed.append(run.public_id)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert errors == []
    assert sorted(claimed) == sorted(ids) and len(set(claimed)) == len(ids)
    for run_id in ids:  # exactly one start event each, never two
        assert actions(evidence_client, run_id).count("started") == 1


def test_two_workers_racing_for_one_run_produce_one_execution(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    _item, who, run, _storage = queued_run(evidence_client, body=deterministic_bytes(70_000))
    outcomes: list[RunOutcome | None] = []

    def worker() -> None:
        outcomes.append(fresh_coordinator(evidence_client).run_next())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert outcomes.count(RunOutcome.COMPLETED) == 1 and outcomes.count(None) == 7
    assert len(who.detail(run["id"])["observations"]) == 5
    assert actions(evidence_client, run["id"]) == ["created", "started", "completed"]


# --- Successful execution ---------------------------------------------------------------------


def test_execution_reads_the_real_bytes_and_publishes_one_atomic_set(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    _item, who, run, storage = queued_run(evidence_client)
    totals = case_totals(evidence_client)
    assert fresh_coordinator(evidence_client).run_pending() == [RunOutcome.COMPLETED]
    detail = who.detail(run["id"])
    assert [o["statement"] for o in detail["observations"]] == list(statements_for(BODY))
    assert case_totals(evidence_client)["observations"] == totals["observations"] + 5
    # The actual stored bytes were read, in bounded chunks, and the handle was closed.
    assert storage.all_closed() and storage.read_sizes
    assert all(size == 65_536 for size in storage.read_sizes if size is not None)
    assert len(storage.read_sizes) == 5  # four chunks and the end-of-object read
    assert actions(evidence_client, run["id"]) == ["created", "started", "completed"]
    completed = run_events(evidence_client, run["id"])[-1]
    assert completed.details["observation_count"] == 5 and completed.actor == SYSTEM_ACTOR


def test_a_failure_while_publishing_leaves_no_observation_and_no_completion(
    evidence_client: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publication is one transaction: all of it, or none of it."""
    _item, who, run, _storage = queued_run(evidence_client, body=deterministic_bytes(70_000))
    totals = case_totals(evidence_client)
    from app.services import records

    real = records.record_observation
    calls = 0

    def flaky(*args: Any, **kwargs: Any) -> Observation:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("synthetic failure while publishing the third observation")
        return real(*args, **kwargs)

    monkeypatch.setattr(records, "record_observation", flaky)
    coordinator = fresh_coordinator(evidence_client)
    claimed = coordinator.claim_next()
    assert claimed is not None
    with pytest.raises(RuntimeError, match="third observation"):
        coordinator.execute(claimed)

    assert case_totals(evidence_client)["observations"] == totals["observations"]  # none at all
    row = run_row(evidence_client, run["id"])
    assert row["state"].value == "running" and row["completed_at"] is None  # not "completed"
    assert actions(evidence_client, run["id"]) == ["created", "started"]  # no completion event
    assert who.detail(run["id"])["observations"] == []


def test_a_failure_writing_the_completion_event_publishes_nothing(
    evidence_client: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _item, who, run, _storage = queued_run(evidence_client, body=deterministic_bytes(70_000))
    totals = case_totals(evidence_client)
    from app.examination import coordinator as module

    real = module.record_run_event

    def failing(session: Any, context: Any, action: Any, **kwargs: Any) -> None:
        if action.value == "examination.run.completed":
            raise RuntimeError("synthetic audit failure")
        real(session, context, action, **kwargs)

    monkeypatch.setattr(module, "record_run_event", failing)
    coordinator = fresh_coordinator(evidence_client)
    claimed = coordinator.claim_next()
    assert claimed is not None
    with pytest.raises(RuntimeError, match="audit failure"):
        coordinator.execute(claimed)
    assert case_totals(evidence_client) == totals
    assert run_row(evidence_client, run["id"])["state"].value == "running"
    assert who.detail(run["id"])["observations"] == []


# --- Failures: distinct, sanitized, and never a false completion ----------------------------------


def assert_failed(
    client: TestClient, who: Examiner, run_id: str, code: Failure, totals: dict[str, int]
) -> dict[str, Any]:
    detail = who.detail(run_id)
    assert detail["state"] == "failed"
    assert detail["failure_code"] == code.value
    assert detail["failure_message"] == FAILURE_MESSAGES[code]
    assert detail["observations"] == []  # nothing that could look like a completed result
    assert detail["completed_at"] and detail["started_at"]
    assert case_totals(client)["observations"] == totals["observations"]
    assert actions(client, run_id) == ["created", "started", "failed"]
    failed = run_events(client, run_id)[-1]
    assert failed.actor == SYSTEM_ACTOR
    assert failed.details["failure_code"] == code.value and failed.details["state"] == "failed"
    return detail


FAULTS: dict[str, tuple[Callable[[TestClient, Any, RecordingStorage], None], Failure]] = {
    "missing": (lambda c, i, s: corrupt_file(c, i, "missing"), Failure.EVIDENCE_UNAVAILABLE),
    "read-error": (lambda c, i, s: setattr(s, "fail_on_read", 2), Failure.EVIDENCE_UNAVAILABLE),
    "shrinks-while-reading": (
        lambda c, i, s: setattr(s, "eof_after_bytes", 70_000),
        Failure.INTEGRITY_MISMATCH,
    ),
    "modified": (lambda c, i, s: corrupt_file(c, i, "modified"), Failure.INTEGRITY_MISMATCH),
    "truncated": (lambda c, i, s: corrupt_file(c, i, "truncated"), Failure.INTEGRITY_MISMATCH),
    "expanded": (lambda c, i, s: corrupt_file(c, i, "expanded"), Failure.INTEGRITY_MISMATCH),
    "replaced": (lambda c, i, s: corrupt_file(c, i, "replaced"), Failure.INTEGRITY_MISMATCH),
}


@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_storage_faults_fail_the_run_with_a_distinct_sanitized_code(
    evidence_client: TestClient,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    fault: str,
) -> None:
    item, who, run, storage = queued_run(evidence_client)
    apply, code = FAULTS[fault]
    apply(evidence_client, item, storage)  # the fault happens AFTER the run was queued
    totals = case_totals(evidence_client)
    with caplog.at_level("DEBUG"):
        assert fresh_coordinator(evidence_client).run_pending() == [RunOutcome.FAILED]
    detail = assert_failed(evidence_client, who, run["id"], code, totals)

    everything = json.dumps(detail) + json.dumps(
        [e.details for e in run_events(evidence_client, run["id"])], default=str
    )
    root = str(application(evidence_client).state.settings.evidence_storage_root)
    logged = production_log(caplog)
    for secret in (SECRET_PATH, item.storage_key, root):
        assert secret not in everything and secret not in logged
    assert "Traceback" not in logged
    assert not storage.streams or storage.all_closed()  # a handle was never left open


def raising_method(message: str) -> Callable[[EvidenceInput, BaseModel], tuple[str, ...]]:
    def execute(source: EvidenceInput, _parameters: BaseModel) -> tuple[str, ...]:
        consume_all(source)
        raise RuntimeError(message)

    return execute


def test_an_unexpected_method_error_fails_closed_without_leaking_its_text(
    evidence_client: TestClient,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(10_000))
    install_methods(
        evidence_client,
        make_method(raising_method(f"boom while reading {SECRET_PATH}"), key="test.raises"),
    )
    who = examiner(evidence_client)
    run = start(who, item, method_key="test.raises")
    totals = case_totals(evidence_client)
    with caplog.at_level("DEBUG"):
        assert fresh_coordinator(evidence_client).run_pending() == [RunOutcome.FAILED]
    detail = assert_failed(evidence_client, who, run["id"], Failure.EXECUTION_FAILED, totals)
    logged = production_log(caplog)
    assert "boom" not in json.dumps(detail) and "boom" not in logged and SECRET_PATH not in logged
    assert '"exception_type": "RuntimeError"' in logged  # only the TYPE is logged, for operators


@pytest.mark.parametrize(
    "statements",
    [
        ("Observed byte count: 1.", "Observed byte count: 2."),  # more than the contract declares
        ("This file is authentic.",),  # a conclusion instead of a measurement
        ("",),
    ],
)
def test_output_that_breaks_the_method_contract_is_never_published(
    evidence_client: TestClient,  # noqa: F811
    statements: tuple[str, ...],
) -> None:
    def execute(source: EvidenceInput, _parameters: BaseModel) -> tuple[str, ...]:
        consume_all(source)
        return statements

    item = preserve(evidence_client, body=deterministic_bytes(10_000))
    install_methods(evidence_client, make_method(execute, key="test.bad_output"))
    who = examiner(evidence_client)
    run = start(who, item, method_key="test.bad_output")
    totals = case_totals(evidence_client)
    fresh_coordinator(evidence_client).run_pending()
    assert_failed(evidence_client, who, run["id"], Failure.EXECUTION_FAILED, totals)


def test_a_method_that_is_gone_when_the_run_starts_fails_as_unavailable(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(10_000))
    install_methods(evidence_client, make_method(key="test.temporary"))
    who = examiner(evidence_client)
    run = start(who, item, method_key="test.temporary")
    install_methods(evidence_client)  # the version is no longer deployed
    totals = case_totals(evidence_client)
    fresh_coordinator(evidence_client).run_pending()
    assert_failed(evidence_client, who, run["id"], Failure.METHOD_UNAVAILABLE, totals)


def test_a_disabled_method_does_not_run_even_if_it_was_queued_while_enabled(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(10_000))
    install_methods(evidence_client, make_method(key="test.toggle"))
    who = examiner(evidence_client)
    run = start(who, item, method_key="test.toggle")
    install_methods(evidence_client, make_method(key="test.toggle", enabled=False))
    totals = case_totals(evidence_client)
    fresh_coordinator(evidence_client).run_pending()
    assert_failed(evidence_client, who, run["id"], Failure.METHOD_UNAVAILABLE, totals)


def test_a_case_that_became_a_demonstration_case_is_not_examined(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item, who, run, _storage = queued_run(evidence_client, body=deterministic_bytes(10_000))
    factory = application(evidence_client).state.session_factory
    flip = text("UPDATE cases SET is_demonstration = :flag WHERE public_id = :id")
    totals = case_totals(evidence_client)
    try:
        with factory() as session:
            session.execute(flip, {"flag": True, "id": item.case_id})
            session.commit()
        fresh_coordinator(evidence_client).run_pending()
    finally:  # always restore the shared Case
        with factory() as session:
            session.execute(flip, {"flag": False, "id": item.case_id})
            session.commit()
    assert_failed(evidence_client, who, run["id"], Failure.NOT_ELIGIBLE, totals)


def test_an_object_over_the_method_limit_at_run_time_fails_with_a_resource_code(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(100_000))
    install_methods(evidence_client, make_method(key="test.limit", max_object_bytes=1_000_000))
    who = examiner(evidence_client)
    run = start(who, item, method_key="test.limit")
    # The limit is lowered after queueing (a new deployment): the run must fail closed.
    install_methods(evidence_client, make_method(key="test.limit", max_object_bytes=60_000))
    totals = case_totals(evidence_client)
    fresh_coordinator(evidence_client).run_pending()
    assert_failed(evidence_client, who, run["id"], Failure.RESOURCE_LIMIT_EXCEEDED, totals)


def test_a_database_error_during_execution_records_nothing_and_leaves_the_run_recoverable(
    evidence_client: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.exc import OperationalError

    _item, _who, run, _storage = queued_run(evidence_client, body=deterministic_bytes(10_000))
    coordinator = fresh_coordinator(evidence_client)

    def database_down(self: RunCoordinator, claimed: Any) -> Any:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(RunCoordinator, "_resolve_target", database_down)
    assert coordinator.run_next() is RunOutcome.LOST
    row = run_row(evidence_client, run["id"])
    assert row["state"].value == "running" and row["failure_code"] is None
    assert actions(evidence_client, run["id"]) == ["created", "started"]


# --- Cancellation ---------------------------------------------------------------------------------


def cancel_in_a_second_session(client: TestClient, run_id: str, actor: str) -> CancelOutcome:
    internal = run_row(client, run_id)["id"]
    with application(client).state.session_factory() as session:
        outcome = cancel_run(session, run_id=internal, actor=actor)
        session.commit()
    return outcome


def test_cancelling_a_running_run_stops_it_and_publishes_nothing(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    _item, who, run, storage = queued_run(evidence_client)
    reads = 0

    def cancel_during_the_second_read() -> None:
        nonlocal reads
        reads += 1
        if reads == 3:  # open, first read, second read
            assert (
                cancel_in_a_second_session(
                    evidence_client, run["id"], who.detail(run["id"])["created_by"]
                )
                is CancelOutcome.REQUESTED
            )

    storage.on_io = cancel_during_the_second_read
    totals = case_totals(evidence_client)
    assert fresh_coordinator(evidence_client).run_pending() == [RunOutcome.CANCELLED]

    detail = who.detail(run["id"])
    assert detail["state"] == "cancelled" and detail["observations"] == []
    assert detail["cancel_requested_at"] and detail["completed_at"] and detail["started_at"]
    assert detail["failure_code"] is None
    assert case_totals(evidence_client)["observations"] == totals["observations"]
    assert actions(evidence_client, run["id"]) == [
        "created",
        "started",
        "cancel_requested",
        "cancelled",
    ]
    requested, cancelled = run_events(evidence_client, run["id"])[2:]
    assert requested.actor == detail["created_by"] and cancelled.actor == SYSTEM_ACTOR
    assert storage.all_closed()
    assert len(storage.read_sizes) < 5  # the object was not read to the end


def test_a_cancellation_request_is_idempotent(evidence_client: TestClient) -> None:  # noqa: F811
    item, who, run, storage = queued_run(evidence_client)
    del item, storage
    claimed = fresh_coordinator(evidence_client).claim_next()
    assert claimed is not None
    actor = who.detail(run["id"])["created_by"]
    first = cancel_in_a_second_session(evidence_client, run["id"], actor)
    second = cancel_in_a_second_session(evidence_client, run["id"], actor)
    assert first is second is CancelOutcome.REQUESTED
    assert actions(evidence_client, run["id"]).count("cancel_requested") == 1


def test_a_cancel_that_arrives_before_the_completion_commit_wins_and_nothing_is_published(
    evidence_client: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All work is done but the cancel request committed first: CANCELLED, never COMPLETED."""
    _item, who, run, _storage = queued_run(evidence_client, body=deterministic_bytes(70_000))
    coordinator = fresh_coordinator(evidence_client, heartbeat_seconds=3600)  # no checkpoints
    original = coordinator._finish_completed

    def cancel_then_complete(claimed: Any, statements: Any) -> RunOutcome:
        cancel_in_a_second_session(evidence_client, run["id"], "USR-001")
        return original(claimed, statements)

    monkeypatch.setattr(coordinator, "_finish_completed", cancel_then_complete)
    totals = case_totals(evidence_client)
    assert coordinator.run_pending() == [RunOutcome.CANCELLED]
    detail = who.detail(run["id"])
    assert detail["state"] == "cancelled" and detail["observations"] == []
    assert case_totals(evidence_client)["observations"] == totals["observations"]
    assert "completed" not in actions(evidence_client, run["id"])


def test_a_failure_that_commits_while_a_cancellation_is_pending_is_recorded_as_a_failure(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    """The failure is real, so it is not hidden behind the cancellation request."""
    item, who, run, storage = queued_run(evidence_client)
    corrupt_file(evidence_client, item, "modified")  # same size: only the final comparison fails
    owner = who.detail(run["id"])["created_by"]
    calls = 0

    def request_cancellation_at_the_final_read() -> None:
        nonlocal calls
        calls += 1
        if (
            calls == 6
        ):  # open, four chunk reads, then the read that finds the end: past the last checkpoint
            cancel_in_a_second_session(evidence_client, run["id"], owner)

    storage.on_io = request_cancellation_at_the_final_read
    assert fresh_coordinator(evidence_client).run_pending() == [RunOutcome.FAILED]
    detail = who.detail(run["id"])
    assert detail["state"] == "failed" and detail["failure_code"] == "integrity_mismatch"
    assert detail["cancel_requested_at"] is not None  # the request is kept in the record
    assert detail["observations"] == []
    assert actions(evidence_client, run["id"]) == [
        "created",
        "started",
        "cancel_requested",
        "failed",
    ]


def test_a_completed_run_cannot_be_cancelled_afterwards(evidence_client: TestClient) -> None:  # noqa: F811
    from app.core.errors import InvalidLifecycleTransitionError

    _item, who, run, _storage = queued_run(evidence_client, body=deterministic_bytes(70_000))
    coordinator_of(evidence_client).run_pending()
    snapshot = run_row(evidence_client, run["id"])
    with pytest.raises(InvalidLifecycleTransitionError):
        cancel_in_a_second_session(evidence_client, run["id"], "USR-001")
    assert run_row(evidence_client, run["id"]) == snapshot
    assert who.detail(run["id"])["state"] == "completed"


def test_cancel_racing_the_worker_never_yields_an_inconsistent_run(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(150_000, seed=21))
    who = examiner(evidence_client)
    ids = [start(who, item)["id"] for _ in range(14)]
    coordinator = fresh_coordinator(evidence_client)
    statuses: list[int] = []

    def worker() -> None:
        coordinator.run_pending()

    def canceller() -> None:
        for run_id in ids:
            statuses.append(who.post(runs_url(f"/{run_id}/cancel")).status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [pool.submit(worker), pool.submit(canceller)]:
            future.result(timeout=120)
    coordinator.run_pending()  # anything still queued or running is finished or cancelled now
    assert set(statuses) <= {200, 202, 409}

    completed = cancelled = 0
    for run_id in ids:
        detail = who.detail(run_id)
        terminal = [
            a for a in actions(evidence_client, run_id) if a in {"completed", "cancelled", "failed"}
        ]
        assert len(terminal) == 1, (run_id, terminal)  # exactly one outcome per run
        assert detail["state"] == terminal[0]
        if detail["state"] == "completed":
            completed += 1
            assert len(detail["observations"]) == 5 and detail["cancel_requested_at"] is None
        else:
            cancelled += 1
            assert detail["state"] == "cancelled" and detail["observations"] == []
    assert completed + cancelled == len(ids)


def test_retry_is_refused_while_the_source_is_still_running_then_accepted_once_it_failed(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    _item, who, run, storage = queued_run(evidence_client)
    storage.fail_on_read = 3  # the run will fail on its third read...
    blocked, release = threading.Event(), threading.Event()
    reads = 0

    def hold_at_the_second_read() -> None:
        nonlocal reads
        reads += 1
        if reads == 2:  # ...but is held RUNNING first
            blocked.set()
            release.wait(30)

    storage.on_io = hold_at_the_second_read
    coordinator = fresh_coordinator(evidence_client)
    outcome: list[RunOutcome | None] = []
    thread = threading.Thread(target=lambda: outcome.append(coordinator.run_next()))
    thread.start()
    assert blocked.wait(30)
    assert who.detail(run["id"])["state"] == "running"
    from tests.examination_support import error_of

    error_of(
        who.post(runs_url(f"/{run['id']}/retry"), {"idempotency_key": "retry-too-early-01"}),
        409,
        "invalid_lifecycle_transition",
    )
    release.set()
    thread.join(60)
    assert outcome == [RunOutcome.FAILED] and who.detail(run["id"])["state"] == "failed"

    storage.fail_on_read, storage.on_io = None, None
    retried = who.post(runs_url(f"/{run['id']}/retry"), {"idempotency_key": "retry-after-fail-01"})
    assert retried.status_code == 201 and retried.json()["id"] != run["id"]


def test_every_run_publishes_observations_only_through_its_own_run(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(5_000))
    who = examiner(evidence_client)
    first, second = start(who, item), start(who, item)
    coordinator_of(evidence_client).run_pending()
    with application(evidence_client).state.session_factory() as session:
        rows = session.execute(
            select(Observation, AnalysisRun.public_id)
            .join(AnalysisRun, AnalysisRun.id == Observation.analysis_run_id)
            .where(AnalysisRun.public_id.in_([first["id"], second["id"]]))
        ).all()
    by_run: dict[str, int] = {}
    for observation, run_public_id in rows:
        assert observation.origin.value == "analysis_run"
        assert observation.created_by == SYSTEM_ACTOR
        by_run[run_public_id] = by_run.get(run_public_id, 0) + 1
    assert by_run == {first["id"]: 5, second["id"]: 5}
