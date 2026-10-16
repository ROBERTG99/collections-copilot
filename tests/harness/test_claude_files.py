"""T-04: the Claude Code harness files are complete and well-formed."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT / ".claude"

RULES = [
    "git-commits",
    "python",
    "frontend",
    "llm-code",
    "testing",
    "evals",
    "docker",
    "terraform",
    "security",
    "docs",
]
AGENTS = [
    "reviewer",
    "security-auditor",
    "test-designer",
    "eval-analyst",
    "ux-reviewer",
    "docs-editor",
    "compliance-reviewer",
    "terraform-reviewer",
]
SKILLS = [
    "tdd",
    "quality-gate",
    "eval-report",
    "new-adr",
    "add-migration",
    "add-endpoint",
    "add-agent-tool",
    "add-eval-case",
    "demo-capture",
    "release",
    "add-compliance-rule",
    "replay-dlq",
    "tune-nba-prompt",
]


def _frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text()
    assert text.startswith("---\n"), f"{path} has no frontmatter"
    _, fm, _ = text.split("---\n", 2)
    data = yaml.safe_load(fm)
    assert isinstance(data, dict)
    return data


def test_claude_md_and_agents_md() -> None:
    claude = (ROOT / "CLAUDE.md").read_text()
    assert len(claude.splitlines()) < 200
    for needle in ("Co-Authored-By", "PROGRESS.md", "docs/DECISIONS.md", "docs/adr", "make verify"):
        assert needle in claude
    assert "make check-fast" in (ROOT / "AGENTS.md").read_text()


def test_settings_keep_attribution_and_required_permissions() -> None:
    settings = json.loads((CLAUDE / "settings.json").read_text())
    assert settings["attribution"] == {"commit": "", "pr": "", "sessionUrl": False}
    perms = settings["permissions"]
    for allow in ("Bash(tflint:*)", "Bash(checkov:*)", "Bash(k6:*)", "Bash(./scripts/*)"):
        assert allow in perms["allow"]
    for deny in (
        "Read(./.env.production)",
        "Read(./.env.development)",
        "Bash(rm -rf /:*)",
        "Bash(rm -rf ~:*)",
        "Bash(aws:*)",
    ):
        assert deny in perms["deny"]
    assert "ask" not in perms


def test_rules_exist_and_git_commits_always_loads() -> None:
    for rule in RULES:
        assert (CLAUDE / "rules" / f"{rule}.md").is_file(), rule
    git_rule = (CLAUDE / "rules" / "git-commits.md").read_text()
    assert not git_rule.startswith("---")
    assert "paths:" not in git_rule
    assert "Co-Authored-By" in git_rule


def test_subagents_have_frontmatter() -> None:
    for agent in AGENTS:
        fm = _frontmatter(CLAUDE / "agents" / f"{agent}.md")
        assert fm["name"] == agent
        assert fm["description"]
    reviewer = (CLAUDE / "agents" / "reviewer.md").read_text()
    assert "Verdict: ship" in reviewer
    assert "Edit" not in str(_frontmatter(CLAUDE / "agents" / "reviewer.md")["tools"])


def test_skills_have_frontmatter() -> None:
    for skill in SKILLS:
        fm = _frontmatter(CLAUDE / "skills" / skill / "SKILL.md")
        assert fm["name"] == skill
        assert fm["description"]


def test_mcp_json_is_dev_only() -> None:
    servers = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]
    assert {"playwright", "postgres-readonly", "ledger"} <= set(servers)
    assert servers["ledger"]["url"] == "http://localhost:8344/mcp"
