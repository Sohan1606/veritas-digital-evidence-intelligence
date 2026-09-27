"""API read models (response schemas).

Public identifiers (``CASE-001`` …) are the only identifiers exposed; internal UUIDs never are.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

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
    ProfileStatus,
    RelationshipType,
)
from app.domain.values import AlternativeExplanation, ProfileAttribute

DEMONSTRATION_NOTICE = "DEMONSTRATION DATA — NOT REAL EVIDENCE"


class ApiModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class ListResponse[T](ApiModel):
    items: list[T]
    count: int


# --- Case -------------------------------------------------------------------------------


class ObjectiveOut(ApiModel):
    id: str
    statement: str
    state: ObjectiveState
    position: int


class CaseCounts(ApiModel):
    evidence: int
    analysis_runs: int
    observations: int
    findings: int
    findings_awaiting_review: int
    claims: int
    assessments: int
    relationships: int


class CaseSummary(ApiModel):
    id: str
    title: str
    state: CaseState
    demonstration: bool
    notice: str | None
    counts: CaseCounts
    created_at: datetime
    updated_at: datetime


class CaseDetail(CaseSummary):
    summary: str | None
    objectives: list[ObjectiveOut]
    created_by: str


# --- Evidence ---------------------------------------------------------------------------


class EvidenceOut(ApiModel):
    id: str
    label: str
    evidence_type: EvidenceType
    description: str | None
    state: EvidenceState
    profile_recorded: bool
    created_at: datetime


class ProfileSectionOut(ApiModel):
    status: ProfileStatus
    attributes: list[ProfileAttribute]
    note: str | None


class EvidenceProfileOut(ApiModel):
    evidence: EvidenceOut
    identity: ProfileSectionOut
    integrity: ProfileSectionOut
    provenance: ProfileSectionOut
    quality: ProfileSectionOut
    acquisition_context: ProfileSectionOut
    classification: ProfileSectionOut
    recorded_by: str
    updated_at: datetime
    # Meaning of every status, owned by domain.values; included so clients never redefine it.
    status_definitions: dict[ProfileStatus, str]


# --- Examination / Findings / Claims ----------------------------------------------------


class AnalysisRunOut(ApiModel):
    id: str
    evidence_id: str
    method_key: str
    method_version: str
    state: AnalysisRunState
    started_at: datetime | None
    completed_at: datetime | None


class ObservationBasis(ApiModel):
    id: str
    statement: str
    origin: ObservationOrigin
    analysis_run_id: str | None
    evidence_id: str
    evidence_label: str
    recorded_by: str


class ClaimLink(ApiModel):
    id: str
    statement: str
    relationship: RelationshipType
    rationale: str | None


class FindingOut(ApiModel):
    id: str
    title: str
    statement: str
    method: str
    limitations: list[str]
    alternative_explanations: list[AlternativeExplanation]
    review_status: FindingReviewStatus
    evidence_basis: list[ObservationBasis]
    related_claims: list[ClaimLink]
    created_by: str
    created_at: datetime
    updated_at: datetime


class FindingLink(ApiModel):
    id: str
    title: str
    relationship: RelationshipType
    review_status: FindingReviewStatus


class EvidenceLink(ApiModel):
    id: str
    label: str


class AssessmentOut(ApiModel):
    id: str
    claim_id: str
    statement: str
    state: AssessmentState
    assessed_by: str
    created_at: datetime


class ClaimOut(ApiModel):
    id: str
    statement: str
    source: str
    state: ClaimState
    related_findings: list[FindingLink]
    referenced_evidence: list[EvidenceLink]
    assessments: list[AssessmentOut]
    created_at: datetime


# --- Case Knowledge Graph ---------------------------------------------------------------


class GraphNode(ApiModel):
    id: str
    type: NodeType
    label: str
    state: str | None


class GraphRelationship(ApiModel):
    id: str
    origin: Literal["structural", "asserted"]
    type: RelationshipType
    source: str
    target: str
    rationale: str | None


class CaseGraph(ApiModel):
    case_id: str
    nodes: list[GraphNode]
    relationships: list[GraphRelationship]


# --- Audit ------------------------------------------------------------------------------


class AuditEventOut(ApiModel):
    id: str
    occurred_at: datetime
    actor: str
    action: str
    entity_type: str
    entity_id: str | None
    details: dict[str, object]


# --- System -----------------------------------------------------------------------------


class HealthOut(ApiModel):
    status: Literal["ok"]


class ReadinessOut(ApiModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, Literal["ok", "failed", "outdated"]]


class CapabilityOut(ApiModel):
    key: str
    label: str
    status: Literal["available", "reserved"]
    note: str


class DatabaseInfo(ApiModel):
    dialect: str
    schema_revision: str | None
    expected_revision: str | None


class SystemInfo(ApiModel):
    name: Literal["VERITAS"]
    version: str
    api_version: Literal["v1"]
    environment: str
    access_mode: str
    principal: Literal["demonstration_viewer"] | None
    uptime_seconds: float
    database: DatabaseInfo
    capabilities: list[CapabilityOut]
