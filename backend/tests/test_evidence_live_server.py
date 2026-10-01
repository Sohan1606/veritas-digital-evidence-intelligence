"""V2.2 behavior over real sockets: a live uvicorn server, raw HTTP clients, real disconnects."""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import struct
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.core.security import SESSION_COOKIE
from tests.evidence_support import (
    RecordingStorage,
    audit_events,
    deterministic_bytes,
    install_recording_storage,
    login_as,
    object_snapshot,
    preserve,
)
from tests.test_evidence_intake_api import (
    application,
    evidence_client,  # noqa: F401  (pytest fixture shared with the V2.1 intake tests)
)

RETRIEVED = "evidence.object.retrieved"
VERIFIED = "evidence.object.integrity_verified"
MIB = 1024 * 1024


@dataclass(frozen=True)
class Wire:
    """A raw HTTP client bound to a live server and one signed-in user."""

    port: int
    token: str
    csrf: str

    def connect(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)

    def headers(self, *, mutating: bool = False) -> dict[str, str]:
        headers = {"Host": "localhost", "Cookie": f"{SESSION_COOKIE}={self.token}"}
        if mutating:
            headers["Cookie"] += f"; veritas_csrf={self.csrf}"
            headers["X-CSRF-Token"] = self.csrf
        return headers

    def get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        conn = self.connect()
        try:
            conn.request("GET", path, headers=self.headers())
            response = conn.getresponse()
            body = response.read()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, body
        finally:
            conn.close()

    def post(self, path: str) -> tuple[int, bytes]:
        conn = self.connect()
        try:
            conn.request("POST", path, headers=self.headers(mutating=True))
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()


@pytest.fixture
def live(evidence_client: TestClient) -> Iterator[Callable[[], Wire]]:  # noqa: F811
    """Serve the test application on a real socket; yields a factory for signed-in clients."""
    application(evidence_client).state.settings.max_evidence_bytes = 64 * MIB
    config = uvicorn.Config(
        application(evidence_client), host="127.0.0.1", port=0, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "the live server did not start"
    port = server.servers[0].sockets[0].getsockname()[1]

    def signed_in() -> Wire:
        client, csrf = login_as(evidence_client, "INVESTIGATOR")
        token = client.cookies.get(SESSION_COOKIE)
        assert token
        return Wire(port, token, csrf)

    try:
        yield signed_in
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def _wait_until(condition: Callable[[], bool], timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def _events(client: TestClient, action: str, object_id: str) -> int:
    return len(audit_events(client, action=action, entity_id=object_id))


def _reset_connection(conn: http.client.HTTPConnection) -> None:
    """Abort a connection with a TCP RST, as a crashed client or a dropped link would."""
    assert conn.sock is not None
    conn.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    conn.close()


def test_real_http_retrieval_is_byte_exact_with_the_documented_headers(
    evidence_client: TestClient,  # noqa: F811
    live: Callable[[], Wire],
) -> None:
    body = deterministic_bytes(3 * MIB + 7, seed=71)
    item = preserve(evidence_client, body=body)
    wire = live()

    status, headers, payload = wire.get(item.content)

    assert status == 200 and payload == body
    assert headers["content-length"] == str(len(body))
    assert headers["content-type"] == "application/pdf"
    assert headers["content-disposition"] == f'attachment; filename="{item.object_id}.bin"'
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["cache-control"] == "no-store" and "transfer-encoding" not in headers
    assert _events(evidence_client, RETRIEVED, item.object_id) == 1


@pytest.mark.parametrize("abort", ["rst", "fin"])
def test_a_real_client_disconnect_mid_download_records_nothing(
    evidence_client: TestClient,  # noqa: F811
    live: Callable[[], Wire],
    abort: str,
) -> None:
    body = deterministic_bytes(24 * MIB, seed=72)
    item = preserve(evidence_client, body=body)
    storage: RecordingStorage = install_recording_storage(evidence_client)
    snapshot = object_snapshot(evidence_client, item)
    wire = live()

    conn = wire.connect()
    conn.request("GET", item.content, headers=wire.headers())
    response = conn.getresponse()
    assert response.status == 200
    assert len(response.read(256 * 1024)) == 256 * 1024
    if abort == "rst":
        _reset_connection(conn)
    else:
        conn.close()

    assert _wait_until(storage.all_closed), "the preserved-object handle was never released"
    assert _events(evidence_client, RETRIEVED, item.object_id) == 0
    assert object_snapshot(evidence_client, item) == snapshot

    status, _, payload = wire.get(item.content)  # the object is still fully retrievable
    assert status == 200 and hashlib.sha256(payload).digest() == hashlib.sha256(body).digest()
    assert _events(evidence_client, RETRIEVED, item.object_id) == 1
    assert storage.all_closed()


def test_a_storage_failure_mid_download_aborts_the_response_on_the_wire(
    evidence_client: TestClient,  # noqa: F811
    live: Callable[[], Wire],
) -> None:
    body = deterministic_bytes(1_500_000, seed=73)
    item = preserve(evidence_client, body=body)
    storage = install_recording_storage(evidence_client)
    storage.fail_on_read = 5
    wire = live()

    conn = wire.connect()
    conn.request("GET", item.content, headers=wire.headers())
    response = conn.getresponse()
    assert response.status == 200 and response.getheader("content-length") == str(len(body))
    with pytest.raises(http.client.IncompleteRead) as incomplete:
        response.read()  # the declared Content-Length is never satisfied
    conn.close()

    assert len(incomplete.value.partial) < len(body)
    assert _wait_until(storage.all_closed)
    assert _events(evidence_client, RETRIEVED, item.object_id) == 0

    storage.fail_on_read = None
    status, _, payload = wire.get(item.content)
    assert status == 200 and payload == body
    assert _events(evidence_client, RETRIEVED, item.object_id) == 1


def test_a_verification_abandoned_by_the_client_is_still_audited_exactly_once(
    evidence_client: TestClient,  # noqa: F811
    live: Callable[[], Wire],
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(20 * MIB, seed=74))
    snapshot = object_snapshot(evidence_client, item)
    wire = live()

    conn = wire.connect()
    conn.request("POST", item.verify, headers=wire.headers(mutating=True))
    _reset_connection(conn)  # gone before the result can be read

    assert _wait_until(lambda: _events(evidence_client, VERIFIED, item.object_id) == 1)
    time.sleep(0.5)  # and it must not be recorded twice
    (event,) = audit_events(evidence_client, action=VERIFIED, entity_id=item.object_id)
    assert event.details["result"] == "MATCH"
    assert object_snapshot(evidence_client, item) == snapshot


def test_concurrent_real_downloads_and_verifications_are_safe(
    evidence_client: TestClient,  # noqa: F811
    live: Callable[[], Wire],
) -> None:
    body = deterministic_bytes(2 * MIB, seed=75)
    item = preserve(evidence_client, body=body)
    storage = install_recording_storage(evidence_client)
    snapshot = object_snapshot(evidence_client, item)
    wires = [live() for _ in range(10)]

    def download(wire: Wire) -> bool:
        status, _, payload = wire.get(item.content)
        return status == 200 and payload == body

    def verify(wire: Wire) -> str:
        status, payload = wire.post(item.verify)
        assert status == 200, payload
        return str(json.loads(payload)["result"])

    with ThreadPoolExecutor(max_workers=20) as pool:
        downloads = [pool.submit(download, wire) for wire in wires]
        verifications = [pool.submit(verify, wire) for wire in wires]
        assert all(job.result(timeout=120) for job in downloads)
        assert {job.result(timeout=120) for job in verifications} == {"MATCH"}

    assert _events(evidence_client, RETRIEVED, item.object_id) == 10
    assert _events(evidence_client, VERIFIED, item.object_id) == 10
    assert _wait_until(storage.all_closed) and len(storage.streams) == 20
    assert object_snapshot(evidence_client, item) == snapshot


def test_a_slow_download_holds_no_database_connection_and_does_not_block_others(
    evidence_client: TestClient,  # noqa: F811
    live: Callable[[], Wire],
) -> None:
    slow_object = preserve(evidence_client, body=deterministic_bytes(16 * MIB, seed=76))
    quick_object = preserve(evidence_client, body=deterministic_bytes(50_000, seed=77))
    pool = application(evidence_client).state.engine.pool
    slow, quick = live(), live()

    conn = slow.connect()
    conn.request("GET", slow_object.content, headers=slow.headers())
    response = conn.getresponse()
    samples: list[int] = []
    for _ in range(6):  # a deliberately slow reader while the server is mid-stream
        assert len(response.read(32 * 1024)) == 32 * 1024
        samples.append(pool.checkedout())
        time.sleep(0.1)
    status, _, payload = quick.get(quick_object.content)  # an unrelated request is unaffected
    status_verify, _ = quick.post(quick_object.verify)
    _reset_connection(conn)

    assert status == 200 and len(payload) == 50_000 and status_verify == 200
    assert samples == [0] * 6, f"a database connection was pinned during a slow download: {samples}"
