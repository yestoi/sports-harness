from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
    #: Amendment 4 (2026-09-08): raised from 20 to 45. Every daytime pricing run on 2026-09-08
    #: exhausted 20 s after one to four of seven variants over 4,541 gaps, so the primary and
    #: the gate variant were scored on under half the ticks (journal 57).
    #: The fetch budget tick_budget_s = 100 is unchanged; this is a ceiling, not a grant. The
    #: pricing budget is additionally capped each tick so that fetch, normalization and pricing
    #: fit the tick cadence in force with a 10 s margin, never below 20 s
    #: (harness/recorder/tick.py: pricing_budget).
    price_budget_s: int = 45
    dashboard_token_file: Path = Path("/run/secrets/dashboard_token")
    build_sha: str = "dev"
    build_time: str | None = None
    odds_monthly_credits: int = 5_000_000  # U1 2026-09-07: Odds API tier upgrade (was 100_000)
    # alternates cadence inside 180 min of kickoff (U1 value); Task 3b wires this into
    # alternates_due, replacing the old flat odds_alternates_interval_s setting.
    odds_alt_interval_near_s: int = 120
    # alternates cadence from 180 min out to odds_alt_window_h of kickoff (U1 value); beyond
    # odds_alt_window_h, alternates are not fetched at all.
    odds_alt_interval_far_s: int = 120
    odds_alt_window_h: int = 36  # U1 value: alternates stop being fetched beyond this many hours to kickoff

    # --- phase 3: paper executor -------------------------------------------------------
    exec_period_s: int = 15
    #: Variants the executor places paper orders for (D10). "sharp_two_sided" only starts
    #: producing intents once pre-registration amendment 3 registers it (U2).
    exec_variants: list[str] = Field(
        default_factory=lambda: ["sharp_direct", "constrained", "sharp_two_sided"])
    #: Cancel a resting order when the venue's own quote has moved this far against it.
    exec_cancel_venue_move_pts: Decimal = Decimal("0.02")
    #: Reprice a resting order when fair value has moved at least this far.
    exec_reprice_fair_move_pts: Decimal = Decimal("0.01")
    exec_kickoff_cutoff_min: int = 10  # R8: stop placing this close to kickoff
    exec_max_open_orders: int = 150  # D10: shared across the executed variants
    exec_intent_ttl_s: int = 900
    exec_book_max_age_s: int = 120

    # --- phase 3: settlement, benchmarks, storage ---------------------------------------
    settle_period_s: int = 3600
    settle_budget_s: int = 600
    gap_outcomes_batch: int = 50_000
    #: How often the `report_wtd` stage rebuilds the week-to-date tables (final review I6).
    #: Six hours, not the hourly cadence the design spec's §3.7 named: two of the ten tables
    #: materialise a week of rows in Python (`tables._T4_SNAPSHOTS` loads every
    #: `market_gap_snapshots` row of the week; `_T5_SNAPSHOTS` parses every WebSocket snapshot
    #: body for the week's moved tickers into a `BookState`), the NAS has about 1 GB of RAM to
    #: spare, and the stage shares its hourly slot with `settle`. Nothing downstream needs the
    #: provisional tables fresher than that; raise it to 3600 for a debugging pass.
    report_wtd_period_s: int = 21_600
    db_budget_gb: int = 2000  # D9: the 2 TB ceiling from U3; the dashboard turns red at 80 %
    #: U5/D1: the one variant the phase gate is judged on. The Task 4b deploy flips it to
    #: "sharp_two_sided"; falls back to the active primary when the named variant is unregistered.
    gate_variant: str = "sharp_direct"

    # --- Task 12b: telemetry --------------------------------------------------------------
    metric_sample_s: int = 60
    watch_sample_s: int = 60
    equity_sample_s: int = 300
    #: Where the Postgres data directory is bind-mounted read-only into app-run, for
    #: `os.statvfs` only (`host.disk_free_gb`). Absent on the Mac and in tests, where the
    #: metric is skipped rather than erroring (ruling 3).
    pg_data_mount: Path = Path("/pgdata-ro")

    # --- phase 4: backups -------------------------------------------------------------------
    #: Where the dump sidecar writes and the encrypt job reads. Bind-mounted into app-run
    #: read-write and into app-backup read-write; absent on the Mac, where the jobs no-op.
    backup_dir: Path = Path("/backups")
    #: The age recipient. A feature switched on `Path.is_file()`, not `exists()`: Compose
    #: materialises a missing bind source as an empty *directory*, so `exists()` would be True
    #: with no key behind it and the encrypt job would error instead of recording
    #: `skipped: no recipient` (I6). With no public key the job leaves the plaintexts alone.
    backup_recipient_file: Path = Path("/run/backup_age.pub")
    #: The private identity, on the Mac only and never pushed to the NAS.
    backup_identity_file: Path = Path("secrets/backup_age_key")
    backup_encrypt_period_s: int = 600
    backup_nightly_max_age_h: int = 26

    # --- phase 4: venue environment and posture -------------------------------------------
    #: Which Kalshi exchange the authenticated adapter targets. `prod` is read-only in this
    #: phase; `demo` is play money and is the only env in which writes can be enabled today.
    kalshi_env: str = "prod"
    kalshi_demo_base_url: str = "https://external-api.demo.kalshi.co/trade-api/v2"
    kalshi_demo_ws_url: str = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
    kalshi_demo_key_id_file: Path = Path("/run/secrets/kalshi_demo_key_id")
    kalshi_demo_private_key_file: Path = Path("/run/secrets/kalshi_demo_private_key.pem")
    legal_decision_file: Path = Path("secrets/legal_decision")
    #: The committed posture lines in deploy/nas.env become the values that control. Both are
    #: read by `make_writer` and by nothing else; flipping either is a gate (roadmap invariant 3).
    mode: str = Field(default="paper", validation_alias="HARNESS_MODE")
    live_trading: int = Field(default=0, validation_alias="LIVE_TRADING")

    def odds_api_key(self) -> str:
        return self.odds_api_key_file.read_text().strip()

    def kalshi_key_id(self) -> str:
        return self.kalshi_key_id_file.read_text().strip()

    def kalshi_private_key_pem(self) -> bytes:
        return self.kalshi_private_key_file.read_bytes()

    def has_kalshi_credentials(self) -> bool:
        """Both key paths, switched on `is_file()` rather than `exists()` (plan-review round 2,
        N1). Task 14 bind-mounts these two files into `app-run`; Compose materialises a missing
        bind source as an empty *directory*, so `exists()` would be True with no key behind it
        and `kalshi_key_id()` would raise `IsADirectoryError` at recorder startup instead of the
        caller quietly running without a reader. Same rule as `backup_recipient_file`."""
        return self.kalshi_key_id_file.is_file() and self.kalshi_private_key_file.is_file()

    def has_kalshi_demo_credentials(self) -> bool:
        # is_file(), not exists(): Compose creates an empty *directory* on the host for a
        # missing bind source, so exists() would be True with no credential behind it (C3).
        return (self.kalshi_demo_key_id_file.is_file()
                and self.kalshi_demo_private_key_file.is_file())

    def kalshi_demo_key_id(self) -> str:
        return self.kalshi_demo_key_id_file.read_text().strip()

    def kalshi_demo_private_key_pem(self) -> bytes:
        return self.kalshi_demo_private_key_file.read_bytes()

    def dashboard_token(self) -> str:
        return self.dashboard_token_file.read_text().strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
