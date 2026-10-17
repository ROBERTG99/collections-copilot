#!/usr/bin/env bash
# make up (BUILD.md T-44, NFR-6): build and start the full stack, wait until every service with a
# healthcheck is healthy, and fail if that takes longer than UP_BUDGET_S (default 300 s).
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="${COMPOSE:-docker compose}"
BUDGET="${UP_BUDGET_S:-300}"

start=$(date +%s)
$COMPOSE up -d --build --wait --wait-timeout "$BUDGET"
elapsed=$(( $(date +%s) - start ))

$COMPOSE ps --format 'table {{.Service}}\t{{.Status}}'
if [ "$elapsed" -gt "$BUDGET" ]; then
  echo "make up: healthy after ${elapsed}s, over the ${BUDGET}s budget" >&2
  exit 1
fi
echo "make up: every service healthy in ${elapsed}s (budget ${BUDGET}s)"
echo "console http://localhost:8300 · api http://localhost:8310/docs · mail http://localhost:8351"
