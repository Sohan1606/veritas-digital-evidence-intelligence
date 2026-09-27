import type { ReactNode } from "react";

export function Panel({
  children,
  className = "",
  as: As = "section",
  labelledBy,
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "article" | "aside";
  labelledBy?: string;
}) {
  return (
    <As className={`surface ${className}`} aria-labelledby={labelledBy}>
      {children}
    </As>
  );
}

export function PanelHeader({
  id,
  eyebrow,
  title,
  actions,
  className = "",
}: {
  id?: string;
  eyebrow?: ReactNode;
  title: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header className={`flex items-start justify-between gap-3 border-b border-line px-4 py-3 ${className}`}>
      <div className="min-w-0">
        {eyebrow && <div className="eyebrow mb-1">{eyebrow}</div>}
        <h2 id={id} className="truncate text-[0.8125rem] font-semibold tracking-[-0.005em] text-fg">
          {title}
        </h2>
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}

/** Page-level heading block used by every workspace page. */
export function PageHeader({
  eyebrow,
  title,
  description,
  meta,
  actions,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
      <div className="min-w-0 max-w-3xl">
        {eyebrow && <div className="eyebrow mb-2">{eyebrow}</div>}
        <h1 className="text-[1.375rem] font-semibold leading-tight tracking-[-0.02em] text-fg md:text-2xl">{title}</h1>
        {description && <p className="mt-2 max-w-2xl text-[0.8125rem] leading-relaxed text-fg-muted">{description}</p>}
        {meta && <div className="mt-3 flex flex-wrap items-center gap-2">{meta}</div>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** Label/value pairs with consistent rhythm. */
export function FactList({ items, className = "" }: { items: { term: ReactNode; detail: ReactNode }[]; className?: string }) {
  return (
    <dl className={`grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-2 text-[0.8125rem] ${className}`}>
      {items.map((item, index) => (
        <div key={index} className="contents">
          <dt className="text-fg-subtle">{item.term}</dt>
          <dd className="min-w-0 break-words text-fg">{item.detail}</dd>
        </div>
      ))}
    </dl>
  );
}
