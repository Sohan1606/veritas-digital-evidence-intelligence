"""Shared V2.2 test support. Builds on the V2.1 intake helpers; redefines none of their behavior."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import io
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, cast
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, inspect, select

from app.core.security import SESSION_COOKIE, hash_password
from app.domain.models import (
    AuditEvent,
    Case,
    EvidenceCustodyEvent,
    EvidenceObject,
    Organization,
    OrganizationMembership,
    Role,
    RoleAssignment,
    User,
)
from app.services.evidence_storage import LocalEvidenceStorage
from tests.test_evidence_intake_api import (
    BASE,
    OPERATIONAL_CASE_ID,
    PASSWORD,
    application,
    csrf_header,
    login,
    object_id,
    provision,
    register,
    storage_key,
    storage_root,
)

__all__ = [
    "BASE",
    "OPERATIONAL_CASE_ID",
    "PDF_PREFIX",
    "SECRET_PATH",
    "AsgiResult",
    "Preserved",
    "RecordingStorage",
    "asgi_get",
    "assert_only_an_audit_event_was_written",
    "audit_events",
    "captured_sql",
    "content_url",
    "csrf_header",
    "deterministic_bytes",
    "foreign_organization_client",
    "install_recording_storage",
    "iter_chunks",
    "login_as",
    "object_snapshot",
    "preserve",
    "preserved_file",
    "second_case",
    "verify_url",
]

PDF_PREFIX = b"%PDF-1.7\n"


def deterministic_bytes(total: int, seed: int = 0) -> bytes:
    """Exactly ``total`` reproducible, incompressible bytes that start with a PDF signature."""
    if total < len(PDF_PREFIX):
        raise ValueError("total is smaller than the signature prefix")
    stream = hashlib.shake_256(f"veritas-v22-test-{seed}".encode())
    return PDF_PREFIX + stream.digest(total - len(PDF_PREFIX))


def content_url(evidence_id: str, item_id: str, case_id: str = OPERATIONAL_CASE_ID) -> str:
    return f"{BASE}/{case_id}/evidence/{evidence_id}/objects/{item_id}/content"


def verify_url(evidence_id: str, item_id: str, case_id: str = OPERATIONAL_CASE_ID) -> str:
    return f"{BASE}/{case_id}/evidence/{evidence_id}/objects/{item_id}/verify"


@dataclass(frozen=True)
class Preserved:
    """A real PRESERVED EvidenceObject created through the V2.1 intake workflow."""

    evidence_id: str
    object_id: str
    body: bytes
    storage_key: str
    case_id: str = OPERATIONAL_CASE_ID

    @property
    def content(self) -> str:
        return content_url(self.evidence_id, self.object_id, self.case_id)

    @property
    def verify(self) -> str:
        return verify_url(self.evidence_id, self.object_id, self.case_id)


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def login_as(
    client: TestClient, role: str, case_id: str | None = OPERATIONAL_CASE_ID
) -> tuple[TestClient, str]:
    """A new client with its own cookie jar, signed in as a fresh user holding ``role``."""
    username = unique(f"v22-{role.lower()}")
    provision(client, username, role, case_id)
    other = TestClient(application(client))
    return other, login(other, username)


def preserve(
    client: TestClient,
    *,
    body: bytes | None = None,
    filename: str = "synthetic.pdf",
    media_type: str = "application/pdf",
    case_id: str = OPERATIONAL_CASE_ID,
    finalize: bool = True,
) -> Preserved:
    """Register, upload and (by default) finalize one object through the real V2.1 workflow.

    Afterwards ``client`` is signed in as the Custodian that finalized the object.
    """
    payload = PDF_PREFIX + b"Synthetic V2.2 preserved bytes.\n" if body is None else body
    investigator, custodian = unique("v22-intake"), unique("v22-custodian")
    provision(client, investigator, "INVESTIGATOR", case_id)
    provision(client, custodian, "CUSTODIAN", case_id)
    csrf = login(client, investigator)
    detail = register(
        client,
        csrf,
        path=f"{BASE}/{case_id}/evidence/intake",
        original_filename=filename,
        declared_media_type=media_type,
    )
    evidence_id = cast(str, cast(dict[str, Any], detail["evidence"])["id"])
    item_id = object_id(detail)
    uploaded = client.put(
        content_url(evidence_id, item_id, case_id),
        content=payload,
        headers={**csrf_header(csrf), "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 200, uploaded.text
    if finalize:
        custodian_csrf = login(client, custodian)
        finalized = client.post(
            f"{BASE}/{case_id}/evidence/{evidence_id}/objects/{item_id}/finalize",
            headers=csrf_header(custodian_csrf),
        )
        assert finalized.status_code == 200, finalized.text
        assert finalized.json()["state"] == "PRESERVED", finalized.text
    return Preserved(evidence_id, item_id, payload, storage_key(client, item_id), case_id)


def preserved_file(client: TestClient, item: Preserved) -> Path:
    return storage_root(client) / "preserved" / f"{item.storage_key}.bin"


def audit_events(
    client: TestClient, *, action: str | None = None, entity_id: str | None = None
) -> list[AuditEvent]:
    with application(client).state.session_factory() as session:
        statement = select(AuditEvent)
        if action is not None:
            statement = statement.where(AuditEvent.action == action)
        if entity_id is not None:
            statement = statement.where(AuditEvent.entity_public_id == entity_id)
        return list(
            session.execute(statement.order_by(AuditEvent.occurred_at, AuditEvent.public_id))
            .scalars()
            .all()
        )


def object_snapshot(client: TestClient, item: Preserved) -> dict[str, object]:
    """Every column of the EvidenceObject row plus custody/object counts for its Case."""
    with application(client).state.session_factory() as session:
        row = session.execute(
            select(EvidenceObject).where(EvidenceObject.public_id == item.object_id)
        ).scalar_one()
        columns = {
            attr.key: getattr(row, attr.key) for attr in inspect(EvidenceObject).column_attrs
        }
        case = session.execute(select(Case).where(Case.public_id == item.case_id)).scalar_one()
        columns["_custody_events"] = session.execute(
            select(func.count())
            .select_from(EvidenceCustodyEvent)
            .where(EvidenceCustodyEvent.evidence_object_id == row.id)
        ).scalar_one()
        columns["_case_object_count"] = session.execute(
            select(func.count())
            .select_from(EvidenceObject)
            .where(EvidenceObject.case_id == case.id)
        ).scalar_one()
        return columns


SECRET_PATH = "/private/secret/evidence/path"


class RecordingStream(io.RawIOBase):
    """A read-only stream that records read sizes, refuses unbounded reads, and can fail."""

    def __init__(
        self,
        inner: BinaryIO,
        sizes: list[int | None],
        *,
        fail_on_read: int | None = None,
        eof_after_bytes: int | None = None,
        on_io: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._inner = inner
        self.sizes = sizes
        self._on_io = on_io
        self._fail_on_read = fail_on_read
        self._eof_after_bytes = eof_after_bytes
        self._reads = 0
        self._delivered = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._inner.seek(offset, whence)

    def tell(self) -> int:
        return self._inner.tell()

    def readinto(self, buffer: Any) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def read(self, size: int | None = -1) -> bytes:
        self.sizes.append(size)
        if size is None or size < 0:
            raise AssertionError("unbounded read of preserved evidence")
        if self._on_io is not None:
            self._on_io()
        self._reads += 1
        if self._fail_on_read is not None and self._reads == self._fail_on_read:
            raise OSError(errno.EIO, f"synthetic I/O failure reading {SECRET_PATH}")
        if self._eof_after_bytes is not None:
            size = min(size, max(self._eof_after_bytes - self._delivered, 0))
        data = self._inner.read(size)
        self._delivered += len(data)
        return data

    def close(self) -> None:
        self._inner.close()
        super().close()


class RecordingStorage(LocalEvidenceStorage):
    """Local storage whose preserved streams record read sizes and closure, and can fail."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.read_sizes: list[int | None] = []
        self.streams: list[RecordingStream] = []
        self.fail_on_read: int | None = None
        self.eof_after_bytes: int | None = None
        self.on_io: Callable[[], None] | None = None

    def open_preserved_object(self, storage_key: str) -> BinaryIO:
        if self.on_io is not None:
            self.on_io()
        stream = RecordingStream(
            super().open_preserved_object(storage_key),
            self.read_sizes,
            fail_on_read=self.fail_on_read,
            eof_after_bytes=self.eof_after_bytes,
            on_io=self.on_io,
        )
        self.streams.append(stream)
        return cast(BinaryIO, stream)

    def all_closed(self) -> bool:
        return bool(self.streams) and all(stream.closed for stream in self.streams)


def install_recording_storage(client: TestClient) -> RecordingStorage:
    """Swap the app's storage for a recording one rooted at the same private directory."""
    app: FastAPI = application(client)
    recording = RecordingStorage(storage_root(client))
    app.state.evidence_storage = recording
    return recording


def iter_chunks(data: bytes, size: int) -> Iterator[bytes]:
    for offset in range(0, len(data), size):
        yield data[offset : offset + size]


# --- Scripted ASGI client ---------------------------------------------------------------


@dataclass
class AsgiResult:
    """What a scripted client observed. Chunks are hashed, and only kept when asked."""

    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    header_items: list[tuple[str, str]] = field(default_factory=list)
    chunk_sizes: list[int] = field(default_factory=list)
    chunks: list[bytes] = field(default_factory=list)
    sha256: str = ""
    completed: bool = False
    error: BaseException | None = None

    @property
    def total(self) -> int:
        return sum(self.chunk_sizes)


async def _asgi_get(
    app: FastAPI,
    path: str,
    *,
    token: str,
    spec_version: str,
    keep_chunks: bool,
    on_start: Callable[[], None] | None,
    on_chunk: Callable[[int, int], None] | None,
    disconnect_after: int | None,
) -> AsgiResult:
    result = AsgiResult()
    digest = hashlib.sha256()
    disconnected = asyncio.Event()
    request_sent = False

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if disconnected.is_set() and spec_version >= "2.4":
            # ASGI spec 2.4 servers raise OSError from send() once the client is gone.
            raise OSError("client disconnected")
        if disconnected.is_set():
            return  # spec 2.3 servers (uvicorn) silently discard output for a gone client
        if message["type"] == "http.response.start":
            result.status = message["status"]
            result.header_items = [
                (k.decode("latin-1").lower(), v.decode("latin-1")) for k, v in message["headers"]
            ]
            result.headers = dict(result.header_items)
            if on_start is not None:
                on_start()
            return
        body = message.get("body", b"")
        if body:
            index = len(result.chunk_sizes)
            result.chunk_sizes.append(len(body))
            digest.update(body)
            if keep_chunks:
                result.chunks.append(body)
            if on_chunk is not None:
                on_chunk(index, len(body))
            if disconnect_after is not None and len(result.chunk_sizes) >= disconnect_after:
                disconnected.set()
        if not message.get("more_body"):
            result.completed = True

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"cookie", f"{SESSION_COOKIE}={token}".encode())],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    try:
        await app(scope, receive, send)  # type: ignore[arg-type]
    except Exception as exc:
        result.error = exc
    result.sha256 = digest.hexdigest()
    return result


def asgi_get(
    client: TestClient,
    path: str,
    *,
    spec_version: str = "2.3",
    keep_chunks: bool = True,
    on_start: Callable[[], None] | None = None,
    on_chunk: Callable[[int, int], None] | None = None,
    disconnect_after: int | None = None,
) -> AsgiResult:
    """GET through the full ASGI stack as the user currently signed in on ``client``."""
    token = client.cookies.get(SESSION_COOKIE)
    assert token, "the client is not signed in"
    return asyncio.run(
        _asgi_get(
            application(client),
            path,
            token=token,
            spec_version=spec_version,
            keep_chunks=keep_chunks,
            on_start=on_start,
            on_chunk=on_chunk,
            disconnect_after=disconnect_after,
        )
    )


# --- SQL-level immutability proof -------------------------------------------------------

# The only writes a retrieval or verification may perform: one AuditEvent and the atomic
# allocation of its public identifier (the canonical mechanism for every AuditEvent).
PERMITTED_WRITE_TARGETS = ("IDENTIFIER_SEQUENCES", "AUDIT_EVENTS")


@contextmanager
def captured_sql(client: TestClient) -> Iterator[list[str]]:
    """Every SQL statement the application's engine executes inside the ``with`` block."""
    engine = application(client).state.engine
    statements: list[str] = []

    def capture(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(" ".join(statement.split()).upper())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def assert_only_an_audit_event_was_written(statements: list[str], *, events: int = 1) -> None:
    """Assert no evidence row, custody row or byte-bearing table was written or locked."""
    assert statements, "no SQL was captured"
    assert not [s for s in statements if "FOR UPDATE" in s], "a row lock was taken"
    assert not [s for s in statements if s.startswith("DELETE")], "a row was deleted"
    writes = [s for s in statements if s.startswith(("INSERT", "UPDATE"))]
    for statement in writes:
        assert any(f" {target}" in statement.split("(")[0] for target in PERMITTED_WRITE_TARGETS), (
            f"unexpected write: {statement[:120]}"
        )
    audit_inserts = [s for s in writes if s.startswith("INSERT INTO AUDIT_EVENTS")]
    assert len(audit_inserts) == events, (
        f"{len(audit_inserts)} AuditEvent inserts, expected {events}"
    )


# --- Shared authorization fixtures ------------------------------------------------------


def second_case(client: TestClient) -> str:
    """A second non-demonstration Case in the same Organization (created once, idempotent)."""
    case_id = "CASE-901"
    with application(client).state.session_factory() as session:
        if session.execute(select(Case).where(Case.public_id == case_id)).scalar_one_or_none():
            return case_id
        organization = session.execute(
            select(Organization).where(Organization.public_id == "ORG-001")
        ).scalar_one()
        session.add(
            Case(
                public_id=case_id,
                organization_id=organization.id,
                title="Synthetic second restricted-mode Case",
                summary=None,
                is_demonstration=False,
                created_by="test:provisioning",
                updated_by="test:provisioning",
            )
        )
        session.commit()
    return case_id


def foreign_organization_client(client: TestClient) -> tuple[TestClient, str]:
    """A signed-in INVESTIGATOR of a *different* Organization, holding a role on its own Case."""
    with application(client).state.session_factory() as session:
        organization = Organization(
            name=f"Synthetic foreign organization {uuid4().hex[:8]}",
            status="active",
            created_by="test:provisioning",
            updated_by="test:provisioning",
        )
        session.add(organization)
        session.flush()
        case = Case(
            organization_id=organization.id,
            title="Synthetic foreign Case",
            summary=None,
            is_demonstration=False,
            created_by="test:provisioning",
            updated_by="test:provisioning",
        )
        session.add(case)
        session.flush()
        outsider = User(
            username=unique("v22-foreign"),
            display_name="Synthetic outsider",
            status="active",
            password_hash=hash_password(PASSWORD),
            auth_provider=None,
            auth_subject=None,
            created_by="test:provisioning",
            updated_by="test:provisioning",
        )
        session.add(outsider)
        session.flush()
        membership = OrganizationMembership(
            user_id=outsider.id,
            organization_id=organization.id,
            status="active",
            created_by="test:provisioning",
            updated_by="test:provisioning",
        )
        session.add(membership)
        session.flush()
        role = session.execute(select(Role).where(Role.name == "INVESTIGATOR")).scalar_one()
        session.add(
            RoleAssignment(
                membership_id=membership.id,
                role_id=role.id,
                case_id=case.id,
                created_by="test:provisioning",
                updated_by="test:provisioning",
            )
        )
        session.commit()
        username, case_public_id = outsider.username, case.public_id
    other = TestClient(application(client))
    login(other, username)
    return other, case_public_id
