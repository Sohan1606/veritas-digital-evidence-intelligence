# VERITAS architecture (V1 foundation + V2 identity/access)

This document records the cumulative V1/V2 architecture. V2 extends the independently verified V1 foundation rather than replacing it. Each concept has exactly one owner, named below.

## 1. System shape

```
browser ──► same origin ──► /            static frontend (Vite build, nginx in Docker)
                        └─► /api /health /ready ──► FastAPI (uvicorn) ──► PostgreSQL
```

* The browser only ever talks to one origin. In development the Vite dev server proxies
  the API; in Docker, nginx does. Frontend code uses relative URLs only (enforced by lint
  and a repository test), so there is no API host to configure in the client and no CORS
  in the default setup.
* Existing V1 case-domain routes remain **read-only** (`GET` only). V2 adds a small set of real
  authentication and identity-administration writes; no uploads, URL fetching, shell execution,
  or external AI calls are introduced.

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
* **Authentication.** A provisioned active User authenticates with a password verified by
  `argon2-cffi`. Authentication is separated from authorization through the
  `Authenticator` protocol and stable User identity. There is no public signup, default account,
  custom password hash, or OIDC/MFA claim. A password adapter is implemented; OIDC/MFA is a
  future provider integration.
* **Sessions.** A fresh, random opaque session value is set in an HttpOnly, SameSite=Strict
  cookie (Secure in production). Only a SHA-256 digest of the high-entropy value is stored.
  Every request checks the server-side session, User status, revocation and expiry. Logout and
  User disablement revoke sessions. A separate SameSite CSRF cookie is compared against a
  session-bound stored digest for unsafe authenticated methods.
* **Authorization.** `domain/roles.py` is the canonical explicit Role-to-capability mapping.
  An active Organization membership and RoleAssignment are required. Case data additionally
  requires a matching Case-scoped capability before any existing read projection runs. Global
  Auditor case access is explicit in its capability and out-of-band assignment. Administrator
  identity privileges do not imply Case access. Case/child authorization failures are masked as
  the same 404 as missing resources. The browser only reflects these backend permissions. Security-audit queries additionally filter
  `AuditEvent.organization_id` to the organizations where the principal holds the explicit
  `security_audit:read` capability; NULL-scoped platform events are not visible to organization roles.
* **Access modes.** `restricted` requires provisioned authentication for operational Case data.
  `demo` is a separate, anonymous, read-only V1 demonstration adapter: only flagged synthetic
  Cases are accessible, only in `development` and `test`; production refuses `demo`. A supplied
  invalid/expired credential never downgrades into the demo adapter.
* **Rate limiting.** Authentication failures are grouped by observed source IP, persisted in
  the database, and locked for a configured window; expired rate buckets are cleaned on login.
  A trusted proxy's `X-Real-IP` is accepted only when the connecting proxy IP matches
  `VERITAS_TRUSTED_PROXY_IPS`. Configure the proxy to overwrite that header. Distributed-source
  attacks and edge-level rate controls remain operational responsibilities.

* **Middleware, outermost first:** security headers (CSP, nosniff, frame deny,
  referrer, permissions; HSTS in production) → request context (request ID, structured
  access log, catch-all error envelope) → trusted hosts → CORS (explicit origins; credentials and the methods required by the session/admin API) → request body limit.
* **Errors** use one envelope `{"error": {"code", "message", "request_id"}}`; stack traces
  and internals are neither returned to clients nor emitted in structured VERITAS JSON logs.
* **Audit** security events extend the canonical `AuditEvent` model (no second system), carry a
  nullable authoritative `organization_id`, are append-only (PostgreSQL trigger rejects
  UPDATE/DELETE) and store identifiers/metadata only. Case events inherit the Case organization;
  identity events are scoped to a single unambiguous membership or an explicit target organization.
  Unknown/global events remain NULL-scoped and are excluded from organization-audit queries.
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
  breadcrumbs and command palette, so a section cannot exist under two names. Workspace sections: Cases plus capability-gated Identity management and Security audit; case
  sections: Evidence, Examination, Findings, Claims, Timeline, Graph, Review, Reports, Audit. Reserved sections are labelled as such.
* **Data**: `api/client.ts` (typed GET and CSRF-aware mutation calls) and `api/useResource.ts`
  (shared in-memory GET cache with de-duplicated requests and explicit reload). Auth state lives
  in context; credential cookies are not placed in local storage. GET entries are scoped to the
  authenticated session identity and cleared before identity changes and at logout; in-flight
  requests from the prior scope remain detached and cannot repopulate the new scope. Logout also
  removes `veritas.activeCase` from session storage. These client controls prevent accidental
  cross-identity reuse but are not an authorization boundary.
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
  labelled as a concept sequence: automated examination is not part of V2.
* **Finding actions**: Trace (recorded relationships), Explain (deterministic composition of
  recorded fields — no generated text), Why not (open vs excluded alternatives with basis).
  Challenge is visible but reserved; review/decision recording is outside V2.

## 7. Decisions

1. **Monorepo, two deployables** (`backend/`, `frontend/`) with shared scripts, Docker and CI.
2. **Cumulative API boundary:** V1 case-domain routes remain GET-only; V2 adds only real
   authentication and minimum identity-administration endpoints. Reserved domain capabilities
   are declared in `/api/v1/system`, not stubbed.
3. **Relational core + projected graph** instead of a graph database: one source of truth,
   referential integrity, and graph semantics kept in code.
4. **Public IDs separate from primary keys**: stable, human-readable references without
   exposing internal keys or row counts across entity types.
5. **Declared vs computed attributes** instead of any aggregate score or verdict.
6. **Same-origin proxying** so the browser never addresses the API host.
7. **Synthetic demonstration data only**, loaded by an idempotent seed that refuses to run in
   production and marks every record `DEMONSTRATION DATA — NOT REAL EVIDENCE`.

## 8. V2 identity/access integration points

* `core/authentication.Authenticator` separates credential verification from stable User identity.
  `ProvisionedPasswordAuthenticator` is the implemented local adapter. A future OIDC/MFA adapter
  resolves immutable issuer/subject linkage to the same User; authorization and domain queries do
  not need to be rewritten.
* `core/security` resolves the opaque server-side Session and enforces role capability and Case
  scope. `records.record_security_event` appends security actions to the existing Audit model.
* `Organization`, `OrganizationMembership`, `Role`, `RoleAssignment`, `User`, and `UserSession`
  have stable public IDs where records are user-visible. No second person/user entity exists.
* User provisioning and high-impact Administrator/Auditor assignment are out-of-band CLI actions;
  ordinary case-scoped Role assignment/status changes are protected admin API operations.

## 9. Extension points for later versions

* Additional organizations and membership lifecycle can be added without changing Case scope.
* Evidence intake, preservation and hashing populate `computed` profile attributes, which is
  what allows a section to become *Verified*.
* Examination methods produce Analysis Runs whose Observations use origin `analysis_run`.
* New asserted relationship types are added to `ASSERTED_RELATIONSHIPS` plus a migration.
