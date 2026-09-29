import { ButtonLink, PageHeader, StateView } from "../../design-system";
import { caseEyebrow, useCase } from "../cases/CaseLayout";
import { ReservedCapability } from "./ReservedCapability";

export function TimelinePage() {
  const c = useCase();
  return (
    <>
      <PageHeader
        eyebrow={caseEyebrow(c.id, "Timeline")}
        title="Timeline"
        description="A chronology of events reconstructed from temporal observations across evidence, with each placement traceable to its source."
      />
      <ReservedCapability capability="timeline" subject="Timeline reconstruction" />
    </>
  );
}

export function ReportsPage() {
  const c = useCase();
  return (
    <>
      <PageHeader
        eyebrow={caseEyebrow(c.id, "Reports")}
        title="Reports"
        description="Reports and verifiable Case Packages assembled from reviewed findings and recorded decisions."
      />
      <ReservedCapability capability="report" subject="Report generation" />
    </>
  );
}

export function NotFoundPage() {
  return (
    <div className="mx-auto max-w-xl px-6 py-24">
      <h1 className="mb-6 text-2xl font-semibold tracking-[-0.02em] text-fg">Page not found</h1>
      <StateView state="unavailable" title="This address does not exist." action={<ButtonLink to="/app/cases" size="sm">Go to cases</ButtonLink>}>
        The page may have moved, or the address is mistyped.
      </StateView>
    </div>
  );
}
