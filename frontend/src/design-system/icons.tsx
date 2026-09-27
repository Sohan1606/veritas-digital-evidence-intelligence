import type { SVGProps } from "react";

/**
 * VERITAS icon set: 16px grid, 1.5px stroke, square-ish terminals.
 * Icons are decorative by default (aria-hidden); pair them with visible text.
 */
const paths = {
  mywork: "M2.5 9.5h3l1 2h3l1-2h3M2.5 9.5 4 3.5h8l1.5 6v3h-11z",
  cases: "M2 4.5h4.2l1.3 1.5H14v6.5H2zM2 4.5V3h4",
  evidence: "M4 2.5h5.5L12 5v8.5H4zM9.5 2.5V5H12M6 8h4M6 10.5h4",
  examination: "M7 3a4 4 0 1 1 0 8 4 4 0 0 1 0-8zM10 10l3.5 3.5M5 7h4",
  finding: "M8 2 13.5 8 8 14 2.5 8zM8 5.5v3M8 10.5v.01",
  claim: "M3 4h10v6.5H7.5L4.5 13v-2.5H3zM5.5 7h5",
  timeline: "M2 8h12M4 5.5v5M8 4v8M12 6v4",
  graph: "M4 4.5a1.5 1.5 0 1 1 0-.01M12 4.5a1.5 1.5 0 1 1 0-.01M8 12.5a1.5 1.5 0 1 1 0-.01M5.3 4.5h5.4M4.7 5.8l2.6 5.4M11.3 5.8l-2.6 5.4",
  review: "M8 2.5 13 4.5v3.7c0 2.7-2.1 4.7-5 5.8-2.9-1.1-5-3.1-5-5.8V4.5zM5.8 8l1.5 1.5 3-3",
  report: "M4 2.5h8v11H4zM6 5.5h4M6 8h4M6 10.5h2.5",
  audit: "M3 3.5h10M3 6.5h10M3 9.5h6M3 12.5h6M12 9v2.5l1.5 1",
  search: "M7 2.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9zM10.5 10.5 14 14",
  bell: "M4 11V7a4 4 0 0 1 8 0v4l1 1.5H3zM6.5 14h3",
  menu: "M2.5 4.5h11M2.5 8h11M2.5 11.5h11",
  close: "M4 4l8 8M12 4l-8 8",
  arrowRight: "M3 8h10M9.5 4.5 13 8l-3.5 3.5",
  chevronRight: "M6 3.5 10.5 8 6 12.5",
  chevronDown: "M3.5 6 8 10.5 12.5 6",
  info: "M8 2a6 6 0 1 1 0 12A6 6 0 0 1 8 2zM8 7v4M8 5v.01",
  image: "M2.5 3.5h11v9h-11zM2.5 10.5l3-3 3 3 2-2 3 3M10.5 6a.5.5 0 1 1 0-.01",
  video: "M2.5 4h8v8h-8zM10.5 7l3-2v6l-3-2",
  audio: "M3 6.5v3M5.5 4.5v7M8 2.5v11M10.5 5v6M13 7v2",
  document: "M4 2.5h5.5L12 5v8.5H4zM9.5 2.5V5H12",
  email: "M2.5 4h11v8h-11zM2.5 4.5 8 9l5.5-4.5",
  message: "M2.5 3.5h11v7.5H6l-3.5 2.5z",
  other: "M3 3h10v10H3zM6 8h4",
  trace: "M3 13V9.5A2.5 2.5 0 0 1 5.5 7h5A2.5 2.5 0 0 0 13 4.5V3M3 13a1 1 0 1 1 0-.01M13 3a1 1 0 1 1 0-.01",
  explain: "M3 3h10v7H8l-3 3v-3H3zM6 5.5h4M6 7.5h2.5",
  challenge: "M8 2.5 14 13H2zM8 6.5v3M8 11v.01",
  whyNot: "M8 2a6 6 0 1 1 0 12A6 6 0 0 1 8 2zM6.3 6.2A1.8 1.8 0 1 1 8 8.5V9.5M8 11.5v.01",
  external: "M9 3h4v4M13 3 7.5 8.5M11 9.5V13H3V5h3.5",
  lock: "M4.5 7.5h7v6h-7zM6 7.5V5.5a2 2 0 0 1 4 0v2",
  pulse: "M2 8h3l1.5-3.5L9 12l1.5-4H14",
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, size = 16, ...rest }: { name: IconName; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      <path d={paths[name]} />
    </svg>
  );
}

/** The VERITAS mark: two converging lines resolved into a single sealed point. */
export function VeritasMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <path d="M7 8.5 16 25l9-16.5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
      <path d="M11.5 8.5 16 17l4.5-8.5" fill="none" stroke="currentColor" strokeOpacity={0.35} strokeWidth={1.25} strokeLinecap="round" strokeLinejoin="round" />
      <rect x="14.1" y="22.6" width="3.8" height="3.8" rx="0.7" fill="var(--color-signal)" />
    </svg>
  );
}
