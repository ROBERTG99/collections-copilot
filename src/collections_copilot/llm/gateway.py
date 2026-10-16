"""The single LLM gateway (BUILD.md §4.6, Appendix B).

Provider selection, timeouts, retries (only 408/409/429/5xx/connection), a circuit breaker
(5 consecutive failures → open 60 s), schema validation with one corrective retry, refusal and
max_tokens handling, token and cost accounting, and one step record per call. Callers fall back to
deterministic rules or templates whenever the result status is not "ok".
"""

from __future__ import annotations

import asyncio
import copy
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from collections_copilot.config import Settings
from collections_copilot.llm.pricing import cost_usd
from collections_copilot.llm.providers.base import (
    Effort,
    Provider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    Usage,
)
from collections_copilot.observability.steps import (
    NullStepSink,
    StepRecord,
    StepSink,
    current_context,
)

T = TypeVar("T", bound=BaseModel)
ResultStatus = Literal["ok", "refusal", "max_tokens", "invalid", "error", "circuit_open"]
UNSUPPORTED_SCHEMA_KEYS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "pattern",
    "format",
    "title",
    "default",
    "uniqueItems",
}


@dataclass(frozen=True)
class RouteSpec:
    name: str
    model: str
    effort: Effort
    max_tokens: int


def routes(settings: Settings) -> dict[str, RouteSpec]:
    """§4.6 routing table. Model IDs come from configuration only."""
    bedrock = settings.llm_provider == "bedrock"
    fast = settings.bedrock_model_fast if bedrock else settings.model_fast
    reasoning = settings.bedrock_model_reasoning if bedrock else settings.model_reasoning
    return {
        "prioritize_rationale": RouteSpec("prioritize_rationale", fast, "low", 1024),
        "next_best_action": RouteSpec("next_best_action", reasoning, "medium", 4096),
        "draft_message": RouteSpec("draft_message", fast, "medium", 2048),
        "classify_reply": RouteSpec("classify_reply", fast, "low", 1024),
        "judge_tone": RouteSpec("judge_tone", fast, "low", 1024),
    }


@dataclass
class LLMResult[R: BaseModel]:
    status: ResultStatus
    parsed: R | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    attempts: int = 0
    error: str | None = None
    refusal_category: str | None = None
    raw_text: str | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.parsed is not None


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema for output_config.format: closed objects, every property required, and only
    keywords structured outputs accept (constraints are enforced afterwards by Pydantic)."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items() if k not in UNSUPPORTED_SCHEMA_KEYS}
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"])
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(copy.deepcopy(model.model_json_schema()))  # type: ignore[no-any-return]


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n") + 1 :] if "\n" in text else text
    return json.loads(text)


class CircuitBreaker:
    def __init__(self, failures: int, reset_s: float, clock: Callable[[], float]) -> None:
        self.threshold = failures
        self.reset_s = reset_s
        self.clock = clock
        self.consecutive = 0
        self.open_until = 0.0

    def allow(self) -> bool:
        return self.clock() >= self.open_until

    def success(self) -> None:
        self.consecutive = 0

    def failure(self) -> None:
        self.consecutive += 1
        if self.consecutive >= self.threshold:
            self.open_until = self.clock() + self.reset_s
            self.consecutive = 0


class LLMGateway:
    def __init__(
        self,
        provider: Provider,
        *,
        sink: StepSink | None = None,
        timeout_s: float = 20.0,
        max_retries: int = 2,
        breaker_failures: int = 5,
        breaker_reset_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.provider = provider
        self.sink = sink or NullStepSink()
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.breaker = CircuitBreaker(breaker_failures, breaker_reset_s, clock)
        self.sleep = sleep

    @classmethod
    def from_settings(
        cls, settings: Settings, provider: Provider, sink: StepSink | None = None
    ) -> LLMGateway:
        return cls(
            provider,
            sink=sink,
            timeout_s=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
            breaker_failures=settings.llm_breaker_failures,
            breaker_reset_s=settings.llm_breaker_reset_s,
        )

    async def _call_with_retries(
        self, request: ProviderRequest
    ) -> tuple[ProviderResponse | None, int, str | None]:
        attempts = 0
        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            attempts += 1
            try:
                response = await asyncio.wait_for(
                    self.provider.complete(request), request.timeout_s
                )
                return response, attempts, None
            except TimeoutError:
                last_error = "timeout"
                retryable = True
            except ProviderError as exc:
                last_error = f"{exc.status or 'connection'}: {exc}"
                retryable = exc.retryable
            if not retryable or attempt == self.max_retries:
                break
            await self.sleep(min(8.0, 0.5 * 2**attempt) + random.uniform(0, 0.25))  # noqa: S311 - jitter
        return None, attempts, last_error

    async def structured(
        self,
        route: RouteSpec,
        *,
        system: str,
        user: str,
        schema: type[T],
        prompt_version: str,
        prompt_hash: str,
        validator: Callable[[T], str | None] | None = None,
    ) -> LLMResult[T]:
        """One structured call. `validator` adds domain checks (return an error string to reject)."""
        started = time.perf_counter()
        result: LLMResult[T]
        if not self.breaker.allow():
            result = LLMResult(status="circuit_open", error="circuit breaker open")
            await self._record(route, prompt_version, prompt_hash, result, started)
            return result
        json_schema = strict_json_schema(schema)
        usage_in = usage_out = 0
        total_cost = 0.0
        attempts = 0
        current_user = user
        max_tokens = route.max_tokens
        model_used: str | None = None
        status: ResultStatus = "invalid"
        error: str | None = None
        parsed: T | None = None
        raw_text: str | None = None
        refusal_category: str | None = None
        for _validation_round in range(2):
            request = ProviderRequest(
                route=route.name,
                model=route.model,
                system=system,
                user=current_user,
                json_schema=json_schema,
                effort=route.effort,
                max_tokens=max_tokens,
                timeout_s=self.timeout_s,
            )
            response, n, call_error = await self._call_with_retries(request)
            attempts += n
            if response is None:
                self.breaker.failure()
                status, error = "error", call_error
                break
            self.breaker.success()
            model_used = response.model
            usage_in += response.usage.input_tokens
            usage_out += response.usage.output_tokens
            total_cost += cost_usd(response.model, response.usage)
            raw_text = response.text
            if response.stop_reason == "refusal":
                status, error, refusal_category = (
                    "refusal",
                    "model refused",
                    response.refusal_category,
                )
                break
            if response.stop_reason == "max_tokens":
                status, error = "max_tokens", "output truncated at max_tokens"
                max_tokens *= 2
                current_user = f"{user}\n\nYour previous answer was cut off. Return only the JSON object, briefly."
                continue
            try:
                candidate = schema.model_validate(_extract_json(response.text))
                problem = validator(candidate) if validator else None
                if problem:
                    raise ValueError(problem)
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                status, error = "invalid", f"validation failed: {exc}"[:500]
                current_user = (
                    f"{user}\n\nYour previous output failed validation: {str(exc)[:400]}\n"
                    "Return only a JSON object that matches the schema."
                )
                continue
            status, parsed, error = "ok", candidate, None
            break
        result = LLMResult(
            status=status,
            parsed=parsed,
            model=model_used,
            input_tokens=usage_in,
            output_tokens=usage_out,
            cost_usd=total_cost,
            attempts=attempts,
            error=error,
            refusal_category=refusal_category,
            raw_text=raw_text,
        )
        await self._record(route, prompt_version, prompt_hash, result, started)
        return result

    async def _record(
        self,
        route: RouteSpec,
        prompt_version: str,
        prompt_hash: str,
        result: LLMResult[Any],
        started: float,
    ) -> None:
        result.latency_ms = (time.perf_counter() - started) * 1000
        ctx = current_context()
        step_status = "ok" if result.status == "ok" else result.status
        await self.sink.record(
            StepRecord(
                run_id=ctx.run_id,
                task_id=ctx.task_id,
                tenant_id=ctx.tenant_id,
                case_id=ctx.case_id,
                node=ctx.node or route.name,
                kind="llm",
                model=result.model or route.model,
                prompt_version=prompt_version,
                prompt_hash=prompt_hash,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
                status=step_status,
                error=result.error,
            )
        )


def usage_from(input_tokens: int = 0, output_tokens: int = 0) -> Usage:
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens)
