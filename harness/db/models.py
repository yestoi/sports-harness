import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Index, Integer, Numeric, SmallInteger, String, Text,
    UniqueConstraint, Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Contract quantities are decimal because Kalshi quotes fractional sizes on some series
#: (addendum §5: "every contract quantity Numeric(14,2)").
CONTRACTS = Numeric(14, 2)
#: Probabilities and probability deltas, in the 0..1 space the harness prices in.
PROB = Numeric(6, 4)


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
    #: The image the run executed under (Settings.build_sha); D11's backstop for a missed
    #: EXECUTOR_VERSION/PRICING_VERSION bump.
    build_sha: Mapped[str | None] = mapped_column(String(24))


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
    #: Kalshi market metadata the executor needs and the phase-2 recorder did not keep.
    expected_expiration_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price_level_structure: Mapped[str | None] = mapped_column(String(48))
    price_ranges: Mapped[list | dict | None] = mapped_column(JSONB)
    #: Per-market fee shape (F45/R21); `fee_model_for` turns the pair into a FeeModel.
    fee_type: Mapped[str | None] = mapped_column(String(32))
    fee_multiplier: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    exchange_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: The matched shape as one string, so an order can be re-joined to its benchmark without
    #: re-matching: f"{game_id}:{market_type}:{side_team_id}:{side}:{threshold}" with NULLs empty.
    match_key: Mapped[str | None] = mapped_column(String(64))


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
    """Partitioned by range on ts (weekly), like the rest of the bulk tape (F19).

    Postgres requires every unique key of a partitioned table to contain the partition key, so
    `ts` joins the primary key. That weakens the key as a deduplicator -- the same print reaching
    us from the WebSocket and from REST one millisecond apart would be two rows -- so both writers
    truncate `ts` to milliseconds and the REST writer skips a `(venue, trade_id)` already on the
    tape, through `ix_trades_venue_trade_id`.
    """
    __tablename__ = "venue_trades"
    venue: Mapped[str] = mapped_column(String(16), primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    yes_price: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    count: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    taker_side: Mapped[str] = mapped_column(String(4), nullable=False)  # canonical: taker_outcome_side or the deprecated taker_side
    taker_outcome_side: Mapped[str | None] = mapped_column(String(4))  # yes|no
    taker_book_side: Mapped[str | None] = mapped_column(String(4))  # bid|ask, quoted from the YES leg
    is_block: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(4), nullable=False)
    raw_id: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (Index("ix_trades_ticker_ts", "ticker", "ts"),
                      {"postgresql_partition_by": "RANGE (ts)"})


class OrderbookEvent(Base):
    """Partitioned by range on ts (weekly): the largest table in the harness by two orders of
    magnitude (up to 4M rows an hour at peak), so a season's tape only fits the NAS as partitions
    that can be archived and dropped week by week (F19). `ts` joins the primary key because
    Postgres requires the partition key in every unique key."""
    __tablename__ = "orderbook_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    sid: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    side: Mapped[str | None] = mapped_column(String(4))
    price: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    delta: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    __table_args__ = (Index("ix_obe_ticker_ts", "ticker", "ts"),
                      {"postgresql_partition_by": "RANGE (ts)"})


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
    #: D12: the realised adverse-selection estimate for this signal's bucket (sport x 5c price
    #: bucket x side, trailing 14 days, >= 50 fills). NULL until the bucket has enough fills.
    as_measured: Mapped[Decimal | None] = mapped_column(PROB)
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


# ---------------------------------------------------------------------------
# Phase 3: paper execution, settlement, benchmarks, CLV (addendum §5).
# The partial, functional and expression indexes these tables need are raw DDL in
# harness/db/schema.py; only plain model indexes live here.
# ---------------------------------------------------------------------------


class Intent(Base):
    """A signal the executor decided to act on, before any order exists. One row per decision,
    so a skipped intent still leaves a trail (order_events.kind = 'skipped')."""
    __tablename__ = "intents"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    signal_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    variant_id: Mapped[str] = mapped_column(String(12), nullable=False)
    venue: Mapped[str] = mapped_column(String(16), nullable=False)
    venue_market_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    target_prob: Mapped[Decimal | None] = mapped_column(PROB)
    target_contracts: Mapped[Decimal | None] = mapped_column(CONTRACTS)
    edge: Mapped[Decimal | None] = mapped_column(PROB)
    edge_min: Mapped[Decimal | None] = mapped_column(PROB)
    fair_p: Mapped[Decimal | None] = mapped_column(PROB)
    fair_row_id: Mapped[int | None] = mapped_column(BigInteger)
    game_id: Mapped[int | None] = mapped_column(Integer)
    kickoff_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stake: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    signal_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    replay: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Order(Base):
    """A paper order. Everything the fill simulator and the scorer need is frozen here at
    placement time, so a re-score never has to re-read the book (D3)."""
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    intent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    variant_id: Mapped[str] = mapped_column(String(12), nullable=False)
    venue: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), default="paper", nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    venue_market_id: Mapped[int] = mapped_column(Integer, nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    prob: Mapped[Decimal] = mapped_column(PROB, nullable=False)
    contracts: Mapped[Decimal] = mapped_column(CONTRACTS, nullable=False)
    #: open|filled|partially_filled|cancelled|expired|settled
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(32))

    # --- pricing context frozen at placement ---
    fair_p_at_place: Mapped[Decimal | None] = mapped_column(PROB)
    fair_row_id_at_place: Mapped[int | None] = mapped_column(BigInteger)
    fair_books_json: Mapped[dict | list | None] = mapped_column(JSONB)
    venue_bid_at_place: Mapped[Decimal | None] = mapped_column(PROB)
    venue_ask_at_place: Mapped[Decimal | None] = mapped_column(PROB)
    venue_mid_at_place: Mapped[Decimal | None] = mapped_column(PROB)
    #: Contracts resting ahead of this order at its price; NULL when no book was seen (D7).
    queue_ahead_at_place: Mapped[Decimal | None] = mapped_column(CONTRACTS)
    book_source: Mapped[str | None] = mapped_column(String(4))  # ws|rest|none
    book_age_s: Mapped[int | None] = mapped_column(Integer)
    book_first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edge_at_place: Mapped[Decimal | None] = mapped_column(PROB)
    edge_min_at_place: Mapped[Decimal | None] = mapped_column(PROB)
    as_at_place: Mapped[Decimal | None] = mapped_column(PROB)
    staleness_at_place: Mapped[int | None] = mapped_column(Integer)
    stale_allowance_at_place: Mapped[int | None] = mapped_column(Integer)
    feed_kind: Mapped[str | None] = mapped_column(String(9))
    config_hash: Mapped[str | None] = mapped_column(String(64))
    gap_snapshot_id: Mapped[int | None] = mapped_column(BigInteger)
    game_id: Mapped[int | None] = mapped_column(Integer)
    sport: Mapped[str | None] = mapped_column(String(8))
    kickoff_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    match_key: Mapped[str | None] = mapped_column(String(64))

    # --- fill simulation state, written back with the fills (D3) ---
    worst_case_fill: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fair_cross_fill: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    filled_contracts: Mapped[Decimal] = mapped_column(CONTRACTS, default=0, nullable=False)
    queue_remaining: Mapped[Decimal | None] = mapped_column(CONTRACTS)
    #: Contracts the tape has printed at this order's price since it was placed (a quantity, not
    #: a price): the queue simulator consumes queue_remaining out of it.
    traded_at_price: Mapped[Decimal | None] = mapped_column(CONTRACTS)
    #: R9: the last orderbook_events.id this order's simulation consumed, so a loop re-reads
    #: only the new tape.
    tape_cursor_event_id: Mapped[int | None] = mapped_column(BigInteger)
    #: The same quartet under the no_watcher counterfactual, so both fills can be scored from
    #: one tape pass without a second orders row.
    nw_filled_contracts: Mapped[Decimal] = mapped_column(CONTRACTS, default=0, nullable=False)
    nw_queue_remaining: Mapped[Decimal | None] = mapped_column(CONTRACTS)
    nw_traded_at_price: Mapped[Decimal | None] = mapped_column(CONTRACTS)
    nw_tape_cursor_event_id: Mapped[int | None] = mapped_column(BigInteger)
    nw_done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: The rest of `execution.fills.SimState`, per track (Task 4 review ruling). `crossed` makes
    #: the worst-case fill happen once even though the book keeps crossing on every later loop;
    #: the print watermark makes a print idempotent even though the executor keeps no print
    #: cursor and rescans from `placed_at - 60 s` every loop.
    crossed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_print_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_print_ids: Mapped[list | None] = mapped_column(JSONB)
    nw_crossed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    nw_last_print_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nw_last_print_ids: Mapped[list | None] = mapped_column(JSONB)
    #: Minutes this order's book spent dirty after a WS gap (D6), so an optimistic queue is visible.
    dirty_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: The same quantity in seconds, which is what the loop can actually accumulate: one dirty
    #: 15 s loop is a quarter of a minute, and adding that to an integer column would round to
    #: zero forever. `dirty_minutes` is derived from this in the same statement.
    dirty_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    replay: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: The venue's own order id, its cancel group, and the shard the order was sent on. NULL for
    #: paper: only KalshiGateway fills these, and it is dormant (§2.3, §7).
    venue_order_id: Mapped[str | None] = mapped_column(String(64))
    order_group_id: Mapped[str | None] = mapped_column(String(64))
    exchange_index_at_place: Mapped[int | None] = mapped_column(Integer)


class OrderEvent(Base):
    """Placement, cancellation and skip audit trail. A skip has no order, so order_id is NULL
    and uq_skip_once keys on the intent instead."""
    __tablename__ = "order_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    intent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: place|cancel|expire|skipped|cap_gate|settle
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    prob: Mapped[Decimal | None] = mapped_column(PROB)
    contracts: Mapped[Decimal | None] = mapped_column(CONTRACTS)
    fair_p_at_event: Mapped[Decimal | None] = mapped_column(PROB)
    reason: Mapped[str | None] = mapped_column(String(48))
    replay: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Fill(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    prob: Mapped[Decimal] = mapped_column(PROB, nullable=False)
    contracts: Mapped[Decimal] = mapped_column(CONTRACTS, nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    fee_type: Mapped[str | None] = mapped_column(String(32))
    fee_multiplier: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    maker_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: queue_model|snapshot_cross|no_watcher
    fill_method: Mapped[str] = mapped_column(String(16), nullable=False)
    source_trade_id: Mapped[str | None] = mapped_column(String(64))
    source_event_id: Mapped[int | None] = mapped_column(BigInteger)
    taker_side: Mapped[str | None] = mapped_column(String(4))
    #: True when the print traded through this order's price rather than at it.
    through: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tape_source: Mapped[str | None] = mapped_column(String(4))  # ws|rest
    has_print: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    replay: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Markout(Base):
    """Fair-value markout for one order at one anchor and horizon."""
    __tablename__ = "markouts"
    order_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: String(10): "cross_fill" (10 chars) is longer than the 8 this table shipped with (the
    #: same class of fix as `Benchmark.benchmark_type` -- fix the model, not an ALTER, since
    #: this table has never existed on the deployed database; Task 9).
    anchor: Mapped[str] = mapped_column(String(10), primary_key=True)
    horizon: Mapped[str] = mapped_column(String(6), primary_key=True)
    at_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    p_used: Mapped[Decimal | None] = mapped_column(PROB)
    fee_per_contract: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    fair_p: Mapped[Decimal | None] = mapped_column(PROB)
    fair_row_id: Mapped[int | None] = mapped_column(BigInteger)
    fair_book_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fair_changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fair_age_s: Mapped[int | None] = mapped_column(Integer)
    venue_mid: Mapped[Decimal | None] = mapped_column(PROB)
    mid_age_s: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(8))


class Settlement(Base):
    __tablename__ = "settlements"
    game_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VenueSettlement(Base):
    """The venue's own result for a market (R11), kept beside the score-derived settlement so a
    disagreement is visible instead of silently overwritten."""
    __tablename__ = "venue_settlements"
    venue: Mapped[str] = mapped_column(String(16), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    result: Mapped[str | None] = mapped_column(String(4))  # yes|no|void
    payout: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_id: Mapped[int | None] = mapped_column(BigInteger)


class Benchmark(Base):
    """A closing/kickoff/other reference probability for one market shape."""
    __tablename__ = "benchmarks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(Integer, nullable=False)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome_team_id: Mapped[int | None] = mapped_column(Integer)
    outcome_side: Mapped[str | None] = mapped_column(String(8))
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    #: String(32): "kalshi_last_trade_pre_kick" (26 chars) and "opening_first_seen" (18 chars)
    #: are both longer than 16 -- this table (and gap_outcomes, order_clv below) shipped with
    #: benchmark_type varchar(16), too narrow for BENCHMARK_TYPES' own longest values (Task 8
    #: fix; ruled: fix the model, not an ALTER, since none of the three tables has ever existed
    #: on the deployed database -- see test_benchmark_type_columns_fit_every_benchmark_type).
    benchmark_type: Mapped[str] = mapped_column(String(32), nullable=False)
    p: Mapped[Decimal | None] = mapped_column(PROB)
    target_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    kickoff_moved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GapOutcome(Base):
    """CLV of a gap snapshot against a benchmark: the counterfactual every observed market gets,
    whether or not a variant traded it."""
    __tablename__ = "gap_outcomes"
    gap_snapshot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: String(32): see the matching note on Benchmark.benchmark_type (Task 8 fix).
    benchmark_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    p_bench: Mapped[Decimal | None] = mapped_column(PROB)
    clv_mid_p: Mapped[Decimal | None] = mapped_column(PROB)
    clv_bid_p: Mapped[Decimal | None] = mapped_column(PROB)
    clv_target_p: Mapped[Decimal | None] = mapped_column(PROB)
    clv_target_p_net: Mapped[Decimal | None] = mapped_column(PROB)
    clv_target_roi_net: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    p_used_kind: Mapped[str | None] = mapped_column(String(8))


class OrderClv(Base):
    """CLV of a placed order against a benchmark (Task 8 fills it; the `clv` view reads it)."""
    __tablename__ = "order_clv"
    order_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: String(32): see the matching note on Benchmark.benchmark_type (Task 8 fix). The `clv`
    #: view (Task 2) reads this column, but `create_all` creates order_clv (and the view is a
    #: separate `CREATE OR REPLACE VIEW` statement) at the right width from the start -- no
    #: ALTER is needed since the table has never existed on the deployed database.
    benchmark_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    p_bench: Mapped[Decimal | None] = mapped_column(PROB)
    p_used: Mapped[Decimal | None] = mapped_column(PROB)
    p_used_kind: Mapped[str | None] = mapped_column(String(8))
    clv_p: Mapped[Decimal | None] = mapped_column(PROB)
    clv_p_net: Mapped[Decimal | None] = mapped_column(PROB)
    clv_roi_net: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Ledger(Base):
    """Cash movements: one row per fill and one per settlement, keyed so a re-run cannot double-post."""
    __tablename__ = "ledger"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    variant_id: Mapped[str] = mapped_column(String(12), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # fill|settlement
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    fill_id: Mapped[int | None] = mapped_column(BigInteger)
    ticker: Mapped[str | None] = mapped_column(String(64))
    side: Mapped[str | None] = mapped_column(String(4))
    contracts: Mapped[Decimal | None] = mapped_column(CONTRACTS)
    price: Mapped[Decimal | None] = mapped_column(PROB)
    fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    payout: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    cash_delta: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    replay: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ExecHeartbeat(Base):
    """Single row (id = 1) the dashboard's Layer 1 reads to tell a stalled executor from an idle one."""
    __tablename__ = "exec_heartbeat"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_loop_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    loops: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    open_orders: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_loop_ms: Mapped[int | None] = mapped_column(Integer)
    p95_loop_ms: Mapped[int | None] = mapped_column(Integer)
    loops_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    book_dirty_markets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ws_last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executor_version: Mapped[str | None] = mapped_column(String(16))


class GateReport(Base):
    """One row per exec variant per evaluation; `gate_variant` marks the single row the phase
    gate is judged on (U5/D1, Settings.gate_variant)."""
    __tablename__ = "gate_reports"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    variant_id: Mapped[str] = mapped_column(String(12), nullable=False)
    gate_variant: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    criteria_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    criteria_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)


class JobRun(Base):
    """A settler/benchmark/markout batch run; `runs` stays the recorder tick's table (D5)."""
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    budget_exhausted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class JobState(Base):
    """Resumable cursors for the batch jobs (e.g. the gap_outcomes backfill's last id)."""
    __tablename__ = "job_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# Task 12b: telemetry the dashboard cannot backfill (U6, spec
# `2026-09-07-dashboard-surfaces-design.md` §3). Every table here is additive and never read
# by a fill, a decision, a settlement or a heartbeat -- only by the dashboard, the weekly
# report and the operator. The five indexes an ordered-by-time query needs are raw DDL in
# harness/db/schema.py (`_INDEX_DDL`), the same convention every other non-trivial index in
# this file uses.
# ---------------------------------------------------------------------------


class MetricSample(Base):
    """One time series point. `source` is the writer (recorder, ws, exec, settle,
    housekeeping, serve); `name` is a dotted metric like `exec.loop_ms`; `labels` carries the
    metric's dimension (a reason, a feed_kind, a variant) when it has one."""
    __tablename__ = "metric_samples"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(48), nullable=False)
    labels: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    #: Nullable, not the brief's bare `Numeric(18,6)` (fix round 1, M2): `exec.p95_loop_ms`
    #: before any loop duration exists and `exec.ws_event_age_s` before any WS event ever has
    #: are both legitimate "nothing to report yet", always written so the verify.md query
    #: "every exec.* name younger than 5 minutes" never misreads the gap as a missing metric.
    #: This table has never existed in production, so this is a model change, not an ALTER.
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))


class OperatorEvent(Base):
    """One operator-visible event: a kill, a deploy, a settle error, a check failure, a note.
    `summary` always passes the F50 sanitizer (`harness.telemetry.sanitize_reason`) before
    insert, whatever kind of free text produced it."""
    __tablename__ = "operator_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    summary: Mapped[str] = mapped_column(String(200), nullable=False)
    ref: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class OrderWatchSample(Base):
    """One open order's queue and book state, sampled every `watch_sample_s`, plus one
    terminal row (`terminal` set) the step the order leaves the open set on."""
    __tablename__ = "order_watch_samples"
    order_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    queue_remaining: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    nw_queue_remaining: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    best_bid: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    best_ask: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    fair_p: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    book_dirty: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    terminal: Mapped[str | None] = mapped_column(String(12))


class EquitySnapshot(Base):
    """One exec variant's cash and mark-to-market, sampled every `equity_sample_s` by the
    executor and once more by the settler after its `settle` stage (`mtm_open` is NULL there:
    the settler has no live book to mark against)."""
    __tablename__ = "equity_snapshots"
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    variant_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    open_stake: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    mtm_open: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    #: Nullable, not the brief's bare `Numeric(5,4)` (fix round 1, I1, one convention): NULL
    #: when there is nothing to compute a coverage share over -- a variant with no open
    #: positions (the executor) or the settler's snapshot, which has no live book at all and so
    #: no coverage concept to report. This table has never existed in production, so this is a
    #: model change, not an ALTER.
    mtm_coverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    n_open_positions: Mapped[int] = mapped_column(Integer, nullable=False)
    n_open_orders: Mapped[int] = mapped_column(Integer, nullable=False)
    #: §3 risk gate: the trailing-7-day peak of `cash`, the drawdown against it, and whether
    #: the -20 % stop is tripped. Nullable: rows written before the gate shipped have none, and
    #: the settler's snapshot computes them the same way the executor does.
    peak_equity_7d: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    drawdown_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    drawdown_stop: Mapped[bool | None] = mapped_column(Boolean)


class GameScoreEvent(Base):
    """One change in a game's live score/clock/status, appended by `link_espn_scoreboard`
    whenever any of those fields differs from the game's newest row."""
    __tablename__ = "game_score_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: String(24), not the brief's String(12): this mirrors `Game.status` exactly, which is
    #: also String(24) precisely because an unrecognized ESPN status is kept raw and lowercased
    #: rather than dropped (`link_espn_scoreboard`'s `_STATUS.get(raw, raw.lower())`) -- e.g.
    #: "status_suspended" (17 chars) already exceeds 12 (see test_unknown_status_is_kept_raw_lowercased).
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    period: Mapped[int | None] = mapped_column(SmallInteger)
    clock: Mapped[str | None] = mapped_column(String(8))
    home_score: Mapped[int | None] = mapped_column(SmallInteger)
    away_score: Mapped[int | None] = mapped_column(SmallInteger)
    raw_id: Mapped[int | None] = mapped_column(BigInteger)


class CheckResult(Base):
    """One Layer 2b invariant's outcome for one housekeeping pass (`harness.ops.checks`)."""
    __tablename__ = "check_results"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_name: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)  # pass|fail|skip
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    threshold: Mapped[str | None] = mapped_column(String(48))
    detail: Mapped[str | None] = mapped_column(String(200))


class ReportRun(Base):
    """One rendering of the weekly report: `harness report` (`provisional = false`) or the
    hourly `report_wtd` settlement stage (`provisional = true`)."""
    __tablename__ = "report_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False)
    build_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    criteria_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hashes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    markdown: Mapped[str | None] = mapped_column(Text)
    markdown_sha256: Mapped[str | None] = mapped_column(String(64))


class ReportCell(Base):
    """One cell of one table of one report run, so the dashboard can read the report without
    parsing Markdown. `text` is the exact rendered string; `flags` carries `greyed`, `flagged`
    and `not_collected`, the same rules `render_markdown` already uses."""
    __tablename__ = "report_cells"
    report_run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    table_key: Mapped[str] = mapped_column(String(4), primary_key=True)
    row_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    col_key: Mapped[str] = mapped_column(String(48), primary_key=True)
    estimate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    n_obs: Mapped[int | None] = mapped_column(Integer)
    n_clusters: Mapped[int | None] = mapped_column(Integer)
    lo: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    hi: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    text: Mapped[str | None] = mapped_column(String(64))
    flags: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class VenueRequest(Base):
    """One authenticated venue call, one row per attempt (addendum §1.1). Never headers, never
    bodies (pre-loaded decision 7). The public read path is not recorded here: table 11 counts
    authenticated traffic, and the recorder's reads are already in `raw_responses` (D5)."""
    __tablename__ = "venue_requests"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    venue: Mapped[str] = mapped_column(String(16), nullable=False)
    env: Mapped[str] = mapped_column(String(8), nullable=False)          # prod|demo
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[int | None] = mapped_column(Integer)                  # NULL on a transport error
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)


class VenueStatus(Base):
    """The §9.4 outage state per (venue, env). The outage counter runs only for env = 'prod':
    a demo smoke's 401s never mark production (A-I3). `reason` is at most 120 characters of the
    venue's own body, ASCII-escaped with newlines stripped, and is untrusted text everywhere it
    is rendered."""
    __tablename__ = "venue_status"
    venue: Mapped[str] = mapped_column(String(16), primary_key=True)
    env: Mapped[str] = mapped_column(String(8), primary_key=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)      # ok|unavailable|frozen
    reason: Mapped[str | None] = mapped_column(String(120))
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BackupRun(Base):
    """One dump, encryption or drill (§4.3, §4.4). `rows_match` is the drill's comparison and
    is NULL for every other kind."""
    __tablename__ = "backup_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)        # nightly|weekly|partition|encrypt|drill
    #: The image build the row was written under. `delete_verified_plaintexts` keys the release
    #: rule on it, so it is a column, not a `notes` key. Same width as `runs.build_sha`.
    build_sha: Mapped[str | None] = mapped_column(String(24))
    path: Mapped[str | None] = mapped_column(String(256))
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    plaintext_sha256: Mapped[str | None] = mapped_column(String(64))
    ciphertext_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)      # ok|error|skipped
    rows_match: Mapped[bool | None] = mapped_column(Boolean)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[dict | None] = mapped_column(JSONB)
