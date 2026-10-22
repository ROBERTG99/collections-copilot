"""The LLM gateway retries only retryable errors, handles refusal/max_tokens, validates schemas,
accounts cost, records step records and trips a circuit breaker."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal

import pytest
from pydantic import BaseModel

from collections_copilot.llm.gateway import LLMGateway, RouteSpec
from collections_copilot.llm.providers.base import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    Usage,
)
from collections_copilot.observability.steps import InMemoryStepSink, StepContext, step_context


class Out(BaseModel):
    label: Literal["a", "b"]
    confidence: float


ROUTE = RouteSpec(name="classify_reply", model="claude-haiku-5-5", effort="low", max_tokens=256)


class Scripted:
    """Provider that replays a script of responses or exceptions."""

    name = "scripted"

    def __init__(self, script: list[ProviderResponse | Exception]) -> None:
        self.script = list(script)
        self.requests: list[ProviderRequest] = []

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(text: str, stop: str = "end_turn", model: str = "claude-haiku-5-5") -> ProviderResponse:
    return ProviderResponse(
        text=text, stop_reason=stop, model=model, usage=Usage(input_tokens=1000, output_tokens=200)
    )


GOOD = json.dumps({"label": "a", "confidence": 0.9})


def gateway(provider: Scripted, sink: InMemoryStepSink | None = None, **kw: object) -> LLMGateway:
    return LLMGateway(provider, sink=sink or InMemoryStepSink(), sleep=_no_sleep, **kw)  # type: ignore[arg-type]


async def _no_sleep(_: float) -> None:
    return None


async def call(gw: LLMGateway) -> object:
    return await gw.structured(
        ROUTE,
        system="sys",
        user="user",
        schema=Out,
        prompt_version="classify_reply.v1",
        prompt_hash="h",
    )


@pytest.mark.parametrize("status", [408, 409, 429, 500, 503, None])
async def test_retries_only_retryable(status: int | None) -> None:
    provider = Scripted([ProviderError("boom", status=status, retryable=True), ok(GOOD)])
    result = await call(gateway(provider))
    assert result.status == "ok"  # type: ignore[attr-defined]
    assert len(provider.requests) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_non_retryable_fails_fast(status: int) -> None:
    provider = Scripted([ProviderError("bad", status=status, retryable=False), ok(GOOD)])
    result = await call(gateway(provider))
    assert result.status == "error"  # type: ignore[attr-defined]
    assert len(provider.requests) == 1


async def test_retry_budget_is_two() -> None:
    errors: list[ProviderResponse | Exception] = [
        ProviderError("x", status=503, retryable=True) for _ in range(5)
    ]
    provider = Scripted(errors)
    result = await call(gateway(provider))
    assert result.status == "error"  # type: ignore[attr-defined]
    assert len(provider.requests) == 3  # first try + 2 retries


async def test_refusal_and_max_tokens_handled() -> None:
    refused = Scripted([ok("", stop="refusal")])
    result = await call(gateway(refused))
    assert result.status == "refusal"  # type: ignore[attr-defined]
    assert result.parsed is None  # type: ignore[attr-defined]
    assert len(refused.requests) == 1  # never retried

    truncated = Scripted(
        [
            ok('{"label": "a", "conf', stop="max_tokens"),
            ok('{"label": "a", "conf', stop="max_tokens"),
        ]
    )
    result = await call(gateway(truncated))
    assert result.status == "max_tokens"  # type: ignore[attr-defined]
    assert result.parsed is None  # type: ignore[attr-defined]
    assert len(truncated.requests) == 2  # one retry with the error, then give up


async def test_invalid_output_retried_once_with_validation_error() -> None:
    provider = Scripted([ok('{"label": "z", "confidence": 2}'), ok(GOOD)])
    result = await call(gateway(provider))
    assert result.status == "ok"  # type: ignore[attr-defined]
    assert "validation" in provider.requests[1].user.lower()
    twice = Scripted([ok("not json"), ok('{"label": "q"}')])
    result = await call(gateway(twice))
    assert result.status == "invalid"  # type: ignore[attr-defined]


async def test_cost_tokens_and_step_records() -> None:
    sink = InMemoryStepSink()
    provider = Scripted([ok(GOOD)])
    with step_context(
        StepContext(run_id="r1", task_id="t1", tenant_id="ten", case_id="c1", node="classify_reply")
    ):
        result = await call(gateway(provider, sink))
    # Haiku 5.5: $0.10 / $0.50 per MTok → 1000 in + 200 out = 0.0001 + 0.0001
    assert result.cost_usd == pytest.approx(0.0002)  # type: ignore[attr-defined]
    [record] = sink.records
    assert record.kind == "llm"
    assert record.model == "claude-haiku-5-5"
    assert record.prompt_version == "classify_reply.v1"
    assert record.input_tokens == 1000
    assert record.run_id == "r1"
    assert record.status == "ok"


async def test_request_shape_uses_route_settings() -> None:
    provider = Scripted([ok(GOOD)])
    await call(gateway(provider))
    req = provider.requests[0]
    assert req.model == "claude-haiku-5-5"
    assert req.effort == "low"
    assert req.json_schema["additionalProperties"] is False
    assert req.timeout_s == 20.0


async def test_circuit_breaker_opens_after_five_failures() -> None:
    clock = {"t": 0.0}
    errors: list[ProviderResponse | Exception] = [
        ProviderError("x", status=503, retryable=False) for _ in range(5)
    ]
    provider = Scripted([*errors, ok(GOOD), ok(GOOD)])
    gw = gateway(provider, clock=lambda: clock["t"])
    for _ in range(5):
        assert (await call(gw)).status == "error"  # type: ignore[attr-defined]
    open_result = await call(gw)
    assert open_result.status == "circuit_open"  # type: ignore[attr-defined]
    assert len(provider.requests) == 5
    clock["t"] = 61.0
    assert (await call(gw)).status == "ok"  # type: ignore[attr-defined]


def test_route_table_matches_spec() -> None:
    from collections_copilot.config import Settings
    from collections_copilot.llm.gateway import routes

    table: dict[str, RouteSpec] = routes(Settings())
    expected: dict[str, tuple[str, str]] = {
        "prioritize_rationale": ("claude-haiku-5-5", "low"),
        "next_best_action": ("claude-opus-5-5", "medium"),
        "draft_message": ("claude-haiku-5-5", "medium"),
        "classify_reply": ("claude-haiku-5-5", "low"),
        "judge_tone": ("claude-haiku-5-5", "low"),
    }
    got: dict[str, tuple[str, str]] = {k: (v.model, v.effort) for k, v in table.items()}
    assert got == expected
    bedrock: Callable[[], dict[str, RouteSpec]] = lambda: routes(Settings(llm_provider="bedrock"))  # noqa: E731
    assert bedrock()["next_best_action"].model == "anthropic.claude-opus-5-5"
