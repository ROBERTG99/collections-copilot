# Changelog

All notable changes to this project are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). No release has been tagged yet, so everything is under Unreleased, grouped by the milestones of the build specification ([docs/specs/000-build-spec.md](docs/specs/000-build-spec.md) §8). Dates are commit dates from `git log`; evidence for each task is in [PROGRESS.md](PROGRESS.md).

## [Unreleased]

### Added

#### M0 Bootstrap and harness (2026-10-16)

- Build specification, MIT licence, commit attribution settings and the progress and decision logs.
- Contract-subset fallbacks for evalgate and mcp-gateway in `third_party/`, because their `v0.1.0` releases do not exist yet.
- uv workspace (`apps/dev-oidc`, `apps/ledger-mcp`, `apps/debtor-sim`), tool configuration, `scripts/install-tools.sh` and the Makefile.
- Claude Code harness: `CLAUDE.md`, `AGENTS.md`, ten rules, eight subagents, thirteen skills, `.mcp.json`, and the session-start, guard, format and stop hooks with tests.
- Compose dependencies, the Python images and API health endpoints.
- Walking skeleton from the API through SQS and the worker to the approval interrupt.

#### M1 Domain and data (2026-10-16)

- Data model and the initial Alembic migration, tenant-scoped repositories, and a hash-chained append-only audit log with `cc audit verify`.
- Synthetic data generator gen.v1 (seed 4242) and `cc seed`.
- Ingest of AR aging as JSON or as a presigned S3 CSV upload, idempotent by SHA-256.
- Deterministic priority score with property tests.
- Deterministic compliance engine: contact window, weekly cap, opt-outs, disputes, ledger-only amounts, footer, forbidden phrases and link allowlist.
- Synthetic ledger MCP server and the gateway services.

#### M2 AI core (2026-10-16)

- LLM gateway with FakeLLM, pricing, step records, retries, a circuit breaker and schema validation.
- Claude API, Bedrock Mantle and ChatBedrock adapters, with ADR 0002 on ChatBedrock versus the SDK.
- Versioned prompts, tone policy, fallback templates and output schemas.
- PII redaction before every provider call.
- Next-best-action node with deterministic rules, and the draft and priority rationale nodes.
- Case graph with the Postgres checkpointer, interrupts and an idempotent runner.
- Reply graph with the STOP rule before any model call.
- Promise write-back to the ledger through mcp-gateway with idempotency keys and supervisor approvals.

#### M3 API and services (2026-10-16 to 2026-10-17)

- dev-oidc personas, OIDC verification and the role matrix; queue, account and case endpoints.
- Approval, batch approval (up to 50) and auto-approval settings endpoints.
- Verified Twilio webhooks, the debtor simulator and the replies consumer.
- Case progress over SSE from Redis with `Last-Event-ID` resume.
- Follow-up scheduler with a single leader.
- Gateway approval webhook and supervisor promise decisions.
- Ops metrics, audit log and opt-out endpoints.
- Body, rate and idempotency limits, and a Schemathesis contract test.

#### M4 Frontend (2026-10-16 to 2026-10-17)

- Next.js console with an encrypted backend-for-frontend session.
- Prioritized queue and account detail screens.
- Case page with live progress and the approval drawer with diff and live post-check.
- Replies, promises, audit, ops and settings screens.
- axe checks on every screen at two widths, and screenshots in `docs/images/`.

#### M5 Evals (2026-10-17)

- `evals.v1` datasets with labels by construction (NBA, replies, adversarial compliance, tone calibration).
- Eval suites, custom metrics and targets, a gate per suite against a committed baseline, and the generated `evals/REPORT.md`.
- Tone rubric, judge calibration and the budgeted live eval runner.

#### M6 Observability and security (2026-10-17)

- Traces for every request, graph node and model call; Prometheus metrics; Grafana dashboard and alert rules in the `observability` compose profile.
- Threat model in `docs/security.md`, `make security` and log hygiene checks.

#### M7 Docker, infrastructure and CI (2026-10-17 to 2026-10-18)

- Terraform modules and `envs/dev`, tested offline with a mock provider and never applied.
- Web image, the web compose service and the containerised test run (`make test-docker`).
- `make bench` with the k6, case-graph and batch-of-400 budgets.
- markdownlint configuration and the Python coverage gate.
- GitHub Actions workflows, Dependabot, CODEOWNERS and repository templates.
- `make verify-clean` in an isolated compose project.
- Console component and library tests with a 70% line coverage gate.

#### M8 Docs and demo (2026-10-18)

- `make demo` runs the four user journeys of spec §3.4 and checks the state after each step; `docs/images/demo.webm` records the main journey.
- README, architecture, AI development, evaluation, security, runbook, design FAQ, contributing guide, known limitations, verification procedure and this changelog; ADRs 0001 to 0009 (T-50).
- Operations commands `cc dlq peek`, `cc replay-dlq` and `cc dev-token`, the chain head lines printed by `cc audit verify`, and the SQS contract test against ElasticMQ and moto (T-50).

#### M9 Reviews and verification (2026-10-18)

- Final reviews by the reviewer, security, compliance and Terraform subagents until the reviewer's verdict was "ship" (T-51), and the clean-clone verification recorded in [docs/verification.md](docs/verification.md) (T-52).
- A CI `security` job (gitleaks over the whole history, pip-audit, `pnpm audit`), and migration 0003 for scheduled promise write-back retries.

### Changed

- Compliance rules for amounts and links fail closed, and text rules read NFKC-normalised text (M1 and M6 reviews).
- Every SMS send re-reads the case, the draft, its approval record and the debtor's facts from the database (M2 review).
- The API container runs several uvicorn processes, and the account detail returns the newest 20 cases (M7, performance budgets).
- The ALB routes only `/api/v1/webhooks/twilio/*` to the API, and Cognito access tokens carry the tenant through a Pre Token Generation trigger; the API publishes the agent JWKS at `/.well-known/agent-jwks.json` for KMS-signed gateway tokens (M7 reviews).

### Fixed

- M0 review findings in the gateway, evalgate, queue parsing and hooks.
- M1 review findings in compliance, ingest and the ledger, including numeric invoice numbers that masked amounts.
- M2 review findings: stale facts at send time, idempotent replies, STOP confirmations, write-backs and case runs, provider error mapping, Spanish local number redaction, STOP phrasing, the contact cap and approvals.
- Integer parameters that map to `bigint` and `integer` columns are bounded (found by Schemathesis).
- M3 review findings in webhooks, limits, the scheduler and auth.
- Write-back only of amounts the debtor wrote, and a wider STOP rule (found by the replies eval dev split).
- M4 UX, review and BFF security findings.
- M5 review and eval analysis findings.
- S3 reads continue to the end of the body within the size cap.
- M7 code review and Terraform review findings.
- M8 review findings: `cc replay-dlq` keeps raw bodies and attributes, and the demo's STOP journey cancels a waiting draft.
- T-51 review findings: a redelivered resume finishes a run that crashed mid-node, a stored auto-approval survives a crash, an analyst's edit is never overridden by auto-approval, template drafts always need a human, and transient ledger failures are retried with back-off instead of failing.

### Security

- T-51 security audit findings: account names are redacted in every escaped form and normalised at ingest, phone numbers are redacted in national and grouped forms, ingest amounts are bounded plain decimals, gateway approval ids are validated, the auto-approval ceiling is bounded and fails closed, and release images build without the shared cache.
- M6 review and security audit findings: exception messages kept off exported spans, Unicode look-alike links and invisible characters blocked, signed and replay-checked gateway webhooks, idempotency failing closed, synthetic numbers enforced outside production, and log redaction of tokens and phone numbers.
