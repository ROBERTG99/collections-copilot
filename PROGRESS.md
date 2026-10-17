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

## Session handoff (resume here)

State on 2026-10-17, branch `feat/serene-gates-04qt5s`, pushed after each milestone:

- **Done:** M0 (T-01 to T-07), M1 (T-08 to T-15), M2 (T-16 to T-24), M3 (T-25 to T-33) and T-34 (web scaffold: BFF session, OIDC code + PKCE, `/api/*` proxy with CSRF header, tokens, generated API types). M2 and M3 reviews are done and their findings fixed (see "Milestone reviews").
- **Next task: T-35** (queue and account detail screens), then T-36 to T-38 (M4), M5 evals, M6 observability and security (`scripts/security.sh` is still missing, so `make security` cannot run yet), M7 Docker/Terraform/CI (web Dockerfile and a `web` compose service are not built yet), M8 docs and demo, M9 hardening.
- **Running the web console locally:** `cd web && pnpm run build && WEB_SESSION_SECRET=<32+ chars> pnpm exec next start -p 8300` (or `pnpm dev`) against the compose stack. Sign in at `http://localhost:8300` as `ana.analyst` (dev-oidc persona picker). `node .next/standalone/server.js` is the production entry point (T-44).
- **Still referenced but not built:** `/.well-known/agent-jwks.json` on the API (KMS signing, AWS only; locally the gateway uses its dev issuer), and the scripts named in the Makefile for later milestones (`time_up.sh`, `bench.sh`, `security.sh`, `tf.sh`, `verify_clean.sh`, `eval_report.py`, `eval_live.py`, `gen_docs.py`, `coverage_gate.py`).

How to resume in a fresh container:

1. Re-read `docs/specs/000-build-spec.md` (= `BUILD.md`), this file, `docs/DECISIONS.md` and `git log --oneline -40`.
2. The SessionStart hook (`.claude/hooks/session_start.sh`) starts dockerd with the `mirror.gcr.io` registry mirror, installs tools, and runs `uv sync` and `pnpm install`. Run it by hand if needed: `CLAUDE_CODE_REMOTE=true .claude/hooks/session_start.sh`.
3. `docker compose up -d --wait --build` (12 services incl. dev-oidc and debtor-sim), then `docker compose run --rm seed`. On "no space left on device", run `docker builder prune -af`.
4. Checks: `make check-fast`; `uv run pytest tests/integration tests/contract -m "not docker" -q`; `uv run pytest tests/integration -m docker -q` with the stack up; `cd web && pnpm run typecheck && pnpm exec vitest run && pnpm run lint`.
5. Commit only after `make check-fast` passes (use `&&`, never `;` or a pipe), and only write evidence after the full integration run has finished (mistakes 1, 10 and 11 below).

Verification counts at this handoff: `make check-fast` passes; unit + harness → 320 passed; integration + contract (not docker) → 169 collected, all passed (incl. Schemathesis with zero server errors); docker-marked → 3 passed (walking skeleton, debtor-sim reply < 5 s, plus the OIDC contract against the rebuilt image); web Vitest → 6 passed.

## Milestone reviews

| Milestone | Reviewer verdict | Follow-up |
|---|---|---|
| M0 | `reviewer`: "ship", with 7 majors | All majors fixed in `c07063d` (gateway approval binding, stale in-flight keys, JWT iat/iss, dev issuer off by default, evalgate empty metrics, SQS per-message parse, guard hook bypasses), plus most minors. |
| M1 | `reviewer`: "fix first" (2 blockers: the amount and link checks failed open; 3 majors) | Fixed in `594268f`. Re-review found one more amount hole (numeric invoice numbers masking amounts), fixed in `4d66bc4`; every other item resolved. Remaining minors are in docs/KNOWN_LIMITATIONS.md. |
| M3 | `reviewer`: "ship" (2 majors: one failing follow-up stalled the scheduler; admins could not record opt-outs; 16 minors). `compliance-reviewer`: "fix first" (1 major: inbound SMS routed to a tenant by phone alone with one shared sending number). `security-auditor`: "fix first" (4 majors: the same routing issue; dev secret defaults with no production guard; webhook lookups and audit writes before the signature check; rate-limit and idempotency keys from unverified tokens) | Fixed with tests in `a106007`: STOP applies in every tenant that texted or holds the number (one confirmation) and other replies are routed only when exactly one tenant matches; production refuses dev secrets and a non-HTTPS issuer; signatures are checked before any lookup, bad-signature audits are throttled and carry no attacker values; limits key on the verified (tenant, sub) else the client IP, webhooks are IP-limited, 401/403/429 are never stored and the idempotency pending marker lives 60 s; scheduler rows back off and are cancelled after 5 attempts, tasks are enqueued after the commit, stale queued cases and received replies are swept, scores refresh daily; opt-outs allowed for every role; `LeaderLock` invalidates its connection; ID tokens rejected, JWKS replaced on refresh; dev-oidc redirect/logout allowlists, direct tokens off by default; audit redaction covers NANP and Spanish local numbers; status callbacks store no phone numbers; SSE ends at token expiry; promise approval refuses when nothing is open. Recorded, not fixed: per-tenant sending numbers (docs/KNOWN_LIMITATIONS.md), SSE stream caps per user (pool capped at 200), gateway HMAC without a timestamp (replays deduplicated by `approval_id`). |
| M2 | Re-review (`compliance-reviewer`, after the fixes): no blocker; 2 majors (STOP phrasings still reached the model; the 7-day cap ignored sends with an unknown outcome) and 7 minors, fixed in `af9bdec` with tests, except the race-free check-to-send (the window between the claim and the provider call remains; the claim now re-checks under the case lock) and the scheduler liveness (T-30). First review: `compliance-reviewer`: 1 blocker (the send used stale invoice facts and ignored case and draft status), 3 majors. `reviewer`: "fix first" (1 blocker: auto-approval treated unknown currencies as USD; 7 majors; 13 minors) | Blockers and majors fixed with regression tests in `db35812` (fresh send snapshot, stored approval record for the exact version on every path, stored draft body, no reopening closed cases, currency fail-closed) and `45221cf` (idempotent reply handlers, stored STOP confirmation, wider and narrower STOP rules, promise row lock, Twilio retry only when the request surely failed, per-case advisory lock, resume kind required, `retry_write_back` wired). Minors fixed in the next commit, except contact-name redaction (no contact field exists; docs/KNOWN_LIMITATIONS.md). |

## Agent mistakes caught (for docs/ai-development.md)

1. **Commits despite failing checks.** `make check-fast | tail -1 && git commit` committed after a lint failure because the pipe hid the exit code; this happened twice (T-06, T-08) and once more with a `;` chain (T-18). Both were caught on the next run and amended before pushing. The fix was a gated commit helper (check-fast first, then progress update and commit).
2. **Silent no-op edits.** Python `str.replace` patches did nothing after the PostToolUse formatter had re-wrapped the target lines; this was caught by tests (dispute preservation, ledger `invoices_belong`). Exact-match Edit, or `assert old in text` before replacing, is required.
3. **Principal cached on `request.state`.** The auth dependency cached the principal on `request.state`, which the test harness shares across requests, so a cross-tenant read returned 200. `test_cross_tenant_batch_is_404` caught it; the cache was removed.
4. **Fail-open compliance rules.** The first amount and link rules only flagged what they recognised. The M1 reviewer found bypasses ("999 dollars", "$1k", goo.gl links); the rules now fail closed, with regression tests.
5. **Own guard hook blocked edits.** A Bash patch containing the text of the terraform rule was blocked by `.claude/hooks/pre_tool_guard.py`. Patches that mention forbidden commands are written with the Write tool or built from concatenated strings in tests.
6. **Disk exhausted by the Docker build cache** (19.7 GB). Fixed with `docker builder prune -af`; documented in docs/DEVELOPMENT.md.
7. **Company names with digits.** The fail-closed amount rule flagged "Synthetic Co 1234". This was caught by the case-graph integration test (the approval loop never left `human_approval`); the account name is now a protected term.
8. **A resumed node reopened a closed case.** LangGraph re-runs an interrupted node from its start, so `human_approval` set `awaiting_approval` again on a case that STOP had closed. `test_approval_on_closed_case_closes_without_send` caught it; case status updates now never change a closed or failed case.
9. **Reviewed after building on top.** T-25/T-26 (M3) started before the M2 review findings were fixed; the fixes landed before any further M3 task, but rule 9 asks for the review first.
10. **Committed after a failed check again.** The M2 re-review fix commit (`af9bdec`) followed a `;` chain after `make check-fast` returned 2. The failures were all in uncommitted T-28 files; stashing them and re-running `make check-fast` on HEAD alone returned 0, so the commit stood. Only the gated helper (`if ! make check-fast; then exit 1; fi`) or `&&` is used from here on.
11. **Evidence recorded before the full suite finished.** The T-33 commit's evidence said Schemathesis passed, but the full integration run in the same command found a bigint overflow 500 on `GET /audit`. Fixed in the next commit (integer parameters are bounded) and the evidence corrected. The helper only gates `make check-fast`; integration results are now checked before writing evidence.
12. **A patch helper left a module function inside a class.** Moving `DbCaseStore._facts` to a public `load_facts` with a scripted edit first produced a function wedged between methods (caught by reading the file before committing; mypy also flagged it). Structural moves are done with a read of the region first.

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
| T-18 | M2 | PII redaction | done | `pytest tests/unit/llm/test_pii_spy.py` → collection error before redaction, now 3 passed; spy over 300 gen.v1 accounts finds no name or phone in any provider payload |
| T-19 | M2 | Prompts v1 and schemas | done | `pytest tests/unit/agent/test_prompts.py` → collection error before module, now 5 passed (untrusted notes fenced and escaped after instructions); `cc prompts list` prints 5 SHA-256 hashes |
| T-20 | M2 | Next-best-action node | done | `pytest tests/unit/agent/test_nba.py` → collection error before node, now 6 passed (dispute note → pause citing it; invalid output twice → analyst nba_invalid_output; outage → rules labelled fallback) |
| T-21 | M2 | Rationale and draft nodes | done | `pytest tests/unit/agent/test_draft.py` → collection error before nodes, now 5 passed (Spanish payment_plan draft ends with ES footer, ledger amounts only, passes post-check; refusal → template; rationale null on outage) |
| T-22 | M2 | Case graph, checkpointer, interrupt | done | `pytest tests/integration/test_case_graph.py tests/integration/test_llm_outage.py` → collection errors before graph, now 7 passed (resume after worker crash without recomputing NBA; approval interrupt; auto-approval; outage → fallback, no send) |
| T-23 | M2 | Reply graph and STOP rule | done | `pytest tests/integration/test_reply_graph.py` → collection error before graph, now 6 passed (STOP/stop/Baja/UNSUBSCRIBE → opt-out, one confirmation, zero LLM calls; dispute cancels open drafts) |
| T-24 | M2 | MCP client and promise write-back | done | `pytest tests/integration/test_promise_writeback.py` → collection error before client, now 3 passed against in-process mcp-gateway + ledger-mcp (crash after call + redelivery → 1 ledger row; USD 8,000 → approval_required → retry same key → 1 row; -32040 → failed, no retry) |
| T-25 | M3 | OIDC, dev-oidc, RBAC | done | uv run pytest tests/contract/test_oidc_contract.py tests/integration/test_rbac.py -q → all pass (7 OIDC contract, role matrix over every /api/v1 route incl. 401/403 + correlation_id, tenant isolation); dev-oidc in compose on 127.0.0.1:8345. Shares its commit with T-26 (same app factory). |
| T-26 | M3 | Queue, accounts, cases endpoints | done | uv run pytest tests/integration/api/test_queue.py -q → 4 passed (test_sorted_by_priority_with_cursor, filters, account detail + 404 cross-tenant, case create/dedupe/enqueue); integration+contract (not docker) 58 passed; walking skeleton (docker) passed with dev-oidc tokens; scores cached on accounts (migration 0002) and refreshed on ingest/seed. make gen-types now regenerates web/lib/api/schema.d.ts (since 23ec742). |
| T-27 | M3 | Approvals and auto-approval policy | done | uv run pytest tests/integration/test_approval_flow.py tests/integration/test_no_send_without_approval.py -q → 13 passed (test_edit_stores_diff_and_rechecks: 422 amount_not_in_ledger on a bad edit, diff in approval and audit, worker sends the edited text; reject needs a reason; 503 then replay sends once; batch per-item 200/409/404 and 51 items 422; settings only tighten, analyst PUT 403; only send_sms and the STOP confirmation call the SMS provider; resume without a record sends nothing; payment_plan 0.99, confidence 0.89, USD 500.01, fallback and tenant tightening never auto-approve) |
| T-28 | M3 | Twilio adapter, webhooks, debtor simulator | done | uv run pytest tests/contract/test_twilio_contract.py tests/integration/test_twilio_webhooks.py tests/unit/apps -q → all pass (WireMock create-message contract, 503 retryable/401 not, signatures equal the twilio library; test_duplicate_status_single_row, test_bad_signature_403_and_audited, STOP handled by rule in the webhook with one confirmation and nothing queued, reply stored once and queued); docker: tests/integration/test_debtor_sim_e2e.py passed (simulated reply < 5 s after the send; worker consumed cc-replies and wrote the promise through mcp-gateway) |
| T-29 | M3 | SSE progress | done | uv run pytest tests/integration/test_sse.py -q → 3 passed (test_resume_with_last_event_id: reconnect with Last-Event-ID receives only the later status and draft events; heartbeat after silence without an id, then the live node event; other tenant 404); RBAC matrix includes the events route; worker publishes to Redis streams |
| T-30 | M3 | Scheduler and follow-ups | done | uv run pytest tests/integration/test_scheduler.py -q → 4 passed (test_broken_promise_reopens_case: promise due yesterday with the invoice open becomes broken and one new queued case is enqueued, a second tick does nothing; paid → kept; test_single_leader: one lock holder, the follower does nothing, two schedulers forced to tick process 10 rows exactly once with SKIP LOCKED, leadership moves on release; approval reminder and send_retry emails). Worker runs the scheduler; human_approval schedules the 24 h reminder. |
| T-31 | M3 | Gateway approval webhook and promise approval | done | uv run pytest tests/integration/test_mcpgw_webhook.py -q → 2 passed (test_unknown_approval_404 plus 403 for a bad HMAC; test_supervisor_approval_enqueues_retry: webhook links the held approval and emails once, analyst 403, supervisor approves at the gateway, retried request reads the decision back, retry_write_back tasks record exactly one ledger row, reject after recording is 409). Also GET /promises, /replies, /follow-ups. |
| T-32 | M3 | Ops metrics and audit endpoints | done | uv run pytest tests/integration/test_metrics_ops.py -q → 3 passed (test_numbers_match_seeded_counts: cases by status, pending approvals, promises, 3 sends with window rate 0.6667, LLM cost 0.03 over 2 accounts = 0.015, node p95 draft 95.5 ms, DLQ depth with an unavailable queue shown as null, opt-outs/follow-ups/replies, another tenant sees nothing; audit pages newest-first with verify=true chain_ok and redacted payloads, analyst 403; manual opt-out closes open work, idempotent, E.164 validated) |
| T-33 | M3 | Limits, problem details, Idempotency-Key, Schemathesis | done | uv run pytest tests/integration/test_limits.py tests/contract/test_openapi_schemathesis.py -q → all pass after `fix(api)` bounding integer parameters: the first full run after the T-33 commit had one Schemathesis 500 (`GET /audit?before_seq=4.9e20`, bigint overflow) that the commit's evidence missed. Covered: test_upload_over_10mb_413 incl. >1 MB JSON and chunked bodies; test_rate_limit_429 with Retry-After and per-user buckets; Idempotency-Key replay and 422 reuse; NUL characters 422. Schemathesis: zero server errors over every /api/v1 operation (two consecutive runs, 25 examples each). |
| T-34 | M4 | Web scaffold, tokens, BFF session | done | cd web && pnpm exec vitest run → 6 passed (session.test.ts: round trip, tampered cookie rejected, foreign secret, garbage, stale, short secret); pnpm run typecheck (tsc --noEmit) and eslint clean; next build OK; smoke: dev-oidc PKCE sign-in → httpOnly cookie → BFF /api/me and /api/queue 200, mutating call without x-cc-csrf 403, /api/webhooks/* 404. make gen-types generates web/lib/api/schema.d.ts from docs/openapi.json. |
| T-35 | M4 | Queue and Account detail | done | cd web && pnpm exec playwright test tests/e2e/queue.spec.ts → 7 passed against the compose stack (queue: success sorted by score + segment filter, empty search with next step, error with correlation id and working retry, loading skeleton; account detail: success with aging/invoices/pre-check, error 404 with correlation id and loading skeleton, empty history offering Start case); axe: zero serious/critical WCAG 2.2 AA violations on each screen; vitest 9 passed; tsc and eslint clean |
| T-36 | M4 | Approval drawer with diff and progress | done | cd web && pnpm exec playwright test → 9 passed (approval.spec.ts: failing post-check (USD 999.00 not in the ledger) disables approve and reject needs a reason; edit shows a word diff, live post-check passes, 'Save edit and approve' records the decision and the worker moves the case on; progress list fed by SSE; axe clean on the drawer). API: POST /cases/{id}/postcheck (side-effect free, RBAC A/S, test_live_postcheck_stores_nothing) and recipient_e164 on the case. Fixed the typed client's POSTs (buffered body). |
| T-37 | M4 | Replies, Promises, Audit, Ops, Settings | done | cd web && pnpm exec playwright test → 15 passed (replies-promises.spec.ts: a debtor-sim reply to an approved SMS is listed classified and is reclassified; empty/error states; supervisor approves a held promise, analysts see the hold without controls. ops.spec.ts: every card equals GET /api/v1/metrics/ops; analysts get no Ops link and a 403 problem; error state). API: POST /replies/{id}:reclassify (records the correction only, STOP refused, cross-tenant 404). eslint/tsc clean. |
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
