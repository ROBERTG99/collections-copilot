"""T-07: api → SQS → worker → FakeLLM → interrupt, in Docker."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.docker]
ROOT = Path(__file__).resolve().parents[2]


def test_case_reaches_approval_interrupt() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/demo.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "awaiting_approval" in result.stdout
