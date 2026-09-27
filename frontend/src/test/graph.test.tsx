import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AppRoutes } from "../App";
import { NODE_HEIGHT, NODE_WIDTH, layoutGraph } from "../features/graph/layout";
import { graph, mockApi, renderAt } from "./fixtures";

describe("Case Knowledge Graph layout", () => {
  it("is deterministic and independent of input order", () => {
    const a = layoutGraph(graph);
    const b = layoutGraph({ ...graph, nodes: [...graph.nodes].reverse(), relationships: [...graph.relationships].reverse() });
    const pos = (l: ReturnType<typeof layoutGraph>) => l.nodes.map((n) => `${n.id}@${n.x},${n.y}`).sort();
    expect(pos(a)).toEqual(pos(b));
  });

  it("places node types in flow order without overlaps", () => {
    const layout = layoutGraph(graph);
    const x = (id: string) => layout.byId.get(id)!.x;
    expect(x("CASE-001")).toBeLessThan(x("EVD-001"));
    expect(x("EVD-001")).toBeLessThan(x("OBS-001"));
    expect(x("OBS-001")).toBeLessThan(x("FND-001"));
    expect(x("FND-001")).toBeLessThan(x("CLM-001"));
    for (const a of layout.nodes) {
      for (const b of layout.nodes) {
        if (a.id >= b.id) continue;
        const overlap = Math.abs(a.x - b.x) < NODE_WIDTH && Math.abs(a.y - b.y) < NODE_HEIGHT;
        expect(overlap, `${a.id} overlaps ${b.id}`).toBe(false);
      }
    }
  });
});

describe("Case Knowledge Graph view", () => {
  it("drives the detail panel from node selection", async () => {
    mockApi();
    const user = userEvent.setup();
    renderAt(<AppRoutes />, "/app/cases/CASE-001/graph");
    const node = await screen.findByRole("button", { name: /^Finding FND-001/ });
    await user.click(node);
    expect(node).toHaveAttribute("aria-pressed", "true");
    const detail = screen.getByRole("complementary", { name: "Fixture finding title" });
    expect(within(detail).getByText("CLM-001")).toBeInTheDocument();
    expect(within(detail).getByRole("link", { name: /open record/i })).toHaveAttribute("href", "/app/cases/CASE-001/findings/FND-001");
  });

  it("selects the node named in ?focus=", async () => {
    mockApi();
    renderAt(<AppRoutes />, "/app/cases/CASE-001/graph?focus=CLM-001");
    expect(await screen.findByRole("button", { name: /^Claim CLM-001/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("filters relationships by type in the textual presentation", async () => {
    mockApi();
    const user = userEvent.setup();
    renderAt(<AppRoutes />, "/app/cases/CASE-001/graph");
    await user.click(await screen.findByRole("tab", { name: "table" }));
    const table = screen.getByRole("table", { name: /relationships/i });
    const before = within(table).getAllByRole("row").length;
    await user.click(screen.getByRole("button", { name: /contradicts/i, pressed: true }));
    expect(within(table).getAllByRole("row").length).toBe(before - 1);
  });
});
