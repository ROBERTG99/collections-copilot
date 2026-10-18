"""Settings defaults are offline-safe and every CC_ field is documented in .env.example."""

from pathlib import Path

from collections_copilot.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_defaults_are_offline() -> None:
    s = Settings()
    assert s.llm_provider == "fake"
    assert s.model_reasoning == "claude-opus-5-5"
    assert s.model_fast == "claude-haiku-5-5"
    assert s.auto_approve_max_usd == 500.0


def test_every_setting_is_documented_in_env_example() -> None:
    documented = {
        line.split("=", 1)[0]
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line and not line.startswith("#")
    }
    missing = [
        f"CC_{name.upper()}"
        for name in Settings.model_fields
        if f"CC_{name.upper()}" not in documented
    ]
    assert missing == []


def test_prod_refuses_dev_secrets_and_local_issuer() -> None:
    import pytest
    from pydantic import ValidationError

    from collections_copilot.config import Settings

    with pytest.raises(ValidationError, match="CC_TWILIO_AUTH_TOKEN"):
        Settings(env="prod", oidc_issuer="https://cognito-idp.example/pool")
    with pytest.raises(ValidationError, match="CC_OIDC_ISSUER"):
        Settings(env="prod", twilio_auth_token="x" * 32, mcpgw_webhook_secret="y" * 32)
    ok = Settings(
        env="prod",
        twilio_auth_token="x" * 32,
        mcpgw_webhook_secret="y" * 32,
        oidc_issuer="https://cognito-idp.example/pool",
        # production also needs real service URLs (test_prod_refuses_local_service_urls)
        sqs_endpoint_url="",
        s3_endpoint_url="",
        twilio_base_url="https://twilio.example",
        mcpgw_url="http://mcp-gateway:8344/mcp",
        mcpgw_admin_url="http://mcp-gateway-admin:8348",
        twilio_from="+15550007777",
    )
    assert ok.env == "prod"


def test_dev_secrets_are_refused_whenever_the_api_is_public() -> None:
    """M6 security audit M9: forgetting CC_ENV=prod must not accept the published dev secrets."""
    import pytest

    from collections_copilot.config import Settings

    with pytest.raises(ValueError, match="dev defaults"):
        Settings(env="dev", public_base_url="https://collections.example.com")
    Settings(env="dev", public_base_url="http://localhost:8310")  # local stack is fine
    Settings(env="dev", public_base_url="http://api:8310")  # compose service name is fine


def test_kms_agent_tokens_need_a_key() -> None:
    import pytest

    from collections_copilot.config import Settings

    with pytest.raises(ValueError, match="CC_AGENT_KMS_KEY_ID"):
        Settings(agent_token_provider="kms", agent_kms_key_id=None)


def test_real_numbers_only_in_production() -> None:
    """M6 security audit M11: outside prod only +1555… and +34555… numbers are accepted."""
    import pytest

    from collections_copilot.config import Settings

    with pytest.raises(ValueError, match="CC_SYNTHETIC_NUMBERS_ONLY"):
        Settings(env="dev", synthetic_numbers_only=False)


PROD_OK = {
    "env": "prod",
    "twilio_auth_token": "x" * 32,
    "mcpgw_webhook_secret": "y" * 32,
    "oidc_issuer": "https://cognito-idp.example/pool",
    "public_base_url": "https://collections.example.com",
    "sqs_endpoint_url": "",
    "s3_endpoint_url": "",
    "twilio_base_url": "https://twilio.example",
    "mcpgw_url": "http://mcp-gateway:8344/mcp",
    "mcpgw_admin_url": "http://mcp-gateway-admin:8348",
    "twilio_from": "+15550007777",
}


def test_prod_refuses_the_default_sending_number() -> None:
    """M8 review: a task definition without CC_TWILIO_FROM must not send from the dev number."""
    import pytest

    with pytest.raises(ValueError, match="CC_TWILIO_FROM"):
        Settings(**{**PROD_OK, "twilio_from": "+15550001000"})


def test_empty_endpoint_urls_mean_the_aws_default() -> None:
    """M7 Terraform review M3: ECS passes "" for the SQS and S3 endpoints; botocore refuses ""."""
    s = Settings(sqs_endpoint_url="", s3_endpoint_url="", s3_public_endpoint_url="")
    assert (s.sqs_endpoint_url, s.s3_endpoint_url, s.s3_public_endpoint_url) == (None, None, None)


def test_prod_refuses_local_service_urls() -> None:
    """A variable missing from the task definition must fail at start-up, not call localhost."""
    import pytest

    assert Settings(**PROD_OK).sqs_endpoint_url is None
    for name, local in (
        ("twilio_base_url", "http://localhost:8342"),
        ("mcpgw_url", "http://localhost:8344/mcp"),
        ("mcpgw_admin_url", "http://127.0.0.1:8348"),
        ("sqs_endpoint_url", "http://localhost:8340"),
        ("s3_endpoint_url", "http://localhost:8341"),
    ):
        with pytest.raises(ValueError, match=f"CC_{name.upper()}"):
            Settings(**{**PROD_OK, name: local})


def test_auto_approval_ceiling_is_bounded_and_finite() -> None:
    """T-51 compliance review: NaN or a looser environment ceiling would fail open."""
    import pytest

    for field, value in (
        ("auto_approve_max_usd", float("nan")),
        ("auto_approve_max_usd", 500.01),
        ("auto_approve_max_usd", 0),
        ("auto_approve_min_confidence", float("nan")),
        ("auto_approve_min_confidence", 0.89),
        ("auto_approve_min_confidence", 1.01),
    ):
        with pytest.raises(ValueError, match=field):
            Settings(**{field: value})
