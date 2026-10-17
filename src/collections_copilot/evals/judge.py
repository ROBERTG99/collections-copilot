"""Tone judge (`prompts/judge_tone.v1.md`) and its calibration against `tone_calibration.v1`.

The judge reads one draft and returns 1 (follows the tone policy) or 0. Calibration compares it
with labels by construction (`evalgate calibrate`); the ≥ 0.8 agreement gate applies to the live
judge only, because offline the judge is FakeLLM's keyword stub.
"""

from __future__ import annotations

from pathlib import Path

from evalgate.calibrate import CalibrationResult, calibrate
from evalgate.loader import load_labels, load_rubric
from evalgate.models import LabelRow

from collections_copilot.agent.prompts import UntrustedNote, render
from collections_copilot.agent.schemas import ToneJudgement
from collections_copilot.config import get_settings
from collections_copilot.llm.gateway import LLMGateway, routes
from collections_copilot.llm.providers import build_provider

RUBRIC = Path("evals/rubrics/tone.yaml")
LABELS = Path("evals/datasets/tone_calibration.v1.jsonl")
NO_JUDGEMENT = -1  # the judge failed: counts as a disagreement, never as a pass


def gateway() -> LLMGateway:
    settings = get_settings()
    return LLMGateway.from_settings(settings, build_provider(settings))


async def judge_tone(row: LabelRow, gw: LLMGateway | None = None) -> int:
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
