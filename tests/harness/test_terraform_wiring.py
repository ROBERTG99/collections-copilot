"""The task definitions in envs/dev must name settings the services read and
paths the API serves. Two of these were wrong and every Terraform test still passed."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from collections_copilot.api.routers.webhooks import mcpgw_router
from collections_copilot.api.routers.webhooks import router as twilio_router
from collections_copilot.config import Settings

ROOT = Path(__file__).resolve().parents[2]
MAIN_TF = (ROOT / "infra/terraform/envs/dev/main.tf").read_text()


def keys(prefix: str) -> set[str]:
    return set(re.findall(rf"\b({prefix}[A-Z0-9_]+)\s*=", MAIN_TF))


def value(name: str) -> str:
    match = re.search(rf'\b{name}\s*=\s*"([^"]*)"', MAIN_TF)
    assert match, f"{name} is not set to a literal in envs/dev/main.tf"
    return match.group(1)


def test_cc_settings_exist() -> None:
    fields = {f"CC_{name.upper()}" for name in Settings.model_fields}
    assert keys("CC_") - fields == set()


def test_gateway_settings_are_the_ones_it_reads() -> None:
    config = (ROOT / "third_party/mcp-gateway/src/mcpgw_lite/config.py").read_text()
    read = set(re.findall(r'"(MCPGW_[A-Z0-9_]+)"', config))
    assert keys("MCPGW_") - read == set()
    for required in ("MCPGW_UPSTREAMS", "MCPGW_DATABASE_URL", "MCPGW_JWKS_URL"):
        assert required in keys("MCPGW_")


def test_web_settings_are_the_ones_the_bff_reads() -> None:
    config = (ROOT / "web/lib/config.ts").read_text()
    read = set(re.findall(r"env\.(WEB_[A-Z0-9_]+)", config))
    assert keys("WEB_") - read == set()


def test_the_public_route_is_the_twilio_webhook_prefix() -> None:
    patterns = re.findall(r'path_patterns\s*=\s*\["([^"]+)"\]', MAIN_TF)
    assert patterns == [f"{twilio_router.prefix}/*"]


def test_gateway_calls_paths_the_api_serves() -> None:
    from collections_copilot.api.main import create_app

    served = set(create_app(Settings()).openapi()["paths"])  # included routers stay nested
    webhook = urlsplit(value("MCPGW_APPROVAL_WEBHOOK_URL")).path
    assert webhook.startswith(mcpgw_router.prefix)
    assert webhook in served
    assert urlsplit(value("MCPGW_JWKS_URL")).path in served


def test_no_service_url_points_at_localhost() -> None:
    urls = re.findall(r'\b[A-Z_]+_URL\s*=\s*"([^"]+)"', MAIN_TF)
    assert urls
    assert [u for u in urls if urlsplit(u).hostname in {"localhost", "127.0.0.1"}] == []
