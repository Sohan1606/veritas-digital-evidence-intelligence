/**
 * API contracts. Mirrors backend/app/schemas.py (the owner of these shapes).
 * Public identifiers (CASE-001, EVD-001 …) are the only identifiers exposed.
 */

export type CaseState = "open" | "on_hold" | "closed";
export type ObjectiveState = "active" | "met" | "withdrawn";
export type EvidenceType = "image" | "video" | "audio" | "document" | "email" | "message_export" | "other";
export type EvidenceState = "registered" | "withdrawn";
export type ProfileStatus = "verified" | "partial" | "unknown" | "not_available";
export type AttributeBasis = "computed" | "declared";
export type AnalysisRunState = "queued" | "running" | "completed" | "failed" | "cancelled";
export type ObservationOrigin = "analysis_run" | "manual";
export type FindingReviewStatus = "unreviewed" | "under_review" | "accepted" | "challenged" | "rejected";
export type ClaimState = "open" | "under_assessment" | "assessed" | "withdrawn";
export type AssessmentState = "draft" | "submitted" | "withdrawn";
export type NodeType = "case" | "evidence" | "observation" | "finding" | "claim";
export type RelationshipType = "contains" | "derived-from" | "supports" | "contradicts" | "references";

export interface ListResponse<T> {
  items: T[];
  count: number;
}

export interface CaseCounts {
  evidence: number;
  analysis_runs: number;
  observations: number;
  findings: number;
  findings_awaiting_review: number;
  claims: number;
  assessments: number;
  relationships: number;
}

export interface CaseSummary {
  id: string;
  title: string;
  state: CaseState;
  demonstration: boolean;
  notice: string | null;
  counts: CaseCounts;
  created_at: string;
  updated_at: string;
}

export interface Objective {
  id: string;
  statement: string;
  state: ObjectiveState;
  position: number;
}

export interface CaseDetail extends CaseSummary {
  summary: string | null;
  objectives: Objective[];
  created_by: string;
}

export interface Evidence {
  id: string;
  label: string;
  evidence_type: EvidenceType;
  description: string | null;
  state: EvidenceState;
  profile_recorded: boolean;
  created_at: string;
}

export type EvidenceObjectState = "QUARANTINED" | "PRESERVED" | "REJECTED";
export type EvidenceValidationStatus = "pending" | "accepted" | "rejected";
export type EvidenceCustodyEventType = "RECEIVED" | "PRESERVED";

export interface EvidenceObject {
  id: string;
  evidence_id: string;
  original_filename: string;
  declared_media_type: string;
  detected_media_type: string | null;
  byte_size: number;
  sha256: string | null;
  sha512: string | null;
  state: EvidenceObjectState;
  validation_status: EvidenceValidationStatus;
  validation_note: string | null;
  acquired_at: string;
  acquired_by: string;
  upload_completed_at: string | null;
  preserved_at: string | null;
  preserved_by: string | null;
}

export interface EvidenceDetail {
  evidence: Evidence;
  objects: EvidenceObject[];
}

/** Outcome of one independent integrity verification. A match is not an authenticity finding. */
export type EvidenceIntegrityResult = "MATCH" | "MISMATCH" | "UNAVAILABLE";

/**
 * Mirrors backend EvidenceIntegrityVerificationOut. byte_size/sha256/sha512 are the values
 * recorded at intake; the expected_/computed_ pairs are present only for MISMATCH.
 */
export interface EvidenceIntegrityVerification {
  evidence_object_id: string;
  result: EvidenceIntegrityResult;
  byte_size: number;
  sha256: string;
  sha512: string;
  verified_at: string;
  verified_by: string;
  message: string;
  expected_byte_size?: number;
  computed_byte_size?: number;
  expected_sha256?: string;
  computed_sha256?: string;
  expected_sha512?: string;
  computed_sha512?: string;
}

export interface EvidenceCustodyEvent {
  id: string;
  evidence_id: string;
  evidence_object_id: string;
  event_type: EvidenceCustodyEventType;
  from_state: string | null;
  to_state: string;
  actor: string;
  occurred_at: string;
  reason: string | null;
  request_id: string;
}

export interface ProfileAttribute {
  key: string;
  label: string;
  value: string | null;
  basis: AttributeBasis;
}

export interface ProfileSection {
  status: ProfileStatus;
  attributes: ProfileAttribute[];
  note: string | null;
}

export const PROFILE_SECTION_KEYS = [
  "identity",
  "integrity",
  "provenance",
  "quality",
  "acquisition_context",
  "classification",
] as const;
export type ProfileSectionKey = (typeof PROFILE_SECTION_KEYS)[number];

export type EvidenceProfile = Record<ProfileSectionKey, ProfileSection> & {
  evidence: Evidence;
  recorded_by: string;
  updated_at: string;
  status_definitions: Record<ProfileStatus, string>;
};

export interface AnalysisRun {
  id: string;
  evidence_id: string;
  method_key: string;
  method_version: string;
  state: AnalysisRunState;
  started_at: string | null;
  completed_at: string | null;
}

export interface ObservationBasis {
  id: string;
  statement: string;
  origin: ObservationOrigin;
  analysis_run_id: string | null;
  evidence_id: string;
  evidence_label: string;
  recorded_by: string;
}

export interface AlternativeExplanation {
  explanation: string;
  status: "open" | "excluded";
  basis: string | null;
}

export interface ClaimLink {
  id: string;
  statement: string;
  relationship: RelationshipType;
  rationale: string | null;
}

export interface Finding {
  id: string;
  title: string;
  statement: string;
  method: string;
  limitations: string[];
  alternative_explanations: AlternativeExplanation[];
  review_status: FindingReviewStatus;
  evidence_basis: ObservationBasis[];
  related_claims: ClaimLink[];
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface FindingLink {
  id: string;
  title: string;
  relationship: RelationshipType;
  review_status: FindingReviewStatus;
}

export interface Assessment {
  id: string;
  claim_id: string;
  statement: string;
  state: AssessmentState;
  assessed_by: string;
  created_at: string;
}

export interface Claim {
  id: string;
  statement: string;
  source: string;
  state: ClaimState;
  related_findings: FindingLink[];
  referenced_evidence: { id: string; label: string }[];
  assessments: Assessment[];
  created_at: string;
}

export interface GraphNode {
  id: string;
  type: NodeType;
  label: string;
  state: string | null;
}

export interface GraphRelationship {
  id: string;
  origin: "structural" | "asserted";
  type: RelationshipType;
  source: string;
  target: string;
  rationale: string | null;
}

export interface CaseGraph {
  case_id: string;
  nodes: GraphNode[];
  relationships: GraphRelationship[];
}

export interface AuditEvent {
  id: string;
  occurred_at: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  details: Record<string, unknown>;
}

export interface Capability {
  key: string;
  label: string;
  status: "available" | "reserved";
  note: string;
}

export interface SystemInfo {
  name: "VERITAS";
  version: string;
  api_version: "v1";
  environment: string;
  access_mode: string;
  principal: "demonstration_viewer" | "user" | null;
  uptime_seconds: number;
  database: { dialect: string; schema_revision: string | null; expected_revision: string | null };
  capabilities: Capability[];
}

export interface SessionInfo {
  authenticated: boolean;
  user_id: string | null;
  display_name: string | null;
  organization_ids: string[];
  roles: string[];
  capabilities: string[];
  case_capabilities: Record<string, string[]>;
  session_id: string | null;
  expires_at: string | null;
  demonstration: boolean;
}

export interface IdentityRole {
  id: string;
  name: string;
  capabilities: string[];
  assignable_in_console: boolean;
}

export interface IdentityUser {
  id: string;
  username: string;
  display_name: string;
  status: "active" | "disabled";
  organization_id: string;
  roles: string[];
  case_assignments: { role_id: string; role: string; case_id: string | null }[];
}

export interface SecurityAuditEvent {
  id: string;
  occurred_at: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  details: Record<string, unknown>;
}
