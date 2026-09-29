#!/usr/bin/env bash
# Local development: apply migrations, load demonstration data, run API and frontend.
# The API listens on 127.0.0.1:8000; open the frontend at http://localhost:5173.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[[ -f .env ]] || { echo "error: .env missing — run scripts/setup.sh first" >&2; exit 1; }
[[ -x backend/.venv/bin/python ]] || { echo "error: backend/.venv missing — run scripts/setup.sh first" >&2; exit 1; }

cd backend
echo "==> Applying migrations"
.venv/bin/alembic upgrade head
echo "==> Loading demonstration data (idempotent; refused in production)"
.venv/bin/python -m app.seed

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "==> Starting API on http://127.0.0.1:8000"
.venv/bin/uvicorn app.main:get_app --factory --host 127.0.0.1 --port 8000 --reload --reload-dir app &
pids+=($!)

echo "==> Starting frontend on http://localhost:5173"
# exec the Vite binary directly (not via npm, which does not forward signals) so the
# cleanup trap reaches the server itself and no orphan keeps port 5173.
(cd "$ROOT/frontend" && exec ./node_modules/.bin/vite) &
pids+=($!)

wait -n "${pids[@]}"
