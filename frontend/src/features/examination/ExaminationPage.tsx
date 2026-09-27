import type { AnalysisRun, ListResponse } from "../../api/types";
import { useResource } from "../../api/useResource";
import { ApiErrorView, DataTable, PageHeader, Panel, PanelHeader, RefId, StateView, Tag, Timestamp } from "../../design-system";
import { caseEyebrow, useCase } from "../cases/CaseLayout";
import { ReservedCapability } from "../reserved/ReservedCapability";

export function ExaminationPage() {
  const c = useCase();
  const runs = useResource<ListResponse<AnalysisRun>>(`/api/v1/cases/${c.id}/analysis-runs`);
  const manualOnly = c.counts.analysis_runs === 0 && c.counts.observations > 0;

  return (
    <>
      <PageHeader
        eyebrow={caseEyebrow(c.id, "Examination")}
        title="Examination"
        description="Analysis Runs record each execution of a Method against Evidence, and the Observations it produced."
      />
      <div className="space-y-6">
        <ReservedCapability capability="examination" subject="Automated examination" />
        <Panel labelledBy="runs-title">
          <PanelHeader id="runs-title" eyebrow="Analysis Runs" title={`Recorded runs in ${c.id}`} />
          {runs.status === "loading" && <StateView state="loading" title="Loading analysis runs…" compact />}
          {runs.status === "error" && <ApiErrorView error={runs.error} subject="Analysis runs" onRetry={runs.reload} compact />}
          {runs.status === "ready" && runs.data.count === 0 && (
            <StateView state="empty" title={`No analysis runs recorded for ${c.id}.`} compact>
              {manualOnly
                ? `All ${c.counts.observations} observations in this case were recorded manually; none was produced by an Analysis Run.`
                : "No observations have been recorded."}
            </StateView>
          )}
          {runs.status === "ready" && runs.data.count > 0 && (
            <DataTable
              caption="Analysis runs"
              rows={runs.data.items}
              rowKey={(r) => r.id}
              columns={[
                { key: "id", header: "Run", render: (r) => <RefId id={r.id} /> },
                { key: "evidence", header: "Evidence", render: (r) => <RefId id={r.evidence_id} /> },
                { key: "method", header: "Method", render: (r) => <span className="mono-id">{`${r.method_key}@${r.method_version}`}</span> },
                { key: "state", header: "State", render: (r) => <Tag>{r.state}</Tag> },
                { key: "completed", header: "Completed", render: (r) => (r.completed_at ? <Timestamp iso={r.completed_at} /> : "—") },
              ]}
            />
          )}
        </Panel>
      </div>
    </>
  );
}
