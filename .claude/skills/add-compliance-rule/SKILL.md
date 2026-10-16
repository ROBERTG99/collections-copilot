---
name: add-compliance-rule
description: "Add a deterministic compliance rule with property tests and engine wiring."
---
# Add a compliance rule

1. New module in `src/collections_copilot/compliance/rules/` implementing `Rule.check(ctx) -> list[Violation]` with a stable violation code.
2. Register it in `compliance/engine.py` for pre-check, post-check or both.
3. Unit tests in `tests/unit/compliance/test_rules.py` and a Hypothesis property if it bounds time, counts or text.
4. Add adversarial cases to the compliance eval dataset via `add-eval-case`.
5. Keep `compliance/` coverage ≥ 95%. The LLM never decides compliance.
