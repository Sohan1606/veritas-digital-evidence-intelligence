# VERITAS architecture (V1 foundation)

This document records how V1 is built and why, so later versions extend it rather than
re-deciding it. Each concept has exactly one owner, named below.

## 1. System shape

```
browser ──► same origin ──► /            static frontend (Vite build, nginx in Docker)
                        └─► /api /health /ready ──► FastAPI (uvicorn) ──► PostgreSQL
```

* The browser only ever talks to one origin. In development the Vite dev server proxies
  the API; in Docker, nginx does. Frontend code uses relative URLs only (enforced by lint
  and a repository test), so there is no API host to configure in the client and no CORS
  in the default setup.
* The V1 API is **read-only** (`GET` only). There are no write endpoints, uploads, URL
  fetching, shell execution or calls to external AI services.

## 2. Canonical domain

Owner: `backend/app/domain/` (enums, values, relationship rules, ORM models). The frontend
mirrors the API contract in `frontend/src/api/types.ts`.

| Entity | Public ID | Lifecycle states | Notes |
| --- | --- | --- | --- |
| Case | `CASE-001` | open · on_hold · closed | `demonstration` flag gates anonymous demo access |
| Objective | `OBJ-001` | active · met · withdrawn | ordered per case |
| Evidence | `EVD-001` | registered · withdrawn | V1 holds metadata records only — no content |
| Evidence Profile | (of its evidence) | per-section status | one per evidence item, six sections |
| Analysis Run | `ANL-001` | queued · running · completed · failed · cancelled | none are produced in V1 |
| Observation | `OBS-001` | recorded · superseded | origin `analysis_run` or `manual` |
| Finding | `FND-001` | review: unreviewed · under_review · accepted · challenged · rejected | method, limitations, alternative explanations |
| Claim | `CLM-001` | open · under_assessment · assessed · withdrawn | assertion by a party, never decided by VERITAS |
| Assessment | `ASM-001` | draft · submitted · withdrawn | human evaluation of a claim |
| Audit Event | `AUD-001` | append-only | metadata only; never evidence content |
| Relationship | `REL-001` | — | asserted graph edge (see §3) |

Every record has an internal UUID primary key (never exposed), a human public ID,
`created_at` / `updated_at` (UTC), and `created_by` / `updated_by` actor strings.
Public IDs are allocated per entity type from one sequence table, so each entity has exactly
one ID system (`{PREFIX}-{n:03d}`, growing past three digits when needed).

**Evidence Profile statuses** — the only status vocabulary for evidence sections; definitions
are owned by `domain/values.py` and served to the UI with every profile:

* **Verified** — every attribute in the section was computed by a recorded, reproducible procedure.
* **Partial** — some attributes are recorded, but at least one is declared (unchecked) or missing.
* **Unknown** — sought but could not be established from what is available.
* **Not available** — no procedure exists in this version, or the section does not apply.

Each attribute carries a basis: `computed` or `declared`. V1 computes nothing, so no V1
section can be *Verified*. Sections are never combined into an overall score.

## 3. Case Knowledge Graph

Owner: `backend/app/services/graph.py` (projection) and `domain/relationships.py` (rules).
There is one graph per case; it is a projection, not a separate store.

* **Structural** edges come from ownership keys and are never stored:
  Case *contains* Evidence, Case *contains* Claim, Observation *derived-from* Evidence.
* **Asserted** edges are stored in `case_relationships` (with optional rationale) and must
  match an allowed triple: Finding *derived-from* Observation/Finding; Finding *supports*
  Claim; Finding *contradicts* Claim/Finding; Claim *contradicts* Claim; Claim *references*
  Evidence. Duplicates are prevented by a unique constraint.

The frontend renders the same graph two ways — a layered SVG diagram (deterministic
barycentric layout, `features/graph/layout.ts`) and an equivalent relationship table — and
selection drives one detail panel in both. The table is the accessible and small-screen
presentation. Node types are distinguished by shape as well as label; relationship types by
line style as well as colour.

## 4. Access, security and privacy

Owner: `backend/app/core/` (`config`, `security`, `middleware`, `errors`, `logging`).

* **Fail-closed configuration.** Defaults: `production`, debug off, `restricted` access, no
  CORS origins, docs off. Production refuses `demo` access (it must be `restricted`), debug,
  wildcard hosts/origins and non-PostgreSQL databases at startup. Validation errors do not
  echo input values, so a misconfiguration cannot print the database password.
* **Access modes.** V1 has no identity provider (reserved for V2; no custom auth is invented).
  `restricted` returns `401 authentication_unavailable` for all case data. `demo` allows
  anonymous read of cases flagged `demonstration` only; anything else is `404`, so
  non-demo records are not even confirmed to exist. `demo` is valid only in `development`
  and `test`; production must use `restricted`.
* **Middleware, outermost first:** security headers (CSP, nosniff, frame deny,
  referrer, permissions; HSTS in production) → request context (request ID, structured
  access log, catch-all error envelope) → trusted hosts → CORS (GET only, explicit
  origins) → request body limit.
* **Errors** use one envelope `{"error": {"code", "message", "request_id"}}`; stack traces
  and internals are never returned.
* **Audit** events are append-only (PostgreSQL trigger rejects UPDATE/DELETE) and store
  identifiers and metadata only.
* **Logs** are JSON lines with request ID, route template, method, status and duration.
  They never contain request bodies, query values, evidence content or secrets.

## 5. Observability

* `GET /health` — liveness, no dependencies.
* `GET /ready` — database reachable and schema at the expected Alembic revision; `503` otherwise.
* `GET /api/v1/system` — version, environment, access mode, schema revision and the
  **capability list**: the single statement of what is available or reserved. The frontend
  renders reserved sections and the showcase capability list from it.
* Every response carries `X-Request-ID`; the UI shows it on error states for correlation.
* The frontend has a top-level error boundary and one per routed page.

## 6. Frontend

* **Shell** (`app-shell/`): one navigation definition (`navigation.ts`) feeds the sidebar,
  breadcrumbs and command palette, so a section cannot exist under two names. Workspace
  sections: My Work, Cases; case sections: Evidence, Examination, Findings, Claims,
  Timeline, Graph, Review, Reports, Audit. Reserved sections are labelled as such.
* **Data**: `api/client.ts` (typed failures: unreachable, restricted, not_found, invalid,
  server) and `api/useResource.ts` (shared in-memory cache with de-duplicated requests and explicit reload).
* **UI states**: every data view renders Loading, Ready, Empty, Error, Partial or
  Unavailable through `design-system/StateView.tsx`.
* **Design system** (`design-system/`, tokens in `styles.css`): graphite surfaces, one
  signal accent, semantic colours only for meaning and always paired with a glyph or text,
  monospace for identifiers, hashes and timestamps; motion tiers micro (120 ms) < state
  (200 ms) < transition (320 ms); `prefers-reduced-motion` disables non-essential motion.
* **Showcase** (`showcase/`): one scroll-linked canvas sequence drawn procedurally
  (`drawScene` is a pure function of size, progress and a seeded scene). It redraws only
  when the quantized frame changes, caps device pixel ratio, uses a portrait layout on
  narrow screens and becomes a static six-panel storyboard under reduced motion. It is
  labelled as a concept sequence: automated examination is not part of V1.
* **Finding actions**: Trace (recorded relationships), Explain (deterministic composition of
  recorded fields — no generated text), Why not (open vs excluded alternatives with basis).
  Challenge is visible but reserved; it needs investigator identity.

## 7. Decisions

1. **Monorepo, two deployables** (`backend/`, `frontend/`) with shared scripts, Docker and CI.
2. **Read-only V1 API** limited to the routes the V1 UI needs; no placeholder endpoints for
   future modules. Reserved capabilities are declared in `/api/v1/system`, not stubbed.
3. **Relational core + projected graph** instead of a graph database: one source of truth,
   referential integrity, and graph semantics kept in code.
4. **Public IDs separate from primary keys**: stable, human-readable references without
   exposing internal keys or row counts across entity types.
5. **Declared vs computed attributes** instead of any aggregate score or verdict.
6. **Same-origin proxying** so the browser never addresses the API host.
7. **Synthetic demonstration data only**, loaded by an idempotent seed that refuses to run in
   production and marks every record `DEMONSTRATION DATA — NOT REAL EVIDENCE`.

## 8. Extension points for later versions

* Identity/RBAC plugs into `core/security.resolve_principal`; `access_mode` gains an
  authenticated mode and `created_by` values become principal IDs.
* Evidence intake, preservation and hashing populate `computed` profile attributes, which is
  what allows a section to become *Verified*.
* Examination methods produce Analysis Runs whose Observations use origin `analysis_run`.
* New asserted relationship types are added to `ASSERTED_RELATIONSHIPS` plus a migration.
