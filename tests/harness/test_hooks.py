"""T-05: Claude Code hooks block forbidden actions, format edits and never loop on Stop."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".claude" / "hooks"

# Built by concatenation so the strings below never trip the guard while editing this file.
ATTR = "Co-Authored" + "-By: Someone <x@example.com>"
REAL_PHONE = "+44" + "7911123456"
AWS = "aw" + "s"


def run_hook(
    name: str, payload: dict[str, object], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
        timeout=60,
    )


def bash(command: str) -> subprocess.CompletedProcess[str]:
    return run_hook(
        "pre_tool_guard.py",
        {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(ROOT)},
    )


def write(path: Path, content: str) -> subprocess.CompletedProcess[str]:
    return run_hook(
        "pre_tool_guard.py",
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(path), "content": content},
            "cwd": str(ROOT),
        },
    )


@pytest.mark.parametrize(
    "command",
    [
        "terraform apply",
        "cd infra/terraform/envs/dev && terraform apply -auto-approve",
        "terraform -chdir=infra destroy",
    ],
)
def test_blocks_terraform_apply(command: str) -> None:
    result = bash(command)
    assert result.returncode == 2
    assert "terraform" in result.stderr


@pytest.mark.parametrize(
    "command",
    ["terraform fmt -recursive infra", "terraform test", "make tf-check", "terraform validate"],
)
def test_allows_offline_terraform(command: str) -> None:
    assert bash(command).returncode == 0


def test_blocks_attribution_trailer() -> None:
    result = bash(f'git commit -m "feat: x" -m "{ATTR}"')
    assert result.returncode == 2
    assert "attribution" in result.stderr
    assert bash('gh pr create --body "Generated with ' + 'Claude Code"').returncode == 2
    assert bash('git commit -m "feat: plain message"').returncode == 0


def test_blocks_attribution_in_message_file(tmp_path: Path) -> None:
    msg = tmp_path / "msg.txt"
    msg.write_text("feat: x\n\n" + ATTR + "\n")
    assert bash(f"git commit -F {msg}").returncode == 2
    clean = tmp_path / "clean.txt"
    clean.write_text("feat: x\n")
    assert bash(f"git commit -F {clean}").returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "git config user.email a@b.c",
        "git config --global user.name x",
        "export GIT_AUTHOR_" + "NAME=x",
        "GIT_COMMITTER_" + "EMAIL=x git commit -m y",
        "git push --force origin main",
        "git push origin v0.1.0",
        "gh repo create foo --public",
        "aws s3 ls",
        "echo hi\n" + AWS + " s3 ls",
        "  sudo " + AWS + " sts get-caller-identity",
        "AWS_PROFILE=x " + AWS + " s3 ls",
        "git -c " + "user.name=X commit -m y",
        "git commit --" + "author='X <x@example.com>' -m y",
        "git push -" + "fu origin main",
        "git push --" + "follow-tags origin main",
        "curl -X POST https://api." + "twilio.com/2010-04-01/Accounts/AC1/Messages.json",
    ],
)
def test_blocks_identity_and_unsafe_git(command: str) -> None:
    assert bash(command).returncode == 2


def test_allows_normal_push() -> None:
    assert bash("git push -u origin feat/x").returncode == 0


def test_blocks_test_partition_edit(tmp_path: Path) -> None:
    datasets = ROOT / "evals" / "datasets"
    datasets.mkdir(parents=True, exist_ok=True)
    existing = datasets / "zz_hook_probe.test.v1.jsonl"
    existing.write_text("{}\n")
    try:
        result = write(existing, "{}\n{}\n")
        assert result.returncode == 2
        assert "append-only" in result.stderr
    finally:
        existing.unlink()
    new_file = datasets / "zz_hook_probe.v2.jsonl"
    assert write(new_file, "{}\n").returncode == 0


def test_blocks_non_synthetic_phone(tmp_path: Path) -> None:
    assert write(tmp_path / "a.py", f'PHONE = "{REAL_PHONE}"').returncode == 2
    formatted_us = "+1 415 " + "555 0100"
    formatted_es = "+34 612 " + "345 678"
    assert write(tmp_path / "d.py", f'P = "{formatted_us}"').returncode == 2
    assert write(tmp_path / "e.py", f'P = "{formatted_es}"').returncode == 2
    assert write(tmp_path / "b.py", 'PHONE = "+15550001234"').returncode == 0
    assert write(tmp_path / "c.py", 'PHONE = "+34555123456"').returncode == 0


def test_stop_hook_never_loops() -> None:
    result = run_hook("stop_check.py", {"stop_hook_active": True}, env={"STOP_CHECK_CMD": "false"})
    assert result.returncode == 0


def test_stop_hook_reports_failure_once() -> None:
    result = run_hook(
        "stop_check.py",
        {"stop_hook_active": False, "cwd": str(ROOT)},
        env={"STOP_CHECK_CMD": "false"},
    )
    assert result.returncode == 2
    assert "failed" in result.stderr
    ok = run_hook(
        "stop_check.py",
        {"stop_hook_active": False, "cwd": str(ROOT)},
        env={"STOP_CHECK_CMD": "true"},
    )
    assert ok.returncode == 0


def test_post_edit_format_formats_python(tmp_path: Path) -> None:
    scratch = ROOT / ".pytest-hooks"
    scratch.mkdir(exist_ok=True)
    target = scratch / "ugly.py"
    target.write_text("x   =   1\n")
    result = run_hook(
        "post_edit_format.py",
        {"tool_name": "Write", "tool_input": {"file_path": str(target)}, "cwd": str(ROOT)},
    )
    assert result.returncode == 0
    assert target.read_text() == "x = 1\n"
    target.unlink()
    scratch.rmdir()


def test_post_edit_format_ignores_files_outside_project(tmp_path: Path) -> None:
    target = tmp_path / "outside.py"
    target.write_text("x   =   1\n")
    run_hook("post_edit_format.py", {"tool_input": {"file_path": str(target)}, "cwd": str(ROOT)})
    assert target.read_text() == "x   =   1\n"


def test_post_edit_format_is_noop_for_unknown_files(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("keep   spacing\n")
    run_hook("post_edit_format.py", {"tool_input": {"file_path": str(target)}, "cwd": str(ROOT)})
    assert target.read_text() == "keep   spacing\n"


def test_session_start_is_noop_outside_cloud() -> None:
    result = subprocess.run(
        ["bash", str(HOOKS / "session_start.sh")],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CLAUDE_CODE_REMOTE": "false"},
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_hooks_registered_in_settings() -> None:
    hooks = json.loads((ROOT / ".claude" / "settings.json").read_text())["hooks"]
    assert {"SessionStart", "PreToolUse", "PostToolUse", "Stop"} <= set(hooks)
    assert "Bash|Write|Edit" in hooks["PreToolUse"][0]["matcher"]
