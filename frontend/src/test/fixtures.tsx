/**
 * Typed API fixtures for frontend tests. They mirror the backend contract through
 * src/api/types.ts, so a contract change fails type-checking here first.
 * Synthetic by construction — not the seed data and not real evidence.
 */
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import type {
  AuditEvent,
  CaseDetail,
  CaseGraph,
  Claim,
  Evidence,
  EvidenceCustodyEvent,
  EvidenceDetail,
  EvidenceObject,
  EvidenceProfile,
  Finding,
  ListResponse,
  SystemInfo,
  SessionInfo,
} from "../api/types";

const T = "2026-01-15T09:30:00Z";
const list = <X,>(items: X[]): ListResponse<X> => ({ items, count: items.length });

export const caseDetail: CaseDetail = {
  id: "CASE-001",
  title: "Synthetic Demonstration Case",
  state: "open",
  demonstration: true,
  notice: "DEMONSTRATION DATA — NOT REAL EVIDENCE",
  counts: {
    evidence: 2,
    analysis_runs: 0,
    observations: 2,
    findings: 1,
    findings_awaiting_review: 1,
    claims: 1,
    assessments: 0,
    relationships: 6,
  },
  created_at: T,
  updated_at: T,
  summary: "Fixture case used by frontend tests.",
  objectives: [{ id: "OBJ-001", statement: "Establish the fixture timeline.", state: "active", position: 1 }],
  created_by: "system:seed",
};

export const evidence: Evidence[] = [
  { id: "EVD-001", label: "fixture-photo.jpg", evidence_type: "image", description: "Fixture image.", state: "registered", profile_recorded: true, created_at: T },
  { id: "EVD-002", label: "fixture-log.txt", evidence_type: "document", description: null, state: "registered", profile_recorded: false, created_at: T },
];

export const evidenceObject: EvidenceObject = {
  id: "EOBJ-001",
  evidence_id: "EVD-001",
  original_filename: "synthetic-photo.jpg",
  declared_media_type: "image/jpeg",
  detected_media_type: "image/jpeg",
  byte_size: 128,
  sha256: "a".repeat(64),
  sha512: "b".repeat(128),
  state: "PRESERVED",
  validation_status: "accepted",
  validation_note: "A supported leading-byte signature matched the declared media type.",
  acquired_at: T,
  acquired_by: "USR-001",
  upload_completed_at: T,
  preserved_at: T,
  preserved_by: "USR-002",
};

export const evidenceDetail: EvidenceDetail = {
  evidence: evidence[0]!,
  objects: [evidenceObject],
};

export const custodyEvents: EvidenceCustodyEvent[] = [
  {
    id: "CST-001",
    evidence_id: "EVD-001",
    evidence_object_id: "EOBJ-001",
    event_type: "RECEIVED",
    from_state: null,
    to_state: "QUARANTINED",
    actor: "USR-001",
    occurred_at: T,
    reason: "Evidence object registered in private quarantine.",
    request_id: "req-synthetic",
  },
  {
    id: "CST-002",
    evidence_id: "EVD-001",
    evidence_object_id: "EOBJ-001",
    event_type: "PRESERVED",
    from_state: "QUARANTINED",
    to_state: "PRESERVED",
    actor: "USR-002",
    occurred_at: T,
    reason: "Basic signature check passed.",
    request_id: "req-synthetic",
  },
];

const section = (status: EvidenceProfile["identity"]["status"], label: string, value: string | null) => ({
  status,
  note: null,
  attributes: [{ key: label.toLowerCase().replace(/\s/g, "_"), label, value, basis: "declared" as const }],
});

export const profile: EvidenceProfile = {
  evidence: evidence[0]!,
  recorded_by: "system:seed",
  updated_at: T,
  status_definitions: {
    verified: "Established by a recorded, repeatable check.",
    partial: "Some attributes are established; others are missing or declared only.",
    unknown: "Not yet established.",
    not_available: "Cannot be established for this evidence.",
  },
  identity: section("verified", "Declared file name", "fixture-photo.jpg"),
  integrity: section("unknown", "Content hash", null),
  provenance: section("partial", "Declared source", "Fixture device"),
  quality: section("unknown", "Resolution", null),
  acquisition_context: section("partial", "Acquired by", "Fixture examiner"),
  classification: section("not_available", "Category", null),
};

export const finding: Finding = {
  id: "FND-001",
  title: "Fixture finding title",
  statement: "The fixture photo was modified after the fixture log entry.",
  method: "Manual comparison of declared timestamps.",
  limitations: ["Timestamps are declared, not computed."],
  alternative_explanations: [
    { explanation: "Clock drift on the fixture device.", status: "open", basis: null },
    { explanation: "Timezone misreport.", status: "excluded", basis: "Both records use UTC." },
  ],
  review_status: "unreviewed",
  evidence_basis: [
    { id: "OBS-001", statement: "Photo timestamp is 10:02.", origin: "manual", analysis_run_id: null, evidence_id: "EVD-001", evidence_label: "fixture-photo.jpg", recorded_by: "system:seed" },
    { id: "OBS-002", statement: "Log entry is 09:58.", origin: "manual", analysis_run_id: null, evidence_id: "EVD-002", evidence_label: "fixture-log.txt", recorded_by: "system:seed" },
  ],
  related_claims: [{ id: "CLM-001", statement: "The photo was taken before the log entry.", relationship: "contradicts", rationale: "Ordering is reversed." }],
  created_by: "system:seed",
  created_at: T,
  updated_at: T,
};

export const claim: Claim = {
  id: "CLM-001",
  statement: "The photo was taken before the log entry.",
  source: "Fixture witness statement",
  state: "open",
  related_findings: [{ id: "FND-001", title: finding.title, relationship: "contradicts", review_status: "unreviewed" }],
  referenced_evidence: [{ id: "EVD-001", label: "fixture-photo.jpg" }],
  assessments: [],
  created_at: T,
};

export const graph: CaseGraph = {
  case_id: "CASE-001",
  nodes: [
    { id: "CASE-001", type: "case", label: "Synthetic Demonstration Case", state: "open" },
    { id: "EVD-001", type: "evidence", label: "fixture-photo.jpg", state: "registered" },
    { id: "EVD-002", type: "evidence", label: "fixture-log.txt", state: "registered" },
    { id: "OBS-001", type: "observation", label: "Photo timestamp is 10:02.", state: null },
    { id: "OBS-002", type: "observation", label: "Log entry is 09:58.", state: null },
    { id: "FND-001", type: "finding", label: finding.title, state: "unreviewed" },
    { id: "CLM-001", type: "claim", label: claim.statement, state: "open" },
  ],
  relationships: [
    { id: "s-1", origin: "structural", type: "contains", source: "CASE-001", target: "EVD-001", rationale: null },
    { id: "s-2", origin: "structural", type: "contains", source: "CASE-001", target: "EVD-002", rationale: null },
    { id: "s-3", origin: "structural", type: "derived-from", source: "OBS-001", target: "EVD-001", rationale: null },
    { id: "s-4", origin: "structural", type: "derived-from", source: "OBS-002", target: "EVD-002", rationale: null },
    { id: "REL-001", origin: "asserted", type: "derived-from", source: "FND-001", target: "OBS-001", rationale: null },
    { id: "REL-002", origin: "asserted", type: "derived-from", source: "FND-001", target: "OBS-002", rationale: null },
    { id: "REL-003", origin: "asserted", type: "contradicts", source: "FND-001", target: "CLM-001", rationale: "Ordering is reversed." },
    { id: "REL-004", origin: "asserted", type: "references", source: "CLM-001", target: "EVD-001", rationale: null },
  ],
};

export const auditEvents: AuditEvent[] = [
  { id: "AUD-002", occurred_at: T, actor: "system:seed", action: "finding.recorded", entity_type: "finding", entity_id: "FND-001", details: { review_status: "unreviewed" } },
  { id: "AUD-001", occurred_at: T, actor: "system:seed", action: "case.opened", entity_type: "case", entity_id: "CASE-001", details: {} },
];

export const systemInfo: SystemInfo = {
  name: "VERITAS",
  version: "0.1.0",
  api_version: "v1",
  environment: "test",
  access_mode: "demo",
  principal: "demonstration_viewer",
  uptime_seconds: 1,
  database: { dialect: "sqlite", schema_revision: "0001", expected_revision: "0001" },
  capabilities: [
    { key: "case_records", label: "Case records", status: "available", note: "Read-only." },
    { key: "identity", label: "User identity & access control", status: "available", note: "Provisioned identity and role-based authorization." },
    { key: "examination", label: "Automated examination", status: "reserved", note: "No examination methods are executable in V1." },
    { key: "timeline", label: "Timeline reconstruction", status: "reserved", note: "Requires temporal observations." },
    { key: "review", label: "Review & decisions", status: "reserved", note: "Requires investigator identity." },
    { key: "report", label: "Reports & Case Package", status: "reserved", note: "Not implemented." },
  ],
};

/** Authenticated V2 system response fixture matching backend principal="user". */
export const authenticatedSystemInfo: SystemInfo = {
  ...systemInfo,
  access_mode: "restricted",
  principal: "user",
};

export const ROUTES: Record<string, unknown> = {
  "/api/v1/system": systemInfo,
  "/api/v1/auth/session": {
    authenticated: false, user_id: null, display_name: null, organization_ids: [], roles: [],
    capabilities: ["case:read"], case_capabilities: { "*": ["case:read", "evidence:read", "findings:read", "claims:read", "graph:read", "review:read", "case_audit:read", "examination:read"] },
    session_id: null, expires_at: null, demonstration: true,
  } satisfies SessionInfo,
  "/api/v1/cases": list([caseDetail]),
  "/api/v1/cases/CASE-001": caseDetail,
  "/api/v1/cases/CASE-001/evidence": list(evidence),
  "/api/v1/cases/CASE-001/evidence/EVD-001/intake": evidenceDetail,
  "/api/v1/cases/CASE-001/evidence/EVD-002/intake": { evidence: evidence[1], objects: [] },
  "/api/v1/cases/CASE-001/evidence/EVD-001/objects/EOBJ-001/custody": list(custodyEvents),
  "/api/v1/cases/CASE-001/evidence/EVD-001/profile": profile,
  "/api/v1/cases/CASE-001/analysis-runs": list([]),
  "/api/v1/cases/CASE-001/findings": list([finding]),
  "/api/v1/cases/CASE-001/findings/FND-001": finding,
  "/api/v1/cases/CASE-001/claims": list([claim]),
  "/api/v1/cases/CASE-001/graph": graph,
  "/api/v1/cases/CASE-001/audit-events": list(auditEvents),
};

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "x-request-id": "req-test" } });

/**
 * Replaces fetch with a router over ROUTES. `overrides` map a path to a status code
 * (error envelope) or a body. Returns the mock so tests can assert on requests.
 */
export function mockApi(overrides: Record<string, number | unknown> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://veritas.test");
    const path = url.pathname;
    if (path in overrides) {
      const value = overrides[path];
      if (typeof value === "number") return json(value, { error: { code: "test_error", message: `Fixture status ${value}`, request_id: "req-test" } });
      return json(200, value);
    }
    if (path in ROUTES) return json(200, ROUTES[path]);
    return json(404, { error: { code: "not_found", message: "Not found.", request_id: "req-test" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export function renderAt(ui: ReactElement, path: string) {
  return render(<MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>);
}
