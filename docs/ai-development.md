# AI-assisted development

collections-copilot was built by an AI coding agent (Claude Code on the web) working from one written specification, inside a harness of rules, hooks, subagents and skills that the agent set up in its first milestone. This page describes that setup, the loop the agent followed, how milestone reviews worked, and the mistakes the agent made that were caught along the way. Everything here is taken from [PROGRESS.md](../PROGRESS.md), [docs/DECISIONS.md](DECISIONS.md), the files under `.claude/` and `git log`.

## The specification

[docs/specs/000-build-spec.md](specs/000-build-spec.md) (`BUILD.md` at the repository root until the build finishes) is the only input. It holds:

- an operating mode (§0): work autonomously, do not stop while the Definition of Done has unchecked items, record ambiguous choices in `docs/DECISIONS.md` instead of asking, keep progress in `PROGRESS.md`, write the failing test first, never invent numbers, stay offline, and never touch a cloud account, a real recipient or the git identity;
- the product brief, functional requirements written as given/then acceptance tests, non-functional budgets and the user journeys (§2–§3);
- the architecture, data model, contracts, agent graphs and model routing (§4);
- a work plan of 52 tasks in milestones M0 to M9 (§8), each naming its files, the test to write first, a done condition and its dependencies;
- the harness to build (§7), the testing, evaluation and quality gates (§10–§12), and the documents to deliver (§18);
- a blockers protocol (§20) listing the only reasons to stop early.

The agent copied the specification into `docs/specs/` in M0 and resumed from `PROGRESS.md` and `docs/DECISIONS.md` after each context reset; the "Session handoff" section at the top of `PROGRESS.md` exists for that.

## The harness

The harness lives in `.claude/` plus two top-level files. `tests/harness/` checks it: front matter and length of the Claude files, every hook's behaviour, every documented make target, the Dockerfiles, the Terraform wiring against the code, and these documents.

| Part | What it is |
|---|---|
| [CLAUDE.md](../CLAUDE.md), [AGENTS.md](../AGENTS.md) | System summary, repository map, make targets, conventions, testing rules, the commit attribution rule, a "never" list and where things live. Loaded into every session. |
| `.claude/settings.json` | Empty commit and pull-request attribution; an allow list (`make`, `uv`, `pnpm`, read-only git, `docker compose`, `terraform fmt/validate/test`, tflint, checkov, k6, `./scripts/*`); a deny list (`terraform apply` and `destroy`, the AWS CLI, `rm -rf` on `/` and `~`, reading `.env` files); the four hooks below. |
| `.claude/rules/` | Ten rule files. `git-commits.md` and `security.md` always load; the others load for the paths they cover: `python.md` (`src/`, `apps/`, `tests/`, `migrations/`), `llm-code.md` (`llm/`, `agent/`, `prompts/`), `testing.md`, `evals.md`, `docker.md`, `terraform.md` (`infra/`), `frontend.md` (`web/`) and `docs.md`. |
| `.claude/agents/` | Eight subagents (below). |
| `.claude/skills/` | Thirteen skills: `tdd`, `quality-gate`, `eval-report`, `new-adr`, `add-migration`, `add-endpoint`, `add-agent-tool`, `add-eval-case`, `demo-capture`, `release` (local tag only), `add-compliance-rule`, `replay-dlq` and `tune-nba-prompt` (dev split only). |
| `.mcp.json` | Development-only MCP servers: Playwright, a read-only Postgres connection on the local database and the ledger through mcp-gateway. No test depends on them. |

### Hooks

| Hook | Event | What it does |
|---|---|---|
| `.claude/hooks/session_start.sh` | SessionStart, only when `CLAUDE_CODE_REMOTE=true` | Starts dockerd with a registry mirror, installs the toolchain, runs `uv sync` and `pnpm install`. Idempotent, so every new cloud session starts ready. |
| `.claude/hooks/pre_tool_guard.py` | PreToolUse on Bash, Write, Edit, MultiEdit | Exit code 2 blocks the call and tells the agent why. Bash: Terraform apply or destroy, cloud deploy tools, the AWS CLI, GitHub repository changes, git identity changes and `--author`, force-pushes, pushing tags, calls to real messaging APIs, and attribution trailers in commit or pull-request text (including message files). Writes: edits to committed eval dataset files, and phone numbers outside the synthetic `+1555…` and `+34555…` ranges. |
| `.claude/hooks/post_edit_format.py` | PostToolUse on Write, Edit, MultiEdit | Formats the edited file with ruff, prettier or `terraform fmt`; a missing tool is a no-op and the hook never fails the call. |
| `.claude/hooks/stop_check.py` | Stop | Runs `make check-fast` before the agent ends a turn and asks it to continue with a one-line summary on failure; it exits 0 when it is already the reason the agent is continuing, so it cannot loop. |

All four are tested in `tests/harness/test_hooks.py`.

### Subagents

| Subagent | Role | Edits files |
|---|---|---|
| `reviewer` | Milestone and release review against the spec, CLAUDE.md and the rules; verdict "ship" or "fix first" | no |
| `compliance-reviewer` | Proves that every path to `send_sms` and `record_promise` passes the compliance engine and an approval | no |
| `security-auditor` | Auth, tenancy, webhooks, prompt injection, PII, secrets, uploads, infrastructure exposure | no |
| `terraform-reviewer` | Least privilege, encryption and exposure in `infra/terraform` | no |
| `ux-reviewer` | The four states per screen, WCAG 2.2 AA, design tokens, AI labelling | no |
| `eval-analyst` | Failure clusters in eval reports; proposes dev-split-only changes | no |
| `test-designer` | Proposes the failing test for a task before implementation | no |
| `docs-editor` | Documentation accuracy and citations | yes |

The read-only reviewers ran in parallel at the end of a milestone; editing stayed with the main agent, so no two agents changed the same files.

### Checks outside `.claude/`

Several project rules are enforced by tools rather than by the agent's discipline: import-linter keeps provider SDKs inside `collections_copilot.llm` and the deterministic core free of the LLM layer; a ruff rule bans raw SQL outside `collections_copilot.db`; `mypy --strict` covers `src/` and `apps/`; `tests/harness/test_docs.py` fails when a reported number in these documents has no citation in its section; `tests/harness/test_terraform_wiring.py` compares the Terraform with the routes and settings the code actually reads.

## The working loop

For each task the agent followed the `tdd` skill:

1. Write the test the spec names under "Test first" and watch it fail (the evidence column of `PROGRESS.md` records the failure, usually as "collection error before module").
2. Implement until it passes, then run `make check-fast` and the area's integration or contract tests.
3. Record the command and a one-line result in `PROGRESS.md`, record any ambiguous choice in `docs/DECISIONS.md` (and an ADR for architectural ones), and commit with a Conventional Commit message.

After mistakes 1 and 10 below, commits went through a gated helper that runs `make check-fast` first and stops on failure. Commit attribution was configured before any other commit: the `attribution` key in `.claude/settings.json`, the `git-commits.md` rule and the guard hook together keep every commit under the maintainer's name with no AI trailers. `PROGRESS.md` holds the evidence for the first commit.

## Milestone reviews

At the end of each milestone the `reviewer` subagent, plus the specialist reviewers that fit the milestone, reviewed the milestone's diff. Blockers and majors were fixed with regression tests before the next milestone (twice the next milestone had already started; see mistakes 9 and 16). Summaries from the "Milestone reviews" table in `PROGRESS.md`:

| Milestone | Reviewers and verdict | Main findings | Fixed in |
|---|---|---|---|
| M0 Bootstrap | `reviewer`: ship, 7 majors | Gateway approval binding, stale in-flight idempotency keys, JWT `iat`/`iss`, dev issuer on by default, evalgate passing empty metrics, SQS batch parsing, guard hook bypasses | `c07063d` |
| M1 Domain | `reviewer`: fix first, 2 blockers | The amount and link checks failed open; the re-review found numeric invoice numbers masking amounts | `594268f`, `4d66bc4` |
| M2 AI core | `compliance-reviewer`: 1 blocker; `reviewer`: fix first, 1 blocker | The send used stale invoice facts and ignored case and draft status; auto-approval treated unknown currencies as USD. The re-review found STOP phrasings still reaching the model and the weekly cap ignoring sends with an unknown outcome | `db35812`, `45221cf`, `af9bdec` |
| M3 API | `reviewer`: ship; `compliance-reviewer` and `security-auditor`: fix first | Inbound SMS routed to a tenant by phone alone with one shared number; dev secret defaults without a production guard; webhook lookups before the signature check; rate-limit keys from unverified tokens | `a106007` |
| M4 Frontend | `ux-reviewer`: fix first, 3 blockers; `reviewer` with a BFF security audit: fix first, 7 majors | The approval drawer's idempotency key locked edits for 24 hours and approve was enabled for unchecked text; no modal focus management; send time in the browser's zone; reclassifying an unprocessed reply drove side effects; token refresh races; e2e evidence that did not reproduce | `7a1188f` |
| M5 Evals | `reviewer`: ship, 4 majors; `eval-analyst` | The live budget could be overshot by one expensive case; replayed cassettes would be labelled `fake`; the wrong-contact NBA category had no rule; over-blocking was never gated | `2562a0a` |
| M6 Observability and security | `reviewer`: fix first, 1 blocker; `security-auditor`: 1 high | OpenTelemetry's defaults exported exception messages (phone numbers, reply text) on spans; Unicode look-alike links and zero-width characters passed the post-check | `c8a6549` |
| M7 Docker, infra, CI | `reviewer` and `terraform-reviewer`: fix first | The ALB routed `/webhooks/*` while the API serves `/api/v1/webhooks/...`; RDS log groups without KMS; Cognito access tokens without the tenant claim; mcp-gateway configured with names it never reads. The Terraform re-review found narrowed egress blocking image pulls | `f43204b`, `953daf7`, `374c864` |

Findings that were recorded rather than fixed went to [docs/KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) and the residual risks in [docs/security.md](security.md).

## Agent mistakes caught

The agent kept a list of its own mistakes in `PROGRESS.md` ("Agent mistakes caught"). Each is summarised here with what caught it. Numbers in this section come from PROGRESS.md.

### Caught by tests

- **Silent no-op edits (2).** Python `str.replace` patches did nothing after the formatting hook had re-wrapped the target lines. Tests on dispute preservation and the ledger's `invoices_belong` check failed. Edits now use exact-match Edit, or assert that the old text is present before replacing.
- **Principal cached on `request.state` (3).** The auth dependency cached the principal on `request.state`, which the test harness shares across requests, so a cross-tenant read returned 200. `test_cross_tenant_batch_is_404` failed; the cache was removed.
- **Company names with digits (7).** The fail-closed amount rule flagged "Synthetic Co 1234". The case-graph integration test never left `human_approval`; the account name is now a protected term.
- **A resumed node reopened a closed case (8).** LangGraph re-runs an interrupted node from its start, so `human_approval` set `awaiting_approval` again on a case STOP had closed. `test_approval_on_closed_case_closes_without_send` failed; status updates now never change a closed or failed case.
- **Evidence recorded before the full suite finished (11).** The T-33 commit said Schemathesis passed, but the full integration run found a bigint overflow 500 on `GET /audit`. The next commit bounded integer parameters and corrected the evidence.
- **A capped S3 read truncated uploads (20).** `get_bytes` called `read(max_bytes + 1)` once, which returns only what the network has delivered, so larger CSV uploads were cut mid-row. `test_duplicate_file_returns_original_batch` failed 2 of 3 runs. It now reads to the end within the cap, with a chunked-stream unit test and a 1 MiB MinIO and moto contract test.
- **A rate-limit test raced the minute boundary (21).** `test_random_tokens_share_the_ip_bucket` failed when a full run crossed a calendar minute. The fixture now holds the clock inside one window with `time-machine`.
- **Own traffic tripped the rate limiter (17).** Hand-made load used the same persona as the Docker-marked tests, which then failed with 429. The demo client now honours `Retry-After`.

### Caught by reviewers

- **Fail-open compliance rules (4).** The first amount and link rules flagged only what they recognised. The M1 reviewer found "999 dollars", "$1k" and a goo.gl link passing; the rules now fail closed, with regression tests.
- **e2e evidence that did not reproduce (13).** T-38 recorded 43 Playwright tests passing twice, but the suite depended on the wall clock, on accounts earlier runs had not used and on a shared rate-limit budget. The M4 reviewers got failures. The helpers now pick accounts by local time, never reuse one and poll instead of sleeping; the remaining time-window gap is a known limitation.
- **Terraform tests asserted the bug (23).** The ALB sent `/webhooks/*` to the API and `network.tftest.hcl` asserted exactly that, while the API serves `/api/v1/webhooks/twilio/*`; the gateway's task definition also used variable names it never reads. Both M7 reviewers caught it. `tests/harness/test_terraform_wiring.py` now checks the Terraform against the code.
- **Web unit coverage at 24.6% against a 70% target (22).** Most of the console was covered only by Playwright, and the M7 reviewer flagged that no web coverage gate was committed. Component and library tests brought `pnpm run test:coverage` to 99.72% of lines (161 tests), and `vitest.config.ts` now fails below 70%. The new tests found a real bug: an error without a reason phrase (HTTP/2) rendered the heading " (500)".

### Caught by the hooks

- **The guard hook blocked the agent's own edits (5, 19).** Bash patches whose text quoted the forbidden Terraform command were blocked by `.claude/hooks/pre_tool_guard.py`. The hook works on command text, so such files are edited with the Edit or Write tools, and tests build the forbidden strings from parts.

### Caught by re-checking or reading back

- **Commits despite failing checks (1).** `make check-fast | tail -1 && git commit` committed after a lint failure because the pipe hid the exit code (T-06, T-08), and once more with a `;` chain (T-18). The next run of the checks showed it, and the commits were amended before pushing. The fix was the gated commit helper.
- **Committed after a failed check again (10).** The M2 re-review fix commit (`af9bdec`) followed a `;` chain after `make check-fast` returned 2. The failures were all in uncommitted T-28 files: with those stashed, `make check-fast` on HEAD returned 0, so the commit stood. From then on only the gated helper or `&&` was used.
- **A patch helper left a module function inside a class (12).** Moving `DbCaseStore._facts` to a public `load_facts` with a scripted edit wedged a function between methods. Reading the file before committing showed it, and mypy flagged it too.
- **A scripted multi-file patch stopped halfway (14).** Writing into a missing directory raised after four of seven files; the rest were written after checking which had landed.
- **A heredoc wrote literal `\n` into DECISIONS.md (18).** Reading the table back showed it; it was repaired.
- **Verified against stale images (15).** `make observability` ran `docker compose up` without `--build`, so Grafana showed no data from the previous images. The target now passes `--build`.
- **Disk exhausted by the Docker build cache (6).** The build cache reached 19.7 GB; `docker builder prune -af` freed it and the step is documented in [docs/DEVELOPMENT.md](DEVELOPMENT.md).

### Not caught by any tool

- **Building on top before the review was fixed (9, 16).** T-25 and T-26 started before the M2 review findings were fixed, and T-42 started while the M5 reviews were still running. In both cases the fixes landed before any further task, but §0 rule 9 asks for the review first. Nothing in the harness enforces this order; the agent recorded it.

### From the decision log

Two further corrections are recorded in [docs/DECISIONS.md](DECISIONS.md):

- The replies eval on the dev split showed "We will pay on 2026-03-16." extracted as a USD 2,026.00 promise that would have reached the ledger. Code now writes back only an amount the debtor actually wrote, and dates are never amounts.
- A decision row claimed the console falls back to polling when the SSE stream is down; the M4 review found the claim untrue. The polling was then added and the row corrected.

## What the record shows

These are observations from the lists above, not measurements:

- Tests caught most implementation slips. Several of the mistakes that mattered most for safety (fail-open compliance rules, a stale send snapshot, the ALB route) were caught by reviewers instead; in the ALB case the Terraform test itself asserted the bug.
- Several mistakes were about evidence rather than code: a pipe that hid an exit code, a result written before the full run finished, an e2e count that depended on the wall clock, stale images. Most responses were mechanical (a gated commit helper, `--build` in the target, polling and account selection in the e2e helpers) rather than reminders.
- The guard hook twice blocked the agent's own patches because their text quoted a forbidden command. The workaround was to use the Edit or Write tools for such files.
- Process ordering (review before building on top) slipped twice and is not enforced by any tool.
