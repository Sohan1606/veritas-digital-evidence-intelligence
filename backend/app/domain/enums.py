"""Canonical lifecycle states and vocabularies.

Values are stored as plain strings (no native database enums) so that future versions
can extend a lifecycle with a data-only migration. Only states that have a defined
meaning today are listed; new states are added when the capability that produces them exists.
"""

from __future__ import annotations

from enum import StrEnum


class CaseState(StrEnum):
    OPEN = "open"
    ON_HOLD = "on_hold"
    CLOSED = "closed"


class ObjectiveState(StrEnum):
    ACTIVE = "active"
    MET = "met"
    WITHDRAWN = "withdrawn"


class EvidenceType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    EMAIL = "email"
    MESSAGE_EXPORT = "message_export"
    OTHER = "other"


class EvidenceState(StrEnum):
    # Logical evidence lifecycle; stored-object lifecycle is defined separately below.
    REGISTERED = "registered"
    WITHDRAWN = "withdrawn"


class EvidenceObjectState(StrEnum):
    QUARANTINED = "QUARANTINED"
    PRESERVED = "PRESERVED"
    REJECTED = "REJECTED"


class EvidenceValidationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class EvidenceCustodyEventType(StrEnum):
    RECEIVED = "RECEIVED"
    PRESERVED = "PRESERVED"


class EvidenceAuditAction(StrEnum):
    REGISTERED = "evidence.registered"
    OBJECT_RECEIVED = "evidence.object.received"
    UPLOAD_COMPLETED = "evidence.upload.completed"
    PRESERVED = "evidence.preserved"
    REJECTED = "evidence.rejected"
    OBJECT_RETRIEVED = "evidence.object.retrieved"
    OBJECT_INTEGRITY_VERIFIED = "evidence.object.integrity_verified"


class EvidenceIntegrityResult(StrEnum):
    """Outcome of one independent integrity verification of a PRESERVED EvidenceObject.

    MATCH: the recomputed byte count, SHA-256 and SHA-512 equal the immutable intake values.
    MISMATCH: every byte was read and at least one recomputed value differs.
    UNAVAILABLE: the preserved bytes could not be read, so no comparison was made. A storage
    failure is never reported as MISMATCH, and an authorization failure is never UNAVAILABLE
    (authorization is decided before any storage access).

    A MATCH is an integrity comparison only. It is not an authenticity determination.
    """

    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNAVAILABLE = "UNAVAILABLE"


class ProfileStatus(StrEnum):
    """Status of one Evidence Profile section. Meanings are enforced in ``domain.profile``."""

    VERIFIED = "verified"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    NOT_AVAILABLE = "not_available"


class AttributeBasis(StrEnum):
    """How a profile attribute value came to be known."""

    COMPUTED = "computed"  # produced by a recorded, reproducible VERITAS procedure
    DECLARED = "declared"  # asserted by a submitter or the acquisition record; not checked


class AnalysisRunState(StrEnum):
    """Lifecycle of one Analysis Run; permitted transitions are owned by ``domain.lifecycle``."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExaminationAuditAction(StrEnum):
    """Canonical AuditEvent actions of the V2.3 examination workflow (metadata only)."""

    RUN_CREATED = "examination.run.created"
    RUN_STARTED = "examination.run.started"
    RUN_COMPLETED = "examination.run.completed"
    RUN_FAILED = "examination.run.failed"
    RUN_CANCELLED = "examination.run.cancelled"
    RUN_RETRIED = "examination.run.retried"
    # Accountability for a user request that is only a request until the worker acts on it.
    RUN_CANCEL_REQUESTED = "examination.run.cancel_requested"
    # A RUNNING run returned to QUEUED because its worker stopped (stale heartbeat or shutdown).
    RUN_RECOVERED = "examination.run.recovered"


class ExaminationFailureCode(StrEnum):
    """Why a run FAILED. Each code has one fixed, sanitized message (``examination.contracts``)."""

    METHOD_UNAVAILABLE = "method_unavailable"
    NOT_ELIGIBLE = "not_eligible"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    INTEGRITY_MISMATCH = "integrity_mismatch"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    EXECUTION_FAILED = "execution_failed"


class ObservationOrigin(StrEnum):
    ANALYSIS_RUN = "analysis_run"  # emitted by an Analysis Run (requires analysis_run_id)
    MANUAL = "manual"  # recorded by a person or process without an Analysis Run


class ObservationState(StrEnum):
    RECORDED = "recorded"
    SUPERSEDED = "superseded"


class FindingReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    UNDER_REVIEW = "under_review"
    ACCEPTED = "accepted"
    CHALLENGED = "challenged"
    REJECTED = "rejected"


class AlternativeExplanationStatus(StrEnum):
    OPEN = "open"  # not excluded by any recorded basis
    EXCLUDED = "excluded"  # excluded, with a recorded basis


class ClaimState(StrEnum):
    OPEN = "open"
    UNDER_ASSESSMENT = "under_assessment"
    ASSESSED = "assessed"
    WITHDRAWN = "withdrawn"


class AssessmentState(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    WITHDRAWN = "withdrawn"


class NodeType(StrEnum):
    """Case Knowledge Graph node types present in V1."""

    CASE = "case"
    EVIDENCE = "evidence"
    OBSERVATION = "observation"
    FINDING = "finding"
    CLAIM = "claim"


class RelationshipType(StrEnum):
    """Case Knowledge Graph relationship types.

    ``contains`` is structural (projected from ownership); the others are asserted and stored.
    ``derived-from`` is both: Observation -> Evidence is structural,
    Finding -> Observation is asserted.
    """

    CONTAINS = "contains"
    DERIVED_FROM = "derived-from"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    REFERENCES = "references"
