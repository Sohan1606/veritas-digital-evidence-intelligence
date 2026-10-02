"""Shared V2.3 test support. Builds on the V2.1/V2.2 helpers; redefines none of their behavior."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, Table, func, inspect, select, update

from app.db.types import utcnow
from app.domain.enums import AnalysisRunState, EvidenceType
from app.domain.models import (
    AnalysisRun,
    Assessment,
    AuditEvent,
    Case,
    CaseRelationship,
    Claim,
    EvidenceCustodyEvent,
    EvidenceObject,
    Finding,
    Observation,
)
from app.examination.contracts import (
    EvidenceInput,
    MethodDefinition,
    OutputSpec,
    ResourceLimits,
)
from app.examination.coordinator import RunCoordinator
from app.examination.methods.binary_characteristics import BINARY_CHARACTERISTICS, NoParameters
from app.examination.registry import MethodRegistry
from app.services.evidence_hashing import HASH_CHUNK_BYTES
from tests.evidence_support import (
    BASE,
    OPERATIONAL_CASE_ID,
    Preserved,
    csrf_header,
    login_as,
    preserved_file,
)
from tests.test_evidence_intake_api import application, storage_key

__all__ = [
    "BINARY",
    "METHOD_KEY",
    "METHOD_VERSION",
    "Examiner",
    "FakeClock",
    "add_preserved_object",
    "case_totals",
    "coordinator_of",
    "corrupt_file",
    "error_of",
    "examiner",
    "fresh_coordinator",
    "install_methods",
    "make_method",
    "payload",
    "quiesce_runs",
    "run_events",
    "run_row",
    "runs_url",
    "start",
]

METHOD_KEY = "core.binary_characteristics"
METHOD_VERSION = "1.0"
BINARY = BINARY_CHARACTERISTICS


def runs_url(suffix: str = "", case_id: str = OPERATIONAL_CASE_ID) -> str:
    return f"{BASE}/{case_id}/analysis-runs{suffix}"


def unique_key(prefix: str = "key") -> str:
    return f"{prefix}-{uuid4().hex[:16]}"


def payload(
    item: Preserved,
    *,
    key: str | None = None,
    parameters: dict[str, Any] | None = None,
    method_key: str = METHOD_KEY,
    method_version: str = METHOD_VERSION,
) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "evidence_object_id": item.object_id,
        "method_key": method_key,
        "method_version": method_version,
        "parameters": {} if parameters is None else parameters,
        "idempotency_key": key or unique_key(),
    }


@dataclass
class Examiner:
    """A signed-in user with their own cookie jar and CSRF token."""

    client: TestClient
    csrf: str

    def post(self, url: str, body: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return self.client.post(url, json=body, headers=csrf_header(self.csrf), **kwargs)

    def get(self, url: str) -> Any:
        return self.client.get(url)

    def start(self, item: Preserved, **kwargs: Any) -> Any:
        return self.post(runs_url(case_id=item.case_id), payload(item, **kwargs))

    def detail(self, run_id: str, case_id: str = OPERATIONAL_CASE_ID) -> dict[str, Any]:
        response = self.get(runs_url(f"/{run_id}", case_id))
        assert response.status_code == 200, response.text
        return cast(dict[str, Any], response.json())


def examiner(
    client: TestClient, role: str = "INVESTIGATOR", case_id: str | None = OPERATIONAL_CASE_ID
) -> Examiner:
    signed_in, csrf = login_as(client, role, case_id)
    return Examiner(signed_in, csrf)


def start(who: Examiner, item: Preserved, **kwargs: Any) -> dict[str, Any]:
    """Queue a run and return its body (asserts the 201 of a newly created run)."""
    response = who.start(item, **kwargs)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def coordinator_of(client: TestClient) -> RunCoordinator:
    return cast(RunCoordinator, application(client).state.examination_coordinator)


def run_row(client: TestClient, run_id: str) -> dict[str, Any]:
    """Every column of an AnalysisRun row (internal identifiers included)."""
    with application(client).state.session_factory() as session:
        row = session.execute(
            select(AnalysisRun).where(AnalysisRun.public_id == run_id)
        ).scalar_one()
        return {attr.key: getattr(row, attr.key) for attr in inspect(AnalysisRun).column_attrs}


def run_events(client: TestClient, run_id: str) -> list[AuditEvent]:
    with application(client).state.session_factory() as session:
        return list(
            session.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.entity_public_id == run_id,
                    AuditEvent.action.like("examination.%"),
                )
                .order_by(
                    AuditEvent.occurred_at, func.length(AuditEvent.public_id), AuditEvent.public_id
                )
            )
            .scalars()
            .all()
        )


def case_totals(client: TestClient, case_id: str = OPERATIONAL_CASE_ID) -> dict[str, int]:
    """Counts of every record family an examination must NOT create or change."""
    with application(client).state.session_factory() as session:
        case = session.execute(select(Case).where(Case.public_id == case_id)).scalar_one()
        models: dict[str, Any] = {
            "findings": Finding,
            "claims": Claim,
            "assessments": Assessment,
            "relationships": CaseRelationship,
            "objects": EvidenceObject,
            "custody_events": EvidenceCustodyEvent,
        }
        totals = {
            name: session.execute(
                select(func.count()).select_from(model).where(model.case_id == case.id)
            ).scalar_one()
            for name, model in models.items()
        }
        totals["observations"] = session.execute(
            select(func.count()).select_from(Observation).where(Observation.case_id == case.id)
        ).scalar_one()
        return totals


def corrupt_file(client: TestClient, item: Preserved, how: str) -> None:
    """Tamper with the stored preserved bytes the way a storage fault or an attacker might."""
    path: Path = preserved_file(client, item)
    original = path.read_bytes()
    if how == "modified":  # same size, one flipped byte
        path.write_bytes(bytes([original[0] ^ 0xFF]) + original[1:])
    elif how == "truncated":
        path.write_bytes(original[: len(original) // 2])
    elif how == "expanded":
        path.write_bytes(original + b"APPENDED")
    elif how == "replaced":
        path.write_bytes(hashlib.sha256(original).digest() * (len(original) // 32 + 1))
    elif how == "missing":
        path.unlink()
    else:  # pragma: no cover - guards the test helper itself
        raise ValueError(how)


# --- Test-only Methods -------------------------------------------------------------------------

ProbeExecutor = Callable[[EvidenceInput, BaseModel], tuple[str, ...]]


class DepthParameters(BaseModel):
    """A parameterized contract, used to exercise typed parameter validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    depth: int = Field(default=1, ge=1, le=5)
    label: str = Field(default="probe", max_length=16)


def consume_all(source: EvidenceInput) -> int:
    return sum(len(chunk) for chunk in source.chunks())


def make_method(
    execute: ProbeExecutor | None = None,
    *,
    key: str = "test.probe",
    version: str = "1.0",
    supported: Iterable[EvidenceType] = tuple(EvidenceType),
    enabled: bool = True,
    parameters_model: type[BaseModel] = NoParameters,
    outputs: int = 1,
    max_object_bytes: int = 1_073_741_824,
) -> MethodDefinition:
    """A deterministic probe Method whose behavior the test controls."""

    def default(source: EvidenceInput, _parameters: BaseModel) -> tuple[str, ...]:
        return (f"Observed byte count: {consume_all(source)}.",) * outputs

    return MethodDefinition(
        key=key,
        version=version,
        name="Test probe",
        purpose="A test-only Method.",
        supported_evidence_types=frozenset(supported),
        input_requirements=("PRESERVED bytes.",),
        parameters_model=parameters_model,
        outputs=tuple(
            OutputSpec(f"out_{i}", f"Output {i}", "A test output.") for i in range(outputs)
        ),
        limitations=("A test-only Method.",),
        limits=ResourceLimits(
            max_object_bytes=max_object_bytes,
            chunk_bytes=HASH_CHUNK_BYTES,
            max_runtime_seconds=60,
            max_observations=16,
            max_statement_chars=500,
        ),
        execute=execute or default,
        enabled=enabled,
    )


def install_methods(
    client: TestClient, *extra: MethodDefinition, binary: bool = True
) -> MethodRegistry:
    """Swap the application's Method registry (the worker reads it from ``app.state``)."""
    registry = MethodRegistry([*([BINARY] if binary else []), *extra])
    application(client).state.method_registry = registry
    return registry


def error_of(response: Any, status: int, code: str | None = None) -> dict[str, Any]:
    """Assert the structured error envelope (and nothing else) and return its ``error`` object."""
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}, body
    error = cast(dict[str, Any], body["error"])
    assert {"code", "message", "request_id"} <= set(error)
    assert error["request_id"] == response.headers["x-request-id"]
    if code is not None:
        assert error["code"] == code
    return error


def add_preserved_object(client: TestClient, item: Preserved, body: bytes) -> Preserved:
    """Add a SECOND acquisition to the same Evidence through the real V2.1 workflow."""
    investigator = examiner(client, "INVESTIGATOR", item.case_id)
    evidence_path = f"{BASE}/{item.case_id}/evidence/{item.evidence_id}/objects"
    created = investigator.post(
        evidence_path,
        {"original_filename": "second-acquisition.pdf", "declared_media_type": "application/pdf"},
    )
    assert created.status_code == 201, created.text
    object_id = str(created.json()["id"])
    uploaded = investigator.client.put(
        f"{evidence_path}/{object_id}/content",
        content=body,
        headers={**csrf_header(investigator.csrf), "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 200, uploaded.text
    custodian = examiner(client, "CUSTODIAN", item.case_id)
    finalized = custodian.post(f"{evidence_path}/{object_id}/finalize")
    assert finalized.status_code == 200 and finalized.json()["state"] == "PRESERVED"
    return Preserved(
        item.evidence_id, object_id, body, storage_key(client, object_id), item.case_id
    )


# --- Isolation, clocks and coordinators ----------------------------------------------------------


@pytest.fixture(autouse=True)
def quiesce_runs(engine: Engine) -> None:
    """Earlier tests share the database: end any run they left queued or running.

    Each leftover moves through a VALID lifecycle transition (queued or running -> cancelled),
    so a worker in the next test never claims or recovers another test's run.
    """
    table = cast(Table, AnalysisRun.__table__)
    now = utcnow()
    with engine.begin() as connection:
        connection.execute(
            update(table)
            .where(table.c.state.in_([AnalysisRunState.QUEUED, AnalysisRunState.RUNNING]))
            .values(
                state=AnalysisRunState.CANCELLED,
                completed_at=now,
                updated_at=now,
                updated_by="test:quiesce",
            )
        )


class FakeClock:
    """A controllable UTC clock for heartbeat and staleness tests."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def fresh_coordinator(
    client: TestClient,
    *,
    heartbeat_seconds: float = 0.0,
    stale_seconds: float = 30.0,
    clock: Callable[[], datetime] | None = None,
) -> RunCoordinator:
    """A separate worker instance wired like the application's (a second process, in effect).

    ``heartbeat_seconds=0`` makes every chunk a checkpoint, so cancellation and heartbeats are
    observed deterministically in small objects.
    """
    app = application(client)
    return RunCoordinator(
        session_factory=app.state.session_factory,
        storage=lambda: app.state.evidence_storage,
        registry=lambda: app.state.method_registry,
        heartbeat_seconds=heartbeat_seconds,
        stale_seconds=stale_seconds,
        clock=clock or utcnow,
    )
