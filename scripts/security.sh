#!/usr/bin/env bash
# make security (BUILD.md §12): gitleaks, bandit, pip-audit, pnpm audit, trivy, hadolint, checkov,
# tflint, actionlint and the licence check. Fails on any high or critical finding that is not
# documented in docs/security.md ("Accepted findings"); lower severities are reported only.
# Offline-friendly: vulnerability databases come through mirror.gcr.io.
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$HOME/go/bin:$PATH"

BANDIT=bandit==1.8.6
PIP_AUDIT=pip-audit==2.9.0
TRIVY_DB=(--db-repository mirror.gcr.io/aquasec/trivy-db
  --java-db-repository mirror.gcr.io/aquasec/trivy-java-db
  --checks-bundle-repository mirror.gcr.io/aquasec/trivy-checks)
IMAGES=(collections-copilot/api:dev collections-copilot/worker:dev collections-copilot/ledger-mcp:dev
  collections-copilot/dev-oidc:dev collections-copilot/web:dev)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

failed=()
notrun=()
check() { # name command...
  local name=$1
  shift
  printf '\n== %s\n' "$name"
  if "$@"; then
    printf 'PASS %s\n' "$name"
  else
    printf 'FAIL %s\n' "$name"
    failed+=("$name")
  fi
}
absent() { # name reason
  printf '\n== %s\nNOT RUN %s: %s\n' "$1" "$1" "$2"
  notrun+=("$1 ($2)")
}
need() { command -v "$1" >/dev/null 2>&1; }

need gitleaks && check gitleaks gitleaks git . --redact --no-banner --exit-code 1 \
  || absent gitleaks "not installed (scripts/install-tools.sh)"

# High severity gates; medium findings are listed for review (docs/security.md).
check bandit uvx --from "$BANDIT" bandit -r src apps third_party -q --severity-level high
uvx --from "$BANDIT" bandit -r src apps third_party -q --severity-level medium --confidence-level medium \
  --exit-zero -f custom --msg-template '  medium: {relpath}:{line} {test_id} {msg}' 2>/dev/null | grep medium: || true

pip_audit() {
  uv export --frozen --no-hashes --no-emit-workspace --no-dev --format requirements-txt >"$TMP/req.txt" \
    && uvx --from "$PIP_AUDIT" pip-audit -r "$TMP/req.txt" --no-deps --disable-pip --progress-spinner off
}
check pip-audit pip_audit

check pnpm-audit bash -c 'cd web && pnpm audit --audit-level high'

if need trivy; then
  check trivy-fs trivy fs "${TRIVY_DB[@]}" --quiet --scanners vuln,secret,misconfig \
    --severity HIGH,CRITICAL --exit-code 1 --skip-dirs node_modules --skip-dirs .venv \
    --skip-dirs web/node_modules --skip-dirs web/.next --skip-dirs infra/terraform/envs/dev/.terraform .
  for image in "${IMAGES[@]}"; do
    if docker image inspect "$image" >/dev/null 2>&1; then
      check "trivy-image ${image}" trivy image "${TRIVY_DB[@]}" --quiet --scanners vuln \
        --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 "$image"
    else
      absent "trivy-image ${image}" "image not built (docker compose build)"
    fi
  done
else
  absent trivy "not installed (scripts/install-tools.sh)"
fi

dockerfiles=(docker/*.Dockerfile)
[ -f web/Dockerfile ] && dockerfiles+=(web/Dockerfile)
need hadolint && check hadolint hadolint "${dockerfiles[@]}" || absent hadolint "not installed"

if [ -d infra/terraform ]; then
  need checkov && check checkov checkov -d infra/terraform --quiet --compact --framework terraform \
    --skip-path '\.terraform' || absent checkov "not installed"
  need tflint && check tflint bash -c 'tflint --chdir=infra/terraform --recursive' || absent tflint "not installed"
else
  absent checkov "infra/terraform does not exist yet (T-45)"
  absent tflint "infra/terraform does not exist yet (T-45)"
fi

if compgen -G ".github/workflows/*.yml" >/dev/null; then
  need actionlint && check actionlint actionlint || absent actionlint "not installed"
else
  absent actionlint ".github/workflows does not exist yet (T-48)"
fi

check licences uv run --frozen python scripts/licence_check.py

printf '\n== summary\n'
for item in "${notrun[@]+"${notrun[@]}"}"; do printf 'not run: %s\n' "$item"; done
if [ ${#failed[@]} -gt 0 ]; then
  printf 'make security: FAIL (%s)\n' "${failed[*]}"
  exit 1
fi
printf 'make security: PASS\n'
