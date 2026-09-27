import { Link, NavLink } from "react-router";
import { useResource } from "../api/useResource";
import type { CaseDetail, SystemInfo } from "../api/types";
import { Icon, StateGlyph, Tooltip, VeritasMark } from "../design-system";
import { CASE_NAV, WORKSPACE_NAV, casePath, type NavItem } from "./navigation";

function itemClass({ isActive }: { isActive: boolean }) {
  return `group relative flex h-8 items-center gap-2.5 rounded-sm px-2.5 text-[0.8125rem] transition-micro ${
    isActive
      ? "bg-ink-750 text-fg shadow-[inset_2px_0_0_var(--color-signal)]"
      : "text-fg-muted hover:bg-ink-800 hover:text-fg"
  }`;
}

function Trailing({ item, reserved, counts }: { item: NavItem; reserved: boolean; counts?: CaseDetail["counts"] }) {
  if (reserved) {
    return (
      <span className="ml-auto font-mono text-[0.625rem] uppercase tracking-[0.1em] text-fg-faint" title="Reserved for a later version">
        reserved
      </span>
    );
  }
  if (counts && item.count) {
    return <span className="mono-id ml-auto text-fg-subtle">{item.count(counts)}</span>;
  }
  return null;
}

export function Sidebar({ activeCaseId, onNavigate }: { activeCaseId: string | null; onNavigate?: () => void }) {
  const system = useResource<SystemInfo>("/api/v1/system");
  const activeCase = useResource<CaseDetail>(activeCaseId ? `/api/v1/cases/${activeCaseId}` : null);
  const reserved = new Set(
    system.status === "ready" ? system.data.capabilities.filter((c) => c.status === "reserved").map((c) => c.key) : [],
  );
  const counts = activeCase.status === "ready" ? activeCase.data.counts : undefined;

  return (
    <div className="flex h-full flex-col">
      <Link to="/" onClick={onNavigate} className="flex h-14 shrink-0 items-center gap-2.5 border-b border-line px-4 text-fg">
        <VeritasMark />
        <span className="font-mono text-[0.8125rem] font-medium tracking-[0.32em]">VERITAS</span>
        <span className="ml-auto rounded-xs border border-line px-1 font-mono text-[0.625rem] text-fg-subtle">V1</span>
      </Link>

      <nav aria-label="Primary" className="flex-1 overflow-y-auto px-2.5 py-4">
        <ul className="space-y-0.5">
          {WORKSPACE_NAV.map((item) => (
            <li key={item.key}>
              <NavLink to={item.to} className={itemClass} onClick={onNavigate} end={item.key === "cases"}>
                <Icon name={item.icon} className="shrink-0 opacity-80" />
                {item.label}
                <Trailing item={item} reserved={item.capability ? reserved.has(item.capability) : false} />
              </NavLink>
            </li>
          ))}
        </ul>

        <div className="mb-2 mt-6 flex items-center justify-between px-2.5">
          <span className="eyebrow">Active case</span>
          {activeCaseId && (
            <Link to={casePath(activeCaseId)} onClick={onNavigate} className="mono-id text-signal transition-micro hover:text-signal-strong">
              {activeCaseId}
            </Link>
          )}
        </div>
        {activeCaseId ? (
          <ul className="space-y-0.5">
            {CASE_NAV.map((item) => (
              <li key={item.key}>
                <NavLink to={casePath(activeCaseId, item.segment)} className={itemClass} onClick={onNavigate}>
                  <Icon name={item.icon} className="shrink-0 opacity-80" />
                  {item.label}
                  <Trailing item={item} reserved={item.capability ? reserved.has(item.capability) && !item.count : false} counts={counts} />
                </NavLink>
              </li>
            ))}
          </ul>
        ) : (
          <p className="px-2.5 text-xs leading-relaxed text-fg-subtle">
            Open a case to reach Evidence, Examination, Findings, Claims, Timeline, Graph, Review, Reports and Audit.
          </p>
        )}
      </nav>

      <SessionFooter system={system.status === "ready" ? system.data : null} failed={system.status === "error"} />
    </div>
  );
}

function SessionFooter({ system, failed }: { system: SystemInfo | null; failed: boolean }) {
  const label = failed
    ? "API unreachable"
    : !system
      ? "Connecting…"
      : system.principal === "demonstration_viewer"
        ? "Demonstration viewer"
        : "No session";
  const tone = failed ? "risk" : system ? "ok" : "neutral";
  return (
    <div className="border-t border-line px-4 py-3">
      <div className="flex items-center gap-2">
        <StateGlyph glyph={failed ? "cross" : system ? "filled" : "ring"} tone={tone} size={8} />
        <span className="text-xs text-fg">{label}</span>
        <Tooltip
          side="top"
          content="V1 has no authentication. In demonstration mode an anonymous, read-only viewer may read demonstration cases only."
        >
          <button type="button" aria-label="About this session" className="ml-auto rounded-xs p-0.5 text-fg-subtle hover:text-fg">
            <Icon name="info" size={13} />
          </button>
        </Tooltip>
      </div>
      <div className="mt-1 font-mono text-2xs text-fg-subtle">
        {system ? `unauthenticated · ${system.access_mode} mode · api ${system.api_version}` : "session state unknown"}
      </div>
    </div>
  );
}
