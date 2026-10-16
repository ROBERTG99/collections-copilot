# Architecture

This document grows with the build; the complete version lands in T-50.

## Priority score (FR-2)

The priority score is computed by `src/collections_copilot/scoring/priority.py` (`priority.v1`). It is deterministic, bounded to 0–100 and non-decreasing in balance, days past due and broken promises. The LLM only writes an explanatory rationale (`prioritize_rationale.v1`) and never changes the number.

| Component | Points | Formula |
|---|---|---|
| Balance | 0–40 | `40 × min(1, log10(1 + balance_usd) / log10(1 + 50 000))` over open invoices only |
| Days past due | 0–35 | `35 × min(1, max_dpd / 120)` over open invoices only |
| Broken promises | 0–15 | `15 × min(3, broken promises with a due date in the last 90 days) / 3` |
| Segment | 3, 6 or 10 | smb 3, mid_market 6, enterprise 10 |

`score = round(sum of components)`, clamped to 0–100. EUR balances use the fixed scoring rate 1.08 (scoring only; amounts shown to debtors always come from the ledger in their own currency). Properties are tested with Hypothesis in `tests/unit/scoring/test_priority.py`.
