"""FastAPI application factory for the collections-copilot API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from collections_copilot.agent.case_graph import build_skeleton_graph
from collections_copilot.agent.checkpointer import open_checkpointer
from collections_copilot.api.routers import cases
from collections_copilot.config import Settings, get_settings
from collections_copilot.db.session import make_engine, ping
from collections_copilot.integrations.queue.sqs import SqsQueueClient


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = make_engine(settings.database_url)
        async with AsyncExitStack() as stack:
            try:
                app.state.queues = await stack.enter_async_context(
                    SqsQueueClient(settings.aws_region, settings.sqs_endpoint_url)
                )
                saver = await stack.enter_async_context(open_checkpointer(settings.database_url))
                app.state.graph = build_skeleton_graph(saver)
            except Exception:  # dependencies down: liveness still answers, readiness reports 503
                app.state.graph = None
            try:
                yield
            finally:
                await app.state.engine.dispose()

    app = FastAPI(title="collections-copilot API", version="0.1.0", lifespan=lifespan)

    app.include_router(cases.router)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    async def readyz() -> JSONResponse:
        ok = await ping(app.state.engine)
        return JSONResponse(
            {"status": "ok" if ok else "unavailable"}, status_code=200 if ok else 503
        )

    return app


app = create_app
