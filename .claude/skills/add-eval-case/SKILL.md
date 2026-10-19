---
name: add-eval-case
description: "Add eval cases by extending the generator (never editing committed test partitions)."
---
# Add an eval case

1. Extend `data/generator/evals.py` with the new scenario; labels are set by construction.
2. Bump the dataset version (`*.v2.jsonl`) instead of editing a committed `*.v1.jsonl` file; update `evals/datasets/MANIFEST.json`.
3. Point the suite in `evals/suites/` at the new version and run `make eval`.
4. Tune only on `dev`; report on `test`.
