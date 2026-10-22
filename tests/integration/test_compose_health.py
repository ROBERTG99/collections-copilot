"""Compose dependencies are healthy and the API answers /healthz on 8310."""

import json
import subprocess

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.docker]

SERVICES = {"postgres", "redis", "elasticmq", "minio", "wiremock-twilio", "mailpit", "api"}


def test_all_services_healthy() -> None:
    out = subprocess.run(
        ["docker", "compose", "ps", "--format", "json"], capture_output=True, text=True, check=True
    ).stdout
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    health = {r["Service"]: r.get("Health") or r.get("State") for r in rows}
    missing = SERVICES - set(health)
    assert not missing, f"not running: {missing}"
    unhealthy = {s: h for s, h in health.items() if s in SERVICES and h != "healthy"}
    assert unhealthy == {}
    resp = httpx.get("http://localhost:8310/healthz", timeout=5)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
