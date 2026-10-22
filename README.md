# collections-copilot

A B2B collections assistant. It ranks past-due accounts with a deterministic priority score, lets an LLM read account notes and draft SMS reminders, holds every draft for human approval, sends through a Twilio adapter, classifies the debtor's reply and writes promises-to-pay back to a ledger through mcp-gateway. Everything runs offline with a deterministic fake model and local doubles for Twilio, Cognito, SQS, S3 and the ledger. All data is synthetic.

![Prioritized queue: accounts ordered by the deterministic score, with next-best-action chips, compliance badges and case status (synthetic data)](docs/images/01-queue.png)

Demo video of the main journey, recorded by `web/tests/e2e/demo.spec.ts` against the local stack (`CC_RECORD_DEMO=1`, see `.claude/skills/demo-capture/SKILL.md`): [docs/images/demo.webm](docs/images/demo.webm). Other screens: [account detail](docs/images/02-account.png), [case](docs/images/03-case.png), [approval drawer](docs/images/04-approval-drawer.png), [replies](docs/images/05-replies.png), [promises](docs/images/06-promises.png), [audit log](docs/images/07-audit.png), [ops dashboard](docs/images/08-ops.png), [settings](docs/images/09-settings.png).

## Why

The target user is a collections analyst who owns a few hundred past-due accounts, sorts a spreadsheet by age, writes each SMS by hand and retypes outcomes into the ledger. Accounts slip past their contact window and some messages go out at the wrong local hour.

The design splits the work by risk:

- **The LLM** reads free-text notes and replies, proposes a next-best action with the notes it relied on, drafts a tone-appropriate SMS in English or Spanish and classifies replies.
- **Deterministic code** owns the priority score, every compliance rule (contact window, weekly cap, opt-outs, disputes, ledger-only amounts, footer, phrases, links), money amounts, STOP handling and every permission to cause a side effect.
- **A human** approves every SMS unless a narrow auto-approval policy matches, and a supervisor approves large or distant promises before they reach the ledger.

## Architecture at a glance

```mermaid
flowchart LR
  B[Browser] --> WEB[web: Next.js console and BFF]
  WEB -->|Bearer JWT| API[api: FastAPI]
  API --> PG[(Postgres: data, checkpoints, audit)]
  API -->|cc-tasks, cc-replies| Q[[SQS / ElasticMQ]]
  Q --> WK[worker: LangGraph case and reply graphs]
  WK --> LLM{{Bedrock, Claude API or FakeLLM}}
  WK -->|approved SMS only| TW[Twilio / WireMock]
  WK -->|MCP + agent JWT| GW[mcp-gateway] --> LED[ledger-mcp]
  TW -->|signed webhooks| API
```

- The **web** console is also the backend-for-frontend: the browser holds an encrypted httpOnly session cookie, never a token.
- The **api** serves REST under `/api/v1`, SSE progress, verified Twilio and gateway webhooks, RBAC and tenant scoping, and records human decisions.
- The **worker** runs the case graph (score, compliance pre-check, next-best action, draft, post-check, approval interrupt, send, follow-up, wait for reply) and the reply graph (STOP rule before any model call, classification, promise validation, write-back) with LangGraph's Postgres checkpointer, plus the follow-up scheduler.
- **mcp-gateway** checks the agent JWT, the tool policy and the idempotency key, and holds large promises for a supervisor.

Details, diagrams of both graphs, the send path and its guards, the data model, queues, observability and the AWS layout: [docs/architecture.md](docs/architecture.md).

## Quickstart

Prerequisites: Docker with Compose v2 and GNU make for the stack; [uv](https://docs.astral.sh/uv/) for `make demo`. `make bootstrap`, needed only for the test and lint targets, also needs Node 22 with pnpm (and Go for actionlint and gitleaks) and installs the remaining tools into `~/.local/bin`. No API keys are needed.

```bash
git clone https://github.com/ROBERTG99/collections-copilot.git
cd collections-copilot
make up     # builds the images, starts every service and waits until all are healthy
make seed   # loads the synthetic dataset (gen.v1, seed 4242)
```

Open `http://localhost:8300` and sign in as `ana.analyst` (the dev-oidc persona picker has no password). Other personas: `sam.supervisor` (batch approval, auto-approval settings, held promises), `adi.admin` (opt-outs) and `tia.tenantb` (an analyst in a second tenant, who sees none of tenant A's data).

The same without make:

```bash
docker compose up -d --wait
docker compose run --rm seed
```

Then run the scripted journeys against the running stack:

```bash
uv sync --frozen   # once, if you skipped make bootstrap
make demo
```

`make demo` (`scripts/demo.py`) imports its own synthetic accounts and checks the state after every step of four user journeys: the main journey (edit a draft, approve, send, the simulated debtor promises to pay, one ledger write, a follow-up), a supervisor batch approval with auto-approval tightened to 0.95, a USD 8,000 promise held for approval and written exactly once, and a STOP reply. `make down` stops the stack; `make clean` also removes its volumes.

| Service | Local address |
|---|---|
| Console (web BFF) | `http://localhost:8300` |
| API and OpenAPI docs | `http://localhost:8310/docs` |
| Mailpit (supervisor e-mails) | `http://localhost:8351` |
| dev-oidc (Cognito double) | `http://localhost:8345` |
| Jaeger, Prometheus, Grafana (`make observability`) | `http://localhost:8392`, `http://localhost:8390`, `http://localhost:8391` |

Every port is bound to 127.0.0.1 in the 8300–8399 range; the full list is in [compose.yaml](compose.yaml) and [docs/architecture.md](docs/architecture.md).

## Results

Every number in this section is copied from a generated report or from the output of the command named next to it.

### Offline evals (test split)

Source: [evals/REPORT.md](evals/REPORT.md), generated by `scripts/eval_report.py` (evalgate v0.1.0) on 2026-10-22 at commit `ab7b186` (clean tree), mode offline, model `fake` (FakeLLM). Counters are the raw totals under each suite in the report.

| Suite (cases) | Metric | Value | Threshold | Gate |
|---|---|---|---|---|
| `nba` (120) | `nba_accuracy` | 0.9750 | ≥ 0.85 | pass |
| | `nba_schema_validity` | 1.0000 | = 1.0 | pass |
| | `citations_supported` | 0.9434 | informational | |
| | graph latency p95 | 21 ms | ≤ 300 ms | pass |
| `replies` (80) | `reply_macro_f1` | 0.9543 | ≥ 0.90 | pass |
| | `stop_recall` | 1.0000 | = 1.0 | pass |
| | `reply_schema_validity` | 1.0000 | = 1.0 | pass |
| | `unauthorized_writebacks` | 0 | = 0 | pass |
| | `pii_leaked` | 0 | = 0 | pass |
| | `authorized_writeback_recall` | 1.0000 | ≥ 0.95 | pass |
| `compliance` (30) | `compliance_violations` | 0 | = 0 | pass |
| | `unauthorized_writebacks` | 0 | = 0 | pass |
| | `pii_leaked` | 0 | = 0 | pass |
| | `sends_when_allowed` | 1.0000 | = 1.0 | pass |

**What these numbers mean.** Offline runs replace the model with FakeLLM, a deterministic keyword reader; everything else is production code. They show that the code owning every side effect (score, compliance engine, STOP rule, approval and write-back guards, schema validation, PII redaction) behaves correctly end to end, and they catch regressions. They are not estimates of model quality. Live evals (`make eval-live`, within `LIVE_EVAL_BUDGET_USD`) need a provider key and are not part of the committed report. See [docs/evaluation.md](docs/evaluation.md).

### Performance budgets

Source: `make bench` on 2026-10-19 at `a16aef7` on the local compose stack, offline, on a macOS host (10 CPUs, Docker Desktop with 10 CPUs and 8 GB) that also ran k6. Earlier `make bench` runs on a Linux host measured the account route at 174.41 ms and 274.77 ms on 2026-10-18 (docs/KNOWN_LIMITATIONS.md, "Performance").

| Requirement | Result | Budget |
|---|---|---|
| NFR-1 `GET /api/v1/queue?limit=50` p95 (k6, 20 VUs for 60 s, 400 accounts) | 218.43 ms | < 300 ms |
| NFR-1 `GET /api/v1/accounts/{id}` p95 | **254.16 ms (missed)** | < 200 ms |
| NFR-1 throughput and errors | 245 req/s, 0 failed requests | |
| NFR-2 case graph to approval interrupt p95 (the 81 of 100 cases that paused for approval) | 127 ms | < 300 ms |
| NFR-4 400 accounts to approval interrupts | 29.8 s | < 180 s |

The API ran 4 uvicorn processes, and k6 opened a new connection per request so the kernel spread requests over them (docs/DECISIONS.md). The batch of 400 used four single-slot consumers in one process, so its time is an upper bound for four worker processes. The account-route gap is in docs/KNOWN_LIMITATIONS.md ("Performance").

### Startup

Source: `make up` (`scripts/time_up.sh`, 13 services including web) on 2026-10-19: the images are built first, then `docker compose up --wait` brings every service to healthy in 66 s against the NFR-6 budget of 300 s (106 s on 2026-10-18 on a Linux host).

### Tests and coverage

Source: `make verify-clean` (a fresh clone of the committed tree) on 2026-10-19 at `a16aef7`.

- Python: `make test` runs unit, harness, integration and contract tests with coverage and then the gate in `scripts/coverage_gate.py`. All 722 passed and the gate passed on every package: scoring 100.00%, compliance 99.33%, agent 92.85%, llm 95.48%, api 85.65%, overall 86.87% (gates: ≥ 85% on each core package, ≥ 70% overall).
- Web: `cd web && pnpm run test:coverage` reported 99.72% of lines over 161 Vitest tests; `web/vitest.config.ts` fails the run below the 70% line gate.
- Containers: `make test-docker` ran 722 tests in the tools image, then the 4 stack tests marked `docker` from the host, exit 0.
- End to end: `make e2e` ran 35 Playwright tests (with axe) against the seeded stack, all passed.

### Infrastructure and security checks

Source: the same `make verify-clean` run on 2026-10-19.

- `make tf-check` (fmt, validate, `terraform test` with a mock provider, tflint, checkov), fully offline: 28 Terraform tests passed; checkov 341 passed and 32 skipped, each skip with an inline reason. The Terraform has never been applied to an account.
- `make security` (gitleaks, bandit, pip-audit, pnpm audit, trivy fs and all 8 images, hadolint, checkov, tflint, actionlint, licences): every check passed. Accepted findings, each with its reason, are listed in [docs/security.md](docs/security.md).

## Make targets

| Target | What it does |
|---|---|
| `make bootstrap` | Installs toolchains and dependencies, creates `.env` from `.env.example` |
| `make up` / `make down` | Builds the images, then starts the full stack and times only `docker compose up --wait` against the 300 s startup budget (NFR-6) / stops it |
| `make seed` | Loads the synthetic dataset (`docker compose run --rm seed`, gen.v1, seed 4242) |
| `make demo` | Runs the four scripted user journeys against the running stack, offline |
| `make logs` | Follows the logs of every service |
| `make check-fast` | Lint, types and unit tests |
| `make test` | Unit, integration and contract tests on the host, with the coverage gates |
| `make test-docker` | The Python suites inside the tools image, then the stack tests |
| `make e2e` | Playwright end-to-end tests with axe against the running stack |
| `make eval` | Offline evals with thresholds; writes `evals/REPORT.md` |
| `make eval-live` | Live evals with real models within `LIVE_EVAL_BUDGET_USD` |
| `make bench` | Performance tests with budgets (k6, case graph, batch of 400) |
| `make lint` / `make typecheck` / `make security` | Each group of checks on its own |
| `make tf-check` / `make tf-test` | fmt, validate, test, tflint, checkov / `terraform test` only, offline |
| `make observability` | The stack plus Jaeger, Prometheus and Grafana, with trace export on |
| `make verify` | lint, typecheck, test, security, tf-check, eval and bench: the single gate |
| `make verify-clean` | `make verify` and the stack checks in a fresh clone |
| `make docs` | Regenerates OpenAPI, frontend types, Terraform module READMEs and screenshots |
| `make gen-types` | Exports OpenAPI and regenerates `web/lib/api/schema.d.ts` |
| `make record-fixtures` | Records live LLM cassettes within budget (needs a provider key) |
| `make dev-token` / `make db-shell` | Prints a dev agent JWT for the ledger / opens psql on the dev database |
| `make clean` | Removes containers, volumes, caches and build outputs |

## Repository map

```text
src/collections_copilot/  api/ agent/ compliance/ scoring/ llm/ integrations/{sms,storage,queue,mcp,email}/
                          db/ ingest/ observability/ evals/ worker/ cli.py config.py
apps/                     dev-oidc/ ledger-mcp/ debtor-sim/          (uv workspace members)
third_party/              mcp-gateway/                                (contract fallback)
web/                      Next.js console and BFF, Vitest and Playwright tests
prompts/                  versioned prompts, policies/tone.md, fallback templates/
data/generator/           synthetic data and eval datasets (gen.v1, seed 4242)
evals/                    datasets/ suites/ rubrics/ reports/ REPORT.md
migrations/               Alembic
deploy/                   wiremock/ mcpgw/ jaeger/ prometheus/ grafana/ postgres/ elasticmq.conf
infra/terraform/          modules/ envs/dev/ (tested offline, never applied)
scripts/                  demo, bench, eval report, security scan, startup timer, verify-clean
tests/                    unit/ integration/ contract/ e2e/ perf/ harness/ fixtures/
.claude/                  Claude Code configuration: rules, hooks, agents, skills
docs/                     adr/ images/ and the documents below
```

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Components, graphs, send path, data model, queues, observability, AWS layout |
| [docs/evaluation.md](docs/evaluation.md) | Datasets, suites, gates and what offline numbers mean |
| [evals/REPORT.md](evals/REPORT.md) | The latest generated eval report |
| [docs/security.md](docs/security.md) | Threat model (STRIDE, OWASP LLM Top 10), scans, accepted findings |
| [docs/runbook.md](docs/runbook.md) | Deploy, rollback, DLQ replay, secret rotation |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Toolchain and everyday development loop |
| [docs/design-faq.md](docs/design-faq.md) | Fifteen design questions answered with `file:line` references |
| [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) | What is not done, or done with a documented compromise |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Engineering decision log, with the alternatives considered |
| [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CHANGELOG.md](CHANGELOG.md), [LICENSE](LICENSE) | Contributing, vulnerability reporting, changes, licence |

Architecture decision records live in [docs/adr/](docs/adr/), each naming the rejected alternative:

- [0001 Bedrock as the primary provider versus the direct Anthropic API](docs/adr/0001-bedrock-primary-vs-anthropic-api.md)
- [0002 ChatBedrock versus the SDK in nodes](docs/adr/0002-chatbedrock-vs-sdk.md)
- [0003 ECS Fargate versus EKS versus Lambda for the worker](docs/adr/0003-fargate-worker-vs-eks-vs-lambda.md)
- [0004 SQS versus a Redis queue](docs/adr/0004-sqs-vs-redis-queue.md)
- [0005 Deterministic compliance outside the LLM](docs/adr/0005-deterministic-compliance-outside-the-llm.md)
- [0006 LangGraph checkpointer with interrupts versus a custom state machine](docs/adr/0006-langgraph-checkpointer-vs-custom-state-machine.md)
- [0007 Next.js BFF versus a Vite SPA](docs/adr/0007-nextjs-bff-vs-vite-spa.md)
- [0008 Terraform versus CDK](docs/adr/0008-terraform-vs-cdk.md)
- [0009 ElasticMQ, MinIO and WireMock versus LocalStack](docs/adr/0009-elasticmq-minio-wiremock-vs-localstack.md)

## Status and limitations

The account-route budget of NFR-1 is not met on the benchmark host (see Performance budgets above). mcp-gateway runs from the contract-compatible fallback in `third_party/mcp-gateway` until its first tagged release; evalgate is consumed from its v0.1.0 tag. Everything else that is missing or done with a documented compromise is in [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md).

## Development

The project uses an AI-assisted workflow: the Claude Code configuration lives in [CLAUDE.md](CLAUDE.md) and `.claude/` (rules, hooks, review agents and skills), and every change is gated by the test suites, the offline evals and the CI workflows in `.github/workflows/`. See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Licence

MIT, see [LICENSE](LICENSE). Copyright ROBERTG99.

Synthetic data only: the seeded generator (gen.v1, seed 4242) produces the seed data and the eval datasets, `make demo` (`scripts/demo.py`) imports its own scripted accounts on every run, and the debtor simulator's replies are scripted personas; phone numbers are in the reserved `+1555…` and `+34555…` ranges, and e-mail addresses use `example` domains. No real person, company or debtor appears in this repository, and nothing is ever sent to a real recipient.
