import type { ReactNode } from "react";
import type { ApiError } from "../api/client";
import { Button } from "./Button";
import { Icon, type IconName } from "./icons";

/**
 * The single component for non-ready UI states. Every major view expresses
 * loading / empty / error / partial / unavailable through this, with specific wording.
 */
export type ViewState = "loading" | "empty" | "error" | "partial" | "unavailable";

const presentation: Record<ViewState, { icon: IconName; tone: string; role: "status" | "alert" }> = {
  loading: { icon: "pulse", tone: "text-signal", role: "status" },
  empty: { icon: "info", tone: "text-fg-subtle", role: "status" },
  error: { icon: "challenge", tone: "text-risk", role: "alert" },
  partial: { icon: "info", tone: "text-warn", role: "status" },
  unavailable: { icon: "lock", tone: "text-fg-subtle", role: "status" },
};

export function StateView({
  state,
  title,
  children,
  action,
  requestId,
  compact = false,
}: {
  state: ViewState;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  requestId?: string | null;
  compact?: boolean;
}) {
  const p = presentation[state];
  return (
    <div
      role={p.role}
      aria-live={state === "loading" ? "polite" : undefined}
      aria-busy={state === "loading" || undefined}
      data-state={state}
      className={`flex flex-col items-start gap-2 ${compact ? "px-4 py-4" : "rounded-md border border-dashed border-line px-5 py-8"}`}
    >
      <div className={`flex items-center gap-2 ${p.tone}`}>
        {state === "loading" ? <LoadingBar /> : <Icon name={p.icon} size={15} />}
        <span className="text-[0.8125rem] font-medium text-fg">{title}</span>
      </div>
      {children && <div className="max-w-xl text-[0.8125rem] leading-relaxed text-fg-muted">{children}</div>}
      {requestId && (
        <div className="text-2xs text-fg-subtle">
          Request <span className="mono-id text-fg-muted">{requestId}</span>
        </div>
      )}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

/** Precise, non-spinner loading indicator. */
function LoadingBar() {
  return (
    <span aria-hidden="true" className="relative inline-block h-[3px] w-6 overflow-hidden rounded-full bg-line-strong">
      <span className="absolute inset-y-0 left-0 w-2 animate-loadscan rounded-full bg-signal" />
    </span>
  );
}

/** Maps an API failure to the correct state with honest wording. */
export function ApiErrorView({ error, subject, onRetry, compact }: { error: ApiError; subject: string; onRetry?: () => void; compact?: boolean }) {
  if (error.kind === "restricted") {
    return (
      <StateView state="unavailable" title={`${subject} unavailable — sign-in required.`} requestId={error.requestId} compact={compact}>
        Sign in with an active, provisioned account to continue.
      </StateView>
    );
  }
  if (error.kind === "forbidden") {
    return (
      <StateView state="unavailable" title={`${subject} unavailable — access denied.`} requestId={error.requestId} compact={compact}>
        This identity does not have the required capability for this action.
      </StateView>
    );
  }
  if (error.kind === "unreachable") {
    return (
      <StateView
        state="unavailable"
        title="VERITAS API unreachable."
        requestId={error.requestId}
        compact={compact}
        action={onRetry && <Button size="sm" onClick={onRetry}>Retry</Button>}
      >
        {subject} cannot be loaded until the backend is running and reachable through this origin.
      </StateView>
    );
  }
  if (error.kind === "not_found") {
    return (
      <StateView state="unavailable" title={`${subject} unavailable.`} requestId={error.requestId} compact={compact}>
        {error.message}
      </StateView>
    );
  }
  return (
    <StateView
      state="error"
      title={`${subject} could not be loaded.`}
      requestId={error.requestId}
      compact={compact}
      action={onRetry && <Button size="sm" onClick={onRetry}>Retry</Button>}
    >
      {error.message}
    </StateView>
  );
}
