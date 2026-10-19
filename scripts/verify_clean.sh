#!/usr/bin/env bash
# make verify-clean (BUILD.md §19.1-2): clone the committed tree into a temporary folder and, with
# no .env and no provider keys, run make bootstrap && make verify, then the stack checks
# (up, seed, demo, e2e, test-docker) and tear the clone's stack down with its volumes.
#
# The clone runs as compose project "cc-verify", so its volumes never touch the dev stack's
# seeded data. Both stacks publish the same host ports, so the dev stack is stopped first
# (containers only; its volumes stay) and is not restarted afterwards.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
STAMP="$(date +%Y%m%d%H%M%S)"
CLONE="${VERIFY_DIR:-${TMPDIR:-/tmp}/verify-collections-copilot-$STAMP}"
LOG="$CLONE.log"

if [ -n "$(git status --porcelain)" ]; then
  echo "verify-clean checks committed work: commit or stash these changes first" >&2
  git status --short >&2
  exit 2
fi

echo "== stopping the dev stack (volumes kept) to free ports 8300-8399"
docker compose --profile observability --profile jobs down --remove-orphans >/dev/null 2>&1 || true

echo "== cloning $(git rev-parse --short HEAD) into $CLONE"
git clone --quiet "$REPO" "$CLONE"
cd "$CLONE"

# No keys, no .env: everything must run offline with the FakeLLM.
unset ANTHROPIC_API_KEY OPENAI_API_KEY AWS_PROFILE AWS_SESSION_TOKEN CC_LLM_API_KEY || true
export CC_LLM_PROVIDER=fake COMPOSE_PROJECT_NAME=cc-verify
rm -f .env

cleanup() { docker compose -p cc-verify --profile observability --profile jobs down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT

run() {
  echo "== $*"
  "$@" 2>&1 | tee -a "$LOG"
}

run make bootstrap
rm -f .env  # bootstrap copies .env.example; the clean run uses defaults only
run make verify
run docker compose -p cc-verify up -d --wait
run make seed
run make demo
run make e2e
run make test-docker
echo "verify-clean: every step passed in $CLONE (log $LOG)"
