#!/usr/bin/env bash
# SessionStart hook: prepares a Claude Code on the web container (BUILD.md Appendix A).
# Runs only when CLAUDE_CODE_REMOTE=true and is idempotent.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$ROOT"
export PATH="$HOME/.local/bin:$HOME/go/bin:$PATH"
log() { printf '[session-start] %s\n' "$*" >&2; }

# 1. Docker daemon with the registry mirror (Docker Hub direct pulls return 429).
if command -v dockerd >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
  log "starting dockerd"
  mkdir -p /etc/docker
  if [ ! -f /etc/docker/daemon.json ]; then
    echo '{"registry-mirrors":["https://mirror.gcr.io"]}' > /etc/docker/daemon.json
  fi
  (nohup dockerd > /tmp/dockerd.log 2>&1 &)
  for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
fi

# 2. Binary tools (terraform, tflint, trivy, hadolint, k6, actionlint, gitleaks, checkov, ...).
./scripts/install-tools.sh >&2 || log "install-tools failed (continuing)"

# 3. Terraform AWS provider in the implicit local mirror (registry.terraform.io is blocked).
if [ -x ./scripts/tf_provider_mirror.sh ]; then
  ./scripts/tf_provider_mirror.sh >&2 || log "provider mirror failed (continuing)"
fi

# 4. Python and Node dependencies.
uv sync --frozen >&2
pnpm install --frozen-lockfile >&2

# 5. Local env file and PATH for the session.
[ -f .env ] || cp .env.example .env
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$HOME/.local/bin:$HOME/go/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
  # Image builds need host networking to reach the egress proxy (Appendix A); elsewhere the
  # compose default (the bridge) applies, since Compose v5 bake refuses host without a grant.
  echo "export CC_BUILD_NETWORK=host" >> "$CLAUDE_ENV_FILE"
fi
log "ready"
