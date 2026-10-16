"""Money in a message must exist in the ledger (fail closed).

Every number in the text is treated as a potential amount unless it is provably something else:
part of a known invoice number, a date or time, inside a URL, part of an identifier (letters or a
hyphen glued in front, e.g. INV-2026-77, W-4821), or a small count (≤ 31) without any money marker.
Everything left must equal an open invoice amount or the open balance, in the invoice currency
when a currency marker is present.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Protocol

# Currency markers mapped to ISO codes; unknown markers (pesos, £, …) never match a USD/EUR invoice.
MARKERS: dict[str, str] = {
    "us$": "USD",
    "$": "USD",
    "usd": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "dólar": "USD",
    "dólares": "USD",
    "dolares": "USD",
    "€": "EUR",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "pesos": "MXN",
    "peso": "MXN",
    "mxn": "MXN",
    "cad": "CAD",
    "aud": "AUD",
    "chf": "CHF",
    "¥": "JPY",
    "jpy": "JPY",
}
_MARKER = "|".join(sorted((re.escape(m) for m in MARKERS), key=len, reverse=True))
NUMBER = r"\d{1,3}(?:[.,  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
TOKEN = re.compile(
    rf"(?P<pre>(?:{_MARKER})\s?)?(?P<num>{NUMBER})(?P<k>[kK]\b)?(?P<post>\s?(?:{_MARKER})(?![a-z]))?",
    re.IGNORECASE,
)
URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
DATE_TIME = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b\d{1,2}:\d{2}\b", re.I
)
SMALL_COUNT_MAX = 31


class _Invoice(Protocol):
    @property
    def number(self) -> str: ...

    @property
    def amount_cents(self) -> int: ...

    @property
    def currency(self) -> str: ...

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


def _blank(text: str, pattern: re.Pattern[str] | str) -> str:
    regex = re.compile(re.escape(pattern)) if isinstance(pattern, str) else pattern
    return regex.sub(lambda m: " " * len(m.group(0)), text)


def extract(
    text: str, invoice_numbers: Iterable[str] = (), protected: Iterable[str] = ()
) -> list[tuple[str, int, str | None]]:
    """Potential money mentions as (token, cents, currency or None). `protected` terms (the account
    name) are removed first, so a company called "Studio 1234" is not read as an amount."""
    scrubbed = _blank(text, URL)
    for term in protected:
        if term and term.strip():
            scrubbed = _blank(scrubbed, re.compile(re.escape(term.strip()), re.I))
    numeric_refs: set[str] = set()
    for number in sorted(invoice_numbers, key=len, reverse=True):
        if not number:
            continue
        if number.isdigit():
            numeric_refs.add(number)  # decided per token below, never blanked as a substring
        else:
            whole = re.compile(rf"(?<![\w]){re.escape(number)}(?![\w])")
            scrubbed = _blank(scrubbed, whole)
    scrubbed = _blank(scrubbed, DATE_TIME)
    found: list[tuple[str, int, str | None]] = []
    for m in TOKEN.finditer(scrubbed):
        start = m.start("num")
        prev = scrubbed[start - 1] if start > 0 else " "
        before_prev = scrubbed[start - 2] if start > 1 else " "
        negative = prev == "-" and not before_prev.isalnum()
        glued_code = bool(m.group("pre")) and not m.group("pre").endswith(" ")
        if (prev.isalpha() or prev in "-_#/") and not glued_code and not negative:
            continue  # identifier such as INV-2026-77, W-4821, A00001
        has_marker = bool(m.group("pre") or m.group("post") or m.group("k"))
        if m.group("num") in numeric_refs and not has_marker:
            continue  # a numeric invoice number used as a reference ("Invoice 100234")
        end = m.end("num")
        nxt = scrubbed[end] if end < len(scrubbed) else " "
        if nxt.isalpha() and not (m.group("k") or m.group("post")):
            continue  # ordinal or unit glued to the number (15th, 3x)
        num = m.group("num")
        cents = to_cents(num)
        if cents is None:
            continue
        if m.group("k"):
            cents *= 1000
        marker = (m.group("pre") or m.group("post") or "").strip().lower()
        currency = MARKERS.get(marker)
        has_decimals = bool(re.search(r"[.,]\d{1,2}$", num))
        if not (marker or m.group("k") or has_decimals) and cents <= SMALL_COUNT_MAX * 100:
            continue  # small count without any money signal ("3 days")
        found.append((num + (m.group("k") or ""), cents, currency))
    return found


def allowed_amounts(
    invoices: Iterable[_Invoice], extra_cents: Iterable[int] = ()
) -> set[tuple[str | None, int]]:
    """(currency, cents) pairs the ledger supports; currency None means any marker-less mention."""
    open_invoices = [i for i in invoices if i.status == "open"]
    allowed: set[tuple[str | None, int]] = set()
    by_currency: dict[str, int] = {}
    for inv in open_invoices:
        allowed.add((inv.currency, inv.amount_cents))
        by_currency[inv.currency] = by_currency.get(inv.currency, 0) + inv.amount_cents
    for currency, total in by_currency.items():
        allowed.add((currency, total))
    for cents in extra_cents:
        for currency in by_currency or {"USD": 0}:
            allowed.add((currency, cents))
    return allowed


def find_unknown(
    text: str,
    allowed: set[tuple[str | None, int]],
    invoices: Iterable[_Invoice] = (),
    protected: Iterable[str] = (),
) -> list[str]:
    cents_any = {c for _, c in allowed}
    unknown = []
    for token, cents, currency in extract(text, [i.number for i in invoices], protected):
        ok = (currency, cents) in allowed if currency else cents in cents_any
        if not ok:
            unknown.append(token)
    return unknown
