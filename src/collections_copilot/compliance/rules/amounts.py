"""Money in a message must exist in the ledger: an open invoice amount or the open balance."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Protocol

CURRENCY = r"(?:US\$|\$|€|USD|EUR|usd|eur|dólares|euros)"
NUMBER = r"\d{1,3}(?:[.,  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
MONEY = re.compile(
    rf"(?P<pre>{CURRENCY}\s?)?(?<![\w\-/.,])(?P<num>{NUMBER})(?![\w\-/]|[.,]\d)(?P<post>\s?{CURRENCY})?"
)


class _Invoice(Protocol):
    @property
    def amount_cents(self) -> int: ...

    @property
    def status(self) -> str: ...


def to_cents(token: str) -> int | None:
    t = token.replace(" ", " ").replace(" ", "")
    if "." in t and "," in t:
        dec = "." if t.rfind(".") > t.rfind(",") else ","
    elif "," in t or "." in t:
        sep = "," if "," in t else "."
        tail = t.rsplit(sep, 1)[1]
        dec = sep if len(tail) in (1, 2) and t.count(sep) == 1 else ""
    else:
        dec = ""
    if dec:
        whole, frac = t.rsplit(dec, 1)
        whole = re.sub(r"[.,]", "", whole)
        frac = (frac + "0")[:2]
    else:
        whole, frac = re.sub(r"[.,]", "", t), "00"
    if not whole.isdigit():
        return None
    return int(whole) * 100 + int(frac)


def extract(text: str) -> list[tuple[str, int]]:
    """Money mentions: numbers next to a currency marker, or numbers with decimals."""
    found = []
    for m in MONEY.finditer(text):
        num = m.group("num")
        has_currency = bool(m.group("pre") or m.group("post"))
        has_decimals = bool(re.search(r"[.,]\d{1,2}$", num))
        if not (has_currency or has_decimals):
            continue
        cents = to_cents(num)
        if cents is not None:
            found.append((num, cents))
    return found


def allowed_amounts(invoices: Iterable[_Invoice], extra_cents: Iterable[int] = ()) -> set[int]:
    open_amounts = [i.amount_cents for i in invoices if i.status == "open"]
    allowed = set(open_amounts) | set(extra_cents)
    if open_amounts:
        allowed.add(sum(open_amounts))
    return allowed


def find_unknown(text: str, allowed: set[int]) -> list[str]:
    return [token for token, cents in extract(text) if cents not in allowed]
