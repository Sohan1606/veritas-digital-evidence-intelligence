import { useEffect, useMemo, useRef, useState } from "react";
import { CAPTIONS, STAGES, STAGE_SNAPSHOTS, buildScene, captionFor, clamp01, drawScene, frameFor, stageFor, type Scene } from "./sequenceScene";

const SEQUENCE_LABEL = "Concept sequence — illustrative only; it does not depict an executed examination";

function usePrefersReducedMotion(): boolean {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() => typeof window !== "undefined" && (window.matchMedia?.(query).matches ?? false));
  useEffect(() => {
    const media = window.matchMedia?.(query);
    if (!media) return;
    const onChange = () => setReduced(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

/** Sizes a canvas for its CSS box; DPR is capped (lower on small screens) to bound memory. */
function fitCanvas(canvas: HTMLCanvasElement): { width: number; height: number; ctx: CanvasRenderingContext2D | null } {
  const rect = canvas.getBoundingClientRect();
  const cap = rect.width < 768 ? 1.5 : 2;
  const dpr = Math.min(window.devicePixelRatio || 1, cap);
  const w = Math.max(1, Math.round(rect.width * dpr));
  const h = Math.max(1, Math.round(rect.height * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  const ctx = canvas.getContext("2d");
  ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { width: rect.width, height: rect.height, ctx };
}

function fontsReady(): Promise<void> {
  const fonts = typeof document !== "undefined" ? document.fonts : undefined;
  if (!fonts?.ready) return Promise.resolve();
  // Never block the sequence on fonts for long; the canvas redraws once they arrive.
  return Promise.race([fonts.ready.then(() => undefined), new Promise<void>((resolve) => setTimeout(resolve, 1500))]);
}

export function EvidenceSequence() {
  const reduced = usePrefersReducedMotion();
  const scene = useMemo(() => buildScene(), []);
  return reduced ? <Storyboard scene={scene} /> : <ScrollSequence scene={scene} />;
}

function ScrollSequence({ scene }: { scene: Scene }) {
  const sectionRef = useRef<HTMLElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [ready, setReady] = useState(false);
  const [caption, setCaption] = useState(0);
  const [stage, setStage] = useState(0);

  useEffect(() => {
    const section = sectionRef.current;
    const canvas = canvasRef.current;
    if (!section || !canvas) return;
    let raf = 0;
    let lastFrame = -1;
    let size = fitCanvas(canvas);
    let disposed = false;

    const progress = () => {
      const rect = section.getBoundingClientRect();
      const travel = rect.height - window.innerHeight;
      return travel > 0 ? clamp01(-rect.top / travel) : 0;
    };
    const render = (force = false) => {
      raf = 0;
      const p = progress();
      const frame = frameFor(p);
      if (!force && frame === lastFrame) return; // redraw only when the visible frame changes
      lastFrame = frame;
      if (size.ctx) drawScene(size.ctx, size.width, size.height, frame / 720, scene);
      setCaption(captionFor(p));
      setStage(stageFor(p));
    };
    const schedule = () => {
      if (!raf) raf = requestAnimationFrame(() => render());
    };
    const onResize = () => {
      size = fitCanvas(canvas);
      render(true);
    };

    render(true);
    void fontsReady().then(() => {
      if (disposed) return;
      render(true);
      setReady(true);
    });
    window.addEventListener("scroll", schedule, { passive: true });
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(onResize) : null;
    observer?.observe(canvas);
    return () => {
      disposed = true;
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener("scroll", schedule);
      observer?.disconnect();
    };
  }, [scene]);

  return (
    <section ref={sectionRef} aria-labelledby="sequence-title" className="relative h-[520vh] md:h-[640vh]">
      <div className="sticky top-0 h-dvh overflow-hidden">
        <canvas
          ref={canvasRef}
          role="img"
          aria-label="Illustration: fragments become evidence, evidence is examined into observations, observations compose findings related to claims, people review them, and the record is sealed append-only."
          className={`absolute inset-0 h-full w-full transition-opacity duration-500 ${ready ? "opacity-100" : "opacity-0"}`}
        />
        {!ready && (
          <div className="absolute inset-0 grid place-items-center" role="status">
            <div className="w-48">
              <div className="eyebrow mb-3 text-center">Preparing sequence</div>
              <div className="relative h-px overflow-hidden bg-line">
                <div className="absolute inset-y-0 w-1/3 animate-loadscan bg-signal/70" />
              </div>
            </div>
          </div>
        )}

        <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-4 px-5 pt-5 sm:px-10 sm:pt-7">
          <h2 id="sequence-title" className="eyebrow text-fg-subtle">
            {SEQUENCE_LABEL}
          </h2>
        </div>

        <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-ink-950 via-ink-950/90 to-transparent px-5 pb-6 pt-16 sm:px-10 sm:pb-8">
          <div className="mx-auto flex max-w-[1400px] flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
            <p aria-live="polite" className="max-w-xl text-balance text-[1.0625rem] leading-snug text-fg sm:text-xl">
              <span key={caption} className="block animate-fade">
                {CAPTIONS[caption]?.text}
              </span>
            </p>
            <ol aria-label="Sequence stages" className="flex gap-1.5 sm:gap-2">
              {STAGES.map((s, i) => (
                <li key={s.key} aria-current={i === stage ? "step" : undefined} className="flex w-11 flex-col gap-1.5 md:w-[5.75rem]">
                  <span className={`h-0.5 rounded-full transition-state ${i < stage ? "bg-signal/60" : i === stage ? "bg-signal" : "bg-line-strong"}`} />
                  <span className={`hidden text-[0.625rem] leading-tight uppercase tracking-[0.06em] md:block ${i === stage ? "text-fg" : "text-fg-faint"}`}>{s.label}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
    </section>
  );
}

/** Reduced-motion presentation: the same scene as six still panels, no scroll linkage. */
function Storyboard({ scene }: { scene: Scene }) {
  return (
    <section aria-labelledby="sequence-title" className="mx-auto max-w-[1400px] px-5 py-16 sm:px-10">
      <h2 id="sequence-title" className="eyebrow mb-6">
        {SEQUENCE_LABEL}
      </h2>
      <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {STAGES.map((s, i) => (
          <li key={s.key} className="surface overflow-hidden rounded-md border border-line">
            <StillFrame scene={scene} progress={STAGE_SNAPSHOTS[i] ?? 1} />
            <div className="border-t border-line px-4 py-3">
              <div className="eyebrow mb-1">
                {String(i + 1).padStart(2, "0")} · {s.label}
              </div>
              <p className="text-[0.8125rem] leading-relaxed text-fg-muted">{CAPTIONS.filter((c) => c.stage === i).at(-1)?.text}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function StillFrame({ scene, progress }: { scene: Scene; progress: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const draw = () => {
      const { ctx, width, height } = fitCanvas(canvas);
      if (ctx) drawScene(ctx, width, height, progress, scene);
    };
    draw();
    let active = true;
    void fontsReady().then(() => active && draw());
    return () => {
      active = false;
    };
  }, [scene, progress]);
  return <canvas ref={ref} aria-hidden="true" className="block aspect-[16/10] w-full" />;
}
