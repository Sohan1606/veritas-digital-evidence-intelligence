"""Durability: heartbeat, stale recovery, fencing, graceful release, restart, background worker."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, update

from app.core.config import Settings
from app.db.types import utcnow
from app.domain.models import AnalysisRun, Case, Organization
from app.examination.coordinator import SYSTEM_ACTOR, RunOutcome
from app.main import create_app
from app.services.evidence_storage import LocalEvidenceStorage
from tests.conftest import make_settings
from tests.evidence_support import (
    OPERATIONAL_CASE_ID,
    RecordingStorage,
    deterministic_bytes,
    install_recording_storage,
    preserve,
)
from tests.examination_support import (
    Examiner,
    FakeClock,
    case_totals,
    examiner,
    fresh_coordinator,
    quiesce_runs,  # noqa: F401  (autouse isolation fixture)
    run_events,
    run_row,
    start,
)
from tests.test_evidence_intake_api import (
    application,
    evidence_client,  # noqa: F401  (pytest fixture shared with the V2.1 intake tests)
)

BODY = deterministic_bytes(200_000, seed=31)


def actions(client: TestClient, run_id: str) -> list[str]:
    return [e.action.removeprefix("examination.run.") for e in run_events(client, run_id)]


def queued(
    client: TestClient, body: bytes = BODY
) -> tuple[Examiner, dict[str, Any], RecordingStorage]:
    item = preserve(client, body=body)
    who = examiner(client)
    storage = install_recording_storage(client)
    return who, start(who, item), storage


# --- Heartbeat -----------------------------------------------------------------------------------


def test_the_heartbeat_advances_while_a_run_executes(evidence_client: TestClient) -> None:  # noqa: F811
    who, run, storage = queued(evidence_client)
    clock = FakeClock()
    coordinator = fresh_coordinator(evidence_client, heartbeat_seconds=0.0, clock=clock)
    seen: list[Any] = []

    def observe() -> None:
        clock.advance(1)
        seen.append(run_row(evidence_client, run["id"])["last_heartbeat_at"])

    claimed = coordinator.claim_next()
    assert claimed is not None
    storage.on_io = observe
    assert coordinator.execute(claimed) is RunOutcome.COMPLETED
    beats = [b for b in seen if b is not None]
    assert len(beats) >= 4 and beats == sorted(beats)
    assert len(set(beats)) >= 3  # it kept moving while bytes were read
    assert beats[-1] > claimed.claim
    assert (
        who.detail(run["id"])["last_heartbeat_at"]
        > claimed.claim.isoformat().replace("+00:00", "Z")[:19]
    )


def test_heartbeats_are_throttled_so_a_short_run_makes_no_extra_writes(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    _who, run, _storage = queued(evidence_client, deterministic_bytes(70_000))
    coordinator = fresh_coordinator(evidence_client, heartbeat_seconds=3600)
    claimed = coordinator.claim_next()
    assert claimed is not None
    assert coordinator.execute(claimed) is RunOutcome.COMPLETED
    assert run_row(evidence_client, run["id"])["last_heartbeat_at"] == claimed.claim


# --- Stale recovery ---------------------------------------------------------------------------


def test_a_stale_running_run_returns_to_the_queue_and_then_completes_exactly_once(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    who, run, _storage = queued(evidence_client)
    clock = FakeClock()
    coordinator = fresh_coordinator(evidence_client, stale_seconds=30.0, clock=clock)
    first_claim = coordinator.claim_next()
    assert first_claim is not None

    clock.advance(10)  # a live worker: its last heartbeat is only 10 s old
    assert coordinator.recover_stale() == 0
    assert run_row(evidence_client, run["id"])["state"].value == "running"

    clock.advance(25)  # 35 s without a heartbeat: the worker is gone
    assert coordinator.recover_stale() == 1
    row = run_row(evidence_client, run["id"])
    assert row["state"].value == "queued"
    assert row["started_at"] is None and row["last_heartbeat_at"] is None
    assert row["cancel_requested_at"] is None and row["completed_at"] is None
    recovered = run_events(evidence_client, run["id"])[-1]
    assert recovered.action == "examination.run.recovered" and recovered.actor == SYSTEM_ACTOR
    assert (
        recovered.details["reason"] == "stale_heartbeat" and recovered.details["state"] == "queued"
    )

    clock.advance(1)
    assert coordinator.run_pending() == [RunOutcome.COMPLETED]
    assert actions(evidence_client, run["id"]) == [
        "created", "started", "recovered", "started", "completed",
    ]  # fmt: skip
    assert len(who.detail(run["id"])["observations"]) == 5  # published once, not twice


def test_recovery_never_touches_a_healthy_worker_that_is_really_running(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    who, run, storage = queued(evidence_client)
    coordinator = fresh_coordinator(evidence_client, heartbeat_seconds=0.0, stale_seconds=30.0)
    blocked, release = threading.Event(), threading.Event()
    reads = 0

    def hold() -> None:
        nonlocal reads
        reads += 1
        if reads == 3:
            blocked.set()
            release.wait(30)

    storage.on_io = hold
    outcome: list[RunOutcome | None] = []
    thread = threading.Thread(target=lambda: outcome.append(coordinator.run_next()))
    thread.start()
    assert blocked.wait(30)
    other_worker = fresh_coordinator(evidence_client, stale_seconds=30.0)
    assert other_worker.recover_stale() == 0  # fresh heartbeat: nothing to recover
    assert other_worker.claim_next() is None  # and it cannot claim the running run
    assert who.detail(run["id"])["state"] == "running"
    release.set()
    thread.join(60)
    assert outcome == [RunOutcome.COMPLETED]
    assert actions(evidence_client, run["id"]) == ["created", "started", "completed"]


def test_a_stale_run_with_a_pending_cancellation_is_cancelled_not_requeued(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    from tests.test_examination_coordinator import cancel_in_a_second_session

    who, run, _storage = queued(evidence_client)
    clock = FakeClock()
    coordinator = fresh_coordinator(evidence_client, stale_seconds=30.0, clock=clock)
    assert coordinator.claim_next() is not None
    cancel_in_a_second_session(evidence_client, run["id"], who.detail(run["id"])["created_by"])
    clock.advance(40)
    assert coordinator.recover_stale() == 1
    detail = who.detail(run["id"])
    assert detail["state"] == "cancelled" and detail["observations"] == []
    last = run_events(evidence_client, run["id"])[-1]
    assert last.action == "examination.run.cancelled"
    assert last.details["reason"] == "stale_heartbeat_with_cancellation_requested"
    assert coordinator.run_pending() == []  # a cancelled run is never executed


def test_a_worker_that_lost_its_run_cannot_publish_anything(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    """Fencing: after recovery and a second claim, the first worker's writes match no row."""
    who, run, _storage = queued(evidence_client)
    clock = FakeClock()
    coordinator = fresh_coordinator(evidence_client, stale_seconds=30.0, clock=clock)
    stale_claim = coordinator.claim_next()
    assert stale_claim is not None
    clock.advance(31)
    assert coordinator.recover_stale() == 1
    clock.advance(1)
    new_claim = coordinator.claim_next()
    assert new_claim is not None and new_claim.claim != stale_claim.claim

    # The stale worker wakes up. Whether it fails at a heartbeat or only at the completion
    # commit, it must lose and write nothing.
    for heartbeat in (0.0, 3600.0):
        stale_worker = fresh_coordinator(evidence_client, heartbeat_seconds=heartbeat, clock=clock)
        before = case_totals(evidence_client)
        assert stale_worker.execute(stale_claim) is RunOutcome.LOST
        assert case_totals(evidence_client) == before
        assert who.detail(run["id"])["observations"] == []
        assert run_row(evidence_client, run["id"])["started_at"] == new_claim.claim

    assert coordinator.execute(new_claim) is RunOutcome.COMPLETED  # the current owner finishes
    assert len(who.detail(run["id"])["observations"]) == 5
    assert actions(evidence_client, run["id"]).count("completed") == 1


def test_recovery_only_requeues_runs_that_are_actually_stale_across_many_runs(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(2_000))
    who = examiner(evidence_client)
    ids = [start(who, item)["id"] for _ in range(5)]
    clock = FakeClock()
    coordinator = fresh_coordinator(evidence_client, stale_seconds=30.0, clock=clock)
    for _ in range(3):  # three runs are claimed early, two later
        assert coordinator.claim_next() is not None
    clock.advance(20)
    assert coordinator.claim_next() is not None and coordinator.claim_next() is not None
    clock.advance(15)  # first three are 35 s old, last two 15 s old
    assert coordinator.recover_stale() == 3
    states = [run_row(evidence_client, i)["state"].value for i in ids]
    assert states == ["queued", "queued", "queued", "running", "running"]


# --- Graceful shutdown and restart ----------------------------------------------------------------


def test_a_run_in_flight_is_released_to_the_queue_when_the_worker_stops(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    who, run, storage = queued(evidence_client)
    stop = threading.Event()
    reads = 0

    def stop_during_the_second_read() -> None:
        nonlocal reads
        reads += 1
        if reads == 3:
            stop.set()

    storage.on_io = stop_during_the_second_read
    coordinator = fresh_coordinator(evidence_client)
    claimed = coordinator.claim_next()
    assert claimed is not None
    assert coordinator.execute(claimed, stop) is RunOutcome.RELEASED

    row = run_row(evidence_client, run["id"])
    assert row["state"].value == "queued" and row["started_at"] is None
    assert who.detail(run["id"])["observations"] == []
    released = run_events(evidence_client, run["id"])[-1]
    assert released.action == "examination.run.recovered"
    assert released.details["reason"] == "worker_shutdown"

    storage.on_io = None
    assert coordinator.run_pending() == [RunOutcome.COMPLETED]  # picked up after the restart
    assert len(who.detail(run["id"])["observations"]) == 5


def test_a_queued_run_survives_a_backend_restart(
    evidence_client: TestClient,  # noqa: F811
    database_url: str,
) -> None:
    who, run, _storage = queued(evidence_client, deterministic_bytes(70_000))
    first = application(evidence_client)
    restarted = create_app(
        make_settings(database_url, access_mode="restricted", max_evidence_bytes=2_000_000)
    )
    restarted.state.evidence_storage = first.state.evidence_storage
    with TestClient(restarted):  # the new process starts (and later stops) cleanly
        assert restarted.state.examination_coordinator.run_pending() == [RunOutcome.COMPLETED]
    assert who.detail(run["id"])["state"] == "completed"
    assert len(who.detail(run["id"])["observations"]) == 5


def test_a_running_run_left_by_a_crashed_process_is_recovered_by_the_next_one(
    evidence_client: TestClient,  # noqa: F811
    database_url: str,
) -> None:
    who, run, _storage = queued(evidence_client, deterministic_bytes(70_000))
    crashed = fresh_coordinator(evidence_client)
    assert crashed.claim_next() is not None  # ... and the process dies here, mid-run
    assert run_row(evidence_client, run["id"])["state"].value == "running"

    with application(evidence_client).state.session_factory() as session:  # time passes
        session.execute(
            update(AnalysisRun)
            .where(AnalysisRun.public_id == run["id"])
            .values(last_heartbeat_at=utcnow() - timedelta(minutes=5))
        )
        session.commit()
    restarted = create_app(
        make_settings(database_url, access_mode="restricted", max_evidence_bytes=2_000_000)
    )
    restarted.state.evidence_storage = application(evidence_client).state.evidence_storage
    with TestClient(restarted):
        coordinator = restarted.state.examination_coordinator
        assert coordinator.recover_stale() == 1
        assert coordinator.run_pending() == [RunOutcome.COMPLETED]
    assert who.detail(run["id"])["state"] == "completed"
    assert actions(evidence_client, run["id"]) == [
        "created", "started", "recovered", "started", "completed",
    ]  # fmt: skip


def test_the_stale_threshold_must_comfortably_exceed_the_heartbeat_interval(
    database_url: str,
) -> None:
    with pytest.raises(ValidationError, match="at least three times"):
        make_settings(
            database_url, examination_heartbeat_seconds=10.0, examination_stale_seconds=20.0
        )
    settings = make_settings(
        database_url, examination_heartbeat_seconds=2.0, examination_stale_seconds=6.0
    )
    assert isinstance(settings, Settings) and settings.examination_worker_enabled is False


# --- The real background worker ----------------------------------------------------------------


@pytest.fixture
def worker_client(database_url: str, tmp_path: Path) -> Iterator[TestClient]:
    """The real application with its background worker running (lifespan started)."""
    app = create_app(
        make_settings(
            database_url,
            access_mode="restricted",
            max_evidence_bytes=2_000_000,
            examination_worker_enabled=True,
            examination_poll_seconds=30.0,  # only an explicit wake-up can be fast
            examination_heartbeat_seconds=0.0,
            examination_stale_seconds=5.0,
        )
    )
    with app.state.session_factory() as session:
        if (
            session.execute(select(Case).where(Case.public_id == OPERATIONAL_CASE_ID)).first()
            is None
        ):
            organization = session.execute(
                select(Organization).where(Organization.public_id == "ORG-001")
            ).scalar_one()
            session.add(
                Case(
                    public_id=OPERATIONAL_CASE_ID,
                    organization_id=organization.id,
                    title="Synthetic restricted-mode Evidence test Case",
                    summary=None,
                    is_demonstration=False,
                    created_by="test:provisioning",
                    updated_by="test:provisioning",
                )
            )
            session.commit()
    app.state.evidence_storage = LocalEvidenceStorage(tmp_path / "private-evidence")
    with TestClient(app) as client:
        yield client


def wait_for(who: Examiner, run_id: str, state: str, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    detail: dict[str, Any] = {}
    while time.monotonic() < deadline:
        detail = who.detail(run_id)
        if detail["state"] == state:
            return detail
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach {state}; last state {detail.get('state')}")


def test_the_background_worker_picks_up_a_new_run_without_polling_delay(
    worker_client: TestClient,
) -> None:
    item = preserve(worker_client, body=deterministic_bytes(120_000, seed=8))
    who = examiner(worker_client)
    supervisor = application(worker_client).state.examination_supervisor
    assert supervisor.running  # started by the application lifespan
    assert any(t.name == "veritas-examination-worker" for t in threading.enumerate())
    started = time.monotonic()
    run = start(who, item)  # poll interval is 30 s: only the wake-up can make this quick
    detail = wait_for(who, run["id"], "completed", timeout=20)
    assert time.monotonic() - started < 20
    assert [o["origin"] for o in detail["observations"]] == ["analysis_run"] * 5
    assert actions(worker_client, run["id"]) == ["created", "started", "completed"]


def test_the_worker_survives_an_unexpected_failure_in_one_iteration(
    worker_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = application(worker_client).state.examination_coordinator
    real = coordinator.recover_stale
    calls = {"n": 0}

    def flaky() -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("synthetic failure inside the worker loop")
        return int(real())

    monkeypatch.setattr(coordinator, "recover_stale", flaky)
    item = preserve(worker_client, body=deterministic_bytes(20_000))
    who = examiner(worker_client)
    supervisor = application(worker_client).state.examination_supervisor
    supervisor.wake()  # iteration 1 raises ...
    time.sleep(0.2)
    run = start(who, item)  # ... and the loop is still alive for this one
    wait_for(who, run["id"], "completed", timeout=20)
    assert supervisor.running and calls["n"] >= 2


def test_stopping_the_worker_releases_the_run_in_flight_and_restarting_finishes_it(
    worker_client: TestClient,
) -> None:
    item = preserve(worker_client, body=BODY)
    who = examiner(worker_client)
    storage = install_recording_storage(worker_client)
    blocked, release = threading.Event(), threading.Event()
    reads = 0

    def hold() -> None:
        nonlocal reads
        reads += 1
        if reads == 3:
            blocked.set()
            release.wait(30)

    storage.on_io = hold
    run = start(who, item)
    assert blocked.wait(30)
    assert who.detail(run["id"])["state"] == "running"

    supervisor = application(worker_client).state.examination_supervisor
    stopper = threading.Thread(target=lambda: supervisor.stop(timeout=30))
    stopper.start()
    time.sleep(0.2)
    release.set()  # the Method resumes, sees the stop request at its next checkpoint
    stopper.join(30)
    assert not supervisor.running
    row = run_row(worker_client, run["id"])
    assert row["state"].value == "queued" and row["started_at"] is None
    assert actions(worker_client, run["id"]) == ["created", "started", "recovered"]

    storage.on_io = None
    supervisor.start()  # the restarted process
    wait_for(who, run["id"], "completed", timeout=30)
    assert len(who.detail(run["id"])["observations"]) == 5


def test_shutdown_of_the_application_stops_the_worker_thread(
    database_url: str, tmp_path: Path
) -> None:
    app = create_app(
        make_settings(database_url, examination_worker_enabled=True, examination_poll_seconds=0.05)
    )
    app.state.evidence_storage = LocalEvidenceStorage(tmp_path / "private-evidence")
    with TestClient(app):
        assert app.state.examination_supervisor.running
    assert not app.state.examination_supervisor.running
    assert not any(
        t.name == "veritas-examination-worker" and t.is_alive() for t in threading.enumerate()
    )
