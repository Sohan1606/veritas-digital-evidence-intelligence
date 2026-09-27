import { Link } from "react-router";
import type { CaseSummary, ListResponse } from "../../api/types";
import { useResource } from "../../api/useResource";
import { CASE_STATE, DataTable, DemoNotice, PageHeader, Panel, RefId, StateView, StatusBadge, Timestamp, ApiErrorView } from "../../design-system";
import { casePath } from "../../app-shell/navigation";

export function CasesPage() {
  const cases = useResource<ListResponse<CaseSummary>>("/api/v1/cases");

  return (
    <>
      <PageHeader
        eyebrow="Workspace · Cases"
        title="Cases"
        description="Cases you can access. Case creation and intake are not part of V1; the list contains only records that already exist."
      />
      {cases.status === "loading" && <StateView state="loading" title="Loading cases…" />}
      {cases.status === "error" && <ApiErrorView error={cases.error} subject="Case list" onRetry={cases.reload} />}
      {cases.status === "ready" && (
        <div className="space-y-4">
          {cases.data.items.some((c) => c.demonstration) && (
            <DemoNotice notice={cases.data.items.find((c) => c.notice)?.notice ?? undefined} />
          )}
          {cases.data.count === 0 ? (
            <StateView state="empty" title="No cases available.">
              No readable cases exist. In development, load the demonstration dataset with the seed command.
            </StateView>
          ) : (
            <Panel>
              <DataTable
                caption="Accessible cases"
                rows={cases.data.items}
                rowKey={(c) => c.id}
                columns={[
                  { key: "id", header: "Case", render: (c) => <RefId id={c.id} to={casePath(c.id)} />, className: "w-28" },
                  {
                    key: "title",
                    header: "Title",
                    render: (c) => (
                      <Link to={casePath(c.id)} className="font-medium text-fg transition-micro hover:text-signal-strong">
                        {c.title}
                      </Link>
                    ),
                  },
                  { key: "state", header: "State", render: (c) => <StatusBadge semantics={CASE_STATE[c.state]} /> },
                  { key: "evidence", header: "Evidence", render: (c) => <span className="mono-id">{c.counts.evidence}</span> },
                  { key: "findings", header: "Findings", render: (c) => <span className="mono-id">{c.counts.findings}</span> },
                  {
                    key: "review",
                    header: "Awaiting review",
                    render: (c) => <span className="mono-id">{c.counts.findings_awaiting_review}</span>,
                    secondary: true,
                  },
                  { key: "updated", header: "Updated", render: (c) => <Timestamp iso={c.updated_at} />, secondary: true },
                ]}
              />
            </Panel>
          )}
        </div>
      )}
    </>
  );
}
