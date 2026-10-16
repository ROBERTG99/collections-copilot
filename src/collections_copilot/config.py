"""Typed settings read from CC_* environment variables (documented in .env.example)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CC_", extra="ignore", env_file=None)

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8310"

    # data stores
    database_url: str = "postgresql+psycopg://cc:cc@localhost:8320/cc"
    redis_url: str = "redis://localhost:8330/0"
    sse_heartbeat_s: float = Field(default=15.0, gt=0, le=60)
    scheduler_interval_s: float = Field(default=30.0, gt=0, le=3600)

    # AWS-compatible services (ElasticMQ and MinIO locally)
    aws_region: str = "us-east-1"
    sqs_endpoint_url: str | None = "http://localhost:8340"
    sqs_tasks_queue: str = "cc-tasks"
    sqs_replies_queue: str = "cc-replies"
    sqs_tasks_dlq: str = "cc-tasks-dlq"
    sqs_replies_dlq: str = "cc-replies-dlq"
    s3_endpoint_url: str | None = "http://localhost:8341"
    s3_public_endpoint_url: str | None = None
    s3_bucket: str = "cc-uploads"

    # identity
    oidc_issuer: str = "http://localhost:8345"
    oidc_jwks_url: str | None = None
    oidc_audience: str = "cc-web"

    # LLM routing (§4.6)
    llm_provider: Literal["fake", "anthropic", "bedrock"] = "fake"
    model_fast: str = "claude-haiku-5-5"
    model_reasoning: str = "claude-opus-5-5"
    bedrock_model_fast: str = "anthropic.claude-haiku-5-5"
    bedrock_model_reasoning: str = "anthropic.claude-opus-5-5"
    llm_timeout_s: float = 20.0
    llm_max_retries: int = 2
    llm_breaker_failures: int = 5
    llm_breaker_reset_s: float = 60.0

    # SMS (Twilio REST; WireMock locally)
    twilio_base_url: str = "http://localhost:8342"
    twilio_account_sid: str = "AC00000000000000000000000000000000"
    twilio_auth_token: str = "dev-twilio-auth-token"  # noqa: S105 - dev double only
    twilio_from: str = "+15550001000"

    # ledger via mcp-gateway
    mcpgw_url: str = "http://localhost:8344/mcp"
    mcpgw_admin_url: str = "http://localhost:8348"
    mcpgw_issuer_url: str = "http://localhost:8349"
    mcpgw_webhook_secret: str = "dev-mcpgw-webhook-secret"  # noqa: S105 - dev only
    agent_token_provider: Literal["dev", "kms"] = "dev"  # noqa: S105
    agent_kms_key_id: str | None = None
    agent_issuer: str = "collections-copilot"

    # email (Mailpit locally)
    smtp_host: str = "localhost"
    smtp_port: int = 8346
    smtp_from: str = "copilot@collections.example"
    supervisor_email: str = "supervisors@collections.example"

    # policy (§3.1 FR-6)
    auto_approve_max_usd: float = 500.0
    auto_approve_min_confidence: float = 0.9
    worker_concurrency: int = 8
    rate_limit_per_min: int = 120
    upload_max_bytes: int = 10 * 1024 * 1024
    upload_max_rows: int = 20_000
    json_max_bytes: int = 1024 * 1024

    # observability
    otel_exporter_otlp_endpoint: str | None = None
    metrics_port: int = 8311

    demo_mode: Literal["offline", "live"] = Field(default="offline")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
