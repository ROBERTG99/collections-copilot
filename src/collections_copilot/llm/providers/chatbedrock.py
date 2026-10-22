"""langchain-aws ChatBedrockConverse adapter and its conformance probe against the LLM rules (ADR 0002).

The probe runs offline against a stubbed Bedrock runtime client and records what the installed
langchain-aws actually sends and returns. `build_provider` uses ChatBedrock only when every check
passes; otherwise Bedrock goes through the SDK's Mantle client.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

from langchain_aws import ChatBedrockConverse
from pydantic import BaseModel

from collections_copilot.llm.providers.base import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    Usage,
    is_retryable_status,
)


def _chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def _status_of(exc: BaseException) -> int | None:
    """HTTP status from a botocore ClientError anywhere in the cause chain."""
    for e in _chain(exc):
        response = getattr(e, "response", None)
        if isinstance(response, dict):
            status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
            if isinstance(status, int):
                return status
    return None


def _is_connection_error(exc: BaseException) -> bool:
    """Retry only transport failures (connection refused, timeouts), like the Claude adapter."""
    names = {type(e).__name__ for e in _chain(exc)}
    return any("Connect" in n or "Timeout" in n for n in names)


PROBE_MODEL = "anthropic.claude-opus-5-5"


class _ProbeOut(BaseModel):
    action: str
    confidence: float


def _converse_response(content: list[dict[str, Any]], stop: str) -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop,
        "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
        "metrics": {"latencyMs": 1},
    }


def _stub(responses: list[dict[str, Any]], calls: list[dict[str, Any]]) -> MagicMock:
    client = MagicMock()

    def converse(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return responses.pop(0)

    client.converse.side_effect = converse
    return client


def _llm(client: Any) -> ChatBedrockConverse:
    return ChatBedrockConverse(
        model_id=PROBE_MODEL,
        client=client,
        region_name="us-east-1",
        additional_model_request_fields={
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "medium"},
        },
    )


def conformance_report() -> dict[str, bool]:
    """LLM-rule checks against the installed langchain-aws (offline)."""
    report: dict[str, bool] = {}
    ok_json = '{"action": "remind", "confidence": 0.9}'

    calls: list[dict[str, Any]] = []
    client = _stub([_converse_response([{"text": ok_json}], "end_turn")], calls)
    parsed = _llm(client).with_structured_output(_ProbeOut, method="json_schema").invoke("hi")
    sent = calls[-1]
    report["json_schema_uses_native_output_format"] = "textFormat" in json.dumps(
        sent.get("outputConfig", {})
    )
    report["json_schema_has_no_forced_tool_choice"] = "toolConfig" not in sent
    report["adaptive_thinking_and_effort_passed_through"] = sent.get(
        "additionalModelRequestFields", {}
    ) == {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "medium"},
    }
    report["json_schema_parses_valid_output"] = isinstance(parsed, _ProbeOut)

    calls = []
    client = _stub([_converse_response([{"text": ok_json}], "end_turn")], calls)
    via_tools = (
        _llm(client).with_structured_output(_ProbeOut, method="function_calling").invoke("hi")
    )
    tool_choice = calls[-1].get("toolConfig", {}).get("toolChoice")
    report["function_calling_never_forces_tool_choice"] = (
        tool_choice is None or "auto" in tool_choice
    )
    report["function_calling_fails_loudly_without_tool_call"] = via_tools is not None

    calls = []
    client = _stub([_converse_response([], "refusal")], calls)
    try:
        result: Any = (
            _llm(client)
            .with_structured_output(_ProbeOut, method="json_schema", include_raw=True)
            .invoke("hi")
        )
        raw = result["raw"]
        report["structured_refusal_exposes_stop_reason"] = (
            raw.response_metadata.get("stopReason") == "refusal"
        )
    except Exception:  # the parser path raises before the stop reason can be read
        report["structured_refusal_exposes_stop_reason"] = False

    calls = []
    client = _stub([_converse_response([], "refusal")], calls)
    message = _llm(client).invoke("hi")
    report["plain_call_exposes_refusal_stop_reason"] = (
        message.response_metadata.get("stopReason") == "refusal"
    )
    report["refusal_category_available"] = "stop_details" in json.dumps(message.response_metadata)
    return report


def is_conformant(report: dict[str, bool] | None = None) -> bool:
    return all((report or conformance_report()).values())


class ChatBedrockProvider:
    """Provider over ChatBedrockConverse (json_schema method, plain call so stopReason is visible)."""

    name = "chatbedrock"

    def __init__(self, factory: Callable[[ProviderRequest], ChatBedrockConverse]) -> None:
        self.factory = factory

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        llm = self.factory(request)
        try:
            message = await llm.bind(
                output_config={
                    "textFormat": {
                        "type": "json_schema",
                        "structure": {
                            "jsonSchema": {
                                "schema": json.dumps(request.json_schema),
                                "name": request.route,
                            }
                        },
                    }
                }
            ).ainvoke([("system", request.system), ("human", request.user)])
        except Exception as exc:
            status = _status_of(exc)
            raise ProviderError(
                f"bedrock converse error: {type(exc).__name__}",
                status=status,
                retryable=is_retryable_status(status) if status else _is_connection_error(exc),
            ) from exc
        usage: dict[str, Any] = dict(message.usage_metadata or {})
        return ProviderResponse(
            text=message.text if isinstance(message.text, str) else str(message.content),
            stop_reason=str(message.response_metadata.get("stopReason", "end_turn")),
            model=request.model,
            usage=Usage(
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
            ),
        )
