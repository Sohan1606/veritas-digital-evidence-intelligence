"""Canonical persistent entities (V1 subset).

Ownership: every case-scoped entity carries ``case_id``. Deletion is restricted at the
database level — forensic records are never cascaded away.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship

from app.db.base import ACTOR_LENGTH, PUBLIC_ID_LENGTH, Base, PublicIdMixin, TimestampedMixin
from app.db.types import utcnow
from app.domain.enums import (
    AnalysisRunState,
    AssessmentState,
    CaseState,
    ClaimState,
    EvidenceState,
    EvidenceType,
    FindingReviewStatus,
    NodeType,
    ObjectiveState,
    ObservationOrigin,
    ObservationState,
    RelationshipType,
)


def state_column[E: StrEnum](enum_cls: type[E]) -> Any:
    """String-backed enum column: validated in Python, extensible without DDL."""
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=32,
        validate_strings=True,
        values_callable=lambda cls: [member.value for member in cls],
    )


def case_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("cases.id", ondelete="RESTRICT"), index=True)


class Organization(PublicIdMixin, TimestampedMixin, Base):
    __tablename__ = "organizations"
    __public_id_prefix__ = "ORG"

    name: Mapped[str] = mapped_column(String(160), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="active")


class User(PublicIdMixin, TimestampedMixin, Base):
    __tablename__ = "users"
    __public_id_prefix__ = "USR"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="user_status"),
        UniqueConstraint("auth_provider", "auth_subject", name="uq_user_auth_linkage"),
    )

    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(16), default="active")
    password_hash: Mapped[str] = mapped_column(String(512))
    auth_provider: Mapped[str | None] = mapped_column(String(64))
    auth_subject: Mapped[str | None] = mapped_column(String(255))


class OrganizationMembership(PublicIdMixin, TimestampedMixin, Base):
    __tablename__ = "organization_memberships"
    __public_id_prefix__ = "MEM"
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")


class Role(PublicIdMixin, TimestampedMixin, Base):
    __tablename__ = "roles"
    __public_id_prefix__ = "ROLE"

    name: Mapped[str] = mapped_column(String(32), unique=True)


class RoleAssignment(PublicIdMixin, TimestampedMixin, Base):
    __tablename__ = "role_assignments"
    __public_id_prefix__ = "RLA"
    __table_args__ = (
        UniqueConstraint("membership_id", "role_id", "case_id", name="uq_role_assignment_scope"),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"), index=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), index=True
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"), index=True
    )


class UserSession(PublicIdMixin, Base):
    __tablename__ = "user_sessions"
    __public_id_prefix__ = "SES"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(index=True)
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[str] = mapped_column(String(ACTOR_LENGTH))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    bucket: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime]
    locked_until: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class Case(PublicIdMixin, TimestampedMixin, Base):
    __tablename__ = "cases"
    __public_id_prefix__ = "CASE"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    state: Mapped[CaseState] = mapped_column(state_column(CaseState), default=CaseState.OPEN)
    # Demonstration data is flagged at the case level and inherited by everything the case owns.
    is_demonstration: Mapped[bool] = mapped_column(Boolean, default=False)

    objectives: Mapped[list[Objective]] = relationship(
        back_populates="case", order_by="Objective.position"
    )


class Objective(PublicIdMixin, TimestampedMixin, Base):
    __tablename__ = "objectives"
    __public_id_prefix__ = "OBJ"

    case_id: Mapped[uuid.UUID] = case_fk()
    statement: Mapped[str] = mapped_column(Text)
    state: Mapped[ObjectiveState] = mapped_column(
        state_column(ObjectiveState), default=ObjectiveState.ACTIVE
    )
    position: Mapped[int] = mapped_column(Integer, default=0)

    case: Mapped[Case] = relationship(back_populates="objectives")


class Evidence(PublicIdMixin, TimestampedMixin, Base):
    __tablename__ = "evidence"
    __public_id_prefix__ = "EVD"

    case_id: Mapped[uuid.UUID] = case_fk()
    label: Mapped[str] = mapped_column(String(200))
    evidence_type: Mapped[EvidenceType] = mapped_column(state_column(EvidenceType))
    description: Mapped[str | None] = mapped_column(Text)
    state: Mapped[EvidenceState] = mapped_column(
        state_column(EvidenceState), default=EvidenceState.REGISTERED
    )

    profile: Mapped[EvidenceProfile | None] = relationship(back_populates="evidence")


class EvidenceProfile(TimestampedMixin, Base):
    """One profile per Evidence item. Identified through its Evidence (no separate public ID).

    Each section is a validated ``domain.values.ProfileSection`` document.
    """

    __tablename__ = "evidence_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="RESTRICT"), unique=True
    )
    identity: Mapped[dict[str, Any]]
    integrity: Mapped[dict[str, Any]]
    provenance: Mapped[dict[str, Any]]
    quality: Mapped[dict[str, Any]]
    acquisition_context: Mapped[dict[str, Any]]
    classification: Mapped[dict[str, Any]]

    evidence: Mapped[Evidence] = relationship(back_populates="profile")


class AnalysisRun(PublicIdMixin, TimestampedMixin, Base):
    """One execution of a Method against Evidence. No methods are executable in V1."""

    __tablename__ = "analysis_runs"
    __public_id_prefix__ = "ANL"

    case_id: Mapped[uuid.UUID] = case_fk()
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="RESTRICT"), index=True
    )
    method_key: Mapped[str] = mapped_column(String(64))
    method_version: Mapped[str] = mapped_column(String(32))
    state: Mapped[AnalysisRunState] = mapped_column(
        state_column(AnalysisRunState), default=AnalysisRunState.QUEUED
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]


class Observation(PublicIdMixin, TimestampedMixin, Base):
    __tablename__ = "observations"
    __public_id_prefix__ = "OBS"
    __table_args__ = (
        CheckConstraint(
            "(origin = 'analysis_run' AND analysis_run_id IS NOT NULL)"
            " OR (origin = 'manual' AND analysis_run_id IS NULL)",
            name="origin_consistency",
        ),
    )

    case_id: Mapped[uuid.UUID] = case_fk()
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="RESTRICT"), index=True
    )
    analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="RESTRICT"), index=True
    )
    origin: Mapped[ObservationOrigin] = mapped_column(state_column(ObservationOrigin))
    statement: Mapped[str] = mapped_column(Text)
    state: Mapped[ObservationState] = mapped_column(
        state_column(ObservationState), default=ObservationState.RECORDED
    )

    evidence: Mapped[Evidence] = relationship()
    analysis_run: Mapped[AnalysisRun | None] = relationship()


class Finding(PublicIdMixin, TimestampedMixin, Base):
    __tablename__ = "findings"
    __public_id_prefix__ = "FND"

    case_id: Mapped[uuid.UUID] = case_fk()
    title: Mapped[str] = mapped_column(String(200))
    statement: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text)
    limitations: Mapped[list[Any]] = mapped_column(default=list)
    # List of domain.values.AlternativeExplanation documents.
    alternative_explanations: Mapped[list[Any]] = mapped_column(default=list)
    review_status: Mapped[FindingReviewStatus] = mapped_column(
        state_column(FindingReviewStatus), default=FindingReviewStatus.UNREVIEWED
    )


class Claim(PublicIdMixin, TimestampedMixin, Base):
    __tablename__ = "claims"
    __public_id_prefix__ = "CLM"

    case_id: Mapped[uuid.UUID] = case_fk()
    statement: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(200))
    state: Mapped[ClaimState] = mapped_column(state_column(ClaimState), default=ClaimState.OPEN)


class Assessment(PublicIdMixin, TimestampedMixin, Base):
    """A human evaluation of a Claim. VERITAS never authors Assessments."""

    __tablename__ = "assessments"
    __public_id_prefix__ = "ASM"

    case_id: Mapped[uuid.UUID] = case_fk()
    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claims.id", ondelete="RESTRICT"), index=True
    )
    statement: Mapped[str] = mapped_column(Text)
    state: Mapped[AssessmentState] = mapped_column(
        state_column(AssessmentState), default=AssessmentState.DRAFT
    )
    assessed_by: Mapped[str] = mapped_column(String(ACTOR_LENGTH))

    claim: Mapped[Claim] = relationship()


class CaseRelationship(PublicIdMixin, TimestampedMixin, Base):
    """Asserted, typed edge of the Case Knowledge Graph (see ``domain.relationships``)."""

    __tablename__ = "case_relationships"
    __public_id_prefix__ = "REL"
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "source_type",
            "source_id",
            "relationship_type",
            "target_type",
            "target_id",
            name="uq_case_relationships_edge",
        ),
    )

    case_id: Mapped[uuid.UUID] = case_fk()
    source_type: Mapped[NodeType] = mapped_column(state_column(NodeType))
    source_id: Mapped[uuid.UUID]
    relationship_type: Mapped[RelationshipType] = mapped_column(state_column(RelationshipType))
    target_type: Mapped[NodeType] = mapped_column(state_column(NodeType))
    target_id: Mapped[uuid.UUID]
    rationale: Mapped[str | None] = mapped_column(Text)


class AuditEvent(PublicIdMixin, Base):
    """Append-only record of actions taken in or by VERITAS. Metadata only — never content."""

    __tablename__ = "audit_events"
    __public_id_prefix__ = "AUD"

    occurred_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(ACTOR_LENGTH))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_public_id: Mapped[str | None] = mapped_column(String(PUBLIC_ID_LENGTH))
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"), index=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)


class AuditEventImmutableError(RuntimeError):
    pass


@event.listens_for(AuditEvent, "before_update")
def _forbid_audit_update(_m: Mapper[Any], _c: Connection, _t: AuditEvent) -> None:
    raise AuditEventImmutableError("audit events are append-only")


@event.listens_for(AuditEvent, "before_delete")
def _forbid_audit_delete(_m: Mapper[Any], _c: Connection, _t: AuditEvent) -> None:
    raise AuditEventImmutableError("audit events are append-only")


# Node type -> model: the single mapping used by graph validation and projection.
NODE_MODELS: dict[NodeType, type[PublicIdMixin]] = {
    NodeType.CASE: Case,
    NodeType.EVIDENCE: Evidence,
    NodeType.OBSERVATION: Observation,
    NodeType.FINDING: Finding,
    NodeType.CLAIM: Claim,
}
