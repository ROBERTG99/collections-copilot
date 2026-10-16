---
name: quality-gate
description: "Run the full local quality gate (lint, types, tests, security, terraform, evals, bench) and summarize failures."
---
# Quality gate

Run in order and stop at the first red group, summarizing the failing output in ≤ 20 lines:

1. `make lint`
2. `make typecheck`
3. `make test`
4. `make security`
5. `make tf-check`
6. `make eval`
7. `make bench`

`make verify` chains them. Fix code, never thresholds.
