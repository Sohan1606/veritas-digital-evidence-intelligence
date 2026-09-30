"""Case-scoped Evidence Intake, EvidenceObject detail and custody reads."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.cases import readable_case
from app.core.errors import EvidenceUnsupportedUploadError, NotFoundError
from app.core.security import Principal, get_principal, require_case_capability
from app.db.session import get_session
from app.domain.models import Case, Evidence
from app.schemas import (
    EvidenceCustodyEventOut,
    EvidenceDetailOut,
    EvidenceIntakeIn,
    EvidenceObjectIntakeIn,
    EvidenceObjectOut,
    ListResponse,
)
from app.services import evidence_intake, queries

router = APIRouter(prefix="/api/v1/cases", tags=["evidence intake"])

CaseId = Annotated[str, Path(pattern=r"^CASE-\d{3,9}$")]
EvidenceId = Annotated[str, Path(pattern=r"^EVD-\d{3,9}$")]
EvidenceObjectId = Annotated[str, Path(pattern=r"^EOBJ-\d{3,9}$")]
SessionDep = Annotated[Session, Depends(get_session)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
CaseDep = Annotated[Case, Depends(readable_case)]


@router.post(
    "/{case_id}/evidence/intake",
    response_model=EvidenceDetailOut,
    status_code=201,
    dependencies=[Depends(require_case_capability("evidence:intake"))],
)
def register_intake(
    case: CaseDep,
    payload: EvidenceIntakeIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> EvidenceDetailOut:
    evidence, _ = evidence_intake.register_evidence_intake(
        session, case=case, principal=principal, payload=payload
    )
    session.commit()
    return queries.evidence_intake_detail(session, case, evidence.public_id)


@router.post(
    "/{case_id}/evidence/{evidence_id}/objects",
    response_model=EvidenceObjectOut,
    status_code=201,
    dependencies=[Depends(require_case_capability("evidence:intake"))],
)
def register_additional_object(
    case: CaseDep,
    evidence_id: EvidenceId,
    payload: EvidenceObjectIntakeIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> EvidenceObjectOut:
    evidence = session.execute(
        select(Evidence).where(Evidence.public_id == evidence_id, Evidence.case_id == case.id)
    ).scalar_one_or_none()
    if evidence is None:
        raise NotFoundError("Evidence was not found in this case")
    item = evidence_intake.register_additional_evidence_object(
        session,
        case=case,
        evidence=evidence,
        principal=principal,
        original_filename=payload.original_filename,
        declared_media_type=payload.declared_media_type,
    )
    session.commit()
    refreshed = queries.evidence_intake_detail(session, case, evidence_id)
    return next(obj for obj in refreshed.objects if obj.id == item.public_id)


@router.put(
    "/{case_id}/evidence/{evidence_id}/objects/{object_id}/content",
    response_model=EvidenceObjectOut,
    dependencies=[Depends(require_case_capability("evidence:intake"))],
)
async def upload_content(
    case: CaseDep,
    evidence_id: EvidenceId,
    object_id: EvidenceObjectId,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
) -> EvidenceObjectOut:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/octet-stream":
        raise EvidenceUnsupportedUploadError("Evidence content must use application/octet-stream")
    item = await evidence_intake.upload_evidence_content(
        session,
        case=case,
        evidence_id=evidence_id,
        object_id=object_id,
        principal=principal,
        storage=request.app.state.evidence_storage,
        settings=request.app.state.settings,
        body=request.stream(),
        content_length=request.headers.get("content-length"),
    )
    refreshed = queries.evidence_intake_detail(session, case, evidence_id)
    return next(obj for obj in refreshed.objects if obj.id == item.public_id)


@router.post(
    "/{case_id}/evidence/{evidence_id}/objects/{object_id}/finalize",
    response_model=EvidenceObjectOut,
    dependencies=[
        Depends(require_case_capability("evidence:intake")),
        Depends(require_case_capability("custody:write")),
    ],
)
def finalize_object(
    case: CaseDep,
    evidence_id: EvidenceId,
    object_id: EvidenceObjectId,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> EvidenceObjectOut:
    item = evidence_intake.finalize_evidence_object(
        session,
        case=case,
        evidence_id=evidence_id,
        object_id=object_id,
        principal=principal,
        storage=request.app.state.evidence_storage,
    )
    refreshed = queries.evidence_intake_detail(session, case, evidence_id)
    return next(obj for obj in refreshed.objects if obj.id == item.public_id)


@router.get(
    "/{case_id}/evidence/{evidence_id}/intake",
    response_model=EvidenceDetailOut,
    dependencies=[
        Depends(
            require_case_capability(
                "evidence:read", authenticated_only=True, non_demonstration_only=True
            )
        )
    ],
)
def get_intake_detail(
    case: CaseDep, evidence_id: EvidenceId, session: SessionDep
) -> EvidenceDetailOut:
    return queries.evidence_intake_detail(session, case, evidence_id)


@router.get(
    "/{case_id}/evidence/{evidence_id}/objects/{object_id}/custody",
    response_model=ListResponse[EvidenceCustodyEventOut],
    dependencies=[
        Depends(
            require_case_capability(
                "custody:read", authenticated_only=True, non_demonstration_only=True
            )
        )
    ],
)
def list_custody(
    case: CaseDep,
    evidence_id: EvidenceId,
    object_id: EvidenceObjectId,
    session: SessionDep,
) -> ListResponse[EvidenceCustodyEventOut]:
    items = queries.evidence_custody_events(session, case, evidence_id, object_id)
    return ListResponse(items=items, count=len(items))
