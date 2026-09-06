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
    espn_base_url: str = "https://site.api.espn.com/apis/site/v2/sports/football"
    tick_budget_s: int = 100
    http_timeout_s: float = 10.0
    ladder_cap_per_tick: int = 400
    kalshi_sleep_s: float = 0.05
    heartbeat_s: int = 30
    tz_local: str = "America/Chicago"

    def odds_api_key(self) -> str:
        return self.odds_api_key_file.read_text().strip()

    def kalshi_key_id(self) -> str:
        return self.kalshi_key_id_file.read_text().strip()

    def kalshi_private_key_pem(self) -> bytes:
        return self.kalshi_private_key_file.read_bytes()

    def has_kalshi_credentials(self) -> bool:
        return self.kalshi_key_id_file.exists() and self.kalshi_private_key_file.exists()


@lru_cache
def get_settings() -> Settings:
    return Settings()
