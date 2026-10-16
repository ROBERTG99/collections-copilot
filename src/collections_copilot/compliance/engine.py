"""Compliance engine (FR-5): pre-check before drafting, post-check on every draft and edit.

Blocking violations stop approval and sending. The contact window never blocks: it defers the
send to the next 08:00 debtor-local time and the caller schedules a follow-up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from collections_copilot.compliance.rules import amounts, footer, links, phrases, window
from collections_copilot.compliance.types import Violation

MAX_SMS_CHARS = 320
CONTACT_CAP = 3
CAP_WINDOW = timedelta(days=7)
TEXT_ACTIONS = ("remind", "payment_plan")


@dataclass(frozen=True)
class AccountFacts:
    timezone: str
    language: str
    currency: str
    opted_out: bool


@dataclass(frozen=True)
class InvoiceFacts:
    number: str
    amount_cents: int
    currency: str
    status: str


@dataclass(frozen=True)
class ComplianceContext:
    now: datetime
    account: AccountFacts
    invoices: list[InvoiceFacts]
    contacts_7d: list[datetime] = field(default_factory=list)
    action: str | None = None
    body: str | None = None
    extra_allowed_cents: tuple[int, ...] = ()
    account_name: str = ""
    contact_flag: str | None = None  # e.g. "wrong_number" from a reply; blocks every send


@dataclass(frozen=True)
class CheckResult:
    violations: list[Violation]
    send_at: datetime
    deferred: bool

    @property
    def ok(self) -> bool:
        return not any(v.blocking for v in self.violations)

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "violations": [v.as_dict() for v in self.violations],
            "send_at": self.send_at.isoformat(),
            "deferred": self.deferred,
        }


def _account_rules(ctx: ComplianceContext) -> list[Violation]:
    out: list[Violation] = []
    if ctx.account.opted_out:
        out.append(Violation("opted_out", "the debtor opted out of SMS"))
    if ctx.contact_flag:
        out.append(Violation("contact_flagged", f"the contact is flagged: {ctx.contact_flag}"))
    recent = [t for t in ctx.contacts_7d if ctx.now - CAP_WINDOW < t <= ctx.now]
    if len(recent) >= CONTACT_CAP:
        out.append(
            Violation(
                "contact_cap_reached",
                f"{len(recent)} contacts in the last 7 days (max {CONTACT_CAP})",
            )
        )
    collectible = [i for i in ctx.invoices if i.status == "open"]
    if not collectible and any(i.status == "disputed" for i in ctx.invoices):
        out.append(Violation("all_invoices_disputed", "every unpaid invoice is disputed"))
    return out


def _window(ctx: ComplianceContext) -> tuple[datetime, bool, list[Violation]]:
    send_at = window.next_send_time(ctx.now, ctx.account.timezone)
    deferred = send_at != ctx.now
    notes = []
    if deferred:
        notes.append(
            Violation(
                "outside_contact_window",
                f"deferred to {send_at.isoformat()} (08:00–21:00 debtor local time)",
                blocking=False,
            )
        )
    return send_at, deferred, notes


def precheck(ctx: ComplianceContext) -> CheckResult:
    send_at, deferred, notes = _window(ctx)
    return CheckResult(_account_rules(ctx) + notes, send_at, deferred)


def text_violations(ctx: ComplianceContext) -> list[Violation]:
    body = (ctx.body or "").strip()
    if not body:
        return [Violation("empty_body", "the message is empty")]
    out: list[Violation] = []
    if len(body) > MAX_SMS_CHARS:
        out.append(Violation("too_long", f"{len(body)} characters > {MAX_SMS_CHARS}"))
    if not footer.has_footer(body, ctx.account.language):
        out.append(
            Violation("missing_footer", f"must end with: {footer.footer_for(ctx.account.language)}")
        )
    allowed = amounts.allowed_amounts(ctx.invoices, ctx.extra_allowed_cents)
    unknown = amounts.find_unknown(body, allowed, ctx.invoices, (ctx.account_name,))
    if unknown:
        out.append(
            Violation("amount_not_in_ledger", f"amounts not in the ledger: {', '.join(unknown)}")
        )
    bad_phrases = phrases.find(body)
    if bad_phrases:
        out.append(Violation("forbidden_phrase", f"forbidden phrases: {', '.join(bad_phrases)}"))
    bad_hosts = links.find_disallowed(body)
    if bad_hosts:
        out.append(
            Violation("link_not_allowed", f"links not on the allowlist: {', '.join(bad_hosts)}")
        )
    if ctx.action in TEXT_ACTIONS or ctx.action is None:
        mentioned = {cents for _, cents, _ in amounts.extract(body, protected=(ctx.account_name,))}
        for inv in ctx.invoices:
            if inv.status != "disputed":
                continue
            if inv.number in body or inv.amount_cents in mentioned:
                out.append(Violation("disputed_invoice", f"invoice {inv.number} is disputed"))
    return out


def postcheck(ctx: ComplianceContext) -> CheckResult:
    send_at, deferred, notes = _window(ctx)
    return CheckResult(_account_rules(ctx) + text_violations(ctx) + notes, send_at, deferred)
