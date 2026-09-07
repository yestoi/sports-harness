from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # running|ok|error|skipped
    error: Mapped[str | None] = mapped_column(Text)
    n_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    credits_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    odds_remaining: Mapped[int | None] = mapped_column(Integer)
    budget_exhausted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class RawResponse(Base):
    """Partitioned by range on fetched_at (weekly). Composite PK required by Postgres partitioning."""
    __tablename__ = "raw_responses"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # odds_api|kalshi|espn
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[dict | list | None] = mapped_column(JSONB)
    __table_args__ = {"postgresql_partition_by": "RANGE (fetched_at)"}


class TradeWatermark(Base):
    __tablename__ = "trade_watermarks"
    ticker: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_volume_fp: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)


class SourceState(Base):
    __tablename__ = "source_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. odds_featured:americanfootball_nfl
    last_fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Team(Base):
    __tablename__ = "teams"
    sport: Mapped[str] = mapped_column(String(8), primary_key=True)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # ESPN team id, unique only within a sport
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    location: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    abbreviation: Mapped[str] = mapped_column(String(16), nullable=False)
    short_display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    popularity_tier: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TeamAlias(Base):
    __tablename__ = "team_aliases"
    sport: Mapped[str] = mapped_column(String(8), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    raw_name: Mapped[str] = mapped_column(String(160), primary_key=True)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (Index("ix_alias_sport_team", "sport", "team_id"),)


class Game(Base):
    __tablename__ = "games"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sport: Mapped[str] = mapped_column(String(8), nullable=False)
    home_team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    away_team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    odds_api_event_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    espn_event_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="scheduled", nullable=False)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (Index("ix_games_sport_kick", "sport", "kickoff_utc"),)


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    book: Mapped[str] = mapped_column(String(32), nullable=False)
    game_id: Mapped[int | None] = mapped_column(Integer)
    market_type: Mapped[str] = mapped_column(String(24), nullable=False)
    outcome_team_id: Mapped[int | None] = mapped_column(Integer)
    outcome_side: Mapped[str | None] = mapped_column(String(8))
    point: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    price_decimal: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    book_last_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index("ix_odds_game_type_fetched", "game_id", "market_type", "fetched_at"),)


class VenueMarket(Base):
    __tablename__ = "venue_markets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venue: Mapped[str] = mapped_column(String(16), nullable=False)
    ticker: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    event_ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    series_ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    game_id: Mapped[int | None] = mapped_column(Integer, index=True)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    side_team_id: Mapped[int | None] = mapped_column(Integer)
    side: Mapped[str | None] = mapped_column(String(8))
    kalshi_team_uuid: Mapped[str | None] = mapped_column(String(64))
    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    match_confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=0, nullable=False)
    match_status: Mapped[str] = mapped_column(String(16), default="unmatched", nullable=False)
    match_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    first_seen_raw_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VenueQuote(Base):
    __tablename__ = "venue_quotes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    venue_market_id: Mapped[int] = mapped_column(Integer, nullable=False)
    yes_bid: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    yes_ask: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    no_bid: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    no_ask: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    yes_bid_size: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    yes_ask_size: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    volume_24h: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    updated_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("raw_id", "venue_market_id", name="uq_quote_raw_market"),
                      Index("ix_quotes_market_fetched", "venue_market_id", "fetched_at"))


class OrderbookSnapshot(Base):
    __tablename__ = "orderbook_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    venue_market_id: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    yes_bids: Mapped[list] = mapped_column(JSONB, nullable=False)
    no_bids: Mapped[list] = mapped_column(JSONB, nullable=False)
    __table_args__ = (Index("ix_ob_market_fetched", "venue_market_id", "fetched_at"),)


class VenueTrade(Base):
    __tablename__ = "venue_trades"
    venue: Mapped[str] = mapped_column(String(16), primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    yes_price: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    count: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    taker_side: Mapped[str] = mapped_column(String(4), nullable=False)  # canonical: taker_outcome_side or the deprecated taker_side
    taker_outcome_side: Mapped[str | None] = mapped_column(String(4))  # yes|no
    taker_book_side: Mapped[str | None] = mapped_column(String(4))  # bid|ask, quoted from the YES leg
    is_block: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(4), nullable=False)
    raw_id: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (Index("ix_trades_ticker_ts", "ticker", "ts"),)


class OrderbookEvent(Base):
    __tablename__ = "orderbook_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sid: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    side: Mapped[str | None] = mapped_column(String(4))
    price: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    delta: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    __table_args__ = (Index("ix_obe_ticker_ts", "ticker", "ts"),)


class NormalizeState(Base):
    __tablename__ = "normalize_state"
    family: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_raw_id: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


class FairValue(Base):
    __tablename__ = "fair_values"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    game_id: Mapped[int] = mapped_column(Integer, nullable=False)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome_team_id: Mapped[int | None] = mapped_column(Integer)
    outcome_side: Mapped[str | None] = mapped_column(String(8))
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    fair_p: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    fair_source: Mapped[str] = mapped_column(String(8), nullable=False)
    n_groups: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disagreement: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    newest_book_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    staleness_s: Mapped[int | None] = mapped_column(Integer)
    #: "featured" or "alternate" -- which Odds API feed produced the group-member line that
    #: set `newest_book_ts` (spec F11). NULL when no sharp book had a `last_update` to key off.
    feed_kind: Mapped[str | None] = mapped_column(String(9))
    #: Seconds between `now` and that line's `fetched_at` -- how old the winning feed poll was.
    feed_lag_s: Mapped[int | None] = mapped_column(Integer)
    #: The staleness budget `not_stale` compares against for this row, from `stale_allowance_s()`.
    stale_allowance_s: Mapped[int | None] = mapped_column(Integer)
    #: harness.pricing.PRICING_VERSION at the time this row was computed.
    pricing_version: Mapped[str | None] = mapped_column(String(16))
    model_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index("ix_fair_game_type_created", "game_id", "market_type", "created_at"),)


class MarketGapSnapshot(Base):
    __tablename__ = "market_gap_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    venue_market_id: Mapped[int] = mapped_column(Integer, nullable=False)
    fair_value_id: Mapped[int | None] = mapped_column(BigInteger)
    fair_source: Mapped[str | None] = mapped_column(String(8))
    fair_p: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    #: Why this market has no fair value, when it doesn't: "unmapped_market_type" (the venue
    #: market's shape isn't moneyline/spread/total), "no_sharp_line" (no Pinnacle-backed line
    #: to price it from), or "pricing_error" (the game's whole fair computation raised and
    #: rolled back). NULL whenever fair_p is populated. Spec §9.6.
    no_fair_reason: Mapped[str | None] = mapped_column(String(32))
    prev_fair_p: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    prev_fair_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    venue_mid: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    best_bid: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    best_ask: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    bid_size: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    ask_size: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    n_groups: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disagreement: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    staleness_s: Mapped[int | None] = mapped_column(Integer)
    #: Copied from the fair value this snapshot is keyed to (spec F11); NULL when there is none.
    feed_kind: Mapped[str | None] = mapped_column(String(9))
    feed_lag_s: Mapped[int | None] = mapped_column(Integer)
    stale_allowance_s: Mapped[int | None] = mapped_column(Integer)
    gap_mid: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    gap_taker_net: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    gap_maker_net: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    ttk_minutes: Mapped[int | None] = mapped_column(Integer)
    dow: Mapped[int] = mapped_column(Integer, nullable=False)
    hour_ct: Mapped[int] = mapped_column(Integer, nullable=False)
    price_bucket: Mapped[int | None] = mapped_column(Integer)
    volume_24h: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    soft_minus_sharp: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    home_popularity_tier: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    away_popularity_tier: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("run_id", "venue_market_id", name="uq_gap_run_market"),
                      Index("ix_gap_market_created", "venue_market_id", "created_at"))


class StrategyVariant(Base):
    __tablename__ = "strategy_variants"
    variant_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    variant_id: Mapped[str] = mapped_column(String(12), nullable=False)
    gap_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    venue_market_id: Mapped[int] = mapped_column(Integer, nullable=False)
    side: Mapped[str] = mapped_column(String(4), default="yes", nullable=False)
    fair_p: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    fair_source: Mapped[str | None] = mapped_column(String(8))
    venue_best_bid: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    venue_best_ask: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    price_target: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    fee_at_target: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    as_estimate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    edge: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    edge_min: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    stake: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    contracts: Mapped[int | None] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(12), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(48))
    labels: Mapped[dict] = mapped_column(JSONB, nullable=False)
    replay: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("run_id", "variant_id", "venue_market_id", "side", "replay", name="uq_signal_key"),
                      Index("ix_signal_variant_created", "variant_id", "created_at"),
                      Index("ix_signal_market_created", "venue_market_id", "created_at"))


class KillSwitch(Base):
    __tablename__ = "kill_switch"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConfigHistory(Base):
    __tablename__ = "config_history"
    config_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
