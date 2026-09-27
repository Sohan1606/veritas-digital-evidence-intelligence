import { Link, useParams } from "react-router";
import type { Evidence, ListResponse } from "../../api/types";
import { useResource } from "../../api/useResource";
import { ApiErrorView, EVIDENCE_TYPE, Icon, PageHeader, StateGlyph, StateView } from "../../design-system";
import { casePath } from "../../app-shell/navigation";
import { caseEyebrow, useCase } from "../cases/CaseLayout";
import { EvidenceProfileView } from "./EvidenceProfileView";

export function EvidencePage() {
  const c = useCase();
  const { evidenceId } = useParams();
  const list = useResource<ListResponse<Evidence>>(`/api/v1/cases/${c.id}/evidence`);
  const selected = list.status === "ready" ? list.data.items.find((e) => e.id === evidenceId) : undefined;

  return (
    <>
      <PageHeader
        eyebrow={caseEyebrow(c.id, "Evidence")}
        title="Evidence"
        description="Registered evidence items and their Evidence Profiles. V1 holds metadata records only; evidence intake, preservation and hashing are reserved for later versions."
      />
      {list.status === "loading" && <StateView state="loading" title="Loading evidence register…" />}
      {list.status === "error" && <ApiErrorView error={list.error} subject="Evidence register" onRetry={list.reload} />}
      {list.status === "ready" && list.data.count === 0 && (
        <StateView state="empty" title={`No evidence registered in ${c.id}.`}>
          Evidence intake is not available in V1, so evidence cannot be added through the application.
        </StateView>
      )}
      {list.status === "ready" && list.data.count > 0 && (
        <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)] xl:grid-cols-[20rem_minmax(0,1fr)]">
          <nav aria-label="Evidence items">
            <ul className="space-y-1.5">
              {list.data.items.map((e) => {
                const type = EVIDENCE_TYPE[e.evidence_type];
                const active = e.id === evidenceId;
                return (
                  <li key={e.id}>
                    <Link
                      to={casePath(c.id, `evidence/${e.id}`)}
                      aria-current={active ? "page" : undefined}
                      className={`flex items-start gap-3 rounded-md border px-3 py-2.5 transition-micro ${
                        active
                          ? "border-line-strong bg-ink-750 shadow-[inset_2px_0_0_var(--color-signal)]"
                          : "border-line bg-ink-850 hover:border-line-strong hover:bg-ink-800"
                      }`}
                    >
                      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-sm border border-line bg-ink-900 text-fg-muted">
                        <Icon name={type.icon} size={14} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-mono text-xs text-fg">{e.label}</span>
                        <span className="mt-1 flex items-center gap-2 text-2xs text-fg-subtle">
                          <span className="mono-id text-2xs">{e.id}</span>
                          <span>{type.label}</span>
                          <span className="ml-auto flex items-center gap-1">
                            <StateGlyph glyph={e.profile_recorded ? "half" : "dashed"} tone={e.profile_recorded ? "warn" : "muted"} size={8} />
                            {e.profile_recorded ? "Profile" : "No profile"}
                          </span>
                        </span>
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
          <div className="min-w-0">
            {!evidenceId && (
              <StateView state="empty" title="Select an evidence item to view its Evidence Profile.">
                Each profile separates Identity, Integrity, Provenance, Quality, Acquisition context and Classification.
              </StateView>
            )}
            {evidenceId && !selected && (
              <StateView state="unavailable" title={`${evidenceId} is not registered in ${c.id}.`} />
            )}
            {selected && !selected.profile_recorded && (
              <StateView state="unavailable" title="Evidence profile unavailable.">
                No Evidence Profile has been recorded for {selected.id} ({selected.label}).
              </StateView>
            )}
            {selected && selected.profile_recorded && <EvidenceProfileView caseId={c.id} evidenceId={selected.id} />}
          </div>
        </div>
      )}
    </>
  );
}
