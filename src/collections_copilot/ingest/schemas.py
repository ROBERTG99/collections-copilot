"""AR-aging row schema (one row per invoice) with strict validation."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import cache
from typing import Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, field_validator

from collections_copilot.config import get_settings
from collections_copilot.integrations.sms.base import is_synthetic

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
CSV_COLUMNS = [
    "external_ref",
    "name",
    "phone_e164",
    "timezone",
    "segment",
    "language",
    "currency",
    "invoice_number",
    "amount",
    "issued_on",
    "due_on",
    "status",
    "dispute_reason",
    "note",
]


class AgingRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_ref: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    phone_e164: str
    timezone: str = Field(min_length=1, max_length=64)
    segment: Literal["enterprise", "mid_market", "smb"]
    language: Literal["en", "es"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    # An identifier, never free text: it reaches prompts and SMS (M6 security audit M2).
    invoice_number: str = Field(pattern=r"^[A-Za-z0-9._/-]{1,64}$")
    amount: str = Field(description="Decimal amount in major units, e.g. 1200.50")
    issued_on: date
    due_on: date
    status: Literal["open", "paid", "disputed", "written_off"] = "open"
    dispute_reason: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("phone_e164")
    @classmethod
    def _e164(cls, v: str) -> str:
        if not E164.match(v):
            raise ValueError("phone_e164 must be E.164 (+ and 8-15 digits)")
        if get_settings().synthetic_numbers_only and not is_synthetic(v):
            raise ValueError("phone_e164 must be in the synthetic ranges +1555… or +34555…")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        if v not in _zones():
            raise ValueError("unknown IANA time zone")
        return v

    @field_validator("amount")
    @classmethod
    def _amount(cls, v: str) -> str:
        try:
            d = Decimal(v)
        except InvalidOperation as exc:
            raise ValueError("amount must be a decimal number") from exc
        if not d.is_finite() or d <= 0:
            raise ValueError("amount must be greater than 0")
        if d.as_tuple().exponent < -2:  # type: ignore[operator]
            raise ValueError("amount has more than 2 decimals")
        return v

    @property
    def amount_cents(self) -> int:
        return int(Decimal(self.amount) * 100)


@cache
def _zones() -> frozenset[str]:
    return frozenset(available_timezones())
