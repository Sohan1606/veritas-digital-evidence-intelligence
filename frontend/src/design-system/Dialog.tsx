import { useEffect, useId, useRef, type ReactNode } from "react";
import { Icon } from "./icons";

/**
 * Modal surface built on the native <dialog> element (focus trapping, Escape and
 * inert background come from the platform). Used for dialogs, the command palette
 * and the mobile navigation drawer.
 */
export function Dialog({
  open,
  onClose,
  title,
  hideTitle = false,
  placement = "center",
  children,
  className = "",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  hideTitle?: boolean;
  placement?: "center" | "top" | "left";
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    } else if (!open && dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
  }, [open]);

  const position = {
    center: "m-auto w-[min(92vw,34rem)] rounded-lg",
    top: "mx-auto mt-[12vh] w-[min(94vw,40rem)] rounded-lg",
    left: "m-0 h-dvh max-h-dvh w-[min(86vw,20rem)] rounded-none border-y-0 border-l-0",
  }[placement];

  return (
    // Backdrop click is a pointer convenience only; keyboard users close with Escape
    // (native cancel event) or the close button, so no key handler is needed here.
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === ref.current) onClose(); // backdrop click
      }}
      className={`animate-enter border border-line-strong bg-ink-850 p-0 text-fg shadow-[0_24px_64px_-16px_rgba(0,0,0,0.9)] backdrop:bg-transparent ${position} ${className}`}
    >
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
        <h2 id={titleId} className={hideTitle ? "sr-only" : "text-[0.8125rem] font-semibold"}>
          {title}
        </h2>
        {!hideTitle && (
          <button
            type="button"
            onClick={onClose}
            className="rounded-xs p-1 text-fg-subtle transition-micro hover:bg-ink-750 hover:text-fg"
            aria-label="Close"
          >
            <Icon name="close" />
          </button>
        )}
      </div>
      {open && children}
    </dialog>
  );
}
