"""Write-side domain commands for canonical entities.

Every command validates domain rules, persists the entity, and appends an Audit Event
in the same transaction. Commands never commit; the caller owns the transaction.

V1 exposes no write API — these commands are used by the demonstration seed and tests,
and are the single place future write endpoints (V2+) must go through.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainRuleViolation
from app.core.logging import request_id_var
from app.domain.enums import (
    EvidenceAuditAction,
    EvidenceType,
    NodeType,
    ObservationOrigin,
    RelationshipType,
)
from app.domain.models import (
    NODE_MODELS,
    AnalysisRun,
    Assessment,
    AuditEvent,
    Case,
    CaseRelationship,
    Claim,
    Evidence,
    EvidenceProfile,
    Finding,
    Objective,
    Observation,
    Organization,
    OrganizationMembership,
    User,
)
from app.domain.relationships import is_assertable
from app.domain.values import PROFILE_SECTIONS, AlternativeExplanation, ProfileSection

_MODEL_NODE_TYPES = {model: node_type for node_type, model in NODE_MODELS.items()}


def record_audit_event(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_public_id: str | None,
    case: Case | None,
    details: Mapping[str, Any] | None = None,
    organization_id: UUID | None = None,
) -> AuditEvent:
    """Append an Audit Event. ``details`` must contain identifiers/metadata only, never content."""
    event = AuditEvent(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_public_id=entity_public_id,
        case_id=case.id if case else None,
        organization_id=case.organization_id if case else organization_id,
        request_id=request_id_var.get(),
        details=dict(details or {}),
    )
    session.add(event)
    session.flush()
    return event


def _persist(session: Session, entity: Any) -> None:
    session.add(entity)
    session.flush()


def create_case(
    session: Session, *, actor: str, title: str, summary: str | None, is_demonstration: bool
) -> Case:
    organization = (
        session.execute(
            select(Organization)
            .where(Organization.status == "active")
            .order_by(Organization.public_id)
        )
        .scalars()
        .first()
    )
    if organization is None:
        raise DomainRuleViolation("No active Organization is provisioned")
    case = Case(
        organization_id=organization.id,
        title=title,
        summary=summary,
        is_demonstration=is_demonstration,
        created_by=actor,
        updated_by=actor,
    )
    _persist(session, case)
    record_audit_event(
        session,
        actor=actor,
        action="case.created",
        entity_type="case",
        entity_public_id=case.public_id,
        case=case,
        details={"is_demonstration": is_demonstration},
    )
    return case


def add_objective(session: Session, *, actor: str, case: Case, statement: str) -> Objective:
    position = len(case.objectives)
    objective = Objective(
        case_id=case.id, statement=statement, position=position, created_by=actor, updated_by=actor
    )
    _persist(session, objective)
    session.refresh(case, attribute_names=["objectives"])
    record_audit_event(
        session,
        actor=actor,
        action="objective.added",
        entity_type="objective",
        entity_public_id=objective.public_id,
        case=case,
    )
    return objective


def register_evidence(
    session: Session,
    *,
    actor: str,
    case: Case,
    label: str,
    evidence_type: EvidenceType,
    description: str | None,
) -> Evidence:
    evidence = Evidence(
        case_id=case.id,
        label=label,
        evidence_type=evidence_type,
        description=description,
        created_by=actor,
        updated_by=actor,
    )
    _persist(session, evidence)
    record_audit_event(
        session,
        actor=actor,
        action=EvidenceAuditAction.REGISTERED.value,
        entity_type="evidence",
        entity_public_id=evidence.public_id,
        case=case,
        details={"evidence_type": evidence_type.value},
    )
    return evidence


def set_evidence_profile(
    session: Session,
    *,
    actor: str,
    case: Case,
    evidence: Evidence,
    sections: Mapping[str, ProfileSection],
) -> EvidenceProfile:
    if evidence.case_id != case.id:
        raise DomainRuleViolation("evidence does not belong to this case")
    if set(sections) != set(PROFILE_SECTIONS):
        raise DomainRuleViolation(
            f"an Evidence Profile requires exactly the sections {PROFILE_SECTIONS}"
        )
    documents = {name: sections[name].model_dump(mode="json") for name in PROFILE_SECTIONS}
    profile = evidence.profile
    if profile is None:
        profile = EvidenceProfile(
            evidence_id=evidence.id, created_by=actor, updated_by=actor, **documents
        )
    else:
        for name, document in documents.items():
            setattr(profile, name, document)
        profile.updated_by = actor
    _persist(session, profile)
    record_audit_event(
        session,
        actor=actor,
        action="evidence_profile.recorded",
        entity_type="evidence_profile",
        entity_public_id=evidence.public_id,
        case=case,
        details={name: sections[name].status.value for name in PROFILE_SECTIONS},
    )
    return profile


def record_observation(
    session: Session,
    *,
    actor: str,
    case: Case,
    evidence: Evidence,
    statement: str,
    analysis_run: AnalysisRun | None = None,
) -> Observation:
    if evidence.case_id != case.id:
        raise DomainRuleViolation("evidence does not belong to this case")
    if analysis_run is not None and analysis_run.evidence_id != evidence.id:
        raise DomainRuleViolation("analysis run did not examine this evidence")
    observation = Observation(
        case_id=case.id,
        evidence_id=evidence.id,
        analysis_run_id=analysis_run.id if analysis_run else None,
        origin=ObservationOrigin.ANALYSIS_RUN if analysis_run else ObservationOrigin.MANUAL,
        statement=statement,
        created_by=actor,
        updated_by=actor,
    )
    _persist(session, observation)
    record_audit_event(
        session,
        actor=actor,
        action="observation.recorded",
        entity_type="observation",
        entity_public_id=observation.public_id,
        case=case,
        details={"evidence": evidence.public_id, "origin": observation.origin.value},
    )
    return observation


def record_finding(
    session: Session,
    *,
    actor: str,
    case: Case,
    title: str,
    statement: str,
    method: str,
    limitations: Sequence[str],
    alternative_explanations: Sequence[AlternativeExplanation],
) -> Finding:
    if not limitations:
        raise DomainRuleViolation("a Finding must state at least one limitation")
    finding = Finding(
        case_id=case.id,
        title=title,
        statement=statement,
        method=method,
        limitations=list(limitations),
        alternative_explanations=[a.model_dump(mode="json") for a in alternative_explanations],
        created_by=actor,
        updated_by=actor,
    )
    _persist(session, finding)
    record_audit_event(
        session,
        actor=actor,
        action="finding.recorded",
        entity_type="finding",
        entity_public_id=finding.public_id,
        case=case,
    )
    return finding


def record_claim(session: Session, *, actor: str, case: Case, statement: str, source: str) -> Claim:
    claim = Claim(
        case_id=case.id, statement=statement, source=source, created_by=actor, updated_by=actor
    )
    _persist(session, claim)
    record_audit_event(
        session,
        actor=actor,
        action="claim.recorded",
        entity_type="claim",
        entity_public_id=claim.public_id,
        case=case,
    )
    return claim


def _node_type(entity: object) -> NodeType:
    node_type = next((nt for model, nt in _MODEL_NODE_TYPES.items() if type(entity) is model), None)
    if node_type is None:
        raise DomainRuleViolation(f"{type(entity).__name__} is not a Case Knowledge Graph node")
    return node_type


def assert_relationship(
    session: Session,
    *,
    actor: str,
    case: Case,
    source: Finding | Claim,
    relationship_type: RelationshipType,
    target: Evidence | Observation | Finding | Claim,
    rationale: str | None = None,
) -> CaseRelationship:
    source_type, target_type = _node_type(source), _node_type(target)
    if not is_assertable(source_type, relationship_type, target_type):
        raise DomainRuleViolation(
            f"'{source_type} {relationship_type} {target_type}' is not a permitted relationship"
        )
    if source.case_id != case.id or target.case_id != case.id:
        raise DomainRuleViolation("relationships may only connect records of the same case")
    if source.id == target.id:
        raise DomainRuleViolation("a record cannot be related to itself")
    edge = CaseRelationship(
        case_id=case.id,
        source_type=source_type,
        source_id=source.id,
        relationship_type=relationship_type,
        target_type=target_type,
        target_id=target.id,
        rationale=rationale,
        created_by=actor,
        updated_by=actor,
    )
    _persist(session, edge)
    record_audit_event(
        session,
        actor=actor,
        action="relationship.asserted",
        entity_type="relationship",
        entity_public_id=edge.public_id,
        case=case,
        details={
            "source": source.public_id,
            "relationship": relationship_type.value,
            "target": target.public_id,
        },
    )
    return edge


def record_assessment(
    session: Session, *, actor: str, case: Case, claim: Claim, statement: str
) -> Assessment:
    if claim.case_id != case.id:
        raise DomainRuleViolation("claim does not belong to this case")
    assessment = Assessment(
        case_id=case.id,
        claim_id=claim.id,
        statement=statement,
        assessed_by=actor,
        created_by=actor,
        updated_by=actor,
    )
    _persist(session, assessment)
    record_audit_event(
        session,
        actor=actor,
        action="assessment.drafted",
        entity_type="assessment",
        entity_public_id=assessment.public_id,
        case=case,
        details={"claim": claim.public_id},
    )
    return assessment


def organization_id_for_user(session: Session, user_id: UUID) -> UUID | None:
    """Return an organization only when the active identity has one unambiguous scope."""
    organization_ids = (
        session.execute(
            select(OrganizationMembership.organization_id).where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.status == "active",
            )
        )
        .scalars()
        .unique()
        .all()
    )
    return organization_ids[0] if len(organization_ids) == 1 else None


def record_security_event(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_public_id: str | None = None,
    details: Mapping[str, Any] | None = None,
    organization_id: UUID | None = None,
) -> AuditEvent:
    """Append a security event to canonical AuditEvent with explicit organization scope.

    User-attributed events inherit scope only when exactly one active membership exists.
    Unrecognized/system events remain NULL-scoped and are intentionally hidden from
    organization-scoped security-audit readers.
    """
    if organization_id is None and actor.startswith("USR-"):
        user_id = session.execute(
            select(User.id).where(User.public_id == actor)
        ).scalar_one_or_none()
        if user_id is not None:
            organization_id = organization_id_for_user(session, user_id)
    return record_audit_event(
        session,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_public_id=entity_public_id,
        case=None,
        details=details,
        organization_id=organization_id,
    )
