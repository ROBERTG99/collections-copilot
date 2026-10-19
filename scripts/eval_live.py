"""`make eval-live`: live evals within LIVE_EVAL_BUDGET_USD; exits 0 without a provider key."""

from __future__ import annotations

import sys

from collections_copilot.evals.live import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
