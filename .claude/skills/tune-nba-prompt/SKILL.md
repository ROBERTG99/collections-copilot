---
name: tune-nba-prompt
description: "Iterate on the next-best-action prompt using only the dev split of nba.v1."
---
# Tune the NBA prompt

1. Copy `prompts/next_best_action.v1.md` to a new version (`v2`); never edit a released version in place.
2. Run `uv run evalgate run evals/suites/nba.yaml --split dev --out .evalgate/nba-dev.json` (offline uses FakeLLM; use `make eval-live` with a budget for real models).
3. Compare with the previous dev run; inspect failures by tag.
4. Only after dev improves, run the test split once and record the result in `docs/DECISIONS.md`.
