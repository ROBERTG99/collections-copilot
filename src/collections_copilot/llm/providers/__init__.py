"""Provider adapters. Only this package imports provider SDKs (import-linter contract)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from collections_copilot.config import Settings
from collections_copilot.llm.providers.base import Provider
from collections_copilot.llm.providers.fake import FakeLLM

CASSETTES = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "llm" / "cassettes"
_WRAPPERS: list[Callable[[Provider], Provider]] = []


@contextmanager
def wrapping_providers(wrap: Callable[[Provider], Provider]) -> Iterator[None]:
    """Wrap every provider built inside the block (live evals meter cost and record cassettes)."""
    _WRAPPERS.append(wrap)
    try:
        yield
    finally:
        _WRAPPERS.remove(wrap)


def build_provider(settings: Settings) -> Provider:
    """CC_LLM_PROVIDER: fake (default, offline), anthropic (Claude API) or bedrock (Mantle)."""
    provider = _build(settings)
    for wrap in _WRAPPERS:
        provider = wrap(provider)
    return provider


def _build(settings: Settings) -> Provider:
    if settings.llm_provider == "anthropic":
        from collections_copilot.llm.providers.anthropic_sdk import anthropic_api

        return anthropic_api(settings.llm_timeout_s)
    if settings.llm_provider == "bedrock":
        from collections_copilot.llm.providers.anthropic_sdk import bedrock_mantle

        return bedrock_mantle(settings.aws_region, settings.llm_timeout_s)
    return FakeLLM(cassette_dir=CASSETTES if CASSETTES.exists() else None)
