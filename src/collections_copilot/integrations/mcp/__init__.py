"""Ledger write-back through mcp-gateway."""

from __future__ import annotations

from collections_copilot.config import Settings
from collections_copilot.integrations.mcp.ledger import McpLedgerTools
from collections_copilot.integrations.mcp.tokens import (
    AgentTokenProvider,
    DevIssuerTokenProvider,
    KmsTokenProvider,
)


def build_token_provider(settings: Settings) -> AgentTokenProvider:
    if settings.agent_token_provider == "kms" and settings.agent_kms_key_id:  # noqa: S105
        return KmsTokenProvider(
            settings.agent_kms_key_id, settings.agent_issuer, settings.aws_region
        )
    return DevIssuerTokenProvider(settings.mcpgw_issuer_url)


def build_ledger(settings: Settings) -> McpLedgerTools:
    return McpLedgerTools(
        settings.mcpgw_url, build_token_provider(settings), timeout_s=settings.llm_timeout_s
    )
