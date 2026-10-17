"""T-47 (NFR-1): the API container runs several uvicorn workers, and /metrics reports their totals."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from collections_copilot.api import serve
from collections_copilot.config import Settings

BUMP = (
    "from collections_copilot.observability.metrics import APPROVALS; "
    "APPROVALS.labels(decision='approve').inc()"
)
SCRAPE = (
    "from collections_copilot.observability.metrics import scrape_registry; "
    "print(scrape_registry().get_sample_value('cc_approvals_total', {'decision': 'approve'}))"
)


@pytest.fixture(autouse=True)
def _restore_multiproc_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # setenv first so monkeypatch restores the variable's absence after main() sets it
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", "unset-by-the-test")
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR")


def _python(code: str, env: dict[str, str]) -> str:
    return subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_metrics_from_every_worker_process_are_summed(tmp_path: Path) -> None:
    env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(tmp_path)}
    _python(BUMP, env)
    _python(BUMP, env)
    assert _python(SCRAPE, env) == "2.0"


def test_launcher_runs_the_configured_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = tmp_path / "counter_123.db"
    stale.write_bytes(b"old")
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda target, **kw: calls.append((target, kw)))
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))

    serve.main(Settings(api_workers=3))

    [(target, options)] = calls
    assert target == "collections_copilot.api.main:app"
    assert options["factory"] is True
    assert options["workers"] == 3
    assert options["port"] == 8310
    assert options["access_log"] is False
    assert options["server_header"] is False
    assert not stale.exists()  # counters of an earlier container run start from zero


def test_several_workers_get_a_metrics_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert Path(serve.METRICS_DIR).is_relative_to("/tmp")  # the writable volume, read-only root
    monkeypatch.setattr(serve, "METRICS_DIR", str(tmp_path / "metrics"))
    monkeypatch.setattr(serve.uvicorn, "run", lambda target, **kw: None)
    serve.main(Settings(api_workers=2))
    assert os.environ["PROMETHEUS_MULTIPROC_DIR"] == str(tmp_path / "metrics")
    assert (tmp_path / "metrics").is_dir()


def test_one_worker_keeps_the_in_process_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda target, **kw: calls.append(kw))
    serve.main(Settings(api_workers=1))
    assert calls[0]["workers"] == 1
    assert "PROMETHEUS_MULTIPROC_DIR" not in os.environ


def test_api_workers_is_bounded() -> None:
    with pytest.raises(ValueError, match="api_workers"):
        Settings(api_workers=0)
