import type { SecurityAuditEvent } from "../../api/types";
import { useResource } from "../../api/useResource";
import { Button, DataTable, PageHeader, Panel, RefId, StateView, Timestamp } from "../../design-system";
import type { ListResponse } from "../../api/types";

export function SecurityAuditPage() {
  const audit = useResource<ListResponse<SecurityAuditEvent>>("/api/v1/admin/security-audit?limit=200");
  return (
    <>
      <PageHeader eyebrow="Restricted security activity" title="Security audit" description="Authentication, session, authorization, role-assignment, and user-status events from the canonical append-only Audit. Secret and credential fields are excluded." />
      {audit.status === "loading" && <StateView state="loading" title="Loading security activity…" />}
      {audit.status === "error" && <StateView state="error" title="Security audit unavailable" action={<Button onClick={audit.reload}>Retry</Button>}>{audit.error.message}</StateView>}
      {audit.status === "ready" && audit.data.count === 0 && <StateView state="empty" title="No security events recorded." />}
      {audit.status === "ready" && audit.data.count > 0 && <Panel><DataTable caption="Security audit events" rows={audit.data.items} rowKey={(event) => event.id} columns={[
        { key: "id", header: "Event", render: (event) => <RefId id={event.id} /> },
        { key: "time", header: "Occurred", render: (event) => <Timestamp iso={event.occurred_at} /> },
        { key: "action", header: "Action", render: (event) => <span className="mono-id text-fg">{event.action}</span> },
        { key: "actor", header: "Actor", render: (event) => <span className="mono-id text-fg-muted">{event.actor}</span> },
        { key: "entity", header: "Entity", render: (event) => <span className="mono-id text-fg-subtle">{event.entity_id ?? event.entity_type}</span> },
        { key: "details", header: "Metadata", render: (event) => <span className="mono-id text-fg-subtle">{Object.entries(event.details).map(([key, value]) => `${key}=${String(value)}`).join(" · ") || "—"}</span> },
      ]} /></Panel>}
    </>
  );
}
