"""Optional real-browser check of the V2.3 Examination workstation (not part of check.sh).

Starts the disposable ``veritas-v23-browser`` Compose project (same helpers and safety rules as
``v2_3_runtime_check.py``), signs in through the real sign-in page in Chromium, and drives the
Examination page against the production nginx Content-Security-Policy:

* Methods come from the backend; only PRESERVED objects are selectable; Start is explained.
* A real examination runs: queued -> completed, five Observations equal to an independent
  computation, exact provenance, and the Observation / Finding / Claim / Assessment legend.
* Tampered bytes show a FAILED run with its code; Retry creates a NEW run that completes.
* A running 96 MiB run can be cancelled from the UI and ends Cancelled.
* Mobile (390 px): no horizontal overflow, with a run open. Keyboard-only operation works.
* axe-core (WCAG 2.1 A/AA and best practice, colour contrast enabled; checked against a known
  positive first) reports no violations in the shipped states. jsdom cannot evaluate contrast,
  so this is the only place it is checked.

Playwright is deliberately NOT a project dependency. Use a throwaway venv:

    python3 -m venv /tmp/pw && /tmp/pw/bin/pip install playwright
    /tmp/pw/bin/python -m playwright install --with-deps chromium
    (cd frontend && npm ci)          # provides axe-core
    /tmp/pw/bin/python scripts/qa/v2_3_browser_check.py [--screenshots DIR]

Requires Docker and a free port 8080. Exit status is non-zero if any check fails.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v2_3_runtime_check as rc

BASE = f"http://{rc.base.HOST}:{rc.base.PORT}"
AXE_PATH = rc.ROOT / "frontend" / "node_modules" / "axe-core" / "axe.min.js"
AXE_RUN = """async () => (await axe.run(document.querySelector('main') ?? document.body, {
  runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']}}))
  .violations.map(v => v.id + ': ' + v.nodes.map(n => n.target.join(' ')).join(', '))"""
VERDICT = re.compile(r"\b(fake|forged|manipulated|suspicious|malicious|genuine)\b", re.IGNORECASE)
LONG_WAIT = 90_000


def examination_url(ctx: rc.Ctx) -> str:
    return f"{BASE}/app/cases/{ctx.case}/examination"


def sign_in(page: Page, ctx: rc.Ctx) -> None:
    page.goto(examination_url(ctx))
    page.get_by_label("Username").fill(f"investigator-{ctx.suffix}")
    page.get_by_label("Password").fill(rc.base.PASSWORD)
    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_role("heading", level=1, name="Examination")).to_be_visible(timeout=30_000)


def run_heading(page: Page, run_id: str | None = None):  # type: ignore[no-untyped-def]
    name = re.compile(run_id if run_id else r"ANL-\d+")
    return page.get_by_role("heading", level=3, name=name)


def newest_run_id(page: Page) -> str:
    """The run whose detail is open: read from its heading, which never lags the list refresh."""
    heading = run_heading(page)
    expect(heading).to_be_visible(timeout=30_000)
    match = re.search(r"ANL-\d+", heading.inner_text())
    if match is None:
        raise RuntimeError("the open run has no identifier in its heading")
    return match.group(0)


def overflows(page: Page) -> bool:
    return bool(page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth + 1"))


def axe_violations(page: Page) -> list[str]:
    return list(page.evaluate(AXE_RUN))


def shot(page: Page, shots: Path | None, name: str) -> None:
    if shots:
        page.screenshot(path=str(shots / name), full_page=True)


def phase_examination_ui(ctx: rc.Ctx, shots: Path | None) -> None:
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context: BrowserContext = browser.new_context(viewport={"width": 1280, "height": 1700})
        page = context.new_page()
        page.on(
            "console",
            lambda m: problems.append(f"{m.type}: {m.text}") if m.type == "error" else None,
        )
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))

        sign_in(page, ctx)
        ctx.check(
            "signed in through the real sign-in page; the Examination workstation is shown",
            True,
        )
        expect(page.get_by_role("radio", name="Binary Characteristics Examination")).to_be_visible(
            timeout=20_000
        )
        ctx.check(
            "Methods come from the backend with their exact version",
            page.get_by_text("core.binary_characteristics@1.0").is_visible(),
        )
        ctx.check(
            "the workstation is not shown as reserved",
            page.get_by_text("not available in this version").count() == 0,
        )
        preserved = page.get_by_role("radio", name=re.compile(ctx.medium_object))
        quarantined = page.get_by_role("radio", name=re.compile(ctx.quarantined_object))
        expect(preserved).to_be_visible(timeout=20_000)
        ctx.check("a PRESERVED object is selectable", preserved.is_enabled())
        ctx.check("a QUARANTINED object is not selectable", quarantined.is_disabled())
        row_texts = (
            ctx.medium_evidence,
            ctx.medium_object,
            "runtime-check.pdf",
            "PRESERVED",
            "SHA-256 and SHA-512 recorded at intake",
        )
        row = preserved.locator("xpath=ancestor::li[1]")
        hidden = [t for t in row_texts if not row.get_by_text(t).first.is_visible()]
        ctx.check(
            "the object row shows ids, filename, state, size and integrity availability",
            not hidden,
            f"not visible in the row: {hidden}",
        )
        start = page.get_by_role("button", name="Start examination")
        ctx.check(
            "Start is disabled until an object is chosen, and says why",
            start.is_disabled()
            and page.get_by_text("Select a preserved EvidenceObject.").is_visible(),
        )
        shot(page, shots, "1-initial.png")

        # Keyboard-only: reach the object radio and Start without a pointer.
        preserved.focus()
        page.keyboard.press("Space")
        ctx.check("the object can be chosen with the keyboard", preserved.is_checked())
        expect(start).to_be_enabled()
        start.focus()
        page.keyboard.press("Enter")
        heading = run_heading(page)
        expect(heading).to_be_visible(timeout=20_000)
        ctx.check(
            "after Start, focus moves to the new run",
            page.evaluate("document.activeElement?.tagName") == "H3",
        )
        first_run = newest_run_id(page)
        expect(heading).to_contain_text("Completed", timeout=LONG_WAIT)
        ctx.check("the run reached Completed in the UI (real backend state)", True, first_run)

        observations = page.get_by_role("heading", name="Observations (5)").locator("xpath=..")
        text = observations.inner_text()
        expected = rc.expected_statements(ctx.medium_hist)
        ctx.check(
            "the five Observations equal an independent computation over the stored bytes",
            all(statement in text for statement in expected if "entropy" not in statement)
            and re.search(r"entropy: [0-9]\.[0-9]{4} bits per byte", text) is not None,
            expected[0],
        )
        trace = page.get_by_role("region", name=f"Provenance of {first_run}")
        provenance = trace.inner_text()
        ctx.check(
            "provenance shows the exact Method and version, Evidence and EvidenceObject",
            "core.binary_characteristics@1.0" in provenance
            and ctx.medium_evidence in provenance
            and ctx.medium_object in provenance,
        )
        ctx.check(
            "a legend keeps Observation, Finding, Claim and Assessment apart",
            all(
                page.locator("dt", has_text=t).first.is_visible()
                for t in ("Observation", "Finding", "Claim", "Assessment")
            ),
        )
        ctx.check(
            "the page says it creates Observations only",
            page.get_by_text("This page creates Observations only").is_visible(),
        )
        body = page.locator("main").inner_text()
        fits = page.evaluate(
            """() => { const wrapper = document.querySelector('table').parentElement;
                      return wrapper.scrollWidth <= wrapper.clientWidth + 1; }"""
        )
        ctx.check("desktop: the runs table fits its panel without sideways scrolling", bool(fits))
        ctx.check(
            "no verdict vocabulary appears on the page",
            VERDICT.search(body) is None,
            (VERDICT.search(body) or [""])[0],
        )
        counts = ctx.case_counts()
        ctx.check(
            "no Finding, Claim or Assessment exists after the examination",
            counts["findings"] == counts["claims"] == counts["assessments"] == 0,
            str(counts),
        )
        shot(page, shots, "2-completed.png")

        # Failed run: tamper with the stored bytes; Retry after restoring them.
        ctx.stack.exec("sh", "-c", f"printf X >> {ctx.target}", user="veritas")
        preserved.check()
        start.click()
        failed_id = ""
        expect(page.get_by_text("integrity_mismatch")).to_be_visible(timeout=LONG_WAIT)
        failed_id = newest_run_id(page)
        ctx.check(
            "tampered bytes: the UI shows FAILED with its code",
            "Failed" in run_heading(page, failed_id).inner_text(),
            failed_id,
        )
        ctx.check(
            "a failed run shows no Observations",
            page.get_by_role("heading", name=re.compile(r"Observations \(")).count() == 0,
        )
        ctx.check(
            "the failure message is the sanitized sentence and offers a new run",
            page.get_by_text("integrity comparison only").is_visible()
            and page.get_by_role("button", name="Retry as a new run").is_visible(),
        )
        shot(page, shots, "3-failed.png")
        ctx.restore()
        page.get_by_role("button", name="Retry as a new run").click()
        expect(run_heading(page)).not_to_have_text(re.compile(failed_id), timeout=20_000)
        expect(
            page.get_by_role("heading", level=3, name=re.compile(r"ANL-\d+.*Completed"))
        ).to_be_visible(timeout=LONG_WAIT)
        retried = newest_run_id(page)
        ctx.check(
            "Retry created a NEW run that completed",
            retried != failed_id,
            f"{failed_id} -> {retried}",
        )
        table = page.get_by_role("table", name="Analysis runs, newest first")
        ctx.check(
            "the failed run stays in the list as history",
            table.get_by_role("button", name=failed_id).is_visible(),
        )

        # Cancel from the UI. First a QUEUED run, made deterministic by keeping the single worker
        # busy with another 96 MiB run started over the API.
        page.reload()
        large = page.get_by_role("radio", name=re.compile(ctx.large_object))
        expect(large).to_be_visible(timeout=20_000)
        status, busy = ctx.start("investigator", ctx.large_evidence, ctx.large_object)
        if status != 201:
            raise RuntimeError(
                f"could not queue the run that keeps the worker busy: {status} {busy}"
            )
        ctx.wait_for(busy["id"], {"running"})
        large.check()
        page.get_by_role("button", name="Start examination").click()
        cancel_queued = page.get_by_role("button", name="Cancel run")
        expect(cancel_queued).to_be_visible(timeout=20_000)
        queued_id = newest_run_id(page)
        ctx.check(
            "a second run waits Queued in the UI behind the busy worker",
            "Queued" in run_heading(page, queued_id).inner_text(),
            queued_id,
        )
        cancel_queued.click()
        expect(run_heading(page, queued_id)).to_contain_text("Cancelled", timeout=20_000)
        ctx.check("a queued run was cancelled from the UI immediately", True, queued_id)
        ctx.wait_for(busy["id"], {"completed", "failed", "cancelled"}, timeout=240)

        # Then a RUNNING run. The backend container is CPU-limited for this step so the run lasts
        # long enough to be seen as Running by the UI's 2 s refresh; the limit is lifted afterwards.
        limited = ctx.limit_backend_cpu("0.3")
        try:
            large.check()
            page.get_by_role("button", name="Start examination").click()
            cancel = page.get_by_role("button", name="Request cancellation")
            expect(cancel).to_be_visible(timeout=LONG_WAIT)
            cancelled_id = newest_run_id(page)
            ctx.check(
                "the UI showed the run as Running before cancelling it",
                "Running" in run_heading(page, cancelled_id).inner_text(),
                f"cpu-limited={limited}",
            )
            cancel.click()
            expect(page.get_by_text(re.compile("Cancellation was requested"))).to_be_visible(
                timeout=20_000
            )
            ctx.check(
                "cancelling a running run shows the request and the run is still Running",
                "Running" in run_heading(page, cancelled_id).inner_text(),
            )
            expect(run_heading(page, cancelled_id)).to_contain_text("Cancelled", timeout=LONG_WAIT)
        finally:
            ctx.limit_backend_cpu("0")
        ctx.check("the running run ended Cancelled in the UI", True, cancelled_id)
        ctx.check(
            "the cancelled run published no Observations",
            page.get_by_role("heading", name=re.compile(r"Observations \(")).count() == 0,
        )
        shot(page, shots, "4-cancelled.png")

        # Mobile: 390 px, a run open.
        page.set_viewport_size({"width": 390, "height": 844})
        page.reload()
        expect(page.get_by_role("heading", level=1, name="Examination")).to_be_visible(
            timeout=20_000
        )
        page.get_by_role("button", name=f"Open run {retried}").click()
        expect(page.get_by_role("heading", name="Observations (5)")).to_be_visible(timeout=20_000)
        ctx.check(
            "mobile (390 px): no horizontal overflow with a run open",
            not overflows(page),
        )
        ctx.check(
            "mobile (390 px): the objects and Start remain reachable",
            page.get_by_role("button", name="Start examination").is_visible(),
        )
        shot(page, shots, "5-mobile.png")
        page.set_viewport_size({"width": 1280, "height": 1700})

        # A browser whose session the server no longer recognises is asked to sign in again.
        context.clear_cookies()
        page.reload()
        try:
            expect(page.get_by_label("Username")).to_be_visible(timeout=20_000)
            asked = True
        except AssertionError:
            asked = False
        ctx.check("without a session the page asks for sign-in", asked)
        ctx.check(
            "and shows no run or Observation",
            page.get_by_text(re.compile(r"ANL-\d+")).count() == 0,
        )
        browser.close()
    violations = [
        m for m in problems if "content security policy" in m.lower() or "refused to" in m.lower()
    ]
    other = [m for m in problems if m not in violations and "Failed to load resource" not in m]
    ctx.check(
        "no Content-Security-Policy violations",
        not violations,
        "; ".join(violations)[:200],
    )
    ctx.check("no unexpected console or page errors", not other, "; ".join(other)[:300])


def phase_accessibility(ctx: rc.Ctx, shots: Path | None) -> None:
    """axe with colour contrast, in a context that bypasses CSP only so axe can be injected."""
    if not AXE_PATH.is_file():
        ctx.check("axe-core is installed (run npm ci in frontend/)", False, str(AXE_PATH))
        return
    ctx.restore()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(bypass_csp=True, viewport={"width": 1280, "height": 1700})
        page = context.new_page()
        sign_in(page, ctx)
        expect(page.get_by_role("radio", name=re.compile(ctx.medium_object))).to_be_visible(
            timeout=20_000
        )
        page.add_script_tag(path=str(AXE_PATH))
        page.evaluate(
            """() => {
              const p = document.createElement('p');
              p.id = 'axe-control';
              p.textContent = 'axe control text';
              p.style.cssText = 'color:#777;background:#888;';
              document.querySelector('main').append(p);
            }"""
        )
        control = axe_violations(page)
        page.evaluate("() => document.getElementById('axe-control')?.remove()")
        ctx.check(
            "instrument control: axe flags a known low-contrast element",
            any(item.startswith("color-contrast") for item in control),
            "; ".join(control)[:120],
        )
        found = axe_violations(page)
        ctx.check(
            "axe (incl. colour contrast): no violations, initial state",
            found == [],
            "; ".join(found),
        )
        page.get_by_role("radio", name=re.compile(ctx.medium_object)).check()
        found = axe_violations(page)
        ctx.check(
            "axe (incl. colour contrast): no violations, object selected",
            found == [],
            "; ".join(found),
        )
        page.get_by_role("button", name="Start examination").click()
        expect(
            page.get_by_role("heading", level=3, name=re.compile(r"ANL-\d+.*Completed"))
        ).to_be_visible(timeout=LONG_WAIT)
        found = axe_violations(page)
        ctx.check(
            "axe (incl. colour contrast): no violations, completed run with Observations",
            found == [],
            "; ".join(found),
        )
        page.get_by_text("Explain Binary Characteristics Examination").click()
        found = axe_violations(page)
        ctx.check(
            "axe (incl. colour contrast): no violations, Method explanation open",
            found == [],
            "; ".join(found),
        )
        ctx.stack.exec("sh", "-c", f"printf X >> {ctx.target}", user="veritas")
        page.get_by_role("button", name="Start examination").click()
        expect(page.get_by_text("integrity_mismatch")).to_be_visible(timeout=LONG_WAIT)
        found = axe_violations(page)
        ctx.check(
            "axe (incl. colour contrast): no violations, failed run",
            found == [],
            "; ".join(found),
        )
        page.set_viewport_size({"width": 390, "height": 844})
        found = axe_violations(page)
        ctx.check(
            "axe (incl. colour contrast): no violations at 390 px",
            found == [],
            "; ".join(found),
        )
        shot(page, shots, "6-axe-mobile.png")
        browser.close()


def main() -> int:
    shots = (
        Path(sys.argv[sys.argv.index("--screenshots") + 1]) if "--screenshots" in sys.argv else None
    )
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="veritas-v23-browser-"))
    stack = rc.Stack23("veritas-v23-browser", workdir)
    report = rc.base.Report()
    ctx = rc.Ctx(stack, report)
    try:
        rc.phase_bring_up(ctx)
        rc.phase_provision(ctx)
        ctx.small = rc.base.PDF_PREFIX + b"Synthetic browser-check bytes " + bytes(range(256)) * 400
        ctx.medium_hist = rc.histogram_of(ctx.small)
        ctx.medium_evidence, ctx.medium_object = ctx.upload_bytes(ctx.small)
        ctx.target = ctx.storage_path(ctx.medium_object)
        _, ctx.quarantined_object = ctx.upload_bytes(
            rc.base.PDF_PREFIX + b"still in quarantine", finalize=False
        )
        large = rc.HistogramBody(rc.LARGE_BYTES)
        ctx.large_evidence, ctx.large_object = ctx.upload_object(large, rc.LARGE_BYTES)
        phase_examination_ui(ctx, shots)
        phase_accessibility(ctx, shots)
    except Exception as exc:  # report it, then always tear down
        report.check("browser check completed", False, f"{type(exc).__name__}: {exc}")
    finally:
        stack.run("down", "-v", "--remove-orphans", check=False)
        shutil.rmtree(workdir, ignore_errors=True)
    failed = [name for name, passed, _ in report.rows if not passed]
    print(f"\n{len(report.rows) - len(failed)} passed, {len(failed)} failed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
