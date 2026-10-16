#!/usr/bin/env python3
"""Stop hook: run `make check-fast`; on failure print one summary and ask Claude to continue.

Never loops: when Claude is already continuing because of this hook (`stop_hook_active`), exit 0.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys

DEFAULT_CMD = "make check-fast"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}
    if payload.get("stop_hook_active"):
        return 0
    cmd = os.environ.get("STOP_CHECK_CMD", DEFAULT_CMD)
    cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        proc = subprocess.run(  # noqa: S603
            shlex.split(cmd), cwd=cwd, capture_output=True, text=True, timeout=300, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"stop check could not run ({exc}); skipping", file=sys.stderr)
        return 0
    if proc.returncode == 0:
        return 0
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:])
    print(f"`{cmd}` failed (exit {proc.returncode}). Fix before stopping:\n{tail}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
