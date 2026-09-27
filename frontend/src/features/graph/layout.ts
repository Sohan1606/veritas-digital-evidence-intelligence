/**
 * Deterministic layered layout for the Case Knowledge Graph.
 *
 * Columns follow the evidential flow (Case → Evidence → Observation → Finding → Claim).
 * Rows are ordered by barycentric sweeps to reduce crossings; ties break on identifier,
 * so the same graph always renders identically. No randomness, no physics.
 */
import type { CaseGraph, GraphNode, NodeType } from "../../api/types";

export const COLUMN_ORDER: NodeType[] = ["case", "evidence", "observation", "finding", "claim"];

export interface PositionedNode extends GraphNode {
  x: number;
  y: number;
  column: number;
}

export interface GraphLayout {
  nodes: PositionedNode[];
  byId: Map<string, PositionedNode>;
  width: number;
  height: number;
}

export const NODE_WIDTH = 164;
export const NODE_HEIGHT = 44;
const COLUMN_GAP = 52;
const ROW_GAP = 18;
const PADDING = 20;

export function layoutGraph(graph: CaseGraph): GraphLayout {
  const columns: GraphNode[][] = COLUMN_ORDER.map((type) =>
    graph.nodes.filter((n) => n.type === type).sort((a, b) => a.id.localeCompare(b.id, "en", { numeric: true })),
  );

  const neighbours = new Map<string, Set<string>>();
  for (const r of graph.relationships) {
    if (!neighbours.has(r.source)) neighbours.set(r.source, new Set());
    if (!neighbours.has(r.target)) neighbours.set(r.target, new Set());
    neighbours.get(r.source)!.add(r.target);
    neighbours.get(r.target)!.add(r.source);
  }

  const rank = new Map<string, number>();
  const refreshRanks = () => columns.forEach((col) => col.forEach((node, index) => rank.set(node.id, index)));
  refreshRanks();

  const sweep = (order: number[]) => {
    for (const c of order) {
      const column = columns[c]!;
      const score = new Map<string, number>();
      for (const node of column) {
        const linked = [...(neighbours.get(node.id) ?? [])].filter((id) => rank.has(id) && !column.some((n) => n.id === id));
        score.set(node.id, linked.length ? linked.reduce((sum, id) => sum + rank.get(id)!, 0) / linked.length : rank.get(node.id)!);
      }
      column.sort((a, b) => score.get(a.id)! - score.get(b.id)! || a.id.localeCompare(b.id, "en", { numeric: true }));
      refreshRanks();
    }
  };
  sweep([2, 3, 4]);
  sweep([3, 2, 1]);
  sweep([2, 3, 4]);

  const tallest = Math.max(1, ...columns.map((c) => c.length));
  const innerHeight = tallest * NODE_HEIGHT + (tallest - 1) * ROW_GAP;
  const nodes: PositionedNode[] = [];
  const usedColumns = columns.map((c, i) => (c.length ? i : -1)).filter((i) => i >= 0);

  usedColumns.forEach((columnIndex, visualIndex) => {
    const column = columns[columnIndex]!;
    const columnHeight = column.length * NODE_HEIGHT + (column.length - 1) * ROW_GAP;
    const offset = PADDING + (innerHeight - columnHeight) / 2;
    column.forEach((node, row) => {
      nodes.push({
        ...node,
        column: columnIndex,
        x: PADDING + visualIndex * (NODE_WIDTH + COLUMN_GAP),
        y: offset + row * (NODE_HEIGHT + ROW_GAP),
      });
    });
  });

  return {
    nodes,
    byId: new Map(nodes.map((n) => [n.id, n])),
    width: PADDING * 2 + usedColumns.length * NODE_WIDTH + Math.max(0, usedColumns.length - 1) * COLUMN_GAP + 40,
    height: PADDING * 2 + innerHeight,
  };
}

/** SVG path between two positioned nodes, connecting facing sides. */
export function edgePath(source: PositionedNode, target: PositionedNode): string {
  const sy = source.y + NODE_HEIGHT / 2;
  const ty = target.y + NODE_HEIGHT / 2;
  if (source.x === target.x) {
    const x = source.x + NODE_WIDTH;
    const bulge = 36 + Math.abs(ty - sy) * 0.25;
    return `M${x} ${sy} C${x + bulge} ${sy}, ${x + bulge} ${ty}, ${x + 4} ${ty}`;
  }
  const forward = source.x < target.x;
  const sx = forward ? source.x + NODE_WIDTH : source.x;
  const tx = forward ? target.x - 4 : target.x + NODE_WIDTH + 4;
  const dx = Math.max(40, Math.abs(tx - sx) * 0.45) * (forward ? 1 : -1);
  return `M${sx} ${sy} C${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`;
}
