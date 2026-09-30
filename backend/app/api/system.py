"""Operational endpoints: liveness, readiness, and system information."""

from __future__ import annotations

import logging
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app import __version__
from app.core.config import Settings
from app.core.security import Principal, get_optional_principal
from app.db.migrations import current_revision
from app.db.session import get_session
from app.schemas import CapabilityOut, DatabaseInfo, HealthOut, ReadinessOut, SystemInfo

logger = logging.getLogger("veritas.system")

router = APIRouter()

# The single, backend-owned statement of what this version can and cannot do.
# The frontend renders availability from this list instead of hard-coding it.
CAPABILITIES: tuple[CapabilityOut, ...] = (
    CapabilityOut(
        key="case_records",
        label="Case records",
        status="available",
        note="Read-only access to case, objective, evidence, finding, claim and assessment "
        "records.",
    ),
    CapabilityOut(
        key="evidence_profile",
        label="Evidence Profile",
        status="available",
        note="Read-only Evidence Profiles; preserved-object integrity values are projected from "
        "server-computed byte size, SHA-256, SHA-512 and basic signature validation.",
    ),
    CapabilityOut(
        key="case_knowledge_graph",
        label="Case Knowledge Graph",
        status="available",
        note="Projection of recorded records and typed relationships.",
    ),
    CapabilityOut(
        key="audit",
        label="Audit",
        status="available",
        note="Append-only record of actions; read-only in V1.",
    ),
    CapabilityOut(
        key="identity",
        label="User identity & access control",
        status="available",
        note="Provisioned identities, server-side sessions, role capabilities and case-level "
        "authorization.",
    ),
    CapabilityOut(
        key="evidence_intake",
        label="Evidence intake, integrity & custody foundation",
        status="available",
        note="Bounded streaming upload, private quarantine/preservation, SHA-256/SHA-512, "
        "basic leading-byte validation and RECEIVED/PRESERVED custody records. This does not "
        "establish authenticity or perform forensic examination.",
    ),
    CapabilityOut(
        key="examination",
        label="Automated examination",
        status="reserved",
        note="No examination methods are executable in V1; no Analysis Runs are produced.",
    ),
    CapabilityOut(
        key="timeline",
        label="Timeline reconstruction",
        status="reserved",
        note="Requires extracted temporal observations from examination.",
    ),
    CapabilityOut(
        key="review",
        label="Review & decisions",
        status="reserved",
        note="Recording reviews and decisions requires investigator identity.",
    ),
    CapabilityOut(
        key="report",
        label="Reports & Case Package",
        status="reserved",
        note="Report generation and verifiable Case Packages are not implemented.",
    ),
)


@router.get("/health", response_model=HealthOut, tags=["system"])
def health() -> HealthOut:
    """Liveness: the process is up. Performs no dependency checks."""
    return HealthOut(status="ok")


@router.get(
    "/ready", response_model=ReadinessOut, tags=["system"], responses={503: {"model": ReadinessOut}}
)
def ready(request: Request) -> JSONResponse:
    """Readiness: database reachable and schema at the expected migration revision."""
    engine: Engine = request.app.state.engine
    checks: dict[str, Literal["ok", "failed", "outdated"]] = {
        "database": "failed",
        "schema": "failed",
    }
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
        revision = current_revision(engine)
        checks["schema"] = "ok" if revision == request.app.state.expected_revision else "outdated"
    except Exception as exc:  # readiness must report, never raise
        logger.warning(
            "readiness check failed",
            extra={"event": "readiness_failed", "exception_type": type(exc).__name__},
        )
    is_ready = all(value == "ok" for value in checks.values())
    body = ReadinessOut(status="ready" if is_ready else "not_ready", checks=checks)
    return JSONResponse(status_code=200 if is_ready else 503, content=body.model_dump())


@router.get("/api/v1/system", response_model=SystemInfo, tags=["system"])
def system_info(
    request: Request,
    principal: Annotated[Principal | None, Depends(get_optional_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> SystemInfo:
    settings: Settings = request.app.state.settings
    engine: Engine = request.app.state.engine
    principal_kind = principal.kind if principal is not None else None
    try:
        revision = current_revision(engine)
    except Exception:
        revision = None
    return SystemInfo(
        name="VERITAS",
        version=__version__,
        api_version="v1",
        environment=settings.environment,
        access_mode=settings.access_mode,
        principal=principal_kind,
        uptime_seconds=round(time.monotonic() - request.app.state.started_monotonic, 1),
        database=DatabaseInfo(
            dialect=engine.dialect.name,
            schema_revision=revision,
            expected_revision=request.app.state.expected_revision,
        ),
        capabilities=list(CAPABILITIES),
    )
