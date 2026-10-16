#!/usr/bin/env python3
"""PreToolUse (Bash|Write|Edit): block actions forbidden by BUILD.md §0 rule 8 and §1.1.

Exit code 2 blocks the tool call and shows the reason to Claude.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BASH_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bterraform\b[^\n|;&]*\b(apply|destroy)\b"), "terraform apply/destroy is never run"),
    (re.compile(r"\b(cdk|sam|serverless|copilot)\s+deploy\b"), "deploying to a cloud account is forbidden"),
    (re.compile(r"(^|[;&|(]\s*)aws\s+\S+"), "the aws CLI is not used by this build"),
    (re.compile(r"\bgh\s+repo\s+(create|delete|edit|rename|archive)\b"), "repository changes are forbidden"),
    (re.compile(r"\bgit\s+config\b[^\n|;&]*\buser\."), "git identity must not be changed"),
    (re.compile(r"(^|[\s;&|(])(export\s+)?GIT_(AUTHOR|COMMITTER)_(NAME|EMAIL|DATE)="),
     "GIT_AUTHOR_*/GIT_COMMITTER_* must not be set"),
    (re.compile(r"\bgit\s+push\b[^\n|;&]*(\s--force\b|\s-f\b|\s--force-with-lease\b|\s\+\S)"),
     "force-push is forbidden"),
    (re.compile(r"\bgit\s+push\b[^\n|;&]*(\s--tags\b|\srefs/tags/|\sv\d+\.\d+\.\d+\b)"),
     "pushing tags is forbidden"),
]
ATTRIBUTION = re.compile(r"Co-Authored-By|Claude-Session|Generated with Claude Code", re.I)
PHONE = re.compile(r"(?<![\w+])\+(\d{8,15})\b")
SYNTHETIC_PREFIXES = ("1555", "34555")


def _deny(reason: str) -> int:
    print(f"Blocked by .claude/hooks/pre_tool_guard.py: {reason}", file=sys.stderr)
    return 2


def check_bash(command: str) -> str | None:
    for pattern, reason in BASH_RULES:
        if pattern.search(command):
            return reason
    if re.search(r"\bgit\s+(commit|tag|notes)\b|\bgh\s+pr\s+(create|edit)\b", command) and (
        ATTRIBUTION.search(command)
    ):
        return "attribution trailers are not allowed in commits or pull requests"
    return None


def check_write(path: Path, content: str, root: Path) -> str | None:
    rel = path.resolve().relative_to(root) if path.resolve().is_relative_to(root) else path
    parts = rel.parts
    if (
        len(parts) >= 3
        and parts[0] == "evals"
        and parts[1] == "datasets"
        and path.exists()
        and ("test" in path.name or path.suffix == ".jsonl")
    ):
        return "committed eval datasets (test partitions) are append-only by version; add a new version"
    for match in PHONE.finditer(content):
        digits = match.group(1)
        if not digits.startswith(SYNTHETIC_PREFIXES):
            return f"non-synthetic phone number +{digits[:4]}…; use +1555… or +34555…"
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    tool = payload.get("tool_name", "")
    data = payload.get("tool_input") or {}
    root = Path(payload.get("cwd") or ".").resolve()
    if tool == "Bash":
        reason = check_bash(str(data.get("command", "")))
    elif tool in ("Write", "Edit", "MultiEdit"):
        content = str(data.get("content") or data.get("new_string") or "")
        for edit in data.get("edits") or []:
            content += "\n" + str(edit.get("new_string", ""))
        reason = check_write(Path(str(data.get("file_path", ""))), content, root)
    else:
        reason = None
    return _deny(reason) if reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
