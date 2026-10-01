# VERITAS V2.2 — Evidence Retrieval + Integrity Verification — ARENA handoff

> **INTEGRITY MATCH IS NOT AUTHENTICITY PROOF.**

Baseline: tag `v2.1.0` = `ebbbc7b7746235585e5f03831112fbe8725d507c`.
Branch: `v2.2-evidence-retrieval-verification` (created from that tag). Nothing was pushed, merged or
rewritten; `main` and the starting branch are untouched.
Commits on the branch (in order):

- `4219c2b V2.2 backend: authorized retrieval and independent integrity verification`
- `2bbcc98 V2.2 frontend: retrieve and verify controls on preserved EvidenceObjects`
- `738c4bc V2.2 tests: verification authorization matrix, method routing, OpenAPI contract`
- `11a5485 V2.2 frontend: do not claim a verification that did not happen`
- `566eaef V2.2 infra: nginx streams retrieval without spooling; runtime and browser gates`
- `1f040a7 V2.2 documentation`

Canonical description: [docs/evidence-retrieval-verification-v2.2.md](docs/evidence-retrieval-verification-v2.2.md).

## 1. Summary

For a **PRESERVED EvidenceObject only**, V2.2 adds authorized retrieval of the exact bytes and an
independent integrity verification, both on the existing `evidence:read` capability, the existing
`EvidenceStorage`, hashing primitives, `AuditEvent`, error envelope and Case/Evidence resolution.
No migration, no new table, no new capability, no verification store. Verification is
observational: its only SQL writes are one AuditEvent and that event's identifier allocation.
The frontend extends the existing EvidenceObject card.

Pre-existing problems found while doing this (all fixed; items 1-4 each have a check that fails on the old code):

1. **V2.1 architecture guards were passing vacuously.** Under the installed FastAPI (0.142) included
   routers are `_IncludedRouter` objects, so `app.routes` held no `APIRoute`; the route allow-list,
   kebab-case, case-scope and forbidden-term guards iterated nothing. They now read the OpenAPI
   document, a non-vacuity test was added, and injecting a `download-all` route or a `DELETE` makes
   the allow-list fail.
2. **V2.1 `LocalEvidenceStorage._open_existing`** could be blocked indefinitely by a FIFO placed
   where a preserved object should be (no `O_NONBLOCK`) and leaked a descriptor if `fstat`/`fdopen`
   failed. Three tests fail against the V2.1 implementation.
3. **V2.1 nginx would spool retrieval responses to the web container's disk.** The content location
   (shared with the upload `PUT`) had default response buffering. With a slow client, nginx had
   already written ~95 MiB of a 96 MiB object to its own (unlinked) temp file; V2.2 writes 0 bytes.
4. **The frontend API client's "relative URL" guard accepted protocol-relative `//host/…`** (and
   `/\host`, `/<TAB>/host`). One shared, stricter guard now covers every client function. Call sites
   always begin `/api/v1/`, so this was defense in depth, not an exploitable path.
5. Docs: a V2.1 threat-model row cited a test that does not exist, and two sentences were stale.

## 2. Files created (14, including this manifest)

- `backend/app/services/evidence_access.py`
- `backend/tests/evidence_support.py`
- `backend/tests/test_evidence_access_failures.py`
- `backend/tests/test_evidence_access_storage.py`
- `backend/tests/test_evidence_live_server.py`
- `backend/tests/test_evidence_retrieval_api.py`
- `backend/tests/test_evidence_retrieval_streaming.py`
- `backend/tests/test_evidence_verification_api.py`
- `docs/evidence-retrieval-verification-v2.2.md`
- `frontend/src/features/evidence/EvidenceObjectAccess.tsx`
- `frontend/src/test/evidence-access.test.tsx`
- `scripts/qa/v2_2_browser_check.py`
- `scripts/qa/v2_2_runtime_check.py`
- `ARENA_V2.2_HANDOFF_MANIFEST.md` (this file)

## 3. Files modified (23)

- `README.md`
- `backend/app/api/evidence.py`
- `backend/app/api/system.py`
- `backend/app/domain/enums.py`
- `backend/app/schemas.py`
- `backend/app/services/evidence_signatures.py`
- `backend/app/services/evidence_storage.py`
- `backend/app/services/queries.py`
- `backend/tests/test_architecture.py`
- `backend/tests/test_evidence_intake_api.py`
- `backend/tests/test_system.py`
- `docker/nginx.conf`
- `docs/architecture.md`
- `docs/evidence-intake-v2.1.md`
- `docs/threat-model-v2.md`
- `frontend/src/api/client.ts`
- `frontend/src/api/types.ts`
- `frontend/src/design-system/semantics.ts`
- `frontend/src/features/evidence/EvidenceIntakeControls.tsx`
- `frontend/src/features/evidence/EvidencePage.tsx`
- `frontend/src/test/api.test.ts`
- `frontend/src/test/evidence.test.tsx`
- `frontend/src/test/fixtures.tsx`

Historical V1/V2/V2.1 files were changed only for V2.2 reasons: the now-false `GET …/content` 405
assertion and the route allow-list guard (required), the storage open-path hardening, the shared
resolver in `queries.py`, the shared client path guard, the backend capability statement and the stale
docs. The guarded `docs/v2.1-evidence-intake-spec.md` is unchanged.

## 4. Endpoints

| Method and path | Result |
| --- | --- |
| `GET /api/v1/cases/{case_id}/evidence/{evidence_id}/objects/{object_id}/content` | `200` binary (bounded 64 KiB chunks); `401`, `404`, `409`, `422`, `503` |
| `POST /api/v1/cases/{case_id}/evidence/{evidence_id}/objects/{object_id}/verify` | `200` JSON `MATCH`/`MISMATCH`/`UNAVAILABLE`; `401`, `403` (CSRF), `404`, `409`, `422`, `500` |

The retrieval route shares its path with the V2.1 `PUT` upload. `HEAD` and other methods are `405`;
`Range` is ignored. Errors use the existing `{"error": {"code", "message", "request_id"}}` envelope.

## 5. Authorization rules

Authentication → `evidence:read` for a non-demonstration Case (route dependency, before any lookup) →
Evidence within the Case → EvidenceObject within Case and Evidence → `PRESERVED`. Investigator,
Reviewer, Custodian, Researcher: allowed. Auditor: allowed within its scope (organization-wide and
Case-scoped both tested). Administrator, anonymous demo viewer, any demonstration Case, other
Case/Organization, guessed or mismatched identifiers: masked `404`. Unauthenticated, disabled,
expired or revoked: `401`. `QUARANTINED`/`REJECTED`: `409`. `POST` needs the session-bound CSRF
token. Authorization failures are never `UNAVAILABLE`.

## 6. Storage design

Bytes come only from `EvidenceStorage.open_preserved_object(key)` with the database-held opaque key;
no quarantine or alternate-location fallback. Reads are at most 64 KiB and run in the thread pool;
nothing buffers an object or writes a temporary copy. The first chunk is read before headers so early
failures are a clean `503`. `Content-Length` is the size at open; reads are capped at it and a file
that shrinks aborts the response. A `StreamingResponse` subclass closes the handle however the
response ends. The read transaction is ended before storage I/O, so no database connection is held
while bytes stream. Symlinked roots/directories/files, non-directory roots, path-like keys and
non-regular files are refused. nginx: `proxy_buffering off`, `proxy_max_temp_file_size 0`.

## 7. Verification semantics

`MATCH` = complete read, all three values equal. `MISMATCH` = complete read, at least one differs.
`UNAVAILABLE` = bytes could not be read; nothing compared. Storage failure is never `MISMATCH` (a
comparison is made only from a complete successful read). All three are `200` results with one audit
event each; `byte_size`/`sha256`/`sha512` in the body are the recorded values and `MISMATCH` adds
`expected_*`/`computed_*`. Never rewrites any value, state or custody; retrieval stays enabled after a
`MISMATCH`.

**Decision for your review:** `UNAVAILABLE` is a `200` result (and audited), not a `503`. Reason and
the (small) change needed to reverse it are in the V2.2 document. Retrieval's storage failure
is the existing `503 evidence_storage_unavailable`.

## 8. Audit events

`evidence.object.retrieved` (once, committed **before the final chunk is released**; `details`:
`evidence_id`, `byte_size` delivered, `media_type`, `state`) and
`evidence.object.integrity_verified` (once per verification; `details`: `evidence_id`, `result`,
`expected_*`, `computed_*`, null when `UNAVAILABLE`). Actor, Case, Organization and request id are
recorded; no bytes, paths, keys or credentials. A failed audit write means no result / no complete copy.

## 9. Database / migration result

None added. Alembic head is `0003`. On PostgreSQL 17.11: upgrade from empty, seed twice (second is a
no-op), downgrade to base and re-upgrade all pass; no file under `backend/migrations/` changed.

## 10. Test results (final, on the committed tree)

| Gate | Result |
| --- | --- |
| Baseline V2.1.0, SQLite | 162 passed, 2 skipped |
| Baseline V2.1.0, PostgreSQL 17.11 | 164 passed, 0 skipped |
| **V2.2 backend, SQLite** | **318 passed, 2 skipped** (+156) |
| **V2.2 backend, PostgreSQL 17.11** | **320 passed, 0 skipped** |
| ruff format / ruff check | 62 files / clean |
| mypy strict (`app tests`) | 58 files, no issues |
| `compileall` | OK |
| Frontend typecheck / lint | clean |
| **Frontend Vitest** | **9 files, 119 tests** (was 8 / 72) |
| Frontend production build | OK (also built inside the project's Node 24 container) |
| Repository lint / hygiene tests | clean / 16 passed |
| `bash scripts/check.sh` | exit 0 |
| `npm audit --omit=dev --audit-level=high` | 0 vulnerabilities |
| `git diff --check` | clean |

New backend modules: retrieval API 41, retrieval streaming 17, verification API 52, storage 31,
failure simulation 7, live server 7; plus 1 new architecture guard.

## 11. Security test results

Authorization matrix for **both** operations (every role, organization-scoped and Case-scoped
Auditor, unauthenticated, disabled user, expired and revoked sessions, wrong Case/Organization,
guessed, cross-Case and mismatched identifiers, Administrator, demonstration Cases, QUARANTINED,
REJECTED, malformed identifiers, CSRF). Content: byte-exact at exact chunk boundaries and 1.5 MB;
headers; hostile database metadata (CRLF, traversal, `text/html`) never reaches a header; no path/key/
credential in any response; no temporary copies; SQL capture proves only an AuditEvent is written and
no row is locked, updated or deleted. Storage: symlinked root/directories/files, non-directory root,
path-like keys, directory/FIFO in place of the file, unreadable file, fd leaks. Mutation checks: each
of audit-before-release, deterministic handle closure, bounded reads, storage-failure-is-not-mismatch,
state gating and the UI guards was broken on purpose and the named tests failed; sources restored.

## 12. Failure-simulation results (real, not inspected)

- Database timeout: real `lock_timeout` on PostgreSQL and real busy-timeout on SQLite; verify returns a
  sanitized `500` with no result and no event, retrieval releases no complete copy; the retry records
  exactly one event.
- Unreachable database: `500`, storage never opened.
- Storage outage (storage root's parent made untraversable) and recovery; missing/unreadable/
  directory/FIFO/symlink objects; read error mid-read and mid-stream.
- Client disconnect: ASGI spec 2.3 and 2.4 paths, and real TCP RST/FIN against a live uvicorn server;
  verification abandoned by the client is still audited exactly once.
- Restart recovery (new application over the same database and storage; interrupted stream).
- Concurrency: 8 threads × verify/retrieve/verify, verification racing an atomic replacement, and 20
  concurrent real connections.

## 13. Docker / runtime results

`python3 scripts/qa/v2_2_runtime_check.py`: new disposable Compose project `veritas-v22-verify`
(Docker 26.1.5 / Compose 2.26.1 in this sandbox; `postgres:17-alpine`, nginx 1.29) — **52 passed, 0
failed**: healthy db/backend/nginx; `/ready` through nginx; evidence volume backend-only (nginx has no
mount and no path); real HTTP retrieval byte-exact for two roles; `nosniff` exactly once; MATCH;
Administrator/unauthenticated denied; CSRF; QUARANTINED `409`; **96 MiB** retrieved and verified through
nginx (backend heap 73 → 83 MiB against a 40 MiB limit; nginx spooled 0 bytes); appended byte,
truncation and in-place change → `MISMATCH` with a full comparison; missing/unreadable/symlinked
object → `UNAVAILABLE` and a sanitized `503`; restore → `MATCH`; audit events visible through the
existing audit API; database stopped → `/ready` 503 and a sanitized error, recovery without a
restart; backend restart and full container recreation keep objects, sessions and audit history;
teardown removed only this project's volumes. `veritas_pgdata`/`veritas-v2_pgdata` are never named,
but **they do not exist in this sandbox**, so this run shows the script never creates, removes or
modifies them, not that it preserves pre-existing ones. The protection that matters is the name
guard: the script refuses any project name not matching `veritas-v22-*` (exit 2; tested with
`veritas`, `veritas-v2`, `veritas_v22` and `other`), so it cannot resolve to those volumes.

Instrument controls (so "nothing observed" cannot be vacuous): memory probe sees a known 64 MiB
allocation; spool probe sees a known unlinked 2 MiB temp file. Negative run: with the V2.1 nginx
config the gate fails exactly one check (nginx spooled 96,993,280 bytes).

`python3 scripts/qa/v2_2_browser_check.py` (optional; real headless Chromium): **32 passed, 0 failed** —
sign-in, a real browser download under the production CSP (name and bytes verified), every state
label, neutral mismatch wording, no CSP violations, and axe-core with colour contrast clean in the
ready/match, mismatch and unavailable states (control: axe flags a known low-contrast element).
Screenshot review changed the wording of the UNAVAILABLE result (it said "Verified").

## 14. Limitations

- **UNAVAILABLE as `200`** is a design decision (section 7); confirm or ask for `503`.
- The retrieval event records release of the final chunk to the transport, not client receipt. A reader
  who aborts before the final chunk leaves **no** retrieval event yet may hold most of the object; only
  the access log shows it.
- Verification costs a full read and two hashes per request; no per-user rate limit.
- A `MATCH` is an integrity comparison only. A `MISMATCH` cannot say why. A writer who can also alter
  the recorded digests in the database defeats detection; there is no WORM storage.
- Browsers buffer the retrieved object as a `Blob` before saving (bounded by `VERITAS_MAX_EVIDENCE_BYTES`,
  100 MiB by default).
- Not exercised: Windows (no `O_NOFOLLOW` there; symlink/junction refusal untested), Docker Desktop,
  macOS, Firefox/WebKit, multi-worker uvicorn, objects larger than 96 MiB, production TLS, external
  penetration testing, forensic certification or legal admissibility (none is claimed).
- `app.__version__` is still `0.1.0` (V2.1 did not bump it either). `scripts/*.sh` are mode `100644` in
  git (pre-existing); run `bash scripts/check.sh`.

## 15. Skipped tests and real reasons

SQLite run: 2 skipped — `tests/test_domain.py:203` and `tests/test_evidence_intake_api.py:872`,
"database-level trigger exists on PostgreSQL only"; both **execute and pass** on PostgreSQL 17.11 (0
skipped). V2.2 tests skip only for a missing OS capability, each with its reason stated in the skip:
symlink creation (Windows privilege / platform), `O_NOFOLLOW` (Windows), `os.mkfifo` (Windows),
directory/file permission enforcement (running as root, or Windows), descriptor counting (no
`/proc/self/fd` or `/dev/fd`). **None of them skipped** in the runs above (non-root Linux).

## 16. Exact commands

```bash
python3 -m venv backend/.venv && backend/.venv/bin/pip install -e "backend[dev]"
(cd frontend && npm ci)                       # Node >= 22.22 (24.21 used); Node 20 cannot run Vitest (jsdom 30)
bash scripts/check.sh                         # static + backend (SQLite) + frontend + hygiene
(cd backend && VERITAS_TEST_DATABASE_URL=postgresql+psycopg://veritas:ci-only-password@127.0.0.1:5432/veritas_test \
   .venv/bin/python -m pytest -q -ra)         # PostgreSQL 17 (role veritas, database veritas_test)
(cd backend && .venv/bin/python -m compileall -q app tests migrations)
(cd backend && alembic upgrade head && python -m app.seed && python -m app.seed && alembic downgrade base)
python3 scripts/qa/v2_2_runtime_check.py      # disposable Docker project; add --keep to leave it up
python3 -m venv /tmp/pw && /tmp/pw/bin/pip install playwright && /tmp/pw/bin/python -m playwright install --with-deps chromium
/tmp/pw/bin/python scripts/qa/v2_2_browser_check.py --screenshots /tmp/v22-shots
git log v2.1.0..v2.2-evidence-retrieval-verification && git diff v2.1.0..v2.2-evidence-retrieval-verification
```

## 17. Final ZIP

`veritas-v2.2-evidence-retrieval-verification.zip` — the complete tracked source tree (155 files,
repository-root entries, no enclosing folder), including `.env.example` and this manifest. Excludes
`.git`, any real `.env`/secrets, local databases, volumes, caches, `node_modules`, `.venv`, temporary
evidence and build artifacts. It was reopened, enumerated and compared with `git ls-files` (identical
sets, integrity test clean, no forbidden paths, the `frontend/src/features/evidence/` sources present),
and the gates were re-run from a fresh extraction with a new venv and `npm ci`: ruff format/check,
mypy strict, compileall, repository lint, hygiene tests (16 passed), backend pytest on SQLite
(318 passed, 2 skipped), frontend typecheck, lint, Vitest (119 passed) and production build.
Not re-run from the extraction: PostgreSQL, Docker and browser checks (run on the working tree above).
