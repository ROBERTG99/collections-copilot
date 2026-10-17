"""Character rules (fail closed): what the other text rules read, and what they refuse to read.

`normalize` folds compatibility forms (fullwidth letters and digits, fullwidth and ideographic
full stops, `[.]`) so the amount, phrase and link rules see what a phone would display.
`violations` blocks invisible format characters (zero-width, bidi overrides) and letters outside
the Latin script, except inside the account's own name: an SMS in English or Spanish never needs
them, and they are how look-alike hosts (`раураl.com`) and disguised words (`ѕue`) are built.
"""

from __future__ import annotations

import re
import unicodedata

_DOTS = re.compile(r"[。．｡]|\[\.\]|\(\.\)")
_ALLOWED_CONTROLS = frozenset("\n\t")


def normalize(text: str) -> str:
    return _DOTS.sub(".", unicodedata.normalize("NFKC", text))


def _latin(ch: str) -> bool:
    return unicodedata.name(ch, "").startswith("LATIN ")


def violations(text: str, account_name: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    hidden = sorted(
        {
            f"U+{ord(ch):04X}"
            for ch in text
            if unicodedata.category(ch) in ("Cf", "Co", "Cs")
            or (unicodedata.category(ch) == "Cc" and ch not in _ALLOWED_CONTROLS)
        }
    )
    if hidden:
        out.append(("invisible_characters", f"invisible characters: {', '.join(hidden)}"))
    rest = text.replace(account_name, " ") if account_name else text
    foreign = sorted({ch for ch in normalize(rest) if ch.isalpha() and not _latin(ch)})
    if foreign:
        out.append(("non_latin_letters", f"letters outside the Latin script: {''.join(foreign)}"))
    return out
