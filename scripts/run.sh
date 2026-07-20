#!/usr/bin/env bash
# Best-effort smooth local server profile for Sniper Trades J-Space
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

# Prefer uvloop + httptools when installed (uvicorn[standard])
exec python -m uvicorn main:app \
  --host 127.0.0.1 \
  --port "${PORT:-8000}" \
  --loop uvloop \
  --http httptools \
  --log-level info \
  --no-access-log \
  --timeout-keep-alive 30 \
  --workers 1
