#!/usr/bin/env bash
# make bench: performance budgets, offline. A missed budget fails the build.
#   NFR-2, NFR-4: tests/perf/test_batch_400.py (pytest, Postgres + ElasticMQ, FakeLLM)
#   NFR-1:        tests/perf/queue.k6.js (k6, 20 VUs for 60 s against the seeded compose stack)
# The k6 run recreates the API with a high per-user rate limit (one persona drives 20 VUs; the
# limiter still runs on every request) and restores the default limit on exit.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
COMPOSE="${COMPOSE:-docker compose}"

if ! curl -fsS -o /dev/null http://localhost:8310/healthz 2>/dev/null; then
  ./scripts/time_up.sh
fi
$COMPOSE run --rm seed

echo "== NFR-2, NFR-4: case graph latency and the batch of 400"
uv run --frozen pytest tests/perf -m perf -s -q

echo "== NFR-1: queue and account routes (k6)"
restore() {
  # Recreated from this shell's environment: a stack started with `make observability` loses
  # its trace export here; run `make observability` again afterwards.
  if ! $COMPOSE up -d --wait api >/dev/null 2>&1; then
    echo "WARNING: could not restore the API's default rate limit; run: $COMPOSE up -d --wait api" >&2
  fi
}
trap restore EXIT
CC_RATE_LIMIT_PER_MINUTE=1000000 $COMPOSE up -d --wait api
k6 run --quiet --no-usage-report tests/perf/queue.k6.js
echo "make bench: every budget met"
