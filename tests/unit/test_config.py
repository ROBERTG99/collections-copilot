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
