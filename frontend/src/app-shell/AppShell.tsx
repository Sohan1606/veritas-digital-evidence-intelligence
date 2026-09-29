import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router";
import { useResource } from "../api/useResource";
import type { CaseSummary, ListResponse, SystemInfo } from "../api/types";
import { Dialog, DemoNotice, Icon, Kbd, StateView } from "../design-system";
import { CommandPalette } from "./CommandPalette";
import { ErrorBoundary } from "./ErrorBoundary";
import { CASE_NAV, WORKSPACE_NAV, casePath, useActiveCaseId } from "./navigation";
import { NotificationButton, NotificationProvider, useNotifications } from "./notifications";
import { Sidebar } from "./Sidebar";
import { SessionProvider, useSession } from "../features/auth/AuthContext";
import { SignInPage } from "../features/auth/SignInPage";

export function AppShell() {
  return (
    <SessionProvider>
      <SessionGate />
    </SessionProvider>
  );
}

function SessionGate() {
  const { state, reload } = useSession();
  if (state.status === "loading") return <StateView state="loading" title="Checking session…" />;
  if (state.status === "error") return <main className="grid min-h-dvh place-items-center bg-ink-950 p-6"><StateView state="unavailable" title="VERITAS identity service unavailable." action={<button type="button" onClick={() => void reload()} className="text-sm text-signal underline">Retry</button>}>{state.error.message}</StateView></main>;
  if (state.status === "signed-out") return <SignInPage />;
  return <NotificationProvider><ShellLayout /></NotificationProvider>;
}

function ShellLayout() {
  const activeCaseId = useActiveCaseId();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  useSystemNotices();

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen((value) => !value);
      } else if (event.key === "/" && !typing) {
        event.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="flex min-h-dvh bg-ink-950">
      <a
        href="#main"
        className="sr-only z-50 rounded-sm bg-fg px-3 py-2 text-ink-950 focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Skip to content
      </a>

      <aside aria-label="Workspace navigation" className="sticky top-0 hidden h-dvh w-60 shrink-0 border-r border-line bg-ink-900 lg:block">
        <Sidebar activeCaseId={activeCaseId} />
      </aside>

      <Dialog open={navOpen} onClose={() => setNavOpen(false)} title="Navigation" placement="left">
        <Sidebar activeCaseId={activeCaseId} onNavigate={() => setNavOpen(false)} />
      </Dialog>

      <div className="flex min-w-0 flex-1 flex-col">
        <ContextBar activeCaseId={activeCaseId} onOpenNav={() => setNavOpen(true)} onOpenSearch={() => setSearchOpen(true)} />
        <main id="main" tabIndex={-1} className="flex-1 px-4 py-6 outline-none sm:px-6 lg:px-10 lg:py-8">
          <div key={location.pathname.split("/").slice(0, 5).join("/")} className="mx-auto w-full max-w-[1400px] animate-enter">
            <ErrorBoundary resetKey={location.pathname}>
              <Outlet />
            </ErrorBoundary>
          </div>
        </main>
      </div>

      <CommandPalette open={searchOpen} onClose={() => setSearchOpen(false)} activeCaseId={activeCaseId} />
    </div>
  );
}

function useBreadcrumbs(activeCaseId: string | null): { label: string; to?: string; mono?: boolean }[] {
  const { pathname } = useLocation();
  const crumbs: { label: string; to?: string; mono?: boolean }[] = [];
  const workspace = WORKSPACE_NAV.find((n) => pathname.startsWith(n.to));
  if (pathname.startsWith("/app/cases/") && activeCaseId) {
    crumbs.push({ label: "Cases", to: "/app/cases" });
    const segment = pathname.split("/")[4];
    crumbs.push({ label: activeCaseId, to: segment ? casePath(activeCaseId) : undefined, mono: true });
    const section = CASE_NAV.find((n) => n.segment === segment);
    if (section) crumbs.push({ label: section.label });
  } else if (workspace) {
    crumbs.push({ label: workspace.label });
  }
  return crumbs;
}

function ContextBar({ activeCaseId, onOpenNav, onOpenSearch }: { activeCaseId: string | null; onOpenNav: () => void; onOpenSearch: () => void }) {
  const crumbs = useBreadcrumbs(activeCaseId);
  const system = useResource<SystemInfo>("/api/v1/system");
  const demo = system.status === "ready" && system.data.access_mode === "demo";

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b border-line bg-ink-950/85 px-3 backdrop-blur-md sm:px-6 lg:px-10">
      <button
        type="button"
        onClick={onOpenNav}
        className="flex h-8 w-8 items-center justify-center rounded-sm text-fg-muted transition-micro hover:bg-ink-750 hover:text-fg lg:hidden"
        aria-label="Open navigation"
      >
        <Icon name="menu" />
      </button>

      <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
        <ol className="flex min-w-0 items-center gap-1.5 text-[0.8125rem]">
          {crumbs.map((crumb, index) => (
            <li key={index} className="flex min-w-0 items-center gap-1.5">
              {index > 0 && <Icon name="chevronRight" size={12} className="shrink-0 text-fg-faint" />}
              {crumb.to ? (
                <Link to={crumb.to} className={`truncate text-fg-subtle transition-micro hover:text-fg ${crumb.mono ? "mono-id" : ""}`}>
                  {crumb.label}
                </Link>
              ) : (
                <span aria-current="page" className={`truncate text-fg ${crumb.mono ? "mono-id" : ""}`}>
                  {crumb.label}
                </span>
              )}
            </li>
          ))}
        </ol>
      </nav>

      {demo && (
        <span className="hidden md:inline-flex">
          <DemoNotice compact />
        </span>
      )}

      <button
        type="button"
        onClick={onOpenSearch}
        className="flex h-8 items-center gap-2 rounded-sm border border-line bg-ink-900 px-2.5 text-[0.8125rem] text-fg-subtle transition-micro hover:border-line-strong hover:text-fg-muted sm:w-64"
        aria-label="Search (Control K)"
      >
        <Icon name="search" size={14} />
        <span className="hidden sm:inline">Search or jump to…</span>
        <span className="ml-auto hidden items-center gap-1 sm:flex">
          <Kbd>Ctrl</Kbd>
          <Kbd>K</Kbd>
        </span>
      </button>
      <NotificationButton />
    </header>
  );
}

/** Publishes notices derived from real system state. */
function useSystemNotices() {
  const { push } = useNotifications();
  const system = useResource<SystemInfo>("/api/v1/system");
  const cases = useResource<ListResponse<CaseSummary>>(system.status === "ready" && system.data.principal ? "/api/v1/cases" : null);

  useEffect(() => {
    if (system.status === "error") {
      push({ id: "api-unreachable", tone: "risk", title: "VERITAS API unavailable", detail: system.error.message });
    } else if (system.status === "ready") {
      if (system.data.access_mode === "demo") {
        push({
          id: "demo-mode",
          tone: "warn",
          title: "Demonstration mode",
          detail: "Explicit development demonstration access. Operational access requires a provisioned identity.",
        });
      } else {
        push({
          id: "restricted-mode",
          tone: "neutral",
          title: "Restricted mode",
          detail: "Sign in with a provisioned account to access authorized case records.",
        });
      }
      if (system.data.database.schema_revision !== system.data.database.expected_revision) {
        push({ id: "schema-outdated", tone: "warn", title: "Database schema outdated", detail: "Run migrations before use." });
      }
    }
  }, [system, push]);

  useEffect(() => {
    if (cases.status === "ready") {
      const demo = cases.data.items.filter((c) => c.demonstration).length;
      if (demo > 0) {
        push({
          id: "demo-dataset",
          tone: "signal",
          title: "Demo dataset loaded",
          detail: `${demo} demonstration case${demo === 1 ? "" : "s"} available. Not real evidence.`,
        });
      }
    }
  }, [cases, push]);
}
