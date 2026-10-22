#!/usr/bin/env python3
"""PreToolUse (Bash|Write|Edit): block actions this repository forbids (see CLAUDE.md, "Never").

Exit code 2 blocks the tool call and shows the reason to Claude.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Start of a shell command: line start or a separator, optional sudo and VAR=value prefixes.
CMD = r"(?:^|[;&|(]|\n)\s*(?:sudo\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"

BASH_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bterraform\b[^\n|;&]*\b(apply|destroy)\b"),
        "terraform apply/destroy is never run",
    ),
    (
        re.compile(r"\b(cdk|sam|serverless|copilot)\s+deploy\b"),
        "deploying to a cloud account is forbidden",
    ),
    (re.compile(CMD + r"aws\s+\S+", re.M), "the aws CLI is not used in this repository"),
    (
        re.compile(r"\bgh\s+repo\s+(create|delete|edit|rename|archive)\b"),
        "repository changes are forbidden",
    ),
    (re.compile(r"\bgit\s+config\b[^\n|;&]*\buser\."), "git identity must not be changed"),
    (re.compile(r"\bgit\b[^\n|;&]*\s-c\s*['\"]?user\."), "git identity must not be changed"),
    (re.compile(r"\bgit\b[^\n|;&]*\s--author\b"), "commits must use the configured git identity"),
    (
        re.compile(r"(^|[\s;&|(])(export\s+)?GIT_(AUTHOR|COMMITTER)_(NAME|EMAIL|DATE)="),
        "GIT_AUTHOR_*/GIT_COMMITTER_* must not be set",
    ),
    (
        re.compile(
            r"\bgit\s+push\b[^\n|;&]*(\s--force\b|\s--force-with-lease\b|\s-[a-zA-Z]*f[a-zA-Z]*\b|\s\+\S)"
        ),
        "force-push is forbidden",
    ),
    (
        re.compile(
            r"\bgit\s+push\b[^\n|;&]*(\s--tags\b|\s--follow-tags\b|\srefs/tags/|\sv\d+\.\d+\.\d+\b)"
        ),
        "pushing tags is forbidden",
    ),
    (
        re.compile(
            r"https?://(api\.twilio\.com|hooks\.slack\.com|api\.sendgrid\.com|slack\.com/api)"
        ),
        "messages to real recipients are forbidden",
    ),
]
ATTRIBUTION = re.compile(r"Co-Authored-By|Claude-Session|Generated with Claude Code", re.I)
COMMITTING = re.compile(r"\bgit\s+(commit|tag|notes)\b|\bgh\s+pr\s+(create|edit)\b")
MESSAGE_FILE = re.compile(r"(?:\s-F\s*|\s--file[= ]|\s--body-file[= ])(\S+)")
PHONE = re.compile(r"(?<![\w+])\+(\d[\d \-().]{6,20}\d)")
SYNTHETIC_PREFIXES = ("1555", "34555")


def _deny(reason: str) -> int:
    print(f"Blocked by .claude/hooks/pre_tool_guard.py: {reason}", file=sys.stderr)
    return 2


def check_bash(command: str, root: Path) -> str | None:
    for pattern, reason in BASH_RULES:
        if pattern.search(command):
            return reason
    if COMMITTING.search(command):
        if ATTRIBUTION.search(command):
            return "attribution trailers are not allowed in commits or pull requests"
        for match in MESSAGE_FILE.finditer(command):
            msg_file = Path(match.group(1).strip("'\""))
            if not msg_file.is_absolute():
                msg_file = root / msg_file
            if msg_file.is_file() and ATTRIBUTION.search(msg_file.read_text(errors="ignore")):
                return "attribution trailers are not allowed in commits or pull requests"
    return None


def check_write(path: Path, content: str, root: Path) -> str | None:
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    rel = resolved.relative_to(root) if resolved.is_relative_to(root) else resolved
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
        digits = re.sub(r"\D", "", match.group(1))
        if not 8 <= len(digits) <= 15:
            continue
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
        reason = check_bash(str(data.get("command", "")), root)
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
