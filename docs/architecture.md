# VERITAS architecture (cumulative V1 + V2.3)

This document records the cumulative V1/V2/V2.1/V2.2/V2.3 architecture. V2 extends the independently verified V1 foundation, V2.1 adds Evidence intake without replacing the V2 identity/access foundation, V2.2 adds retrieval and integrity verification of preserved EvidenceObjects without a new storage, audit or authorization system, and V2.3 activates examination (Method → Analysis Run → Observation) on the existing AnalysisRun, Observation, audit, storage and authorization concepts. Each concept has exactly one owner, named below.

## 1. System shape

```
browser ──► same origin ──► /            static frontend (Vite build, nginx in Docker)
                        └─► /api /health /ready ──► FastAPI (uvicorn) ──► PostgreSQL
```

* The browser only ever talks to one origin. In development the Vite dev server proxies
  the API; in Docker, nginx does. Frontend code uses relative URLs only (enforced by lint
  and a repository test), so there is no API host to configure in the client and no CORS
  in the default setup.
* Existing V1 case-domain read projections remain read-only. V2 adds authentication and identity
  administration writes. V2.1 adds only explicit Case-scoped Evidence intake writes: the API
  streams raw bytes to a private backend storage abstraction and does not provide URL fetching,
  shell execution, semantic file parsing, forensic examination or external AI calls. V2.2 adds
  one read of those preserved bytes (`GET …/content`) and one observational recomputation
  (`POST …/verify`, which writes a single AuditEvent). V2.3 adds queued examination writes
  (`POST …/analysis-runs`, `…/cancel`, `…/retry`): they write Analysis Runs, Observations and
  AuditEvents only, execute in an in-process worker outside the request, and never touch an
  Evidence, EvidenceObject, custody record or stored byte.

## 2. Canonical domain

Owner: `backend/app/domain/` (enums, values, relationship rules, ORM models). The frontend
mirrors the API contract in `frontend/src/api/types.ts`.

| Entity | Public ID | Lifecycle states | Notes |
| --- | --- | --- | --- |
| Case | `CASE-001` | open · on_hold · closed | `demonstration` flag gates anonymous demo access |
| Objective | `OBJ-001` | active · met · withdrawn | ordered per case |
| Evidence | `EVD-001` | registered · withdrawn | canonical logical Case evidence record |
| EvidenceObject | `EOBJ-001` | QUARANTINED · PRESERVED · REJECTED | one immutable captured byte object; stored outside PostgreSQL |
| EvidenceCustodyEvent | `CST-001` | RECEIVED · PRESERVED | append-only custody projection; only these event types in V2.1 |
| Evidence Profile | (of its evidence) | per-section status | one per evidence item, six sections; computed integrity values project from EvidenceObject |
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

Each attribute carries a basis: `computed` or `declared`. V2.1 can project computed intake
values (for example size and digests) from the associated EvidenceObject; declared values remain
explicitly identified. No section is an authenticity verdict, and sections are never combined
into an overall score.

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
  the same 404 as missing resources. Evidence intake, detail and custody routes each apply their
  own explicit Case capability in addition to Case authorization; Administrator identity privileges
  do not imply evidence access. The browser only reflects these backend permissions. Security-audit
  queries additionally filter `AuditEvent.organization_id` to the organizations where the principal
  holds the explicit `security_audit:read` capability; NULL-scoped platform events are not visible
  to organization roles.
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
* **Audit** security and intake events extend the canonical `AuditEvent` model (no second
  general audit system), carry a nullable authoritative `organization_id`, are append-only
  (PostgreSQL trigger rejects UPDATE/DELETE) and store identifiers/metadata only. Case events
  inherit the Case organization; identity events are scoped to a single unambiguous membership
  or an explicit target organization. Unknown/global events remain NULL-scoped and are excluded
  from organization-audit queries. `EvidenceCustodyEvent` is a separate append-only domain
  projection limited to `RECEIVED` and `PRESERVED` event types; ORM guards and a PostgreSQL
  trigger protect it.
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
* **Data**: `api/client.ts` (typed GET, CSRF-aware JSON mutations, and raw same-origin evidence
  streaming) and `api/useResource.ts` (shared in-memory GET cache with de-duplicated requests and
  explicit reload). Auth state lives in context; credential cookies are not placed in local storage. GET entries are scoped to the
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
  labelled as an illustrative concept sequence; it does not depict an executed examination.
* **Finding actions**: Trace (recorded relationships), Explain (deterministic composition of
  recorded fields — no generated text), Why not (open vs excluded alternatives with basis).
  Challenge is visible but reserved; review/decision recording is outside V2.

## 7. Decisions

1. **Monorepo, two deployables** (`backend/`, `frontend/`) with shared scripts, Docker and CI.
2. **Cumulative API boundary:** existing V1 projections remain read-only; V2 adds authentication
   and identity-administration endpoints, V2.1 adds explicit Case-authorized Evidence intake,
   metadata and custody routes, and V2.2 adds per-EvidenceObject retrieval and verification. No
   generic or path-based download and no reserved-workflow stub is added.
3. **Relational core + projected graph** instead of a graph database: one source of truth,
   referential integrity, and graph semantics kept in code.
4. **Public IDs separate from primary keys**: stable, human-readable references without
   exposing internal keys or row counts across entity types.
5. **Declared vs computed attributes** instead of any aggregate score or verdict.
6. **Same-origin proxying** so the browser never addresses the API host.
7. **Demonstration-data separation:** the idempotent seed loads synthetic metadata only and
   refuses to run in production. Separately authorized V2.1 Evidence intake stores captured bytes
   only in private backend storage, never in the seed or PostgreSQL.

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
* Evidence intake, preservation and hashing are implemented in V2.1. The storage interface
  isolates the local filesystem backend so a future object-storage adapter can be evaluated
  without adding another Evidence domain concept. Such an adapter, WORM guarantees and signed
  manifests are not part of this implementation.
* Further examination Methods are added to the code-owned registry as new `(key, version)` entries
  (section 12); they publish Observations only. Any forensic interpretation, Findings from
  Observations, or review is a separate design gate.
* New asserted relationship types are added to `ASSERTED_RELATIONSHIPS` plus a migration.

## 10. V2.1 Evidence intake and storage

* `Evidence` remains the logical record and `EvidenceProfile` remains the only profile projection.
  `EvidenceObject` represents one captured byte sequence; another acquisition creates a new object.
* Intake is split into metadata registration, raw-byte upload and explicit finalization. Upload
  streams through bounded chunks, counts actual received bytes against `VERITAS_MAX_EVIDENCE_BYTES`,
  and computes SHA-256/SHA-512 while writing to quarantine. Finalization performs only a bounded,
  deterministic leading-byte signature/type compatibility check and either rejects or preserves.
* The only EvidenceObject terminal paths are `QUARANTINED → PRESERVED` and
  `QUARANTINED → REJECTED`. Preserved metadata and bytes are not editable. The separate
  append-only custody projection records only `RECEIVED` and `PRESERVED` in V2.1.
* `EvidenceStorage` hides storage mechanics behind an application protocol. `LocalEvidenceStorage`
  uses opaque server-generated keys, exclusive file creation and a same-filesystem atomic
  publication into a backend-private preserved directory. The configured evidence root is
  outside PostgreSQL. Compose declares a separate named volume mounted only by the backend;
  nginx has no evidence-volume mount and exposes no static storage route.
* Case authorization is applied before item lookup. Intake/upload require `evidence:intake`,
  object detail requires authenticated `evidence:read`, finalization requires `evidence:intake`
  and `custody:write`, and custody reads require authenticated `custody:read`. No V2.1 object
  creation or metadata/custody access is permitted for a demonstration Case, even to an
  authenticated role. Anonymous demo access retains V1 Evidence/Profile behavior but cannot read
  V2.1 object metadata/custody or see object-derived digests through the profile projection.
  Administrator identity privileges are not evidence capabilities. There is no general
  evidence-byte download endpoint; V2.2 retrieval is per PRESERVED EvidenceObject (section 11).
* Migration `0003_evidence_intake_integrity` adds the intake/custody schema and PostgreSQL
  append-only protection (the Alembic head in V2.2; V2.3 appends `0004`). SQLite upgrade/downgrade/re-upgrade and
  ORM immutability are tested; the PostgreSQL trigger tests run only against a PostgreSQL test
  database. Docker runtime and volume persistence are established only by running the stack, not
  by static Compose inspection (see `scripts/qa/v2_2_runtime_check.py`).
* Hashes confirm only the byte sequence VERITAS received and processed. Basic signatures are not
  content parsing or malware analysis; private local storage is not WORM, independently verified
  tamper-proof storage, or a forensic certification. See [the V2.1 implementation notes](evidence-intake-v2.1.md)
  for endpoint contracts and verification boundaries.

## 11. V2.2 Evidence retrieval and integrity verification

* **One owner per concept.** `services/evidence_access.py` owns retrieval and verification.
  Resolution is `queries.get_evidence_object` (Case → Evidence in Case → EvidenceObject in both;
  the same resolver the custody query uses). Bytes come only from
  `EvidenceStorage.open_preserved_object`; recomputation is `evidence_hashing.digest_file`; events
  go through `records.record_audit_event`; errors use the existing envelope. The vocabulary lives in
  `domain.enums` (`EvidenceIntegrityResult`, two `EvidenceAuditAction` values) and the response in
  `schemas.EvidenceIntegrityVerificationOut`. There is no verification table, no cached result,
  no new capability and no migration: a result is computed on request and recorded only as an
  AuditEvent.
* **Authorization first.** Both routes use `require_case_capability("evidence:read",
  authenticated_only=True, non_demonstration_only=True)` ahead of any resolution or storage
  access. Denied, demonstration-Case and cross-Case requests are masked as not found; an object
  that is not `PRESERVED` is a `409`. Authorization failures are never `UNAVAILABLE`.
* **Retrieval.** A sync route opens the preserved object, reads the first chunk (so early storage
  failures are a clean `503`), and returns a `RetrievalResponse`: a `StreamingResponse` that closes
  the preserved-object handle however the response ends. Chunks are at most `HASH_CHUNK_BYTES`
  and are read in the thread pool; `Content-Length` is the size at open and reads are capped at
  it. The retrieval AuditEvent is committed before the final chunk is released. No database
  connection is held while bytes stream.
* **Verification.** A sync route recomputes byte count, SHA-256 and SHA-512 with bounded reads
  and compares all three with the recorded values. `MATCH`, `MISMATCH` and `UNAVAILABLE` are all
  `200` results, each with exactly one AuditEvent; storage failures are `UNAVAILABLE` and never
  `MISMATCH`. The only SQL writes are that event and its identifier allocation; no row is
  locked, updated or deleted.
* **Storage.** `LocalEvidenceStorage._open_existing` now closes its descriptor on every failure
  path and opens with `O_NONBLOCK`, so a special file swapped in for an object is refused instead
  of blocking a worker thread.
* **Edge.** nginx serves the content route with `proxy_buffering off` and
  `proxy_max_temp_file_size 0` so preserved bytes are streamed through and never spooled to the
  web container's disk. The evidence volume is still mounted only into the backend.
* **Frontend.** `features/evidence/EvidenceObjectAccess.tsx` extends the existing EvidenceObject
  card. State labels come from `design-system/semantics.ts` (`INTEGRITY_RESULT`); retrieval uses
  `api/client.apiDownload` (same-origin, relative) and never keeps bytes in component state.
* **Not claimed.** A `MATCH` is an integrity comparison with the intake record, not authenticity,
  forensic reliability or legal admissibility. See
  [the V2.2 implementation notes](evidence-retrieval-verification-v2.2.md).

## 12. V2.3 Examination Core

* **One owner per concept.** The Method contract is `examination/contracts.py`; the single
  authoritative list of executable Methods is `examination/registry.py` (`METHOD_REGISTRY`, code
  only — no table, flag or setting changes what a Method does); the first Method is
  `examination/methods/binary_characteristics.py`; the only bounded evidence reader is
  `examination/runner.EvidenceReader`; **every** Analysis Run state change in the database is made
  by `examination/coordinator.py` through one compare-and-set primitive that first asks
  `domain/lifecycle.py`; API commands (eligibility, idempotent creation, cancel, retry) are
  `services/examination.py`; routes are `api/examination.py`. `AnalysisRun` is the execution
  record (there is no `Examination` entity), Observations are written by the existing
  `records.record_observation`, events by `records.record_audit_event`, errors use the existing
  envelope, and the role map stays in `domain/roles.py`.
* **Exact provenance.** `analysis_runs.evidence_object_id` is NOT NULL and part of a composite
  foreign key `(evidence_object_id, evidence_id, case_id)` to `evidence_objects`, so the database
  refuses a run whose object is not in that Evidence and Case. Migration `0004` is append-only and
  refuses to upgrade over pre-V2.3 rows rather than guess a provenance. Observations point at their
  run; the run points at its object.
* **Durable execution without new infrastructure.** The `analysis_runs` table is the queue. A run
  is `QUEUED` by the request and executed by an in-process worker thread started in the
  application lifespan. A claim is a compare-and-set (`FOR UPDATE SKIP LOCKED` on PostgreSQL);
  each claim stamps `started_at`, which every later write of that worker repeats (fencing), so a
  stalled worker that lost its run cannot publish. Heartbeats carry cancellation requests; stale
  runs are requeued; shutdown releases the run in flight. Completion, Observations and audit are one
  transaction.
* **Authorization before storage.** Routes use `require_case_capability("examination:execute",
  authenticated_only=True, non_demonstration_only=True)`; the service re-checks demonstration
  Cases; the object is opened (never read) only after every database-level rule passes.
* **Bounded, private, fail-closed.** The Method receives chunks, not a path; the reader enforces
  64 KiB reads, the byte and time limits, checks cooperative cancellation and compares the bytes
  read with the recorded byte count and digests (`evidence_hashing.matches_recorded_integrity`,
  shared with V2.2) so Observations exist only for bytes that still equal the PRESERVED object.
  Nothing is written to storage and no copy is made.
* **Capability registry.** `examination` is `available` in `/api/v1/system`; the frontend reads
  that statement and the session's Case capabilities; Timeline, Review and Report stay reserved.
* **Frontend.** `features/examination/` (Methods, EvidenceObject picker, runs and run detail) reuses
  the shell, navigation, `StateView`, `DataTable` and `semantics.ts` (`RUN_STATE`,
  `OBJECT_STATE_TONE` — the one object-state mapping, now also used by the Evidence card).
  `api/useResource.ts` gained in-place polling (`refreshMs`, `refreshWhile`, `refresh`,
  `refreshError`): the same single GET owner, not a second fetch mechanism.
* **Not claimed.** An Observation is a measurement. No authenticity, origin, manipulation, malware
  or admissibility statement is made. See [the V2.3 notes](examination-core-v2.3.md).
