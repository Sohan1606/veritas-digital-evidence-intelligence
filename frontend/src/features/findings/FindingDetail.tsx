import { useState, type ReactNode } from "react";
import type { Finding } from "../../api/types";
import {
  ButtonLink,
  DemoNotice,
  Icon,
  Panel,
  RELATIONSHIP,
  REVIEW_STATUS,
  RefId,
  StateGlyph,
  StatusBadge,
  Tag,
  Timestamp,
  formatUtc,
  type IconName,
} from "../../design-system";
import { casePath } from "../../app-shell/navigation";

type Inspection = "trace" | "explain" | "challenge" | "why-not";

const ACTIONS: { key: Inspection; label: string; icon: IconName; hint: string }[] = [
  { key: "trace", label: "Trace", icon: "trace", hint: "Follow this finding back to its observations and evidence." },
  { key: "explain", label: "Explain", icon: "explain", hint: "Summarise the recorded basis of this finding." },
  { key: "challenge", label: "Challenge", icon: "challenge", hint: "Record a challenge (reserved: requires identity)." },
  { key: "why-not", label: "Why not", icon: "whyNot", hint: "Show alternative explanations and whether any are excluded." },
];

function uniqueEvidence(finding: Finding) {
  const seen = new Map<string, string>();
  finding.evidence_basis.forEach((b) => seen.set(b.evidence_id, b.evidence_label));
  return [...seen.entries()].map(([id, label]) => ({ id, label }));
}

export function FindingDetail({ finding, caseId, demonstration }: { finding: Finding; caseId: string; demonstration: boolean }) {
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const evidence = uniqueEvidence(finding);
  const openAlternatives = finding.alternative_explanations.filter((a) => a.status === "open").length;
  const allManual = finding.evidence_basis.every((b) => b.origin === "manual");

  return (
    <article aria-labelledby="finding-title" className="space-y-5">
      <header className="surface px-5 py-5">
        <div className="eyebrow mb-3">Finding</div>
        <div className="flex flex-wrap items-center gap-2">
          <RefId id={finding.id} className="text-fg" />
          <StatusBadge semantics={REVIEW_STATUS[finding.review_status]} />
          {demonstration && <DemoNotice compact />}
        </div>
        <h2 id="finding-title" className="mt-3 text-lg font-semibold leading-snug tracking-[-0.01em] text-fg">
          {finding.title}
        </h2>
        <p className="mt-2 max-w-3xl text-[0.875rem] leading-relaxed text-fg-muted">{finding.statement}</p>

        <div role="toolbar" aria-label="Finding actions" className="mt-5 flex flex-wrap gap-1.5">
          {ACTIONS.map((action) => (
            <button
              key={action.key}
              type="button"
              aria-pressed={inspection === action.key}
              aria-controls="finding-inspection"
              title={action.hint}
              onClick={() => setInspection((current) => (current === action.key ? null : action.key))}
              className={`inline-flex h-8 items-center gap-2 rounded-sm border px-3 font-mono text-2xs uppercase tracking-[0.12em] transition-micro ${
                inspection === action.key
                  ? "border-signal/50 bg-signal/10 text-signal-strong"
                  : "border-line-strong bg-ink-850 text-fg-muted hover:border-fg-faint hover:text-fg"
              }`}
            >
              <Icon name={action.icon} size={14} />
              {action.label}
              {action.key === "challenge" && <span className="text-fg-faint">· reserved</span>}
            </button>
          ))}
        </div>
      </header>

      <div id="finding-inspection" aria-live="polite">
        {inspection === "trace" && <TracePanel finding={finding} caseId={caseId} />}
        {inspection === "explain" && <ExplainPanel finding={finding} evidenceCount={evidence.length} openAlternatives={openAlternatives} />}
        {inspection === "why-not" && <WhyNotPanel finding={finding} openAlternatives={openAlternatives} />}
        {inspection === "challenge" && (
          <InspectionPanel title="Challenge" icon="challenge" footer="No challenge can be recorded in V1.">
            <p>
              Challenging a finding records a Review action attributed to an authenticated investigator, with the grounds for the
              challenge. Investigator identity and Review are reserved for later versions, so this action is not available yet.
            </p>
          </InspectionPanel>
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="Observation" count={finding.evidence_basis.length}>
          <ul className="space-y-2">
            {finding.evidence_basis.map((b) => (
              <li key={b.id} className="rounded-sm border border-line bg-ink-900/60 px-3 py-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <RefId id={b.id} />
                  <Tag tone={b.origin === "manual" ? "muted" : "signal"}>{b.origin === "manual" ? "manual record" : `analysis run ${b.analysis_run_id}`}</Tag>
                </div>
                <p className="mt-1.5 text-[0.8125rem] leading-relaxed text-fg">{b.statement}</p>
              </li>
            ))}
          </ul>
        </Section>

        <Section title="Method">
          <p className="text-[0.8125rem] leading-relaxed text-fg">{finding.method}</p>
          {allManual && (
            <p className="mt-3 flex gap-2 text-xs text-fg-subtle">
              <Icon name="info" size={13} className="mt-0.5 shrink-0" />
              No Analysis Run is associated with this finding; its observations were recorded manually.
            </p>
          )}
        </Section>

        <Section title="Evidence basis" count={evidence.length}>
          <ul className="space-y-1.5">
            {evidence.map((e) => (
              <li key={e.id} className="flex items-center gap-2">
                <RefId id={e.id} to={casePath(caseId, `evidence/${e.id}`)} />
                <span className="truncate font-mono text-xs text-fg-muted">{e.label}</span>
              </li>
            ))}
          </ul>
        </Section>

        <Section title="Related claim" count={finding.related_claims.length}>
          {finding.related_claims.length === 0 ? (
            <p className="text-xs text-fg-subtle">No claim is related to this finding.</p>
          ) : (
            <ul className="space-y-2.5">
              {finding.related_claims.map((claim) => (
                <li key={claim.id}>
                  <div className="flex flex-wrap items-center gap-2">
                    <Tag tone={RELATIONSHIP[claim.relationship].tone}>{RELATIONSHIP[claim.relationship].label}</Tag>
                    <RefId id={claim.id} to={`${casePath(caseId, "claims")}#${claim.id}`} />
                  </div>
                  <p className="mt-1.5 text-[0.8125rem] text-fg">{claim.statement}</p>
                  {claim.rationale && <p className="mt-1 text-xs text-fg-subtle">Rationale: {claim.rationale}</p>}
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Alternative explanations" count={finding.alternative_explanations.length}>
          <AlternativeList finding={finding} />
        </Section>

        <Section title="Limitations" count={finding.limitations.length}>
          <ul className="space-y-2">
            {finding.limitations.map((limitation, index) => (
              <li key={index} className="flex gap-2.5 text-[0.8125rem] leading-relaxed text-fg">
                <span className="mt-2 h-px w-2.5 shrink-0 bg-warn" aria-hidden="true" />
                {limitation}
              </li>
            ))}
          </ul>
        </Section>
      </div>

      <Section title="Review status">
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge semantics={REVIEW_STATUS[finding.review_status]} />
          <span className="text-xs text-fg-subtle">
            Recorded by <span className="mono-id text-fg-muted">{finding.created_by}</span> · <Timestamp iso={finding.created_at} />
          </span>
        </div>
        <p className="mt-2 text-xs text-fg-subtle">
          A finding is not relied upon until a person has reviewed it. Recording reviews requires investigator identity (V2).
        </p>
      </Section>
    </article>
  );
}

function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  const id = `finding-section-${title.toLowerCase().replace(/\s+/g, "-")}`;
  return (
    <Panel labelledBy={id} className="px-4 py-4">
      <h3 id={id} className="eyebrow mb-3 flex items-center gap-2">
        {title}
        {count !== undefined && <span className="text-fg-faint">{count}</span>}
      </h3>
      {children}
    </Panel>
  );
}

function AlternativeList({ finding }: { finding: Finding }) {
  if (finding.alternative_explanations.length === 0) {
    return <p className="text-xs text-fg-subtle">No alternative explanations recorded.</p>;
  }
  return (
    <ul className="space-y-2.5">
      {finding.alternative_explanations.map((a, index) => (
        <li key={index} className="flex gap-2.5">
          <span className="mt-1">
            <StateGlyph glyph={a.status === "open" ? "ring" : "cross"} tone={a.status === "open" ? "warn" : "muted"} size={9} />
          </span>
          <div>
            <p className={`text-[0.8125rem] leading-relaxed ${a.status === "open" ? "text-fg" : "text-fg-subtle line-through decoration-fg-faint"}`}>
              {a.explanation}
            </p>
            <p className="mt-0.5 text-xs text-fg-subtle">
              {a.status === "open" ? "Open — no recorded basis excludes this." : `Excluded — ${a.basis}`}
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}

function InspectionPanel({ title, icon, children, footer }: { title: string; icon: IconName; children: ReactNode; footer: string }) {
  return (
    <section aria-label={title} className="animate-enter rounded-md border border-signal/25 bg-signal/[0.03]">
      <header className="flex items-center gap-2 border-b border-signal/15 px-4 py-2.5 font-mono text-2xs uppercase tracking-[0.14em] text-signal">
        <Icon name={icon} size={14} />
        {title}
      </header>
      <div className="px-4 py-4 text-[0.8125rem] leading-relaxed text-fg">{children}</div>
      <footer className="border-t border-signal/15 px-4 py-2 text-2xs text-fg-subtle">{footer}</footer>
    </section>
  );
}

function TracePanel({ finding, caseId }: { finding: Finding; caseId: string }) {
  return (
    <InspectionPanel title="Trace" icon="trace" footer="Built from recorded relationships in the Case Knowledge Graph.">
      <ol className="space-y-3">
        {finding.evidence_basis.map((b) => (
          <li key={b.id} className="flex flex-wrap items-center gap-x-2 gap-y-1.5 font-mono text-xs">
            <RefId id={finding.id} />
            <span className="text-fg-subtle">derived from</span>
            <RefId id={b.id} />
            <span className="text-fg-subtle">derived from</span>
            <RefId id={b.evidence_id} to={casePath(caseId, `evidence/${b.evidence_id}`)} />
            <span className="text-fg-subtle">contained in</span>
            <RefId id={caseId} to={casePath(caseId)} />
            <span className="basis-full pl-1 text-2xs text-fg-subtle">
              Produced by {b.origin === "manual" ? `manual record (${b.recorded_by}) — no Analysis Run` : `Analysis Run ${b.analysis_run_id}`}
            </span>
          </li>
        ))}
      </ol>
      <div className="mt-4">
        <ButtonLink to={`${casePath(caseId, "graph")}?focus=${finding.id}`} size="sm" icon="graph">
          Open in Case Knowledge Graph
        </ButtonLink>
      </div>
    </InspectionPanel>
  );
}

function ExplainPanel({ finding, evidenceCount, openAlternatives }: { finding: Finding; evidenceCount: number; openAlternatives: number }) {
  const relation = finding.related_claims
    .map((c) => `${RELATIONSHIP[c.relationship].label} ${c.id}${c.rationale ? ` (${c.rationale})` : ""}`)
    .join("; ");
  const sentences = [
    `${finding.id} was recorded by ${finding.created_by} at ${formatUtc(finding.created_at)}.`,
    `Method: ${finding.method}`,
    `It rests on ${finding.evidence_basis.length} observation${finding.evidence_basis.length === 1 ? "" : "s"} drawn from ${evidenceCount} evidence item${evidenceCount === 1 ? "" : "s"}.`,
    relation ? `It ${relation}.` : "It is not related to any claim.",
    `${openAlternatives} of ${finding.alternative_explanations.length} alternative explanations remain open; ${finding.limitations.length} limitation${finding.limitations.length === 1 ? " is" : "s are"} recorded.`,
    `Review status: ${REVIEW_STATUS[finding.review_status].label}.${finding.review_status === "unreviewed" ? " No person has reviewed this finding." : ""}`,
  ];
  return (
    <InspectionPanel title="Explain" icon="explain" footer="Composed deterministically from recorded fields. No generated text.">
      <ul className="space-y-1.5">
        {sentences.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ul>
    </InspectionPanel>
  );
}

function WhyNotPanel({ finding, openAlternatives }: { finding: Finding; openAlternatives: number }) {
  return (
    <InspectionPanel title="Why not" icon="whyNot" footer="An alternative is excluded only when a recorded basis excludes it.">
      <p className="mb-3 text-fg-muted">
        {openAlternatives} of {finding.alternative_explanations.length} alternative explanations remain open.
      </p>
      <AlternativeList finding={finding} />
      <p className="mt-3 text-xs text-fg-subtle">
        See also{" "}
        <a className="text-signal hover:text-signal-strong" href="#finding-section-limitations">
          Limitations
        </a>
        .
      </p>
    </InspectionPanel>
  );
}
