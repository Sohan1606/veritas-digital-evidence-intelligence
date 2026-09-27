#!/usr/bin/env bash
# Every verification gate, in the order CI runs them. Exits non-zero on the first failure.
# Backend tests use SQLite unless VERITAS_TEST_DATABASE_URL points at PostgreSQL.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/backend/.venv/bin/python"
[[ -x "$PY" ]] || { echo "error: backend/.venv missing — run scripts/setup.sh first" >&2; exit 1; }

step() { printf '\n==> %s\n' "$*"; }

cd "$ROOT/backend"
step "backend: ruff format --check";  "$PY" -m ruff format --check .
step "backend: ruff check";           "$PY" -m ruff check .
step "backend: mypy (strict)";        "$PY" -m mypy app tests
step "backend: pytest";               "$PY" -m pytest -q

cd "$ROOT/frontend"
step "frontend: typecheck";           npm run --silent typecheck
step "frontend: lint";                npm run --silent lint
step "frontend: test";                npm test --silent
step "frontend: build";               npm run --silent build

cd "$ROOT"
step "repository: lint";              "$PY" -m ruff check --config backend/pyproject.toml tests scripts/qa
"$PY" -m ruff format --check --config backend/pyproject.toml tests scripts/qa
step "repository: hygiene tests";     "$PY" -m pytest -q tests

printf '\nAll checks passed.\n'
