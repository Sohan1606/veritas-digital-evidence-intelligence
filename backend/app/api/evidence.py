"""Case-scoped Evidence Intake, EvidenceObject detail and custody reads."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.types import Receive, Scope, Send

from app.api.cases import readable_case
from app.core.errors import EvidenceUnsupportedUploadError, NotFoundError
from app.core.security import Principal, get_principal, require_case_capability
from app.db.session import get_session
from app.domain.models import Case, Evidence
from app.schemas import (
    EvidenceCustodyEventOut,
    EvidenceDetailOut,
    EvidenceIntakeIn,
    EvidenceIntegrityVerificationOut,
    EvidenceObjectIntakeIn,
    EvidenceObjectOut,
    ListResponse,
)
from app.services import evidence_access, evidence_intake, queries
from app.services.evidence_access import EvidenceRetrieval

router = APIRouter(prefix="/api/v1/cases", tags=["evidence intake"])

CaseId = Annotated[str, Path(pattern=r"^CASE-\d{3,9}$")]
EvidenceId = Annotated[str, Path(pattern=r"^EVD-\d{3,9}$")]
EvidenceObjectId = Annotated[str, Path(pattern=r"^EOBJ-\d{3,9}$")]
SessionDep = Annotated[Session, Depends(get_session)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
CaseDep = Annotated[Case, Depends(readable_case)]

# V2.2 retrieval and verification: the existing ``evidence:read`` capability, authenticated
# identity only, never a demonstration Case. Case authorization runs before anything else.
EVIDENCE_READ_ACCESS = Depends(
    require_case_capability("evidence:read", authenticated_only=True, non_demonstration_only=True)
)


class RetrievalResponse(StreamingResponse):
    """Streams one retrieval and releases its preserved-object handle however it ends.

    Starlette does not close a body iterator when a client disconnects or a transfer fails, so
    the handle is closed here deterministically instead of relying on garbage collection.
    """

    def __init__(self, retrieval: EvidenceRetrieval) -> None:
        self._retrieval = retrieval
        self._chunks = retrieval.chunks()
        headers = {
            # Set explicitly so no charset is ever asserted for bytes that were never decoded.
            "content-type": retrieval.media_type,
            "content-disposition": f'attachment; filename="{retrieval.filename}"',
        }
        if retrieval.content_length is not None:
            headers["content-length"] = str(retrieval.content_length)
        super().__init__(self._chunks, headers=headers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._chunks.aclose()
            self._retrieval.close()


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


@router.get(
    "/{case_id}/evidence/{evidence_id}/objects/{object_id}/content",
    response_class=Response,
    responses={
        200: {
            "description": "The exact preserved bytes, streamed in bounded chunks.",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        },
        409: {"description": "The EvidenceObject is not PRESERVED."},
        503: {"description": "Private evidence storage is unavailable."},
    },
    dependencies=[EVIDENCE_READ_ACCESS],
)
def retrieve_object_content(
    case: CaseDep,
    evidence_id: EvidenceId,
    object_id: EvidenceObjectId,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
) -> Response:
    retrieval = evidence_access.open_retrieval(
        session,
        case=case,
        evidence_id=evidence_id,
        object_id=object_id,
        principal=principal,
        storage=request.app.state.evidence_storage,
    )
    return RetrievalResponse(retrieval)


@router.post(
    "/{case_id}/evidence/{evidence_id}/objects/{object_id}/verify",
    response_model=EvidenceIntegrityVerificationOut,
    response_model_exclude_none=True,
    dependencies=[EVIDENCE_READ_ACCESS],
)
def verify_object_integrity(
    case: CaseDep,
    evidence_id: EvidenceId,
    object_id: EvidenceObjectId,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
) -> EvidenceIntegrityVerificationOut:
    return evidence_access.verify_preserved_object(
        session,
        case=case,
        evidence_id=evidence_id,
        object_id=object_id,
        principal=principal,
        storage=request.app.state.evidence_storage,
    )
