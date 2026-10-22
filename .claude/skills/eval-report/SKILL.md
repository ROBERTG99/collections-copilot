---
name: eval-report
description: "Run offline evals and regenerate evals/REPORT.md from the latest reports."
---
# Eval report

1. `make eval` (runs every suite in `evals/suites/` on the test split with FakeLLM, gates thresholds and regression against `evals/reports/baseline.json`, writes `evals/REPORT.md`).
2. If a gate fails, run the `eval-analyst` agent on the reports.
3. Never hand-edit `evals/REPORT.md`; commit it with the reports that produced it.
4. Update `evals/reports/baseline.json` only when a deliberate improvement lands, and record why in `docs/DECISIONS.md`.
