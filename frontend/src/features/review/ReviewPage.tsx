import type { Claim, Finding, ListResponse } from "../../api/types";
import { useResource } from "../../api/useResource";
import { ASSESSMENT_STATE, ApiErrorView, DataTable, PageHeader, Panel, PanelHeader, REVIEW_STATUS, RefId, StateView, StatusBadge } from "../../design-system";
import { casePath } from "../../app-shell/navigation";
import { caseEyebrow, useCase } from "../cases/CaseLayout";
import { ReservedCapability } from "../reserved/ReservedCapability";

/**
 * Review queue. Shows what is awaiting human judgment, derived from recorded review
 * statuses and assessments. Recording a review or decision is reserved for V2.
 */
export function ReviewPage() {
  const c = useCase();
  const findings = useResource<ListResponse<Finding>>(`/api/v1/cases/${c.id}/findings`);
  const claims = useResource<ListResponse<Claim>>(`/api/v1/cases/${c.id}/claims`);

  const awaiting = findings.status === "ready" ? findings.data.items.filter((f) => f.review_status === "unreviewed") : [];
  const assessments = claims.status === "ready" ? claims.data.items.flatMap((cl) => cl.assessments) : [];

  return (
    <>
      <PageHeader
        eyebrow={caseEyebrow(c.id, "Review")}
        title="Review"
        description="What awaits human judgment in this case. Consequential judgment is never automated."
      />
      <div className="space-y-6">
        <ReservedCapability capability="review" subject="Recording reviews and decisions" />

        <Panel labelledBy="awaiting-title">
          <PanelHeader id="awaiting-title" eyebrow="Findings" title="Awaiting review" />
          {findings.status === "loading" && <StateView state="loading" title="Loading review queue…" compact />}
          {findings.status === "error" && <ApiErrorView error={findings.error} subject="Review queue" onRetry={findings.reload} compact />}
          {findings.status === "ready" && awaiting.length === 0 && <StateView state="empty" title="No findings are awaiting review." compact />}
          {awaiting.length > 0 && (
            <DataTable
              caption="Findings awaiting review"
              rows={awaiting}
              rowKey={(f) => f.id}
              columns={[
                { key: "id", header: "Finding", render: (f) => <RefId id={f.id} to={casePath(c.id, `findings/${f.id}`)} />, className: "w-28" },
                { key: "title", header: "Title", render: (f) => <span className="text-fg">{f.title}</span> },
                { key: "status", header: "Status", render: (f) => <StatusBadge semantics={REVIEW_STATUS[f.review_status]} /> },
                {
                  key: "alternatives",
                  header: "Open alternatives",
                  render: (f) => <span className="mono-id">{f.alternative_explanations.filter((a) => a.status === "open").length}</span>,
                  secondary: true,
                },
              ]}
            />
          )}
        </Panel>

        <Panel labelledBy="assessments-title">
          <PanelHeader id="assessments-title" eyebrow="Claims" title="Assessments" />
          {claims.status === "loading" && <StateView state="loading" title="Loading assessments…" compact />}
          {claims.status === "error" && <ApiErrorView error={claims.error} subject="Assessments" onRetry={claims.reload} compact />}
          {claims.status === "ready" && assessments.length === 0 && <StateView state="empty" title="No assessments recorded." compact />}
          {assessments.length > 0 && (
            <DataTable
              caption="Assessments of claims"
              rows={assessments}
              rowKey={(a) => a.id}
              columns={[
                { key: "id", header: "Assessment", render: (a) => <RefId id={a.id} />, className: "w-28" },
                { key: "claim", header: "Claim", render: (a) => <RefId id={a.claim_id} to={`${casePath(c.id, "claims")}#${a.claim_id}`} /> },
                { key: "state", header: "State", render: (a) => <StatusBadge semantics={ASSESSMENT_STATE[a.state]} /> },
                { key: "statement", header: "Statement", render: (a) => <span className="text-fg-muted">{a.statement}</span> },
              ]}
            />
          )}
        </Panel>
      </div>
    </>
  );
}
