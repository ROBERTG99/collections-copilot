#!/usr/bin/env python3
"""PostToolUse (Write|Edit): format the edited file with ruff, prettier or terraform fmt.

Every formatter is optional: a missing tool is a no-op. The hook never fails the tool call.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

PRETTIER_EXT = {".ts", ".tsx", ".js", ".mjs", ".cjs", ".css", ".json", ".md", ".yaml", ".yml"}


def commands_for(path: Path, root: Path) -> list[list[str]]:
    suffix = path.suffix
    if suffix == ".py":
        if shutil.which("uv"):
            return [
                ["uv", "run", "--frozen", "ruff", "check", "--fix", "--quiet", str(path)],
                ["uv", "run", "--frozen", "ruff", "format", "--quiet", str(path)],
            ]
        if shutil.which("ruff"):
            return [["ruff", "format", "--quiet", str(path)]]
        return []
    if suffix in (".tf", ".tfvars", ".hcl") and shutil.which("terraform"):
        return [["terraform", "fmt", str(path)]]
    if suffix in PRETTIER_EXT and "web" in path.parts:
        prettier = root / "web" / "node_modules" / ".bin" / "prettier"
        if prettier.exists():
            return [[str(prettier), "--write", "--log-level", "silent", str(path)]]
    return []


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    file_path = (payload.get("tool_input") or {}).get("file_path")
    if not file_path:
        return 0
    path = Path(file_path)
    root = Path(payload.get("cwd") or ".").resolve()
    if not path.exists():
        return 0
    for cmd in commands_for(path, root):
        try:
            subprocess.run(cmd, cwd=root, capture_output=True, timeout=60, check=False)  # noqa: S603
        except (OSError, subprocess.TimeoutExpired):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
