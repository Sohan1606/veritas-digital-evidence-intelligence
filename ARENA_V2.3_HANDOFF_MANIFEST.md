# ARENA V2.3 handoff manifest — VERITAS Examination Core

Everything stated here was executed or read from source; limits and decisions that need a human are
in sections 12–14. Nothing is claimed that was not run.

## 1. Identification

| Item | Value |
| --- | --- |
| Baseline | `v2.2.0` = `9c690c843287bce6794bf728cd7325f3828a5015` (merge of PR #2) |
| Branch | `v2.3-examination-core` — local only; not pushed; `main` and the starting tag untouched |
| Final commit | `git rev-parse v2.3-examination-core` (reported in the delivery message; a file cannot contain the hash of the commit that contains it) |
| Migration revision | `0004` (`0004_examination_core`), down-revision `0003` |
| Method registry | `backend/app/examination/registry.py` (`METHOD_REGISTRY`) |
| First executable Method | `backend/app/examination/methods/binary_characteristics.py` — `core.binary_characteristics@1.0` |

About the baseline: the remote `v2.2.0` tree equals the V2.2 work delivered earlier except for two
test files (Windows/mypy portability: `getattr` guards for `os.mkfifo` and `os.O_NONBLOCK`, and a
skip of the atomic-replacement test on native Windows). V2.3 starts from `9c690c8`, so those changes
are part of the baseline.

## 2. What V2.3 is

`PRESERVED EvidenceObject → authorized examination → versioned Method → real byte processing →
Analysis Run → deterministic Observations → audit`, and nothing beyond it. Full description:
[docs/examination-core-v2.3.md](docs/examination-core-v2.3.md). In short: one code-owned Method;
`AnalysisRun` is the only execution record and names the exact EvidenceObject (database-enforced);
runs are idempotent (database constraint), durable (the table is the queue; an in-process worker
claims with compare-and-set, heartbeats, fences a stale worker, recovers abandoned runs, publishes
atomically), cancellable and retryable (retry = new run). Observations are the only output; no
Finding, Claim or Assessment is created; no AI; evidence is never modified.

## 3. Files

**Created (31):**

- Backend: `app/api/examination.py`, `app/domain/lifecycle.py`, `app/examination/{__init__,contracts,coordinator,registry,runner,supervisor}.py`, `app/examination/methods/{__init__,binary_characteristics}.py`, `app/services/examination.py`, `migrations/versions/0004_examination_core.py`.
- Backend tests: `tests/examination_support.py`, `tests/test_examination_{methods,runner,lifecycle,api,authorization,coordinator,recovery,security,migration}.py`.
- Frontend: `features/examination/{MethodPanel,ObjectPicker,RunsPanel,examinationModel}.tsx|ts`, `test/examination.test.tsx`.
- QA and docs: `scripts/qa/v2_3_runtime_check.py`, `scripts/qa/v2_3_browser_check.py`, `docs/examination-core-v2.3.md`, `ARENA_V2.3_HANDOFF_MANIFEST.md`.

**Modified (33):** `README.md`, `.env.example`, `docs/architecture.md`, `docs/threat-model-v2.md`;
backend `api/{cases,system}.py`, `core/{config,errors}.py`, `domain/{enums,models,roles}.py`, `main.py`,
`schemas.py`, `services/{evidence_access,evidence_hashing,queries,records}.py`; backend tests
`conftest.py`, `test_architecture.py`, `test_cases_api.py`, `test_system.py`; frontend
`api/{types,useResource}.ts`, `design-system/{primitives.tsx,semantics.ts}` (the demonstration banner no longer reads as a statement about the whole product), `features/{cases/CaseOverview,evidence/EvidenceIntakeControls,examination/ExaminationPage,findings/FindingsPage}.tsx`,
`showcase/EvidenceSequence.tsx`, `test/{app.test,fixtures}.tsx`; `tests/test_repository.py`.
Historical V1/V2/V2.1/V2.2 artifacts were not edited except where V2.3 required it (listed above):
the V2.2 `evidence_access.verify_preserved_object` now calls the shared comparison
`evidence_hashing.matches_recorded_integrity` (behaviour unchanged; all V2.2 tests pass).

## 4. Database migration `0004`

Append-only; earlier migrations are unchanged (a test pins their hashes with line endings normalized).
Adds to `analysis_runs`: `evidence_object_id` (NOT NULL), `idempotency_key`, `request_fingerprint`
(NOT NULL), `cancel_requested_at`, `last_heartbeat_at`, `failure_code`, `failure_message`; a composite
foreign key `(evidence_object_id, evidence_id, case_id) → evidence_objects(id, evidence_id, case_id)`
`ON DELETE RESTRICT` (backed by the new unique index `uq_evidence_objects_provenance`); unique
`(case_id, created_by, idempotency_key)`; four CHECK constraints (state set, lifecycle/timestamp
consistency, failure consistency, fingerprint length); index `(state, created_at)`; on PostgreSQL a
guard trigger (no delete, request columns immutable, finished rows immutable, lifecycle only).
No existing row, column or constraint of any table was removed or weakened.

- **Legacy rows:** no V1–V2.2 code path ever created an Analysis Run, so the table is empty in every
  supported deployment. The upgrade **refuses** (clear error, schema untouched) if it finds rows rather
  than guess an EvidenceObject.
- **Downgrade** drops the V2.3 columns and is destructive for V2.3 provenance; the repository only
  downgrades disposable databases. It works with runs present (tested on PostgreSQL).
- Verified: SQLite and PostgreSQL up/down/up; the literal CI sequence (`alembic upgrade head`,
  `python -m app.seed` twice, `alembic downgrade base`) on PostgreSQL; schema/model drift test.
- SQLite (tests only) has the ORM guard and CHECKs but no trigger; tests say so and skip the trigger
  tests there.

## 5. API

Added: `GET …/examination/methods`, `POST …/analysis-runs`, `GET …/analysis-runs/{run_id}`,
`POST …/analysis-runs/{run_id}/cancel`, `POST …/analysis-runs/{run_id}/retry`. Changed: `GET
…/analysis-runs` (now carries the exact EvidenceObject and the V2.3 run fields; moved to
`api/examination.py` with the same path and operation). Statuses: create/retry `201` (new) or `200`
(idempotent replay); cancel `200` (queued, immediate) or `202` (running, requested). Error codes are in
the V2.3 notes (`method_unavailable`, `method_inapplicable`, `evidence_object_not_preserved`,
`idempotency_conflict`, `invalid_lifecycle_transition`, `invalid_method_parameters`,
`evidence_storage_unavailable`, …), all in the existing error envelope. No route accepts a path.

## 6. The Method and the registry

Code-owned, no database policy. Contract pinned by `MethodDefinition.digest()` plus golden outputs.
`core.binary_characteristics@1.0`: one pass, 64 KiB chunks, 256-bin histogram; byte count, Shannon
entropy (`decimal`, 50 digits, half-even, 4 places, no floating point), printable-ASCII ratio
(`0x20`–`0x7E`), NUL ratio, distinct byte values; empty object → "not defined", no invented value; five
Observations in the wording of the brief. Verified against an independent floating-point oracle,
exact half-even cross-checks, chunk-boundary independence, and independence from the global decimal
context (a test of that found and fixed a real defect: `Decimal.quantize` used the thread's context).

## 7. Execution, security and authorization changes

- **Authorization:** new capability `examination:execute` for INVESTIGATOR and RESEARCHER only (one map,
  `domain/roles.py`); REVIEWER and AUDITOR read only; CUSTODIAN and ADMINISTRATOR neither; the anonymous
  demonstration viewer can read (empty) lists but never execute; route guard = authenticated, not a
  demonstration Case, Case authorization first, CSRF. Unauthorized and ineligible requests never open
  storage (tested with a recording storage).
- **Integrity gate (decision, §13):** the reader compares the bytes read with the recorded byte count and
  SHA-256/SHA-512 and fails the run (`integrity_mismatch`) on any difference, so Observations exist only
  for bytes that still equal the PRESERVED object.
- **No leakage:** audit carries identifiers and state only; failure text is one fixed sentence per code;
  logs carry only exception types; responses never carry paths, keys, fingerprints or idempotency keys.
- **No side channels:** the examination package imports no process/network/filesystem module and never
  evaluates or opens (AST test); the Method still runs with sockets and process creation made impossible;
  nothing is written under the evidence root and no copy is made.
- **Audit:** the six actions of the brief plus two (`cancel_requested`, `recovered`, §13); existing
  `AuditEvent` only.

## 8. Frontend

`features/examination/` replaces the reserved page: Explain (Methods from the backend, expandable
explanation), Examine (EvidenceObject picker — Evidence ID, object ID, filename, state, size, integrity
availability; only PRESERVED selectable — and Start with the exact reason it is unavailable), Trace (runs
newest first; exact Method/version, Evidence, EvidenceObject, parameters, requester, timestamps; real
polled state; Observations; Cancel and Retry), Verify (pointer to Evidence-page verification), Review
(legend separating Observation, Finding, Claim, Assessment). Availability from `/api/v1/system`,
permissions from the session; idempotency key kept per user intent (reused only while an outcome is
unknown). `useResource` gained in-place polling (same single GET owner). Reuses `StateView`,
`DataTable`, `semantics.ts` (`RUN_STATE`, `OBJECT_STATE_TONE`, now also used by the Evidence card).
Stale wording changed in `CaseOverview`, `FindingsPage`, the showcase label, the capability note and
the docs.

## 9. Tests run (all executed on this branch)

| Gate | Result |
| --- | --- |
| Backend full suite, SQLite | **591 passed, 15 skipped** (the skips are PostgreSQL-only tests, listed below); the V2.2.0 baseline in this environment was 318 passed, 2 skipped (V2.2.0 baseline in this environment: 318 passed, 2 skipped) |
| Backend full suite, PostgreSQL 17 | **606 passed, 0 skipped** on PostgreSQL 17.11 (the V2.2 tree had 320 passed on PostgreSQL) |
| V2.3 backend tests only, PostgreSQL | all **286** V2.3 tests (`tests/test_examination_*.py`: methods 47, runner 20, lifecycle 59, API 42, authorization 51, coordinator 31, recovery 15, security 12, migration 9) pass on PostgreSQL, 0 skipped; they are included in the 606 |
| Ruff format/check, mypy strict (Linux) | `ruff format --check .` (84 files), `ruff check .`, `mypy` strict on 79 files — all clean; the repository-root `tests` and `scripts/qa` are clean under `--config backend/pyproject.toml` |
| mypy strict `--platform win32` | `mypy --platform win32 app tests`: no issues in 79 files (a static check only; no code was executed on Windows) |
| compileall | OK (`app`, `tests`, repository-root `tests`, `scripts/qa`, `migrations`) |
| Frontend typecheck / lint / test / build / audit | typecheck clean; lint clean; **160 tests in 10 files** pass (119 in 9 before); production build OK; `npm audit --omit=dev --audit-level=high`: 0 vulnerabilities |
| Repository hygiene (`tests/`) | **18 passed** (`pytest -q tests` from the repository root; 16 before) — includes the new guards that the frontend defines no Method and that documentation cites only tests that exist |
| `scripts/check.sh` | **exit 0** on commit `ff96f3a` (one run: ruff format/check, mypy strict, 591 passed / 15 skipped, frontend typecheck/lint/160 tests/build, repository lint, 18 hygiene tests); later commits change only this manifest |
| Fresh extraction of the ZIP (new venv, `npm ci`) | **exit 0** from a fresh extraction of the archive built from commit `3f2796e` (new venv, `npm ci` on Node 24.21.0, then `scripts/check.sh`): backend 591 passed / 15 skipped, frontend 160 tests, hygiene 18 passed; the delivered archive differs from that one only in this manifest (checked with `diff -r`) |

Skipped tests (SQLite run only), each with its real reason: 2 pre-existing PostgreSQL-trigger tests
(`test_domain.py`, `test_evidence_intake_api.py`: "database-level trigger exists on PostgreSQL only"),
10 `test_examination_lifecycle.py` trigger tests ("the analysis_runs guard trigger exists on PostgreSQL
only (SQLite: ORM guard)") and 3 `test_examination_migration.py` PostgreSQL checks ("need
VERITAS_TEST_DATABASE_URL"). None is skipped on PostgreSQL.

**Mutation pass.** 17 mutations of one safety-critical production line each were applied and the
targeted tests run (all on SQLite temporary databases): fencing removed from the completion commit;
completion no longer refusing a pending cancellation; publication no longer atomic; the integrity gate
no longer failing a mismatch; recovery treating every running run as stale; the create route no longer
requiring `examination:execute`; idempotency with the replay lookup removed, with the unique-violation
handler removed, and with both removed; a claim that is not a compare-and-set; the heartbeat ignoring a
cancellation request; a completed run becoming retryable; the service no longer refusing a demonstration
Case; a finished run allowed to leave its final state; the output contract off; an unbounded read; a
failure message carrying exception text. **All 17 were detected**, and each source file was restored
byte-identical (verified against git). One first appeared to survive — removing only the replay lookup —
because the mutation was imprecise: the database's unique constraint and its violation handler still
decide, which is the intended defence in depth. Splitting it into three precise mutations showed each is
caught by its own test.

**Defects found by the verification and fixed, each with a regression test confirmed to fail without the
fix:** `Decimal.quantize` used the thread's global decimal context, so entropy was not independent of it
(found by the context-independence test); a cancelled run's response showed a stale state because a
compare-and-set bypassed the ORM identity map (found by the API tests); the run list did not refresh
after Start when no listed run was active (found independently by the jsdom test after review and by the
real-browser run); the opened Method explanation skipped a heading level (found by axe in the real
browser; reproduced in jsdom before the fix). A layout flaw found by reviewing screenshots (the run table
overflowing its half-width panel) was fixed and is now guarded by a browser check. One tooling error of
mine was also caught and reverted: `ruff format` run without `--config backend/pyproject.toml` applies an
88-column limit and had reformatted three historical V2.2 scripts; they were restored byte-identical and
the repository gate command (which passes `--config`) is what this branch is formatted with.

## 10. Runtime checks (disposable Compose projects `veritas-v23-*`)

| Gate | Result |
| --- | --- |
| `scripts/qa/v2_3_runtime_check.py` (PostgreSQL, nginx, real HTTP) | **113 passed, 0 failed** (official run, project `veritas-v23-verify`, torn down; protected-volume check passed) |
| `scripts/qa/v2_3_browser_check.py` (Chromium, production CSP, axe with colour contrast) | **58 passed, 0 failed** (Chromium headless shell via Playwright; screenshots of the shipped states reviewed) |
| V2.2 runtime gate re-run against V2.3 (`scripts/qa/v2_2_runtime_check.py`) | **52 passed, 0 failed** — the V2.2 script, unmodified, against V2.3 code (its own V2.2 result was also 52/52) |
| V2.2 browser gate re-run against V2.3 (`scripts/qa/v2_2_browser_check.py`) | **32 passed, 0 failed** — the V2.2 script, unmodified, against V2.3 code (its own V2.2 result was also 32/32) |

The runtime gate covers: the full acceptance flow (queued → running → completed; five Observations
equal to an independent computation; exact provenance; audit created/started/completed; no Finding,
Claim or Assessment; EvidenceObject, custody and stored bytes unchanged; V2.2 verification still
MATCH, retrieval still exact); roles over HTTP, CSRF, demonstration Case, unknown Method, invalid
parameters, QUARANTINED object, guessed ids; idempotency incl. ten concurrent identical requests; a
96 MiB object (RUNNING seen over HTTP, heartbeat advancing, measured backend heap growth, Observations
equal to an independent computation); cancellation of a queued and of a running run; tampered bytes →
FAILED `integrity_mismatch`, retry → new run, old run unchanged; a missing and an unreadable object →
503; truncation while a run executes → FAILED; a database outage with recovery and exactly one
completion; graceful restart with a running and a queued run; `SIGKILL` of the backend with a running
run; container recreation with persisted history; leak scans of audit, runs and logs.

Two test techniques worth knowing: the UI check widens the window in which a run is visibly `Running` by temporarily limiting the backend container's CPU (`docker update --cpus 0.3`, lifted afterwards), because the UI refreshes every 2 s and a 96 MiB examination otherwise finishes in about 4 s; and axe-core is injected in a separate browser context that bypasses the page's Content-Security-Policy (as the V2.2 gate does), while the behavioural checks run under the production CSP with no violations.

Protected volumes `veritas_pgdata` and `veritas-v2_pgdata`: **neither exists on the host used for
verification** (a fresh Docker data root), so the check demonstrates only that no such volume was
created, removed or modified; the name guard (`veritas-v23-*` only) is the protection on a host where
they exist. Nothing was run against them.

## 11. Failure simulation (real, not inspected)

Real file edits in the evidence volume (append, move, `chmod 000`, `truncate` during a run), real
`docker stop`/`start` of PostgreSQL, real `docker restart` and `docker kill` of the backend, and in the
pytest suite a recording storage that injects read errors and early EOF, mid-run cancellation, a
completion race forced by committing a cancel just before the completion commit, forced failure of the
publication transaction, and stalled-worker fencing with a controllable clock.

## 12. Known limitations

One in-process worker thread per backend process (pure Python, about 29 MiB/s measured); no per-user
queue quota or rate limit; a run that crashes its worker every time is retried each time (no attempt
counter); host clock skew shifts stale detection; recovery after a hard crash waits up to the stale
threshold (30 s by default); the browser buffers nothing for examination (no download), but retrieval
(V2.2) still does; no examination Method other than the byte-level one; no timeline, review, report;
`app.__version__` is still `0.1.0` (pre-existing); the examination of an object near the 1 GiB limit
takes minutes. Platform: everything ran on Linux (Debian 13, Python 3.13, Node 24, PostgreSQL 17,
Docker 26). **Windows was not exercised.** mypy passes for `--platform win32`; the new code and tests
avoid POSIX-only attributes (a test that patches `socket` does so only around the worker so the test
client's event loops are untouched); `scripts/check.sh` is a bash script (pre-existing), so on native
Windows run the commands in §15 individually. Not exercised: Docker Desktop, macOS, Firefox/WebKit,
multi-worker uvicorn, objects larger than 96 MiB.

## 13. Decisions that need confirmation (deviations from, or additions to, the brief)

1. **Two audit actions beyond the six named** — `examination.run.cancel_requested` (a user's request to
   stop a running run) and `examination.run.recovered` (a RUNNING run returned to the queue). Without
   them those state changes would be unaudited. Say if you want them folded into the six.
2. **Integrity gate** — an examination fails (`integrity_mismatch`) if the bytes read no longer equal the
   recorded intake values. The brief did not ask for it; it makes "only PRESERVED bytes are examined"
   hold at run time. It costs two hashes per run (included in the 29 MiB/s figure).
3. **`uq_evidence_objects_provenance` and the composite foreign key** touch `evidence_objects` by adding
   one unique index (no data or rule of the object changes) to give database-enforced exact provenance.
4. **`RUNNING → QUEUED`** is a permitted transition (stale recovery and graceful release), though not in
   the brief's list of allowed transitions; it is the only way "stale RUNNING → QUEUED" can be recorded.
5. **Retry** is allowed only from FAILED or CANCELLED (not from COMPLETED); anyone holding
   `examination:execute` in the Case may cancel or retry, not only the creator.
6. **Methods list for a demonstration Case** is readable (static metadata, `examination:read`); execution
   is not.
7. **`ObjectPicker`** is a compact selection list, not a reuse of the heavy `EvidenceObjectCard`
   (upload/finalize/custody); it reuses the same intake endpoint and the one object-state tone mapping.
8. **Migration refuses legacy rows** instead of backfilling.

## 14. Not claimed

Authenticity, origin, manipulation, malware status, truthfulness, legal admissibility, forensic
certification, production readiness, external security assessment, or Windows/macOS behaviour.

## 15. Exact commands

```bash
# setup (once)
python3 -m venv backend/.venv && backend/.venv/bin/pip install -e "backend[dev]"
(cd frontend && npm ci)                                   # Node >= 22.22 (24 used)

# static + tests (backend; SQLite)
cd backend && .venv/bin/python -m ruff format --check . && .venv/bin/python -m ruff check . \
  && .venv/bin/python -m mypy app tests && .venv/bin/python -m pytest -q
# PostgreSQL (real triggers and concurrency):
VERITAS_TEST_DATABASE_URL=postgresql+psycopg://veritas:ci-only-password@127.0.0.1:5432/veritas_test \
  .venv/bin/python -m pytest -q
# migrations on PostgreSQL (CI sequence)
VERITAS_ENVIRONMENT=development VERITAS_DATABASE_URL=<url> alembic upgrade head \
  && python -m app.seed && python -m app.seed && alembic downgrade base

# frontend
cd frontend && npm run typecheck && npm run lint && npm test && npm run build

# repository hygiene (from the repository root; note --config, which sets the 100-column limit)
backend/.venv/bin/python -m ruff format --check --config backend/pyproject.toml tests scripts/qa
backend/.venv/bin/python -m ruff check --config backend/pyproject.toml tests scripts/qa
backend/.venv/bin/python -m pytest -q tests

# everything in one go (bash)
bash scripts/check.sh

# runtime (Docker; a new disposable project; port 8080 must be free)
python3 scripts/qa/v2_3_runtime_check.py
/tmp/pw/bin/python scripts/qa/v2_3_browser_check.py --screenshots ./shots   # see the script header for Playwright
```

## 16. Artifact exclusions and the ZIP

`VERITAS-V2.3-EXAMINATION-CORE.zip` is `git archive` of the final commit with the repository files at
the root (no wrapper directory). It therefore excludes `.git`, every untracked or ignored file
(`.env`, `node_modules`, `backend/.venv`, `__pycache__`, caches, `dist`, `build`, logs, temporary
evidence, databases, volumes) and contains no secret; the set equals `git ls-files`, which is checked
(and compared with a fresh extraction) before delivery. The SHA-256 of the ZIP is in the delivery
message and in the sidecar file `VERITAS-V2.3-EXAMINATION-CORE.zip.sha256` beside it (a file inside the
ZIP cannot contain the ZIP's own hash).
