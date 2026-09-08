from decimal import Decimal
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
    assert s.odds_alt_interval_near_s == 120  # U1 value (F11)
    assert s.odds_alt_interval_far_s == 120  # U1 value (F11)
    assert s.odds_alt_window_h == 36  # U1 value (F11)


def test_odds_alternates_interval_s_setting_is_gone(tmp_path: Path, monkeypatch):
    # Task 3b: alternates_due is rewired to the near/far/window settings, so the flat
    # odds_alternates_interval_s setting (and every reader of it) is removed.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    s = Settings()
    assert not hasattr(s, "odds_alternates_interval_s")


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


def test_settings_phase3_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    s = Settings()
    assert s.exec_period_s == 15
    assert s.exec_variants == ["sharp_direct", "constrained", "sharp_two_sided"]  # D10
    assert s.exec_cancel_venue_move_pts == Decimal("0.02")
    assert s.exec_reprice_fair_move_pts == Decimal("0.01")
    assert s.exec_kickoff_cutoff_min == 10
    assert s.exec_max_open_orders == 150
    assert s.exec_intent_ttl_s == 900
    assert s.exec_book_max_age_s == 120
    assert s.settle_period_s == 3600
    assert s.settle_budget_s == 600
    assert s.gap_outcomes_batch == 50_000
    assert s.report_wtd_period_s == 21_600  # final review I6: six hours, not one
    assert s.db_budget_gb == 2000  # D9: 2 TB ceiling from U3
    assert s.gate_variant == "sharp_direct"  # U5/D1; the Task 4b deploy flips it


def test_price_budget_is_45_inside_the_unchanged_tick_budget(env_settings):
    # Amendment 4 (2026-09-08): raised from 20 s, which every daytime pricing run exhausted.
    # This is the ceiling. What a tick may actually spend is capped to the cadence in force by
    # `harness.recorder.tick.pricing_budget`, covered in tests/test_tick.py.
    assert env_settings.price_budget_s == 45
    assert env_settings.tick_budget_s == 100
    assert env_settings.price_budget_s < env_settings.tick_budget_s


def test_posture_defaults(env_settings):
    assert env_settings.mode == "paper"
    assert env_settings.live_trading == 0
    assert env_settings.kalshi_env == "prod"


def test_posture_binds_the_deploy_env_names(monkeypatch):
    monkeypatch.setenv("HARNESS_MODE", "live")
    monkeypatch.setenv("LIVE_TRADING", "1")
    s = Settings(database_url="postgresql+psycopg://x/y")
    assert s.mode == "live" and s.live_trading == 1


def test_demo_hosts_are_pinned_to_the_roadmap_strings(env_settings):
    assert env_settings.kalshi_demo_base_url == \
        "https://external-api.demo.kalshi.co/trade-api/v2"
    assert env_settings.kalshi_demo_ws_url == \
        "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
