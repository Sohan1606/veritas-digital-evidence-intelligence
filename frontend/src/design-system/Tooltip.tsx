import { useId, type ReactElement, cloneElement, type ReactNode } from "react";

/**
 * CSS-only tooltip shown on hover and keyboard focus. The trigger is linked to the
 * tooltip text with aria-describedby so assistive technology announces it too.
 */
export function Tooltip({
  content,
  children,
  side = "top",
}: {
  content: ReactNode;
  children: ReactElement<{ "aria-describedby"?: string }>;
  side?: "top" | "bottom" | "right";
}) {
  const id = useId();
  const position = {
    top: "bottom-full left-1/2 mb-2 -translate-x-1/2",
    bottom: "top-full left-1/2 mt-2 -translate-x-1/2",
    right: "left-full top-1/2 ml-2 -translate-y-1/2",
  }[side];
  return (
    <span className="group/tooltip relative inline-flex">
      {cloneElement(children, { "aria-describedby": id })}
      <span
        id={id}
        role="tooltip"
        className={`pointer-events-none invisible absolute z-50 w-max max-w-64 rounded-sm border border-line-strong bg-ink-700 px-2.5 py-1.5 text-xs leading-snug text-fg-muted opacity-0 shadow-lg transition-micro group-focus-within/tooltip:visible group-focus-within/tooltip:opacity-100 group-hover/tooltip:visible group-hover/tooltip:opacity-100 ${position}`}
      >
        {content}
      </span>
    </span>
  );
}
