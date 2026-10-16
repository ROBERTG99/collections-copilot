"""Twilio REST adapter (Messages.json via httpx; WireMock locally). Retries live in the caller."""

from __future__ import annotations

import httpx

from collections_copilot.integrations.sms.base import SmsError, SmsResult


class TwilioSmsSender:
    def __init__(
        self,
        base_url: str,
        account_sid: str,
        auth_token: str,
        from_e164: str,
        status_callback: str | None = None,
        timeout_s: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = f"{base_url.rstrip('/')}/2010-04-01/Accounts/{account_sid}/Messages.json"
        self.auth = (account_sid, auth_token)
        self.from_e164 = from_e164
        self.status_callback = status_callback
        self.timeout_s = timeout_s
        self.client = client

    async def send(self, to_e164: str, body: str, *, idempotency_key: str) -> SmsResult:
        data = {"To": to_e164, "From": self.from_e164, "Body": body}
        if self.status_callback:
            data["StatusCallback"] = self.status_callback
        headers = {"I-Twilio-Idempotency-Token": idempotency_key}
        try:
            if self.client is not None:
                resp = await self.client.post(
                    self.url, data=data, auth=self.auth, headers=headers, timeout=self.timeout_s
                )
            else:
                async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                    resp = await client.post(self.url, data=data, auth=self.auth, headers=headers)
        except httpx.HTTPError as exc:
            raise SmsError(
                f"twilio connection error: {type(exc).__name__}", retryable=True
            ) from exc
        if resp.status_code >= 500 or resp.status_code == 429:
            raise SmsError(f"twilio {resp.status_code}", retryable=True, status=resp.status_code)
        if resp.status_code >= 400:
            raise SmsError(
                f"twilio {resp.status_code}: {resp.text[:200]}",
                retryable=False,
                status=resp.status_code,
            )
        payload = resp.json()
        sid = payload.get("sid")
        if not isinstance(sid, str) or not sid.startswith("SM"):
            raise SmsError(
                "twilio response without a message sid", retryable=False, status=resp.status_code
            )
        return SmsResult(provider_sid=sid, status=str(payload.get("status", "queued")))
