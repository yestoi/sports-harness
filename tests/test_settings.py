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
