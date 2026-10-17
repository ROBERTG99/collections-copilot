"""FastAPI application factory for the collections-copilot API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from collections_copilot.api import errors
from collections_copilot.api.deps.auth import JwksTokenVerifier, TokenVerifier
from collections_copilot.api.middleware.limits import (
    BodyLimitMiddleware,
    IdempotencyMiddleware,
    RateLimitMiddleware,
)
from collections_copilot.api.routers import (
    accounts,
    approvals,
    audit,
    cases,
    events,
    ingest,
    me,
    metrics,
    optouts,
    promises,
    queue,
    webhooks,
)
from collections_copilot.api.routers import (
    settings as settings_router,
)
from collections_copilot.config import Settings, get_settings
from collections_copilot.db.session import make_engine, make_sessionmaker, ping
from collections_copilot.integrations.email.smtp import Notifier, SmtpNotifier
from collections_copilot.integrations.mcp import build_gateway_admin
from collections_copilot.integrations.mcp.admin import GatewayAdmin
from collections_copilot.integrations.queue.sqs import QueueClient, SqsQueueClient
from collections_copilot.integrations.sms import build_sms_sender
from collections_copilot.integrations.sms.base import SmsSender
from collections_copilot.integrations.storage.s3 import ObjectStore, S3ObjectStore

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
    queues: QueueClient | None = None,
    object_store: ObjectStore | None = None,
    sms_sender: SmsSender | None = None,
    gateway_admin: GatewayAdmin | None = None,
    notifier: Notifier | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = make_engine(settings.database_url)
        app.state.sessionmaker = make_sessionmaker(app.state.engine)
        app.state.sms = sms_sender or build_sms_sender(settings)  # STOP confirmations only
        app.state.redis = Redis.from_url(
            settings.redis_url, max_connections=settings.redis_max_connections
        )
        app.state.gateway_admin = gateway_admin or build_gateway_admin(settings)
        app.state.notifier = notifier or SmtpNotifier(
            settings.smtp_host, settings.smtp_port, settings.smtp_from, settings.supervisor_email
        )
        app.state.token_verifier = token_verifier or JwksTokenVerifier(
            settings.oidc_issuer, settings.oidc_audience, settings.oidc_jwks_url
        )
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
            try:
                yield
            finally:
                await app.state.engine.dispose()
                await app.state.redis.aclose()

    app = FastAPI(title="collections-copilot API", version="0.1.0", lifespan=lifespan)
    errors.install(app)
    # Added last runs first: body size → rate limit → idempotency → routes.
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_json_bytes)
    app.include_router(me.router)
    app.include_router(ingest.router)
    app.include_router(queue.router)
    app.include_router(accounts.router)
    app.include_router(cases.router)
    app.include_router(approvals.router)
    app.include_router(settings_router.router)
    app.include_router(events.router)
    app.include_router(promises.router)
    app.include_router(metrics.router)
    app.include_router(audit.router)
    app.include_router(optouts.router)
    app.include_router(webhooks.router)
    app.include_router(webhooks.mcpgw_router)

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
