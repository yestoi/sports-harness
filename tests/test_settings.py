from pathlib import Path

from harness.config.settings import Settings


def test_settings_reads_key_from_file(tmp_path: Path, monkeypatch):
    key_file = tmp_path / "odds_api_key"
    key_file.write_text("abc123\n")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    monkeypatch.setenv("ODDS_API_KEY_FILE", str(key_file))
    s = Settings()
    assert s.odds_api_key() == "abc123"
    assert s.tick_budget_s == 100
    assert s.odds_api_bookmakers.startswith("pinnacle")
    assert s.kalshi_base_url == "https://api.elections.kalshi.com/trade-api/v2"
    assert s.odds_monthly_credits == 5_000_000  # U1 2026-09-07
    assert s.odds_alternates_interval_s == 120  # U1 2026-09-07
    assert s.odds_alt_interval_near_s == 120  # U1 value (F11)
    assert s.odds_alt_interval_far_s == 120  # U1 value (F11)
    assert s.odds_alt_window_h == 36  # U1 value (F11)


def test_settings_build_stamp_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    monkeypatch.setenv("BUILD_SHA", "abc1234")
    monkeypatch.setenv("BUILD_TIME", "2026-09-07T06:00:00Z")
    s = Settings()
    assert s.build_sha == "abc1234"
    assert s.build_time == "2026-09-07T06:00:00Z"


def test_settings_build_stamp_defaults_when_unset(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    monkeypatch.delenv("BUILD_SHA", raising=False)
    monkeypatch.delenv("BUILD_TIME", raising=False)
    s = Settings()
    assert s.build_sha == "dev"
    assert s.build_time is None
