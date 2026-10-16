"""Forbidden phrases: threats, legal intimidation and harassment (EN and ES)."""

from __future__ import annotations

import re

PATTERNS = [
    r"\bsue\b",
    r"\blawsuits?\b",
    r"\barrest(?:ed)?\b",
    r"\bjail\b",
    r"\bprison\b",
    r"\bpolice\b",
    r"\bgarnish\w*",
    r"\bfinal warning\b",
    r"\bcriminal\b",
    r"\blegal action\b",
    r"\bcollections? agency\b",
    r"\bdemand(?:a|aremos|arle)\b",
    r"\bc[aá]rcel\b",
    r"\bpolic[ií]a\b",
    r"\bembarg\w*",
    r"\barrest\w*",
    r"\bprisi[oó]n\b",
    r"\bpenal\b",
    r"\b[uú]ltimo aviso\b",
    r"\bacciones legales\b",
]
FORBIDDEN = re.compile("|".join(PATTERNS), re.IGNORECASE)


def find(text: str) -> list[str]:
    return [m.group(0) for m in FORBIDDEN.finditer(text)]
