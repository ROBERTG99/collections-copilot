"""Demo-only debtor simulator.

Polls the WireMock request journal for SMS the platform sent to the Twilio double, then posts what
Twilio and a debtor would: signed status callbacks (`sent`, `delivered`) and, per persona, a
scripted reply to the inbound webhook. Signatures use the official `twilio` RequestValidator, so
the simulator also cross-checks the API's own verification. Synthetic numbers only.

Personas (by the last digit of the debtor's number, or DEBTOR_SIM_PERSONA):
  0-3 promise ("I can pay <open balance> on Friday"), 4-5 dispute, 6 paid, 7 stop, 8-9 silent.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
from twilio.request_validator import RequestValidator

log = logging.getLogger("debtor_sim")
ALIVE = Path("/tmp/debtor-sim.alive")  # noqa: S108 - container healthcheck marker
PERSONAS = (
    "promise",
    "promise",
    "promise",
    "promise",
    "dispute",
    "dispute",
    "paid",
    "stop",
    "silent",
    "silent",
)
REPLIES = {
    "en": {
        "promise": "I can pay {amount} on Friday",
        "dispute": "We never received these goods, the invoice is incorrect.",
        "paid": "We already paid last week",
        "stop": "STOP",
    },
    "es": {
        "promise": "Puedo pagar {amount} el viernes",
        "dispute": "Nunca recibimos esta mercancía, la factura es incorrecta.",
        "paid": "Ya pagamos la semana pasada",
        "stop": "BAJA",
    },
}
AMOUNT = re.compile(r"\b[A-Z]{3}\s*(\d[\d.,]*\d)")
BALANCE = re.compile(r"(?:Open balance|Saldo pendiente)[^:]*:\s*[A-Z]{3}\s*([\d.,]+)")


def persona_for(phone: str, override: str | None = None) -> str:
    if override and override != "auto":
        return override
    digits = [c for c in phone if c.isdigit()]
    return PERSONAS[int(digits[-1]) if digits else 8]


def language_of(body: str) -> str:
    return "es" if "Responde STOP" in body else "en"


def reply_text(persona: str, outbound_body: str) -> str | None:
    """The debtor's answer, or None for silent personas and non-reminder messages."""
    if persona == "silent":
        return None
    match = BALANCE.search(outbound_body)
    amounts = [m.group(1).rstrip(".,") for m in AMOUNT.finditer(outbound_body)]
    if match is None and not amounts:  # STOP confirmations and other notices get no reply
        return None
    amount = match.group(1).rstrip(".,") if match else max(amounts, key=_cents)
    return REPLIES[language_of(outbound_body)][persona].format(amount=amount)


def _cents(amount: str) -> int:
    digits = amount.replace(".", "").replace(",", "")
    return int(digits) if digits.isdigit() else 0


@dataclass
class Simulator:
    wiremock_url: str
    api_url: str
    public_base_url: str
    auth_token: str
    persona: str | None = None
    reply_delay_s: float = 1.0
    seen: set[str] = field(default_factory=set)
    client: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=10)
        return self.client

    async def signed_post(self, path: str, params: dict[str, str]) -> int:
        signature = RequestValidator(self.auth_token).compute_signature(
            f"{self.public_base_url.rstrip('/')}{path}", params
        )
        resp = await self._client().post(
            f"{self.api_url.rstrip('/')}{path}",
            data=params,
            headers={"X-Twilio-Signature": signature},
        )
        return resp.status_code

    async def new_messages(self) -> list[dict[str, str]]:
        resp = await self._client().get(f"{self.wiremock_url.rstrip('/')}/__admin/requests")
        resp.raise_for_status()
        out = []
        for entry in reversed(resp.json().get("requests", [])):
            request, response = entry.get("request", {}), entry.get("response", {})
            if entry.get("id") in self.seen or request.get("method") != "POST":
                continue
            if not str(request.get("url", "")).endswith("/Messages.json"):
                continue
            self.seen.add(entry["id"])
            if response.get("status") != 201:
                continue
            form = dict(parse_qsl(request.get("body", "")))
            sid = _sid(response.get("body", ""))
            if sid and form.get("To"):
                out.append(
                    {
                        "sid": sid,
                        "to": form["To"],
                        "from": form.get("From", ""),
                        "body": form.get("Body", ""),
                    }
                )
        return out

    async def handle(self, message: dict[str, str]) -> str | None:
        base = {
            "MessageSid": message["sid"],
            "AccountSid": "AC" + "0" * 32,
            "To": message["to"],
            "From": message["from"],
        }
        for status in ("sent", "delivered"):
            await self.signed_post(
                "/api/v1/webhooks/twilio/status", {**base, "MessageStatus": status}
            )
        text = reply_text(persona_for(message["to"], self.persona), message["body"])
        if text is None:
            return None
        await asyncio.sleep(self.reply_delay_s)
        await self.signed_post(
            "/api/v1/webhooks/twilio/inbound",
            {
                "MessageSid": "SM" + uuid.uuid4().hex,
                "AccountSid": "AC" + "0" * 32,
                "From": message["to"],
                "To": message["from"],
                "Body": text,
            },
        )
        return text

    async def tick(self) -> int:
        messages = await self.new_messages()
        await asyncio.gather(*(self.handle(m) for m in messages))
        return len(messages)


def _sid(body: str) -> str | None:
    match = re.search(r'"sid"\s*:\s*"(SM[0-9a-f]+)"', body or "")
    return match.group(1) if match else None


async def run(sim: Simulator, interval_s: float = 1.0) -> None:
    while True:  # skip the journal backlog: only messages sent after start get callbacks
        try:
            await sim.new_messages()
            break
        except httpx.HTTPError:
            await asyncio.sleep(interval_s)
    while True:
        try:
            await sim.tick()
            await asyncio.to_thread(ALIVE.touch)
        except httpx.HTTPError as exc:
            log.warning("debtor-sim tick failed: %s", type(exc).__name__)
        await asyncio.sleep(interval_s)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    sim = Simulator(
        wiremock_url=os.environ.get("DEBTOR_SIM_WIREMOCK_URL", "http://wiremock-twilio:8080"),
        api_url=os.environ.get("DEBTOR_SIM_API_URL", "http://api:8310"),
        public_base_url=os.environ.get("CC_PUBLIC_BASE_URL", "http://api:8310"),
        auth_token=os.environ.get("CC_TWILIO_AUTH_TOKEN", "dev-twilio-auth-token"),
        persona=os.environ.get("DEBTOR_SIM_PERSONA"),
        reply_delay_s=float(os.environ.get("DEBTOR_SIM_REPLY_DELAY_S", "1.0")),
    )
    asyncio.run(run(sim))


if __name__ == "__main__":
    main()
