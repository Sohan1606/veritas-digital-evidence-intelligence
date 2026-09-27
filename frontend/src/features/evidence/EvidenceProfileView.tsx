import type { EvidenceProfile, ProfileSection, ProfileSectionKey, ProfileStatus } from "../../api/types";
import { PROFILE_SECTION_KEYS } from "../../api/types";
import { useResource } from "../../api/useResource";
import {
  ApiErrorView,
  EVIDENCE_TYPE,
  Icon,
  PROFILE_STATUS,
  Panel,
  RefId,
  StateGlyph,
  StateView,
  StatusBadge,
  Tag,
  Timestamp,
} from "../../design-system";

/** Presentation copy: what each Evidence Profile section is about. */
const SECTION_COPY: Record<ProfileSectionKey, { title: string; scope: string }> = {
  identity: { title: "Identity", scope: "What the item is: name, media type, size." },
  integrity: { title: "Integrity", scope: "Whether content is unchanged since acquisition." },
  provenance: { title: "Provenance", scope: "Where the item came from and who supplied it." },
  quality: { title: "Quality", scope: "Whether content is fit for examination." },
  acquisition_context: { title: "Acquisition context", scope: "How and when the item was acquired." },
  classification: { title: "Classification", scope: "What kind of evidence this is and its data origin." },
};

const STATUS_ORDER: ProfileStatus[] = ["verified", "partial", "unknown", "not_available"];

export function EvidenceProfileView({ caseId, evidenceId }: { caseId: string; evidenceId: string }) {
  const profile = useResource<EvidenceProfile>(`/api/v1/cases/${caseId}/evidence/${evidenceId}/profile`);

  if (profile.status === "loading") return <StateView state="loading" title="Loading evidence profile…" />;
  if (profile.status === "error") return <ApiErrorView error={profile.error} subject="Evidence profile" onRetry={profile.reload} />;

  const p = profile.data;
  const type = EVIDENCE_TYPE[p.evidence.evidence_type];
  return (
    <article aria-labelledby="profile-title" className="space-y-5">
      <header className="surface px-5 py-5">
        <div className="eyebrow mb-3">Evidence Profile</div>
        <div className="flex flex-wrap items-center gap-2">
          <RefId id={p.evidence.id} className="text-fg" />
          <Tag>
            <Icon name={type.icon} size={11} /> {type.label}
          </Tag>
          <Tag tone="neutral">{p.evidence.state}</Tag>
        </div>
        <h2 id="profile-title" className="mt-3 font-mono text-base font-medium text-fg">
          {p.evidence.label}
        </h2>
        {p.evidence.description && <p className="mt-2 max-w-3xl text-[0.8125rem] leading-relaxed text-fg-muted">{p.evidence.description}</p>}
        <p className="mt-3 text-xs text-fg-subtle">
          Recorded by <span className="mono-id text-fg-muted">{p.recorded_by}</span> · updated <Timestamp iso={p.updated_at} />
        </p>
      </header>

      <section aria-label="Status definitions" className="grid gap-2 rounded-md border border-line px-4 py-3 sm:grid-cols-2">
        {STATUS_ORDER.map((status) => (
          <div key={status} className="flex gap-2.5 text-xs leading-snug">
            <span className="mt-0.5">
              <StateGlyph glyph={PROFILE_STATUS[status].glyph} tone={PROFILE_STATUS[status].tone} size={10} />
            </span>
            <span>
              <span className="font-medium text-fg">{PROFILE_STATUS[status].label}</span>
              <span className="text-fg-subtle"> — {p.status_definitions[status]}</span>
            </span>
          </div>
        ))}
      </section>

      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
        {PROFILE_SECTION_KEYS.map((key) => (
          <ProfileSectionCard key={key} sectionKey={key} section={p[key]} />
        ))}
      </div>

      <p className="text-xs text-fg-subtle">
        Sections are assessed independently. VERITAS does not combine them into an overall score or an authenticity verdict.
      </p>
    </article>
  );
}

function ProfileSectionCard({ sectionKey, section }: { sectionKey: ProfileSectionKey; section: ProfileSection }) {
  const copy = SECTION_COPY[sectionKey];
  const headingId = `profile-section-${sectionKey}`;
  return (
    <Panel labelledBy={headingId} className="flex flex-col" as="section">
      <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div>
          <h3 id={headingId} className="text-[0.8125rem] font-semibold text-fg">
            {copy.title}
          </h3>
          <p className="mt-0.5 text-xs text-fg-subtle">{copy.scope}</p>
        </div>
        <StatusBadge semantics={PROFILE_STATUS[section.status]} className="shrink-0" />
      </header>
      {section.attributes.length === 0 && <p className="px-4 py-3 text-xs italic text-fg-faint">No attributes recorded for this section.</p>}
      {section.attributes.length > 0 && (
        <dl className="divide-y divide-line/70 px-4">
          {section.attributes.map((a) => (
            <div key={a.key} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 py-2.5">
              <dt className="text-xs text-fg-subtle">{a.label}</dt>
              <dd className="row-span-2 self-center">
                <Tag tone={a.basis === "computed" ? "ok" : "muted"}>{a.basis}</Tag>
              </dd>
              <dd className={a.value ? "mono-id break-all text-fg" : "text-xs italic text-fg-faint"}>{a.value ?? "Not recorded"}</dd>
            </div>
          ))}
        </dl>
      )}
      {section.note && (
        <p className="mt-auto flex gap-2 border-t border-line px-4 py-3 text-xs leading-relaxed text-fg-muted">
          <Icon name="info" size={13} className="mt-0.5 shrink-0 text-fg-subtle" />
          {section.note}
        </p>
      )}
    </Panel>
  );
}
