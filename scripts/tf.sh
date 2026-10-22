#!/usr/bin/env bash
# make tf-check / make tf-test: everything runs offline against the local
# provider mirror (scripts/install-tools.sh) and the mock provider. Nothing is ever applied:
# `command = apply` appears only inside `terraform test` with mock_provider.
set -euo pipefail
cd "$(dirname "$0")/../infra/terraform"
export PATH="$HOME/.local/bin:$PATH" CHECKPOINT_DISABLE=1 TF_IN_AUTOMATION=1

mode="${1:-check}"
init() {
  (cd envs/dev && terraform init -backend=false -input=false -no-color >/dev/null)
}

case "$mode" in
  test)
    init
    (cd envs/dev && terraform test -no-color)
    ;;
  check)
    terraform fmt -check -recursive -diff .
    init
    (cd envs/dev && terraform validate -no-color)
    (cd envs/dev && terraform test -no-color)
    tflint --recursive --config="$PWD/.tflint.hcl"
    checkov -d . --quiet --compact --framework terraform --config-file .checkov.yaml
    ;;
  *)
    echo "usage: $0 [check|test]" >&2
    exit 2
    ;;
esac
