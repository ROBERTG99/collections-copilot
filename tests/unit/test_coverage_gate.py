"""Line coverage ≥ 85% on scoring, compliance, agent, llm and api, ≥ 95% on compliance
≥ 70% overall. scripts/coverage_gate.py reads coverage.xml and fails below any of them."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "coverage_gate.py"


def _xml(path: Path, files: dict[str, tuple[int, int]]) -> Path:
    """files: filename -> (covered, total) lines."""
    classes = []
    for name, (covered, total) in files.items():
        lines = "".join(
            f'<line number="{i + 1}" hits="{1 if i < covered else 0}"/>' for i in range(total)
        )
        classes.append(f'<class name="x" filename="{name}"><lines>{lines}</lines></class>')
    path.write_text(
        '<?xml version="1.0" ?><coverage><packages><package name="p"><classes>'
        + "".join(classes)
        + "</classes></package></packages></coverage>"
    )
    return path


def _run(xml: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(xml)], capture_output=True, text=True, check=False
    )


GOOD = {
    "scoring/priority.py": (90, 100),
    "compliance/engine.py": (96, 100),
    "agent/case_graph.py": (86, 100),
    "llm/gateway.py": (85, 100),
    "api/main.py": (90, 100),
    "worker/main.py": (10, 100),
}


def test_passes_when_every_threshold_is_met(tmp_path: Path) -> None:
    result = _run(_xml(tmp_path / "c.xml", GOOD))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "compliance" in result.stdout
    assert "PASS" in result.stdout


def test_fails_on_a_core_package_below_85(tmp_path: Path) -> None:
    result = _run(_xml(tmp_path / "c.xml", {**GOOD, "api/main.py": (84, 100)}))
    assert result.returncode == 1
    assert "api" in result.stdout
    assert "FAIL" in result.stdout


def test_compliance_needs_95(tmp_path: Path) -> None:
    result = _run(_xml(tmp_path / "c.xml", {**GOOD, "compliance/engine.py": (94, 100)}))
    assert result.returncode == 1


def test_fails_below_70_overall(tmp_path: Path) -> None:
    files = {**GOOD, "worker/main.py": (0, 2000)}
    result = _run(_xml(tmp_path / "c.xml", files))
    assert result.returncode == 1
    assert "overall" in result.stdout


def test_a_missing_core_package_fails(tmp_path: Path) -> None:
    files = {k: v for k, v in GOOD.items() if not k.startswith("llm/")}
    result = _run(_xml(tmp_path / "c.xml", files))
    assert result.returncode == 1
    assert "llm" in result.stdout
