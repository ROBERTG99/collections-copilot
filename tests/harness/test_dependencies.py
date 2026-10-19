"""T-02: the sibling dependencies (or their documented fallbacks) are importable and usable."""

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_evalgate_and_gateway_available(tmp_path: Path) -> None:
    import evalgate
    from mcpgw_lite.policy import Policy

    assert evalgate.__version__ == "0.1.0"
    out = tmp_path / "r.json"
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "evalgate.cli",
            "run",
            "tests/fixtures/evalgate/trivial.yaml",
            "--split",
            "dev",
            "--out",
            str(out),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    report = json.loads(out.read_text())
    assert report["schema"] == "evalgate.report.v1"
    assert report["metrics"]["label_accuracy"]["value"] == 1.0

    policy = Policy.load(ROOT / "deploy/mcpgw/policy/collections.yaml")
    scopes = frozenset({"ledger:read", "ledger:write"})
    soon = (date.today() + timedelta(days=5)).isoformat()
    small = {"amount_cents": 120000, "promised_date": soon}
    assert (
        policy.evaluate("cc-worker", scopes, "ledger.record_promise_to_pay", small).effect
        == "allow"
    )
    big = {"amount_cents": 800000, "promised_date": soon}
    assert policy.evaluate("cc-worker", scopes, "ledger.record_promise_to_pay", big).effect == (
        "require_approval"
    )
    assert policy.evaluate("other-agent", scopes, "ledger.get_balance", {}).effect == "deny"
