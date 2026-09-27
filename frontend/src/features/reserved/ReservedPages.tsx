import { ButtonLink, PageHeader, StateView } from "../../design-system";
import { caseEyebrow, useCase } from "../cases/CaseLayout";
import { ReservedCapability } from "./ReservedCapability";

export function MyWorkPage() {
  return (
    <>
      <PageHeader eyebrow="Workspace · My Work" title="My Work" description="Assignments, reviews and follow-ups for the signed-in investigator." />
      <ReservedCapability capability="identity" subject="My Work">
        <ButtonLink to="/app/cases" size="sm" trailingIcon="arrowRight">
          Go to cases
        </ButtonLink>
      </ReservedCapability>
    </>
  );
}

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
      <StateView state="unavailable" title="This address does not exist." action={<ButtonLink to="/app/cases" size="sm">Go to cases</ButtonLink>}>
        The page may have moved, or the address is mistyped.
      </StateView>
    </div>
  );
}
