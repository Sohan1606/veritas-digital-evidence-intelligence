import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AppRoutes } from "../App";
import type { SessionInfo, SystemInfo } from "../api/types";
import { authenticatedSystemInfo, auditEvents, caseDetail, finding, graph, systemInfo } from "./fixtures";
import { renderAt } from "./fixtures";

type FakeCall = { method: string; path: string; body?: unknown };
type LoginAccount = { password: string; session: SessionInfo };

const signedOutSession: SessionInfo = {
  authenticated: false,
  user_id: null,
  display_name: null,
  organization_ids: [],
  roles: [],
  capabilities: [],
  case_capabilities: {},
  session_id: null,
  expires_at: null,
  demonstration: false,
};

function userSession(
  userId: string,
  displayName: string,
  roles: string[],
  capabilities: string[],
  caseCapabilities: Record<string, string[]> = {},
): SessionInfo {
  return {
    authenticated: true,
    user_id: userId,
    display_name: displayName,
    organization_ids: ["ORG-001"],
    roles,
    capabilities,
    case_capabilities: caseCapabilities,
    session_id: `SES-${userId}`,
    expires_at: "2026-10-01T00:00:00Z",
    demonstration: false,
  };
}

const caseReaderCapabilities = ["case:read", "evidence:read", "findings:read", "claims:read", "graph:read"];
const investigatorSession = userSession("USR-201", "Investigator A", ["INVESTIGATOR"], caseReaderCapabilities, {
  "CASE-001": [...caseReaderCapabilities, "case_audit:read", "examination:read"],
});
const researcherSession = userSession("USR-202", "Researcher A", ["RESEARCHER"], caseReaderCapabilities, {
  "CASE-001": [...caseReaderCapabilities, "examination:read"],
});
const administratorSession = userSession(
  "USR-101",
  "Access Administrator",
  ["ADMINISTRATOR"],
  ["identity:read", "users:read", "users:manage", "roles:assign", "security_audit:read"],
);
const userASession = userSession("USR-A", "User A", ["INVESTIGATOR"], caseReaderCapabilities, {
  "CASE-001": [...caseReaderCapabilities, "case_audit:read"],
});
const userBSession = userSession("USR-B", "User B", ["INVESTIGATOR"], caseReaderCapabilities, {
  "CASE-001": [...caseReaderCapabilities, "case_audit:read"],
});

const adminUsers = {
  items: [{
    id: "USR-201",
    username: "investigator.a",
    display_name: "Investigator A",
    status: "active" as const,
    organization_id: "ORG-001",
    roles: ["INVESTIGATOR"],
    case_assignments: [{ role_id: "ROLE-001", role: "INVESTIGATOR", case_id: "CASE-001" }],
  }],
  count: 1,
};
const adminRoles = {
  items: [{ id: "ROLE-001", name: "INVESTIGATOR", capabilities: caseReaderCapabilities, assignable_in_console: true }],
  count: 1,
};
const securityAudit = {
  items: [{
    id: "AUD-010",
    occurred_at: "2026-09-28T12:00:00Z",
    actor: "USR-101",
    action: "role.assigned",
    entity_type: "user",
    entity_id: "USR-201",
    details: { role_id: "ROLE-001", case_id: "CASE-001" },
  }],
  count: 1,
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "x-request-id": "req-v2-frontend-test" },
  });
}

/**
 * A frontend-only API stub to exercise screens and identity transitions. It asserts
 * client behavior only; it is not evidence of backend authorization.
 */
function mockV2Api(options: {
  initialSession?: SessionInfo;
  loginAccounts?: Record<string, LoginAccount>;
  caseTitles?: Record<string, string>;
  delayCaseForUserId?: string;
} = {}) {
  let currentSession = options.initialSession ?? signedOutSession;
  const calls: FakeCall[] = [];
  let markOldCaseStarted: (() => void) | undefined;
  let releaseOldCase: (() => void) | undefined;
  const oldCaseStarted = new Promise<void>((resolve) => { markOldCaseStarted = resolve; });
  const oldCaseReleased = new Promise<void>((resolve) => { releaseOldCase = resolve; });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://veritas.test");
    const method = init?.method ?? (input instanceof Request ? input.method : "GET");
    let body: unknown;
    if (typeof init?.body === "string") {
      try { body = JSON.parse(init.body) as unknown; } catch { body = init.body; }
    }
    calls.push({ method, path: url.pathname, body });

    if (url.pathname === "/api/v1/auth/session" && method === "GET") return jsonResponse(currentSession);
    if (url.pathname === "/api/v1/auth/login" && method === "POST") {
      const credentials = body as { username?: string; password?: string };
      const account = credentials.username ? options.loginAccounts?.[credentials.username] : undefined;
      if (!account || credentials.password !== account.password) {
        return jsonResponse({ error: { code: "invalid_credentials", message: "Invalid username or password", request_id: "req-v2-frontend-test" } }, 401);
      }
      currentSession = account.session;
      return jsonResponse(currentSession);
    }
    if (url.pathname === "/api/v1/auth/logout" && method === "POST") {
      currentSession = signedOutSession;
      return jsonResponse({ status: "signed_out" });
    }
    if (url.pathname === "/api/v1/system" && method === "GET") {
      const base: SystemInfo = currentSession.authenticated
        ? authenticatedSystemInfo
        : currentSession.demonstration
          ? systemInfo
          : { ...systemInfo, access_mode: "restricted", principal: null };
      return jsonResponse(base);
    }
    if (url.pathname === "/api/v1/cases" && method === "GET") {
      const title = currentSession.user_id ? `Cases visible to ${currentSession.display_name}` : caseDetail.title;
      return jsonResponse({ items: [{ ...caseDetail, title }], count: 1 });
    }
    if (url.pathname === "/api/v1/cases/CASE-001" && method === "GET") {
      const requestUserId = currentSession.user_id ?? "";
      const title = options.caseTitles?.[requestUserId] ?? caseDetail.title;
      if (requestUserId === options.delayCaseForUserId) {
        markOldCaseStarted?.();
        await oldCaseReleased;
      }
      return jsonResponse({ ...caseDetail, title });
    }
    if (url.pathname === "/api/v1/cases/CASE-001/audit-events" && method === "GET") {
      return jsonResponse({ items: auditEvents, count: auditEvents.length });
    }
    if (url.pathname === "/api/v1/cases/CASE-001/claims" && method === "GET") {
      return jsonResponse({ items: [], count: 0 });
    }
    if (url.pathname === "/api/v1/cases/CASE-001/findings" && method === "GET") {
      return jsonResponse({ items: [finding], count: 1 });
    }
    if (url.pathname === "/api/v1/cases/CASE-001/graph" && method === "GET") {
      return jsonResponse(graph);
    }
    if (url.pathname === "/api/v1/admin/users" && method === "GET") return jsonResponse(adminUsers);
    if (url.pathname === "/api/v1/admin/roles" && method === "GET") return jsonResponse(adminRoles);
    if (url.pathname === "/api/v1/admin/security-audit" && method === "GET") return jsonResponse(securityAudit);
    return jsonResponse({ error: { code: "not_found", message: "Not found.", request_id: "req-v2-frontend-test" } }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    calls,
    fetchMock,
    oldCaseStarted,
    releaseOldCase: () => releaseOldCase?.(),
  };
}

describe("V2 provisioned identity frontend", () => {
  it("bootstraps the authenticated session and displays the server-returned identity", async () => {
    const api = mockV2Api({ initialSession: investigatorSession });
    renderAt(<AppRoutes />, "/app/cases");
    expect(await screen.findByRole("heading", { level: 1, name: "Cases" })).toBeInTheDocument();
    expect(await screen.findByText("Investigator A")).toBeInTheDocument();
    expect(api.calls.filter((call) => call.path === "/api/v1/auth/session")).toHaveLength(1);
    expect(authenticatedSystemInfo.principal).toBe("user");
  });

  it("shows the provisioned-identity SignInPage for an unauthenticated session", async () => {
    mockV2Api();
    renderAt(<AppRoutes />, "/app");
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText(/there is no public sign-up/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
  });

  it("handles successful sign-in and publishes the authenticated session to the shell", async () => {
    mockV2Api({ loginAccounts: { "investigator.a": { password: "fixture-password", session: investigatorSession } } });
    const user = userEvent.setup();
    renderAt(<AppRoutes />, "/app");
    await screen.findByRole("heading", { level: 1, name: "Sign in" });
    await user.type(screen.getByLabelText("Username"), "investigator.a");
    await user.type(screen.getByLabelText("Password"), "fixture-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Cases" })).toBeInTheDocument();
    expect(await screen.findByText("Investigator A")).toBeInTheDocument();
  });

  it("handles failed sign-in generically and clears the password field", async () => {
    mockV2Api();
    const user = userEvent.setup();
    renderAt(<AppRoutes />, "/app");
    await screen.findByRole("heading", { level: 1, name: "Sign in" });
    const password = screen.getByLabelText("Password");
    await user.type(screen.getByLabelText("Username"), "not-provisioned");
    await user.type(password, "wrong-fixture-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Sign-in failed. Check the credentials or contact your administrator.");
    expect(password).toHaveValue("");
    expect(screen.getByLabelText("Username")).toHaveValue("not-provisioned");
  });

  it("shows role-aware case and administration navigation from session capabilities", async () => {
    mockV2Api({ initialSession: researcherSession });
    const { unmount } = renderAt(<AppRoutes />, "/app/cases/CASE-001");
    const nav = await screen.findByRole("navigation", { name: "Primary" });
    expect(await screen.findByRole("heading", { level: 1, name: caseDetail.title })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Audit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Identity management" })).not.toBeInTheDocument();
    expect(nav).toBeInTheDocument();
    unmount();

    mockV2Api({ initialSession: administratorSession });
    renderAt(<AppRoutes />, "/app/cases");
    expect(await screen.findByRole("link", { name: "Identity management" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Security audit" })).toBeInTheDocument();
  });

  it("renders the administrator identity directory from its typed API response", async () => {
    mockV2Api({ initialSession: administratorSession });
    renderAt(<AppRoutes />, "/app/admin/users");
    expect(await screen.findByRole("heading", { level: 1, name: "Identity management" })).toBeInTheDocument();
    expect(await screen.findByText("Investigator A")).toBeInTheDocument();
    expect(screen.getByText("investigator.a", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("INVESTIGATOR · CASE-001")).toBeInTheDocument();
  });

  it("renders canonical security-Audit events for a capability-bearing identity", async () => {
    mockV2Api({ initialSession: administratorSession });
    renderAt(<AppRoutes />, "/app/admin/security-audit");
    expect(await screen.findByRole("heading", { level: 1, name: "Security audit" })).toBeInTheDocument();
    expect(await screen.findByText("role.assigned")).toBeInTheDocument();
    expect(screen.getByText("USR-201")).toBeInTheDocument();
  });

  it("renders access denied and does not request the administrator directory without capability", async () => {
    const api = mockV2Api({ initialSession: investigatorSession });
    renderAt(<AppRoutes />, "/app/admin/users");
    expect(await screen.findByText("Access denied.")).toBeInTheDocument();
    expect(api.calls.some((call) => call.path === "/api/v1/admin/users")).toBe(false);
  });

  it("does not surface a User A in-flight response after User B establishes a new session", async () => {
    const api = mockV2Api({
      initialSession: userASession,
      loginAccounts: { "user.b": { password: "user-b-password", session: userBSession } },
      caseTitles: { "USR-A": "Late User A private response", "USR-B": "User B fresh CASE-001" },
      delayCaseForUserId: "USR-A",
    });
    const user = userEvent.setup();
    renderAt(<AppRoutes />, "/app/cases/CASE-001");
    await screen.findByRole("button", { name: "Sign out" });
    await api.oldCaseStarted;

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByRole("heading", { level: 1, name: "Sign in" });
    expect(window.sessionStorage.getItem("veritas.activeCase")).toBeNull();
    await user.type(screen.getByLabelText("Username"), "user.b");
    await user.type(screen.getByLabelText("Password"), "user-b-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("link", { name: "Cases visible to User B" }));
    expect(await screen.findByRole("heading", { level: 1, name: "User B fresh CASE-001" })).toBeInTheDocument();

    await act(async () => {
      api.releaseOldCase();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.getByRole("heading", { level: 1, name: "User B fresh CASE-001" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1, name: "Late User A private response" })).not.toBeInTheDocument();
    expect(api.calls.filter((call) => call.path === "/api/v1/cases/CASE-001" && call.method === "GET")).toHaveLength(2);
  });

  it("drops User A's ready CASE-001 resource on logout and fetches a new User B response", async () => {
    const api = mockV2Api({
      initialSession: userASession,
      loginAccounts: { "user.b": { password: "user-b-password", session: userBSession } },
      caseTitles: { "USR-A": "User A private CASE-001", "USR-B": "User B fresh CASE-001" },
    });
    window.sessionStorage.setItem("veritas.activeCase", "CASE-001");
    const user = userEvent.setup();
    renderAt(<AppRoutes />, "/app/cases/CASE-001");

    expect(await screen.findByRole("heading", { level: 1, name: "User A private CASE-001" })).toBeInTheDocument();
    expect(api.calls.filter((call) => call.path === "/api/v1/cases/CASE-001" && call.method === "GET")).toHaveLength(1);
    await waitFor(() => expect(window.sessionStorage.getItem("veritas.activeCase")).toBe("CASE-001"));

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(window.sessionStorage.getItem("veritas.activeCase")).toBeNull();

    await user.type(screen.getByLabelText("Username"), "user.b");
    await user.type(screen.getByLabelText("Password"), "user-b-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    const userBCaseLink = await screen.findByRole("link", { name: "Cases visible to User B" });
    await user.click(userBCaseLink);

    expect(await screen.findByRole("heading", { level: 1, name: "User B fresh CASE-001" })).toBeInTheDocument();
    expect(api.calls.filter((call) => call.path === "/api/v1/cases/CASE-001" && call.method === "GET")).toHaveLength(2);
    expect(screen.queryByRole("heading", { level: 1, name: "User A private CASE-001" })).not.toBeInTheDocument();
    expect(api.calls.some((call) => call.path === "/api/v1/auth/logout" && call.method === "POST")).toBe(true);
  });
});
