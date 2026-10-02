"""Case-scoped examination API: Methods, Analysis Runs, cancellation and retry.

Reading uses ``examination:read``. Everything that creates or changes a run uses the new
``examination:execute`` capability, for an authenticated identity, never on a demonstration
Case. Case authorization (with masked 404s and CSRF for unsafe methods) runs first; no storage
is touched until the request has been authorized and found eligible.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response
from sqlalchemy.orm import Session

from app.api.cases import readable_case
from app.core.security import Principal, get_principal, require_case_capability
from app.db.session import get_session
from app.domain.models import Case
from app.examination.coordinator import CancelOutcome
from app.schemas import (
    AnalysisRunCreateIn,
    AnalysisRunDetailOut,
    AnalysisRunOut,
    AnalysisRunRetryIn,
    ListResponse,
    MethodOut,
)
from app.services import examination, queries

router = APIRouter(prefix="/api/v1/cases", tags=["examination"])

RunId = Annotated[str, Path(pattern=r"^ANL-\d{3,9}$", description="Analysis Run identifier")]
SessionDep = Annotated[Session, Depends(get_session)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
CaseDep = Annotated[Case, Depends(readable_case)]

EXAMINATION_READ = Depends(require_case_capability("examination:read"))
EXAMINATION_EXECUTE = Depends(
    require_case_capability(
        "examination:execute", authenticated_only=True, non_demonstration_only=True
    )
)


def _wake_worker(request: Request) -> None:
    request.app.state.examination_supervisor.wake()


@router.get(
    "/{case_id}/examination/methods",
    response_model=ListResponse[MethodOut],
    dependencies=[EXAMINATION_READ],
)
def list_methods(request: Request, case: CaseDep) -> ListResponse[MethodOut]:
    items = examination.method_catalogue(request.app.state.method_registry)
    return ListResponse(items=items, count=len(items))


@router.get(
    "/{case_id}/analysis-runs",
    response_model=ListResponse[AnalysisRunOut],
    dependencies=[EXAMINATION_READ],
)
def list_analysis_runs(case: CaseDep, session: SessionDep) -> ListResponse[AnalysisRunOut]:
    items = queries.list_analysis_runs(session, case)
    return ListResponse(items=items, count=len(items))


@router.post(
    "/{case_id}/analysis-runs",
    response_model=AnalysisRunOut,
    status_code=201,
    responses={200: {"description": "The idempotency key was used before; the original run."}},
    dependencies=[EXAMINATION_EXECUTE],
)
def create_analysis_run(
    case: CaseDep,
    payload: AnalysisRunCreateIn,
    request: Request,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
) -> AnalysisRunOut:
    run, created = examination.create_run(
        session,
        case=case,
        principal=principal,
        payload=payload,
        registry=request.app.state.method_registry,
        storage=request.app.state.evidence_storage,
    )
    response.status_code = 201 if created else 200
    if created:
        _wake_worker(request)
    return run


@router.get(
    "/{case_id}/analysis-runs/{run_id}",
    response_model=AnalysisRunDetailOut,
    dependencies=[EXAMINATION_READ],
)
def get_analysis_run(case: CaseDep, run_id: RunId, session: SessionDep) -> AnalysisRunDetailOut:
    return queries.analysis_run_detail(session, case, run_id)


@router.post(
    "/{case_id}/analysis-runs/{run_id}/cancel",
    response_model=AnalysisRunOut,
    responses={202: {"description": "Cancellation was requested for a running run."}},
    dependencies=[EXAMINATION_EXECUTE],
)
def cancel_analysis_run(
    case: CaseDep,
    run_id: RunId,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
) -> AnalysisRunOut:
    run, outcome = examination.cancel(session, case=case, principal=principal, run_public_id=run_id)
    response.status_code = 200 if outcome is CancelOutcome.CANCELLED else 202
    return run


@router.post(
    "/{case_id}/analysis-runs/{run_id}/retry",
    response_model=AnalysisRunOut,
    status_code=201,
    responses={200: {"description": "The idempotency key was used before; the original run."}},
    dependencies=[EXAMINATION_EXECUTE],
)
def retry_analysis_run(
    case: CaseDep,
    run_id: RunId,
    payload: AnalysisRunRetryIn,
    request: Request,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
) -> AnalysisRunOut:
    run, created = examination.retry_run(
        session,
        case=case,
        principal=principal,
        run_public_id=run_id,
        idempotency_key=payload.idempotency_key,
        registry=request.app.state.method_registry,
        storage=request.app.state.evidence_storage,
    )
    response.status_code = 201 if created else 200
    if created:
        _wake_worker(request)
    return run
