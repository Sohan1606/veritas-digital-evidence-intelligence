"""V2.2 retrieval streaming: bounded reads/memory, audit ordering, disconnects and failures."""

from __future__ import annotations

import hashlib
import tracemalloc
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services.evidence_hashing import HASH_CHUNK_BYTES
from app.services.records import record_audit_event
from tests.evidence_support import (
    SECRET_PATH,
    asgi_get,
    audit_events,
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
)

RETRIEVED = "evidence.object.retrieved"


def _events(client: TestClient, object_id: str) -> int:
    return len(audit_events(client, action=RETRIEVED, entity_id=object_id))


def _chunks_for(size: int) -> int:
    return -(-size // HASH_CHUNK_BYTES)


# --- Bounded streaming --------------------------------------------------------------------


def test_reads_are_bounded_and_the_handle_is_closed(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    body = deterministic_bytes(1_900_000, seed=1)
    item = preserve(evidence_client, body=body)
    storage = install_recording_storage(evidence_client)
    reader, _ = login_as(evidence_client, "INVESTIGATOR")

    response = reader.get(item.content)

    assert response.status_code == 200 and response.content == body
    assert storage.read_sizes, "the preserved stream was never read"
    assert None not in storage.read_sizes, "an unbounded read() of preserved evidence occurred"
    assert max(s for s in storage.read_sizes if s is not None) <= HASH_CHUNK_BYTES
    # One bounded read per chunk plus the final look-ahead; never a single read of the object.
    assert len(storage.read_sizes) >= _chunks_for(len(body))
    assert storage.all_closed() and len(storage.streams) == 1


def test_a_large_object_streams_in_bounded_chunks_with_bounded_memory(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    size = 40 * 1024 * 1024
    application(evidence_client).state.settings.max_evidence_bytes = 64 * 1024 * 1024
    body = deterministic_bytes(size, seed=2)
    expected = hashlib.sha256(body).hexdigest()
    item = preserve(evidence_client, body=body)
    del body  # from here on the test itself holds no copy of the object
    reader, _ = login_as(evidence_client, "INVESTIGATOR")

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        result = asgi_get(reader, item.content, keep_chunks=False)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result.status == 200 and result.completed and result.error is None
    assert result.total == size and result.sha256 == expected
    assert result.headers["content-length"] == str(size)
    assert max(result.chunk_sizes) <= HASH_CHUNK_BYTES
    assert len(result.chunk_sizes) == _chunks_for(size)
    # Streaming 40 MiB must not hold it (or anything close): far below the object size.
    assert peak < 4 * 1024 * 1024, f"peak traced memory {peak} bytes"
    assert peak < size // 8


def test_repeated_retrievals_are_identical_and_leave_no_handle_open(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    body = deterministic_bytes(250_000, seed=3)
    item = preserve(evidence_client, body=body)
    storage = install_recording_storage(evidence_client)
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    responses = [reader.get(item.content) for _ in range(4)]
    assert all(r.content == body for r in responses)
    assert len(storage.streams) == 4 and storage.all_closed()
    assert _events(evidence_client, item.object_id) == 4


# --- Audit ordering -----------------------------------------------------------------------


def test_audit_is_committed_before_the_final_chunk_is_released(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    size = 3 * HASH_CHUNK_BYTES + 17  # four chunks
    item = preserve(evidence_client, body=deterministic_bytes(size, seed=4))
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    seen: list[tuple[int, int]] = []
    at_start: list[int] = []

    result = asgi_get(
        reader,
        item.content,
        on_start=lambda: at_start.append(_events(evidence_client, item.object_id)),
        on_chunk=lambda index, _size: seen.append(
            (index, _events(evidence_client, item.object_id))
        ),
    )

    assert result.completed and result.total == size
    assert len(result.chunk_sizes) == 4
    assert at_start == [0]
    # No event while any byte other than the last chunk is being released; one by the last.
    assert seen == [(0, 0), (1, 0), (2, 0), (3, 1)]
    assert _events(evidence_client, item.object_id) == 1


@pytest.mark.parametrize("size", [0, 1, HASH_CHUNK_BYTES])
def test_single_chunk_and_empty_objects_are_audited_before_the_response_exists(
    evidence_client: TestClient,  # noqa: F811
    size: int,
) -> None:
    body = b"x" * size if size <= 1 else deterministic_bytes(size, seed=5)
    media = "text/plain" if size <= 1 else "application/pdf"
    item = preserve(evidence_client, body=body, filename="one.bin", media_type=media)
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    at_start: list[int] = []

    result = asgi_get(
        reader,
        item.content,
        on_start=lambda: at_start.append(_events(evidence_client, item.object_id)),
    )

    assert result.completed and b"".join(result.chunks) == body
    assert at_start == [1]
    assert _events(evidence_client, item.object_id) == 1


# --- Client disconnects -------------------------------------------------------------------


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
@pytest.mark.parametrize("disconnect_after", [1, 2])
def test_client_disconnect_records_nothing_closes_the_handle_and_mutates_nothing(
    evidence_client: TestClient,  # noqa: F811
    spec_version: str,
    disconnect_after: int,
) -> None:
    size = 6 * HASH_CHUNK_BYTES + 5
    item = preserve(evidence_client, body=deterministic_bytes(size, seed=6))
    storage = install_recording_storage(evidence_client)
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    snapshot = object_snapshot(evidence_client, item)
    path = preserved_file(evidence_client, item)
    file_state = (path.read_bytes(), path.stat().st_mtime_ns)

    result = asgi_get(
        reader, item.content, spec_version=spec_version, disconnect_after=disconnect_after
    )

    assert not result.completed
    assert result.total < size
    # Deterministic closure: released by the time the ASGI call returned, not at garbage collection.
    assert storage.all_closed()
    assert _events(evidence_client, item.object_id) == 0
    assert object_snapshot(evidence_client, item) == snapshot
    assert (path.read_bytes(), path.stat().st_mtime_ns) == file_state
    # The object remains fully retrievable and exactly one event is recorded for the next read.
    again = reader.get(item.content)
    assert again.status_code == 200 and again.content == item.body
    assert _events(evidence_client, item.object_id) == 1


# --- Audit-write failure: fail closed -----------------------------------------------------


def _failing_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("action") == RETRIEVED:
            raise RuntimeError("synthetic audit store failure")
        return record_audit_event(*args, **kwargs)

    # Patch the name where the service looks it up, leaving every other audit write intact.
    monkeypatch.setattr("app.services.evidence_access.record_audit_event", broken)


def test_failed_audit_withholds_the_final_chunk_of_a_multi_chunk_object(
    evidence_client: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    size = 3 * HASH_CHUNK_BYTES + 100
    body = deterministic_bytes(size, seed=7)
    item = preserve(evidence_client, body=body)
    storage = install_recording_storage(evidence_client)
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    _failing_audit(monkeypatch)

    result = asgi_get(reader, item.content)

    assert not result.completed
    assert result.total == 3 * HASH_CHUNK_BYTES  # everything except the held-back final chunk
    assert hashlib.sha256(body).hexdigest() != result.sha256
    assert storage.all_closed() and _events(evidence_client, item.object_id) == 0
    monkeypatch.undo()
    assert reader.get(item.content).content == body
    assert _events(evidence_client, item.object_id) == 1


def test_failed_audit_releases_nothing_for_a_single_chunk_object(
    evidence_client: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = deterministic_bytes(2_000, seed=8)
    item = preserve(evidence_client, body=body)
    storage = install_recording_storage(evidence_client)
    reader, _ = login_as(evidence_client, "INVESTIGATOR")
    _failing_audit(monkeypatch)

    result = asgi_get(reader, item.content)

    assert result.status == 500
    payload = b"".join(result.chunks)
    assert body not in payload and b"internal_error" in payload
    assert b"synthetic audit store failure" not in payload
    assert storage.all_closed() and _events(evidence_client, item.object_id) == 0


# --- Storage failures ---------------------------------------------------------------------


def test_a_read_failure_on_the_first_read_is_a_clean_sanitized_503(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(300_000, seed=9))
    storage = install_recording_storage(evidence_client)
    storage.fail_on_read = 1
    reader, _ = login_as(evidence_client, "INVESTIGATOR")

    response = reader.get(item.content)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "evidence_storage_unavailable"
    assert error["message"] == "Private evidence storage is unavailable"
    assert SECRET_PATH not in response.text and item.storage_key not in response.text
    assert storage.all_closed() and _events(evidence_client, item.object_id) == 0


def test_a_read_failure_mid_stream_aborts_without_an_event(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    size = 5 * HASH_CHUNK_BYTES
    item = preserve(evidence_client, body=deterministic_bytes(size, seed=10))
    storage = install_recording_storage(evidence_client)
    storage.fail_on_read = 4  # first, look-ahead, then the third read fails inside the stream
    reader, _ = login_as(evidence_client, "INVESTIGATOR")

    result = asgi_get(reader, item.content)

    assert result.status == 200 and not result.completed
    assert 0 < result.total < size
    assert SECRET_PATH not in str(result.header_items)
    assert storage.all_closed() and _events(evidence_client, item.object_id) == 0


def test_a_file_that_shrinks_while_open_aborts_instead_of_returning_a_short_body(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    size = 4 * HASH_CHUNK_BYTES
    item = preserve(evidence_client, body=deterministic_bytes(size, seed=11))
    storage = install_recording_storage(evidence_client)
    storage.eof_after_bytes = 2 * HASH_CHUNK_BYTES + 10
    reader, _ = login_as(evidence_client, "INVESTIGATOR")

    result = asgi_get(reader, item.content)

    assert result.headers["content-length"] == str(size)
    assert not result.completed and result.total < size
    assert storage.all_closed() and _events(evidence_client, item.object_id) == 0


def test_a_file_that_grows_while_streaming_is_capped_at_the_advertised_length(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    size = 4 * HASH_CHUNK_BYTES
    body = deterministic_bytes(size, seed=12)
    item = preserve(evidence_client, body=body)
    path = preserved_file(evidence_client, item)
    reader, _ = login_as(evidence_client, "INVESTIGATOR")

    def grow(index: int, _size: int) -> None:
        if index == 0:
            with path.open("ab") as handle:
                handle.write(b"appended after the object was opened" * 50)

    result = asgi_get(reader, item.content, on_chunk=grow)

    assert result.completed and result.headers["content-length"] == str(size)
    assert b"".join(result.chunks) == body  # never longer than what was advertised
    assert _events(evidence_client, item.object_id) == 1
