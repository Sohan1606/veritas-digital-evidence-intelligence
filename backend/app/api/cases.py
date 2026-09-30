"""Case-scoped, read-only API.

Every case-data route is nested under ``/api/v1/cases/{case_id}`` so that V2 case-level
authorization has exactly one enforcement point (:func:`readable_case`).
V1 has no write routes: cases cannot be created or modified through the API.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.security import Principal, get_principal, require_case_capability
from app.db.session import get_session
from app.domain.models import Case
from app.schemas import (
    AnalysisRunOut,
    AuditEventOut,
    CaseDetail,
    CaseGraph,
    CaseSummary,
    ClaimOut,
    EvidenceOut,
    EvidenceProfileOut,
    FindingOut,
    ListResponse,
)
from app.services import queries
from app.services.graph import build_case_graph

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])

CaseId = Annotated[
    str, Path(pattern=r"^CASE-\d{3,9}$", description="Case identifier, e.g. CASE-001")
]
EvidenceId = Annotated[
    str, Path(pattern=r"^EVD-\d{3,9}$", description="Evidence identifier, e.g. EVD-001")
]
FindingId = Annotated[
    str, Path(pattern=r"^FND-\d{3,9}$", description="Finding identifier, e.g. FND-001")
]

SessionDep = Annotated[Session, Depends(get_session)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]


def readable_case(case_id: CaseId, session: SessionDep, principal: PrincipalDep) -> Case:
    return queries.get_readable_case(session, principal, case_id)


CaseDep = Annotated[Case, Depends(readable_case)]


@router.get("", response_model=ListResponse[CaseSummary])
def list_cases(session: SessionDep, principal: PrincipalDep) -> ListResponse[CaseSummary]:
    items = queries.list_cases(session, principal)
    return ListResponse(items=items, count=len(items))


@router.get("/{case_id}", response_model=CaseDetail)
def get_case(case: CaseDep, session: SessionDep) -> CaseDetail:
    return queries.case_detail(session, case)


@router.get(
    "/{case_id}/evidence",
    response_model=ListResponse[EvidenceOut],
    dependencies=[Depends(require_case_capability("evidence:read"))],
)
def list_evidence(case: CaseDep, session: SessionDep) -> ListResponse[EvidenceOut]:
    items = queries.list_evidence(session, case)
    return ListResponse(items=items, count=len(items))


@router.get(
    "/{case_id}/evidence/{evidence_id}/profile",
    response_model=EvidenceProfileOut,
    dependencies=[Depends(require_case_capability("evidence:read"))],
)
def get_evidence_profile(
    case: CaseDep, evidence_id: EvidenceId, session: SessionDep, principal: PrincipalDep
) -> EvidenceProfileOut:
    # Preserve anonymous V1 demo profiles while withholding V2.1 object-derived digests.
    return queries.evidence_profile(
        session,
        case,
        evidence_id,
        include_evidence_object_integrity=(principal.authenticated and not case.is_demonstration),
    )


@router.get(
    "/{case_id}/analysis-runs",
    response_model=ListResponse[AnalysisRunOut],
    dependencies=[Depends(require_case_capability("examination:read"))],
)
def list_analysis_runs(case: CaseDep, session: SessionDep) -> ListResponse[AnalysisRunOut]:
    items = queries.list_analysis_runs(session, case)
    return ListResponse(items=items, count=len(items))


@router.get(
    "/{case_id}/findings",
    response_model=ListResponse[FindingOut],
    dependencies=[Depends(require_case_capability("findings:read"))],
)
def list_findings(case: CaseDep, session: SessionDep) -> ListResponse[FindingOut]:
    items = queries.list_findings(session, case)
    return ListResponse(items=items, count=len(items))


@router.get(
    "/{case_id}/findings/{finding_id}",
    response_model=FindingOut,
    dependencies=[Depends(require_case_capability("findings:read"))],
)
def get_finding(case: CaseDep, finding_id: FindingId, session: SessionDep) -> FindingOut:
    return queries.get_finding(session, case, finding_id)


@router.get(
    "/{case_id}/claims",
    response_model=ListResponse[ClaimOut],
    dependencies=[Depends(require_case_capability("claims:read"))],
)
def list_claims(case: CaseDep, session: SessionDep) -> ListResponse[ClaimOut]:
    items = queries.list_claims(session, case)
    return ListResponse(items=items, count=len(items))


@router.get(
    "/{case_id}/graph",
    response_model=CaseGraph,
    dependencies=[Depends(require_case_capability("graph:read"))],
)
def get_case_graph(case: CaseDep, session: SessionDep) -> CaseGraph:
    return build_case_graph(session, case)


@router.get(
    "/{case_id}/audit-events",
    response_model=ListResponse[AuditEventOut],
    dependencies=[Depends(require_case_capability("case_audit:read"))],
)
def list_audit_events(
    case: CaseDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ListResponse[AuditEventOut]:
    items = queries.list_audit_events(session, case, limit)
    return ListResponse(items=items, count=len(items))
