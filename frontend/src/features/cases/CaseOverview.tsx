import { Link } from "react-router";
import type { AuditEvent, ListResponse } from "../../api/types";
import { useResource } from "../../api/useResource";
import {
  ApiErrorView,
  CASE_STATE,
  DemoNotice,
  FactList,
  Icon,
  Panel,
  PanelHeader,
  RefId,
  StateView,
  StatusBadge,
  Tag,
  Timestamp,
  type IconName,
} from "../../design-system";
import { casePath } from "../../app-shell/navigation";
import { useSession } from "../auth/AuthContext";
import { useCase } from "./CaseLayout";

interface Tile {
  key: string;
  label: string;
  icon: IconName;
  segment: string;
  value: number | null;
  unit: string;
  detail: string;
  reserved?: boolean;
}

export function CaseOverview() {
  const c = useCase();
  const auth = useSession();
  const n = c.counts;
  const caseCapabilities = auth.state.status === "ready"
    ? auth.state.session.case_capabilities[c.id] ?? auth.state.session.case_capabilities["*"] ?? []
    : [];
  const tiles: Tile[] = [
    { key: "evidence", label: "Evidence", icon: "evidence", segment: "evidence", value: n.evidence, unit: "registered", detail: "Metadata records · content not ingested" },
    { key: "examination", label: "Examination", icon: "examination", segment: "examination", value: n.analysis_runs, unit: "analysis runs", detail: c.demonstration ? "Demonstration cases never execute Methods" : "Methods publish Observations from preserved bytes" },
    { key: "findings", label: "Findings", icon: "finding", segment: "findings", value: n.findings, unit: "recorded", detail: `${n.findings_awaiting_review} unreviewed` },
    { key: "claims", label: "Claims", icon: "claim", segment: "claims", value: n.claims, unit: "recorded", detail: `${n.assessments} assessment${n.assessments === 1 ? "" : "s"} recorded` },
    { key: "timeline", label: "Timeline", icon: "timeline", segment: "timeline", value: null, unit: "", detail: "Timeline reconstruction is reserved", reserved: true },
    { key: "graph", label: "Graph", icon: "graph", segment: "graph", value: 1 + n.evidence + n.observations + n.findings + n.claims, unit: "nodes", detail: `${n.relationships} asserted relationships` },
    { key: "review", label: "Review", icon: "review", segment: "review", value: n.findings_awaiting_review, unit: "awaiting review", detail: "Review recording is not implemented in V2" },
    { key: "report", label: "Report", icon: "report", segment: "reports", value: null, unit: "", detail: "Report generation is reserved", reserved: true },
  ];

  return (
    <div className="space-y-6">
      <section aria-labelledby="case-title" className="surface lab-grid relative overflow-hidden px-5 py-6 md:px-7 md:py-7">
        <div aria-hidden="true" className="pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full bg-signal/[0.06] blur-3xl" />
        <div className="eyebrow mb-3">Case workspace</div>
        <div className="flex flex-wrap items-center gap-2">
          <RefId id={c.id} className="text-[0.8125rem] text-fg" />
          <StatusBadge semantics={CASE_STATE[c.state]} />
          {c.demonstration && <DemoNotice compact />}
        </div>
        <h1 id="case-title" className="mt-3 max-w-4xl text-2xl font-semibold leading-tight tracking-[-0.02em] md:text-[1.75rem]">
          {c.title}
        </h1>
        {c.summary && <p className="mt-3 max-w-3xl text-[0.8125rem] leading-relaxed text-fg-muted">{c.summary}</p>}
        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-xs text-fg-subtle">
          <span>
            Created by <span className="mono-id text-fg-muted">{c.created_by}</span>
          </span>
          <span>
            Created <Timestamp iso={c.created_at} />
          </span>
        </div>
      </section>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-6">
          <Panel labelledBy="objectives-title">
            <PanelHeader id="objectives-title" eyebrow="Objective" title="What this case must establish" />
            {c.objectives.length === 0 ? (
              <StateView state="empty" title="No objective recorded." compact />
            ) : (
              <ol className="divide-y divide-line">
                {c.objectives.map((o) => (
                  <li key={o.id} className="flex gap-4 px-4 py-3.5">
                    <RefId id={o.id} className="h-fit shrink-0" />
                    <p className="flex-1 text-[0.8125rem] leading-relaxed text-fg">{o.statement}</p>
                    <Tag tone={o.state === "active" ? "signal" : "muted"} className="h-fit shrink-0">
                      {o.state}
                    </Tag>
                  </li>
                ))}
              </ol>
            )}
          </Panel>

          <section aria-labelledby="case-state-title">
            <div className="mb-3 flex items-baseline justify-between">
              <h2 id="case-state-title" className="eyebrow">
                Case state
              </h2>
              <span className="text-2xs text-fg-subtle">Counts are read from recorded data</span>
            </div>
            <ul className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-4">
              {tiles.map((tile) => (
                <li key={tile.key}>
                  <Link
                    to={casePath(c.id, tile.segment)}
                    className="surface group flex h-full flex-col gap-3 px-4 py-4 transition-state hover:border-line-strong hover:bg-ink-750"
                  >
                    <div className="flex items-center gap-2 text-fg-muted">
                      <Icon name={tile.icon} className="text-fg-subtle transition-micro group-hover:text-signal" />
                      <span className="text-[0.8125rem] font-medium text-fg">{tile.label}</span>
                      <Icon name="arrowRight" size={14} className="ml-auto opacity-0 transition-micro group-hover:opacity-100" />
                    </div>
                    {tile.reserved ? (
                      <div className="font-mono text-2xs uppercase tracking-[0.12em] text-fg-faint">Reserved</div>
                    ) : (
                      <div className="flex items-baseline gap-2">
                        <span className="font-mono text-2xl font-medium tabular-nums text-fg">{tile.value}</span>
                        <span className="text-xs text-fg-subtle">{tile.unit}</span>
                      </div>
                    )}
                    <p className="mt-auto text-xs leading-snug text-fg-subtle">{tile.detail}</p>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        </div>

        <aside className="space-y-6" aria-label="Case context">
          {caseCapabilities.includes("case_audit:read") && <RecentAudit caseId={c.id} />}
          <Panel labelledBy="origin-title">
            <PanelHeader id="origin-title" eyebrow="Data origin" title={c.demonstration ? "Demonstration dataset" : "Case records"} />
            <div className="space-y-4 px-4 py-4">
              {c.demonstration && c.notice && <DemoNotice notice={c.notice} />}
              <FactList
                items={[
                  { term: "Observations", detail: <span className="mono-id">{n.observations}</span> },
                  { term: "Analysis runs", detail: <span className="mono-id">{n.analysis_runs}</span> },
                  {
                    term: "Origin",
                    detail: n.analysis_runs === 0 && n.observations > 0 ? "All observations recorded manually" : "—",
                  },
                ]}
              />
            </div>
          </Panel>
        </aside>
      </div>
    </div>
  );
}

function RecentAudit({ caseId }: { caseId: string }) {
  const events = useResource<ListResponse<AuditEvent>>(`/api/v1/cases/${caseId}/audit-events?limit=6`);
  return (
    <Panel labelledBy="recent-audit-title">
      <PanelHeader
        id="recent-audit-title"
        eyebrow="Audit"
        title="Recent activity"
        actions={
          <Link to={casePath(caseId, "audit")} className="text-xs text-fg-subtle transition-micro hover:text-fg">
            All events
          </Link>
        }
      />
      {events.status === "loading" && <StateView state="loading" title="Loading audit trail…" compact />}
      {events.status === "error" && <ApiErrorView error={events.error} subject="Audit trail" onRetry={events.reload} compact />}
      {events.status === "ready" && events.data.count === 0 && <StateView state="empty" title="No audit events recorded." compact />}
      {events.status === "ready" && events.data.count > 0 && (
        <ol className="divide-y divide-line">
          {events.data.items.map((e) => (
            <li key={e.id} className="px-4 py-2.5">
              <div className="flex items-center gap-2">
                <span className="mono-id text-fg">{e.action}</span>
                {e.entity_id && <span className="mono-id ml-auto text-fg-subtle">{e.entity_id}</span>}
              </div>
              <div className="mt-0.5 flex gap-2 text-2xs text-fg-subtle">
                <span className="mono-id">{e.actor}</span>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
