"""Update a task row in PROGRESS.md: python scripts/progress.py T-01 done "evidence"."""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROGRESS = Path(__file__).resolve().parents[1] / "PROGRESS.md"


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    task, status, evidence = argv[1], argv[2], argv[3].replace("|", "\\|")
    text = PROGRESS.read_text()
    pattern = re.compile(rf"^\| {re.escape(task)} \| (M\d) \| (.+?) \| [^|]+ \| .*$", re.M)
    if not pattern.search(text):
        print(f"task {task} not found")
        return 1
    text = pattern.sub(
        lambda m: f"| {task} | {m.group(1)} | {m.group(2)} | {status} | {evidence} |", text
    )
    PROGRESS.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
