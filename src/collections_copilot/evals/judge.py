"""Tone judge (`prompts/judge_tone.v1.md`) and its calibration against `tone_calibration.v1`.

The judge reads one draft and returns 1 (follows the tone policy) or 0. Calibration compares it
with labels by construction: agreement and Cohen's kappa (evalgate's), gated by the rubric's
thresholds. The ≥ 0.8 agreement gate applies to the live judge only, because offline the judge is
FakeLLM's keyword stub.

The rubric is an `evalgate.rubric.v1` document. The label rows are read here rather than with
evalgate's `label_row.v1` reader: the frozen `tone_calibration.v1` dataset predates that contract
(its `input` is an object and pointwise rows have no `reference`), and committed eval datasets
are never edited.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from evalgate.contracts import Rubric
from evalgate.judge.calibrate import cohen_kappa
from pydantic import BaseModel, ConfigDict, Field

from collections_copilot.agent.prompts import UntrustedNote, render
from collections_copilot.agent.schemas import ToneJudgement
from collections_copilot.config import get_settings
from collections_copilot.llm.gateway import LLMGateway, routes
from collections_copilot.llm.providers import build_provider

RUBRIC = Path("evals/rubrics/tone.yaml")
LABELS = Path("evals/datasets/tone_calibration.v1.jsonl")
NO_JUDGEMENT = -1  # the judge failed: counts as a disagreement, never as a pass


class ToneLabel(BaseModel):
    """One row of `tone_calibration.v1`."""

    model_config = ConfigDict(extra="forbid")

    id: str
    rubric: str
    split: Literal["dev", "test"]
    input: dict[str, Any]
    reference: str | None = None
    candidate: str
    candidate_b: str | None = None
    label: int
    source: Literal["construction", "maintainer"] = "construction"
    meta: dict[str, Any] = Field(default_factory=dict)


@dataclass
class CalibrationResult:
    n: int
    agreement: float
    kappa: float
    confusion: dict[str, int] = field(default_factory=dict)
    passed: bool = False


def load_rubric(path: Path = RUBRIC) -> Rubric:
    return Rubric.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_labels(path: Path = LABELS, split: str | None = None) -> list[ToneLabel]:
    rows = [
        ToneLabel.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [row for row in rows if split is None or row.split == split]


def calibrate(
    rubric: Rubric, labels: Sequence[ToneLabel], judge: Callable[[ToneLabel], int]
) -> CalibrationResult:
    predicted = [str(judge(row)) for row in labels]
    gold = [str(row.label) for row in labels]
    n = len(gold)
    agreement = sum(1 for p, g in zip(predicted, gold, strict=True) if p == g) / n if n else 0.0
    kappa = cohen_kappa(gold, predicted) if n else 0.0
    thresholds = rubric.thresholds
    return CalibrationResult(
        n=n,
        agreement=round(agreement, 4),
        kappa=round(kappa, 4),
        confusion=dict(Counter(f"{g}->{p}" for g, p in zip(gold, predicted, strict=True))),
        passed=n > 0 and agreement >= thresholds.agreement_min and kappa >= thresholds.kappa_min,
    )


def gateway() -> LLMGateway:
    settings = get_settings()
    return LLMGateway.from_settings(settings, build_provider(settings))


async def judge_tone(row: ToneLabel, gw: LLMGateway | None = None) -> int:
    rendered = render(
        "judge_tone",
        {"action": row.input.get("action"), "language": row.input.get("language", "en")},
        [UntrustedNote(id=row.id, source="draft", body=row.candidate)],
    )
    gw = gw or gateway()
    result = await gw.structured(
        routes(get_settings())["judge_tone"],
        system=rendered.system,
        user=rendered.user,
        schema=ToneJudgement,
        prompt_version=f"{rendered.name}.{rendered.version}",
        prompt_hash=rendered.hash,
        language=str(row.input.get("language", "en")),
    )
    if result.ok and result.parsed is not None:
        return int(result.parsed.label)
    return NO_JUDGEMENT


async def calibrate_tone(split: str | None = "test") -> CalibrationResult:
    rubric = load_rubric(RUBRIC)
    labels = load_labels(LABELS, split)
    gw = gateway()
    predictions = {row.id: await judge_tone(row, gw) for row in labels}
    return calibrate(rubric, labels, lambda row: predictions[row.id])
