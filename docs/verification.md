# Final verification

The §19 procedure of `docs/specs/000-build-spec.md`, run on a fresh clone with no `.env` file and no provider keys. Results are copied from the commands' own output.

## Status (2026-10-19)

Run on 2026-10-19 on a macOS 27.0.1 host (10 CPUs; Docker Desktop 29.8.2 with 10 CPUs and 8 GB; Python 3.12.13 in `.venv`, Node 22.16.0), after switching to the published evalgate v0.1.0. Fresh clone of `a16aef7` in `~/dev/verify-collections-copilot` with no `.env` file, no provider keys and `CC_LLM_PROVIDER=fake`. Every figure is copied from the commands' own output.

**Result: not every gate is green.** The same two items as on 2026-10-18 stay open, both in `docs/KNOWN_LIMITATIONS.md`: the replies macro-F1 gate (needs the maintainer; identical value with evalgate v0.1.0) and NFR-1's account-route budget on this host.

### Step 1: `make verify-clean` at `a16aef7`

`scripts/verify_clean.sh` cloned the committed tree and ran `make bootstrap` and `make verify` (5 min 56 s until it stopped):

| Step | Result |
|---|---|
| `make bootstrap` | passed |
| `make lint` | passed: ruff "All checks passed!", import-linter "Contracts: 2 kept, 0 broken", eslint, prettier "All matched files use Prettier code style!", markdownlint "0 issues", `terraform fmt` |
| `make typecheck` | passed: mypy "Success: no issues found in 124 source files", tsc |
| `make test` | passed: 722 Python tests; coverage gate scoring 100.00 %, compliance 99.33 %, agent 92.85 %, llm 95.48 %, api 85.65 %, overall 86.87 %; Vitest "Test Files 20 passed (20), Tests 161 passed (161)" |
| `make security` | "make security: PASS" (gitleaks, bandit, pip-audit, pnpm audit, trivy fs and all 8 images, hadolint, checkov, tflint, actionlint, licences) |
| `make tf-check` | "Success! 28 passed, 0 failed."; checkov "Passed checks: 341, Failed checks: 0, Skipped checks: 32" |
| `make eval` | **failed**: "nba: PASS (120 test cases)", "replies: FAIL (80 test cases) FAIL reply_macro_f1: threshold value=0.8916666667 threshold=0.9", "compliance: PASS (30 test cases)" |

### Step 2: the stack in the same clone (compose project `cc-verify`)

| Step | Result | Duration |
|---|---|---|
| `docker compose build` | passed | 30 s |
| `docker compose up -d --wait` | passed, every service healthy | 32 s |
| `make seed` | passed: acme-distribution 400 accounts, tenant-b-supplies 40 accounts | 8 s |
| `make demo` | passed: "demo: every §3.4 journey completed and checked" | 20 s |
| `make e2e` | passed (exit 0; 35 tests, as in the dev-checkout run "35 passed (2.3m)") | 140 s |
| `make test-docker` | passed: 722 tests in the tools image, then the 4 `-m docker` stack tests | 141 s |
| `docker compose down -v` | passed | 19 s |

On the dev checkout, before the clone: `make up` "every service healthy in 66s (budget 300s)" (NFR-6), and `make bench` at `a16aef7`:

| Requirement | Result | Budget |
|---|---|---|
| NFR-1 queue p95 (k6, 20 VUs, 60 s) | 218.43 ms, passed | < 300 ms |
| NFR-1 account p95 | **254.16 ms, failed** | < 200 ms |
| NFR-1 throughput and errors | 245 req/s, 0 of 14,811 requests failed | |
| NFR-2 case graph p95 (81 of 100 cases paused for approval) | 127 ms, passed | < 300 ms |
| NFR-4 batch of 400 | 29.8 s, passed | < 180 s |

CI on pull request ROBERTG99/collections-copilot#9: every check passes except `evals`, which fails on the same `reply_macro_f1` value (0.8916666667).

### Reviews (§19 step 3)

- `reviewer` over this branch's changes (the history before it had its final "ship" at `1d77cee`): "fix first" with three majors (macro-F1 had lost its point-value regression check, a gated metric dropped from a suite passed, and the docs overstated the gate), fixed in `6ad0b6b`; re-review verdict **"ship"**.
- `security-auditor` over the same changes: nothing at medium or above; three lows, two fixed (the dev dependency group is now audited; a failed restore in `scripts/e2e.sh` fails the run), one recorded in `docs/security.md`.

### Live evals (§19 step 4)

No provider key is set: live evals were not run, and `evals/REPORT.md` says so.

### Authorship (§19 step 5)

```text
$ git log --format='%an <%ae> | %cn <%ce>' | sort -u
ROBERTG99 <54124633+ROBERTG99@users.noreply.github.com> | GitHub <noreply@github.com>
ROBERTG99 <54124633+ROBERTG99@users.noreply.github.com> | ROBERTG99 <54124633+ROBERTG99@users.noreply.github.com>
ROBERTG99 <robertino.gattas@gmail.com> | ROBERTG99 <robertino.gattas@gmail.com>
$ git log --format=%B | grep -E 'Co-Authored-By|Claude-Session|Generated with Claude Code'
(no output, exit 1)
```

Every author is the maintainer. `GitHub <noreply@github.com>` is the committer of the merge of pull request #1; the commits of 2026-10-19 use the local checkout's own identity (the maintainer's e-mail address) and the earlier ones the noreply address.

## Earlier verification (2026-10-18)

Run on 2026-10-18 on a Claude Code on the web container (Linux 6.18, 4 CPUs, Docker 29.8.2, Python 3.13.16, Node 22.22.0), in fresh clones with no `.env` file, no provider keys and `CC_LLM_PROVIDER=fake`. Every line below is copied from the commands' own output (logs `/tmp/verify-cc.log`, `/tmp/verify-cc.<step>.log` and `/tmp/verify-cc2.<step>.log` on that container).

**Result: not every gate is green.** Two items stay open, both recorded in `docs/KNOWN_LIMITATIONS.md`: the replies macro-F1 gate (needs the maintainer) and the NFR-1 account-route budget on this host at verification time.

### Pass 1 (2026-10-18): `make verify-clean` at `6531af0`

`scripts/verify_clean.sh` cloned the committed tree to `/tmp/verify-cc` and ran `make bootstrap` and `make verify` (7 min 51 s until it stopped):

| Step | Result |
|---|---|
| `make bootstrap` | passed |
| `make lint` | passed: ruff "All checks passed!", import-linter "Contracts: 2 kept, 0 broken", prettier, eslint, markdownlint, `terraform fmt` |
| `make typecheck` | passed: mypy "Success: no issues found in 123 source files", tsc |
| `make test` | passed: 720 Python tests (unit, harness, integration, contract), coverage gate scoring 100.00 %, compliance 99.33 %, agent 92.85 %, llm 95.48 %, api 85.71 %, overall 87.11 %; Vitest "Test Files 20 passed (20), Tests 161 passed (161)", lines 99.72 % |
| `make security` | "make security: PASS" (gitleaks, bandit, pip-audit, pnpm audit, trivy fs and all 8 images, hadolint, checkov, tflint, actionlint, licences) |
| `make tf-check` | "Success! 28 passed, 0 failed."; checkov "Passed checks: 341, Failed checks: 0, Skipped checks: 32" |
| `make eval` | **failed**: "nba: PASS (120 test cases)", "replies: FAIL (80 test cases) FAIL reply_macro_f1: threshold value=0.891667 threshold=0.9", "compliance: PASS (30 test cases)" |
| `make bench` | not reached inside `make verify` (run separately below) |

The steps after `make verify` ran on their own in the same clone (compose project `cc-verify`):

| Step | Result | Duration |
|---|---|---|
| `docker compose build` | passed | 163 s |
| `docker compose -p cc-verify up -d --wait` | passed, every service healthy | 29 s |
| `make seed` | passed: acme-distribution 400 accounts, tenant-b-supplies 40 accounts | 12 s |
| `make demo` | passed: "demo: every §3.4 journey completed and checked" | 22 s |
| `make e2e` | passed: "35 passed (3.7m)" | 225 s |
| `make test-docker` | **failed**: `tests/integration/test_walking_skeleton.py::test_case_reaches_approval_interrupt` (the demo no longer printed `awaiting_approval`; fixed in `180ca64`) | 228 s |
| `make bench` | **failed**: account p95 298.62 ms (budget 200 ms); queue p95 206.27 ms (budget 300 ms) passed | 186 s |
| `docker compose down -v` | passed | 13 s |

### Pass 2: fresh clone at `180ca64`

| Step | Result | Duration |
|---|---|---|
| `make bootstrap`, `docker compose build`, `up -d --wait`, `make seed` | passed | 4 s, 184 s, 29 s, 13 s |
| `make test-docker` | passed: 724 tests (720 in the tools image, 4 `-m docker` from the host), 0 failed | 261 s |
| `docker compose down -v`, then a fresh `up -d --wait` and `make seed` | passed | 18 s, 27 s, 13 s |
| `make bench` | **failed on NFR-1 account only**: account p95 274.77 ms (budget 200 ms); queue p95 174.05 ms (budget 300 ms); 175 req/s, 0 failed requests; NFR-2 case graph p95 168 ms over 82 of 100 cases (budget 300 ms); NFR-4 batch of 400 in 52.7 s (budget 180 s) | 185 s |

The same host had slowed down since the morning: `make bench` at `dde18c3` (the M8 commit), on a fresh stack right after pass 2, measured account p95 **281.09 ms**, while the same commit had measured 174.41 ms earlier on 2026-10-18 (PROGRESS.md, M8 review row). The code path of the account route did not change in between; NFR-1 is therefore not met on this host at verification time. NFR-6: `make up` reported "every service healthy in 28s (budget 300s)" on the dev stack (106 s after a rebuild earlier the same day).

### Acceptance test results

Every FR row in the table below passed in `make test` above (52 node ids including parametrised cases; the FR-14 Playwright spec in `make e2e`; FR-15 in `make tf-check`).

### Reviews (§19 step 3)

- `reviewer` over the whole history, three rounds (T-51 row in PROGRESS.md): final verdict **"ship"** at `1d77cee`.
- `security-auditor`, `compliance-reviewer` and `terraform-reviewer`: findings fixed or recorded (PROGRESS.md, Milestone reviews, M9).
- `security-review` skill over the branch: no vulnerabilities at or above 0.7 confidence.
- The `code-review` skill was not run separately; the `reviewer` subagent's three rounds covered correctness.

### Live evals (§19 step 4)

No provider key is set (`ANTHROPIC_API_KEY` unset): live evals were not run, and `evals/REPORT.md` says so.

### Authorship (§19 step 5)

```text
$ git log --format='%an <%ae> | %cn <%ce>' | sort -u
ROBERTG99 <54124633+ROBERTG99@users.noreply.github.com> | ROBERTG99 <54124633+ROBERTG99@users.noreply.github.com>
$ git log --format=%B | grep -E 'Co-Authored-By|Claude-Session|Generated with Claude Code'
(no output, exit 1)
```

## Procedure

1. Commit everything, then run `make verify-clean` (`scripts/verify_clean.sh`). It clones the committed tree into a temporary folder, runs `make bootstrap` and `make verify` there with no keys (it first stops the dev stack's containers to free the ports, keeping its volumes, and does not restart it), then, in the clone, starts the stack as compose project `cc-verify` (`docker compose -p cc-verify up -d --wait`), runs `make seed`, `make demo`, `make e2e` and `make test-docker`, and removes the clone's stack with its volumes. The dev stack's volumes are never touched.
2. Run the `reviewer` subagent over the whole history (`git diff $(git rev-list --max-parents=0 HEAD)..HEAD`), then the `security-review` and `code-review` skills. Fix every blocker and repeat step 1.
3. Live evals: if a provider key is set, `make eval-live` once within `LIVE_EVAL_BUDGET_USD`, labelled live in `evals/REPORT.md` with date, model and cost. Without a key the report says live evals were not run.
4. Authorship: every commit is by the maintainer, with no AI attribution trailers (`git log --format='%an <%ae> | %cn <%ce>%n%B'`).

## Acceptance tests (spec §3.1)

Each functional requirement's *given/when/then* is checked by the tests below; all passed in the final run (see Status).

| FR | Acceptance test |
|---|---|
| FR-1 Ingest | `tests/integration/test_ingest.py::test_duplicate_file_returns_original_batch`, `::test_negative_amount_rejected_with_row` |
| FR-2 Priority score | `tests/unit/scoring/test_priority.py::test_monotonic_in_balance_and_dpd`, `::test_balance_property`; `tests/unit/agent/test_draft.py::test_rationale_never_changes_score_and_is_null_on_failure` |
| FR-3 Next best action | `tests/unit/agent/test_nba.py::test_dispute_note_yields_pause`, `::test_invalid_output_twice_routes_to_analyst` |
| FR-4 Drafting | `tests/unit/agent/test_draft.py::test_spanish_draft_has_footer_and_ledger_amounts_only` |
| FR-5 Compliance engine | `tests/unit/compliance/test_rules.py::test_approval_at_2055_local_defers_to_0800`, `::test_amount_not_in_ledger_blocks` |
| FR-6 Human approval | `tests/integration/test_approval_flow.py::test_edit_stores_diff_and_rechecks`; `tests/integration/test_no_send_without_approval.py::test_auto_approval_negatives_wait_for_a_human[payment_plan_0.99]` |
| FR-7 SMS | `tests/integration/test_twilio_webhooks.py::test_duplicate_status_single_row`, `::test_bad_signature_403_and_audited` |
| FR-8 Replies | `tests/integration/test_reply_graph.py::test_stop_without_llm_call`; `tests/integration/test_reply_idempotency.py::test_stop_confirmation_is_stored_and_sent_once` |
| FR-9 Promise write-back | `tests/integration/test_promise_writeback.py::test_redelivery_single_ledger_row`, `::test_approval_required_then_retry_same_key` |
| FR-10 Follow-ups | `tests/integration/test_scheduler.py::test_broken_promise_reopens_case` |
| FR-11 Live progress | `tests/integration/test_sse.py::test_resume_with_last_event_id` |
| FR-12 Audit | `tests/unit/db/test_audit_chain.py::test_tamper_detected_at_first_bad_seq`; `tests/integration/db/test_audit_cli.py::test_cli_detects_tamper` |
| FR-13 Auth and RBAC | `tests/integration/test_rbac.py::test_role_matrix` (an analyst's `PUT /api/v1/settings/auto-approval` is 403), `::test_cross_tenant_ids_are_404` |
| FR-14 Ops dashboard | `tests/integration/test_metrics_ops.py::test_numbers_match_seeded_counts`; `web/tests/e2e/ops.spec.ts` |
| FR-15 AWS infrastructure | `make tf-test` (`infra/terraform/envs/dev/tests/*.tftest.hcl`, `mock_provider`, offline) |

## Known gates

`make verify` stops at `make eval` while the replies macro-F1 gate is open (see `docs/KNOWN_LIMITATIONS.md`, "Gates"); the steps after it are run on their own and recorded separately.
