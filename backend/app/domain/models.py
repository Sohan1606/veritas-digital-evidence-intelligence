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
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy import (
    inspect as sa_inspect,
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
    EvidenceCustodyEventType,
    EvidenceObjectState,
    EvidenceState,
    EvidenceType,
    EvidenceValidationStatus,
    FindingReviewStatus,
    NodeType,
    ObjectiveState,
    ObservationOrigin,
    ObservationState,
    RelationshipType,
)
from app.domain.lifecycle import TERMINAL_RUN_STATES, InvalidRunTransitionError, require_transition


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
    objects: Mapped[list[EvidenceObject]] = relationship(back_populates="evidence")


class EvidenceObject(PublicIdMixin, TimestampedMixin, Base):
    """One immutable stored acquisition associated with a logical Evidence record."""

    __tablename__ = "evidence_objects"
    __public_id_prefix__ = "EOBJ"
    __table_args__ = (
        CheckConstraint("state IN ('QUARANTINED', 'PRESERVED', 'REJECTED')", name="object_state"),
        CheckConstraint(
            "validation_status IN ('pending', 'accepted', 'rejected')", name="validation_status"
        ),
        CheckConstraint("byte_size >= 0", name="byte_size_nonnegative"),
        CheckConstraint("sha256 IS NULL OR length(sha256) = 64", name="sha256_length"),
        CheckConstraint("sha512 IS NULL OR length(sha512) = 128", name="sha512_length"),
        CheckConstraint(
            "(upload_completed_at IS NULL AND sha256 IS NULL AND sha512 IS NULL AND byte_size = 0)"
            " OR (upload_completed_at IS NOT NULL AND sha256 IS NOT NULL AND sha512 IS NOT NULL)",
            name="upload_digest_consistency",
        ),
        CheckConstraint(
            "state != 'PRESERVED' OR (upload_completed_at IS NOT NULL "
            "AND detected_media_type IS NOT NULL AND validation_status = 'accepted' "
            "AND preserved_at IS NOT NULL AND preserved_by IS NOT NULL)",
            name="preserved_metadata_consistency",
        ),
        CheckConstraint(
            "state != 'REJECTED' OR validation_status = 'rejected'",
            name="rejected_validation_consistency",
        ),
        UniqueConstraint("storage_key", name="uq_evidence_objects_storage_key"),
        # Referenced by the composite key that gives every Analysis Run exact, database-enforced
        # provenance (see AnalysisRun). It adds no rule to the object itself.
        Index("uq_evidence_objects_provenance", "id", "evidence_id", "case_id", unique=True),
    )

    case_id: Mapped[uuid.UUID] = case_fk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="RESTRICT"), index=True
    )
    storage_key: Mapped[str] = mapped_column(String(64))
    original_filename: Mapped[str] = mapped_column(String(255))
    declared_media_type: Mapped[str] = mapped_column(String(127))
    detected_media_type: Mapped[str | None] = mapped_column(String(127))
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))
    sha512: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[EvidenceObjectState] = mapped_column(
        state_column(EvidenceObjectState), default=EvidenceObjectState.QUARANTINED
    )
    validation_status: Mapped[EvidenceValidationStatus] = mapped_column(
        state_column(EvidenceValidationStatus), default=EvidenceValidationStatus.PENDING
    )
    validation_note: Mapped[str | None] = mapped_column(String(500))
    acquired_at: Mapped[datetime] = mapped_column(default=utcnow)
    acquired_by: Mapped[str] = mapped_column(String(ACTOR_LENGTH))
    upload_completed_at: Mapped[datetime | None]
    preserved_at: Mapped[datetime | None]
    preserved_by: Mapped[str | None] = mapped_column(String(ACTOR_LENGTH))

    evidence: Mapped[Evidence] = relationship(back_populates="objects")


class EvidenceCustodyEvent(PublicIdMixin, TimestampedMixin, Base):
    """Append-only record of the two custody events implemented in V2.1."""

    __tablename__ = "evidence_custody_events"
    __public_id_prefix__ = "CST"
    __table_args__ = (
        CheckConstraint("event_type IN ('RECEIVED', 'PRESERVED')", name="custody_event_type"),
        CheckConstraint(
            "(event_type = 'RECEIVED' AND from_state IS NULL AND to_state = 'QUARANTINED')"
            " OR (event_type = 'PRESERVED' AND from_state = 'QUARANTINED'"
            " AND to_state = 'PRESERVED')",
            name="custody_state_transition",
        ),
    )

    case_id: Mapped[uuid.UUID] = case_fk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="RESTRICT"), index=True
    )
    evidence_object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_objects.id", ondelete="RESTRICT"), index=True
    )
    event_type: Mapped[EvidenceCustodyEventType] = mapped_column(
        state_column(EvidenceCustodyEventType)
    )
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32))
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    counterparty_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    reason: Mapped[str | None] = mapped_column(String(500))
    occurred_at: Mapped[datetime] = mapped_column(default=utcnow)
    request_id: Mapped[str] = mapped_column(String(64), default="unknown")


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
    """One execution of a versioned Method against exactly one PRESERVED EvidenceObject.

    This is the canonical execution record (there is no separate "examination" entity). The
    request columns (case, evidence, evidence object, method, parameters, idempotency key,
    fingerprint) never change after creation; only the lifecycle columns do, and only along
    ``domain.lifecycle``. A finished run is final; a retry is a new run.

    ``started_at`` is set by each claim and cleared when a run is returned to the queue, so it
    also identifies the claim that currently owns a RUNNING run (a stale worker cannot publish).
    ``completed_at`` is the time the run reached any terminal state.
    """

    __tablename__ = "analysis_runs"
    __public_id_prefix__ = "ANL"
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="run_state",
        ),
        CheckConstraint(
            "(state = 'queued' AND started_at IS NULL AND completed_at IS NULL"
            " AND last_heartbeat_at IS NULL AND cancel_requested_at IS NULL)"
            " OR (state = 'running' AND started_at IS NOT NULL AND completed_at IS NULL"
            " AND last_heartbeat_at IS NOT NULL)"
            " OR (state = 'completed' AND started_at IS NOT NULL AND completed_at IS NOT NULL"
            " AND cancel_requested_at IS NULL)"
            " OR (state = 'failed' AND started_at IS NOT NULL AND completed_at IS NOT NULL)"
            " OR (state = 'cancelled' AND completed_at IS NOT NULL)",
            name="lifecycle_consistency",
        ),
        CheckConstraint(
            "(state = 'failed' AND failure_code IS NOT NULL AND failure_message IS NOT NULL)"
            " OR (state != 'failed' AND failure_code IS NULL AND failure_message IS NULL)",
            name="failure_consistency",
        ),
        CheckConstraint("length(request_fingerprint) = 64", name="fingerprint_length"),
        # A repeated HTTP request (same principal, Case and key) can never create a second run.
        # NULL keys (internal, non-HTTP creation) never collide with each other.
        UniqueConstraint(
            "case_id", "created_by", "idempotency_key", name="uq_analysis_runs_idempotency"
        ),
        # Exact provenance, enforced by the database: the object must belong to this Evidence
        # and this Case. The service checks the same relationships before it ever inserts.
        ForeignKeyConstraint(
            ["evidence_object_id", "evidence_id", "case_id"],
            ["evidence_objects.id", "evidence_objects.evidence_id", "evidence_objects.case_id"],
            name="fk_analysis_runs_evidence_object_id_evidence_objects",
            ondelete="RESTRICT",
        ),
        Index("ix_analysis_runs_state_created_at", "state", "created_at"),
    )

    case_id: Mapped[uuid.UUID] = case_fk()
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="RESTRICT"), index=True
    )
    evidence_object_id: Mapped[uuid.UUID] = mapped_column(index=True)
    method_key: Mapped[str] = mapped_column(String(64))
    method_version: Mapped[str] = mapped_column(String(32))
    state: Mapped[AnalysisRunState] = mapped_column(
        state_column(AnalysisRunState), default=AnalysisRunState.QUEUED
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    cancel_requested_at: Mapped[datetime | None]
    last_heartbeat_at: Mapped[datetime | None]
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(String(500))


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


class EvidenceObjectImmutableError(RuntimeError):
    """Raised when a stored-object lifecycle or immutable record is rewritten."""


class EvidenceCustodyEventImmutableError(RuntimeError):
    """Raised when append-only custody history is changed through the ORM."""


class AuditEventImmutableError(RuntimeError):
    pass


class AnalysisRunImmutableError(RuntimeError):
    """Raised when execution history is rewritten, deleted, or its lifecycle is violated."""


_EVIDENCE_QUARANTINE_MUTABLE = frozenset(
    {
        "byte_size",
        "sha256",
        "sha512",
        "upload_completed_at",
        "detected_media_type",
        "state",
        "validation_status",
        "validation_note",
        "preserved_at",
        "preserved_by",
        "updated_at",
        "updated_by",
    }
)


@event.listens_for(EvidenceObject, "before_update")
def _protect_evidence_object(_m: Mapper[Any], _c: Connection, target: EvidenceObject) -> None:
    state = sa_inspect(target)
    previous = state.attrs.state.history.deleted
    old_state = previous[0] if previous else target.state
    new_state = target.state
    if old_state in (EvidenceObjectState.PRESERVED, EvidenceObjectState.REJECTED):
        raise EvidenceObjectImmutableError("final evidence objects are immutable")
    if new_state not in (
        EvidenceObjectState.QUARANTINED,
        EvidenceObjectState.PRESERVED,
        EvidenceObjectState.REJECTED,
    ):
        raise EvidenceObjectImmutableError("unsupported evidence object state transition")
    if new_state is not old_state and (
        old_state is not EvidenceObjectState.QUARANTINED
        or new_state not in (EvidenceObjectState.PRESERVED, EvidenceObjectState.REJECTED)
    ):
        raise EvidenceObjectImmutableError("unsupported evidence object state transition")
    changed = {attribute.key for attribute in state.attrs if attribute.history.has_changes()}
    if changed - _EVIDENCE_QUARANTINE_MUTABLE:
        raise EvidenceObjectImmutableError("evidence object metadata is immutable")
    for digest_name in ("sha256", "sha512", "upload_completed_at"):
        history = state.attrs[digest_name].history
        if history.deleted and history.deleted[0] is not None:
            raise EvidenceObjectImmutableError("completed upload metadata is immutable")


@event.listens_for(EvidenceObject, "before_delete")
def _forbid_evidence_object_delete(_m: Mapper[Any], _c: Connection, _t: EvidenceObject) -> None:
    raise EvidenceObjectImmutableError("evidence objects cannot be deleted")


# What a run IS (and was asked to do) never changes; only its lifecycle columns do.
_ANALYSIS_RUN_IMMUTABLE = frozenset(
    {
        "id",
        "public_id",
        "case_id",
        "evidence_id",
        "evidence_object_id",
        "method_key",
        "method_version",
        "parameters",
        "idempotency_key",
        "request_fingerprint",
        "created_at",
        "created_by",
    }
)


@event.listens_for(AnalysisRun, "before_update")
def _guard_analysis_run(_m: Mapper[Any], _c: Connection, target: AnalysisRun) -> None:
    """ORM-level guard. The coordinator's compare-and-set updates are Core statements, so the
    same rules are enforced there (``domain.lifecycle``) and, on PostgreSQL, by a trigger."""
    state = sa_inspect(target)
    changed = {attribute.key for attribute in state.attrs if attribute.history.has_changes()}
    if not changed:
        return
    if changed & _ANALYSIS_RUN_IMMUTABLE:
        raise AnalysisRunImmutableError("analysis run identity and execution request are immutable")
    previous = state.attrs.state.history.deleted
    old_state = previous[0] if previous else target.state
    if old_state in TERMINAL_RUN_STATES:
        raise AnalysisRunImmutableError("finished analysis runs are immutable")
    try:
        if target.state is not old_state:
            require_transition(old_state, target.state)
    except InvalidRunTransitionError as exc:
        raise AnalysisRunImmutableError(str(exc)) from None


@event.listens_for(AnalysisRun, "before_delete")
def _forbid_analysis_run_delete(_m: Mapper[Any], _c: Connection, _t: AnalysisRun) -> None:
    raise AnalysisRunImmutableError("analysis runs are execution history and cannot be deleted")


@event.listens_for(EvidenceCustodyEvent, "before_update")
def _forbid_custody_update(_m: Mapper[Any], _c: Connection, _t: EvidenceCustodyEvent) -> None:
    raise EvidenceCustodyEventImmutableError("custody events are append-only")


@event.listens_for(EvidenceCustodyEvent, "before_delete")
def _forbid_custody_delete(_m: Mapper[Any], _c: Connection, _t: EvidenceCustodyEvent) -> None:
    raise EvidenceCustodyEventImmutableError("custody events are append-only")


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
