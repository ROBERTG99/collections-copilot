"""Every outbound SMS ends with the opt-out footer in the account language."""

from __future__ import annotations

FOOTERS = {"en": "Reply STOP to opt out.", "es": "Responde STOP para darte de baja."}


def footer_for(language: str) -> str:
    return FOOTERS.get(language, FOOTERS["en"])


def has_footer(body: str, language: str) -> bool:
    return body.rstrip().endswith(footer_for(language))
