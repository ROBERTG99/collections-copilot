"""SMS adapters behind the SmsSender interface."""

from __future__ import annotations

from collections_copilot.config import Settings
from collections_copilot.integrations.sms.base import SmsSender
from collections_copilot.integrations.sms.twilio import TwilioSmsSender


def build_sms_sender(settings: Settings) -> SmsSender:
    return TwilioSmsSender(
        settings.twilio_base_url,
        settings.twilio_account_sid,
        settings.twilio_auth_token,
        settings.twilio_from,
        status_callback=f"{settings.public_base_url.rstrip('/')}/api/v1/webhooks/twilio/status",
    )
