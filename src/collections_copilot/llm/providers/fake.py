"""FakeLLM: the deterministic offline provider (default; tests, evals and DEMO_MODE=offline).

It reads the same rendered prompt a real model receives: a `<context>` JSON block plus
`<untrusted_note>` blocks. If a recorded cassette exists for the exact request it replays it;
otherwise it answers with transparent keyword heuristics. Offline eval numbers therefore measure
the pipeline and guardrails with this fake, not model quality (see docs/evaluation.md).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from collections_copilot.llm.providers.base import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    Usage,
)

NOTE_RE = re.compile(
    r'<untrusted_note id="(?P<id>[^"]*)"[^>]*>(?P<body>.*?)</untrusted_note>', re.S
)
CONTEXT_RE = re.compile(r"<context>\s*(?P<json>\{.*?\})\s*</context>", re.S)

NBA_KEYWORDS: list[tuple[str, list[str]]] = [
    (
        "pause",
        [
            r"disput",
            r"damaged",
            r"billed twice",
            r"credit note",
            r"refuses? to pay until",
            r"disputa",
            r"dañad",
            r"cobr\w+ dos veces",
            r"nota de cr[eé]dito",
        ],
    ),
    (
        "no_action",
        [
            r"payment (?:was )?sent",
            r"wire",
            r"check went out",
            r"remittance",
            r"already paid",
            r"transfiri",
            r"pago sali",
            r"remesa",
            r"ya pag",
        ],
    ),
    (
        "escalate",
        [
            r"escalat",
            r"will not pay",
            r"stopped answering",
            r"refer to supervisor",
            r"bounces",
            r"escalar",
            r"no pagar[aá]",
            r"dej[oó] de responder",
            r"supervisor",
            r"rebota",
        ],
    ),
    (
        "payment_plan",
        [
            r"installment",
            r"payment plan",
            r"split the balance",
            r"cash flow",
            r"cuotas",
            r"plan de pagos",
            r"caja est[aá] ajustada",
        ],
    ),
]
REPLY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("stop", [r"^\s*(stop|unsubscribe|cancel|end|quit|baja|parar|alto)\s*[.!]*\s*$"]),
    (
        "wrong_number",
        [
            r"wrong number",
            r"no (?:soy|es) (?:yo|la persona)",
            r"n[uú]mero equivocado",
            r"don'?t know (?:this|the) company",
            r"not my company",
        ],
    ),
    (
        "dispute",
        [
            r"disput",
            r"never (?:received|ordered)",
            r"damaged",
            r"incorrect",
            r"wrong amount",
            r"not (?:what|the) (?:we|i) ordered",
            r"no (?:recib|ped)",
            r"dañad",
            r"incorrect",
            r"billed twice",
            r"duplicad",
        ],
    ),
    (
        "paid",
        [
            r"already paid",
            r"(?:we|i) paid",
            r"payment (?:was )?sent",
            r"ya (?:pagu|pagamos|pag)",
            r"transfer(?:i|í)mos",
            r"check is in the mail",
            r"pagado",
        ],
    ),
    (
        "promise_to_pay",
        [
            r"\bcan pay\b",
            r"\bwill pay\b",
            r"\bpay(?:ing)? (?:on|by|next|this)\b",
            r"\bpromise\b",
            r"\bpagar[eé]\b",
            r"\bpagamos el\b",
            r"\bpuedo pagar\b",
            r"\bpagaremos\b",
        ],
    ),
]
WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "lunes": 0,
    "martes": 1,
    "miércoles": 2,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sábado": 5,
    "sabado": 5,
    "domingo": 6,
}


@dataclass
class FakeMode:
    """Test hooks: force a route to refuse, truncate, return invalid JSON or fail like an outage."""

    refusal: frozenset[str] = frozenset()
    max_tokens: frozenset[str] = frozenset()
    invalid: frozenset[str] = frozenset()
    outage: bool = False


class FakeLLM:
    name = "fake"

    def __init__(self, cassette_dir: Path | None = None, mode: FakeMode | None = None) -> None:
        self.cassette_dir = cassette_dir
        self.mode = mode or FakeMode()
        self.requests: list[ProviderRequest] = []

    @staticmethod
    def request_key(request: ProviderRequest) -> str:
        blob = json.dumps(
            [request.route, request.model, request.system, request.user], ensure_ascii=False
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def _cassette(self, request: ProviderRequest) -> ProviderResponse | None:
        if self.cassette_dir is None:
            return None
        path = self.cassette_dir / request.route / f"{self.request_key(request)}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return ProviderResponse(
            text=data["text"],
            stop_reason=data["stop_reason"],
            model=data["model"],
            usage=Usage(**data.get("usage", {})),
            refusal_category=data.get("refusal_category"),
        )

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if self.mode.outage:
            raise ProviderError("simulated provider outage", status=503, retryable=True)
        recorded = self._cassette(request)
        if recorded is not None:
            return recorded
        usage = Usage(input_tokens=(len(request.system) + len(request.user)) // 4, output_tokens=60)
        if request.route in self.mode.refusal:
            return ProviderResponse("", "refusal", "fake", usage, refusal_category="general_harms")
        if request.route in self.mode.max_tokens:
            return ProviderResponse('{"truncated": ', "max_tokens", "fake", usage)
        if request.route in self.mode.invalid:
            return ProviderResponse('{"unexpected": true}', "end_turn", "fake", usage)
        ctx = _context(request.user)
        notes = [(m.group("id"), m.group("body").strip()) for m in NOTE_RE.finditer(request.user)]
        handler = {
            "next_best_action": _nba,
            "draft_message": _draft,
            "classify_reply": _classify,
            "prioritize_rationale": _rationale,
            "judge_tone": _judge,
        }.get(request.route)
        if handler is None:
            return ProviderResponse("{}", "end_turn", "fake", usage)
        return ProviderResponse(
            json.dumps(handler(ctx, notes), ensure_ascii=False), "end_turn", "fake", usage
        )


def _context(user: str) -> dict[str, Any]:
    m = CONTEXT_RE.search(user)
    if not m:
        return {}
    try:
        value = json.loads(m.group("json"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _match(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.I | re.M) for p in patterns)


def _nba(ctx: dict[str, Any], notes: list[tuple[str, str]]) -> dict[str, Any]:
    for action, patterns in NBA_KEYWORDS:
        cited = [nid for nid, body in notes if _match(body, patterns)]
        if cited:
            return {
                "action": action,
                "rationale": f"Notes indicate {action.replace('_', ' ')}.",
                "confidence": 0.92,
                "cited_note_ids": cited[:3],
            }
    broken = int(ctx.get("broken_promises_90d", 0))
    action = "escalate" if broken >= 2 and int(ctx.get("max_days_past_due", 0)) >= 120 else "remind"
    return {
        "action": action,
        "rationale": "No blocking signal in the notes; a reminder is appropriate."
        if action == "remind"
        else "Repeated broken promises on a long-overdue balance.",
        "confidence": 0.8,
        "cited_note_ids": [],
    }


def _draft(ctx: dict[str, Any], notes: list[tuple[str, str]]) -> dict[str, Any]:
    es = ctx.get("language") == "es"
    invoices = ctx.get("invoices") or []
    total = ctx.get("total_open") or ""
    link = ctx.get("pay_link") or ""
    name = ctx.get("account_name", "")
    first = invoices[0] if invoices else {}
    if ctx.get("action") == "payment_plan":
        body = (
            f"Hola {name}, podemos acordar un plan de pagos para su saldo de {total}. "
            f"Responda a este mensaje para coordinarlo o use {link}."
            if es
            else f"Hi {name}, we can set up a payment plan for your balance of {total}. "
            f"Reply to this message to arrange it or use {link}."
        )
    else:
        inv = f"{first.get('number', '')} ({first.get('amount', '')})" if first else ""
        body = (
            f"Hola {name}, le recordamos que la factura {inv} está vencida. "
            f"Saldo pendiente: {total}. Puede pagar en {link}."
            if es
            else f"Hi {name}, a reminder that invoice {inv} is past due. "
            f"Open balance: {total}. You can pay at {link}."
        )
    return {"body": re.sub(r"\s+", " ", body).strip()}


def _amount_cents(text: str) -> int | None:
    m = re.search(
        r"(?:\$|€|usd|eur)?\s*(\d{1,3}(?:[.,]\d{3})+|\d+)(?:([.,])(\d{2}))?(?!\d)", text, re.I
    )
    if not m:
        return None
    whole = re.sub(r"[.,]", "", m.group(1))
    cents = int(m.group(3)) if m.group(3) else 0
    return int(whole) * 100 + cents


def _promise_date(text: str, today: date) -> str | None:
    low = text.lower()
    if "tomorrow" in low or "mañana" in low:
        return (today + timedelta(days=1)).isoformat()
    if "next week" in low or "próxima semana" in low or "proxima semana" in low:
        return (today + timedelta(days=7)).isoformat()
    for word, wd in WEEKDAYS.items():
        if re.search(rf"\b{word}\b", low):
            delta = (wd - today.weekday()) % 7 or 7
            return (today + timedelta(days=delta)).isoformat()
    m = re.search(r"(20\d\d-\d\d-\d\d)", text)
    return m.group(1) if m else None


def _classify(ctx: dict[str, Any], notes: list[tuple[str, str]]) -> dict[str, Any]:
    text = " ".join(body for _, body in notes)
    today = date.fromisoformat(ctx.get("today", date.today().isoformat()))
    for label, patterns in REPLY_KEYWORDS:
        if _match(text, patterns):
            promise = None
            if label == "promise_to_pay":
                promise = {
                    "amount_cents": _amount_cents(text),
                    "promised_date": _promise_date(text, today),
                }
            return {"label": label, "confidence": 0.9, "promise": promise}
    return {"label": "other", "confidence": 0.55, "promise": None}


def _rationale(ctx: dict[str, Any], notes: list[tuple[str, str]]) -> dict[str, Any]:
    comps = ctx.get("components") or {}
    top = sorted(comps.items(), key=lambda kv: -float(kv[1]))[:2]
    drivers = " and ".join(k.replace("_", " ") for k, _ in top) or "balance"
    return {"rationale": f"Score {ctx.get('score', '?')} is driven mostly by {drivers}."}


def _judge(ctx: dict[str, Any], notes: list[tuple[str, str]]) -> dict[str, Any]:
    text = " ".join(body for _, body in notes)
    harsh = re.search(
        r"\b(sue|arrest|jail|police|final warning|demanda|policía|cárcel)\b", text, re.I
    )
    return {
        "label": 0 if harsh else 1,
        "reason": "threatening language" if harsh else "respectful and factual",
    }
