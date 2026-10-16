# PROGRESS

## Needs the maintainer

- evalgate `v0.1.0` tag does not exist (`git ls-remote --tags https://github.com/ROBERTG99/evalgate refs/tags/v0.1.0` printed nothing on 2026-10-16); the contract subset lives in `third_party/evalgate/`. Publish the tag and switch the dependency (see docs/DECISIONS.md).
- mcp-gateway `v0.1.0` tag and `ghcr.io/robertg99/mcp-gateway:v0.1.0` image do not exist (`docker pull` → `denied`); the contract subset lives in `third_party/mcp-gateway/` with a YAML policy. Publish the image and switch compose (see docs/DECISIONS.md).
- Maintainer review of 40 NBA cases and 20 drafts from the eval datasets (§11).

## Commit attribution evidence (§1.1)

Identity variables `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL` are all defined (Claude Code on the web).

```text
$ git log -1 --format='%an <%ae> | %cn <%ce>%n%B'   # on commit "chore: configure commit attribution"
ROBERTG99 <54124633+ROBERTG99@users.noreply.github.com> | ROBERTG99 <54124633+ROBERTG99@users.noreply.github.com>
chore: configure commit attribution
```

## Tasks

| ID | Milestone | Title | Status | Evidence |
|---|---|---|---|---|
| T-01 | M0 | Bootstrap, attribution evidence, progress files | done | `uv run pytest tests/harness/test_repo_layout.py` → 4 passed; §1.1 evidence above |
| T-02 | M0 | Resolve evalgate and mcp-gateway | done | `uv run python -c "import evalgate"` → 0.1.0; `pytest tests/harness/test_dependencies.py` → 1 passed (fallbacks in third_party/, see DECISIONS) |
| T-03 | M0 | uv workspace, tool config, Makefile | done | `make bootstrap && make check-fast` → ruff clean, import-linter 2 kept, 9 passed |
| T-04 | M0 | Claude harness files | done | `uv run pytest tests/harness` → 13 passed (CLAUDE.md 54 lines, 10 rules, 8 subagents, 13 skills, .mcp.json) |
| T-05 | M0 | Hooks | done | `uv run pytest tests/harness/test_hooks.py` → 25 passed; `CLAUDE_CODE_REMOTE=true .claude/hooks/session_start.sh` → ready in 0.8 s |
| T-06 | M0 | Compose dependencies and api health | done | `docker compose up -d --wait` → 7 services healthy; `pytest tests/integration/test_compose_health.py` → 1 passed |
| T-07 | M0 | Walking skeleton | done | `make demo` (scripts/demo.py) → case awaiting_approval in Docker; `pytest tests/integration/test_walking_skeleton.py` failed before worker existed, now 1 passed |
| T-08 | M1 | Models and migration 0001 | done | `pytest tests/integration/db/test_migrations.py` → failed before 0001 existed, now 1 passed (upgrade, constraints, append-only trigger, downgrade base) |
| T-09 | M1 | Tenant-scoped repositories | done | `pytest tests/integration/db/test_tenant_scoping.py` → collection error before repositories, now 2 passed; ruff TID251 bans sqlalchemy.text outside db/ |
| T-10 | M1 | Audit chain and `cc audit verify` | done | `pytest tests/unit/db/test_audit_chain.py` → 4 passed incl. Hypothesis single-byte property; `pytest tests/integration/db/test_audit_cli.py` → exit 0 intact, exit 1 first_bad_seq after tamper |
| T-11 | M1 | Synthetic generator gen.v1 | done | `pytest tests/unit/generator` → 4 passed (digest stable for seed 4242); `make seed` (docker compose run --rm seed) → acme-distribution 400 accounts digest 874543310ca9ffbe, tenant-b-supplies 40 digest 37383436757dd895; rerun unchanged |
| T-12 | M1 | Ingest via JSON and presigned S3 upload | done | `pytest tests/integration/test_ingest.py` → 5 passed (duplicate 400-row file returns original batch; negative amount rejected at row 4; 413 over 10 MB); `pytest tests/contract/test_s3_contract.py` → 2 passed (MinIO, moto) |
| T-13 | M1 | Priority score | done | `pytest tests/unit/scoring/test_priority.py` → collection error before module, now 5 passed (incl. Hypothesis monotonicity); formula in docs/architecture.md |
| T-14 | M1 | Compliance engine | done | `pytest tests/unit/compliance` → collection errors before engine, now 33 passed (Hypothesis: never outside window/over cap/after opt-out); compliance/ coverage 100% |
| T-15 | M1 | Ledger MCP server | done | `pytest tests/integration/test_ledger_mcp.py` → collection error before server, now 3 passed (schemas match §4.4; same key → same ledger_ref, 1 row); docker smoke: ledger.get_balance through mcp-gateway with dev-issuer JWT → untrusted=true |
| T-16 | M2 | LLM gateway, FakeLLM, cassettes, step records | done | `pytest tests/unit/llm/test_gateway.py tests/unit/llm/test_fake.py` → collection error before gateway, now 22 passed (retries only 408/409/429/5xx, refusal/max_tokens, breaker, cost); import-linter: provider SDKs only in llm |
| T-17 | M2 | Bedrock Mantle, Anthropic, ChatBedrock adapters | done | `pytest tests/unit/llm/test_providers_recorded.py tests/unit/llm/test_chatbedrock_conformance.py` → 13 passed offline (Appendix B request shape for Claude API and Bedrock Mantle; ChatBedrock 7/9 checks → Mantle); docs/adr/0002-chatbedrock-vs-sdk.md |
| T-18 | M2 | PII redaction | todo | |
| T-19 | M2 | Prompts v1 and schemas | todo | |
| T-20 | M2 | Next-best-action node | todo | |
| T-21 | M2 | Rationale and draft nodes | todo | |
| T-22 | M2 | Case graph, checkpointer, interrupt | todo | |
| T-23 | M2 | Reply graph and STOP rule | todo | |
| T-24 | M2 | MCP client and promise write-back | todo | |
| T-25 | M3 | OIDC, dev-oidc, RBAC | todo | |
| T-26 | M3 | Queue, accounts, cases endpoints | todo | |
| T-27 | M3 | Approvals and auto-approval policy | todo | |
| T-28 | M3 | Twilio adapter, webhooks, debtor simulator | todo | |
| T-29 | M3 | SSE progress | todo | |
| T-30 | M3 | Scheduler and follow-ups | todo | |
| T-31 | M3 | Gateway approval webhook and promise approval | todo | |
| T-32 | M3 | Ops metrics and audit endpoints | todo | |
| T-33 | M3 | Limits, problem details, Idempotency-Key, Schemathesis | todo | |
| T-34 | M4 | Web scaffold, tokens, BFF session | todo | |
| T-35 | M4 | Queue and Account detail | todo | |
| T-36 | M4 | Approval drawer with diff and progress | todo | |
| T-37 | M4 | Replies, Promises, Audit, Ops, Settings | todo | |
| T-38 | M4 | Accessibility, responsive, screenshots | todo | |
| T-39 | M5 | Eval datasets | todo | |
| T-40 | M5 | Suites, custom metrics, gate, report | todo | |
| T-41 | M5 | Judge calibration and live runner | todo | |
| T-42 | M6 | Traces, logs, metrics, dashboards | todo | |
| T-43 | M6 | Threat model, scans, log hygiene | todo | |
| T-44 | M7 | Images, full compose, test compose | todo | |
| T-45 | M7 | Terraform data-plane modules | todo | |
| T-46 | M7 | Terraform compute, identity, observability, envs/dev | todo | |
| T-47 | M7 | Performance budgets | todo | |
| T-48 | M7 | CI workflows | todo | |
| T-49 | M8 | Demo scenario and capture | todo | |
| T-50 | M8 | README, ADRs, §18 documents | todo | |
| T-51 | M9 | Reviews and fixes | todo | |
| T-52 | M9 | Final verification | todo | |

## Global Definition of Done

- [ ] `make verify` passes from a fresh clone (§19) with no API keys set.
- [ ] `docker compose up -d --wait` brings every service to healthy; `make seed` and `make demo` run the scripted scenario end to end.
- [ ] `make test-docker` runs the full test suite inside containers and passes.
- [ ] Unit, integration, contract and E2E suites pass; line coverage is at least 85% on core packages and 70% overall.
- [ ] Offline evals meet every threshold in §11, and `evals/REPORT.md` is generated by script from the latest run.
- [ ] Lint, format and type checks are clean.
- [ ] Security scans report no high or critical findings, or each one is documented with a reason in `docs/security.md`.
- [ ] Every workflow in §17 exists and passes actionlint; if a GitHub remote exists and CI results can be observed, the pushed branch is green.
- [ ] Every screen in §13 is implemented with loading, empty, error and success states; axe reports no serious violations; Playwright captured the screenshots in `docs/images/`.
- [ ] The harness in §7 is complete: CLAUDE.md, AGENTS.md, settings, hooks with passing tests, rules, subagents, skills and `.mcp.json`.
- [ ] Every document in §18 exists, and every number in the README points to the command or report that produced it.
- [ ] The `reviewer` subagent's final verdict is "ship", and `security-review` findings are fixed or documented.
- [ ] `PROGRESS.md` has every task checked with evidence, and `docs/KNOWN_LIMITATIONS.md` lists what is not done.
- [ ] Every commit is authored and committed by the maintainer, and no commit message or pull request contains `Co-Authored-By`, `Claude-Session` or "Generated with Claude Code" (checked as in §19).
- [ ] `BUILD.md` lives at `docs/specs/000-build-spec.md`; all work is committed, and pushed when a remote exists.
- [ ] The §8 acceptance tests pass for FR-1, FR-2, FR-3, FR-4, FR-5, FR-6, FR-7, FR-8, FR-9, FR-10, FR-11, FR-12, FR-13, FR-14 and FR-15, each with a row in `docs/verification.md`.
- [ ] NFR-1, NFR-2, NFR-4 and NFR-6 budgets met in `make bench` and `scripts/time_up.sh`.
- [ ] Latest `make eval`: compliance violations = 0, unauthorized write-backs = 0, PII leaked = 0.
- [ ] `make tf-check` passes offline and `terraform apply` was never run.
