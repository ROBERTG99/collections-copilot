"""T-39: eval datasets have the §11 counts and splits, are deterministic and match MANIFEST.json."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
from evalgate.suite.loader import load_dataset

from collections_copilot.evals.judge import load_labels
from data.generator import evals as gen

ROOT = Path(__file__).resolve().parents[3]
DATASETS = ROOT / "evals" / "datasets"

COUNTS = {
    "nba.v1": (180, 120),
    "replies.v1": (120, 80),
    "compliance_adversarial.v1": (30, 30),
}


def test_counts_splits_and_determinism(tmp_path: Path) -> None:
    first = gen.write_all(tmp_path / "a")
    second = gen.write_all(tmp_path / "b")
    assert first == second  # same seed, same digests
    for name in [*COUNTS, "tone_calibration.v1"]:
        a = (tmp_path / "a" / f"{name}.jsonl").read_bytes()
        assert a == (tmp_path / "b" / f"{name}.jsonl").read_bytes(), name

    for name, (dev, test) in COUNTS.items():
        rows = load_dataset(tmp_path / "a" / f"{name}.jsonl")
        splits = Counter(r.split for r in rows)
        assert (splits["dev"], splits["test"]) == (dev, test), name
        assert all(r.expected is not None for r in rows), name
    labels = load_labels(tmp_path / "a" / "tone_calibration.v1.jsonl")
    assert len(labels) == 80
    assert {row.label for row in labels} == {0, 1}

    # The committed files are exactly what the generator writes, and the manifest says so.
    manifest = json.loads((DATASETS / "MANIFEST.json").read_text())
    assert manifest == first
    assert manifest["seed"] == 4242
    for name, entry in manifest["datasets"].items():
        committed = (DATASETS / f"{name}.jsonl").read_bytes()
        assert hashlib.sha256(committed).hexdigest() == entry["sha256"], name


def test_labels_follow_the_policy_not_the_model() -> None:
    """NBA labels come from the scenario (notes) or the deterministic rules, never from a model."""
    rows = gen.nba_rows()
    by_scenario: dict[str, Counter[str]] = {}
    for row in rows:
        scenario = row["meta"]["scenario"]
        by_scenario.setdefault(scenario, Counter())[row["expected"]["action"]] += 1
    assert set(by_scenario["dispute"]) == {"pause"}
    assert set(by_scenario["wrong_contact"]) == {"pause"}
    assert set(by_scenario["hardship"]) == {"payment_plan"}
    assert set(by_scenario["escalate"]) == {"escalate"}
    assert set(by_scenario["paid_claim"]) == {"no_action"}
    actions = Counter(r["expected"]["action"] for r in rows)
    assert set(actions) == {"remind", "payment_plan", "escalate", "pause", "no_action"}
    for row in rows:
        if row["expected"]["cited_note_ids"]:
            note_ids = {n["id"] for n in row["input"]["input_facts"]["notes"]}
            assert set(row["expected"]["cited_note_ids"]) <= note_ids


@pytest.mark.parametrize("name", [*COUNTS, "tone_calibration.v1"])
def test_only_synthetic_contact_data(name: str) -> None:
    text = (DATASETS / f"{name}.jsonl").read_text()
    import re

    for phone in re.findall(r"\+\d{8,15}", text):
        assert phone.startswith(("+1555", "+34555")), phone
    for email in re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", text):
        assert email.endswith(".example"), email


def test_replies_cover_every_label_and_stop_variants() -> None:
    rows = gen.reply_rows()
    labels = Counter(r["expected"]["label"] for r in rows)
    assert set(labels) == {"promise_to_pay", "dispute", "wrong_number", "paid", "stop", "other"}
    assert labels["stop"] >= 25
    adversarial = [r for r in rows if "adversarial" in r["tags"]]
    assert len(adversarial) >= 10
    # Write-back is expected only for a valid promise (amount within the balance, date 1-90 days).
    for row in rows:
        exp = row["expected"]
        if exp["write_back"]:
            assert exp["label"] == "promise_to_pay"
            assert (
                0 < exp["promise"]["amount_cents"] <= row["input"]["account"]["open_balance_cents"]
            )


def test_compliance_rows_state_what_may_be_sent() -> None:
    rows = gen.compliance_rows()
    categories = Counter(r["meta"]["category"] for r in rows)
    assert len(categories) == 10
    assert set(categories.values()) == {6}
    assert {r["input"]["approver"]["kind"] for r in rows} == {"approve", "edit", "none"}
    assert any(r["expected"]["may_send"] for r in rows)
    assert any(not r["expected"]["may_send"] for r in rows)
