import { createContext, useContext } from "react";
import { Outlet, useParams } from "react-router";
import type { CaseDetail } from "../../api/types";
import { useResource } from "../../api/useResource";
import { ApiErrorView, ButtonLink, StateView } from "../../design-system";

const CaseContext = createContext<CaseDetail | null>(null);

/** The case every section below /app/cases/:caseId works in. */
export function useCase(): CaseDetail {
  const value = useContext(CaseContext);
  if (!value) throw new Error("useCase must be used inside CaseLayout");
  return value;
}

export function CaseLayout() {
  const { caseId = "" } = useParams();
  const valid = /^CASE-\d{3,9}$/.test(caseId);
  const resource = useResource<CaseDetail>(valid ? `/api/v1/cases/${caseId}` : null);

  if (!valid) {
    return (
      <StateView state="unavailable" title="Not a case identifier." action={<ButtonLink to="/app/cases" size="sm">View cases</ButtonLink>}>
        Case identifiers have the form CASE-001.
      </StateView>
    );
  }
  if (resource.status === "loading") return <StateView state="loading" title="Loading case state…" />;
  if (resource.status === "error") return <ApiErrorView error={resource.error} subject={`Case ${caseId}`} onRetry={resource.reload} />;
  return (
    <CaseContext.Provider value={resource.data}>
      <Outlet />
    </CaseContext.Provider>
  );
}

/** Standard eyebrow for case section pages: "CASE-001 · Evidence". */
export function caseEyebrow(caseId: string, section: string) {
  return (
    <>
      <span className="text-signal">{caseId}</span> · {section}
    </>
  );
}
