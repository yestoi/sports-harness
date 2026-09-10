"""The phase 5 settings, their exact defaults, and the two key switches.

Every value here is quoted from the addendum. A test that reads the setting back rather than
restating the number would pass against any number at all, so these are literals on purpose:
this file is where the addendum's arithmetic is pinned.
"""
from decimal import Decimal
from pathlib import Path

from harness.config.settings import Settings


def test_the_spend_caps_are_the_u4_values_as_decimals(env_settings):
    assert env_settings.veto_daily_usd_cap == Decimal("25")
    assert env_settings.veto_weekly_usd_cap == Decimal("150")
    assert isinstance(env_settings.veto_daily_usd_cap, Decimal)
    assert isinstance(env_settings.veto_weekly_usd_cap, Decimal)


def test_the_veto_shape_settings(env_settings):
    assert env_settings.veto_max_searches == 3
    assert env_settings.veto_bucket_minutes == 30


def test_the_rfq_settings(env_settings):
    assert env_settings.rfq_margin_per_leg == Decimal("0.03")
    assert env_settings.rfq_collateral_cap_usd == Decimal("50")
    assert env_settings.rfq_listener_enabled is True


def test_the_nws_settings(env_settings):
    """R:212 fixes the User-Agent verbatim. A 403 or a blocklist on this exact string is a user
    gate, never an edit (ruling A-M11), which is why the test quotes it in full."""
    assert env_settings.nws_user_agent == "sports-harness/1 (self-hosted research harness)"
    assert env_settings.nws_base_url == "https://api.weather.gov"
    assert env_settings.nws_budget_s == 20


def test_the_worker_switch_defaults_on(env_settings):
    assert env_settings.research_worker_enabled is True


def test_the_key_switch_is_is_file_not_exists(monkeypatch, tmp_path):
    """A missing bind source is materialised by Compose as an empty *directory*, so `exists()`
    would be True with no key behind it and the worker would raise instead of going dormant."""
    key_file = tmp_path / "odds_api_key"
    key_file.write_text("test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    monkeypatch.setenv("ODDS_API_KEY_FILE", str(key_file))

    as_directory = tmp_path / "anthropic_dir"
    as_directory.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY_FILE", str(as_directory))
    assert Settings().has_anthropic_key() is False

    as_file = tmp_path / "anthropic_api_key"
    as_file.write_text("sk-ant-not-a-real-key\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY_FILE", str(as_file))
    settings = Settings()
    assert settings.has_anthropic_key() is True
    assert settings.anthropic_api_key() == "sk-ant-not-a-real-key"


def test_the_key_file_default_is_the_container_mount(env_settings):
    assert env_settings.anthropic_api_key_file == Path("/run/secrets/anthropic_api_key")
    assert env_settings.has_anthropic_key() is False   # not present on the Mac or in CI


def test_the_anthropic_sdk_is_importable_and_pinned():
    """Conformance item 2: exactly one new dependency, pinned by exact version."""
    import tomllib
    from pathlib import Path as P

    import anthropic  # noqa: F401  - the import is the assertion

    root = P(__file__).resolve().parents[1]
    deps = tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
    assert any(d.startswith("anthropic") for d in deps)
    pins = [l for l in (root / "constraints.txt").read_text().splitlines()
            if l.lower().startswith("anthropic==")]
    assert len(pins) == 1
