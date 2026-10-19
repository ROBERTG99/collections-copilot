"""draft node (FR-4): SMS in the account language with ledger amounts only.

The model sees only open (not disputed) invoices with amounts pre-formatted from the ledger, the
open total and the allowlisted payment link; the account name is redacted before the call and
restored after. The opt-out footer is appended by code. Any model failure falls back to the
versioned template (author "fallback", shown as "Template" in the console).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from collections_copilot.agent.prompts import render, template
from collections_copilot.agent.schemas import Draft
from collections_copilot.compliance.engine import InvoiceFacts
from collections_copilot.compliance.rules.footer import footer_for
from collections_copilot.llm.gateway import LLMGateway, RouteSpec
from collections_copilot.llm.redaction import AccountPII, Redactor


def format_money(cents: int, currency: str, language: str) -> str:
    whole, frac = divmod(abs(cents), 100)
    if language == "es":
        grouped = f"{whole:,}".replace(",", ".")
        return f"{'-' if cents < 0 else ''}{grouped},{frac:02d} {currency}"
    return f"{currency} {'-' if cents < 0 else ''}{whole:,}.{frac:02d}"


@dataclass(frozen=True)
class DraftInput:
    action: Literal["remind", "payment_plan"]
    language: str
    account_name: str
    currency: str
    invoices: list[InvoiceFacts]
    pay_link: str
    pii: AccountPII | None = None
    feedback: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DraftResult:
    body: str
    author: Literal["ai", "fallback"]
    model: str | None = None
    prompt_version: str | None = None
    cost_usd: float = 0.0


def _open(inp: DraftInput) -> list[InvoiceFacts]:
    return sorted((i for i in inp.invoices if i.status == "open"), key=lambda i: -i.amount_cents)


def _context(inp: DraftInput) -> dict[str, object]:
    open_invoices = _open(inp)
    total = sum(i.amount_cents for i in open_invoices)
    ctx: dict[str, object] = {
        "action": inp.action,
        "language": inp.language,
        "account_name": inp.account_name,
        "invoices": [
            {"number": i.number, "amount": format_money(i.amount_cents, i.currency, inp.language)}
            for i in open_invoices[:3]
        ],
        "total_open": format_money(total, inp.currency, inp.language),
        "pay_link": inp.pay_link,
    }
    if inp.feedback:
        ctx["previous_violations"] = inp.feedback
    return ctx


def with_footer(body: str, language: str) -> str:
    body = re.sub(r"\s+", " ", body).strip()
    footer = footer_for(language)
    return body if body.endswith(footer) else f"{body} {footer}"


def template_body(inp: DraftInput) -> str:
    ctx = _context(inp)
    invoices = ctx["invoices"]
    first = invoices[0] if isinstance(invoices, list) and invoices else {"number": "", "amount": ""}
    text = template(inp.action, inp.language).format(
        account_name=inp.account_name,
        invoice_number=first["number"],
        invoice_amount=first["amount"],
        total_open=ctx["total_open"],
        pay_link=inp.pay_link,
    )
    return with_footer(text, inp.language)


async def make_draft(
    gateway: LLMGateway, routes: dict[str, RouteSpec], inp: DraftInput
) -> DraftResult:
    rendered = render("draft_message", _context(inp), [])
    version = f"{rendered.name}.{rendered.version}"
    result = await gateway.structured(
        routes["draft_message"],
        system=rendered.system,
        user=rendered.user,
        schema=Draft,
        prompt_version=version,
        prompt_hash=rendered.hash,
        pii=inp.pii,
        language=inp.language,
    )
    if result.ok and result.parsed is not None:
        body = Redactor.restore(result.parsed.body, result.redactions)
        return DraftResult(
            with_footer(body, inp.language), "ai", result.model, version, result.cost_usd
        )
    return DraftResult(template_body(inp), "fallback", result.model, version, result.cost_usd)
