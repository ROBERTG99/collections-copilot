"""Twilio REST adapter (Messages.json via httpx; WireMock locally). Retries live in the caller,
which retries only errors marked `retryable` (the request certainly did not reach Twilio)."""

from __future__ import annotations

import httpx

from collections_copilot.integrations.sms.base import SmsError, SmsResult, is_synthetic
from collections_copilot.observability.logging import redact


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
        synthetic_only: bool = False,
    ) -> None:
        self.url = f"{base_url.rstrip('/')}/2010-04-01/Accounts/{account_sid}/Messages.json"
        self.auth = (account_sid, auth_token)
        self.from_e164 = from_e164
        self.status_callback = status_callback
        self.timeout_s = timeout_s
        self.client = client
        self.synthetic_only = synthetic_only

    async def send(self, to_e164: str, body: str, *, idempotency_key: str) -> SmsResult:
        if self.synthetic_only and not is_synthetic(to_e164):
            # The last gate before Twilio: outside production only synthetic numbers are texted.
            raise SmsError("recipient outside the synthetic ranges", retryable=False)
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
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # The request never left: safe to retry.
            raise SmsError(f"twilio connect error: {type(exc).__name__}", retryable=True) from exc
        except httpx.HTTPError as exc:
            # Read/write timeouts and dropped connections: Twilio may have accepted the message
            # (Messages.json does not honour idempotency tokens), so the outcome is unknown.
            raise SmsError(
                f"twilio outcome unknown: {type(exc).__name__}", retryable=False, maybe_sent=True
            ) from exc
        if resp.status_code in (429, 503):  # throttled or unavailable: the message was not taken
            raise SmsError(f"twilio {resp.status_code}", retryable=True, status=resp.status_code)
        if resp.status_code >= 500:  # 500/502/504 may come after Twilio accepted the message
            raise SmsError(
                f"twilio {resp.status_code}: outcome unknown",
                retryable=False,
                status=resp.status_code,
                maybe_sent=True,
            )
        if resp.status_code >= 400:
            raise SmsError(
                # Twilio's error body names the To number: mask it before anyone stores it.
                f"twilio {resp.status_code}: {redact(resp.text[:200])}",
                retryable=False,
                status=resp.status_code,
            )
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        sid = payload.get("sid") if isinstance(payload, dict) else None
        if not isinstance(sid, str) or not sid.startswith("SM"):
            raise SmsError(
                "twilio response without a message sid",
                retryable=False,
                status=resp.status_code,
                maybe_sent=True,
            )
        return SmsResult(provider_sid=sid, status=str(payload.get("status", "queued")))
