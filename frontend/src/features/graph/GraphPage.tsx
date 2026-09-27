import { useSearchParams } from "react-router";
import type { CaseGraph } from "../../api/types";
import { useResource } from "../../api/useResource";
import { ApiErrorView, PageHeader, StateView } from "../../design-system";
import { caseEyebrow, useCase } from "../cases/CaseLayout";
import { CaseGraphView } from "./CaseGraphView";

export function GraphPage() {
  const c = useCase();
  const [params] = useSearchParams();
  const graph = useResource<CaseGraph>(`/api/v1/cases/${c.id}/graph`);

  return (
    <>
      <PageHeader
        eyebrow={caseEyebrow(c.id, "Graph")}
        title="Case Knowledge Graph"
        description="One graph per case, projected from recorded records. Structural relationships come from ownership; asserted relationships are typed and recorded with a rationale where one exists."
      />
      {graph.status === "loading" && <StateView state="loading" title="Loading Case Knowledge Graph…" />}
      {graph.status === "error" && <ApiErrorView error={graph.error} subject="Case Knowledge Graph" onRetry={graph.reload} />}
      {graph.status === "ready" && graph.data.relationships.length === 0 && (
        <StateView state="empty" title={`The graph for ${c.id} contains only the case node.`}>
          Relationships appear once evidence, observations, findings or claims are recorded.
        </StateView>
      )}
      {graph.status === "ready" && graph.data.relationships.length > 0 && (
        <CaseGraphView graph={graph.data} initialFocus={params.get("focus")} />
      )}
    </>
  );
}
