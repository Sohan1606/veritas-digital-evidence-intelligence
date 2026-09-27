import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AppRoutes } from "../App";
import { mockApi, profile, renderAt } from "./fixtures";

const SECTIONS = ["Identity", "Integrity", "Provenance", "Quality", "Acquisition context", "Classification"];

describe("Evidence Profile", () => {
  it("renders all six sections with their declared status", async () => {
    mockApi();
    renderAt(<AppRoutes />, "/app/cases/CASE-001/evidence/EVD-001");
    for (const name of SECTIONS) {
      expect(await screen.findByRole("heading", { level: 3, name: new RegExp(`^${name}`, "i") })).toBeInTheDocument();
    }
    expect(screen.getByText("Fixture device")).toBeInTheDocument();
  });

  it("defines each status using the backend's definitions", async () => {
    mockApi();
    renderAt(<AppRoutes />, "/app/cases/CASE-001/evidence/EVD-001");
    for (const definition of Object.values(profile.status_definitions)) {
      expect(await screen.findByText((_, el) => el?.tagName === "SPAN" && el.textContent === ` — ${definition}`)).toBeInTheDocument();
    }
  });

  it("never presents an overall score", async () => {
    mockApi();
    const { container } = renderAt(<AppRoutes />, "/app/cases/CASE-001/evidence/EVD-001");
    await screen.findByText("Fixture device");
    expect(screen.getByText(/does not combine them into an overall score/i)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\b\d{1,3}\s?%|\btruth score\b|\btrust score\b/i);
  });

  it("asks for a selection instead of auto-selecting evidence", async () => {
    mockApi();
    renderAt(<AppRoutes />, "/app/cases/CASE-001/evidence");
    expect(await screen.findByText("Select an evidence item to view its Evidence Profile.")).toBeInTheDocument();
    const list = screen.getByRole("navigation", { name: /evidence/i });
    expect(within(list).getAllByRole("link")).toHaveLength(2);
  });

  it("states when an evidence item has no recorded profile", async () => {
    mockApi({ "/api/v1/cases/CASE-001/evidence/EVD-002/profile": 404 });
    renderAt(<AppRoutes />, "/app/cases/CASE-001/evidence/EVD-002");
    expect((await screen.findAllByText(/no evidence profile|profile unavailable|not been recorded/i)).length).toBeGreaterThan(0);
  });
});
