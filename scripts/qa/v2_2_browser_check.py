"""Optional real-browser check of the V2.2 retrieve/verify UI (not part of scripts/check.sh).

Starts the disposable ``veritas-v22-browser`` Compose project (same helpers and safety rules as
``v2_2_runtime_check.py``), signs in through the real sign-in page in Chromium, and exercises the
EvidenceObject card against the production nginx Content-Security-Policy:

* Retrieve performs an actual browser download (name, bytes) with no CSP violation.
* Verify shows Integrity match, Integrity mismatch (recorded vs recomputed) and Verification
  unavailable; Retrieve shows Retrieval unavailable; an authentication failure is never shown
  as "Verification unavailable".
* axe-core (WCAG 2.1 A/AA and best practice, colour contrast enabled) reports no violations in
  each result state. jsdom cannot evaluate contrast, so this is the only place it is checked.

Playwright is deliberately NOT a project dependency. Use a throwaway venv:

    python3 -m venv /tmp/pw && /tmp/pw/bin/pip install playwright
    /tmp/pw/bin/python -m playwright install --with-deps chromium
    (cd frontend && npm ci)          # provides axe-core
    /tmp/pw/bin/python scripts/qa/v2_2_browser_check.py

Requires Docker and a free port 8080. Exit status is non-zero if any check fails.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import BrowserContext, Locator, Page, expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v2_2_runtime_check as rc

BASE = f"http://{rc.HOST}:{rc.PORT}"
AXE_PATH = rc.ROOT / "frontend" / "node_modules" / "axe-core" / "axe.min.js"
AXE_RUN = """async (selector) => (await axe.run(document.querySelector(selector), {
  runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']}}))
  .violations.map(v => v.id + ': ' + v.nodes.map(n => n.target.join(' ')).join(', '))"""
ACCUSATORY = ("fake", "forg", "fraud", "inauthentic", "malicious", "tamper")


def sign_in(page: Page, ctx: rc.Ctx, evidence_id: str) -> Locator:
    page.goto(f"{BASE}/app/cases/{ctx.case}/evidence/{evidence_id}")
    page.get_by_label("Username").fill(f"investigator-{ctx.suffix}")
    page.get_by_label("Password").fill(rc.PASSWORD)
    page.get_by_role("button", name="Continue").click()
    group = page.get_by_role("group", name=f"Preserved bytes of {ctx.object_id}")
    expect(group).to_be_visible(timeout=30000)
    return group


def text_visible(group: Locator, text: str, timeout: int = 20000) -> bool:
    try:
        expect(group.get_by_text(text, exact=True)).to_be_visible(timeout=timeout)
    except AssertionError:
        return False
    return True


def phase_download_and_states(ctx: rc.Ctx, evidence_id: str, shots: Path | None) -> None:
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            accept_downloads=True, viewport={"width": 1280, "height": 1500}
        )
        page = context.new_page()
        page.on(
            "console",
            lambda m: problems.append(f"{m.type}: {m.text}") if m.type == "error" else None,
        )
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        group = sign_in(page, ctx, evidence_id)
        ctx.check(
            "signed in through the real sign-in page; preserved object controls are shown", True
        )
        disclaimer = "Integrity verification is not an authenticity determination."
        ctx.check(
            "the authenticity disclaimer is always visible",
            group.get_by_text(disclaimer).is_visible(),
        )
        ctx.check("initial retrieval state is 'Ready'", text_visible(group, "Ready", 5000))
        if shots:
            page.screenshot(path=str(shots / "1-ready.png"), full_page=True)

        with page.expect_download() as download_info:
            group.get_by_role("button", name="Retrieve").click()
        download = download_info.value
        saved = Path(download.path()).read_bytes()
        ctx.check(
            "a real browser download has the deterministic name",
            download.suggested_filename == f"{ctx.object_id}.bin",
            download.suggested_filename,
        )
        ctx.check("the downloaded file is byte-exact", saved == ctx.small, f"{len(saved)} bytes")
        ctx.check("retrieval state shows 'Completed'", text_visible(group, "Completed", 5000))

        group.get_by_role("button", name="Verify").click()
        ctx.check("Verify shows 'Integrity match'", text_visible(group, "Integrity match"))
        if shots:
            page.screenshot(path=str(shots / "2-match.png"), full_page=True)

        ctx.stack.exec("sh", "-c", f"printf X >> {ctx.target}", user="veritas")
        group.get_by_role("button", name="Verify").click()
        ctx.check("Verify shows 'Integrity mismatch'", text_visible(group, "Integrity mismatch"))
        differing = group.get_by_text("Differs", exact=True).count()
        ctx.check(
            "the comparison marks all three differing values in text",
            differing == 3,
            str(differing),
        )
        wording = group.inner_text().lower()
        ctx.check("mismatch wording is neutral", not any(word in wording for word in ACCUSATORY))
        if shots:
            page.screenshot(path=str(shots / "3-mismatch.png"), full_page=True)
        with page.expect_download() as download_info:
            group.get_by_role("button", name="Retrieve").click()
        stored = Path(download_info.value.path()).read_bytes()
        ctx.check(
            "retrieval stays enabled after a mismatch and returns the stored bytes",
            stored == ctx.small + b"X",
        )

        ctx.stack.exec("rm", "-f", ctx.target, user="veritas")
        group.get_by_role("button", name="Verify").click()
        ctx.check(
            "a missing object shows 'Verification unavailable'",
            text_visible(group, "Verification unavailable"),
        )
        group.get_by_role("button", name="Retrieve").click()
        ctx.check(
            "a missing object shows 'Retrieval unavailable'",
            text_visible(group, "Retrieval unavailable"),
        )
        if shots:
            page.screenshot(path=str(shots / "4-unavailable.png"), full_page=True)

        ctx.restore()
        group.get_by_role("button", name="Verify").click()
        ctx.check(
            "restoring the bytes shows 'Integrity match' again",
            text_visible(group, "Integrity match"),
        )

        context.clear_cookies()  # the server no longer recognises this browser
        group.get_by_role("button", name="Verify").click()
        failure = group.get_by_role("alert").filter(has_text="Verification could not be completed")
        expect(failure).to_be_visible(timeout=20000)
        ctx.check(
            "an authentication failure is not shown as 'Verification unavailable'",
            group.get_by_text("Verification unavailable", exact=True).count() == 0,
        )
        browser.close()
    violations = [
        m for m in problems if "content security policy" in m.lower() or "refused to" in m.lower()
    ]
    other = [m for m in problems if m not in violations and "Failed to load resource" not in m]
    ctx.check(
        "no Content-Security-Policy violations during the real download",
        not violations,
        "; ".join(violations)[:200],
    )
    ctx.check("no unexpected console or page errors", not other, "; ".join(other)[:300])


def axe_violations(page: Page, group: Locator) -> list[str]:
    selector = '[role="group"][aria-label^="Preserved bytes of"]'
    group.scroll_into_view_if_needed()
    return list(page.evaluate(AXE_RUN, selector))


def phase_accessibility(ctx: rc.Ctx, evidence_id: str) -> None:
    """axe with colour contrast, in a context that bypasses CSP only so axe can be injected."""
    if not AXE_PATH.is_file():
        ctx.check("axe-core is installed (run npm ci in frontend/)", False, str(AXE_PATH))
        return
    ctx.restore()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context: BrowserContext = browser.new_context(
            bypass_csp=True, viewport={"width": 1280, "height": 1500}
        )
        page = context.new_page()
        group = sign_in(page, ctx, evidence_id)
        page.add_script_tag(path=str(AXE_PATH))
        # Instrument control: axe must flag a known low-contrast element, or a pass means nothing.
        page.evaluate(
            """() => {
              const p = document.createElement('p');
              p.id = 'axe-control';
              p.textContent = 'axe control text';
              p.style.cssText = 'color:#777;background:#888;';
              document.querySelector('[role="group"][aria-label^="Preserved bytes of"]').append(p);
            }"""
        )
        control = axe_violations(page, group)
        page.evaluate("() => document.getElementById('axe-control')?.remove()")
        ctx.check(
            "instrument control: axe flags a known low-contrast element",
            any(item.startswith("color-contrast") for item in control),
            "; ".join(control)[:120],
        )
        group.get_by_role("button", name="Verify").click()
        text_visible(group, "Integrity match")
        ctx.check(
            "axe (incl. colour contrast): no violations, Ready and Integrity match states",
            axe_violations(page, group) == [],
        )
        ctx.stack.exec("sh", "-c", f"printf X >> {ctx.target}", user="veritas")
        group.get_by_role("button", name="Verify").click()
        text_visible(group, "Integrity mismatch")
        found = axe_violations(page, group)
        ctx.check(
            "axe (incl. colour contrast): no violations, Integrity mismatch state",
            found == [],
            "; ".join(found),
        )
        ctx.stack.exec("rm", "-f", ctx.target, user="veritas")
        group.get_by_role("button", name="Verify").click()
        text_visible(group, "Verification unavailable")
        group.get_by_role("button", name="Retrieve").click()
        text_visible(group, "Retrieval unavailable")
        found = axe_violations(page, group)
        ctx.check(
            "axe (incl. colour contrast): no violations, unavailable states",
            found == [],
            "; ".join(found),
        )
        browser.close()


def main() -> int:
    shots = (
        Path(sys.argv[sys.argv.index("--screenshots") + 1]) if "--screenshots" in sys.argv else None
    )
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="veritas-v22-browser-"))
    stack = rc.Stack("veritas-v22-browser", workdir)
    report = rc.Report()
    ctx = rc.Ctx(stack, report)
    try:
        rc.phase_bring_up(ctx)
        rc.phase_provision(ctx)
        ctx.small = rc.PDF_PREFIX + b"Synthetic browser-check bytes " + bytes(range(256)) * 400
        before = rc.preserved_files(stack)
        evidence_id, ctx.object_id = ctx.preserve(ctx.small, len(ctx.small))
        created = rc.preserved_files(stack) - before
        ctx.target = f"{rc.EVIDENCE_DIR}/preserved/{next(iter(created))}"
        phase_download_and_states(ctx, evidence_id, shots)
        phase_accessibility(ctx, evidence_id)
    except Exception as exc:  # report it, then always tear down
        report.check("browser check completed", False, f"{type(exc).__name__}: {exc}")
    finally:
        stack.run("down", "-v", "--remove-orphans", check=False)
        shutil.rmtree(workdir, ignore_errors=True)
    failed = [name for name, passed, _ in report.rows if not passed]
    print(f"\n{len(report.rows) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
