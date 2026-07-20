#!/usr/bin/env bash
# Quick latency check against a running server
set -euo pipefail
BASE="${1:-http://127.0.0.1:8000}"
echo "Health: $(curl -s "$BASE/health")"
echo "Ready:  $(curl -s "$BASE/ready")"
for i in 1 2 3 4 5; do
  curl -s -o /tmp/st_res.json -w "run $i: %{time_total}s http=%{http_code}\n" \
    -H 'Content-Type: application/json' \
    -d "{\"query\":\"BTC plan $i\",\"session_id\":\"bench\",\"reuse_session\":true}" \
    "$BASE/research/search"
done
python3 -c "import json; d=json.load(open('/tmp/st_res.json')); print('last latency_ms', d.get('latency_ms'))"
