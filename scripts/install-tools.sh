#!/usr/bin/env bash
# Idempotently installs the binary toolchain used by collections-copilot into ~/.local/bin.
# Versions are pinned; bump them deliberately (see docs/DEVELOPMENT.md).
set -euo pipefail

BIN="${INSTALL_BIN:-$HOME/.local/bin}"
mkdir -p "$BIN"
export PATH="$BIN:$HOME/go/bin:$PATH"

TERRAFORM_VERSION=1.16.6
TFLINT_VERSION=0.64.0
TRIVY_VERSION=0.76.0
HADOLINT_VERSION=2.15.1
K6_VERSION=2.3.0
TERRAFORM_DOCS_VERSION=0.24.0
ACTIONLINT_VERSION=1.7.12
GITLEAKS_VERSION=8.30.1
SYFT_VERSION=1.54.1
CHECKOV_VERSION=3.3.29
PRE_COMMIT_VERSION=4.7.0
SHELLCHECK_VERSION=0.11.0
AWS_PROVIDER_VERSION=6.68.0

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"   # linux | darwin
ARCH="$(uname -m)"
case "$ARCH" in x86_64|amd64) ARCH=amd64 ;; arm64|aarch64) ARCH=arm64 ;; esac

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

have() { command -v "$1" >/dev/null 2>&1; }
log() { printf '[install-tools] %s\n' "$*"; }
fetch() { curl -fsSL --retry 4 --retry-delay 2 -o "$2" "$1"; }

install_zip() { # name url
  fetch "$2" "$TMP/$1.zip" && (cd "$TMP" && unzip -oq "$1.zip" "$1") && install -m 0755 "$TMP/$1" "$BIN/$1"
}
install_tgz() { # name url
  fetch "$2" "$TMP/$1.tgz" && tar -xzf "$TMP/$1.tgz" -C "$TMP" "$1" && install -m 0755 "$TMP/$1" "$BIN/$1"
}

if ! have terraform; then
  log "terraform $TERRAFORM_VERSION"
  install_zip terraform "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_${OS}_${ARCH}.zip"
fi
if ! have tflint; then
  log "tflint $TFLINT_VERSION"
  install_zip tflint "https://github.com/terraform-linters/tflint/releases/download/v${TFLINT_VERSION}/tflint_${OS}_${ARCH}.zip"
fi
if ! have trivy; then
  log "trivy $TRIVY_VERSION"
  T_OS=Linux; [ "$OS" = darwin ] && T_OS=macOS
  T_ARCH=64bit; [ "$ARCH" = arm64 ] && T_ARCH=ARM64
  install_tgz trivy "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_${T_OS}-${T_ARCH}.tar.gz"
fi
if ! have hadolint; then
  log "hadolint $HADOLINT_VERSION"
  H_OS=Linux; [ "$OS" = darwin ] && H_OS=Darwin
  H_ARCH=x86_64; [ "$ARCH" = arm64 ] && H_ARCH=arm64
  base="https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}"
  fetch "$base/hadolint-${H_OS}-${H_ARCH}" "$TMP/hadolint" \
    || fetch "$base/hadolint-$(echo "$H_OS" | tr '[:upper:]' '[:lower:]')-${H_ARCH}" "$TMP/hadolint"
  install -m 0755 "$TMP/hadolint" "$BIN/hadolint"
fi
if ! have k6; then
  log "k6 $K6_VERSION"
  K_OS=$OS; [ "$OS" = darwin ] && K_OS=macos
  if [ "$OS" = darwin ]; then
    fetch "https://github.com/grafana/k6/releases/download/v${K6_VERSION}/k6-v${K6_VERSION}-${K_OS}-${ARCH}.zip" "$TMP/k6.zip"
    (cd "$TMP" && unzip -oq k6.zip) && install -m 0755 "$TMP/k6-v${K6_VERSION}-${K_OS}-${ARCH}/k6" "$BIN/k6"
  else
    fetch "https://github.com/grafana/k6/releases/download/v${K6_VERSION}/k6-v${K6_VERSION}-${K_OS}-${ARCH}.tar.gz" "$TMP/k6.tgz"
    tar -xzf "$TMP/k6.tgz" -C "$TMP" && install -m 0755 "$TMP/k6-v${K6_VERSION}-${K_OS}-${ARCH}/k6" "$BIN/k6"
  fi
fi
if ! have terraform-docs; then
  log "terraform-docs $TERRAFORM_DOCS_VERSION"
  install_tgz terraform-docs "https://github.com/terraform-docs/terraform-docs/releases/download/v${TERRAFORM_DOCS_VERSION}/terraform-docs-v${TERRAFORM_DOCS_VERSION}-${OS}-${ARCH}.tar.gz"
fi
if ! have syft; then
  log "syft $SYFT_VERSION"
  install_tgz syft "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_${OS}_${ARCH}.tar.gz"
fi
if ! have shellcheck; then  # actionlint lints workflow run: scripts with it
  log "shellcheck $SHELLCHECK_VERSION"
  S_OS=$OS; S_ARCH=x86_64; [ "$ARCH" = arm64 ] && S_ARCH=aarch64
  fetch "https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.${S_OS}.${S_ARCH}.tar.xz" "$TMP/sc.tar.xz"
  tar -xJf "$TMP/sc.tar.xz" -C "$TMP" && install -m 0755 "$TMP/shellcheck-v${SHELLCHECK_VERSION}/shellcheck" "$BIN/shellcheck"
fi
if have go; then
  have actionlint || { log "actionlint $ACTIONLINT_VERSION"; GOBIN="$BIN" go install "github.com/rhysd/actionlint/cmd/actionlint@v${ACTIONLINT_VERSION}"; }
  have gitleaks || { log "gitleaks $GITLEAKS_VERSION"; GOBIN="$BIN" go install "github.com/zricethezav/gitleaks/v8@v${GITLEAKS_VERSION}"; }
else
  log "go missing: skipping actionlint and gitleaks"
fi
if have uv; then
  have checkov || { log "checkov $CHECKOV_VERSION"; uv tool install --quiet "checkov==${CHECKOV_VERSION}"; }
  have pre-commit || { log "pre-commit $PRE_COMMIT_VERSION"; uv tool install --quiet "pre-commit==${PRE_COMMIT_VERSION}"; }
fi

# The AWS provider in Terraform's implicit local mirror, so `terraform init` works offline
# (registry.terraform.io is blocked in the cloud container; releases.hashicorp.com is not).
PROVIDER_DIR="$HOME/.terraform.d/plugins/registry.terraform.io/hashicorp/aws/${AWS_PROVIDER_VERSION}/${OS}_${ARCH}"
if [ ! -d "$PROVIDER_DIR" ]; then
  log "terraform provider hashicorp/aws $AWS_PROVIDER_VERSION"
  fetch "https://releases.hashicorp.com/terraform-provider-aws/${AWS_PROVIDER_VERSION}/terraform-provider-aws_${AWS_PROVIDER_VERSION}_${OS}_${ARCH}.zip" "$TMP/provider.zip"
  mkdir -p "$PROVIDER_DIR" && unzip -oq "$TMP/provider.zip" -d "$PROVIDER_DIR"
fi

log "done; ensure $BIN is on PATH"
