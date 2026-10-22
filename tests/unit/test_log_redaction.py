"""NFR-10: JSON logs carry identifiers, never phone numbers, tokens or message bodies."""

from __future__ import annotations

import io
import json
import logging

import pytest

from collections_copilot.observability import logging as cc_logging
from collections_copilot.observability.steps import StepContext, step_context

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhbmEifQ.c2lnbmF0dXJlLXZhbHVl"


@pytest.fixture
def capture() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(cc_logging.JsonFormatter(service="test"))
    logger = logging.getLogger("cc.test.redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, stream


def _lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_no_phone_or_token_in_logs(capture: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = capture
    with step_context(StepContext(tenant_id="t-1", case_id="c-1", run_id="r-1")):
        token = cc_logging.correlation_id.set("corr-1")
        try:
            logger.info(
                "sending to +15550001234 with Bearer %s for ana@example.com",
                JWT,
                extra={
                    "to": "+34555000111",
                    "body": "We will pay USD 300 on Friday",
                    "authorization": f"Bearer {JWT}",
                    "queue": "cc-tasks",
                    "detail": "callback for +1 (555) 000-2222 failed",
                },
            )
            try:
                raise ValueError(f"twilio rejected +15550009999 token={JWT}")
            except ValueError:
                logger.exception("send failed")
        finally:
            cc_logging.correlation_id.reset(token)

    text = stream.getvalue()
    for secret in (
        "+15550001234",
        "+34555000111",
        "555) 000-2222",
        "+15550009999",
        JWT,
        "ana@example.com",
    ):
        assert secret not in text, secret
    assert "We will pay" not in text  # message bodies are dropped, not just masked

    first, second = _lines(stream)
    assert first["level"] == "INFO"
    assert first["service"] == "test"
    assert first["correlation_id"] == "corr-1"
    assert first["tenant_id"] == "t-1"
    assert first["case_id"] == "c-1"
    assert first["queue"] == "cc-tasks"  # harmless fields survive
    assert "[phone]" in str(first["msg"])
    assert "[token]" in str(first["msg"])
    assert "body" not in first
    assert "to" not in first
    assert second["exc_type"] == "ValueError"
    assert "[phone]" in str(second["exc"])


def test_configure_logging_is_idempotent() -> None:
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    try:
        cc_logging.configure_logging("api", "INFO")
        cc_logging.configure_logging("api", "INFO")
        json_handlers = [
            h for h in root.handlers if isinstance(h.formatter, cc_logging.JsonFormatter)
        ]
        assert len(json_handlers) == 1
        assert logging.getLogger("uvicorn.access").disabled
    finally:
        logging.getLogger(
            "uvicorn.access"
        ).disabled = False  # leave logging as other tests expect it
        root.handlers, root.level = saved_handlers, saved_level


@pytest.mark.parametrize(
    "text",
    [
        "GET /api/v1/queue?q=x&phone=%2B15551234567",  # URL-encoded phone
        "callback for 555 123 4567 failed",
        "number 15551234567 bounced",
        "refresh_token=abc.def.ghi&x=1",
        "auth_token=0123456789abcdef",
        "key sk-ant-api03-AbCdEf_123-xyz",
        "mail ana%40example.com",
    ],
)
def test_encoded_and_unprefixed_secrets_are_masked(text: str) -> None:
    out = cc_logging.redact(text)
    for secret in (
        "15551234567",
        "555 123 4567",
        "abc.def.ghi",
        "0123456789abcdef",
        "sk-ant-api03",
        "ana@example.com",
        "ana%40example",
    ):
        assert secret not in out


def test_access_log_is_the_route_template_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """uvicorn's access log (raw path and query) is off; the middleware logs the template.
    The API image runs the launcher (collections_copilot.api.serve), which starts uvicorn."""
    from pathlib import Path

    from collections_copilot.api import serve
    from collections_copilot.config import Settings

    dockerfile = Path("docker/python.Dockerfile").read_text()
    assert 'CMD ["python", "-m", "collections_copilot.api.serve"]' in dockerfile
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda target, **kw: calls.append(kw))
    serve.main(Settings(api_workers=1))
    assert calls[0]["access_log"] is False
