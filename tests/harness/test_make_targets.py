"""Every make target documented in the README, CLAUDE.md, AGENTS.md and CONTRIBUTING.md exists in the Makefile."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ["README.md", "CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md"]


def _makefile_targets() -> set[str]:
    text = (ROOT / "Makefile").read_text()
    return set(re.findall(r"^([a-zA-Z][a-zA-Z0-9_-]*):", text, re.M))


def _documented_targets() -> set[str]:
    found: set[str] = set()
    for name in DOCS:
        path = ROOT / name
        if path.exists():
            found |= set(re.findall(r"`make ([a-z][a-z0-9-]*)`", path.read_text()))
    return found


def test_every_documented_target_exists() -> None:
    documented = _documented_targets()
    assert {"bootstrap", "verify", "verify-clean", "tf-check", "dev-token"} <= documented
    missing = sorted(documented - _makefile_targets())
    assert missing == []


def test_targets_have_help_text() -> None:
    text = (ROOT / "Makefile").read_text()
    for target in ("bootstrap", "up", "verify", "eval", "bench", "security"):
        assert re.search(rf"^{target}:.*## ", text, re.M), target
