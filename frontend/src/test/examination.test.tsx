import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import type {
  AnalysisRun,
  AnalysisRunDetail,
  AnalysisRunState,
  CaseDetail,
  Evidence,
  EvidenceDetail,
  EvidenceObject,
  ExaminationMethod,
  ListResponse,
  SessionInfo,
} from "../api/types";
import { RUN_STATE } from "../design-system";
import { IntentKeys, eligibilityOf } from "../features/examination/examinationModel";
import { authenticatedSystemInfo, caseDetail, mockApi } from "./fixtures";

const T = "2026-10-01T09:30:00Z";
const CASE = "CASE-900";
const BASE = `/api/v1/cases/${CASE}`;
const list = <X,>(items: X[]): ListResponse<X> => ({ items, count: items.length });

// --- Fixtures (synthetic; typed through src/api/types.ts so a contract change fails here) -----

const operationalCase: CaseDetail = { ...caseDetail, id: CASE, demonstration: false, notice: null, title: "Synthetic operational Case" };

const binaryMethod: ExaminationMethod = {
  key: "core.binary_characteristics",
  version: "1.0",
  name: "Binary Characteristics Examination",
  purpose: "Calculate deterministic byte-level characteristics of a preserved EvidenceObject.",
  supported_evidence_types: ["image", "video", "audio", "document", "email", "message_export", "other"],
  input_requirements: ["The PRESERVED bytes of one EvidenceObject, read through private evidence storage."],
  parameters: { type: "object", properties: {}, additionalProperties: false },
  outputs: [
    { key: "byte_count", label: "Byte count", definition: "The number of bytes read from the preserved EvidenceObject." },
    { key: "shannon_entropy", label: "Shannon byte entropy", definition: "Bits per byte, from 0 to 8." },
  ],
  limitations: ["Measures byte-level statistics only. It does not determine authenticity, origin or manipulation."],
  resource_limits: { max_object_bytes: 1_073_741_824, chunk_bytes: 65_536, max_runtime_seconds: 900, max_observations: 16, max_statement_chars: 500 },
  deterministic: true,
  enabled: true,
};

const documentEvidence: Evidence = { id: "EVD-010", label: "synthetic-statement", evidence_type: "document", description: null, state: "registered", profile_recorded: false, created_at: T };
const imageEvidence: Evidence = { id: "EVD-011", label: "synthetic-capture", evidence_type: "image", description: null, state: "registered", profile_recorded: false, created_at: T };

const object = (id: string, evidenceId: string, state: EvidenceObject["state"], extra: Partial<EvidenceObject> = {}): EvidenceObject => ({
  id,
  evidence_id: evidenceId,
  original_filename: `${id.toLowerCase()}.bin`,
  declared_media_type: "application/octet-stream",
  detected_media_type: state === "PRESERVED" ? "application/pdf" : null,
  byte_size: 4096,
  sha256: state === "PRESERVED" ? "a".repeat(64) : null,
  sha512: state === "PRESERVED" ? "b".repeat(128) : null,
  state,
  validation_status: state === "PRESERVED" ? "accepted" : state === "REJECTED" ? "rejected" : "pending",
  validation_note: null,
  acquired_at: T,
  acquired_by: "USR-001",
  upload_completed_at: state === "QUARANTINED" ? null : T,
  preserved_at: state === "PRESERVED" ? T : null,
  preserved_by: state === "PRESERVED" ? "USR-002" : null,
  ...extra,
});

const EOBJ_PRESERVED = object("EOBJ-010", "EVD-010", "PRESERVED", { original_filename: "statement-export.txt" });
const EOBJ_QUARANTINED = object("EOBJ-011", "EVD-010", "QUARANTINED");
const EOBJ_REJECTED = object("EOBJ-012", "EVD-010", "REJECTED");
const EOBJ_IMAGE = object("EOBJ-020", "EVD-011", "PRESERVED", { byte_size: 2048 });

const OBSERVATIONS = [
  "Observed byte count: 4096.",
  "Observed Shannon byte entropy: 7.9534 bits per byte.",
  "Observed printable ASCII byte ratio: 0.3711.",
  "Observed NUL-byte ratio: 0.0039.",
  "Observed distinct byte values: 256 of 256.",
];

function session(roles: string[], capabilities: string[]): SessionInfo {
  return {
    authenticated: true,
    user_id: "USR-300",
    display_name: "Synthetic Examiner",
    organization_ids: ["ORG-001"],
    roles,
    capabilities,
    case_capabilities: { [CASE]: capabilities },
    session_id: "SES-300",
    expires_at: "2026-10-02T00:00:00Z",
    demonstration: false,
  };
}
const READ = ["case:read", "evidence:read", "findings:read", "claims:read", "graph:read", "examination:read"];
const investigator = session(["INVESTIGATOR"], [...READ, "evidence:intake", "examination:execute"]);
const reviewer = session(["REVIEWER"], [...READ, "review:read"]);

// --- A stateful fake of the examination API -------------------------------------------------

interface Call {
  method: string;
  path: string;
  body?: Record<string, unknown>;
}

interface Options {
  who?: SessionInfo;
  methods?: ExaminationMethod[];
  evidence?: Evidence[];
  objects?: Record<string, EvidenceObject[]>;
  runs?: AnalysisRunDetail[];
  capabilityStatus?: "available" | "reserved";
}

function makeRun(id: string, state: AnalysisRunState, extra: Partial<AnalysisRunDetail> = {}): AnalysisRunDetail {
  const finished = state === "completed" || state === "failed" || state === "cancelled";
  return {
    id,
    evidence_id: "EVD-010",
    evidence_object_id: "EOBJ-010",
    method_key: binaryMethod.key,
    method_version: binaryMethod.version,
    state,
    parameters: {},
    started_at: state === "queued" ? null : T,
    completed_at: finished ? T : null,
    cancel_requested_at: null,
    last_heartbeat_at: state === "running" ? T : null,
    failure_code: null,
    failure_message: null,
    created_by: "USR-300",
    created_at: T,
    updated_at: T,
    observations:
      state === "completed"
        ? OBSERVATIONS.map((statement, index) => ({
            id: `OBS-${100 + index}`,
            statement,
            origin: "analysis_run" as const,
            analysis_run_id: id,
            evidence_id: "EVD-010",
            evidence_label: "synthetic-statement",
            recorded_by: "system:examination-worker",
            created_at: T,
          }))
        : [],
    ...extra,
  };
}

function summary(run: AnalysisRunDetail): AnalysisRun {
  const copy: Partial<AnalysisRunDetail> = { ...run };
  delete copy.observations;
  return copy as AnalysisRun;
}

/** The one element of a list that must have exactly one element. */
function only<X>(items: X[]): X {
  if (items.length !== 1) throw new Error(`expected exactly one item, found ${items.length}`);
  return items[0] as X;
}

function createFake(options: Options = {}) {
  const state = {
    runs: [...(options.runs ?? [])],
    calls: [] as Call[],
    sequence: 100,
    idempotent: new Map<string, string>(),
    failCreate: null as null | "network" | { status: number; code: string; message: string },
    holdCreate: null as null | Promise<void>,
    listStatus: 200,
    detailStatus: 200,
  };
  const who = options.who ?? investigator;
  const methods = options.methods ?? [binaryMethod];
  const evidence = options.evidence ?? [documentEvidence, imageEvidence];
  const objects = options.objects ?? {
    "EVD-010": [EOBJ_PRESERVED, EOBJ_QUARANTINED, EOBJ_REJECTED],
    "EVD-011": [EOBJ_IMAGE],
  };
  const system = {
    ...authenticatedSystemInfo,
    capabilities: authenticatedSystemInfo.capabilities.map((c) =>
      c.key === "examination" ? { ...c, status: options.capabilityStatus ?? "available" } : c,
    ),
  };
  const json = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "x-request-id": "req-exam" } });
  const error = (status: number, code: string, message: string) => json(status, { error: { code, message, request_id: "req-exam" } });
  const find = (id: string) => state.runs.find((run) => run.id === id);

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://veritas.test");
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.pathname;
    const body = typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : undefined;
    state.calls.push({ method, path, body });

    if (path === "/api/v1/system") return json(200, system);
    if (path === "/api/v1/auth/session") return json(200, who);
    if (path === BASE) return json(200, operationalCase);
    if (path === `${BASE}/examination/methods`) return json(200, list(methods));
    if (path === `${BASE}/evidence`) return json(200, list(evidence));
    const intake = /^\/api\/v1\/cases\/CASE-900\/evidence\/(EVD-\d+)\/intake$/.exec(path);
    if (intake) {
      const owner = evidence.find((e) => e.id === String(intake[1]));
      return owner ? json(200, { evidence: owner, objects: objects[owner.id] ?? [] } satisfies EvidenceDetail) : error(404, "not_found", "Evidence was not found");
    }
    if (path === `${BASE}/analysis-runs` && method === "GET") {
      return state.listStatus === 200 ? json(200, list(state.runs.map(summary))) : error(state.listStatus, "server_error", "The run list failed");
    }
    if (path === `${BASE}/analysis-runs` && method === "POST") {
      if (state.holdCreate) await state.holdCreate;
      if (state.failCreate === "network") {
        state.failCreate = null;
        throw new TypeError("network down");
      }
      if (state.failCreate) {
        const failure = state.failCreate;
        state.failCreate = null;
        return error(failure.status, failure.code, failure.message);
      }
      const key = String(body?.idempotency_key);
      const known = state.idempotent.get(key);
      if (known) return json(200, summary(find(known) as AnalysisRunDetail));
      state.sequence += 1;
      const run = makeRun(`ANL-${state.sequence}`, "queued", {
        evidence_id: String(body?.evidence_id),
        evidence_object_id: String(body?.evidence_object_id),
      });
      state.runs.push(run);
      state.idempotent.set(key, run.id);
      return json(201, summary(run));
    }
    const detail = /^\/api\/v1\/cases\/CASE-900\/analysis-runs\/(ANL-\d+)$/.exec(path);
    if (detail && method === "GET") {
      if (state.detailStatus !== 200) return error(state.detailStatus, "internal_error", "An internal error occurred");
      const run = find(String(detail[1]));
      return run ? json(200, run) : error(404, "not_found", "Analysis run was not found in this case");
    }
    const action = /^\/api\/v1\/cases\/CASE-900\/analysis-runs\/(ANL-\d+)\/(cancel|retry)$/.exec(path);
    if (action && method === "POST") {
      const run = find(String(action[1]));
      if (!run) return error(404, "not_found", "Analysis run was not found in this case");
      if (String(action[2]) === "cancel") {
        if (run.state === "queued") {
          Object.assign(run, { state: "cancelled", completed_at: T });
          return json(200, summary(run));
        }
        if (run.state === "running") {
          run.cancel_requested_at = T;
          return json(202, summary(run));
        }
        return error(409, "invalid_lifecycle_transition", `A ${run.state} run is final and cannot be cancelled`);
      }
      if (run.state !== "failed" && run.state !== "cancelled") {
        return error(409, "invalid_lifecycle_transition", `A ${run.state} run cannot be retried`);
      }
      const key = String(body?.idempotency_key);
      const known = state.idempotent.get(key);
      if (known) return json(200, summary(find(known) as AnalysisRunDetail));
      state.sequence += 1;
      const created = makeRun(`ANL-${state.sequence}`, "queued", { evidence_id: run.evidence_id, evidence_object_id: run.evidence_object_id });
      state.runs.push(created);
      state.idempotent.set(key, created.id);
      return json(201, summary(created));
    }
    return error(404, "not_found", "Not found.");
  });
  vi.stubGlobal("fetch", fetchMock);
  return { state, fetchMock, find, posts: () => state.calls.filter((call) => call.method === "POST") };
}

function openExamination(path = `/app/cases/${CASE}/examination`) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

async function axeViolations(container: HTMLElement) {
  const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
  return results.violations.map((violation) => `${violation.id}: ${violation.help}`);
}

async function chooseDefaults(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("radio", { name: /Binary Characteristics Examination/ }));
  await user.click(await screen.findByRole("radio", { name: /EOBJ-010/ }));
}

beforeEach(() => {
  vi.stubGlobal("crypto", { randomUUID: (() => { let n = 0; return () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}`; })() });
});
afterEach(() => {
  vi.useRealTimers();
});

// --- Methods are discovered from the backend ----------------------------------------------------

describe("Method list", () => {
  it("renders the Methods the backend registry returns, with exact Method and version", async () => {
    createFake({ methods: [{ ...binaryMethod, key: "test.zeta_probe", name: "Zeta Probe", version: "2.5" }] });
    openExamination();
    expect(await screen.findByRole("radio", { name: "Zeta Probe" })).toBeInTheDocument();
    expect(screen.getByText("test.zeta_probe@2.5")).toBeInTheDocument();
    expect(screen.queryByText(/Binary Characteristics/)).not.toBeInTheDocument();
  });

  it("explains a Method from backend metadata: outputs, requirements, limits and limitations", async () => {
    createFake();
    const user = userEvent.setup();
    openExamination();
    await user.click(await screen.findByText("Explain Binary Characteristics Examination"));
    expect(screen.getByText("Byte count")).toBeInTheDocument();
    expect(screen.getByText("The number of bytes read from the preserved EvidenceObject.")).toBeInTheDocument();
    expect(screen.getByText(/does not determine authenticity, origin or manipulation/)).toBeInTheDocument();
    expect(screen.getByText(/Parameters: none/)).toBeInTheDocument();
    expect(screen.getByText(/65,536 bytes chunks/)).toBeInTheDocument();
  });

  it("selects the only enabled Method by default and marks disabled Methods unavailable", async () => {
    createFake({ methods: [binaryMethod, { ...binaryMethod, key: "test.off", name: "Switched Off", enabled: false }] });
    openExamination();
    const disabled = await screen.findByRole("radio", { name: "Switched Off" });
    expect(disabled).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Binary Characteristics Examination" })).toBeChecked();
  });
});

describe("Capability state comes from the backend", () => {
  it("shows the reserved state, and fetches no Methods, when /api/v1/system says reserved", async () => {
    const fake = createFake({ capabilityStatus: "reserved" });
    openExamination();
    expect(await screen.findByText(/Examination is not available in this version/)).toBeInTheDocument();
    expect(fake.state.calls.some((call) => call.path.endsWith("/examination/methods"))).toBe(false);
  });
});

// --- EvidenceObject selection and eligibility ------------------------------------------------

describe("EvidenceObject selection", () => {
  it("shows Evidence ID, EvidenceObject ID, filename, state, size and integrity availability", async () => {
    createFake();
    openExamination();
    const row = (await screen.findByRole("radio", { name: /EOBJ-010/ })).closest("li") as HTMLElement;
    expect(within(row).getByText("EVD-010")).toBeInTheDocument();
    expect(within(row).getByText("EOBJ-010")).toBeInTheDocument();
    expect(within(row).getByText("statement-export.txt")).toBeInTheDocument();
    expect(within(row).getByText("PRESERVED")).toBeInTheDocument();
    expect(within(row).getByText(/4,096 bytes/)).toBeInTheDocument();
    expect(within(row).getByText(/SHA-256 and SHA-512 recorded at intake/)).toBeInTheDocument();
    const quarantined = screen.getByRole("radio", { name: /EOBJ-011/ }).closest("li") as HTMLElement;
    expect(within(quarantined).getByText(/No integrity values recorded/)).toBeInTheDocument();
  });

  it("does not call upload digests of an unpreserved object 'recorded at intake'", async () => {
    createFake({
      objects: {
        "EVD-010": [object("EOBJ-013", "EVD-010", "QUARANTINED", { sha256: "c".repeat(64), sha512: "d".repeat(128) })],
        "EVD-011": [],
      },
    });
    openExamination();
    const row = (await screen.findByRole("radio", { name: /EOBJ-013/ })).closest("li") as HTMLElement;
    expect(within(row).getByText(/computed on the upload \(not preserved\)/)).toBeInTheDocument();
    expect(within(row).queryByText(/recorded at intake/)).not.toBeInTheDocument();
  });

  it("makes only PRESERVED objects selectable and says why the others are not", async () => {
    createFake();
    openExamination();
    expect(await screen.findByRole("radio", { name: /EOBJ-010/ })).toBeEnabled();
    for (const id of ["EOBJ-011", "EOBJ-012"]) {
      const radio = screen.getByRole("radio", { name: new RegExp(id) });
      expect(radio).toBeDisabled();
      const reason = radio.getAttribute("aria-describedby");
      expect(reason && document.getElementById(reason)?.textContent).toMatch(/Only PRESERVED objects can be examined/);
    }
    expect(screen.getByRole("radio", { name: /EOBJ-020/ })).toBeEnabled();
  });

  it("explains each reason Start is unavailable, then enables it for a valid pair", async () => {
    createFake();
    const user = userEvent.setup();
    openExamination();
    const start = await screen.findByRole("button", { name: "Start examination" });
    await screen.findByRole("radio", { name: /EOBJ-010/ });
    expect(screen.getByRole("radio", { name: "Binary Characteristics Examination" })).toBeChecked();
    expect(start).toBeDisabled();
    expect(screen.getByText("Select a preserved EvidenceObject.")).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /EOBJ-010/ }));
    expect(start).toBeEnabled();
    expect(screen.getByText(/Ready: core\.binary_characteristics@1\.0 will read the preserved bytes of EOBJ-010 \(EVD-010\)/)).toBeInTheDocument();
    expect(start.getAttribute("aria-describedby")).toBe("start-reason");
  });

  it("refuses a Method that does not support the evidence type before any request", async () => {
    const imageOnly = { ...binaryMethod, supported_evidence_types: ["image" as const] };
    const fake = createFake({ methods: [imageOnly] });
    const user = userEvent.setup();
    openExamination();
    await user.click(await screen.findByRole("radio", { name: /EOBJ-010/ }));
    expect(screen.getByText(/does not support document evidence/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start examination" })).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: /EOBJ-020/ }));
    expect(screen.getByRole("button", { name: "Start examination" })).toBeEnabled();
    expect(fake.posts()).toHaveLength(0);
  });

  it("covers the remaining eligibility reasons in the pure model", () => {
    const base = { canExecute: true, demonstration: false, method: binaryMethod, evidence: documentEvidence, object: EOBJ_PRESERVED };
    expect(eligibilityOf(base)).toEqual({ eligible: true });
    expect(eligibilityOf({ ...base, demonstration: true })).toMatchObject({ eligible: false, reason: expect.stringContaining("Demonstration cases never execute") });
    expect(eligibilityOf({ ...base, canExecute: false })).toMatchObject({ reason: expect.stringContaining("can read examinations but cannot start them") });
    expect(eligibilityOf({ ...base, method: undefined })).toMatchObject({ reason: "Select a Method." });
    expect(eligibilityOf({ ...base, method: { ...binaryMethod, enabled: false } })).toMatchObject({ reason: expect.stringContaining("is disabled") });
    expect(eligibilityOf({ ...base, method: { ...binaryMethod, parameters: { required: ["depth"] } } })).toMatchObject({ reason: expect.stringContaining("needs parameters") });
    expect(eligibilityOf({ ...base, object: EOBJ_QUARANTINED })).toMatchObject({ reason: expect.stringContaining("EOBJ-011 is QUARANTINED") });
    expect(eligibilityOf({ ...base, object: { ...EOBJ_PRESERVED, byte_size: 2_000_000_000 } })).toMatchObject({ reason: expect.stringContaining("size limit") });
  });
});

// --- Starting an examination ----------------------------------------------------------------

describe("Start examination", () => {
  it("sends exactly the documented request (no path, key or storage field) and shows the queued run", async () => {
    const fake = createFake();
    const user = userEvent.setup();
    openExamination();
    await chooseDefaults(user);
    await user.click(screen.getByRole("button", { name: "Start examination" }));

    await waitFor(() => expect(fake.posts()).toHaveLength(1));
    const post = only(fake.posts());
    expect(post.path).toBe(`${BASE}/analysis-runs`);
    expect(Object.keys(post.body ?? {}).sort()).toEqual(
      ["evidence_id", "evidence_object_id", "idempotency_key", "method_key", "method_version", "parameters"],
    );
    expect(post.body).toMatchObject({
      evidence_id: "EVD-010",
      evidence_object_id: "EOBJ-010",
      method_key: "core.binary_characteristics",
      method_version: "1.0",
      parameters: {},
    });
    expect(String(post.body?.idempotency_key)).toMatch(/^ui-/);
    expect(JSON.stringify(post.body)).not.toMatch(/path|storage|\/var\//i);

    const heading = await screen.findByRole("heading", { name: /ANL-101/ });
    expect(within(heading).getByText("Queued")).toBeInTheDocument();
    expect(screen.getByText(/Waiting for a worker to claim this run/)).toBeInTheDocument();
    expect(heading).toHaveFocus();
    // The run list shows the new run too, even though no run was active before it was started.
    const table = await screen.findByRole("table", { name: "Analysis runs, newest first" });
    expect(within(table).getByRole("button", { name: "Open run ANL-101" })).toBeInTheDocument();
    expect(screen.queryByText(/No analysis runs recorded/)).not.toBeInTheDocument();
  });

  it("shows a loading state while the request is in flight, then returns to Start", async () => {
    const fake = createFake();
    let release: () => void = () => undefined;
    fake.state.holdCreate = new Promise<void>((resolve) => (release = resolve));
    const user = userEvent.setup();
    openExamination();
    await chooseDefaults(user);
    await user.click(screen.getByRole("button", { name: "Start examination" }));
    const busy = await screen.findByRole("button", { name: "Starting…" });
    expect(busy).toBeDisabled();
    await act(async () => release());
    expect(await screen.findByRole("button", { name: "Start examination" })).toBeEnabled();
  });

  it("reuses one idempotency key while the outcome is unknown, so a second click cannot start a second run", async () => {
    const fake = createFake();
    fake.state.failCreate = "network";
    const user = userEvent.setup();
    openExamination();
    await chooseDefaults(user);
    await user.click(screen.getByRole("button", { name: "Start examination" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be reached/i);
    await user.click(screen.getByRole("button", { name: "Start examination" }));
    await screen.findByRole("heading", { name: /ANL-101/ });
    const keys = fake.posts().map((post) => post.body?.idempotency_key);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
    expect(fake.state.runs).toHaveLength(1);

    // A definite success settles the intent: examining the same object again is a NEW run.
    await user.click(screen.getByRole("button", { name: "Start examination" }));
    await waitFor(() => expect(fake.state.runs).toHaveLength(2));
    expect(fake.posts().at(2)?.body?.idempotency_key).not.toBe(keys[0]);
  });

  it("settles the intent after a definite refusal and shows the server's message with its request id", async () => {
    const fake = createFake();
    fake.state.failCreate = { status: 409, code: "evidence_object_not_preserved", message: "Only a PRESERVED EvidenceObject can be examined" };
    const user = userEvent.setup();
    openExamination();
    await chooseDefaults(user);
    await user.click(screen.getByRole("button", { name: "Start examination" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Only a PRESERVED EvidenceObject can be examined");
    expect(alert).toHaveTextContent("req-exam");
    await user.click(screen.getByRole("button", { name: "Start examination" }));
    await waitFor(() => expect(fake.state.runs).toHaveLength(1));
    expect(fake.posts().at(0)?.body?.idempotency_key).not.toBe(fake.posts().at(1)?.body?.idempotency_key);
  });

  it("keeps IntentKeys honest: one key per intent until settled", () => {
    vi.stubGlobal("crypto", { randomUUID: (() => { let n = 0; return () => `u-${++n}`; })() });
    const keys = new IntentKeys();
    const first = keys.keyFor("a");
    expect(keys.keyFor("a")).toBe(first);
    expect(keys.keyFor("b")).not.toBe(first);
    keys.settle("a");
    expect(keys.keyFor("a")).not.toBe(first);
  });
});

// --- Every run state, rendered from the backend's real state --------------------------------------

describe("Run states", () => {
  it.each<[AnalysisRunState, RegExp]>([
    ["queued", /Waiting for a worker to claim this run/],
    ["running", /A worker is executing this run/],
    ["completed", /is not a Finding, a Claim or an Assessment/],
    ["failed", /No Observations were published/],
    ["cancelled", /was cancelled and published no Observations/],
  ])("renders the %s state with its own wording", async (state, wording) => {
    const run = makeRun("ANL-050", state, state === "failed" ? { failure_code: "evidence_unavailable", failure_message: "The preserved EvidenceObject could not be read from private evidence storage." } : {});
    createFake({ runs: [run] });
    const user = userEvent.setup();
    openExamination();
    await user.click(await screen.findByRole("button", { name: "Open run ANL-050" }));
    expect(await screen.findByText(wording)).toBeInTheDocument();
    const heading = screen.getByRole("heading", { name: /ANL-050/ });
    expect(within(heading).getByText(RUN_STATE[state].label)).toBeInTheDocument();
    expect(screen.getByText(`ANL-050 is ${RUN_STATE[state].label.toLowerCase()}`)).toHaveAttribute("role", "status");
  });

  it("gives every state a distinct label and glyph, so state never relies on colour", () => {
    const entries = Object.values(RUN_STATE);
    expect(new Set(entries.map((e) => e.label)).size).toBe(5);
    expect(new Set(entries.map((e) => e.glyph)).size).toBe(5);
  });

  it("shows exact provenance and the five Observations of a completed run, separate from Findings", async () => {
    createFake({ runs: [makeRun("ANL-051", "completed")] });
    const user = userEvent.setup();
    openExamination();
    await user.click(await screen.findByRole("button", { name: "Open run ANL-051" }));
    const trace = await screen.findByRole("region", { name: "Provenance of ANL-051" });
    expect(within(trace).getByText("core.binary_characteristics@1.0")).toBeInTheDocument();
    expect(within(trace).getByText("EVD-010")).toBeInTheDocument();
    expect(within(trace).getByText("EOBJ-010")).toBeInTheDocument();
    expect(within(trace).getByText("None")).toBeInTheDocument(); // no parameters
    expect(within(trace).getByText("USR-300")).toBeInTheDocument();
    const observations = screen.getByRole("heading", { name: "Observations (5)" }).closest("section") as HTMLElement;
    for (const statement of OBSERVATIONS) expect(within(observations).getByText(statement)).toBeInTheDocument();
    expect(within(observations).getAllByText("system:examination-worker")).toHaveLength(5);
    expect(within(observations).getByText(/is not a Finding, a Claim or an Assessment/)).toBeInTheDocument();
    // The legend keeps the four record types apart and says this page creates Observations only.
    for (const term of ["Observation", "Finding", "Claim", "Assessment"]) {
      expect(screen.getByText(term, { selector: "dt" })).toBeInTheDocument();
    }
    expect(screen.getByText(/This page creates Observations only/)).toBeInTheDocument();
  });

  it("shows the failure code and message of a failed run without inventing a result", async () => {
    createFake({
      runs: [makeRun("ANL-052", "failed", { failure_code: "integrity_mismatch", failure_message: "The bytes read for this run do not match the byte count and digests recorded at intake, so no Observations were published. This is an integrity comparison only." })],
    });
    const user = userEvent.setup();
    openExamination();
    await user.click(await screen.findByRole("button", { name: "Open run ANL-052" }));
    expect(await screen.findByText("integrity_mismatch")).toBeInTheDocument();
    expect(screen.getByText(/do not match the byte count and digests recorded at intake/)).toBeInTheDocument();
    expect(screen.queryByText(/Observations \(/)).not.toBeInTheDocument();
    expect(screen.getByText(/Retrying creates a new Analysis Run; this one stays as history/)).toBeInTheDocument();
  });

  it("lists runs newest first, each with its state and exact EvidenceObject", async () => {
    createFake({ runs: [makeRun("ANL-001", "completed"), makeRun("ANL-002", "failed", { failure_code: "execution_failed", failure_message: "x." }), makeRun("ANL-003", "queued")] });
    openExamination();
    const table = await screen.findByRole("table", { name: "Analysis runs, newest first" });
    const ids = within(table).getAllByRole("button").map((b) => b.textContent);
    expect(ids).toEqual(["ANL-003", "ANL-002", "ANL-001"]);
    expect(within(table).getAllByText("EOBJ-010")).toHaveLength(3);
    // The exact Method and version of every run is visible in the list itself.
    expect(within(table).getAllByText("core.binary_characteristics@1.0")).toHaveLength(3);
    // Three short columns: a half-width panel must not need to scroll sideways.
    expect(within(table).getAllByRole("columnheader", { hidden: true })).toHaveLength(3);
  });
});

// --- Polling shows real backend state only ------------------------------------------------------------

describe("Polling", () => {
  it("follows a run from queued to running to completed, then stops polling", async () => {
    const fake = createFake({ runs: [makeRun("ANL-060", "queued")] });
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    openExamination();
    const open = await screen.findByRole("button", { name: "Open run ANL-060" });
    await act(async () => open.click());
    const heading = await screen.findByRole("heading", { name: /ANL-060/ });
    expect(within(heading).getByText("Queued")).toBeInTheDocument();

    const tick = async () => act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    Object.assign(fake.find("ANL-060") as AnalysisRunDetail, { state: "running", started_at: T, last_heartbeat_at: T });
    await tick();
    await waitFor(() => expect(within(screen.getByRole("heading", { name: /ANL-060/ })).getByText("Running")).toBeInTheDocument());
    expect(screen.getByText(/Last heartbeat/)).toBeInTheDocument();

    Object.assign(fake.find("ANL-060") as AnalysisRunDetail, makeRun("ANL-060", "completed"));
    await tick();
    await waitFor(() => expect(screen.getByRole("heading", { name: "Observations (5)" })).toBeInTheDocument());

    const before = fake.state.calls.length;
    await tick();
    await tick();
    expect(fake.state.calls.length).toBe(before); // a finished run is history: no more requests
  });

  it("says plainly when the latest state could not be refreshed and keeps the last state it received", async () => {
    const fake = createFake({ runs: [makeRun("ANL-061", "running")] });
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    openExamination();
    const open = await screen.findByRole("button", { name: "Open run ANL-061" });
    await act(async () => open.click());
    await screen.findByText(/A worker is executing this run/);
    fake.state.listStatus = 503;
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(await screen.findByText(/The run list could not be refreshed/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /ANL-061/ })).toBeInTheDocument(); // last known state stays visible
    expect(screen.getByRole("button", { name: "Open run ANL-061" })).toBeInTheDocument();
  });
});

// --- Cancel and retry ----------------------------------------------------------------------------------

describe("Cancel and retry", () => {
  it("cancels a queued run immediately", async () => {
    const fake = createFake({ runs: [makeRun("ANL-070", "queued")] });
    const user = userEvent.setup();
    openExamination();
    await user.click(await screen.findByRole("button", { name: "Open run ANL-070" }));
    await user.click(await screen.findByRole("button", { name: "Cancel run" }));
    expect(await screen.findByText(/was cancelled and published no Observations/)).toBeInTheDocument();
    expect(fake.posts().at(-1)).toMatchObject({ path: `${BASE}/analysis-runs/ANL-070/cancel` });
    expect(screen.queryByRole("button", { name: "Cancel run" })).not.toBeInTheDocument();
  });

  it("requests cancellation of a running run and then offers nothing further to click", async () => {
    createFake({ runs: [makeRun("ANL-071", "running")] });
    const user = userEvent.setup();
    openExamination();
    await user.click(await screen.findByRole("button", { name: "Open run ANL-071" }));
    await user.click(await screen.findByRole("button", { name: "Request cancellation" }));
    expect(await screen.findByText(/Cancellation was requested/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Request cancellation" })).not.toBeInTheDocument();
    expect(within(screen.getByRole("heading", { name: /ANL-071/ })).getByText("Running")).toBeInTheDocument(); // still the real state
  });

  it("shows the server's refusal when a run can no longer be cancelled", async () => {
    const fake = createFake({ runs: [makeRun("ANL-072", "queued")] });
    const user = userEvent.setup();
    openExamination();
    await user.click(await screen.findByRole("button", { name: "Open run ANL-072" }));
    const cancel = await screen.findByRole("button", { name: "Cancel run" });
    Object.assign(fake.find("ANL-072") as AnalysisRunDetail, makeRun("ANL-072", "completed")); // it finished meanwhile
    await user.click(cancel);
    expect(await screen.findByRole("alert")).toHaveTextContent(/final and cannot be cancelled/);
  });

  it("retries a failed run as a NEW run and leaves the old one as history", async () => {
    const fake = createFake({ runs: [makeRun("ANL-073", "failed", { failure_code: "evidence_unavailable", failure_message: "The preserved EvidenceObject could not be read from private evidence storage." })] });
    const user = userEvent.setup();
    openExamination();
    await user.click(await screen.findByRole("button", { name: "Open run ANL-073" }));
    await user.click(await screen.findByRole("button", { name: "Retry as a new run" }));
    const heading = await screen.findByRole("heading", { name: /ANL-101/ });
    expect(within(heading).getByText("Queued")).toBeInTheDocument();
    expect(fake.posts().at(-1)).toMatchObject({ path: `${BASE}/analysis-runs/ANL-073/retry` });
    expect(String(fake.posts().at(-1)?.body?.idempotency_key)).toMatch(/^ui-/);
    expect(fake.find("ANL-073")?.state).toBe("failed"); // unchanged
    const table = screen.getByRole("table", { name: "Analysis runs, newest first" });
    expect(within(table).getByRole("button", { name: "Open run ANL-073" })).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Open run ANL-101" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry as a new run" })).not.toBeInTheDocument(); // the new run is queued
    expect(heading).toHaveFocus(); // focus follows the run that was just created
  });

  it("offers retry only for failed or cancelled runs", async () => {
    createFake({ runs: [makeRun("ANL-074", "completed")] });
    const user = userEvent.setup();
    openExamination();
    await user.click(await screen.findByRole("button", { name: "Open run ANL-074" }));
    await screen.findByRole("heading", { name: "Observations (5)" });
    expect(screen.queryByRole("button", { name: /Retry/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Cancel|cancellation/ })).not.toBeInTheDocument();
  });
});

// --- Errors and permissions -------------------------------------------------------------------------

describe("Errors and permissions", () => {
  it("shows an error state with retry when the Methods cannot be loaded", async () => {
    const fake = createFake();
    const original = fake.fetchMock.getMockImplementation();
    fake.fetchMock.mockImplementation(async (input, init) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://veritas.test");
      if (url.pathname.endsWith("/examination/methods")) {
        return new Response(JSON.stringify({ error: { code: "internal_error", message: "An internal error occurred", request_id: "req-500" } }), { status: 500, headers: { "content-type": "application/json" } });
      }
      return original ? original(input, init) : new Response(null, { status: 500 });
    });
    openExamination();
    expect(await screen.findByText(/Methods unavailable|Methods could not be loaded|Methods/i, { selector: "[role=alert] *" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("shows an error for a run that cannot be loaded, and recovers when retried", async () => {
    const fake = createFake({ runs: [makeRun("ANL-080", "queued")] });
    const user = userEvent.setup();
    openExamination();
    const open = await screen.findByRole("button", { name: "Open run ANL-080" });
    fake.state.detailStatus = 500;
    await user.click(open);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Run ANL-080 could not be loaded.");
    fake.state.detailStatus = 200;
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { name: /ANL-080/ })).toBeInTheDocument();
  });

  it("lets a reviewer read runs and Observations but not start, cancel or retry anything", async () => {
    createFake({ who: reviewer, runs: [makeRun("ANL-081", "completed"), makeRun("ANL-082", "failed", { failure_code: "execution_failed", failure_message: "x." }), makeRun("ANL-083", "queued")] });
    const user = userEvent.setup();
    openExamination();
    await user.click(await screen.findByRole("button", { name: "Open run ANL-081" }));
    expect(await screen.findByRole("heading", { name: "Observations (5)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start examination" })).toBeDisabled();
    expect(screen.getByText(/can read examinations but cannot start them/)).toBeInTheDocument();
    for (const id of ["ANL-082", "ANL-083"]) {
      await user.click(screen.getByRole("button", { name: `Open run ${id}` }));
      await screen.findByRole("heading", { name: new RegExp(id) });
      expect(screen.queryByRole("button", { name: /Retry|Cancel|cancellation/ })).not.toBeInTheDocument();
    }
  });

  it("never offers execution on a demonstration Case", async () => {
    const demoSession = session(["INVESTIGATOR"], [...READ, "examination:execute"]);
    demoSession.case_capabilities = { "CASE-001": [...READ, "examination:execute"] };
    mockApi({
      "/api/v1/system": authenticatedSystemInfo,
      "/api/v1/auth/session": demoSession,
      "/api/v1/cases/CASE-001/examination/methods": list([binaryMethod]),
    });
    openExamination("/app/cases/CASE-001/examination");
    expect(await screen.findByText("Demonstration cases never execute Methods.")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Start examination" })).toBeDisabled();
    expect(screen.queryByRole("radio", { name: /EOBJ/ })).not.toBeInTheDocument();
  });
});

// --- Language, accessibility and layout --------------------------------------------------------------

describe("Language", () => {
  it("never states a conclusion about authenticity, manipulation or origin", async () => {
    createFake({ runs: [makeRun("ANL-090", "completed"), makeRun("ANL-091", "failed", { failure_code: "integrity_mismatch", failure_message: "The bytes read for this run do not match the byte count and digests recorded at intake, so no Observations were published. This is an integrity comparison only." })] });
    const user = userEvent.setup();
    const { container } = openExamination();
    await user.click(await screen.findByRole("button", { name: "Open run ANL-090" }));
    await screen.findByRole("heading", { name: "Observations (5)" });
    await user.click(screen.getByRole("button", { name: "Open run ANL-091" }));
    await screen.findByText("integrity_mismatch");
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/\b(fake|forged|manipulated|suspicious|malicious)\b/i);
    expect(text).not.toMatch(/\b(real|authentic|genuine)\b/i);
    expect(text).not.toMatch(/truth score|trust score|authenticity score|confidence|probability|\bAI\b|real\/fake/i);
    // "authenticity" appears only inside an explicit disclaimer.
    for (const match of text.matchAll(/[^.]*\bauthenticity\b[^.]*\./gi)) {
      expect(match[0]).toMatch(/does not determine authenticity|not an authenticity|does not replace/i);
    }
  });
});

describe("Accessibility", () => {
  it("has no detectable violations with Methods, objects, runs and a completed run on screen", async () => {
    createFake({ runs: [makeRun("ANL-095", "completed"), makeRun("ANL-096", "running"), makeRun("ANL-097", "failed", { failure_code: "execution_failed", failure_message: "x." })] });
    const user = userEvent.setup();
    const { container } = openExamination();
    await chooseDefaults(user);
    await user.click(screen.getByRole("button", { name: "Open run ANL-095" }));
    await screen.findByRole("heading", { name: "Observations (5)" });
    expect(await axeViolations(container)).toEqual([]);
    for (const id of ["ANL-096", "ANL-097"]) {
      await user.click(screen.getByRole("button", { name: `Open run ${id}` }));
      await screen.findByRole("heading", { name: new RegExp(id) });
      expect(await axeViolations(container)).toEqual([]);
    }
  });

  it("has no detectable violations, including heading order, with a Method explanation open", async () => {
    createFake();
    const user = userEvent.setup();
    const { container } = openExamination();
    await user.click(await screen.findByText("Explain Binary Characteristics Examination"));
    const details = container.querySelector("details");
    expect(details).toHaveAttribute("open"); // axe ignores the content of a closed <details>
    expect(await axeViolations(container)).toEqual([]);
  });

  it("is operable by keyboard: radios by arrow keys, Start by Enter, focus moves to the new run", async () => {
    const fake = createFake();
    const user = userEvent.setup();
    openExamination();
    const method = await screen.findByRole("radio", { name: /Binary Characteristics Examination/ });
    await user.click(method);
    await user.tab(); // out of the method group
    await screen.findByRole("radio", { name: /EOBJ-010/ });
    const preserved = screen.getByRole("radio", { name: /EOBJ-010/ });
    preserved.focus();
    await user.keyboard(" ");
    expect(preserved).toBeChecked();
    const start = screen.getByRole("button", { name: "Start examination" });
    start.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(fake.posts()).toHaveLength(1));
    expect(await screen.findByRole("heading", { name: /ANL-101/ })).toHaveFocus();
  });

  it("labels every control and announces run state changes in a live region", async () => {
    createFake({ runs: [makeRun("ANL-098", "queued")] });
    const user = userEvent.setup();
    openExamination();
    await user.click(await screen.findByRole("button", { name: "Open run ANL-098" }));
    for (const radio of await screen.findAllByRole("radio")) {
      expect(radio).toHaveAccessibleName();
    }
    const live = await screen.findByText("ANL-098 is queued");
    expect(live.getAttribute("role")).toBe("status");
  });
});

describe("Mobile layout contract", () => {
  it("stacks the workstation in one column on narrow screens and lets long identifiers wrap", async () => {
    createFake({ runs: [makeRun("ANL-099", "completed")], objects: { "EVD-010": [object("EOBJ-010", "EVD-010", "PRESERVED", { original_filename: "a-very-long-original-filename-without-any-break-opportunity-0123456789.bin" })], "EVD-011": [] } });
    const { container } = openExamination();
    const filename = await screen.findByText(/a-very-long-original-filename/);
    expect(filename.className).toMatch(/break-all/); // cannot overflow a 390px viewport
    const grid = container.querySelector(".grid.gap-6");
    expect(grid?.className).toMatch(/xl:grid-cols-2/);
    expect(grid?.className).not.toMatch(/(^|\s)grid-cols-[2-9]/); // no fixed multi-column layout at base size
    const table = await screen.findByRole("table", { name: "Analysis runs, newest first" });
    expect(table.className).toMatch(/max-md:block/); // rows reflow into stacked cards
    for (const column of within(table).queryAllByRole("columnheader", { hidden: true })) {
      expect(column.closest("thead")?.className).toMatch(/max-md:sr-only/);
    }
  });
});
