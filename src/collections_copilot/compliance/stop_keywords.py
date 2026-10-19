"""STOP handling by rule (FR-8): opt-out keywords are recognised before any model sees a reply.

Rules, in English and Spanish, on the upper-cased ASCII-folded text:
1. The message is only keywords and courtesy words ("STOP", "STOP ALL", "Alto por favor", "Basta").
2. An unambiguous opt-out phrase anywhere ("unsubscribe", "opt me out", "darme de baja",
   "remove me from your list", "cancelar suscripción").
3. A request to stop texting ("please stop texting me", "don't text me again", "leave me alone",
   "dejen de escribirme", "no me molesten", "no quiero recibir mensajes", "I'd rather not get
   these texts anymore", "prefiero no recibir más avisos"), unless negated
   ("don't stop texting me").

Ambiguous words inside longer messages ("End of month", "Cancel the invoice", "no text received
with the link", "stop calling me, text me instead") are not opt-outs here; the classifier can
still label them `stop`. Calls are not SMS, so "stop calling" is not an SMS opt-out.
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
        "REVOKE",
        "BAJA",
        "ALTO",
        "PARA",
        "PARAR",
        "PARE",
        "BASTA",
        "CANCELAR",
        "DESUSCRIBIR",
    }
)
COURTESY = frozenset(
    {
        "PLEASE",
        "PLS",
        "NOW",
        "ALL",
        "IT",
        "THANKS",
        "THANK",
        "YOU",
        "POR",
        "FAVOR",
        "YA",
        "GRACIAS",
        "TODO",
        "TODOS",
    }
)
WHOLE_PHRASES = frozenset({"OPT OUT", "NO MAS", "DAR DE BAJA", "DARME DE BAJA"})
_PUNCT = re.compile(r"[^\w\s-]")
_ANYWHERE = re.compile(
    r"\b(UNSUBSCRIBE|STOPALL|OPT-?OUT|OPT ME OUT|OPT US OUT|DESUSCRIBIR(ME|NOS)?"
    r"|(DAR(ME|NOS)?|DEN(ME|NOS)|ME DOY|NOS DAMOS) DE BAJA"
    r"|CANCELAR (LA |MI )?SUSCRIPCION|REMOVE (ME|US|MY NUMBER) FROM|TAKE (ME|US) OFF)\b"
)
_TEXTS = r"(TEXT|TEXTS|TEXTING|MESSAGE|MESSAGES|MESSAGING|SMS|CONTACT|CONTACTING)"
_NEGATED = re.compile(
    r"\b(DON T|DONT|DO NOT|PLEASE DON T) STOP\b|\bNO (DEJEN|DEJES|DEJE|PAREN|PARES) DE\b"
)
_REQUESTS = [
    re.compile(r"\bSTOP (SENDING|TEXTING|MESSAGING|CONTACTING)\b"),
    re.compile(rf"\bSTOP (THE|THESE|ALL|YOUR) {_TEXTS}\b"),
    re.compile(rf"\b(DON T|DONT|DO NOT|NEVER|PLEASE DON T) {_TEXTS} (ME|US)\b"),
    re.compile(r"\bLEAVE (ME|US) ALONE\b"),
    re.compile(rf"\bNO MORE {_TEXTS}\b"),
    re.compile(r"\b(DEJ(EN|E|A|AR)|PAR(EN|E|A|AR)) DE (ESCRIBIR|ENVIAR|MANDAR|CONTACTAR)"),
    re.compile(r"\bNO (ME |NOS )?(ESCRIBAN|ESCRIBAS|ENVIEN|MANDEN|CONTACTEN|MOLESTEN|MOLESTES)\b"),
    re.compile(r"\bNO QUIERO (RECIBIR )?(MAS )?(MENSAJES|SMS|TEXTOS|AVISOS)\b"),
    re.compile(r"\b(PREFIERO|QUIERO) NO RECIBIR (MAS )?(MENSAJES|SMS|TEXTOS|AVISOS)\b"),
    re.compile(
        rf"\b(RATHER NOT|DON T WANT|DONT WANT|DO NOT WANT|NO LONGER WANT)( TO)?( GET| RECEIVE)?"
        rf"( THESE| YOUR| ANY)? {_TEXTS} ANY ?MORE\b"
    ),
    re.compile(r"\bNO MAS (MENSAJES|SMS|TEXTOS)\b"),
]


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(_PUNCT.sub(" ", text).upper().split())


def is_stop(body: str) -> bool:
    text = _normalise(body)
    if not text:
        return False
    words = text.split()
    if text in WHOLE_PHRASES or (
        any(w in STOP_KEYWORDS for w in words)
        and all(w in STOP_KEYWORDS or w in COURTESY for w in words)
    ):
        return True
    if _ANYWHERE.search(text):
        return True
    if _NEGATED.search(text):
        return False
    return any(p.search(text) for p in _REQUESTS)
