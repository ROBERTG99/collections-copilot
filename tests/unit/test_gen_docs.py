"""make docs: scripts/gen_docs.py writes each Terraform module's README with terraform-docs and,
with --check, fails when a README is out of date."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_docs", ROOT / "scripts" / "gen_docs.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_terraform_docs(directory: Path) -> str:
    return f"## Inputs\n\n{directory.name}\n"


def test_writes_a_generated_readme_per_module(tmp_path: Path) -> None:
    gen = load()
    for name in ("alb", "kms"):
        (tmp_path / "modules" / name).mkdir(parents=True)
        (tmp_path / "modules" / name / "main.tf").write_text("")
    assert gen.main(["--root", str(tmp_path)], render=fake_terraform_docs) == 0
    readme = (tmp_path / "modules" / "alb" / "README.md").read_text()
    assert readme.startswith(gen.HEADER)
    assert "## Inputs\n\nalb\n" in readme


def test_check_fails_when_a_readme_is_stale(tmp_path: Path) -> None:
    gen = load()
    module = tmp_path / "modules" / "kms"
    module.mkdir(parents=True)
    (module / "main.tf").write_text("")
    assert gen.main(["--root", str(tmp_path), "--check"], render=fake_terraform_docs) == 1
    assert gen.main(["--root", str(tmp_path)], render=fake_terraform_docs) == 0
    assert gen.main(["--root", str(tmp_path), "--check"], render=fake_terraform_docs) == 0
