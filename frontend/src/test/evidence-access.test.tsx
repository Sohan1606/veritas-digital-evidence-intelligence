import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EvidenceIntegrityVerification, EvidenceObject } from "../api/types";
import { EvidenceObjectsPanel } from "../features/evidence/EvidenceIntakeControls";
import { NOT_AUTHENTICITY, PreservedObjectAccess } from "../features/evidence/EvidenceObjectAccess";
import {
  evidence,
  evidenceDetail,
  evidenceObject,
  mockApi,
  verificationMatch,
  verificationMismatch,
  verificationUnavailable,
} from "./fixtures";

const CONTENT = "/api/v1/cases/CASE-001/evidence/EVD-001/objects/EOBJ-001/content";
const VERIFY = "/api/v1/cases/CASE-001/evidence/EVD-001/objects/EOBJ-001/verify";
const SYNTHETIC_BYTES = "SYNTHETIC-RETRIEVED-EVIDENCE-BYTES";
const INTEGRITY_LABELS = {
  MATCH: "Integrity match",
  MISMATCH: "Integrity mismatch",
  UNAVAILABLE: "Verification unavailable",
} as const;

const FRAUD_WORDING = /\b(fake|forged?|forgery|fraud\w*|inauthentic|malicious|tamper\w*|counterfeit)\b/i;

const envelope = (status: number, code: string, message: string) =>
  new Response(JSON.stringify({ error: { code, message, request_id: "req-access" } }), {
    status,
    headers: { "content-type": "application/json", "x-request-id": "req-access" },
  });

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

// A string body: jsdom's Blob is not Node's, and Response would stringify it.
const bytes = () =>
  new Response(SYNTHETIC_BYTES, {
    status: 200,
    headers: {
      "content-type": "application/pdf",
      "content-disposition": 'attachment; filename="EOBJ-001.bin"',
      "x-request-id": "req-access",
    },
  });

type Handler = (init?: RequestInit) => Response | Promise<Response>;

/** Routes only the two V2.2 endpoints; every other path falls through to the standard fixtures. */
function stubAccess(handlers: { content?: Handler; verify?: Handler }) {
  const fallback = mockApi();
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://veritas.test");
    if (url.pathname === CONTENT && handlers.content) return handlers.content(init);
    if (url.pathname === VERIFY && handlers.verify) return handlers.verify(init);
    return fallback(input);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

let saved: { download: string; href: string }[];
let objectUrls: Blob[];
let scheduled: { callback: () => void; delay: number | undefined }[];

beforeEach(() => {
  saved = [];
  objectUrls = [];
  scheduled = [];
  URL.createObjectURL = vi.fn((blob: Blob) => {
    objectUrls.push(blob);
    return "blob:veritas-test";
  });
  URL.revokeObjectURL = vi.fn();
  // jsdom cannot navigate; record what the browser would be asked to save instead.
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    saved.push({ download: this.download, href: this.href });
  });
  const realSetTimeout = window.setTimeout.bind(window);
  vi.spyOn(window, "setTimeout").mockImplementation(((callback: () => void, delay?: number, ...rest: unknown[]) => {
    if (delay === 10_000) {
      scheduled.push({ callback, delay });
      return 0;
    }
    return realSetTimeout(callback, delay, ...rest);
  }) as typeof window.setTimeout);
});

afterEach(() => {
  document.cookie = "veritas_csrf=; Max-Age=0";
});

function renderAccess(item: EvidenceObject = evidenceObject) {
  return render(<PreservedObjectAccess caseId="CASE-001" item={item} />);
}

async function expectNoAxeViolations(container: HTMLElement) {
  const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
  expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

describe("retrieval", () => {
  it("always states that verification is not an authenticity determination, and starts Ready", () => {
    stubAccess({});
    renderAccess();
    expect(screen.getByText(new RegExp(NOT_AUTHENTICITY))).toBeInTheDocument();
    expect(NOT_AUTHENTICITY).toBe("Integrity verification is not an authenticity determination.");
    expect(screen.getByRole("button", { name: "Retrieve" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Verify" })).toBeEnabled();
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("fetches through a same-origin relative URL and hands the bytes to the browser, never to state", async () => {
    const fetchMock = stubAccess({ content: bytes });
    const { container } = renderAccess();
    await userEvent.click(screen.getByRole("button", { name: "Retrieve" }));

    expect(await screen.findByText("Completed")).toBeInTheDocument();
    const [path, init] = fetchMock.mock.calls.find(([input]) => input === CONTENT)!;
    expect(path).toBe(CONTENT);
    expect(String(path).startsWith("/")).toBe(true);
    expect(init).toMatchObject({ credentials: "same-origin" });
    // The bytes went to the browser's download mechanism, named by the backend's header.
    expect(objectUrls).toHaveLength(1);
    expect(await objectUrls[0]!.text()).toBe(SYNTHETIC_BYTES);
    expect(saved).toEqual([{ download: "EOBJ-001.bin", href: "blob:veritas-test" }]);
    expect(screen.getByText("EOBJ-001.bin")).toBeInTheDocument();
    // ...and not into the rendered output or any link.
    expect(container.textContent).not.toContain(SYNTHETIC_BYTES);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    // The transient object URL is scheduled for release.
    expect(scheduled).toHaveLength(1);
    scheduled[0]!.callback();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:veritas-test");
  });

  it("falls back to a deterministic name when the header is missing or unsafe", async () => {
    stubAccess({
      content: () => new Response("x", { status: 200, headers: { "content-disposition": 'attachment; filename="../../etc/passwd"' } }),
    });
    renderAccess();
    await userEvent.click(screen.getByRole("button", { name: "Retrieve" }));
    await screen.findByText("Completed");
    expect(saved[0]!.download).toBe("EOBJ-001.bin");
  });

  it("shows Retrieving… while in flight and ignores a second activation", async () => {
    let release: (response: Response) => void = () => undefined;
    const fetchMock = stubAccess({ content: () => new Promise<Response>((resolve) => (release = resolve)) });
    renderAccess();
    const button = screen.getByRole("button", { name: "Retrieve" });
    await userEvent.click(button);
    expect(await screen.findByText("Retrieving…")).toBeInTheDocument();
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(fetchMock.mock.calls.filter(([input]) => input === CONTENT)).toHaveLength(1);
    release(bytes());
    expect(await screen.findByText("Completed")).toBeInTheDocument();
    expect(button).toBeEnabled();
  });

  it("reports Retrieval unavailable with the sanitized message and request id", async () => {
    stubAccess({ content: () => envelope(503, "evidence_storage_unavailable", "Private evidence storage is unavailable") });
    renderAccess();
    await userEvent.click(screen.getByRole("button", { name: "Retrieve" }));
    expect(await screen.findByText("Retrieval unavailable")).toBeInTheDocument();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Private evidence storage is unavailable");
    expect(alert).toHaveTextContent("req-access");
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Retrieve" })).toBeEnabled(); // can be retried
  });

  it("treats a network failure as Retrieval unavailable", async () => {
    stubAccess({ content: () => Promise.reject(new TypeError("Failed to fetch")) });
    renderAccess();
    await userEvent.click(screen.getByRole("button", { name: "Retrieve" }));
    expect(await screen.findByText("Retrieval unavailable")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("could not be reached");
  });
});

describe("verification", () => {
  it("verifies with a CSRF-protected same-origin POST and shows an integrity match", async () => {
    document.cookie = "veritas_csrf=synthetic-csrf";
    const fetchMock = stubAccess({ verify: () => json(verificationMatch) });
    renderAccess();
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));

    const badge = await screen.findByText("Integrity match");
    expect(badge).toBeInTheDocument();
    const [path, init] = fetchMock.mock.calls.find(([input]) => input === VERIFY)!;
    expect(String(path).startsWith("/")).toBe(true);
    expect(init).toMatchObject({ method: "POST", credentials: "same-origin" });
    expect((init as RequestInit).headers).toMatchObject({ "X-CSRF-Token": "synthetic-csrf" });
    expect(screen.getByText(verificationMatch.message)).toBeInTheDocument();
    expect(screen.getByText("a".repeat(64))).toBeInTheDocument();
    expect(screen.getByText("USR-003")).toBeInTheDocument();
    // Still the standing disclaimer; a match never reads as authenticity.
    expect(screen.getByText(new RegExp(NOT_AUTHENTICITY))).toBeInTheDocument();
    expect(screen.queryByText(/authentic(?!ity)/i)).not.toBeInTheDocument();
  });

  it("shows Verifying… while in flight and ignores a second activation", async () => {
    let release: (response: Response) => void = () => undefined;
    const fetchMock = stubAccess({ verify: () => new Promise<Response>((resolve) => (release = resolve)) });
    renderAccess();
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    const busy = await screen.findByRole("button", { name: "Verifying…" });
    expect(busy).toBeDisabled();
    await userEvent.click(busy);
    expect(fetchMock.mock.calls.filter(([input]) => input === VERIFY)).toHaveLength(1);
    release(json(verificationMatch));
    expect(await screen.findByText("Integrity match")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Verify" })).toBeEnabled();
  });

  it("shows the recorded and recomputed values for a mismatch without accusatory wording", async () => {
    stubAccess({ verify: () => json(verificationMismatch), content: bytes });
    const { container } = renderAccess();
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByText("Integrity mismatch")).toBeInTheDocument();
    expect(screen.getByText(verificationMismatch.message)).toBeInTheDocument();
    // Both sides of the comparison are visible, with an explicit (non-colour) difference marker.
    expect(screen.getAllByText("a".repeat(64))).toHaveLength(1);
    expect(screen.getByText("c".repeat(64))).toBeInTheDocument();
    expect(screen.getByText("b".repeat(128))).toBeInTheDocument();
    expect(screen.getByText("d".repeat(128))).toBeInTheDocument();
    expect(screen.getByText("127")).toBeInTheDocument();
    expect(screen.getAllByText("Differs")).toHaveLength(3);
    expect(screen.getByText(/does not\s+determine why/i)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(FRAUD_WORDING);
    // Retrieval is not disabled by a mismatch.
    expect(screen.getByRole("button", { name: "Retrieve" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Retrieve" }));
    expect(await screen.findByText("Completed")).toBeInTheDocument();
  });

  it("marks only the values that actually differ", async () => {
    stubAccess({ verify: () => json({ ...verificationMismatch, computed_byte_size: 128, computed_sha512: "b".repeat(128) }) });
    renderAccess();
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByText("Integrity mismatch");
    expect(screen.getAllByText("Differs")).toHaveLength(1);
    expect(screen.getAllByText("Equal")).toHaveLength(2);
  });

  it("shows Verification unavailable only for the UNAVAILABLE result, and says it is not a mismatch", async () => {
    stubAccess({ verify: () => json(verificationUnavailable) });
    const { container } = renderAccess();
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByText("Verification unavailable")).toBeInTheDocument();
    expect(screen.getByText(verificationUnavailable.message)).toBeInTheDocument();
    expect(screen.getByText(/this is not a mismatch: nothing about the stored bytes was established/i)).toBeInTheDocument();
    // The wording never claims a verification that did not happen.
    expect(container.textContent).not.toMatch(/\bverified\b/i);
    expect(screen.getByText(/verification run/i)).toBeInTheDocument();
    expect(screen.queryByText("Integrity mismatch")).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(FRAUD_WORDING);
  });

  it.each([
    [401, "unauthenticated", "Unauthorized"],
    [403, "access_denied", "A valid session-bound CSRF token is required"],
    [404, "not_found", "Resource was not found"],
    [409, "evidence_state_conflict", "Only a preserved evidence object can be verified"],
    [422, "validation_error", "Request validation failed"],
    [500, "internal_error", "An internal error occurred"],
    [503, "service_unavailable", "Service Unavailable"],
  ])("never labels an HTTP %i error as Verification unavailable", async (status, code, message) => {
    stubAccess({ verify: () => envelope(status, code, message) });
    renderAccess();
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Verification could not be completed");
    expect(alert).toHaveTextContent(message);
    expect(alert).toHaveTextContent("req-access");
    expect(screen.queryByText("Verification unavailable")).not.toBeInTheDocument();
    expect(screen.queryByText("Integrity match")).not.toBeInTheDocument();
    expect(screen.queryByText("Integrity mismatch")).not.toBeInTheDocument();
  });

  it("does not render an unexpected 200 body as a result", async () => {
    stubAccess({ verify: () => new Response("<!doctype html>", { status: 200, headers: { "content-type": "text/html" } }) });
    renderAccess();
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("unexpected verification response");
    expect(screen.queryByText(/integrity (match|mismatch)/i)).not.toBeInTheDocument();
  });

  it("rejects a verification whose result is not one of the three defined values", async () => {
    stubAccess({ verify: () => json({ ...verificationMatch, result: "AUTHENTIC" }) });
    renderAccess();
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("unexpected verification response");
    expect(screen.queryByText("AUTHENTIC")).not.toBeInTheDocument();
    expect(screen.queryByText(/integrity (match|mismatch)/i)).not.toBeInTheDocument();
  });

  it.each<[string, EvidenceIntegrityVerification]>([
    ["match", verificationMatch],
    ["mismatch", verificationMismatch],
    ["unavailable", verificationUnavailable],
  ])("has no accessibility violations after a %s result", async (_name, verification) => {
    stubAccess({ verify: () => json(verification) });
    const { container } = renderAccess();
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByText(INTEGRITY_LABELS[verification.result]);
    await expectNoAxeViolations(container);
  });
});

describe("EvidenceObject card integration", () => {
  const preserved = evidenceObject;
  const quarantined: EvidenceObject = { ...evidenceObject, id: "EOBJ-002", original_filename: "quarantined-upload.jpg", state: "QUARANTINED", validation_status: "pending", preserved_at: null, preserved_by: null };
  const rejected: EvidenceObject = { ...evidenceObject, id: "EOBJ-003", original_filename: "rejected-upload.jpg", state: "REJECTED", validation_status: "rejected", preserved_at: null, preserved_by: null };

  function renderPanel(permissions: string[]) {
    mockApi({
      "/api/v1/cases/CASE-001/evidence/EVD-001/intake": { ...evidenceDetail, objects: [preserved, quarantined, rejected] },
    });
    render(<EvidenceObjectsPanel caseId="CASE-001" evidence={evidence[0]!} permissions={permissions} onChanged={() => undefined} />);
  }

  it("offers Retrieve and Verify for PRESERVED objects only", async () => {
    renderPanel(["evidence:read", "custody:read"]);
    await screen.findByText("synthetic-photo.jpg");
    expect(screen.getAllByRole("button", { name: "Retrieve" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Verify" })).toHaveLength(1);
    const group = screen.getByRole("group", { name: "Preserved bytes of EOBJ-001" });
    expect(within(group).getByRole("button", { name: "Retrieve" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: /EOBJ-002/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: /EOBJ-003/ })).not.toBeInTheDocument();
  });

  it("offers neither action without the evidence:read capability", async () => {
    renderPanel(["custody:read"]);
    await screen.findByText("synthetic-photo.jpg");
    expect(screen.queryByRole("button", { name: "Retrieve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Verify" })).not.toBeInTheDocument();
  });

  it("never renders retrieval as a hyperlink", async () => {
    renderPanel(["evidence:read", "custody:read"]);
    await screen.findByText("synthetic-photo.jpg");
    expect(screen.queryByRole("link", { name: /download|retrieve/i })).not.toBeInTheDocument();
    expect(document.querySelector("a[download]")).toBeNull();
  });
});
