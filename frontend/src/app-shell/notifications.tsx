import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Icon, StateGlyph, type Tone } from "../design-system";

/**
 * Notification foundation. V1 notices are generated client-side from real system
 * state (API availability, access mode, dataset). Investigator-directed notifications
 * require identity and arrive with V2.
 */
export interface Notice {
  id: string;
  title: string;
  detail: string;
  tone: Tone;
}

interface NotificationContextValue {
  notices: Notice[];
  unread: number;
  push: (notice: Notice) => void;
  markRead: () => void;
}

const NotificationContext = createContext<NotificationContextValue | null>(null);

export function NotificationProvider({ children }: { children: ReactNode }) {
  const [notices, setNotices] = useState<Notice[]>([]);
  const [readIds, setReadIds] = useState<Set<string>>(new Set());

  const push = useCallback((notice: Notice) => {
    setNotices((current) => (current.some((n) => n.id === notice.id) ? current : [notice, ...current]));
  }, []);
  const markRead = useCallback(() => setReadIds(new Set(notices.map((n) => n.id))), [notices]);

  const value = useMemo(
    () => ({ notices, unread: notices.filter((n) => !readIds.has(n.id)).length, push, markRead }),
    [notices, readIds, push, markRead],
  );
  return <NotificationContext.Provider value={value}>{children}</NotificationContext.Provider>;
}

export function useNotifications(): NotificationContextValue {
  const context = useContext(NotificationContext);
  if (!context) throw new Error("useNotifications must be used inside NotificationProvider");
  return context;
}

export function NotificationButton() {
  const { notices, unread, markRead } = useNotifications();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    const onPointer = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        onClick={() => {
          setOpen((value) => !value);
          if (!open) markRead();
        }}
        className="relative flex h-8 w-8 items-center justify-center rounded-sm text-fg-subtle transition-micro hover:bg-ink-750 hover:text-fg"
      >
        <Icon name="bell" />
        {unread > 0 && <span aria-hidden="true" className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-signal" />}
      </button>
      {open && (
        <div
          id={panelId}
          role="region"
          aria-label="Notifications"
          className="animate-enter absolute right-0 top-10 z-40 w-[min(88vw,22rem)] rounded-md border border-line-strong bg-ink-850 shadow-[0_24px_48px_-16px_rgba(0,0,0,0.9)]"
        >
          <div className="border-b border-line px-4 py-2.5 eyebrow">System notices</div>
          {notices.length === 0 ? (
            <p className="px-4 py-4 text-[0.8125rem] text-fg-muted">No notices.</p>
          ) : (
            <ul className="max-h-80 divide-y divide-line overflow-y-auto">
              {notices.map((notice) => (
                <li key={notice.id} className="flex gap-3 px-4 py-3">
                  <span className="mt-1.5">
                    <StateGlyph glyph="filled" tone={notice.tone} size={7} />
                  </span>
                  <div>
                    <div className="text-[0.8125rem] font-medium text-fg">{notice.title}</div>
                    <div className="mt-0.5 text-xs leading-relaxed text-fg-muted">{notice.detail}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <p className="border-t border-line px-4 py-2 text-2xs text-fg-subtle">
            Investigator notifications require identity (V2).
          </p>
        </div>
      )}
    </div>
  );
}
