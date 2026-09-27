# VERITAS — Digital Evidence Intelligence

VERITAS is a platform for trained investigators to organize digital evidence, trace every
finding to its basis and keep conclusions open to human review. Its principle:
**automate work, not accountability.** Evidence is treated as untrusted data, never as
instructions, and the system is not a source of forensic truth.

> **Status: V1 foundation.** Read-only, synthetic demonstration data only. Not for use with
> real evidence.

## Scope

**Implemented in V1**

- Canonical domain: Case, Objective, Evidence, Evidence Profile, Analysis Run, Observation,
  Finding, Claim, Assessment, Audit Event. Stable IDs (`CASE-001`, `EVD-001`, `OBS-001`,
  `FND-001`, `CLM-001`, `ASM-001`, `ANL-001`, `AUD-001`), timestamps, lifecycle states,
  created/updated by, typed relationships.
- PostgreSQL schema with Alembic migrations, append-only audit trail, idempotent dev seed.
- Read-only API: `GET /health`, `GET /ready`, `GET /api/v1/system`, and case routes for
  list/detail, evidence, evidence profile, analysis runs, findings, claims, graph and audit events.
- Investigator shell: side navigation (My Work, Cases, Evidence, Examination, Findings,
  Claims, Timeline, Graph, Review, Reports, Audit), context bar, global search / command
  palette (`Ctrl/⌘ K`), notifications, responsive layout.
- Case workspace for **CASE-001 — Synthetic Demonstration Case**.
- Evidence Profile (Identity, Integrity, Provenance, Quality, Acquisition context,
  Classification) with statuses Verified / Partial / Unknown / Not available. No score.
- Finding view with Observation, Method, Evidence basis, Related claim, Alternative
  explanations, Limitations, Review status, and Trace / Explain / Why not actions
  (deterministic, no generated text). Challenge is shown as reserved.
- Case Knowledge Graph: one graph per case, typed relationships (supports, contradicts,
  derived-from, references, contains), visual and table presentations.
- Showcase page with a scroll-linked concept sequence (reduced-motion and mobile variants).

**Reserved for later versions (not built):** identity and access control, evidence upload,
quarantine, preservation, chain of custody, hashing, forensic analyses, OCR, analysis
orchestration, contradiction detection, timeline reconstruction, recording reviews and
decisions, report generation, Case Packages, verification, benchmarks, model governance.
The running backend lists these in `GET /api/v1/system`, and the UI labels them as reserved.

## Architecture

```
browser ─► one origin ─► /                  React + TypeScript (Vite, Tailwind CSS)
                     └─► /api /health /ready ─► FastAPI + Pydantic ─► PostgreSQL (SQLAlchemy, Alembic)
```

The browser talks to one origin only: the Vite dev server (development) or nginx (Docker)
proxies API calls. Design decisions, the domain model, graph rules and security layers
are in [docs/architecture.md](docs/architecture.md).

## Repository structure

```
backend/            FastAPI service
  app/api/          HTTP routes (system, cases)
  app/core/         config, security, middleware, errors, logging
  app/db/           engine/session, types, migration status
  app/domain/       canonical enums, value definitions, relationship rules, ORM models
  app/services/     read queries, record allocation, graph projection
  app/seed.py       synthetic demonstration dataset (development only)
  migrations/       Alembic migrations
  tests/            API, domain, security and architecture tests
frontend/           React application
  src/api/          typed API client and resource cache
  src/app-shell/    navigation, shell, command palette, notifications, error boundary
  src/design-system/ tokens usage, primitives, state views, icons
  src/features/     cases, evidence, examination, findings, claims, graph, review, audit, reserved
  src/showcase/     concept sequence (procedural canvas)
  src/test/         frontend tests and typed fixtures
docker/             Dockerfiles and nginx config
docs/               architecture
scripts/            setup, dev, check
tests/              repository hygiene tests
```

## Run locally

Requirements: Python ≥ 3.12, Node ≥ 22.22 (CI uses Node 24), PostgreSQL 17.

```bash
scripts/setup.sh      # backend venv, frontend deps, .env from .env.example
# create the PostgreSQL role/database named in VERITAS_DATABASE_URL, then:
scripts/dev.sh        # migrate, seed demonstration data, run API (:8000) + frontend (:5173)
```

Open http://localhost:5173 (showcase) or http://localhost:5173/app (workspace).

## Environment

All settings are `VERITAS_*` variables, read from the environment or a repository-root `.env`
(see [.env.example](.env.example); placeholders only). Unset values fail closed.

| Variable | Default | Purpose |
| --- | --- | --- |
| `VERITAS_ENVIRONMENT` | `production` | `development` · `test` · `production` |
| `VERITAS_ACCESS_MODE` | `restricted` | `restricted` serves no case data; `demo` serves demonstration cases read-only |
| `VERITAS_DATABASE_URL` | — (required) | PostgreSQL URL |
| `VERITAS_ALLOWED_HOSTS` | `localhost,127.0.0.1` | trusted Host headers |
| `VERITAS_CORS_ORIGINS` | empty | explicit browser origins (none needed with the proxy) |
| `VERITAS_DEBUG` | `false` | refused in production |
| `VERITAS_EXPOSE_API_DOCS` | `false` | `/docs` outside production only |
| `VERITAS_MAX_REQUEST_BYTES` | `1048576` | request body limit |
| `VERITAS_LOG_LEVEL` | `INFO` | log level |
| `VERITAS_API_PROXY_TARGET` | `http://127.0.0.1:8000` | dev-server proxy target |
| `VERITAS_DEV_ALLOWED_HOSTS` | empty | extra hostnames for the dev server |

## Docker

```bash
cp .env.example .env         # set POSTGRES_PASSWORD
docker compose up --build    # http://localhost:8080
```

Runs PostgreSQL, the API and nginx serving the built frontend. Only port 8080 is published,
on 127.0.0.1. The compose stack runs in development mode with demonstration access and
loads the demonstration dataset. It is a local stack, not a production deployment.

## Testing

```bash
scripts/check.sh
```

Runs, in order: backend `ruff format --check`, `ruff check`, `mypy --strict`, `pytest`;
frontend `typecheck`, `lint`, `vitest`, production `build`; repository hygiene tests.
Backend tests use a temporary SQLite database, or PostgreSQL when
`VERITAS_TEST_DATABASE_URL` is set (as in CI, which also applies migrations and runs the
seed twice). CI: `.github/workflows/ci.yml`.

Coverage includes health/readiness, validation and error envelopes, routes, database
constraints and append-only audit, no secret or stack-trace leakage, the auth placeholder,
CORS and trusted hosts, canonical entities, no duplicate endpoints, no demo-data leakage
into non-demo cases, frontend boot and routing, navigation, responsive fallback,
automated accessibility checks (axe), WCAG AA contrast of every text colour token on every
surface, Evidence Profile, Finding actions, graph layout and the API client.

Optional real-browser QA (not part of `check.sh`; Playwright is not a project dependency):
`scripts/qa/browser_smoke.py` runs against a running build in Chromium, Firefox and WebKit
at 1440 and 390 px — route content, axe WCAG 2.1 AA including colour contrast, showcase
canvas, graph selection, command palette / mobile navigation, console errors and CSP
violations; `--perf` adds a throttled-CPU mobile scroll probe. Setup is in its docstring.

## Security

- Read-only API; no upload, URL fetching, shell execution, external AI calls, signup, public
  case creation or debug endpoints.
- No authentication exists in V1 and none is improvised: `restricted` mode (the default)
  serves no case data; `demo` mode serves demonstration cases only.
- Trusted hosts, explicit-origin CORS (GET only), body-size limit, security headers
  (strict CSP, HSTS in production), uniform error envelope without stack traces.
- Structured logs with request IDs; no request bodies, evidence content or secrets.
- Secrets come from the environment only; `.env` is git-ignored.

## Demonstration data policy

All data is synthetic, created by `python -m app.seed` (development only; refused in
production). Records are flagged `demonstration` and labelled
**DEMONSTRATION DATA — NOT REAL EVIDENCE** in the API and UI. Evidence items are metadata
records only; no media or evidence files exist in the repository or database.

## Known limitations

- No identity, so nothing can be written: reviews, challenges and assessments are shown as
  recorded but cannot be created in the UI.
- No evidence content: Integrity and Quality are *Not available*, and no profile section can
  be *Verified*.
- No examination: no Analysis Runs exist; the demonstration observations are manual records.
- Global search matches loaded navigation targets and records literally; there is no search index.
- Notifications are local, client-side system notices.
- Browser coverage is automated headless engines (Chromium, Firefox, Playwright WebKit on
  Linux). Safari on Apple platforms, mobile devices and screen readers are not yet tested.
- ESLint stays on 9.x (no longer supported upstream) until `eslint-plugin-jsx-a11y`
  supports ESLint 10; it is a development-only dependency.

## Roadmap

- **V2:** identity provider integration, roles and case permissions, recording reviews,
  challenges and assessments.
- **Next:** evidence intake with quarantine, preservation, hashing and chain of custody;
  examination methods and Analysis Runs; timeline reconstruction; reports and verifiable
  Case Packages.

## License

MIT — see [LICENSE](LICENSE).
