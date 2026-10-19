"""T-17: provider adapters against recorded responses (no network): request shape follows
Appendix B and responses map to the gateway's types for the Claude API and Bedrock Mantle."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest

from collections_copilot.llm.gateway import LLMGateway, RouteSpec
from collections_copilot.llm.providers.anthropic_sdk import (
    AnthropicSdkProvider,
    anthropic_api,
    bedrock_mantle,
)
from collections_copilot.llm.providers.base import ProviderError, ProviderRequest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "llm"
SCHEMA = {
    "type": "object",
    "properties": {"action": {"type": "string", "enum": ["remind", "pause"]}},
    "required": ["action"],
    "additionalProperties": False,
}


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())  # type: ignore[no-any-return]


class Recorder:
    def __init__(self, responses: list[tuple[int, dict[str, Any]]]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        status, body = self.responses.pop(0)
        return httpx2.Response(status, json=body, headers={"request-id": "req_fixture"})

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler))

    def body(self, i: int = 0) -> dict[str, Any]:
        return json.loads(self.requests[i].content)  # type: ignore[no-any-return]


def request(model: str = "claude-opus-5-5", effort: str = "medium") -> ProviderRequest:
    return ProviderRequest(
        "next_best_action", model, "STABLE SYSTEM", "<context>{}</context>", SCHEMA, effort, 4096
    )  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")


Factory = Callable[[httpx2.AsyncClient], AnthropicSdkProvider]
PROVIDERS: dict[str, Factory] = {
    "anthropic": lambda c: anthropic_api(20.0, http_client=c),
    "bedrock": lambda c: bedrock_mantle("us-east-1", 20.0, http_client=c),
}


@pytest.mark.parametrize("name", ["anthropic", "bedrock"])
async def test_request_shape_follows_appendix_b(name: str) -> None:
    rec = Recorder([(200, fixture("messages_nba_ok.json"))])
    provider = PROVIDERS[name](rec.client())
    model = "anthropic.claude-opus-5-5" if name == "bedrock" else "claude-opus-5-5"
    response = await provider.complete(request(model))
    body = rec.body()
    assert body["model"] == model
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert [m["role"] for m in body["messages"]] == ["user"]  # no assistant prefill
    assert "tool_choice" not in body
    assert "budget_tokens" not in json.dumps(body)
    assert "temperature" not in body
    if name == "anthropic":
        assert body["fallbacks"] == "default"
        assert "server-side-fallback-2026-07-01" in rec.requests[0].headers["anthropic-beta"]
        assert rec.requests[0].url.host == "api.anthropic.com"
    else:
        assert "fallbacks" not in body
        assert "bedrock-mantle" in rec.requests[0].url.host
    assert response.stop_reason == "end_turn"
    assert json.loads(response.text)["action"] == "pause"
    assert response.usage.cache_read_input_tokens == 1200


async def test_haiku_route_has_no_server_fallback() -> None:
    rec = Recorder([(200, fixture("messages_max_tokens.json"))])
    provider = PROVIDERS["anthropic"](rec.client())
    response = await provider.complete(request("claude-haiku-5-5", "low"))
    assert "fallbacks" not in rec.body()
    assert response.stop_reason == "max_tokens"


async def test_refusal_category_mapped() -> None:
    rec = Recorder([(200, fixture("messages_refusal.json"))])
    response = await PROVIDERS["anthropic"](rec.client()).complete(request())
    assert response.stop_reason == "refusal"
    assert response.refusal_category == "general_harms"
    assert response.text == ""


@pytest.mark.parametrize(
    ("status", "retryable"), [(429, True), (529, True), (500, True), (400, False), (404, False)]
)
async def test_errors_map_to_retryability(status: int, retryable: bool) -> None:
    body = fixture("error_429.json" if retryable else "error_400.json")
    rec = Recorder([(status, body)])
    with pytest.raises(ProviderError) as info:
        await PROVIDERS["anthropic"](rec.client()).complete(request())
    assert info.value.status == status
    assert info.value.retryable is retryable
    assert len(rec.requests) == 1  # the SDK does not retry; the gateway owns retries


async def test_gateway_end_to_end_over_recorded_transport() -> None:
    from typing import Literal

    from pydantic import BaseModel

    class NBA(BaseModel):
        action: Literal["remind", "payment_plan", "escalate", "pause", "no_action"]
        rationale: str
        confidence: float
        cited_note_ids: list[str]

    rec = Recorder([(429, fixture("error_429.json")), (200, fixture("messages_nba_ok.json"))])
    gateway = LLMGateway(PROVIDERS["anthropic"](rec.client()), sleep=_no_sleep)
    result = await gateway.structured(
        RouteSpec("next_best_action", "claude-opus-5-5", "medium", 4096),
        system="s",
        user="u",
        schema=NBA,
        prompt_version="next_best_action.v1",
        prompt_hash="h",
    )
    assert result.ok
    assert result.parsed is not None
    assert result.parsed.action == "pause"
    assert result.attempts == 2
    # $4/MTok in, $20/MTok out, $0.20/MTok cache read
    assert result.cost_usd == pytest.approx((1800 * 4 + 120 * 20 + 1200 * 0.2) / 1_000_000)


async def _no_sleep(_: float) -> None:
    return None
