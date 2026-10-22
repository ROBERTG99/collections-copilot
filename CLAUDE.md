# CLAUDE.md — collections-copilot

B2B collections agent: a deterministic priority queue, LLM-assisted next-best-action and SMS drafts with human approval, Twilio SMS, reply classification and promise-to-pay write-back to a ledger through mcp-gateway. Synthetic data only. Architecture: `docs/architecture.md`; decisions: `docs/DECISIONS.md` and `docs/adr/`.

## Map

```text
src/collections_copilot/  api/ agent/ compliance/ scoring/ llm/ integrations/{sms,storage,queue,mcp,email}/
                          db/ ingest/ observability/ evals/ worker/ cli.py config.py
apps/                     dev-oidc/ ledger-mcp/ debtor-sim/        (uv workspace members)
third_party/              mcp-gateway/                              (contract fallback)
web/                      Next.js console + BFF
prompts/                  versioned prompts, policies/tone.md, templates/
data/generator/           synthetic data and eval datasets (gen.v1, seed 4242)
evals/                    datasets/ suites/ rubrics/ reports/ REPORT.md
migrations/               Alembic
deploy/                   wiremock/ mcpgw/ otel/ prometheus/ grafana/ elasticmq.conf
infra/terraform/          modules/ envs/dev/ (never applied)
tests/                    unit/ integration/ contract/ e2e/ perf/ harness/ fixtures/
```

## Make targets

`make bootstrap` · `make up` / `make down` · `make seed` · `make demo` · `make logs` · `make check-fast` (lint + unit, < 60 s) · `make test` (unit, integration, contract; starts deps) · `make test-docker` · `make e2e` · `make eval` · `make eval-live` · `make bench` · `make lint` · `make typecheck` · `make security` · `make tf-check` · `make tf-test` · `make verify` (the gate) · `make verify-clean` · `make gen-types` · `make docs` · `make dev-token` · `make db-shell` · `make clean`.

## Conventions

- Async I/O everywhere; FastAPI + SQLAlchemy 2 async (psycopg 3); LangGraph with `AsyncPostgresSaver` (`thread_id = case_id`).
- Tenant scoping: every table has `tenant_id`; access data only via `db/repositories/*` (tenant is the first argument). Raw SQL outside `db/` is banned by ruff.
- Money is `bigint` cents + `currency`; never floats. Times are `timestamptz`; debtor-local via `zoneinfo`.
- Deterministic code owns the priority score, compliance, amounts, STOP handling and every side-effect permission. The LLM only reads notes/replies and drafts text.
- Only `collections_copilot.llm` imports provider SDKs (import-linter). Model IDs from `CC_MODEL_FAST` / `CC_MODEL_REASONING`.
- Prompts in `prompts/<name>.v<N>.md`; untrusted text only inside `<untrusted_note>` after the instructions.
- Errors are RFC 9457 problem details with `correlation_id`; mutating routes accept `Idempotency-Key`.

## Testing rules

Failing test first. No sleeps (poll with timeout), clocks frozen with `time-machine`, fixed seeds, one tenant per integration test. Never skip, xfail or weaken a test or threshold; record gaps in `docs/KNOWN_LIMITATIONS.md`. Everything runs offline with `CC_LLM_PROVIDER=fake`.

## Commit attribution

Commits and pull requests go out under the repository owner's name only. Do not add Co-Authored-By, Claude-Session or "Generated with Claude Code" trailers. Do not modify `git config user.*` or `GIT_AUTHOR_*` / `GIT_COMMITTER_*`.

## Never

- Run `terraform apply` / `destroy`, deploy to a cloud account, or touch AWS with real credentials.
- Create, publish or change visibility of a GitHub repository; push tags; force-push.
- Send messages to real recipients; use non-synthetic phone numbers (only `+1555…`, `+34555…`).
- Commit secrets or real personal data; edit committed `evals/datasets/*test*` files; lower an eval threshold.
- Send an SMS without an approval record or a matching auto-approval policy (the single STOP confirmation is the one rule-based exception).

## Where things live

Architecture `docs/architecture.md` · decisions `docs/DECISIONS.md` · ADRs `docs/adr/` · known gaps `docs/KNOWN_LIMITATIONS.md` · runbook `docs/runbook.md` · security `docs/security.md` · rules `.claude/rules/` · agents `.claude/agents/` · skills `.claude/skills/`.
