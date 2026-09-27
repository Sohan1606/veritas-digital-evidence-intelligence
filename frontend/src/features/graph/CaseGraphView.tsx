import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import type { CaseGraph, GraphNode, GraphRelationship, RelationshipType } from "../../api/types";
import { DataTable, Icon, NODE_TYPE, Panel, PanelHeader, RELATIONSHIP, RefId, StateView, TONE_VAR, Tag } from "../../design-system";
import { casePath } from "../../app-shell/navigation";
import { NODE_HEIGHT, NODE_WIDTH, edgePath, layoutGraph, type PositionedNode } from "./layout";

const RELATIONSHIP_TYPES: RelationshipType[] = ["supports", "contradicts", "derived-from", "references", "contains"];
const DASH = { solid: undefined, dashed: "6 4", dotted: "1.5 3.5", faint: undefined } as const;

function recordPath(caseId: string, node: GraphNode): string | null {
  switch (node.type) {
    case "case":
      return casePath(caseId);
    case "evidence":
      return casePath(caseId, `evidence/${node.id}`);
    case "finding":
      return casePath(caseId, `findings/${node.id}`);
    case "claim":
      return `${casePath(caseId, "claims")}#${node.id}`;
    default:
      return null; // observations are shown through the findings that derive from them
  }
}

/**
 * The Case Knowledge Graph view. One graph, typed relationships, two presentations:
 * a visual layered diagram and an equivalent relationship table (the accessible and
 * mobile fallback). Selecting a node drives the detail panel in both.
 */
export function CaseGraphView({ graph, initialFocus }: { graph: CaseGraph; initialFocus?: string | null }) {
  const [selectedId, setSelectedId] = useState<string | null>(
    initialFocus && graph.nodes.some((n) => n.id === initialFocus) ? initialFocus : null,
  );
  const [hidden, setHidden] = useState<Set<RelationshipType>>(new Set());
  const [showStructural, setShowStructural] = useState(true);
  const [view, setView] = useState<"visual" | "table">(() =>
    typeof window !== "undefined" && window.matchMedia?.("(max-width: 767px)").matches ? "table" : "visual",
  );

  const layout = useMemo(() => layoutGraph(graph), [graph]);
  const visible = useMemo(
    () => graph.relationships.filter((r) => !hidden.has(r.type) && (showStructural || r.origin === "asserted")),
    [graph.relationships, hidden, showStructural],
  );
  const selected = selectedId ? layout.byId.get(selectedId) ?? null : null;
  const connected = useMemo(() => {
    if (!selectedId) return null;
    const ids = new Set<string>([selectedId]);
    visible.forEach((r) => {
      if (r.source === selectedId) ids.add(r.target);
      if (r.target === selectedId) ids.add(r.source);
    });
    return ids;
  }, [selectedId, visible]);

  const toggle = (type: RelationshipType) =>
    setHidden((current) => {
      const next = new Set(current);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });

  return (
    <div className="space-y-4">
      <Panel className="min-w-0 overflow-hidden" labelledBy="graph-panel-title">
        <PanelHeader
          id="graph-panel-title"
          eyebrow="Case Knowledge Graph"
          title={`${graph.nodes.length} nodes · ${visible.length} of ${graph.relationships.length} relationships shown`}
          actions={
            <div role="tablist" aria-label="Graph presentation" className="flex rounded-sm border border-line p-0.5">
              {(["visual", "table"] as const).map((mode) => (
                <button
                  key={mode}
                  role="tab"
                  type="button"
                  aria-selected={view === mode}
                  onClick={() => setView(mode)}
                  className={`h-6 rounded-xs px-2.5 text-xs capitalize transition-micro ${view === mode ? "bg-ink-700 text-fg" : "text-fg-subtle hover:text-fg"}`}
                >
                  {mode}
                </button>
              ))}
            </div>
          }
        />
        <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-4 py-2.5" role="group" aria-label="Relationship filters">
          {RELATIONSHIP_TYPES.map((type) => {
            const meta = RELATIONSHIP[type];
            const on = !hidden.has(type);
            return (
              <button
                key={type}
                type="button"
                aria-pressed={on}
                onClick={() => toggle(type)}
                className={`inline-flex h-6 items-center gap-1.5 rounded-xs border px-2 text-xs transition-micro ${
                  on ? "border-line-strong text-fg-muted" : "border-line text-fg-faint line-through"
                }`}
              >
                <svg width="18" height="6" aria-hidden="true">
                  <line x1="0" y1="3" x2="18" y2="3" stroke={TONE_VAR[meta.tone]} strokeWidth="1.6" strokeDasharray={DASH[meta.stroke]} opacity={meta.stroke === "faint" ? 0.5 : 1} />
                </svg>
                {meta.label}
              </button>
            );
          })}
          <label className="ml-auto flex items-center gap-2 text-xs text-fg-subtle">
            <input type="checkbox" checked={showStructural} onChange={(e) => setShowStructural(e.target.checked)} className="accent-[var(--color-signal)]" />
            Structural relationships
          </label>
        </div>

        {view === "visual" ? (
          <VisualGraph layout={layout} relationships={visible} selectedId={selectedId} connected={connected} onSelect={setSelectedId} />
        ) : (
          <RelationshipTable graph={graph} relationships={visible} selectedId={selectedId} onSelect={setSelectedId} />
        )}
      </Panel>

      <NodeDetail caseId={graph.case_id} node={selected} relationships={visible} onSelect={setSelectedId} />
    </div>
  );
}

function VisualGraph({
  layout,
  relationships,
  selectedId,
  connected,
  onSelect,
}: {
  layout: ReturnType<typeof layoutGraph>;
  relationships: GraphRelationship[];
  selectedId: string | null;
  connected: Set<string> | null;
  onSelect: (id: string | null) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  // Keep the selected node visible when the diagram is wider than its container.
  useEffect(() => {
    const el = scroller.current;
    const node = selectedId ? layout.byId.get(selectedId) : undefined;
    if (!el || !node || el.scrollWidth <= el.clientWidth) return;
    if (node.x < el.scrollLeft || node.x + NODE_WIDTH > el.scrollLeft + el.clientWidth) {
      el.scrollLeft = node.x + NODE_WIDTH / 2 - el.clientWidth / 2;
    }
  }, [selectedId, layout]);
  return (
    <div ref={scroller} className="lab-grid overflow-x-auto">
      <svg
        role="group"
        aria-label="Case Knowledge Graph diagram. Use Tab to move between nodes and Enter to select."
        width={layout.width}
        height={layout.height}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        className="mx-auto block"
        onClick={(event) => event.target === event.currentTarget && onSelect(null)}
      >
        <defs>
          {RELATIONSHIP_TYPES.map((type) => (
            <marker key={type} id={`arrow-${type}`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0.5 L7.5 4 L0 7.5 Z" fill={TONE_VAR[RELATIONSHIP[type].tone]} />
            </marker>
          ))}
        </defs>
        <g aria-hidden="true">
          {relationships.map((r) => {
            const s = layout.byId.get(r.source);
            const t = layout.byId.get(r.target);
            if (!s || !t) return null;
            const meta = RELATIONSHIP[r.type];
            const active = !connected || (connected.has(r.source) && connected.has(r.target) && (r.source === selectedId || r.target === selectedId));
            return (
              <path
                key={r.id}
                d={edgePath(s, t)}
                fill="none"
                stroke={TONE_VAR[meta.tone]}
                strokeWidth={active && connected ? 1.8 : 1.2}
                strokeDasharray={DASH[meta.stroke]}
                markerEnd={`url(#arrow-${r.type})`}
                opacity={active ? (meta.stroke === "faint" ? 0.35 : 0.85) : 0.08}
                className="transition-state"
              />
            );
          })}
        </g>
        {layout.nodes.map((node) => (
          <GraphNodeShape
            key={node.id}
            node={node}
            selected={node.id === selectedId}
            dimmed={connected ? !connected.has(node.id) : false}
            onSelect={() => onSelect(node.id === selectedId ? null : node.id)}
          />
        ))}
      </svg>
    </div>
  );
}

function GraphNodeShape({ node, selected, dimmed, onSelect }: { node: PositionedNode; selected: boolean; dimmed: boolean; onSelect: () => void }) {
  const type = NODE_TYPE[node.type];
  const label = node.label.length > 22 ? `${node.label.slice(0, 21)}…` : node.label;
  const glyphY = node.y + NODE_HEIGHT / 2;
  const gx = node.x + 14;
  return (
    <g
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`${type.label} ${node.id}: ${node.label}`}
      onClick={(event) => {
        event.stopPropagation();
        onSelect();
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
      className="cursor-pointer outline-none transition-state [&:focus-visible>rect:first-child]:stroke-[var(--color-signal)]"
      opacity={dimmed ? 0.28 : 1}
    >
      <rect
        x={node.x}
        y={node.y}
        width={NODE_WIDTH}
        height={NODE_HEIGHT}
        rx={node.type === "observation" ? NODE_HEIGHT / 2 : node.type === "case" ? 8 : 3}
        fill={selected ? "var(--color-ink-700)" : "var(--color-ink-850)"}
        stroke={selected ? "var(--color-signal)" : node.type === "case" ? "var(--color-line-strong)" : "var(--color-line-strong)"}
        strokeWidth={selected ? 1.5 : 1}
      />
      {/* Type glyph: shape carries the type independently of colour. */}
      {node.type === "case" && <rect x={gx - 5} y={glyphY - 5} width={10} height={10} rx={2} fill="none" stroke="var(--color-signal)" strokeWidth={1.4} />}
      {node.type === "evidence" && <rect x={gx - 4.5} y={glyphY - 4.5} width={9} height={9} fill="var(--color-fg-muted)" />}
      {node.type === "observation" && <circle cx={gx} cy={glyphY} r={4.5} fill="none" stroke="var(--color-fg-muted)" strokeWidth={1.4} />}
      {node.type === "finding" && <path d={`M${gx} ${glyphY - 6} L${gx + 6} ${glyphY} L${gx} ${glyphY + 6} L${gx - 6} ${glyphY} Z`} fill="var(--color-warn)" />}
      {node.type === "claim" && <path d={`M${gx - 5} ${glyphY - 5} h10 v7 h-5 l-3 3 v-3 h-2 z`} fill="none" stroke="var(--color-signal)" strokeWidth={1.3} />}
      <text x={node.x + 28} y={node.y + 17} className="fill-[var(--color-fg-subtle)] font-mono" fontSize={9.5} letterSpacing="0.04em">
        {node.id}
      </text>
      <text x={node.x + 28} y={node.y + 32} className="fill-[var(--color-fg)]" fontSize={11.5}>
        {label}
      </text>
    </g>
  );
}

function RelationshipTable({
  graph,
  relationships,
  selectedId,
  onSelect,
}: {
  graph: CaseGraph;
  relationships: GraphRelationship[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const nodes = new Map(graph.nodes.map((n) => [n.id, n]));
  if (relationships.length === 0) {
    return <StateView state="empty" title="No relationships match the current filters." compact />;
  }
  return (
    <DataTable
      caption="Case Knowledge Graph relationships"
      rows={relationships}
      rowKey={(r) => r.id}
      isSelected={(r) => r.source === selectedId || r.target === selectedId}
      columns={[
        { key: "source", header: "Source", render: (r) => <NodeButton id={r.source} node={nodes.get(r.source)} onSelect={onSelect} /> },
        { key: "type", header: "Relationship", render: (r) => <Tag tone={RELATIONSHIP[r.type].tone}>{RELATIONSHIP[r.type].label}</Tag> },
        { key: "target", header: "Target", render: (r) => <NodeButton id={r.target} node={nodes.get(r.target)} onSelect={onSelect} /> },
        { key: "origin", header: "Origin", render: (r) => <span className="text-xs text-fg-subtle">{r.origin}</span>, secondary: true },
        { key: "rationale", header: "Rationale", render: (r) => <span className="text-xs text-fg-muted">{r.rationale ?? "—"}</span>, secondary: true },
      ]}
    />
  );
}

function NodeDetail({
  caseId,
  node,
  relationships,
  onSelect,
}: {
  caseId: string;
  node: GraphNode | null;
  relationships: GraphRelationship[];
  onSelect: (id: string) => void;
}) {
  if (!node) {
    return (
      <Panel as="aside" className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <p className="text-[0.8125rem] text-fg-muted">Select a node to see its relationships and open its record.</p>
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-subtle" aria-label="Node types">
          {(["case", "evidence", "observation", "finding", "claim"] as const).map((t) => (
            <li key={t} className="flex items-center gap-1.5">
              <Icon name={NODE_TYPE[t].icon} size={13} /> {NODE_TYPE[t].label}
            </li>
          ))}
        </ul>
      </Panel>
    );
  }
  const outgoing = relationships.filter((r) => r.source === node.id);
  const incoming = relationships.filter((r) => r.target === node.id);
  const path = recordPath(caseId, node);
  return (
    <Panel as="aside" className="grid md:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)]" labelledBy="graph-node-title">
      <div className="border-b border-line px-4 py-4 md:border-b-0 md:border-r">
        <div className="eyebrow mb-2 flex items-center gap-1.5">
          <Icon name={NODE_TYPE[node.type].icon} size={12} /> {NODE_TYPE[node.type].label}
        </div>
        <RefId id={node.id} className="text-fg" />
        <h2 id="graph-node-title" className="mt-2 text-[0.8125rem] font-medium leading-snug text-fg">
          {node.label}
        </h2>
        {node.state && <div className="mt-2 text-xs text-fg-subtle">State: {node.state.replace(/_/g, " ")}</div>}
        {path && (
          <Link to={path} className="mt-3 inline-flex items-center gap-1.5 text-xs text-signal transition-micro hover:text-signal-strong">
            Open record <Icon name="arrowRight" size={12} />
          </Link>
        )}
      </div>
      <div className="px-4 py-4">
        <RelationshipList title="Outgoing" items={outgoing} other={(r) => r.target} onSelect={onSelect} />
      </div>
      <div className="px-4 pb-4 md:py-4">
        <RelationshipList title="Incoming" items={incoming} other={(r) => r.source} onSelect={onSelect} />
      </div>
    </Panel>
  );
}

function NodeButton({ id, node, onSelect }: { id: string; node: GraphNode | undefined; onSelect: (id: string) => void }) {
  return (
    <button type="button" onClick={() => onSelect(id)} className="flex items-center gap-2 text-left transition-micro hover:text-signal-strong">
      <span className="mono-id text-fg">{id}</span>
      {node && <span className="text-2xs text-fg-subtle">{NODE_TYPE[node.type].label}</span>}
    </button>
  );
}

function RelationshipList({
  title,
  items,
  other,
  onSelect,
}: {
  title: string;
  items: GraphRelationship[];
  other: (r: GraphRelationship) => string;
  onSelect: (id: string) => void;
}) {
  return (
    <div>
      <h3 className="eyebrow mb-2">
        {title} <span className="text-fg-faint">{items.length}</span>
      </h3>
      {items.length === 0 ? (
        <p className="text-xs text-fg-subtle">None shown.</p>
      ) : (
        <ul className="space-y-1.5">
          {items.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-2">
              <Tag tone={RELATIONSHIP[r.type].tone}>{RELATIONSHIP[r.type].label}</Tag>
              <button type="button" onClick={() => onSelect(other(r))} className="mono-id text-fg transition-micro hover:text-signal-strong">
                {other(r)}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
