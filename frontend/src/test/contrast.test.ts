import css from "../styles.css?raw";

/**
 * jsdom cannot evaluate colour contrast, so this guards the design tokens
 * directly: every foreground/semantic token used for text must reach
 * WCAG 2.x AA (4.5:1) on every ink surface it can be placed on.
 */
function token(name: string): string {
  const match = new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`).exec(css);
  if (!match?.[1]) throw new Error(`token --color-${name} not found`);
  return match[1];
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * (r ?? 0) + 0.7152 * (g ?? 0) + 0.0722 * (b ?? 0);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return ((hi ?? 0) + 0.05) / ((lo ?? 0) + 0.05);
}

const SURFACES = ["ink-950", "ink-900", "ink-850", "ink-800", "ink-750", "ink-700"];
const TEXT_TOKENS = [
  "fg",
  "fg-muted",
  "fg-subtle",
  "fg-faint",
  "signal",
  "ok",
  "warn",
  "risk",
  "neutral",
];

describe("design token contrast", () => {
  for (const text of TEXT_TOKENS) {
    it(`${text} reaches 4.5:1 on every ink surface`, () => {
      for (const surface of SURFACES) {
        const ratio = contrast(token(text), token(surface));
        expect(ratio, `${text} on ${surface} = ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(4.5);
      }
    });
  }

  it("keeps the text hierarchy ordered (fg > muted > subtle > faint)", () => {
    const l = ["fg", "fg-muted", "fg-subtle", "fg-faint"].map((t) => luminance(token(t)));
    for (let i = 1; i < l.length; i++) expect(l[i - 1]).toBeGreaterThan(l[i] ?? 0);
  });
});
