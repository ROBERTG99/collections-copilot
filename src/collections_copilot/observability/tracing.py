"""OpenTelemetry tracing (§14): API → SQS → graph nodes → LLM, MCP and Twilio calls.

Spans carry identifiers and step-record attributes (route, model, tokens, cost, status, prompt
version and hash), never message text or phone numbers. LLM spans follow the OpenTelemetry GenAI
conventions (`gen_ai.*`). Without `CC_OTEL_EXPORTER_OTLP_ENDPOINT` spans are created but not
exported.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping, MutableMapping
from typing import Any

from opentelemetry import context, propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

__all__ = ["Span", "SpanKind", "Status", "StatusCode"]

TRACER_NAME = "collections_copilot"
_test_exporter: InMemorySpanExporter | None = None


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def _provider(service: str) -> TracerProvider:
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        return current
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    trace.set_tracer_provider(provider)
    return provider


_exporting: set[str] = set()


def setup_tracing(service: str, endpoint: str | None) -> None:
    """Install the SDK provider once per process; export over OTLP/HTTP when an endpoint is set
    (one exporter per endpoint, however many apps start in the process)."""
    provider = _provider(service)
    if endpoint and endpoint not in _exporting:
        _exporting.add(endpoint)
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))


def install_test_exporter() -> InMemorySpanExporter:
    """Tests: record finished spans in memory (installed once per process)."""
    global _test_exporter
    if _test_exporter is None:
        _test_exporter = InMemorySpanExporter()
        _provider("collections-copilot-tests").add_span_processor(
            SimpleSpanProcessor(_test_exporter)
        )
    return _test_exporter


def inject(carrier: MutableMapping[str, str]) -> MutableMapping[str, str]:
    """Write the current trace context (W3C `traceparent`) into message attributes or headers."""
    propagate.inject(carrier)
    return carrier


@contextlib.contextmanager
def span(
    name: str,
    attributes: Mapping[str, Any] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
    *,
    paused: tuple[type[BaseException], ...] = (),
) -> Iterator[Span]:
    """The only way this codebase opens a span.

    OpenTelemetry would record the exception message and stack trace on the span (and set them
    as its status); messages can carry debtor text or phone numbers, so a failure records its
    type only. Exceptions listed in `paused` (LangGraph's interrupt) are not failures.
    """
    with tracer().start_as_current_span(
        name,
        kind=kind,
        attributes=dict(attributes or {}),
        record_exception=False,
        set_status_on_exception=False,
    ) as current:
        try:
            yield current
        except paused:
            raise
        except BaseException as exc:
            current.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


@contextlib.contextmanager
def consumer_span(
    name: str, carrier: Mapping[str, str], attributes: Mapping[str, Any] | None = None
) -> Iterator[Span]:
    """Continue the producer's trace (SQS message attributes) in a consumer span."""
    token = context.attach(propagate.extract(dict(carrier)))
    try:
        with span(name, attributes, kind=SpanKind.CONSUMER) as current:
            yield current
    finally:
        context.detach(token)


def gen_ai_attributes(
    *,
    provider: str,
    route: str,
    request_model: str,
    response_model: str | None,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    status: str,
    prompt_version: str,
    prompt_hash: str,
    attempts: int,
) -> dict[str, Any]:
    return {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": provider,
        "gen_ai.system": provider,
        "gen_ai.request.model": request_model,
        "gen_ai.response.model": response_model or request_model,
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
        "cc.route": route,
        "cc.cost_usd": cost_usd,
        "cc.status": status,
        "cc.prompt_version": prompt_version,
        "cc.prompt_hash": prompt_hash,
        "cc.attempts": attempts,
    }
