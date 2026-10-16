# Development

## Environment recorded at bootstrap (2026-10-16)

Claude Code on the web (`CLAUDE_CODE_REMOTE=true`), Linux 6.18 x86_64, 4 vCPU, 15 GiB RAM.

| Tool | Version | Source |
|---|---|---|
| Python | 3.13.16 | preinstalled |
| uv | 0.11.32 | preinstalled |
| Node | 22.22.0 | preinstalled |
| pnpm | 10.28.0 | preinstalled |
| Go | 1.24.7 | preinstalled |
| Docker | 29.8.2 | preinstalled, daemon started per Appendix A |
| PostgreSQL client | 16 | preinstalled |
| Terraform | 1.16.6 | `scripts/install-tools.sh` (releases.hashicorp.com) |
| tflint | 0.64.0 | `scripts/install-tools.sh` (GitHub release) |
| trivy | 0.76.0 | `scripts/install-tools.sh` (GitHub release) |
| hadolint | 2.15.1 | `scripts/install-tools.sh` (GitHub release) |
| k6 | 2.3.0 | `scripts/install-tools.sh` (GitHub release) |
| terraform-docs | 0.24.0 | `scripts/install-tools.sh` (GitHub release) |
| syft | 1.54.1 | `scripts/install-tools.sh` (GitHub release) |
| actionlint | 1.7.12 | `go install` |
| gitleaks | 8.30.1 | `go install` |
| checkov | 3.3.29 | `uv tool install` |
| pre-commit | 4.7.0 | `uv tool install` |
| kubectl, Helm | missing | not needed |

Network notes for this container: github.com web pages and `api.github.com` answer 403 through the agent proxy, but `git ls-remote` and release asset downloads work, so tool versions were resolved with `git ls-remote --tags`. `checkpoint-api.hashicorp.com` is blocked (harmless). `minio/minio` cannot be pulled (see docs/DECISIONS.md).

## Everyday loop

```bash
make bootstrap      # tools, uv sync, pnpm install, .env
make up             # full stack in Docker
make seed && make demo
make check-fast     # lint + unit tests
make test           # unit + integration + contract (needs Docker deps)
```

## Disk budget in cloud sessions

The session has a fixed disk allowance (`df -h /` reported 39G used and 76M free when the first build failed on 2026-10-16). `docker system df` then showed 19.73GB of build cache; when `docker compose build` fails with "no space left on device", run `docker builder prune -af` and `docker image prune -f`.
