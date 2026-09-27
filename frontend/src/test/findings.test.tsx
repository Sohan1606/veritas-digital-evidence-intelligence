import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AppRoutes } from "../App";
import { mockApi, renderAt } from "./fixtures";

const SECTIONS = ["Observation", "Method", "Evidence basis", "Related claim", "Alternative explanations", "Limitations", "Review status"];

async function openFinding() {
  mockApi();
  renderAt(<AppRoutes />, "/app/cases/CASE-001/findings/FND-001");
  return screen.findByRole("heading", { level: 2, name: "Fixture finding title" });
}

describe("Finding", () => {
  it("shows every canonical section", async () => {
    await openFinding();
    for (const name of SECTIONS) {
      expect(screen.getByRole("heading", { level: 3, name: new RegExp(`^${name}`) })).toBeInTheDocument();
    }
  });

  it("traces the finding back through observations to evidence", async () => {
    const user = userEvent.setup();
    await openFinding();
    const trace = screen.getByRole("button", { name: /^trace/i });
    await user.click(trace);
    expect(trace).toHaveAttribute("aria-pressed", "true");
    const panel = screen.getByRole("region", { name: "Trace" });
    expect(within(panel).getAllByText("OBS-001").length).toBeGreaterThan(0);
    expect(within(panel).getAllByText("EVD-002").length).toBeGreaterThan(0);
  });

  it("explains deterministically and says no text is generated", async () => {
    const user = userEvent.setup();
    await openFinding();
    await user.click(screen.getByRole("button", { name: /^explain/i }));
    const panel = screen.getByRole("region", { name: "Explain" });
    expect(within(panel).getByText(/no generated text/i)).toBeInTheDocument();
  });

  it("separates open and excluded alternatives under Why not", async () => {
    const user = userEvent.setup();
    await openFinding();
    await user.click(screen.getByRole("button", { name: /^why not/i }));
    const panel = screen.getByRole("region", { name: "Why not" });
    expect(within(panel).getByText(/Clock drift/)).toBeInTheDocument();
    expect(within(panel).getByText(/Both records use UTC/)).toBeInTheDocument();
  });

  it("does not pretend a challenge can be recorded", async () => {
    const user = userEvent.setup();
    await openFinding();
    await user.click(screen.getByRole("button", { name: /^challenge/i }));
    const panel = screen.getByRole("region", { name: "Challenge" });
    expect(within(panel).getByText("No challenge can be recorded in V1.")).toBeInTheDocument();
  });
});
