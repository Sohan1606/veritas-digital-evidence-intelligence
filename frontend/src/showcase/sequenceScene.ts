/**
 * The showcase concept sequence, drawn procedurally. `drawScene` is a pure function of
 * (canvas size, progress, scene): no timers, no internal state, no image assets. The
 * same progress always produces the same frame, so the component only redraws when the
 * quantized frame changes.
 */

export const FRAME_COUNT = 720;

export const STAGES = [
  { key: "artifacts", label: "Digital artifacts" },
  { key: "evidence", label: "Evidence" },
  { key: "examination", label: "Examination" },
  { key: "intelligence", label: "Intelligence" },
  { key: "review", label: "Review" },
  { key: "verification", label: "Verification" },
] as const;

export const CAPTIONS: { from: number; stage: number; text: string }[] = [
  { from: 0, stage: 0, text: "An investigation begins with fragments — files, logs, messages, images." },
  { from: 0.1, stage: 0, text: "Every fragment is treated as untrusted data, never as an instruction." },
  { from: 0.18, stage: 1, text: "Admitted material becomes Evidence, profiled by what is known about its identity, integrity and provenance." },
  { from: 0.34, stage: 2, text: "Methods examine evidence and record Observations, each tied to the evidence that produced it." },
  { from: 0.5, stage: 3, text: "Observations are composed into Findings and related to the Claims made in the case." },
  { from: 0.6, stage: 3, text: "Contradictions and alternative explanations stay visible instead of being averaged away." },
  { from: 0.68, stage: 4, text: "People review, challenge and decide. Consequential judgment is never automated." },
  { from: 0.84, stage: 5, text: "The record is append-only and verifiable, so every conclusion traces back to its basis." },
];

export function clamp01(value: number): number {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

export function frameFor(progress: number): number {
  return Math.round(clamp01(progress) * FRAME_COUNT);
}

export function stageFor(progress: number): number {
  return Math.min(STAGES.length - 1, Math.floor(clamp01(progress) * STAGES.length));
}

export function captionFor(progress: number): number {
  let index = 0;
  CAPTIONS.forEach((caption, i) => {
    if (progress >= caption.from) index = i;
  });
  return index;
}

/** Representative progress for each stage, used by the static storyboard. */
export const STAGE_SNAPSHOTS = [0.12, 0.3, 0.46, 0.64, 0.8, 1] as const;

// ---------------------------------------------------------------------------
// Scene model (flow coordinates: t along the flow, s across it, both 0..1)
// ---------------------------------------------------------------------------

interface Fragment {
  t: number;
  s: number;
  size: number;
  kind: number;
  card: number;
  delay: number;
  drift: number;
}
interface SceneNode {
  t: number;
  s: number;
  label: string;
}
interface Link {
  from: number;
  to: number;
  kind: "supports" | "contradicts";
}

export interface Scene {
  fragments: Fragment[];
  evidence: SceneNode[];
  observations: (SceneNode & { source: number })[];
  findings: (SceneNode & { basis: number[]; challenged: boolean })[];
  claims: SceneNode[];
  claimLinks: Link[];
  chain: string[];
}

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const spread = (count: number, i: number, lo = 0.06, hi = 0.94) => (count === 1 ? 0.5 : lo + ((hi - lo) * i) / (count - 1));

export function buildScene(seed = 1606): Scene {
  const rand = mulberry32(seed);
  const fragments: Fragment[] = Array.from({ length: 18 }, (_, i) => ({
    t: 0.01 + rand() * 0.3,
    s: 0.02 + rand() * 0.96,
    size: 0.5 + rand() * 0.7,
    kind: Math.floor(rand() * 3),
    card: i % 6,
    delay: rand(),
    drift: rand() * Math.PI * 2,
  }));
  const evidence = Array.from({ length: 6 }, (_, i) => ({ t: 0.2, s: spread(6, i), label: `EVD-00${i + 1}` }));
  const observations = Array.from({ length: 9 }, (_, i) => ({
    t: 0.43,
    s: spread(9, i, 0.03, 0.97),
    label: `OBS-00${i + 1}`,
    source: Math.min(5, Math.floor((i * 6) / 9)),
  }));
  const findings = [
    { t: 0.63, s: 0.18, label: "FND-001", basis: [0, 1, 2], challenged: false },
    { t: 0.63, s: 0.5, label: "FND-002", basis: [3, 4, 5], challenged: true },
    { t: 0.63, s: 0.82, label: "FND-003", basis: [6, 7, 8], challenged: false },
  ];
  const claims = [
    { t: 0.84, s: 0.32, label: "CLM-001" },
    { t: 0.84, s: 0.7, label: "CLM-002" },
  ];
  const claimLinks: Link[] = [
    { from: 0, to: 0, kind: "supports" },
    { from: 1, to: 0, kind: "contradicts" },
    { from: 1, to: 1, kind: "supports" },
    { from: 2, to: 1, kind: "supports" },
  ];
  const chain = Array.from({ length: 8 }, () =>
    Array.from({ length: 4 }, () => Math.floor(rand() * 16).toString(16)).join(""),
  );
  return { fragments, evidence, observations, findings, claims, claimLinks, chain };
}

// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------

const C = {
  bg: "#05070a",
  dot: "#141b25",
  line: "#1b2330",
  lineStrong: "#283242",
  fg: "#e7ecf3",
  muted: "#a3aebd",
  subtle: "#748092",
  faint: "#505b6b",
  signal: "#5ec4d0",
  signalDeep: "#1d4c53",
  warn: "#d9a44c",
  risk: "#e2685f",
  ok: "#52c08c",
  panel: "#0b0f15",
};
const MONO = '"JetBrains Mono Variable", ui-monospace, Menlo, monospace';

/** Progress of `p` within [a, b], eased. */
function seg(p: number, a: number, b: number): number {
  const x = clamp01((p - a) / (b - a));
  return 1 - (1 - x) ** 3;
}
const lerp = (a: number, b: number, x: number) => a + (b - a) * x;

interface Frame {
  ctx: CanvasRenderingContext2D;
  portrait: boolean;
  x0: number;
  y0: number;
  flow: number;
  across: number;
  card: { w: number; h: number };
  r: number;
  font: number;
}

function point(f: Frame, t: number, s: number): [number, number] {
  return f.portrait ? [f.x0 + s * f.across, f.y0 + t * f.flow] : [f.x0 + t * f.flow, f.y0 + s * f.across];
}

function line(f: Frame, a: [number, number], b: [number, number], color: string, alpha: number, width = 1, dash: number[] = []) {
  if (alpha <= 0.001) return;
  const { ctx } = f;
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.setLineDash(dash);
  ctx.beginPath();
  // Gentle curve along the flow direction.
  const mid = f.portrait ? [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] : [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  ctx.moveTo(a[0], a[1]);
  if (f.portrait) ctx.bezierCurveTo(a[0], mid[1]!, b[0], mid[1]!, b[0], b[1]);
  else ctx.bezierCurveTo(mid[0]!, a[1], mid[0]!, b[1], b[0], b[1]);
  ctx.stroke();
  ctx.setLineDash([]);
}

/** Partial edge from a toward b, `x` of the way. */
function growingLine(f: Frame, a: [number, number], b: [number, number], x: number, color: string, alpha: number, width = 1, dash: number[] = []) {
  if (x <= 0) return;
  const end: [number, number] = [lerp(a[0], b[0], x), lerp(a[1], b[1], x)];
  line(f, a, end, color, alpha, width, dash);
}

function label(f: Frame, text: string, x: number, y: number, color: string, alpha: number, align: CanvasTextAlign = "center", scale = 1) {
  if (alpha <= 0.001) return;
  const { ctx } = f;
  ctx.globalAlpha = alpha;
  ctx.fillStyle = color;
  ctx.font = `500 ${Math.round(f.font * scale)}px ${MONO}`;
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  ctx.fillText(text, x, y);
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function diamond(ctx: CanvasRenderingContext2D, x: number, y: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x, y - r);
  ctx.lineTo(x + r, y);
  ctx.lineTo(x, y + r);
  ctx.lineTo(x - r, y);
  ctx.closePath();
}

export function drawScene(ctx: CanvasRenderingContext2D, width: number, height: number, progress: number, scene: Scene): void {
  const p = clamp01(progress);
  const portrait = height > width * 1.05;
  const padA = portrait ? width * 0.08 : height * 0.12;
  const x0 = portrait ? padA : width * 0.07;
  const y0 = portrait ? height * 0.12 : height * 0.14;
  const flow = portrait ? height * 0.56 : width * 0.86;
  const across = portrait ? width - 2 * padA : height * 0.5;
  const unit = Math.min(width, height);
  const f: Frame = {
    ctx,
    portrait,
    x0,
    y0,
    flow,
    across,
    card: portrait
      ? { w: Math.max(40, Math.min(across / 7.4, 80)), h: Math.max(18, Math.min(flow * 0.05, 28)) }
      : { w: Math.max(84, Math.min(flow * 0.085, 132)), h: Math.max(22, Math.min(across * 0.1, 34)) },
    r: Math.max(5, unit * 0.011),
    font: portrait ? Math.max(7.5, Math.min(width / 44, 10)) : Math.max(9, Math.min(width / 130, 11)),
  };

  ctx.save();
  ctx.globalAlpha = 1;
  ctx.fillStyle = C.bg;
  ctx.fillRect(0, 0, width, height);

  // Measurement lattice — the quiet "lab" surface.
  const step = Math.max(22, unit / 24);
  ctx.fillStyle = C.dot;
  for (let gx = step / 2; gx < width; gx += step) {
    for (let gy = step / 2; gy < height; gy += step) ctx.fillRect(gx, gy, 1, 1);
  }

  const evidencePts = scene.evidence.map((e) => point(f, e.t, e.s));
  const obsPts = scene.observations.map((o) => point(f, o.t, o.s));
  const findingPts = scene.findings.map((n) => point(f, n.t, n.s));
  const claimPts = scene.claims.map((n) => point(f, n.t, n.s));

  // Later stages recede so the current stage leads.
  const recede = (from: number) => lerp(1, 0.45, seg(p, from, from + 0.1));

  // --- Stage 1-2: artifacts converge into evidence -------------------------
  const converge = seg(p, 0.15, 0.3);
  const cardIn = seg(p, 0.2, 0.3);
  scene.fragments.forEach((fr) => {
    const appear = seg(p, fr.delay * 0.07, fr.delay * 0.07 + 0.04);
    const alpha = appear * (1 - seg(p, 0.24, 0.31));
    if (alpha <= 0.001) return;
    const wobble = 0.008 * Math.sin(fr.drift + p * 30);
    const [sx, sy] = point(f, fr.t + wobble, fr.s + wobble * 0.6);
    const [tx, ty] = evidencePts[fr.card]!;
    const x = lerp(sx, tx, converge);
    const y = lerp(sy, ty, converge);
    const size = f.r * 2.1 * fr.size * lerp(1, 0.4, converge);
    ctx.globalAlpha = alpha * 0.9;
    ctx.strokeStyle = fr.kind === 0 ? C.muted : fr.kind === 1 ? C.subtle : C.signal;
    ctx.fillStyle = C.panel;
    ctx.lineWidth = 1;
    if (fr.kind === 0) {
      ctx.fillRect(x - size, y - size * 0.7, size * 2, size * 1.4);
      ctx.strokeRect(x - size, y - size * 0.7, size * 2, size * 1.4);
    } else if (fr.kind === 1) {
      ctx.beginPath();
      ctx.arc(x, y, size * 0.8, 0, Math.PI * 2);
      ctx.stroke();
    } else {
      for (let k = 0; k < 3; k++) ctx.fillRect(x - size, y - size * 0.6 + k * size * 0.6, size * (2 - k * 0.5), 1);
      ctx.globalAlpha = alpha * 0.9;
    }
  });

  // --- Stage 3: examination --------------------------------------------------
  const scan = seg(p, 0.34, 0.47);
  const scanning = p > 0.33 && p < 0.5;
  scene.observations.forEach((o, i) => {
    const reached = scan >= o.s - 0.02;
    const appear = reached ? seg(p, 0.34 + o.s * 0.13, 0.37 + o.s * 0.13) : 0;
    growingLine(f, evidencePts[o.source]!, obsPts[i]!, appear, C.lineStrong, 0.9 * recede(0.66), 1);
  });

  // Evidence cards.
  scene.evidence.forEach((e, i) => {
    if (cardIn <= 0.001) return;
    const [x, y] = evidencePts[i]!;
    const { w, h } = f.card;
    const examined = scan >= e.s - 0.02 && p > 0.34;
    ctx.globalAlpha = cardIn * recede(0.66);
    ctx.fillStyle = C.panel;
    roundRect(ctx, x - w / 2, y - h / 2, w, h, 3);
    ctx.fill();
    ctx.strokeStyle = examined ? C.signalDeep : C.lineStrong;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = examined ? C.signal : C.muted;
    ctx.fillRect(x - w / 2 + 6, y - 3, 6, 6);
    label(f, e.label, x - w / 2 + 17, y + 0.5, C.fg, cardIn * recede(0.66), "left", portrait ? 0.9 : 1);
  });

  if (scanning) {
    const s = lerp(-0.04, 1.04, scan);
    const a = point(f, 0.2 - 0.07, s);
    const b = point(f, 0.2 + 0.07, s);
    const fade = Math.min(seg(p, 0.33, 0.35), 1 - seg(p, 0.47, 0.5));
    line(f, a, b, C.signal, 0.8 * fade, 1.5);
  }

  // Observations.
  scene.observations.forEach((o, i) => {
    const reached = scan >= o.s - 0.02;
    const appear = reached ? seg(p, 0.35 + o.s * 0.13, 0.39 + o.s * 0.13) : 0;
    if (appear <= 0.001) return;
    const [x, y] = obsPts[i]!;
    ctx.globalAlpha = appear * recede(0.66);
    ctx.fillStyle = C.bg;
    ctx.strokeStyle = C.muted;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(x, y, f.r * 0.75, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    if (!portrait) label(f, o.label, x + f.r * 1.6, y, C.subtle, appear * recede(0.66), "left", 0.9);
  });

  // --- Stage 4: intelligence -------------------------------------------------
  const compose = seg(p, 0.5, 0.6);
  scene.findings.forEach((n, i) => {
    n.basis.forEach((b) => growingLine(f, obsPts[b]!, findingPts[i]!, compose, C.faint, 0.9, 1));
  });
  const relate = seg(p, 0.57, 0.67);
  scene.claimLinks.forEach((l) => {
    const contradicts = l.kind === "contradicts";
    growingLine(f, findingPts[l.from]!, claimPts[l.to]!, relate, contradicts ? C.risk : C.signal, contradicts ? 0.8 : 0.75, 1.3, contradicts ? [5, 4] : []);
  });

  scene.findings.forEach((n, i) => {
    const appear = seg(p, 0.53 + i * 0.02, 0.6 + i * 0.02);
    if (appear <= 0.001) return;
    const [x, y] = findingPts[i]!;
    ctx.globalAlpha = appear;
    ctx.fillStyle = C.panel;
    ctx.strokeStyle = C.warn;
    ctx.lineWidth = 1.3;
    diamond(ctx, x, y, f.r * 1.5);
    ctx.fill();
    ctx.stroke();
    const off = portrait ? f.r * 2.6 : f.r * 2.4;
    label(f, n.label, portrait ? x : x, portrait ? y - off : y - off, C.fg, appear, "center", portrait ? 0.9 : 1);
  });

  scene.claims.forEach((n, i) => {
    const appear = seg(p, 0.58 + i * 0.02, 0.65 + i * 0.02);
    if (appear <= 0.001) return;
    const [x, y] = claimPts[i]!;
    const w = f.card.w * 0.9;
    const h = f.card.h;
    ctx.globalAlpha = appear;
    ctx.fillStyle = C.panel;
    ctx.strokeStyle = C.signal;
    ctx.lineWidth = 1;
    roundRect(ctx, x - w / 2, y - h / 2, w, h, h / 2);
    ctx.fill();
    ctx.stroke();
    label(f, n.label, x, y + 0.5, C.fg, appear, "center", portrait ? 0.9 : 1);
  });

  // --- Stage 5: review -------------------------------------------------------
  scene.findings.forEach((n, i) => {
    const sweep = seg(p, 0.68 + i * 0.035, 0.76 + i * 0.035);
    if (sweep <= 0.001) return;
    const [x, y] = findingPts[i]!;
    const color = n.challenged ? C.warn : C.ok;
    ctx.globalAlpha = 0.9;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.2;
    if (n.challenged) ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.arc(x, y, f.r * 2.6, -Math.PI / 2, -Math.PI / 2 + sweep * Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    const tag = n.challenged ? "CHALLENGED" : "REVIEWED";
    const off = f.r * 3.8;
    label(f, tag, x, y + off, color, seg(p, 0.72 + i * 0.035, 0.78 + i * 0.035), "center", 0.82);
  });

  // --- Stage 6: verification — append-only chain ------------------------------
  const chainN = scene.chain.length;
  const chainT = portrait ? 1.13 : 0.5;
  const chainS = portrait ? 0.5 : 1.28;
  const blockW = portrait ? Math.min(across / (chainN + 1.5), 44) : Math.min(flow / (chainN * 1.6), 104);
  const blockH = portrait ? f.card.h * 0.9 : f.card.h * 0.9;
  const spanLen = blockW * chainN * 1.35;
  const [cx, cy] = point(f, chainT, chainS);
  const startX = cx - spanLen / 2;
  scene.chain.forEach((hash, i) => {
    const appear = seg(p, 0.84 + i * 0.015, 0.88 + i * 0.015);
    if (appear <= 0.001) return;
    const x = startX + i * blockW * 1.35;
    const y = cy - blockH / 2;
    ctx.globalAlpha = appear;
    ctx.fillStyle = C.panel;
    ctx.strokeStyle = i === chainN - 1 && p > 0.97 ? C.signal : C.lineStrong;
    ctx.lineWidth = 1;
    roundRect(ctx, x, y, blockW, blockH, 2);
    ctx.fill();
    ctx.stroke();
    if (i > 0) line(f, [x - blockW * 0.35, cy], [x, cy], C.signal, appear * 0.7, 1);
    label(f, portrait ? hash.slice(0, 2) : `${hash}…`, x + blockW / 2, cy + 0.5, C.muted, appear, "center", portrait ? 0.8 : 0.9);
  });
  const sealed = seg(p, 0.93, 1);
  label(f, "APPEND-ONLY RECORD", cx, cy - blockH * 1.2, C.subtle, sealed, "center", 0.85);

  ctx.restore();
}
