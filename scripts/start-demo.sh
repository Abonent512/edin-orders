#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
if [ ! -d frontend/dist ]; then (cd frontend && npm ci --no-audit --no-fund && npm run build); fi
export DATA_DIR="$PWD/data" STATIC_DIR="$PWD/frontend/dist" DEMO_MODE=true COOKIE_SECURE=false
export ALLOWED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
unset DATABASE_URL
export PYTHONPATH="$PWD/backend"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
