"""Bootstrap files required by BUILD.md §1 and T-01."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BOOTSTRAP_FILES = [
    "docs/DEVELOPMENT.md",
    ".gitignore",
    ".editorconfig",
    "docs/specs/000-build-spec.md",
    "PROGRESS.md",
    "docs/DECISIONS.md",
    "LICENSE",
    ".claude/settings.json",
    ".claude/rules/git-commits.md",
]


def test_bootstrap_files_exist() -> None:
    missing = [f for f in BOOTSTRAP_FILES if not (ROOT / f).is_file()]
    assert missing == []


def test_progress_has_maintainer_section_and_every_task() -> None:
    text = (ROOT / "PROGRESS.md").read_text()
    assert text.index("## Needs the maintainer") < text.index("## Tasks")
    for n in range(1, 53):
        assert f"| T-{n:02d} |" in text


def test_gitignore_keeps_env_example_and_lockfiles() -> None:
    text = (ROOT / ".gitignore").read_text()
    assert "!.env.example" in text
    assert "!evals/reports/baseline.json" in text
    for lock in ("uv.lock", "pnpm-lock.yaml", ".terraform.lock.hcl"):
        assert lock not in text


def test_license_is_mit_for_maintainer() -> None:
    text = (ROOT / "LICENSE").read_text()
    assert text.startswith("MIT License")
    assert "ROBERTG99" in text
