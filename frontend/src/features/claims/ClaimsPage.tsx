import { useEffect } from "react";
import { useLocation } from "react-router";
import type { Claim, ListResponse } from "../../api/types";
import { useResource } from "../../api/useResource";
import {
  ASSESSMENT_STATE,
  ApiErrorView,
  CLAIM_STATE,
  PageHeader,
  Panel,
  RELATIONSHIP,
  REVIEW_STATUS,
  RefId,
  StateGlyph,
  StateView,
  StatusBadge,
  Tag,
} from "../../design-system";
import { casePath } from "../../app-shell/navigation";
import { caseEyebrow, useCase } from "../cases/CaseLayout";

export function ClaimsPage() {
  const c = useCase();
  const { hash } = useLocation();
  const claims = useResource<ListResponse<Claim>>(`/api/v1/cases/${c.id}/claims`);

  useEffect(() => {
    if (claims.status === "ready" && hash) document.getElementById(hash.slice(1))?.scrollIntoView({ block: "start" });
  }, [claims.status, hash]);

  return (
    <>
      <PageHeader
        eyebrow={caseEyebrow(c.id, "Claims")}
        title="Claims"
        description="Assertions made by parties to the case. Claims are evaluated by people through Assessments; VERITAS relates findings to claims but never decides them."
      />
      {claims.status === "loading" && <StateView state="loading" title="Loading claims…" />}
      {claims.status === "error" && <ApiErrorView error={claims.error} subject="Claims" onRetry={claims.reload} />}
      {claims.status === "ready" && claims.data.count === 0 && <StateView state="empty" title={`No claims recorded in ${c.id}.`} />}
      {claims.status === "ready" && claims.data.count > 0 && (
        <ul className="space-y-4">
          {claims.data.items.map((claim) => (
            <li key={claim.id} id={claim.id} className="scroll-mt-20">
              <Panel as="article" labelledBy={`${claim.id}-statement`} className={hash === `#${claim.id}` ? "border-signal/40" : ""}>
                <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
                  <RefId id={claim.id} className="text-fg" />
                  <StatusBadge semantics={CLAIM_STATE[claim.state]} />
                  <span className="ml-auto text-xs text-fg-subtle">Source: {claim.source}</span>
                </header>
                <div className="grid gap-5 px-4 py-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
                  <div>
                    <p id={`${claim.id}-statement`} className="text-[0.9375rem] leading-relaxed text-fg">
                      “{claim.statement}”
                    </p>
                    <h3 className="eyebrow mb-2 mt-5">Related findings</h3>
                    {claim.related_findings.length === 0 ? (
                      <p className="text-xs text-fg-subtle">No finding relates to this claim.</p>
                    ) : (
                      <ul className="space-y-2">
                        {claim.related_findings.map((f) => (
                          <li key={f.id} className="flex flex-wrap items-center gap-2 text-[0.8125rem]">
                            <RefId id={f.id} to={casePath(c.id, `findings/${f.id}`)} />
                            <Tag tone={RELATIONSHIP[f.relationship].tone}>{RELATIONSHIP[f.relationship].label}</Tag>
                            <span className="min-w-0 flex-1 text-fg-muted">{f.title}</span>
                            <span className="flex items-center gap-1 text-2xs text-fg-subtle">
                              <StateGlyph glyph={REVIEW_STATUS[f.review_status].glyph} tone={REVIEW_STATUS[f.review_status].tone} size={8} />
                              {REVIEW_STATUS[f.review_status].label}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                    <h3 className="eyebrow mb-2 mt-5">Referenced evidence</h3>
                    <div className="flex flex-wrap gap-2">
                      {claim.referenced_evidence.length === 0 && <span className="text-xs text-fg-subtle">None.</span>}
                      {claim.referenced_evidence.map((e) => (
                        <span key={e.id} className="flex items-center gap-1.5">
                          <RefId id={e.id} to={casePath(c.id, `evidence/${e.id}`)} />
                          <span className="font-mono text-2xs text-fg-subtle">{e.label}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="rounded-md border border-line bg-ink-900/50 px-4 py-3">
                    <h3 className="eyebrow mb-2">Assessments</h3>
                    {claim.assessments.length === 0 ? (
                      <p className="text-xs leading-relaxed text-fg-subtle">No assessment recorded. Assessments are made by investigators, not by VERITAS.</p>
                    ) : (
                      <ul className="space-y-3">
                        {claim.assessments.map((a) => (
                          <li key={a.id}>
                            <div className="flex flex-wrap items-center gap-2">
                              <RefId id={a.id} />
                              <StatusBadge semantics={ASSESSMENT_STATE[a.state]} />
                            </div>
                            <p className="mt-2 text-[0.8125rem] leading-relaxed text-fg">{a.statement}</p>
                            <p className="mt-1 text-2xs text-fg-subtle">
                              By <span className="mono-id text-2xs">{a.assessed_by}</span>
                            </p>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              </Panel>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
