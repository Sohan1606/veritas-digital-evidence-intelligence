VERITAS V2.1 Arena Handoff

Baseline:
50ef8bd

Implementation status:
Uncommitted; no commit or push was made.

Scope:
Evidence Intake & Integrity Foundation, including Windows storage portability, portable symlink tests, password-safe Compose PostgreSQL connection configuration, and the quoted Nginx evidence-content route regex.

Storage correction:
- Evidence file contents are still fsynced before publication.
- POSIX directory fsync remains enabled and genuine I/O errors continue to surface as sanitized storage failures.
- Windows directory fsync is explicitly reported as unsupported; successful content publication and quarantine cleanup are not converted into failures solely because directory fsync is unavailable. Windows directory-entry durability across power loss is not claimed.
- Publication remains exclusive/no-overwrite and immutable. The preserved directory is synced before removing the quarantine name where directory fsync is supported; the quarantine directory is then synced after cleanup.
- Configured roots and quarantine/preserved paths reject symlink/junction components. Path-key/traversal tests remain independent of symlink privilege.
- Symlink-specific tests run when symlink creation is available. On Windows, only those tests skip for WinError 1314, with a privilege-specific explanation.
- Compose supplies the backend's database password through `PGPASSWORD` while its SQLAlchemy URL contains no password; PostgreSQL receives the same `POSTGRES_PASSWORD` variable. `pgdata` and `evidencedata` remain separate.

Created files:
- backend/app/api/evidence.py
- backend/app/services/evidence_hashing.py
- backend/app/services/evidence_intake.py
- backend/app/services/evidence_signatures.py
- backend/app/services/evidence_storage.py
- backend/migrations/versions/0003_evidence_intake_integrity.py
- backend/tests/test_evidence_intake_api.py
- backend/tests/test_evidence_migration.py
- backend/tests/test_evidence_primitives.py
- backend/tests/test_database_configuration.py
- docs/evidence-intake-v2.1.md
- frontend/src/features/evidence/EvidenceIntakeControls.tsx

Modified files:
- .dockerignore
- .env.example
- README.md
- backend/app/api/cases.py
- backend/app/api/system.py
- backend/app/core/config.py
- backend/app/core/errors.py
- backend/app/core/middleware.py
- backend/app/core/security.py
- backend/app/domain/enums.py
- backend/app/domain/models.py
- backend/app/domain/roles.py
- backend/app/main.py
- backend/app/schemas.py
- backend/app/services/queries.py
- backend/app/services/records.py
- backend/tests/test_architecture.py
- backend/tests/test_identity_api.py
- backend/tests/test_security.py
- backend/tests/test_system.py
- docker-compose.yml
- docker/backend.Dockerfile
- docker/nginx.conf
- docs/architecture.md
- docs/threat-model-v2.md
- docs/v2.1-evidence-intake-spec.md
- frontend/src/api/client.ts
- frontend/src/api/types.ts
- frontend/src/features/evidence/EvidencePage.tsx
- frontend/src/features/evidence/EvidenceProfileView.tsx
- frontend/src/test/api.test.ts
- frontend/src/test/evidence.test.tsx
- frontend/src/test/fixtures.tsx

Previously completed project-workspace validation (before this archive-completeness repair):
- `backend/.venv/bin/python -m pytest backend/tests/test_database_configuration.py -q -ra` — 1 passed. A generated test-only credential containing `@:/?#%` remains in `PGPASSWORD`; SQLAlchemy/psycopg connection arguments contain host `db`, user/database `veritas`, and no password field.
- `backend/.venv/bin/python -m pytest backend/tests -q -ra` — 162 passed, 2 skipped. Both skips are PostgreSQL-only trigger checks.
- Nginx 1.29.8 syntax check inside the built web image — `nginx -t` passed (`syntax is ok`; `test is successful`).
- `backend/.venv/bin/pytest -q tests/test_architecture.py tests/test_security.py tests/test_evidence_migration.py` — 57 passed.
- `backend/.venv/bin/pytest -q tests/test_evidence_migration.py::test_0003_upgrade_and_downgrade_preserve_the_previous_head` — 1 passed; SQLite upgrade/downgrade/re-upgrade.
- `(cd backend && .venv/bin/alembic heads)` — `0003 (head)`.
- `ruff check backend/app backend/tests` and `ruff format --check backend/app backend/tests` — passed; 50 files already formatted.
- `mypy backend/app` — passed; no issues in 38 source files.
- Frontend `npm ci` — passed using Node v24.15.0.
- Frontend `npm run typecheck`, `npm run lint`, `npm test`, and `npm run build` — passed; 8 test files and 72 tests passed; production build succeeded.
- `git diff --check` — passed.
- `docker compose -p veritas_v2_1 config --format json` — rendered configuration passed secret-redacted checks: DB URL has no password, db/backend password sources match, and volumes remain separate.
- Web image built from `docker/frontend.Dockerfile`; its production `npm run build` succeeded. Nginx syntax checks passed both in the built web image and in the running `veritas_v2_1-web-1` container (`nginx -t`).
- Full stack was started with Compose `up -d --build`, using a temporary second Compose file that set only `build.network: host` because the sandbox Docker daemon has IPv4 forwarding disabled; runtime service configuration was unchanged. The default-network web build had previously stalled at `npm ci`, so the temporary build-only override was used rather than persisted.
- `docker compose -p veritas_v2_1 ps` — db, backend, and web were healthy.
- `GET /health`, `GET /ready`, and `GET /api/v1/system` through `http://127.0.0.1:8080` — all returned HTTP 200.
- Web logs showed Nginx 1.29.8 startup and the three successful requests; no configuration error. Backend logs confirmed Alembic upgrades 0001→0002→0003, seed completion, and Uvicorn startup.
- Nginx boundary audit passed: only `^/api/v1/cases/CASE-[0-9]{3,9}/evidence/EVD-[0-9]{3,9}/objects/EOBJ-[0-9]{3,9}/content$` receives the `100m` body limit and `proxy_request_buffering off`; the global limit remains `1m`. No evidence filesystem path/alias exists in Nginx, the evidence volume is mounted only into backend, and no public GET content/download route was introduced.

Complete-source handoff repair (2026-09-30):
- Compared the source path set to the immediately previous complete 141-file `veritas-v2.1-evidence-intake-ARENA-HANDOFF-DOCKER-DB-URL-FIX.zip`: all 141 paths are present, with no missing or additional source paths.
- `EvidenceIntakeControls.tsx`, `EvidencePage.tsx`, and `EvidenceProfileView.tsx` are present in `frontend/src/features/evidence/` and byte-identical to the previous complete handoff. The earlier 138-file Nginx ZIP omitted them because its packaging filter excluded every path named `evidence`; the corrected packaging preserves source directories and excludes only actual generated/data paths.
- Frontend on Node v24.15.0: `npm ci`, `npm run typecheck`, `npm run lint`, `npm test -- --run`, and `npm run build` — all passed. 8 test files, 72 tests passed; production build succeeded.
- Backend on the Linux Arena environment: `.venv/bin/python -m pytest backend/tests -q -ra` — 162 passed, 2 PostgreSQL-only trigger tests skipped. Ruff check, Ruff format check, and mypy passed (38 source files).
- Docker is unavailable in this repair environment. No Docker build of the repaired archive is claimed; the earlier stack/Nginx results above are from the prior project-workspace validation.
- The completed archive uses repository-root entries (`docker/`, `backend/`, `frontend/`, `docs/`, etc.), not an enclosing project directory.

Verification limits:
- In the earlier project-workspace run, the Docker-managed PostgreSQL service was healthy and runtime migrations completed, but the two test-harness PostgreSQL trigger checks were skipped; those database-trigger assertions remain unverified. SQLite results do not replace them.
- In that earlier run, Docker runtime was exercised for Nginx syntax, stack health, startup logs, and the listed HTTP endpoints. The build-only host-network override was temporary and not added to project source. Docker was unavailable during this archive repair, so the new ZIP was not Docker-built.
- The current environment is POSIX, not Windows. Windows unsupported-fsync behavior was exercised by a focused platform-branch test; no native Windows run was performed. The WinError 1314 conditional skip behavior is present but was not exercised on Windows.
- Do not interpret this handoff as full V2.1 acceptance until PostgreSQL trigger verification and the remaining acceptance checks are completed.

Archive:
- `veritas-v2.1-evidence-intake-ARENA-HANDOFF-COMPLETE-NGINX-FIX.zip` — complete 141-file source tree, with repository-root entries (no enclosing folder), `.env.example`, and this manifest. Excludes `.git`, real `.env`/secrets, `.venv`, `node_modules`, generated caches/build outputs, databases/evidence data, and all ZIP archives. Archive contents were reopened, enumerated, checked for the three frontend evidence files and manifest equality, and passed the ZIP CRC test.

Specification:
docs/v2.1-evidence-intake-spec.md
