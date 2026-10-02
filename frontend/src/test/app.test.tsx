import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { CASE_NAV, casePath } from "../app-shell/navigation";
import { mockApi, renderAt } from "./fixtures";

describe("application boot and routing", () => {
  it("redirects /app to the case list and shows the demonstration case", async () => {
    mockApi();
    renderAt(<AppRoutes />, "/app");
    expect(await screen.findByRole("heading", { level: 1, name: "Cases" })).toBeInTheDocument();
    expect(await screen.findByText("Synthetic Demonstration Case")).toBeInTheDocument();
  });

  it("opens the case workspace with objective, state and demonstration notice", async () => {
    mockApi();
    renderAt(<AppRoutes />, "/app/cases/CASE-001");
    expect(await screen.findByRole("heading", { level: 1, name: "Synthetic Demonstration Case" })).toBeInTheDocument();
    expect(screen.getByText("Establish the fixture timeline.")).toBeInTheDocument();
    expect(screen.getAllByText(/demonstration data/i).length).toBeGreaterThan(0);
  });

  it.each(CASE_NAV.map((item) => [item.label, item.segment] as const))("renders the %s section", async (label, segment) => {
    mockApi();
    renderAt(<AppRoutes />, casePath("CASE-001", segment));
    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent(label === "Graph" ? "Case Knowledge Graph" : label);
  });

  it("marks reserved sections as unavailable using backend capability notes", async () => {
    mockApi();
    renderAt(<AppRoutes />, casePath("CASE-001", "timeline"));
    expect(await screen.findByText("Timeline reconstruction is not available in this version.")).toBeInTheDocument();
    expect(screen.getByText("Requires temporal observations.")).toBeInTheDocument();
  });

  it("rejects malformed case identifiers without calling the API for them", async () => {
    const fetchMock = mockApi();
    renderAt(<AppRoutes />, "/app/cases/not-a-case");
    expect(await screen.findByText("Not a case identifier.")).toBeInTheDocument();
    expect(fetchMock.mock.calls.map((c) => String(c[0]))).not.toContain("/api/v1/cases/not-a-case");
  });

  it("shows a not-found state for unknown routes", async () => {
    mockApi();
    renderAt(<AppRoutes />, "/app/nowhere");
    expect(await screen.findByText("This address does not exist.")).toBeInTheDocument();
  });

  it("shows an unknown case as not found", async () => {
    mockApi();
    renderAt(<AppRoutes />, "/app/cases/CASE-404");
    expect(await screen.findByText(/Case CASE-404/)).toBeInTheDocument();
  });

  it("explains restricted access when the API requires authentication", async () => {
    mockApi({ "/api/v1/cases": 401 });
    renderAt(<AppRoutes />, "/app/cases");
    expect(await screen.findByText(/sign-in required/i)).toBeInTheDocument();
  });

  it("shows an error state, not a blank page, when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("network down"))));
    renderAt(<AppRoutes />, "/app/cases");
    expect((await screen.findAllByText(/could not be reached|not reachable|unreachable/i)).length).toBeGreaterThan(0);
  });

  it("renders the showcase with the concept label and backend capabilities", async () => {
    mockApi();
    renderAt(<AppRoutes />, "/");
    expect(await screen.findByRole("heading", { level: 1, name: /automate work,\s*not accountability/i })).toBeInTheDocument();
    expect(screen.getByText(/illustrative only; it does not depict an executed examination/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("User identity & access control")).toBeInTheDocument());
    expect(screen.getAllByText("Reserved").length).toBeGreaterThan(0);
  });
});
