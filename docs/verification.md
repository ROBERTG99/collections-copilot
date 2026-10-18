# Final verification

The §19 procedure of `docs/specs/000-build-spec.md`, run on a fresh clone with no `.env` file and no provider keys. Results are recorded here by T-52 from the commands' own output; until then this page lists the procedure only.

## Status

Not run yet. T-52 replaces this section with the date, the commit, and the result line of each step below.

## Procedure

1. Commit everything, then run `make verify-clean` (`scripts/verify_clean.sh`). It clones the committed tree into a temporary folder, runs `make bootstrap` and `make verify` there with no keys (it first stops the dev stack's containers to free the ports, keeping its volumes, and does not restart it), then, in the clone, starts the stack as compose project `cc-verify` (`docker compose -p cc-verify up -d --wait`), runs `make seed`, `make demo`, `make e2e` and `make test-docker`, and removes the clone's stack with its volumes. The dev stack's volumes are never touched.
2. Run the `reviewer` subagent over the whole history (`git diff $(git rev-list --max-parents=0 HEAD)..HEAD`), then the `security-review` and `code-review` skills. Fix every blocker and repeat step 1.
3. Live evals: if a provider key is set, `make eval-live` once within `LIVE_EVAL_BUDGET_USD`, labelled live in `evals/REPORT.md` with date, model and cost. Without a key the report says live evals were not run.
4. Authorship: every commit is by the maintainer, with no AI attribution trailers (`git log --format='%an <%ae> | %cn <%ce>%n%B'`).

## Acceptance tests (spec §3.1)

Each functional requirement's *given/when/then* is checked by the tests below; T-52 records the result of each row from the final run.

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
