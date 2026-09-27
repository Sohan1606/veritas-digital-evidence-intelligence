import { Link, useParams } from "react-router";
import type { Finding, ListResponse } from "../../api/types";
import { useResource } from "../../api/useResource";
import { ApiErrorView, PageHeader, REVIEW_STATUS, StateGlyph, StateView } from "../../design-system";
import { casePath } from "../../app-shell/navigation";
import { caseEyebrow, useCase } from "../cases/CaseLayout";
import { FindingDetail } from "./FindingDetail";

export function FindingsPage() {
  const c = useCase();
  const { findingId } = useParams();
  const list = useResource<ListResponse<Finding>>(`/api/v1/cases/${c.id}/findings`);
  const selected = list.status === "ready" ? list.data.items.find((f) => f.id === findingId) : undefined;

  return (
    <>
      <PageHeader
        eyebrow={caseEyebrow(c.id, "Findings")}
        title="Findings"
        description="Each finding states its observations, method, evidence basis, related claims, alternative explanations and limitations. Findings are unreliable until reviewed by a person."
      />
      {list.status === "loading" && <StateView state="loading" title="Loading findings…" />}
      {list.status === "error" && <ApiErrorView error={list.error} subject="Findings" onRetry={list.reload} />}
      {list.status === "ready" && list.data.count === 0 && (
        <StateView state="empty" title={`No findings recorded in ${c.id}.`}>
          Findings are recorded from observations. No automated examination exists in V1 to produce them.
        </StateView>
      )}
      {list.status === "ready" && list.data.count > 0 && (
        <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)] xl:grid-cols-[20rem_minmax(0,1fr)]">
          <nav aria-label="Findings">
            <ul className="space-y-1.5">
              {list.data.items.map((f) => {
                const active = f.id === findingId;
                const review = REVIEW_STATUS[f.review_status];
                return (
                  <li key={f.id}>
                    <Link
                      to={casePath(c.id, `findings/${f.id}`)}
                      aria-current={active ? "page" : undefined}
                      className={`block rounded-md border px-3 py-2.5 transition-micro ${
                        active
                          ? "border-line-strong bg-ink-750 shadow-[inset_2px_0_0_var(--color-signal)]"
                          : "border-line bg-ink-850 hover:border-line-strong hover:bg-ink-800"
                      }`}
                    >
                      <span className="flex items-center gap-2 text-2xs text-fg-subtle">
                        <span className="mono-id text-2xs text-fg-muted">{f.id}</span>
                        <span className="ml-auto flex items-center gap-1">
                          <StateGlyph glyph={review.glyph} tone={review.tone} size={8} />
                          {review.label}
                        </span>
                      </span>
                      <span className="mt-1 block text-[0.8125rem] leading-snug text-fg">{f.title}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
          <div className="min-w-0">
            {!findingId && (
              <StateView state="empty" title="Select a finding to inspect it.">
                Use Trace, Explain and Why not to examine how a finding was reached and what it does not exclude.
              </StateView>
            )}
            {findingId && !selected && <StateView state="unavailable" title={`${findingId} is not recorded in ${c.id}.`} />}
            {selected && <FindingDetail finding={selected} caseId={c.id} demonstration={c.demonstration} />}
          </div>
        </div>
      )}
    </>
  );
}
