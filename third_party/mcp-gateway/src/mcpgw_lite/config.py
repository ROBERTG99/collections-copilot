"""Gateway configuration from MCPGW_* environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _upstreams(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in filter(None, (p.strip() for p in raw.split(","))):
        name, _, url = item.partition("=")
        out[name.strip()] = url.strip()
    return out


@dataclass(frozen=True)
class Config:
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "MCPGW_DATABASE_URL", "postgresql://cc:cc@localhost:8320/cc"
        )
    )
    upstreams: dict[str, str] = field(
        default_factory=lambda: _upstreams(
            os.environ.get("MCPGW_UPSTREAMS", "ledger=http://localhost:8343/mcp")
        )
    )
    jwks_urls: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            u for u in os.environ.get("MCPGW_JWKS_URL", "").split(",") if u.strip()
        )
    )
    policy_file: str = field(
        default_factory=lambda: os.environ.get(
            "MCPGW_POLICY_FILE", "deploy/mcpgw/policy/collections.yaml"
        )
    )
    webhook_url: str = field(
        default_factory=lambda: os.environ.get("MCPGW_APPROVAL_WEBHOOK_URL", "")
    )
    webhook_secret: str = field(
        default_factory=lambda: os.environ.get("MCPGW_APPROVAL_WEBHOOK_SECRET", "")
    )
    approval_ttl_s: int = field(
        default_factory=lambda: int(os.environ.get("MCPGW_APPROVAL_TTL_SECONDS", "900"))
    )
    dev_issuer: bool = field(
        default_factory=lambda: os.environ.get("MCPGW_DEV_ISSUER", "true").lower() == "true"
    )
    issuer: str = field(
        default_factory=lambda: os.environ.get("MCPGW_DEV_ISSUER_NAME", "mcp-gateway dev-issuer")
    )
    host: str = field(default_factory=lambda: os.environ.get("MCPGW_HOST", "0.0.0.0"))  # noqa: S104
    port: int = field(default_factory=lambda: int(os.environ.get("MCPGW_PORT", "8344")))
    admin_port: int = field(default_factory=lambda: int(os.environ.get("MCPGW_ADMIN_PORT", "8348")))
    issuer_port: int = field(
        default_factory=lambda: int(os.environ.get("MCPGW_ISSUER_PORT", "8349"))
    )
