import type { ReactNode } from "react";
import { TONE_TEXT, TONE_VAR, type Glyph, type StateSemantics, type Tone } from "./semantics";

/** Shape-coded state marker so meaning survives without colour. */
export function StateGlyph({ glyph, tone, size = 9 }: { glyph: Glyph; tone: Tone; size?: number }) {
  const c = TONE_VAR[tone];
  const r = size / 2 - 0.75;
  const mid = size / 2;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true" className="shrink-0">
      {glyph === "filled" && <circle cx={mid} cy={mid} r={r} fill={c} />}
      {glyph === "ring" && <circle cx={mid} cy={mid} r={r} fill="none" stroke={c} strokeWidth={1.4} />}
      {glyph === "half" && (
        <>
          <circle cx={mid} cy={mid} r={r} fill="none" stroke={c} strokeWidth={1.4} />
          <path d={`M${mid} ${mid - r} A${r} ${r} 0 0 1 ${mid} ${mid + r} Z`} fill={c} />
        </>
      )}
      {glyph === "dashed" && <circle cx={mid} cy={mid} r={r} fill="none" stroke={c} strokeWidth={1.2} strokeDasharray="1.6 1.6" />}
      {glyph === "cross" && (
        <path d={`M1.5 1.5 L${size - 1.5} ${size - 1.5} M${size - 1.5} 1.5 L1.5 ${size - 1.5}`} stroke={c} strokeWidth={1.4} />
      )}
      {glyph === "diamond" && <path d={`M${mid} 0.8 L${size - 0.8} ${mid} L${mid} ${size - 0.8} L0.8 ${mid} Z`} fill={c} />}
    </svg>
  );
}

/** A labelled state (e.g. Evidence Profile status, review status). */
export function StatusBadge({ semantics, className = "", title }: { semantics: StateSemantics; className?: string; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex h-6 items-center gap-1.5 rounded-xs border border-line bg-ink-850 px-2 text-xs font-medium ${TONE_TEXT[semantics.tone]} ${className}`}
    >
      <StateGlyph glyph={semantics.glyph} tone={semantics.tone} />
      <span className="text-fg-muted">{semantics.label}</span>
    </span>
  );
}

/** Neutral metadata tag. */
export function Tag({ children, tone = "muted", className = "" }: { children: ReactNode; tone?: Tone; className?: string }) {
  return (
    <span
      className={`inline-flex h-5 items-center gap-1 rounded-xs border border-line px-1.5 font-mono text-2xs uppercase tracking-[0.08em] ${TONE_TEXT[tone]} ${className}`}
    >
      {children}
    </span>
  );
}
