import { Link, NavLink, useNavigate } from "react-router";
import { useResource } from "../api/useResource";
import type { CaseDetail, SystemInfo } from "../api/types";
import { Icon, StateGlyph, VeritasMark } from "../design-system";
import { CASE_NAV, WORKSPACE_NAV, casePath, type NavItem } from "./navigation";
import { useSession } from "../features/auth/AuthContext";

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
  const auth = useSession();
  const activeCase = useResource<CaseDetail>(activeCaseId ? `/api/v1/cases/${activeCaseId}` : null);
  const reserved = new Set(
    system.status === "ready" ? system.data.capabilities.filter((c) => c.status === "reserved").map((c) => c.key) : [],
  );
  const counts = activeCase.status === "ready" ? activeCase.data.counts : undefined;
  const casePermissions = auth.state.status === "ready"
    ? auth.state.session.case_capabilities[activeCaseId ?? ""] ?? auth.state.session.case_capabilities["*"] ?? []
    : [];

  return (
    <div className="flex h-full flex-col">
      <Link to="/" onClick={onNavigate} className="flex h-14 shrink-0 items-center gap-2.5 border-b border-line px-4 text-fg">
        <VeritasMark />
        <span className="font-mono text-[0.8125rem] font-medium tracking-[0.32em]">VERITAS</span>
        <span className="ml-auto rounded-xs border border-line px-1 font-mono text-[0.625rem] text-fg-subtle">V2</span>
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
        {auth.state.status === "ready" && auth.state.session.capabilities.includes("users:read") && (
          <div className="mt-5 border-t border-line pt-3">
            <p className="eyebrow px-2.5 pb-2">Administration</p>
            <ul className="space-y-0.5">
              <li><NavLink to="/app/admin/users" className={itemClass} onClick={onNavigate}><Icon name="review" />Identity management</NavLink></li>
              {auth.state.session.capabilities.includes("security_audit:read") && <li><NavLink to="/app/admin/security-audit" className={itemClass} onClick={onNavigate}><Icon name="audit" />Security audit</NavLink></li>}
            </ul>
          </div>
        )}

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
            {CASE_NAV.filter((item) => !item.permission || casePermissions.includes(item.permission)).map((item) => (
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
  const { state, signOut } = useSession();
  const navigate = useNavigate();
  const leaveIdentity = () => {
    void signOut()
      .then(() => navigate("/app/cases", { replace: true }))
      .catch(() => undefined);
  };
  const signedIn = state.status === "ready" && state.session.authenticated;
  const label = failed ? "API unreachable" : signedIn ? state.session.display_name ?? state.session.user_id ?? "Signed in" : state.status === "ready" && state.session.demonstration ? "Demonstration viewer" : "Session unavailable";
  const subline = signedIn
    ? `${state.session.user_id} · ${state.session.roles.join(", ") || "no roles"}`
    : system ? `read-only · ${system.access_mode} mode` : "session state unknown";
  return (
    <div className="border-t border-line px-4 py-3">
      <div className="flex items-center gap-2">
        <StateGlyph glyph={failed ? "cross" : state.status === "ready" ? "filled" : "ring"} tone={failed ? "risk" : state.status === "ready" ? "ok" : "neutral"} size={8} />
        <span className="truncate text-xs text-fg">{label}</span>
        <button type="button" onClick={leaveIdentity} className="ml-auto rounded-xs px-1.5 py-1 text-xs text-fg-subtle hover:text-fg" aria-label={signedIn ? "Sign out" : "Sign in"}>
          {signedIn ? "Sign out" : "Sign in"}
        </button>
      </div>
      <div className="mt-1 truncate font-mono text-2xs text-fg-subtle">{subline}</div>
    </div>
  );
}
