"""Optional real-browser QA for the VERITAS frontend (not part of scripts/check.sh).

Runs against an already running build (default: `npx vite preview` on :4173 with
the API on :8000) in Chromium, Firefox and WebKit at desktop (1440) and mobile
(390) widths. Per engine and width it checks that every route renders its
content with an h1, that axe-core (WCAG 2.1 A/AA + best practice, colour
contrast included) reports no violations, that the showcase canvas paints, that
graph node selection drives the detail panel, that the command palette (desktop)
or mobile navigation works, and that no console/page errors occur. `--perf`
adds a throttled-CPU mobile scroll probe of the showcase (Chromium only).

Playwright is deliberately NOT a project dependency. Use a throwaway venv:

    python3 -m venv /tmp/pw && /tmp/pw/bin/pip install playwright
    /tmp/pw/bin/python -m playwright install --with-deps chromium firefox webkit
    /tmp/pw/bin/python scripts/qa/browser_smoke.py [--perf]

Exit status is non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

AXE_PATH = Path(__file__).resolve().parents[2] / "frontend/node_modules/axe-core/axe.min.js"
AXE_RUN = """async () => (await axe.run(document, {runOnly: {type: 'tag',
  values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']}}))
  .violations.map(v => v.id + ':' + v.nodes.length)"""
PAINTED = """() => { const c = document.querySelector('canvas');
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0;
  for (let i = 0; i < d.length; i += 4 * 97) if (d[i] + d[i + 1] + d[i + 2] > 60) n++;
  return n; }"""
SCROLL_PROBE = """() => new Promise(res => {
  const H = document.documentElement.scrollHeight, ts = [], start = performance.now();
  function step(t) { ts.push(t); const f = (t - start) / 4000;
    window.scrollTo(0, H * 0.75 * Math.min(1, f));
    if (f < 1) { requestAnimationFrame(step); return; }
    const d = ts.slice(1).map((x, i) => x - ts[i]).sort((a, b) => a - b);
    res({fps: Math.round(1000 * d.length / (ts.at(-1) - ts[0])),
         p95_ms: +d[Math.floor(d.length * 0.95)].toFixed(1),
         frames_over_50ms: d.filter(x => x > 50).length}); }
  requestAnimationFrame(step); })"""

# route -> text that must appear once the route's data has loaded
ROUTES = {
    "/": "Automate work",
    "/app/cases": "CASE-001",
    "/app/cases/CASE-001": "Synthetic Demonstration Case",
    "/app/cases/CASE-001/evidence/EVD-001": "Acquisition context",
    "/app/cases/CASE-001/findings/FND-001": "Alternative explanations",
    "/app/cases/CASE-001/graph": "Relationships",
    "/app/cases/CASE-001/claims": "CLM-003",
    "/app/cases/CASE-001/review": "FND-002",
    "/app/cases/CASE-001/audit": "AUD-001",
    "/app/nowhere": "Page not found",
}
CLAIM_TEXT = "photograph was taken at the recipient"


def wait_text(page: Page, text: str) -> bool:
    try:
        page.wait_for_function(
            "t => document.body.innerText.toLowerCase().includes(t)",
            arg=text.lower(),
            timeout=20_000,
        )
        return True
    except Exception:
        return False


def run_engine(ctx: BrowserContext, base: str, label: str, desktop: bool, axe: str) -> list[str]:
    fails: list[str] = []
    errors: list[str] = []

    def page() -> Page:
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        return pg

    for route, text in ROUTES.items():  # fresh page per route keeps engines comparable
        pg = page()
        pg.goto(base + route, wait_until="domcontentloaded")
        if not wait_text(pg, text):
            fails.append(f"{label} {route}: missing {text!r}")
        if pg.locator("h1").count() < 1:
            fails.append(f"{label} {route}: no h1")
        pg.add_script_tag(content=axe)
        if violations := pg.evaluate(AXE_RUN):
            fails.append(f"{label} {route}: axe {violations}")
        pg.close()

    pg = page()
    pg.goto(base + "/", wait_until="domcontentloaded")
    wait_text(pg, ROUTES["/"])
    pg.evaluate("window.scrollTo(0, document.documentElement.scrollHeight * 0.4)")
    pg.wait_for_timeout(1200)
    if pg.locator("canvas").count() and pg.evaluate(PAINTED) <= 10:
        fails.append(f"{label} showcase canvas did not paint")
    pg.close()

    pg = page()
    pg.goto(base + "/app/cases/CASE-001/graph", wait_until="domcontentloaded")
    wait_text(pg, ROUTES["/app/cases/CASE-001/graph"])
    before = CLAIM_TEXT in pg.inner_text("body").lower()
    node = pg.locator('g[aria-label^="Claim CLM-001"]')
    target = (
        node.first
        if node.count() and node.first.is_visible()
        else pg.get_by_role("button", name="CLM-001").first
    )
    target.click()
    if before or not wait_text(pg, CLAIM_TEXT):
        fails.append(f"{label} graph selection did not drive the detail panel")
    pg.close()

    pg = page()
    pg.goto(base + "/app/cases/CASE-001", wait_until="domcontentloaded")
    wait_text(pg, ROUTES["/app/cases/CASE-001"])
    try:
        if desktop:
            pg.keyboard.press("Control+k")
            pg.wait_for_selector("dialog[open]", timeout=5_000)
            pg.keyboard.type("FND-001")
            pg.wait_for_timeout(300)
            pg.keyboard.press("Enter")
            pg.wait_for_url("**/findings/FND-001", timeout=15_000)
        else:
            pg.get_by_role("button", name="Open navigation").first.click()
            pg.get_by_role("link", name="Evidence").first.click()
            pg.wait_for_url("**/evidence", timeout=15_000)
    except Exception:
        fails.append(f"{label} {'command palette' if desktop else 'mobile nav'} failed ({pg.url})")
    pg.close()

    if errors:
        fails.append(f"{label} console/page errors: {errors[:3]}")
    return fails


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:4173")
    parser.add_argument("--browsers", default="chromium,firefox,webkit")
    parser.add_argument("--perf", action="store_true", help="throttled mobile scroll probe")
    args = parser.parse_args()
    axe = AXE_PATH.read_text(encoding="utf-8")  # installed by `npm ci` in frontend/
    fails: list[str] = []
    with sync_playwright() as p:
        for name in args.browsers.split(","):
            browser = getattr(p, name).launch()
            for width, height in ((1440, 900), (390, 844)):
                ctx = browser.new_context(viewport={"width": width, "height": height})
                ctx.set_default_timeout(60_000)
                engine_fails = run_engine(ctx, args.base, f"{name}@{width}", width > 700, axe)
                print(f"{name} {browser.version} @{width}: {'ok' if not engine_fails else 'FAIL'}")
                fails += engine_fails
                ctx.close()
            browser.close()
        if args.perf:
            browser = p.chromium.launch()
            for rate in (1, 4, 6):
                ctx = browser.new_context(
                    viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True
                )
                pg = ctx.new_page()
                pg.goto(args.base + "/")
                pg.wait_for_selector("canvas")
                pg.wait_for_timeout(1500)
                ctx.new_cdp_session(pg).send("Emulation.setCPUThrottlingRate", {"rate": rate})
                result = json.dumps(pg.evaluate(SCROLL_PROBE))
                print(f"showcase scroll, 390px DPR3, CPU x{rate}: {result}")
                ctx.close()
            browser.close()
    for fail in fails:
        print("FAIL:", fail)
    print("browser smoke:", "passed" if not fails else f"{len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
