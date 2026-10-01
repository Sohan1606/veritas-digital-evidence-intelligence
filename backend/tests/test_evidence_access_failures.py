"""V2.2 failure simulation: database timeout, storage outage and restart recovery (all real)."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.main import create_app
from app.services.evidence_hashing import HASH_CHUNK_BYTES
from app.services.evidence_storage import LocalEvidenceStorage
from tests.conftest import make_settings
from tests.evidence_support import (
    SECRET_PATH,
    Preserved,
    asgi_get,
    audit_events,
    csrf_header,
    deterministic_bytes,
    install_recording_storage,
    login_as,
    object_snapshot,
    preserve,
    preserved_file,
)
from tests.test_evidence_intake_api import (
    application,
    evidence_client,  # noqa: F401  (pytest fixture shared with the V2.1 intake tests)
    storage_root,
)

RETRIEVED = "evidence.object.retrieved"
VERIFIED = "evidence.object.integrity_verified"


def _count(client: TestClient, action: str, item: Preserved) -> int:
    return len(audit_events(client, action=action, entity_id=item.object_id))


@contextmanager
def audit_writes_time_out(client: TestClient) -> Iterator[None]:
    """Make every AuditEvent insert hit a genuine, fast database lock timeout.

    PostgreSQL: another connection holds ACCESS EXCLUSIVE on ``audit_events`` and the
    application's sessions run with ``lock_timeout``. SQLite: another connection holds the
    write lock and the application's sessions use a short busy timeout. Reads are unaffected,
    so authorization and evidence resolution succeed and only the audit write fails.
    """
    app = application(client)
    engine = app.state.engine
    url = engine.url.render_as_string(hide_password=False)
    original_factory = app.state.session_factory
    if engine.dialect.name == "postgresql":
        fast = create_engine(url, connect_args={"options": "-c lock_timeout=300"})
        holder = engine.connect()
        transaction = holder.begin()
        holder.execute(text("LOCK TABLE audit_events IN ACCESS EXCLUSIVE MODE"))

        def release() -> None:
            transaction.rollback()
            holder.close()

    else:
        fast = create_engine(url, connect_args={"timeout": 0.2})
        raw = sqlite3.connect(engine.url.database or "", timeout=0.1, isolation_level=None)
        raw.execute("BEGIN IMMEDIATE")

        def release() -> None:
            raw.execute("ROLLBACK")
            raw.close()

    app.state.session_factory = sessionmaker(bind=fast, autoflush=False, expire_on_commit=False)
    try:
        yield
    finally:
        app.state.session_factory = original_factory
        release()
        fast.dispose()


def _quiet_client(client: TestClient) -> TestClient:
    return TestClient(application(client), raise_server_exceptions=False)


# --- Database timeout ---------------------------------------------------------------------


def test_a_database_timeout_during_verification_fails_closed_and_recovers(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(300_000, seed=61))
    reader, csrf = login_as(evidence_client, "INVESTIGATOR")
    caller = _quiet_client(evidence_client)
    caller.cookies.update(reader.cookies)
    storage = install_recording_storage(evidence_client)
    snapshot = object_snapshot(evidence_client, item)

    with audit_writes_time_out(evidence_client):
        response = caller.post(item.verify, headers=csrf_header(csrf))

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error" and error["request_id"]
    for leak in (
        "lock",
        "timeout",
        "sqlite",
        "psycopg",
        "audit_events",
        "INSERT",
        item.storage_key,
    ):
        assert leak.lower() not in response.text.lower()
    assert "MATCH" not in response.text  # no result may be returned without an audit record
    assert storage.all_closed()
    assert _count(evidence_client, VERIFIED, item) == 0
    assert object_snapshot(evidence_client, item) == snapshot
    assert application(evidence_client).state.engine.pool.checkedout() == 0

    # After the database recovers the same request succeeds and records exactly one event:
    # the failed attempt left no partial or duplicate record behind.
    retry = caller.post(item.verify, headers=csrf_header(csrf))
    assert retry.status_code == 200 and retry.json()["result"] == "MATCH"
    assert _count(evidence_client, VERIFIED, item) == 1


def test_a_database_timeout_during_retrieval_releases_no_complete_copy(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    streamed = preserve(
        evidence_client, body=deterministic_bytes(4 * HASH_CHUNK_BYTES + 5, seed=62)
    )
    single = preserve(evidence_client, body=deterministic_bytes(2_000, seed=63))
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    caller = _quiet_client(evidence_client)
    caller.cookies.update(reader.cookies)
    storage = install_recording_storage(evidence_client)

    with audit_writes_time_out(evidence_client):
        small = caller.get(single.content)
        big = asgi_get(reader, streamed.content)

    # Single-chunk object: the audit is written before the response exists -> clean 500.
    assert small.status_code == 500 and small.json()["error"]["code"] == "internal_error"
    assert single.body not in small.content
    # Streamed object: everything except the held-back final chunk, never a complete copy.
    assert not big.completed and big.total == 4 * HASH_CHUNK_BYTES
    assert storage.all_closed()
    assert (
        _count(evidence_client, RETRIEVED, single)
        == _count(evidence_client, RETRIEVED, streamed)
        == 0
    )

    assert caller.get(single.content).content == single.body
    assert caller.get(streamed.content).content == streamed.body
    assert _count(evidence_client, RETRIEVED, streamed) == 1


def test_an_unreachable_database_is_a_sanitized_error_and_storage_is_never_opened(
    evidence_client: TestClient,  # noqa: F811
    tmp_path: Path,
) -> None:
    item = preserve(evidence_client)
    dead = create_app(
        make_settings(
            "postgresql+psycopg://nobody:placeholder@127.0.0.1:1/none?connect_timeout=1",
            access_mode="restricted",
        )
    )
    recording = install_recording_storage(evidence_client)
    dead.state.evidence_storage = recording
    caller = TestClient(dead, raise_server_exceptions=False)
    caller.cookies.set("veritas_session", "x" * 40)

    for response in (caller.get(item.content), caller.post(item.verify)):
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "internal_error"
        assert "placeholder" not in response.text and "127.0.0.1" not in response.text
    assert recording.streams == []  # without a database decision, storage is never touched


# --- Storage outage -----------------------------------------------------------------------


def test_a_storage_outage_is_reported_honestly_and_fully_recovers(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    """The volume becomes inaccessible (like a bad mount) and later comes back.

    Making the storage root's *parent* untraversable is an outage the application cannot
    repair. (Chmod on the store's own directories is not one: the store re-applies 0700 to its
    directories on every access.)
    """
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        pytest.skip("directory permissions are not enforced for root or on this platform")
    item = preserve(evidence_client, body=deterministic_bytes(200_000, seed=64))
    reader, csrf = login_as(evidence_client, "INVESTIGATOR")
    assert reader.get(item.content).content == item.body
    volume = storage_root(evidence_client)

    volume.parent.chmod(0)
    try:
        retrieval = reader.get(item.content)
        verification = reader.post(item.verify, headers=csrf_header(csrf))
    finally:
        volume.parent.chmod(0o700)

    assert retrieval.status_code == 503
    assert retrieval.json()["error"]["code"] == "evidence_storage_unavailable"
    assert verification.status_code == 200 and verification.json()["result"] == "UNAVAILABLE"
    for response in (retrieval, verification):
        assert str(volume) not in response.text and item.storage_key not in response.text
    assert _count(evidence_client, RETRIEVED, item) == 1  # only the pre-outage success
    assert _count(evidence_client, VERIFIED, item) == 1  # the UNAVAILABLE attempt is recorded

    # Recovery: nothing was damaged, and both operations work again immediately.
    assert reader.get(item.content).content == item.body
    assert reader.post(item.verify, headers=csrf_header(csrf)).json()["result"] == "MATCH"
    results = [
        e.details["result"]
        for e in audit_events(evidence_client, action=VERIFIED, entity_id=item.object_id)
    ]
    assert results == ["UNAVAILABLE", "MATCH"]


def test_a_storage_error_message_carrying_a_private_path_never_reaches_a_client(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(300_000, seed=65))
    storage = install_recording_storage(evidence_client)
    storage.fail_on_read = 1
    reader, csrf = login_as(evidence_client, "INVESTIGATOR")
    responses: list[Any] = [
        reader.get(item.content),
        reader.post(item.verify, headers=csrf_header(csrf)),
    ]
    for response in responses:
        assert SECRET_PATH not in response.text
        assert SECRET_PATH not in str(dict(response.headers))


# --- Restart recovery ---------------------------------------------------------------------


def _restarted(client: TestClient, database_url: str) -> TestClient:
    """A brand-new application (new engine, pools, state) over the same database and storage."""
    app = create_app(
        make_settings(database_url, access_mode="restricted", max_evidence_bytes=2_000_000)
    )
    app.state.evidence_storage = LocalEvidenceStorage(storage_root(client))
    return TestClient(app)


def test_objects_sessions_and_audit_history_survive_an_application_restart(
    evidence_client: TestClient,  # noqa: F811
    database_url: str,
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(350_000, seed=66))
    reader, csrf = login_as(evidence_client, "INVESTIGATOR")
    assert reader.post(item.verify, headers=csrf_header(csrf)).json()["result"] == "MATCH"
    assert reader.get(item.content).content == item.body
    before = {
        VERIFIED: [
            e.public_id
            for e in audit_events(evidence_client, action=VERIFIED, entity_id=item.object_id)
        ],
        RETRIEVED: [
            e.public_id
            for e in audit_events(evidence_client, action=RETRIEVED, entity_id=item.object_id)
        ],
    }
    snapshot = object_snapshot(evidence_client, item)

    with _restarted(evidence_client, database_url) as after:
        after.cookies.update(reader.cookies)  # sessions are server-side, so they survive
        assert after.post(item.verify, headers=csrf_header(csrf)).json()["result"] == "MATCH"
        assert after.get(item.content).content == item.body

    assert object_snapshot(evidence_client, item) == snapshot
    for action in (VERIFIED, RETRIEVED):
        history = [
            e.public_id
            for e in audit_events(evidence_client, action=action, entity_id=item.object_id)
        ]
        assert history[: len(before[action])] == before[action]  # earlier events are intact
        assert len(history) == len(before[action]) + 1


def test_an_interrupted_stream_before_a_restart_leaves_no_partial_state(
    evidence_client: TestClient,  # noqa: F811
    database_url: str,
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(6 * HASH_CHUNK_BYTES, seed=67))
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    snapshot = object_snapshot(evidence_client, item)
    path = preserved_file(evidence_client, item)
    stat = (path.stat().st_size, path.stat().st_mtime_ns)

    interrupted = asgi_get(reader, item.content, disconnect_after=2)  # the process "dies" here
    assert not interrupted.completed

    with _restarted(evidence_client, database_url) as after:
        after.cookies.update(reader.cookies)
        assert _count(evidence_client, RETRIEVED, item) == 0
        assert object_snapshot(evidence_client, item) == snapshot
        assert (path.stat().st_size, path.stat().st_mtime_ns) == stat
        again = after.get(item.content)
        assert again.status_code == 200 and again.content == item.body
    assert _count(evidence_client, RETRIEVED, item) == 1
