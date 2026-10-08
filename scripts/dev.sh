#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x backend/.venv/bin/python || ! -d frontend/node_modules ]]; then
  echo '请先按 README 安装前后端依赖。' >&2
  exit 1
fi
(cd backend && .venv/bin/python -m alembic upgrade head)
backend/.venv/bin/python -m uvicorn app.run:app --app-dir backend --host 127.0.0.1 --port 8000 &
api_pid=$!
cleanup() { kill "$api_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
npm run dev --prefix frontend
