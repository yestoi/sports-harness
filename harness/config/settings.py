from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    database_url_test: str | None = None
    odds_api_key_file: Path = Path("/run/secrets/odds_api_key")
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"
    odds_api_bookmakers: str = "pinnacle,betonlineag,lowvig,draftkings,fanduel,novig,kalshi"
    kalshi_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    kalshi_key_id_file: Path = Path("/run/secrets/kalshi_key_id")
    kalshi_private_key_file: Path = Path("/run/secrets/kalshi_private_key.pem")
    kalshi_ws_url: str = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    ws_max_tickers: int = 500
    ws_lookahead_hours: int = 72  # F8: phase 3 places paper orders days out, so tape the book that early
    ws_lookback_hours: int = 8  # F8: post-kickoff prints still feed settlement and markouts
    ws_stale_s: int = 180  # force a ws reconnect after this much silence on the socket
    espn_base_url: str = "https://site.api.espn.com/apis/site/v2/sports/football"
    tick_budget_s: int = 100
    http_timeout_s: float = 10.0
    ladder_cap_per_tick: int = 400
    kalshi_sleep_s: float = 0.05
    heartbeat_s: int = 30
    tz_local: str = "America/Chicago"
    variants_dir: Path | None = None
    price_budget_s: int = 20
    dashboard_token_file: Path = Path("/run/secrets/dashboard_token")
    build_sha: str = "dev"
    build_time: str | None = None
    odds_monthly_credits: int = 5_000_000  # U1 2026-09-07: Odds API tier upgrade (was 100_000)
    odds_alternates_interval_s: int = 120  # U1 2026-09-07: alternates cadence inside 36h of kickoff (was 900)
    odds_alt_interval_near_s: int = 120  # U1 value: alternates cadence inside odds_alt_window_h of kickoff
    odds_alt_interval_far_s: int = 120  # U1 value: alternates cadence beyond odds_alt_window_h of kickoff
    odds_alt_window_h: int = 36  # U1 value: the near/far cutoff for odds_alt_interval_*_s

    def odds_api_key(self) -> str:
        return self.odds_api_key_file.read_text().strip()

    def kalshi_key_id(self) -> str:
        return self.kalshi_key_id_file.read_text().strip()

    def kalshi_private_key_pem(self) -> bytes:
        return self.kalshi_private_key_file.read_bytes()

    def has_kalshi_credentials(self) -> bool:
        return self.kalshi_key_id_file.exists() and self.kalshi_private_key_file.exists()

    def dashboard_token(self) -> str:
        return self.dashboard_token_file.read_text().strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
