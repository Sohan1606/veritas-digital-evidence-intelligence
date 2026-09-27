import type { ReactNode } from "react";
import { Link } from "react-router";
import { Icon } from "./icons";

/** Canonical human-readable identifier (CASE-001, EVD-001 …). */
export function RefId({ id, to, className = "" }: { id: string; to?: string; className?: string }) {
  const classes = `mono-id inline-flex items-center rounded-xs border border-line bg-ink-900 px-1.5 py-px text-fg-muted ${className}`;
  if (to) {
    return (
      <Link to={to} className={`${classes} transition-micro hover:border-signal-deep hover:text-signal-strong`}>
        {id}
      </Link>
    );
  }
  return <span className={classes}>{id}</span>;
}

const formatter = new Intl.DateTimeFormat("en-GB", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
  timeZone: "UTC",
});

/** Timestamps are always shown in UTC, explicitly labelled, in mono. */
export function formatUtc(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "invalid time";
  const parts = Object.fromEntries(formatter.formatToParts(date).map((p) => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} UTC`;
}

export function Timestamp({ iso, className = "" }: { iso: string; className?: string }) {
  return (
    <time dateTime={iso} className={`mono-id text-fg-muted ${className}`}>
      {formatUtc(iso)}
    </time>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded-xs border border-line-strong bg-ink-900 px-1 font-mono text-2xs text-fg-subtle">
      {children}
    </kbd>
  );
}

/** Mandatory label wherever demonstration records are displayed. */
export function DemoNotice({ notice = "Demonstration data", compact = false }: { notice?: string; compact?: boolean }) {
  if (compact) {
    return (
      <span className="inline-flex h-6 items-center gap-1.5 rounded-xs border border-warn/30 bg-warn/[0.07] px-2 font-mono text-2xs uppercase tracking-[0.1em] text-warn">
        <Icon name="info" size={12} />
        Demonstration data
      </span>
    );
  }
  return (
    <div role="note" className="flex items-start gap-3 rounded-md border border-warn/25 bg-warn/[0.05] px-4 py-3">
      <Icon name="info" size={16} className="mt-0.5 shrink-0 text-warn" />
      <div className="text-[0.8125rem] leading-relaxed">
        <div className="font-mono text-2xs uppercase tracking-[0.14em] text-warn">{notice}</div>
        <p className="mt-1 text-fg-muted">
          Fictional records created by the development seed. Evidence items are metadata only — no evidence content
          exists — and no examination method has been executed. Nothing shown here is a forensic result.
        </p>
      </div>
    </div>
  );
}
