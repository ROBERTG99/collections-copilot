"""make test: fail when line coverage is below BUILD.md §10 (coverage.xml from pytest-cov).

Core packages (scoring, compliance, agent, llm, api) need 85%, compliance 95% (T-14), and the
whole of src/collections_copilot 70%. Paths in coverage.xml are relative to that source root.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET  # coverage.xml is written by our own test run
from collections import defaultdict
from pathlib import Path

CORE = {"scoring": 85.0, "compliance": 95.0, "agent": 85.0, "llm": 85.0, "api": 85.0}
OVERALL = 70.0


def line_counts(path: Path) -> dict[str, list[int]]:
    """Covered and total lines per top-level package."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    root = ET.parse(path).getroot()  # noqa: S314
    for cls in root.iter("class"):
        package = cls.get("filename", "").split("/")[0]
        for line in cls.iter("line"):
            counts[package][1] += 1
            counts[package][0] += int(line.get("hits", "0")) > 0
    return counts


def main(argv: list[str]) -> int:
    counts = line_counts(Path(argv[1] if len(argv) > 1 else "coverage.xml"))
    failures = 0
    for package, minimum in CORE.items():
        covered, total = counts.get(package, [0, 0])
        pct = 100.0 * covered / total if total else 0.0
        ok = total > 0 and pct >= minimum
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'} {package:<12} {pct:6.2f}% (minimum {minimum:.0f}%)")
    covered = sum(c for c, _ in counts.values())
    total = sum(t for _, t in counts.values())
    pct = 100.0 * covered / total if total else 0.0
    ok = pct >= OVERALL
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'} {'overall':<12} {pct:6.2f}% (minimum {OVERALL:.0f}%)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
