# Final verification

The §19 procedure of `docs/specs/000-build-spec.md`, run on a fresh clone with no `.env` file and no provider keys. Results are recorded here by T-52 from the commands' own output; until then this page lists the procedure only.

## Status

Not run yet. T-52 replaces this section with the date, the commit, and the result line of each step below.

## Procedure

1. Commit everything, then run `make verify-clean` (`scripts/verify_clean.sh`). It clones the committed tree into a temporary folder, runs `make bootstrap` and `make verify` there with no keys, then, in the clone, starts the stack as compose project `cc-verify` (`docker compose -p cc-verify up -d --wait`), runs `make seed`, `make demo`, `make e2e` and `make test-docker`, and removes the clone's stack with its volumes. The dev stack's volumes are never touched.
2. Run the `reviewer` subagent over the whole history (`git diff $(git rev-list --max-parents=0 HEAD)..HEAD`), then the `security-review` and `code-review` skills. Fix every blocker and repeat step 1.
3. Live evals: if a provider key is set, `make eval-live` once within `LIVE_EVAL_BUDGET_USD`, labelled live in `evals/REPORT.md` with date, model and cost. Without a key the report says live evals were not run.
4. Authorship: every commit is by the maintainer, with no AI attribution trailers (`git log --format='%an <%ae> | %cn <%ce>%n%B'`).

## Known gates

`make verify` stops at `make eval` while the replies macro-F1 gate is open (see `docs/KNOWN_LIMITATIONS.md`, "Gates"); the steps after it are run on their own and recorded separately.
