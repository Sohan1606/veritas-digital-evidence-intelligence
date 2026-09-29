"""Read-side projections of canonical entities into API read models.

All functions take an already-authorized ``Case`` (see :func:`get_readable_case`), so the
access boundary is enforced in exactly one place.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.security import Principal, has_case_capability
from app.domain.enums import FindingReviewStatus, NodeType, RelationshipType
from app.domain.models import (
    AnalysisRun,
    Assessment,
    AuditEvent,
    Case,
    CaseRelationship,
    Claim,
    Evidence,
    EvidenceProfile,
    Finding,
    Observation,
)
from app.domain.values import PROFILE_STATUS_DEFINITIONS, ProfileSection
from app.schemas import (
    DEMONSTRATION_NOTICE,
    AnalysisRunOut,
    AssessmentOut,
    AuditEventOut,
    CaseCounts,
    CaseDetail,
    CaseSummary,
    ClaimLink,
    ClaimOut,
    EvidenceLink,
    EvidenceOut,
    EvidenceProfileOut,
    FindingLink,
    FindingOut,
    ObjectiveOut,
    ObservationBasis,
    ProfileSectionOut,
)

# --- Cases ------------------------------------------------------------------------------


def get_readable_case(session: Session, principal: Principal, case_public_id: str) -> Case:
    case = session.execute(
        select(Case).where(Case.public_id == case_public_id)
    ).scalar_one_or_none()
    if case is None or not has_case_capability(session, principal, case, "case:read"):
        # Unreadable cases are indistinguishable from nonexistent ones.
        from app.services.records import record_security_event

        record_security_event(
            session,
            actor=principal.subject,
            action="authorization.denied",
            entity_type="case",
            entity_public_id=case.public_id if case is not None else None,
            details={"capability": "case:read"},
            organization_id=case.organization_id if case is not None else None,
        )
        session.commit()
        raise NotFoundError("Case was not found")
    return case


def _count(session: Session, model: Any, case_id: uuid.UUID, *extra: Any) -> int:
    stmt = select(func.count()).select_from(model).where(model.case_id == case_id, *extra)
    return int(session.execute(stmt).scalar_one())


def case_counts(session: Session, case: Case) -> CaseCounts:
    return CaseCounts(
        evidence=_count(session, Evidence, case.id),
        analysis_runs=_count(session, AnalysisRun, case.id),
        observations=_count(session, Observation, case.id),
        findings=_count(session, Finding, case.id),
        findings_awaiting_review=_count(
            session, Finding, case.id, Finding.review_status == FindingReviewStatus.UNREVIEWED
        ),
        claims=_count(session, Claim, case.id),
        assessments=_count(session, Assessment, case.id),
        relationships=_count(session, CaseRelationship, case.id),
    )


def _summary_fields(session: Session, case: Case) -> dict[str, Any]:
    return {
        "id": case.public_id,
        "title": case.title,
        "state": case.state,
        "demonstration": case.is_demonstration,
        "notice": DEMONSTRATION_NOTICE if case.is_demonstration else None,
        "counts": case_counts(session, case),
        "created_at": case.created_at,
        "updated_at": case.updated_at,
    }


def list_cases(session: Session, principal: Principal) -> list[CaseSummary]:
    stmt = select(Case).order_by(Case.created_at, Case.public_id)
    if principal.kind == "demonstration_viewer":
        stmt = stmt.where(Case.is_demonstration.is_(True))
    cases = session.execute(stmt).scalars().all()
    return [
        CaseSummary(**_summary_fields(session, c))
        for c in cases
        if has_case_capability(session, principal, c, "case:read")
    ]


def case_detail(session: Session, case: Case) -> CaseDetail:
    return CaseDetail(
        **_summary_fields(session, case),
        summary=case.summary,
        objectives=[
            ObjectiveOut(id=o.public_id, statement=o.statement, state=o.state, position=o.position)
            for o in case.objectives
        ],
        created_by=case.created_by,
    )


# --- Evidence ---------------------------------------------------------------------------


def _evidence_out(evidence: Evidence, profile_recorded: bool) -> EvidenceOut:
    return EvidenceOut(
        id=evidence.public_id,
        label=evidence.label,
        evidence_type=evidence.evidence_type,
        description=evidence.description,
        state=evidence.state,
        profile_recorded=profile_recorded,
        created_at=evidence.created_at,
    )


def list_evidence(session: Session, case: Case) -> list[EvidenceOut]:
    rows = session.execute(
        select(Evidence, EvidenceProfile.id)
        .outerjoin(EvidenceProfile, EvidenceProfile.evidence_id == Evidence.id)
        .where(Evidence.case_id == case.id)
        .order_by(Evidence.created_at, Evidence.public_id)
    ).all()
    return [_evidence_out(evidence, profile_id is not None) for evidence, profile_id in rows]


def evidence_profile(session: Session, case: Case, evidence_public_id: str) -> EvidenceProfileOut:
    evidence = session.execute(
        select(Evidence).where(
            Evidence.public_id == evidence_public_id, Evidence.case_id == case.id
        )
    ).scalar_one_or_none()
    if evidence is None:
        raise NotFoundError(f"Evidence {evidence_public_id} was not found in {case.public_id}")
    profile = evidence.profile
    if profile is None:
        raise NotFoundError(f"No Evidence Profile has been recorded for {evidence_public_id}")

    def section(document: dict[str, Any]) -> ProfileSectionOut:
        parsed = ProfileSection.model_validate(document)
        return ProfileSectionOut(
            status=parsed.status,
            attributes=list(parsed.attributes),
            note=parsed.note,
        )

    return EvidenceProfileOut(
        evidence=_evidence_out(evidence, True),
        identity=section(profile.identity),
        integrity=section(profile.integrity),
        provenance=section(profile.provenance),
        quality=section(profile.quality),
        acquisition_context=section(profile.acquisition_context),
        classification=section(profile.classification),
        recorded_by=profile.updated_by,
        updated_at=profile.updated_at,
        status_definitions=dict(PROFILE_STATUS_DEFINITIONS),
    )


# --- Examination ------------------------------------------------------------------------


def list_analysis_runs(session: Session, case: Case) -> list[AnalysisRunOut]:
    rows = session.execute(
        select(AnalysisRun, Evidence.public_id)
        .join(Evidence, Evidence.id == AnalysisRun.evidence_id)
        .where(AnalysisRun.case_id == case.id)
        .order_by(AnalysisRun.created_at)
    ).all()
    return [
        AnalysisRunOut(
            id=run.public_id,
            evidence_id=evidence_id,
            method_key=run.method_key,
            method_version=run.method_version,
            state=run.state,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )
        for run, evidence_id in rows
    ]


# --- Findings & Claims ------------------------------------------------------------------


def _edges(session: Session, case: Case) -> list[CaseRelationship]:
    return list(
        session.execute(
            select(CaseRelationship)
            .where(CaseRelationship.case_id == case.id)
            .order_by(CaseRelationship.created_at, CaseRelationship.public_id)
        ).scalars()
    )


def _observation_basis(session: Session, case: Case) -> dict[uuid.UUID, ObservationBasis]:
    rows = session.execute(
        select(Observation, Evidence, AnalysisRun.public_id)
        .join(Evidence, Evidence.id == Observation.evidence_id)
        .outerjoin(AnalysisRun, AnalysisRun.id == Observation.analysis_run_id)
        .where(Observation.case_id == case.id)
    ).all()
    return {
        obs.id: ObservationBasis(
            id=obs.public_id,
            statement=obs.statement,
            origin=obs.origin,
            analysis_run_id=run_id,
            evidence_id=evidence.public_id,
            evidence_label=evidence.label,
            recorded_by=obs.created_by,
        )
        for obs, evidence, run_id in rows
    }


def list_findings(session: Session, case: Case) -> list[FindingOut]:
    findings = (
        session.execute(
            select(Finding)
            .where(Finding.case_id == case.id)
            .order_by(Finding.created_at, Finding.public_id)
        )
        .scalars()
        .all()
    )
    claims = {
        c.id: c for c in session.execute(select(Claim).where(Claim.case_id == case.id)).scalars()
    }
    observations = _observation_basis(session, case)

    basis: dict[uuid.UUID, list[ObservationBasis]] = defaultdict(list)
    related: dict[uuid.UUID, list[ClaimLink]] = defaultdict(list)
    for edge in _edges(session, case):
        if edge.source_type is not NodeType.FINDING:
            continue
        if (
            edge.relationship_type is RelationshipType.DERIVED_FROM
            and edge.target_type is NodeType.OBSERVATION
        ):
            if edge.target_id in observations:
                basis[edge.source_id].append(observations[edge.target_id])
        elif edge.target_type is NodeType.CLAIM and edge.target_id in claims:
            claim = claims[edge.target_id]
            related[edge.source_id].append(
                ClaimLink(
                    id=claim.public_id,
                    statement=claim.statement,
                    relationship=edge.relationship_type,
                    rationale=edge.rationale,
                )
            )

    return [
        FindingOut(
            id=f.public_id,
            title=f.title,
            statement=f.statement,
            method=f.method,
            limitations=list(f.limitations),
            alternative_explanations=f.alternative_explanations,
            review_status=f.review_status,
            evidence_basis=basis[f.id],
            related_claims=related[f.id],
            created_by=f.created_by,
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in findings
    ]


def get_finding(session: Session, case: Case, finding_public_id: str) -> FindingOut:
    for finding in list_findings(session, case):
        if finding.id == finding_public_id:
            return finding
    raise NotFoundError(f"Finding {finding_public_id} was not found in {case.public_id}")


def list_claims(session: Session, case: Case) -> list[ClaimOut]:
    claims = (
        session.execute(
            select(Claim)
            .where(Claim.case_id == case.id)
            .order_by(Claim.created_at, Claim.public_id)
        )
        .scalars()
        .all()
    )
    findings = {
        f.id: f
        for f in session.execute(select(Finding).where(Finding.case_id == case.id)).scalars()
    }
    evidence = {
        e.id: e
        for e in session.execute(select(Evidence).where(Evidence.case_id == case.id)).scalars()
    }
    assessments: dict[uuid.UUID, list[AssessmentOut]] = defaultdict(list)
    for a in session.execute(
        select(Assessment).where(Assessment.case_id == case.id).order_by(Assessment.created_at)
    ).scalars():
        assessments[a.claim_id].append(
            AssessmentOut(
                id=a.public_id,
                claim_id=a.claim.public_id,
                statement=a.statement,
                state=a.state,
                assessed_by=a.assessed_by,
                created_at=a.created_at,
            )
        )

    finding_links: dict[uuid.UUID, list[FindingLink]] = defaultdict(list)
    evidence_links: dict[uuid.UUID, list[EvidenceLink]] = defaultdict(list)
    for edge in _edges(session, case):
        if edge.target_type is NodeType.CLAIM and edge.source_type is NodeType.FINDING:
            f = findings.get(edge.source_id)
            if f:
                finding_links[edge.target_id].append(
                    FindingLink(
                        id=f.public_id,
                        title=f.title,
                        relationship=edge.relationship_type,
                        review_status=f.review_status,
                    )
                )
        elif edge.source_type is NodeType.CLAIM and edge.target_type is NodeType.EVIDENCE:
            e = evidence.get(edge.target_id)
            if e:
                evidence_links[edge.source_id].append(EvidenceLink(id=e.public_id, label=e.label))

    return [
        ClaimOut(
            id=c.public_id,
            statement=c.statement,
            source=c.source,
            state=c.state,
            related_findings=finding_links[c.id],
            referenced_evidence=evidence_links[c.id],
            assessments=assessments[c.id],
            created_at=c.created_at,
        )
        for c in claims
    ]


# --- Audit ------------------------------------------------------------------------------


def list_audit_events(session: Session, case: Case, limit: int) -> list[AuditEventOut]:
    events = session.execute(
        select(AuditEvent)
        .where(AuditEvent.case_id == case.id)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.public_id.desc())
        .limit(limit)
    ).scalars()
    return [
        AuditEventOut(
            id=e.public_id,
            occurred_at=e.occurred_at,
            actor=e.actor,
            action=e.action,
            entity_type=e.entity_type,
            entity_id=e.entity_public_id,
            details=e.details,
        )
        for e in events
    ]
