"""JSON logs (§14, NFR-10): one object per line with correlation, tenant, case and trace ids.

Phone numbers, e-mail addresses and tokens are masked wherever they appear (message, extra
fields, exception text); fields that hold message bodies, recipients or credentials are dropped.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from collections_copilot.observability.steps import current_context

correlation_id: ContextVar[str] = ContextVar("cc_correlation_id", default="")

# A "+" followed by 8–15 digits, allowing spaces, dots, dashes and brackets between them.
_PHONE = re.compile(r"\+\d(?:[\s().-]*\d){7,14}")
_JWT = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]*")
_BEARER = re.compile(
    r"(?i)\b(bearer|basic|token[=:]|api[_-]?key[=:]|secret[=:]|password[=:])\s*\S+"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
DROPPED = frozenset(
    {
        "body",
        "text",
        "message_body",
        "to",
        "from",
        "phone",
        "phone_e164",
        "recipient",
        "authorization",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "password",
        "secret",
        "api_key",
        "cookie",
    }
)
_STANDARD = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
}


def redact(text: str) -> str:
    text = _JWT.sub("[token]", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} [token]", text)
    text = _EMAIL.sub("[email]", text)
    return _PHONE.sub("[phone]", text)


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if str(k).lower() not in DROPPED}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    return redact(str(value))


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        ctx = current_context()
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service,
            "msg": redact(record.getMessage()),
            "correlation_id": correlation_id.get() or None,
            "tenant_id": ctx.tenant_id or None,
            "case_id": ctx.case_id,
        }
        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            out["trace_id"] = f"{span.trace_id:032x}"
            out["span_id"] = f"{span.span_id:016x}"
        for key, value in vars(record).items():
            if key in _STANDARD or key.startswith("_") or key.lower() in DROPPED:
                continue
            out[key] = _clean(value)
        if record.exc_info and record.exc_info[0] is not None:
            out["exc_type"] = record.exc_info[0].__name__
            out["exc"] = redact("".join(traceback.format_exception(*record.exc_info)))
        return json.dumps({k: v for k, v in out.items() if v is not None}, default=str)


def configure_logging(service: str, level: str = "INFO") -> None:
    """Replace the root handlers with one JSON handler on stdout (safe to call twice)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
