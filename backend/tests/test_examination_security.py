"""V2.3 security properties: no leaks, storage isolation, bounded memory, no side channels."""

from __future__ import annotations

import ast
import base64
import json
import shutil
import socket
import subprocess
import tempfile
import tracemalloc
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app as application_package
from app.api import system
from app.db.base import Base
from app.examination.contracts import VERDICT_VOCABULARY
from app.examination.methods.binary_characteristics import BINARY_CHARACTERISTICS
from tests.evidence_support import (
    BASE,
    OPERATIONAL_CASE_ID,
    SECRET_PATH,
    audit_events,
    deterministic_bytes,
    install_recording_storage,
    preserve,
    preserved_file,
    verify_url,
)
from tests.examination_support import (
    Examiner,
    corrupt_file,
    examiner,
    fresh_coordinator,
    quiesce_runs,  # noqa: F401  (autouse isolation fixture)
    run_events,
    run_row,
    runs_url,
    start,
)
from tests.test_evidence_intake_api import (
    application,
    evidence_client,  # noqa: F401  (pytest fixture shared with the V2.1 intake tests)
    storage_root,
)

MIB = 1024 * 1024
ALLOWED_DETAIL_KEYS = {
    "examination.run.created": {
        "case_id", "evidence_id", "evidence_object_id", "method_key", "method_version", "state",
    },
    "examination.run.started": {
        "case_id", "evidence_id", "evidence_object_id", "method_key", "method_version", "state",
    },
    "examination.run.completed": {
        "case_id", "evidence_id", "evidence_object_id", "method_key", "method_version", "state",
        "observation_count",
    },
    "examination.run.failed": {
        "case_id", "evidence_id", "evidence_object_id", "method_key", "method_version", "state",
        "failure_code",
    },
    "examination.run.cancelled": {
        "case_id", "evidence_id", "evidence_object_id", "method_key", "method_version", "state",
        "reason",
    },
    "examination.run.retried": {
        "case_id", "evidence_id", "evidence_object_id", "method_key", "method_version", "state",
        "source_run_id",
    },
    "examination.run.cancel_requested": {
        "case_id", "evidence_id", "evidence_object_id", "method_key", "method_version", "state",
    },
    "examination.run.recovered": {
        "case_id", "evidence_id", "evidence_object_id", "method_key", "method_version", "state",
        "reason",
    },
}  # fmt: skip


def leak_candidates(client: TestClient, item: Any, run: dict[str, Any], key: str) -> list[str]:
    """Everything that must never appear in an audit event, a response or a log."""
    body = item.body
    return [
        item.storage_key,
        str(storage_root(client)),
        SECRET_PATH,
        key,
        run_row(client, run["id"])["request_fingerprint"],
        body[:64].hex(),
        base64.b64encode(body[:48]).decode(),
        "Traceback",
    ]


def lifecycle(client: TestClient, who: Examiner, item: Any, key: str) -> dict[str, Any]:
    run = start(who, item, key=key)
    fresh_coordinator(client).run_pending()
    return run


# --- Audit: exact metadata, no leaks ----------------------------------------------------------


def test_audit_events_carry_only_the_documented_metadata(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(90_000, seed=13))
    who = examiner(evidence_client)
    key = "audit-scan-key-0001"
    run = lifecycle(evidence_client, who, item, key)
    cancelled = start(who, item, key="audit-scan-key-0002")
    who.post(runs_url(f"/{cancelled['id']}/cancel"))
    retried = who.post(
        runs_url(f"/{cancelled['id']}/retry"), {"idempotency_key": "audit-scan-key-0003"}
    )
    assert retried.status_code == 201

    seen_actions: set[str] = set()
    for run_id in (run["id"], cancelled["id"], retried.json()["id"]):
        for event in run_events(evidence_client, run_id):
            seen_actions.add(event.action)
            assert set(event.details) == ALLOWED_DETAIL_KEYS[event.action], event.action
            assert event.entity_type == "analysis_run" and event.entity_public_id == run_id
            assert event.case_id is not None and event.organization_id is not None
            rendered = json.dumps(event.details, default=str) + event.action + event.actor
            for secret in leak_candidates(evidence_client, item, run, key):
                assert secret not in rendered, (event.action, secret)
    assert seen_actions >= {
        "examination.run.created", "examination.run.started", "examination.run.completed",
        "examination.run.cancelled", "examination.run.retried",
    }  # fmt: skip


def test_failure_audit_and_state_carry_codes_never_exception_text(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(300_000, seed=14))
    who = examiner(evidence_client)
    storage = install_recording_storage(evidence_client)
    storage.fail_on_read = 2  # raises OSError("synthetic I/O failure reading <SECRET_PATH>")
    run = lifecycle(evidence_client, who, item, "failure-scan-key-01")
    row = run_row(evidence_client, run["id"])
    assert row["state"].value == "failed"
    texts = [row["failure_code"], row["failure_message"]] + [
        json.dumps(e.details, default=str) for e in run_events(evidence_client, run["id"])
    ]
    for text in texts:
        assert SECRET_PATH not in text and "synthetic" not in text and "OSError" not in text
    assert (
        run_events(evidence_client, run["id"])[-1].details["failure_code"] == "evidence_unavailable"
    )


def test_no_response_discloses_a_path_a_storage_key_or_evidence_bytes(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(80_000, seed=15))
    who = examiner(evidence_client)
    key = "response-scan-key-01"
    responses = [who.start(item, key=key)]
    run_id = responses[0].json()["id"]
    fresh_coordinator(evidence_client).run_pending()
    responses += [
        who.get(runs_url()),
        who.get(runs_url(f"/{run_id}")),
        who.get(f"{BASE}/{OPERATIONAL_CASE_ID}/examination/methods"),
        who.start(item, key=key),  # replay
        who.post(runs_url(f"/{run_id}/cancel")),  # 409
        who.post(runs_url("/ANL-999/cancel")),  # 404
        who.start(item, key="response-scan-key-02", parameters={"x": SECRET_PATH}),  # 422
        who.get(f"{BASE}/{OPERATIONAL_CASE_ID}/audit-events"),
    ]
    for response in responses:
        for secret in leak_candidates(evidence_client, item, {"id": run_id}, key)[:1]:
            assert secret not in response.text  # the storage key
        assert str(storage_root(evidence_client)) not in response.text
        assert SECRET_PATH not in response.text
        assert item.body[:48].hex() not in response.text
        assert "Traceback" not in response.text and "storage_key" not in response.text
        assert "request_fingerprint" not in response.text


# --- Isolation and side channels --------------------------------------------------------------


def _tree(root: Path) -> list[tuple[str, int, int]]:
    return sorted(
        (p.relative_to(root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    )


def test_examination_never_writes_to_evidence_storage_or_makes_copies(
    evidence_client: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(250_000, seed=16))
    who = examiner(evidence_client)
    run = start(who, item)
    root = storage_root(evidence_client)
    before = _tree(root)

    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("examination attempted to create a temporary or duplicate copy")

    # Patched only while the worker runs, so the test's own HTTP calls are unaffected.
    with monkeypatch.context() as patch:
        for target in (
            "mkstemp",
            "mkdtemp",
            "NamedTemporaryFile",
            "TemporaryFile",
            "SpooledTemporaryFile",
        ):
            patch.setattr(tempfile, target, forbidden)
        for target in ("copy", "copy2", "copyfile", "copyfileobj", "move", "copytree"):
            patch.setattr(shutil, target, forbidden)
        assert fresh_coordinator(evidence_client).run_pending()

    assert who.detail(run["id"])["state"] == "completed"
    assert _tree(root) == before  # not one byte, name or timestamp changed under the storage root
    assert preserved_file(evidence_client, item).read_bytes() == item.body


def test_a_failed_examination_also_leaves_storage_exactly_as_it_was(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(120_000, seed=17))
    who = examiner(evidence_client)
    run = start(who, item)
    corrupt_file(evidence_client, item, "modified")  # the stored object no longer matches intake
    root = storage_root(evidence_client)
    before = _tree(root)
    fresh_coordinator(evidence_client).run_pending()
    assert who.detail(run["id"])["failure_code"] == "integrity_mismatch"
    assert _tree(root) == before  # no repair, no quarantine, no relocation


def test_examination_uses_no_process_or_network_capability_at_run_time(
    evidence_client: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(60_000, seed=18))
    who = examiner(evidence_client)
    run = start(who, item)

    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("a forbidden process or network operation was attempted")

    # Patched only while the worker runs. The test client's event loops are created per request
    # and (on Windows) emulate socketpair() with a loopback connect(), so they must not be patched.
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", forbidden)
        patch.setattr("os.system", forbidden)
        patch.setattr(socket, "create_connection", forbidden)
        patch.setattr(socket.socket, "connect", forbidden)
        patch.setattr(socket, "getaddrinfo", forbidden)
        assert fresh_coordinator(evidence_client).run_pending()
    assert who.detail(run["id"])["state"] == "completed"


def test_bytes_are_read_once_in_bounded_chunks_and_never_all_at_once(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    body = deterministic_bytes(500_000, seed=19)
    item = preserve(evidence_client, body=body)
    who = examiner(evidence_client)
    storage = install_recording_storage(evidence_client)
    start(who, item)
    storage.streams.clear()
    storage.read_sizes.clear()  # discard the eligibility probe; examine the worker only
    fresh_coordinator(evidence_client).run_pending()
    assert len(storage.streams) == 1 and storage.all_closed()
    assert storage.read_sizes and None not in storage.read_sizes
    assert max(size for size in storage.read_sizes if size is not None) == 65_536
    assert storage.streams[0]._delivered == len(body)  # every byte exactly once


def test_memory_stays_bounded_while_a_large_object_is_examined(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    """A 24 MiB object is examined with a hard ceiling on peak Python allocations."""
    application(evidence_client).state.settings.max_evidence_bytes = 64 * MIB
    body = deterministic_bytes(24 * MIB, seed=20)
    item = preserve(evidence_client, body=body)
    who = examiner(evidence_client)
    run = start(who, item)
    coordinator = fresh_coordinator(evidence_client, heartbeat_seconds=2.0)
    del body
    tracemalloc.start()
    try:
        outcomes = coordinator.run_pending()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert [o.value for o in outcomes] == ["completed"]
    assert (
        who.detail(run["id"])["observations"][0]["statement"] == f"Observed byte count: {24 * MIB}."
    )
    # Chunks are 64 KiB; a handful may be live at once. Buffering the object would be 24 MiB.
    assert peak < 4 * MIB, f"peak {peak / MIB:.1f} MiB for a 24 MiB object"


# --- V2.2 behavior is untouched by examination ---------------------------------------------------


def test_retrieval_and_verification_still_work_exactly_as_in_v22_after_an_examination(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    body = deterministic_bytes(75_000, seed=22)
    item = preserve(evidence_client, body=body)
    who = examiner(evidence_client)
    lifecycle(evidence_client, who, item, "regression-key-0001")
    retrieved = who.client.get(item.content)
    assert retrieved.status_code == 200 and retrieved.content == body
    assert (
        retrieved.headers["content-disposition"] == f'attachment; filename="{item.object_id}.bin"'
    )
    verified = who.client.post(
        verify_url(item.evidence_id, item.object_id), headers={"X-CSRF-Token": who.csrf}
    )
    assert verified.status_code == 200 and verified.json()["result"] == "MATCH"
    assert len(audit_events(evidence_client, action="evidence.object.integrity_verified")) >= 1


# --- Copy audit: nothing implies authenticity, manipulation or origin ---


def _string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_user_facing_examination_copy_states_measurements_never_conclusions() -> None:
    """Runtime strings may name a conclusion only inside an explicit disclaimer."""
    root = Path(application_package.__file__).parent
    files = [
        *sorted((root / "examination").rglob("*.py")),
        root / "services" / "examination.py",
        root / "api" / "examination.py",
    ]
    disclaimers = {
        BINARY_CHARACTERISTICS.purpose,
        BINARY_CHARACTERISTICS.limitations[0],
        # The one sentence that says failure is an integrity comparison, not authenticity.
        next(
            c for c in _string_constants(root / "examination" / "contracts.py")
            if "integrity comparison only" in c
        ),
    }  # fmt: skip
    offenders = [
        (path.name, text[:80])
        for path in files
        for text in _string_constants(path)
        if VERDICT_VOCABULARY.search(text) and text not in disclaimers
    ]
    # The regex itself is the only other literal that names the words.
    offenders = [o for o in offenders if "authentic" not in o[1] or "(" not in o[1]]
    assert offenders == []


def test_the_capability_note_describes_examination_without_a_verdict_claim() -> None:
    (entry,) = [c for c in system.CAPABILITIES if c.key == "examination"]
    assert entry.status == "available"
    for banned in ("real", "fake", "forged", "suspicious", "malicious", "manipulated"):
        assert banned not in entry.note.lower().split()
    assert "Observations only" in entry.note
    assert "makes no authenticity determination" in entry.note


# --- One concept, one owner ---


def test_there_is_no_second_method_policy_and_no_examination_table() -> None:
    tables = set(Base.metadata.tables)
    assert not {t for t in tables if "method" in t or "examination" in t or "verification" in t}
    runs = Base.metadata.tables["analysis_runs"]
    assert {
        "evidence_object_id", "idempotency_key", "request_fingerprint", "cancel_requested_at",
        "last_heartbeat_at", "failure_code", "failure_message",
    } <= set(runs.c.keys())  # fmt: skip
    # Only the Method's key and version are stored; never its definition or output.
    names = set(runs.c.keys())
    assert not {c for c in names if "definition" in c or "output" in c or "result" in c}
    observations = Base.metadata.tables["observations"]
    assert "evidence_object_id" not in observations.c  # provenance is run -> object, once
