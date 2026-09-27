#!/usr/bin/env bash
# One-time local setup: backend virtualenv, frontend dependencies, local .env.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

need() { command -v "$1" >/dev/null 2>&1 || { echo "error: $1 is required" >&2; exit 1; }; }
need python3
need node
need npm

python3 - <<'PY'
import sys
if sys.version_info < (3, 12):
    sys.exit(f"error: Python >= 3.12 required, found {sys.version.split()[0]}")
PY
node -e 'const [a,b]=process.versions.node.split(".").map(Number); if (a<22||(a===22&&b<22)) { console.error(`error: Node >= 22.22 required, found ${process.versions.node}`); process.exit(1); }'

echo "==> Backend: virtualenv + dependencies"
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install --quiet --upgrade pip
backend/.venv/bin/python -m pip install --quiet -e "backend[dev]"

echo "==> Frontend: dependencies (from package-lock.json)"
(cd frontend && npm ci --no-audit --no-fund)

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "==> Created .env from .env.example — set a real local database password in VERITAS_DATABASE_URL."
fi

cat <<'MSG'

Setup complete. Next:
  1. Create a PostgreSQL role and database matching VERITAS_DATABASE_URL in .env
     (or run `docker compose up` to use the bundled stack instead).
  2. scripts/dev.sh     — migrate, seed demonstration data, run API + frontend
  3. scripts/check.sh   — all linters, type checks, tests and the production build
MSG
