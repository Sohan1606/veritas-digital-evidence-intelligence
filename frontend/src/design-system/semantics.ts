/**
 * Semantic state vocabulary: one place that maps each domain state to its label, tone
 * and non-colour glyph. Colour is never the only carrier of meaning.
 */
import type {
  AssessmentState,
  CaseState,
  ClaimState,
  EvidenceIntegrityResult,
  EvidenceType,
  FindingReviewStatus,
  NodeType,
  ProfileStatus,
  RelationshipType,
} from "../api/types";
import type { IconName } from "./icons";

export type Tone = "ok" | "warn" | "risk" | "neutral" | "signal" | "muted";
/** Glyphs: filled ●, half ◐, ring ○, dashed ◌, cross ×, diamond ◆. */
export type Glyph = "filled" | "half" | "ring" | "dashed" | "cross" | "diamond";

export interface StateSemantics {
  label: string;
  tone: Tone;
  glyph: Glyph;
}

export const PROFILE_STATUS: Record<ProfileStatus, StateSemantics> = {
  verified: { label: "Verified", tone: "ok", glyph: "filled" },
  partial: { label: "Partial", tone: "warn", glyph: "half" },
  unknown: { label: "Unknown", tone: "neutral", glyph: "ring" },
  not_available: { label: "Not available", tone: "muted", glyph: "dashed" },
};

/** Integrity verification outcomes. Neutral wording: a mismatch is a difference, never a verdict. */
export const INTEGRITY_RESULT: Record<EvidenceIntegrityResult, StateSemantics> = {
  MATCH: { label: "Integrity match", tone: "ok", glyph: "filled" },
  MISMATCH: { label: "Integrity mismatch", tone: "warn", glyph: "diamond" },
  UNAVAILABLE: { label: "Verification unavailable", tone: "muted", glyph: "dashed" },
};

export const REVIEW_STATUS: Record<FindingReviewStatus, StateSemantics> = {
  unreviewed: { label: "Unreviewed", tone: "warn", glyph: "ring" },
  under_review: { label: "Under review", tone: "signal", glyph: "half" },
  accepted: { label: "Accepted", tone: "ok", glyph: "filled" },
  challenged: { label: "Challenged", tone: "risk", glyph: "diamond" },
  rejected: { label: "Rejected", tone: "risk", glyph: "cross" },
};

export const CASE_STATE: Record<CaseState, StateSemantics> = {
  open: { label: "Open", tone: "signal", glyph: "filled" },
  on_hold: { label: "On hold", tone: "warn", glyph: "half" },
  closed: { label: "Closed", tone: "muted", glyph: "ring" },
};

export const CLAIM_STATE: Record<ClaimState, StateSemantics> = {
  open: { label: "Open", tone: "neutral", glyph: "ring" },
  under_assessment: { label: "Under assessment", tone: "signal", glyph: "half" },
  assessed: { label: "Assessed", tone: "ok", glyph: "filled" },
  withdrawn: { label: "Withdrawn", tone: "muted", glyph: "cross" },
};

export const ASSESSMENT_STATE: Record<AssessmentState, StateSemantics> = {
  draft: { label: "Draft", tone: "neutral", glyph: "dashed" },
  submitted: { label: "Submitted", tone: "ok", glyph: "filled" },
  withdrawn: { label: "Withdrawn", tone: "muted", glyph: "cross" },
};

export const RELATIONSHIP: Record<RelationshipType, { label: string; tone: Tone; stroke: "solid" | "dashed" | "dotted" | "faint" }> = {
  supports: { label: "supports", tone: "ok", stroke: "solid" },
  contradicts: { label: "contradicts", tone: "risk", stroke: "dashed" },
  "derived-from": { label: "derived from", tone: "neutral", stroke: "solid" },
  references: { label: "references", tone: "signal", stroke: "dotted" },
  contains: { label: "contains", tone: "muted", stroke: "faint" },
};

export const NODE_TYPE: Record<NodeType, { label: string; plural: string; icon: IconName }> = {
  case: { label: "Case", plural: "Case", icon: "cases" },
  evidence: { label: "Evidence", plural: "Evidence", icon: "evidence" },
  observation: { label: "Observation", plural: "Observations", icon: "examination" },
  finding: { label: "Finding", plural: "Findings", icon: "finding" },
  claim: { label: "Claim", plural: "Claims", icon: "claim" },
};

export const EVIDENCE_TYPE: Record<EvidenceType, { label: string; icon: IconName }> = {
  image: { label: "Image", icon: "image" },
  video: { label: "Video", icon: "video" },
  audio: { label: "Audio", icon: "audio" },
  document: { label: "Document", icon: "document" },
  email: { label: "Email", icon: "email" },
  message_export: { label: "Message export", icon: "message" },
  other: { label: "Other", icon: "other" },
};

export const TONE_TEXT: Record<Tone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  risk: "text-risk",
  neutral: "text-neutral",
  signal: "text-signal",
  muted: "text-fg-subtle",
};

export const TONE_VAR: Record<Tone, string> = {
  ok: "var(--color-ok)",
  warn: "var(--color-warn)",
  risk: "var(--color-risk)",
  neutral: "var(--color-neutral)",
  signal: "var(--color-signal)",
  muted: "var(--color-fg-faint)",
};
