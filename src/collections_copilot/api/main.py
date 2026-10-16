"""FastAPI application factory for the collections-copilot API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from collections_copilot.api import errors
from collections_copilot.api.deps.auth import JwksTokenVerifier, TokenVerifier
from collections_copilot.api.routers import cases, ingest
from collections_copilot.config import Settings, get_settings
from collections_copilot.db.session import make_engine, make_sessionmaker, ping
from collections_copilot.integrations.queue.sqs import QueueClient, SqsQueueClient
from collections_copilot.integrations.storage.s3 import ObjectStore, S3ObjectStore

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    queues: QueueClient | None = None,
    object_store: ObjectStore | None = None,
    start_graph: bool = True,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = make_engine(settings.database_url)
        app.state.sessionmaker = make_sessionmaker(app.state.engine)
        app.state.token_verifier = token_verifier or JwksTokenVerifier(
            settings.oidc_issuer, settings.oidc_audience, settings.oidc_jwks_url
        )
        app.state.graph = None
        async with AsyncExitStack() as stack:
            app.state.queues = queues or await stack.enter_async_context(
                SqsQueueClient(settings.aws_region, settings.sqs_endpoint_url)
            )
            store: Any = object_store
            if store is None:
                store = await stack.enter_async_context(
                    S3ObjectStore(
                        settings.s3_bucket,
                        settings.aws_region,
                        settings.s3_endpoint_url,
                        settings.s3_public_endpoint_url,
                    )
                )
                try:
                    await store.ensure_bucket()
                except Exception:
                    log.warning("object store unavailable at startup")
            app.state.object_store = store
            if start_graph:
                try:
                    from collections_copilot.agent.case_graph import build_skeleton_graph
                    from collections_copilot.agent.checkpointer import open_checkpointer

                    saver = await stack.enter_async_context(
                        open_checkpointer(settings.database_url)
                    )
                    app.state.graph = build_skeleton_graph(saver)
                except Exception:
                    log.warning("checkpointer unavailable at startup")
            try:
                yield
            finally:
                await app.state.engine.dispose()

    app = FastAPI(title="collections-copilot API", version="0.1.0", lifespan=lifespan)
    errors.install(app)
    app.include_router(ingest.router)
    app.include_router(cases.router)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    async def readyz(request: Request) -> JSONResponse:
        ok = await ping(request.app.state.engine)
        return JSONResponse(
            {"status": "ok" if ok else "unavailable"}, status_code=200 if ok else 503
        )

    return app


app = create_app
