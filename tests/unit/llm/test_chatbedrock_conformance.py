"""ADR 0002: does langchain-aws ChatBedrockConverse meet the LLM rules in `.claude/rules/llm-code.md`? (offline probe)

ChatBedrock may be used in nodes only if every check passes. Two checks fail on the installed
version, so Bedrock traffic goes through the SDK's Mantle client. If langchain-aws changes this,
the test fails and ADR 0002 must be revisited.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from langchain_aws import ChatBedrockConverse

from collections_copilot.config import Settings
from collections_copilot.llm.providers import build_provider
from collections_copilot.llm.providers.anthropic_sdk import AnthropicSdkProvider
from collections_copilot.llm.providers.base import ProviderRequest
from collections_copilot.llm.providers.chatbedrock import (
    ChatBedrockProvider,
    conformance_report,
    is_conformant,
)

EXPECTED = {
    "json_schema_uses_native_output_format": True,
    "json_schema_has_no_forced_tool_choice": True,
    "adaptive_thinking_and_effort_passed_through": True,
    "json_schema_parses_valid_output": True,
    "function_calling_never_forces_tool_choice": True,
    # with_structured_output(method="function_calling") returns None when the model answers in
    # text: a silent failure the LLM rules forbid (outputs must be validated, never dropped).
    "function_calling_fails_loudly_without_tool_call": False,
    "structured_refusal_exposes_stop_reason": True,
    "plain_call_exposes_refusal_stop_reason": True,
    # Converse does not return stop_details.category, which the gateway logs for refusals.
    "refusal_category_available": False,
}


def test_chatbedrock_conformance_report() -> None:
    report = conformance_report()
    assert report == EXPECTED
    assert is_conformant(report) is False


def test_bedrock_uses_sdk_mantle_client(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "cclocal")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "cclocalsecret")
    provider = build_provider(Settings(llm_provider="bedrock"))
    assert isinstance(provider, AnthropicSdkProvider)
    assert provider.name == "bedrock"
    assert type(provider.client).__name__ == "AsyncAnthropicBedrockMantle"


async def test_chatbedrock_provider_maps_response() -> None:
    client = MagicMock()
    client.converse.return_value = {
        "output": {"message": {"role": "assistant", "content": [{"text": '{"action": "remind"}'}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 12, "outputTokens": 4, "totalTokens": 16},
        "metrics": {"latencyMs": 1},
    }
    provider = ChatBedrockProvider(
        lambda req: ChatBedrockConverse(model_id=req.model, client=client, region_name="us-east-1")
    )
    req = ProviderRequest(
        "next_best_action",
        "anthropic.claude-opus-5-5",
        "s",
        "u",
        {"type": "object", "properties": {}},
        "medium",
        512,
    )
    response = await provider.complete(req)
    assert json.loads(response.text) == {"action": "remind"}
    assert response.usage.input_tokens == 12
