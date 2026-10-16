"""STOP handling by rule (FR-8): opt-out keywords are recognised before any model sees a reply."""

from __future__ import annotations

import re
import unicodedata

STOP_KEYWORDS = frozenset(
    {
        "STOP",
        "STOPALL",
        "UNSUBSCRIBE",
        "CANCEL",
        "END",
        "QUIT",
        "OPTOUT",
        "OPT-OUT",
        "REVOKE",
        "BAJA",
        "ALTO",
        "PARAR",
        "PARE",
        "CANCELAR",
        "DESUSCRIBIR",
    }
)
_PUNCT = re.compile(r"[^\w\s-]")


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return _PUNCT.sub(" ", text).strip().upper()


def is_stop(body: str) -> bool:
    """True when the message is a stop keyword, alone or as its first word ("STOP please")."""
    words = _normalise(body).split()
    if not words:
        return False
    if " ".join(words) in {"NO MAS", "DARME DE BAJA", "DAR DE BAJA"}:
        return True
    return words[0] in STOP_KEYWORDS and len(words) <= 4
