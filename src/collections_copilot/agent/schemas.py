"""Structured-output schemas for every LLM route (validated before anything is persisted)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NbaAction = Literal["remind", "payment_plan", "escalate", "pause", "no_action"]
ReplyLabel = Literal["promise_to_pay", "dispute", "wrong_number", "paid", "stop", "other"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PriorityRationale(_Strict):
    rationale: str = Field(min_length=1, max_length=400)


class NextBestAction(_Strict):
    action: NbaAction
    rationale: str = Field(min_length=1, max_length=600)
    confidence: float = Field(ge=0.0, le=1.0)
    cited_note_ids: list[str] = Field(max_length=10)


class Draft(_Strict):
    body: str = Field(min_length=1, max_length=280)


class PromiseExtraction(_Strict):
    amount_cents: int | None = Field(default=None, gt=0)
    promised_date: date | None = None


class ReplyClassification(_Strict):
    label: ReplyLabel
    confidence: float = Field(ge=0.0, le=1.0)
    promise: PromiseExtraction | None = None


class ToneJudgement(_Strict):
    label: Literal[0, 1]
    reason: str = Field(min_length=1, max_length=400)


SCHEMAS: dict[str, type[BaseModel]] = {
    "PriorityRationale": PriorityRationale,
    "NextBestAction": NextBestAction,
    "Draft": Draft,
    "ReplyClassification": ReplyClassification,
    "ToneJudgement": ToneJudgement,
}
