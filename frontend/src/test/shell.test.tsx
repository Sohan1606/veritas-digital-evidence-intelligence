import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { describe, expect, it } from "vitest";
import { AppRoutes } from "../App";
import { installBrowserStubs } from "./setup";
import { mockApi, renderAt } from "./fixtures";

const NAV_LABELS = ["My Work", "Cases", "Evidence", "Examination", "Findings", "Claims", "Timeline", "Graph", "Review", "Reports", "Audit"];

async function expectNoAxeViolations(container: HTMLElement) {
  const results = await axe.run(container, {
    // jsdom has no layout or computed colours; contrast is reviewed visually instead.
    rules: { "color-contrast": { enabled: false } },
  });
  const summary = results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(summary).toEqual([]);
}

describe("investigator shell", () => {
  it("offers every workspace section in the primary navigation, in order", async () => {
    mockApi();
    renderAt(<AppRoutes />, "/app/cases/CASE-001");
    await screen.findByRole("heading", { level: 1, name: "Synthetic Demonstration Case" });
    const nav = screen.getAllByRole("navigation", { name: "Primary" })[0]!;
    const labels = within(nav)
      .getAllByRole("link")
      .map((link) => NAV_LABELS.find((label) => link.textContent?.includes(label)))
      .filter(Boolean);
    expect(labels).toEqual(NAV_LABELS);
  });

  it("marks the active section with aria-current", async () => {
    mockApi();
    renderAt(<AppRoutes />, "/app/cases/CASE-001/findings");
    await screen.findByRole("heading", { level: 1, name: "Findings" });
    const nav = screen.getAllByRole("navigation", { name: "Primary" })[0]!;
    const current = within(nav).getAllByRole("link").filter((l) => l.getAttribute("aria-current") === "page");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("Findings");
  });

  it("provides a skip link and a focusable main region", async () => {
    mockApi();
    renderAt(<AppRoutes />, "/app/cases");
    await screen.findByRole("heading", { level: 1, name: "Cases" });
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
  });

  it("opens global search with Ctrl+K and navigates to a record", async () => {
    mockApi();
    const user = userEvent.setup();
    renderAt(<AppRoutes />, "/app/cases/CASE-001");
    await screen.findByRole("heading", { level: 1, name: "Synthetic Demonstration Case" });
    act(() => {
      fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    });
    const input = await screen.findByRole("combobox", { name: /search cases/i });
    await user.type(input, "FND-001");
    await waitFor(() => expect(screen.getAllByRole("option").length).toBeGreaterThan(0));
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("heading", { level: 2, name: "Fixture finding title" })).toBeInTheDocument();
  });

  it("uses the textual graph presentation by default on small screens", async () => {
    installBrowserStubs(390);
    mockApi();
    renderAt(<AppRoutes />, "/app/cases/CASE-001/graph");
    const tab = await screen.findByRole("tab", { name: "table" });
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("table", { name: /relationships/i })).toBeInTheDocument();
  });

  it.each([
    ["case overview", "/app/cases/CASE-001", "Synthetic Demonstration Case"],
    ["evidence profile", "/app/cases/CASE-001/evidence/EVD-001", "fixture-photo.jpg"],
    ["finding", "/app/cases/CASE-001/findings/FND-001", "Fixture finding title"],
    ["graph", "/app/cases/CASE-001/graph", "Case Knowledge Graph"],
  ])("has no detectable accessibility violations on the %s", async (_name, path, text) => {
    mockApi();
    const { container } = renderAt(<AppRoutes />, path);
    await screen.findAllByText(text);
    await waitFor(() => expect(screen.queryByRole("status", { name: /loading/i })).not.toBeInTheDocument());
    await expectNoAxeViolations(container);
  });
});
