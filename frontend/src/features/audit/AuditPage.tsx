import type { AuditEvent, ListResponse } from "../../api/types";
import { useResource } from "../../api/useResource";
import { ApiErrorView, DataTable, PageHeader, Panel, RefId, StateView, Timestamp } from "../../design-system";
import { caseEyebrow, useCase } from "../cases/CaseLayout";

function Details({ details }: { details: Record<string, unknown> }) {
  const entries = Object.entries(details);
  if (entries.length === 0) return <span className="text-fg-faint">—</span>;
  return (
    <span className="flex flex-wrap gap-x-3 gap-y-0.5">
      {entries.map(([key, value]) => (
        <span key={key} className="mono-id text-2xs">
          <span className="text-fg-subtle">{key}=</span>
          <span className="text-fg-muted">{String(value)}</span>
        </span>
      ))}
    </span>
  );
}

export function AuditPage() {
  const c = useCase();
  const events = useResource<ListResponse<AuditEvent>>(`/api/v1/cases/${c.id}/audit-events?limit=200`);

  return (
    <>
      <PageHeader
        eyebrow={caseEyebrow(c.id, "Audit")}
        title="Audit"
        description="Append-only record of actions taken in this case, newest first. Audit events carry identifiers and metadata only — never evidence content. They cannot be edited or deleted."
      />
      {events.status === "loading" && <StateView state="loading" title="Loading audit trail…" />}
      {events.status === "error" && <ApiErrorView error={events.error} subject="Audit trail" onRetry={events.reload} />}
      {events.status === "ready" && events.data.count === 0 && <StateView state="empty" title={`No audit events recorded for ${c.id}.`} />}
      {events.status === "ready" && events.data.count > 0 && (
        <Panel>
          <DataTable
            caption={`Audit events for ${c.id}`}
            rows={events.data.items}
            rowKey={(e) => e.id}
            columns={[
              { key: "id", header: "Event", render: (e) => <RefId id={e.id} />, className: "w-28" },
              { key: "time", header: "Occurred", render: (e) => <Timestamp iso={e.occurred_at} className="whitespace-nowrap" /> },
              { key: "action", header: "Action", render: (e) => <span className="mono-id text-fg">{e.action}</span> },
              { key: "entity", header: "Entity", render: (e) => (e.entity_id ? <span className="mono-id text-fg-muted">{e.entity_id}</span> : "—") },
              { key: "actor", header: "Actor", render: (e) => <span className="mono-id text-fg-subtle">{e.actor}</span>, secondary: true },
              { key: "details", header: "Details", render: (e) => <Details details={e.details} />, secondary: true },
            ]}
          />
        </Panel>
      )}
    </>
  );
}
