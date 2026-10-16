"""STOP handling by rule (FR-8): opt-out keywords are recognised before any model sees a reply.

Three rules, in English and Spanish:
1. The whole message is a keyword ("STOP", "cancel", "Alto", "dar de baja").
2. An unambiguous opt-out word anywhere ("unsubscribe", "STOPALL", "opt out", "darme de baja").
3. A request to stop contacting ("please stop texting me", "STOP please", "dejen de escribirme").

Ambiguous words in a longer message ("End of month", "Cancel the invoice", "don't stop the
service") are not opt-outs here; the classifier can still label them `stop`.
"""

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
        "OPT OUT",
        "REVOKE",
        "BAJA",
        "ALTO",
        "PARAR",
        "PARE",
        "CANCELAR",
        "DESUSCRIBIR",
        "NO MAS",
        "DAR DE BAJA",
        "DARME DE BAJA",
    }
)
_PUNCT = re.compile(r"[^\w\s-]")
_ANYWHERE = re.compile(
    r"\b(UNSUBSCRIBE|STOPALL|OPT-?OUT|OPT OUT|DESUSCRIBIR(ME)?|DAR(ME)? DE BAJA)\b"
)
_CONTACT = r"(TEXT|TEXTING|TEXTS|MESSAGE|MESSAGES|MESSAGING|SMS|CONTACT|CONTACTING|CALLING|SENDING ME|EMAILING)"
_REQUESTS = [
    # "STOP", "STOP please", "please stop", "STOP now" — but not "don't stop ..."
    re.compile(r"^(PLEASE |PLS |JUST )?STOP( (PLEASE|PLS|NOW|IT|THIS|THANKS|THANK YOU))*$"),
    re.compile(rf"(?<!DON T )(?<!DO NOT )\bSTOP {_CONTACT}\b"),
    re.compile(rf"\bNO (MORE )?{_CONTACT}\b"),
    re.compile(r"^BAJA\b"),
    re.compile(r"\bDEJ(EN|E|AR) DE (ESCRIBIR|ENVIAR|MANDAR|LLAMAR|CONTACTAR)"),
    re.compile(r"\bNO (ME )?(ESCRIBAN|ESCRIBAS|ENVIEN|MANDEN|LLAMEN|CONTACTEN)\b"),
    re.compile(r"\bNO MAS (MENSAJES|SMS|TEXTOS)\b"),
]


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(_PUNCT.sub(" ", text).upper().split())


def is_stop(body: str) -> bool:
    text = _normalise(body)
    if not text:
        return False
    if text in STOP_KEYWORDS:
        return True
    if _ANYWHERE.search(text):
        return True
    return any(p.search(text) for p in _REQUESTS)
