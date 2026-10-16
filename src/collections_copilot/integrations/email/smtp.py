"""Notifier interface: supervisor escalation email via SMTP (Mailpit locally)."""

from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol


class Notifier(Protocol):
    async def notify_supervisor(
        self, *, case_id: str, reason: str, idempotency_key: str
    ) -> None: ...


@dataclass
class SmtpNotifier:
    host: str
    port: int
    sender: str
    recipient: str
    _sent_keys: set[str] = field(default_factory=set)

    async def notify_supervisor(self, *, case_id: str, reason: str, idempotency_key: str) -> None:
        if idempotency_key in self._sent_keys:
            return
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = self.recipient
        msg["Subject"] = f"[collections-copilot] Case {case_id} escalated"
        msg["X-Idempotency-Key"] = idempotency_key
        msg.set_content(f"Case {case_id} needs a supervisor.\nReason: {reason}\n")

        def _send() -> None:
            with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
                smtp.send_message(msg)

        await asyncio.to_thread(_send)
        self._sent_keys.add(idempotency_key)


@dataclass
class InMemoryNotifier:
    sent: list[dict[str, str]] = field(default_factory=list)

    async def notify_supervisor(self, *, case_id: str, reason: str, idempotency_key: str) -> None:
        if any(s["key"] == idempotency_key for s in self.sent):
            return
        self.sent.append({"case_id": case_id, "reason": reason, "key": idempotency_key})
