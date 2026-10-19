#!/usr/bin/env bash
# make e2e (BUILD.md T-36): the Playwright suite (+ axe) against the running, seeded stack.
# The suite drives the console as one analyst and, on a fast host, issues more than the API's
# default 120 requests per user per minute, so a page would show the 429 problem instead of
# the screen under test. Like `make bench`, the API is recreated with a high per-user limit for
# the run (the limiter still runs on every request; its 429 path is covered by the API tests)
# and restored on exit.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="${COMPOSE:-docker compose}"
PNPM="${PNPM:-pnpm}"

restore() {
  if ! $COMPOSE up -d --wait api >/dev/null 2>&1; then
    echo "WARNING: could not restore the API's default rate limit; run: $COMPOSE up -d --wait api" >&2
  fi
}
trap restore EXIT
CC_RATE_LIMIT_PER_MINUTE="${E2E_RATE_LIMIT_PER_MINUTE:-100000}" $COMPOSE up -d --wait api
cd web && $PNPM exec playwright test "$@"
