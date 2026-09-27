import type { SystemInfo } from "../api/types";
import { useResource } from "../api/useResource";
import { ButtonLink, Icon, StateGlyph, VeritasMark } from "../design-system";
import { EvidenceSequence } from "./EvidenceSequence";

const PRINCIPLES = [
  {
    title: "Evidence is untrusted data",
    body: "Content under examination is never interpreted as instructions — not by people's tools, and not by models.",
  },
  {
    title: "Every finding shows its basis",
    body: "Observations, method, evidence, limitations and alternative explanations travel with each finding.",
  },
  {
    title: "People decide",
    body: "VERITAS automates work, not accountability. Review, challenge and assessment are human acts.",
  },
  {
    title: "The record is append-only",
    body: "Actions are captured as audit events that cannot be edited or deleted.",
  },
];

export default function ShowcasePage() {
  return (
    <div className="min-h-dvh bg-ink-950 text-fg">
      <header className="absolute inset-x-0 top-0 z-10">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between px-5 py-4 sm:px-10">
          <div className="flex items-center gap-2.5">
            <VeritasMark />
            <span className="text-[0.8125rem] font-semibold tracking-[0.2em]">VERITAS</span>
          </div>
          <ButtonLink to="/app/cases" size="sm" variant="secondary" trailingIcon="arrowRight">
            Open workspace
          </ButtonLink>
        </div>
      </header>

      <main id="main">
        <section aria-labelledby="hero-title" className="lab-grid relative flex min-h-[92dvh] items-end">
          <div className="mx-auto w-full max-w-[1400px] px-5 pb-16 pt-32 sm:px-10 sm:pb-24">
            <p className="eyebrow mb-6">Digital evidence intelligence &amp; verification</p>
            <h1 id="hero-title" className="max-w-4xl font-display text-[clamp(2.75rem,8vw,6.5rem)] leading-[0.95] tracking-[-0.01em] text-fg">
              Automate work,
              <br />
              <span className="text-fg-muted">not accountability.</span>
            </h1>
            <p className="mt-8 max-w-xl text-[0.9375rem] leading-relaxed text-fg-muted sm:text-base">
              A workspace for trained investigators to organize evidence, trace findings to their basis and keep every
              conclusion open to review. This is the V1 foundation: case records, evidence profiles, findings and the
              case knowledge graph — read-only, on synthetic demonstration data.
            </p>
            <div className="mt-10 flex flex-wrap items-center gap-3">
              <ButtonLink to="/app/cases/CASE-001" variant="primary" trailingIcon="arrowRight">
                Open demonstration case
              </ButtonLink>
              <a href="#sequence-title" className="inline-flex items-center gap-1.5 px-2 text-[0.8125rem] text-fg-subtle transition-micro hover:text-fg">
                How it works <Icon name="chevronDown" size={14} />
              </a>
            </div>
          </div>
        </section>

        <EvidenceSequence />

        <section aria-labelledby="principles-title" className="border-t border-line">
          <div className="mx-auto max-w-[1400px] px-5 py-20 sm:px-10">
            <h2 id="principles-title" className="eyebrow mb-10">
              Operating principles
            </h2>
            <ul className="grid gap-px overflow-hidden rounded-md border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
              {PRINCIPLES.map((item, i) => (
                <li key={item.title} className="bg-ink-950 px-5 py-6">
                  <div className="mono-id mb-4 text-fg-faint">{String(i + 1).padStart(2, "0")}</div>
                  <h3 className="text-[0.9375rem] font-medium text-fg">{item.title}</h3>
                  <p className="mt-2 text-[0.8125rem] leading-relaxed text-fg-muted">{item.body}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <Capabilities />
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-[1400px] flex-col gap-3 px-5 py-8 text-xs text-fg-subtle sm:flex-row sm:items-center sm:justify-between sm:px-10">
          <p>For use by trained investigators. Not a source of legal conclusions. All data shown is synthetic demonstration data — not real evidence.</p>
          <p className="mono-id text-fg-faint">MIT License</p>
        </div>
      </footer>
    </div>
  );
}

/** What this build can and cannot do — read from the backend, never hard-coded here. */
function Capabilities() {
  const system = useResource<SystemInfo>("/api/v1/system");
  return (
    <section aria-labelledby="capabilities-title" className="border-t border-line">
      <div className="mx-auto grid max-w-[1400px] gap-10 px-5 py-20 sm:px-10 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <div>
          <h2 id="capabilities-title" className="eyebrow mb-4">
            In this version
          </h2>
          <p className="text-[0.8125rem] leading-relaxed text-fg-muted">
            {system.status === "ready"
              ? `VERITAS ${system.data.version}. Capability state is reported by the running backend.`
              : "Capability state is reported by the running backend."}
          </p>
        </div>
        {system.status === "loading" && <p className="text-[0.8125rem] text-fg-subtle">Reading capability state…</p>}
        {system.status === "error" && (
          <p className="text-[0.8125rem] text-fg-muted" role="status">
            The backend is not reachable, so capability state cannot be shown. Nothing is assumed available.
          </p>
        )}
        {system.status === "ready" && (
          <ul className="divide-y divide-line border-y border-line">
            {system.data.capabilities.map((c) => (
              <li key={c.key} className="grid gap-1 py-3 sm:grid-cols-[16rem_7rem_minmax(0,1fr)] sm:items-baseline sm:gap-4">
                <span className="text-[0.8125rem] text-fg">{c.label}</span>
                <span className={`flex items-center gap-1.5 text-xs ${c.status === "available" ? "text-ok" : "text-fg-subtle"}`}>
                  <StateGlyph glyph={c.status === "available" ? "filled" : "dashed"} tone={c.status === "available" ? "ok" : "neutral"} size={8} />
                  {c.status === "available" ? "Available" : "Reserved"}
                </span>
                <span className="text-xs leading-relaxed text-fg-subtle">{c.note}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
