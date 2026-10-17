"""`make eval`: offline evals with gates; writes evals/reports/*.json and evals/REPORT.md."""

from __future__ import annotations

import sys

from collections_copilot.evals.report import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
