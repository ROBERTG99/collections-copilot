"""Typed settings read from CC_* environment variables (documented in .env.example)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


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
    max_json_bytes: int = Field(default=1024 * 1024, gt=0)
    api_workers: int = Field(default=1, ge=1, le=16)  # uvicorn processes per API container
    rate_limit_per_minute: int = Field(default=120, gt=0)
    webhook_rate_limit_per_minute: int = Field(default=600, gt=0)
    redis_max_connections: int = Field(default=200, gt=0)
    sse_max_connections: int = Field(default=200, gt=0)  # a separate pool for SSE streams
    sse_streams_per_user: int = Field(default=5, gt=0)

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

    # LLM routing
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

    # policy
    # The policy ceiling: an environment may tighten it, never loosen it, and never to NaN.
    auto_approve_max_usd: float = Field(500.0, gt=0, le=500, allow_inf_nan=False)
    auto_approve_min_confidence: float = Field(0.9, ge=0.9, le=1, allow_inf_nan=False)
    worker_concurrency: int = 8
    rate_limit_per_min: int = 120
    upload_max_bytes: int = 10 * 1024 * 1024
    upload_max_rows: int = 20_000
    json_max_bytes: int = 1024 * 1024

    # observability
    otel_exporter_otlp_endpoint: str | None = None
    metrics_port: int = 8311

    demo_mode: Literal["offline", "live"] = Field(default="offline")
    # Outside production only +1555… and +34555… numbers are accepted at ingest or texted.
    synthetic_numbers_only: bool = True

    @field_validator("sqs_endpoint_url", "s3_endpoint_url", "s3_public_endpoint_url", mode="before")
    @classmethod
    def _empty_means_aws_default(cls, value: object) -> object:
        """ECS task definitions pass "" for an unset endpoint; botocore refuses ""."""
        return None if value == "" else value

    @model_validator(mode="after")
    def _refuse_dev_defaults_in_prod(self) -> Settings:
        """In production, or whenever the API is reachable under a public hostname (a forgotten
        CC_ENV=prod must not fail open), every shared secret must be set and the issuer must be
        HTTPS: a dev default would let anyone who read the repository sign webhooks."""
        if self.agent_token_provider == "kms" and not self.agent_kms_key_id:  # noqa: S105
            raise ValueError("CC_AGENT_TOKEN_PROVIDER=kms needs CC_AGENT_KMS_KEY_ID")
        if not self.synthetic_numbers_only and self.env != "prod":
            raise ValueError("CC_SYNTHETIC_NUMBERS_ONLY may be false only with CC_ENV=prod")
        host = urlsplit(self.public_base_url).hostname or ""
        public = "." in host and host != "127.0.0.1"
        if self.env != "prod" and not public:
            return self
        problems = [
            name
            for name, value in (
                ("CC_TWILIO_AUTH_TOKEN", self.twilio_auth_token),
                ("CC_MCPGW_WEBHOOK_SECRET", self.mcpgw_webhook_secret),
            )
            if not value or value.startswith("dev-")
        ]
        if self.twilio_from == Settings.model_fields["twilio_from"].default:
            problems.append("CC_TWILIO_FROM (the tenant's sending number)")
        if not self.oidc_issuer.startswith("https://") or "localhost" in self.oidc_issuer:
            problems.append("CC_OIDC_ISSUER (https, not localhost)")
        # A service URL left at its local default would call this container, not the service.
        for name, url in (
            ("CC_TWILIO_BASE_URL", self.twilio_base_url),
            ("CC_MCPGW_URL", self.mcpgw_url),
            ("CC_MCPGW_ADMIN_URL", self.mcpgw_admin_url),
            ("CC_SQS_ENDPOINT_URL", self.sqs_endpoint_url),
            ("CC_S3_ENDPOINT_URL", self.s3_endpoint_url),
        ):
            if url and (urlsplit(url).hostname or "") in LOCAL_HOSTS:
                problems.append(f"{name} (not localhost)")
        if problems:
            raise ValueError(f"production settings still use dev defaults: {', '.join(problems)}")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
