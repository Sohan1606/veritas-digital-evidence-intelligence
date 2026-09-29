# VERITAS — Digital Evidence Intelligence

VERITAS is a platform for trained investigators to organize digital evidence, trace every
finding to its basis and keep conclusions open to human review. Its principle:
**automate work, not accountability.** Evidence is treated as untrusted data, never as
instructions, and the system is not a source of forensic truth.

> **Status: cumulative V2 foundation.** V2 adds provisioned identity, server-side sessions,
> explicit capabilities and case-scoped backend authorization to the independently verified
> V1 foundation. Demonstration records remain synthetic metadata only—not real evidence.

## Scope

**Preserved from V1:** the canonical Case, Objective, Evidence, Evidence Profile, Analysis Run,
Observation, Finding, Claim, Assessment, Case Knowledge Graph, and append-only Audit concepts;
the read-only case API and investigator shell; the synthetic demo dataset; the structured error
contract; and the PostgreSQL/SQLAlchemy/Alembic architecture.

**Implemented in V2:**

- Provisioned Users (no public signup), Organizations and memberships, a canonical six-role
  catalog, case-scoped role assignments, and immutable public identifiers (`USR-001`,
  `ORG-001`, `ROLE-001`, `SES-001`, etc.). Credentials never appear in identity responses.
- Password verification through the established `argon2-cffi` library; an injectable
  authentication adapter boundary for future OIDC/MFA integration; no custom password or
  session cryptography and no default account/password.
- Opaque, random, database-backed sessions with server-side revocation and expiry; HttpOnly,
  SameSite=Strict cookies and Secure cookies in production; session-bound CSRF checks for
  authenticated writes/logout; generic invalid/disabled-credential responses; per-source-IP
  login lockout with stale-bucket cleanup.
- Backend capability and organization/case authorization before existing case and child-resource
  projections. Knowing `CASE-001`, `EVD-001`, or another public ID is not access authority.
  Unreadable Case/child resources are masked as not found.
- Administrator identity management and organization-scoped security-audit reading, added to the
  existing VERITAS shell. Role-aware navigation is a UI convenience; backend policy remains the
  authority. Privileged Administrator/Auditor assignments are intentionally out-of-band.
- Authentication, session, authorization, user-status and role-assignment events in the
  existing append-only Audit model, with a nullable authoritative Organization association; no
  second audit system.
- A tested `development`/`test`-only anonymous read-only demo adapter remains for the V1
  synthetic dataset. Production rejects demo access; restricted mode requires provisioned
  authenticated Users.

**Not implemented (later versions):** real evidence upload/storage/preservation, file parsing,
hashing of uploaded evidence, OCR/NLP/CV, deepfake or media examination, automated analysis,
agent orchestration, external AI APIs, review/decision recording, reports, Case Packages, a
public verifier, public signup, OIDC provider configuration, and MFA. The backend capability
list is the runtime source of what is available or reserved.

## Architecture

```
browser ─► same-origin proxy ─► React + TypeScript
                            └─► FastAPI ─► SQLAlchemy/Alembic ─► PostgreSQL
                                  ├─► opaque server-side session authentication
                                  ├─► capabilities + Organization/Case authorization
                                  └─► existing canonical Audit event stream
```

The browser uses one origin only: Vite's proxy in development or nginx in Docker. Backend
security checks apply to direct API requests and are not replaced by frontend visibility. The
single authoritative role-to-capability mapping is `backend/app/domain/roles.py`; the database
Role catalog stores role identities, not a second mutable copy of policy. See
[docs/architecture.md](docs/architecture.md) and the factual
[docs/threat-model-v2.md](docs/threat-model-v2.md).

### Role-to-capability policy

Capabilities are explicit and enforced server-side. The six role identities are provisioned in
migration `0002`; `backend/app/domain/roles.py` is the sole policy mapping (the database Role
table does not duplicate mutable capability lists). Case-scoped roles require a RoleAssignment
for the target Case. `ADMINISTRATOR` and `AUDITOR` are only assigned by an operator; the Auditor's
case-wide grant is bounded by its Organization membership.

| Role | Capabilities | Scope / console assignment |
| --- | --- | --- |
| `INVESTIGATOR` | `case:read`, `evidence:read`, `findings:read`, `claims:read`, `graph:read`, `case_audit:read`, `examination:read` | Assigned per Case |
| `REVIEWER` | Investigator read capabilities plus `review:read` | Assigned per Case |
| `CUSTODIAN` | `case:read`, `evidence:read`, `case_audit:read`, `custody:read` | Assigned per Case; custody workflow is not implemented |
| `RESEARCHER` | `case:read`, `evidence:read`, `findings:read`, `claims:read`, `graph:read`, `examination:read` | Assigned per Case |
| `ADMINISTRATOR` | `identity:read`, `users:read`, `users:manage`, `roles:assign`, `security_audit:read` | Organization identity administration; out-of-band assignment |
| `AUDITOR` | Read capabilities plus `case:read:any` and `security_audit:read` | Organization-scoped case-wide access; out-of-band assignment |

## Repository structure

```
backend/            FastAPI service
  app/api/          system, cases, auth, identity administration
  app/core/         settings, authentication adapter, session security, middleware, errors, logs
  app/db/           SQLAlchemy engine/session, types, migration status
  app/domain/       canonical entities, explicit role capabilities, relationship rules
  app/services/     read projections, canonical Audit writes, identifier allocation
  app/provision.py  interactive out-of-band account/privileged-role provisioning
  app/seed.py       synthetic demonstration dataset (development only)
  migrations/       append-only Alembic migration chain
  tests/            V1 regression, auth, authorization, audit and architecture tests
frontend/           React/TypeScript application shell, sign-in state, role-aware navigation,
                    identity management, security Audit and preserved V1 workspaces
docker/             Dockerfiles and same-origin nginx proxy
docs/               architecture and threat-model documentation
scripts/            setup, local dev and deterministic verification
```

## Run locally

Requirements: Python ≥ 3.12, Node ≥ 22.22 (CI uses Node 24), PostgreSQL 17.

```bash
scripts/setup.sh      # backend venv, frontend deps, .env from .env.example
# Create the PostgreSQL role/database named in VERITAS_DATABASE_URL, then:
scripts/dev.sh        # migrate, seed synthetic demo data, run API (:8000) + web (:5173)
```

Open <http://localhost:5173> (concept showcase) or <http://localhost:5173/app> (workspace).
The example `.env` enables anonymous, read-only demonstration access in development only.
To exercise real sign-in locally, provision an account; the password is entered through a
non-echoing terminal prompt and is never supplied as a command-line argument:

```bash
cd backend
.venv/bin/python -m app.provision \
  --username operator --display-name "Local Operator" --role ADMINISTRATOR
# Additional operational roles must be explicitly scoped to a Case:
.venv/bin/python -m app.provision \
  --username investigator --display-name "Local Investigator" \
  --role INVESTIGATOR --case-id CASE-001
```

`ADMINISTRATOR` and `AUDITOR` are organization-scoped operator roles; operational roles require
`--case-id`. There are no shipped credentials. After provisioning, sign in from the workspace.
For normal non-demo operation set `VERITAS_ACCESS_MODE=restricted`; an authenticated identity
and an explicit Case role assignment are required before case data is returned.

## API changes

Existing V1 case routes stay GET-only and retain their response contracts. V2 adds:

- `POST /api/v1/auth/login`, `GET /api/v1/auth/session`, `POST /api/v1/auth/logout`.
- `GET /api/v1/admin/users`, `GET /api/v1/admin/roles`.
- `POST /api/v1/admin/users/{user_id}/roles`,
  `DELETE /api/v1/admin/users/{user_id}/roles/{role_id}?case_id=CASE-…`,
  `PATCH /api/v1/admin/users/{user_id}/status`.
- `GET /api/v1/admin/security-audit?limit=…`.

Login/session JSON contains identity, role/capability and expiry metadata only. The opaque session
credential is set as an HttpOnly cookie; it is not returned in JSON or stored raw. CSRF material
is held in a SameSite cookie and checked against the server-side session hash for unsafe methods.
Errors keep the existing `{ "error": { "code", "message", "request_id" } }` envelope. See
`backend/app/schemas.py` for exact response contracts.

## Environment and production boundary

Settings are `VERITAS_*`, read from the process environment or repository-root `.env` (see
`.env.example`; placeholders only). Defaults fail closed.

- `VERITAS_ENVIRONMENT=production` requires `VERITAS_ACCESS_MODE=restricted`, debug off,
  explicit non-wildcard hosts/origins, and PostgreSQL. Production with `demo` access is refused
  at startup.
- `VERITAS_ACCESS_MODE=restricted` means no anonymous operational access. It does not disable
  provisioned password authentication.
- `VERITAS_ACCESS_MODE=demo` is permitted only in development/test and serves only flagged
  synthetic Cases read-only to an anonymous demonstration viewer. A presented invalid/expired
  cookie never falls back to demo access.
- `VERITAS_TRUSTED_PROXY_IPS` is an optional list of trusted IPs/CIDRs. Configure it only for
  actual reverse-proxy addresses; the API then accepts `X-Real-IP` only from those proxies.
  The trusted proxy must overwrite that header. Do not trust arbitrary client-supplied forwarding
  headers. Production should use HTTPS and secure secret/log management at the deployment edge.

Useful settings: `VERITAS_SESSION_TTL_MINUTES` (5–1440, default 720),
`VERITAS_LOGIN_FAILURE_LIMIT` (default 5), `VERITAS_LOGIN_LOCKOUT_MINUTES` (default 15),
`VERITAS_ALLOWED_HOSTS`, `VERITAS_CORS_ORIGINS`, and `VERITAS_MAX_REQUEST_BYTES`.

## Docker

```bash
cp .env.example .env         # replace POSTGRES_PASSWORD locally; never commit .env
docker compose up --build    # http://localhost:8080
```

The local stack runs PostgreSQL, API and nginx; only port 8080 is published on 127.0.0.1. It
runs with development demo access and seeds the synthetic dataset; it is not a production
deployment. To add an identity in the running stack:

```bash
docker compose exec backend python -m app.provision \
  --username operator --display-name "Local Operator" --role ADMINISTRATOR
```

## Testing and verification

```bash
scripts/check.sh
```

Runs backend Ruff format/lint, strict mypy, full pytest; frontend typecheck, lint, Vitest and
production build; repository hygiene tests. Backend defaults to temporary SQLite; set
`VERITAS_TEST_DATABASE_URL` to a PostgreSQL test database to exercise PostgreSQL-only audit
trigger behavior. CI is in `.github/workflows/ci.yml`.

V2 security regression coverage includes unknown/disabled/wrong credentials, session cookie
properties, expiry/logout/replay, IP lockout, CSRF, allowed/denied/cross-case and nonexistent
Case access, role boundaries, direct admin endpoint calls, privilege escalation attempts,
secret/error leakage, security Audit creation and V1 API/demo regression.

Optional browser QA: `scripts/qa/browser_smoke.py` (Playwright must be installed separately).

## Security and limitations

- No public signup; authentication verifies only provisioned active accounts. Password hashes
  use Argon2id via `argon2-cffi`; the session credential is random, opaque and revocable.
- Authorization is server-side. A role does not automatically grant access to every Case;
  organization membership, capabilities and Case scope are independently checked. `AUDITOR`
  case-wide access is limited to the Administrator-provisioned Auditor role and its organization.
- The case-domain API remains read-only; only identity/admin endpoints mutate identity state.
  No uploads, URL fetch, shell execution, external AI call, or evidence content processing.
- Security activity is written through canonical `AuditEvent`, which carries an authoritative
  nullable `organization_id`; the security-audit endpoint filters to organizations where the
  caller holds `security_audit:read`. NULL-scoped global/unknown-login events are deliberately
  not returned to any organization. PostgreSQL uses the existing append-only trigger; SQLite
  test runs only prove ORM-level immutability.
- There is no configured OIDC provider or MFA adapter implementation yet. The authentication
  protocol and immutable issuer/subject linkage fields are intended as replacement points, not
  a claim of OIDC/MFA support.
- New accounts and privileged Administrator/Auditor roles are provisioned only through the
  operator CLI. The console handles authorized status changes and ordinary case-role assignments;
  there is no self-service signup, password reset, or recovery workflow.
- No review/decision write workflow, evidence content, forensic examination, reports, or Case
  Packages are part of V2. Synthetic V1 evidence items remain metadata only.
- Rate limiting is database-backed per observed source IP; distributed-source attacks
  and shared-proxy lockout require additional edge controls. A database superuser can tamper
  with audit storage; external WORM/tamper-evidence export is not implemented.
- No external penetration test, production Docker deployment, PostgreSQL runtime, browser-device
  security assessment, or OIDC/MFA integration is claimed by the source tests. There is no
  global platform-event reader in V2: unknown-login events and user events with ambiguous
  multi-Organization membership remain `organization_id = NULL` and are invisible in every
  organization-scoped security-audit response. A future platform-wide audit role/API would need
  its own explicit authorization design.

## Roadmap and explicit boundaries

Potential later work requires separate design/review gates: OIDC and MFA provider integration;
organization lifecycle and multi-organization administration; secure evidence intake, preservation
and examination; review/decision write workflows; case reporting and export; independent external
security review; and hardened distributed rate limiting and tamper-evident audit export. These
are not present in V2. Public signup and public evidence verification remain out of scope.

## Demonstration data policy

All data is synthetic, created by `python -m app.seed` (development/test only; refused in
production). Records are flagged `demonstration` and labelled
**DEMONSTRATION DATA — NOT REAL EVIDENCE** in the API and UI. Evidence records have no media
or evidence bytes in the repository or database.

## License

MIT — see [LICENSE](LICENSE).
