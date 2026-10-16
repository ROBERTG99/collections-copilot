"""API liveness endpoint answers without dependencies."""

from fastapi.testclient import TestClient

from collections_copilot.api.main import create_app
from collections_copilot.config import Settings


def test_healthz_ok() -> None:
    app = create_app(Settings(database_url="postgresql+psycopg://x:x@127.0.0.1:1/x"))
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").status_code == 503
