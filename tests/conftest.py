import itertools
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest


@pytest.fixture
def env_settings(monkeypatch, tmp_path):
    key_file = tmp_path / "odds_api_key"
    key_file.write_text("test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    monkeypatch.setenv("ODDS_API_KEY_FILE", str(key_file))
    from harness.config.settings import Settings

    return Settings()


@pytest.fixture(scope="session")
def _schema():
    """Build the test schema once per session. Dropping and recreating ~35 tables plus their
    indexes and views for every DB test cost more than the tests themselves; db_session
    truncates instead."""
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    from sqlalchemy.orm import sessionmaker

    from harness.db.engine import make_engine
    from harness.db.schema import create_schema, drop_schema, ensure_partitions

    engine = make_engine(url)
    drop_schema(engine)
    create_schema(engine)
    # ensure_partitions covers all three partitioned tables (raw_responses, orderbook_events,
    # venue_trades), so every db test can write to the tape without creating a partition first.
    # It builds this week's and next week's, i.e. rows dated inside [Monday, Monday + 14d).
    with sessionmaker(bind=engine)() as session:
        ensure_partitions(session, datetime.now(timezone.utc))
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(_schema):
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    from harness.db.models import Base

    with sessionmaker(bind=_schema)() as session:
        yield session
        session.rollback()
    tables = ", ".join(sorted(Base.metadata.tables))
    with _schema.begin() as conn:
        conn.execute(text(f"truncate {tables} restart identity cascade"))


# --- Task T10: the parlay builder's fixtures --------------------------------------------------
#
# `build_card`'s tests share one moment, `PARLAY_NOW`, and one sport, `ncaaf`, so the pool
# query's `g.sport = :sport and g.kickoff_utc > :now` clause always matches without each fixture
# having to restate it. `_next_id` hands out ids that are unique across a single test's fixtures
# (there is no foreign key in this schema to enforce it, but two rows sharing a `run_id` would
# trip `market_gap_snapshots`' own `uq_gap_run_market` constraint) -- `db_session`'s truncate
# resets identity between tests, so it never has to be unique across the whole run.

PARLAY_NOW = datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc)
_PARLAY_SPORT = "ncaaf"
_parlay_ids = itertools.count(9001)


def _next_id() -> int:
    return next(_parlay_ids)


def _make_team(session, abbr: str, sport: str = _PARLAY_SPORT):
    from harness.db.models import Team

    team_id = _next_id()
    team = Team(sport=sport, id=team_id, display_name=abbr, location=abbr, name=abbr,
               abbreviation=abbr, short_display_name=abbr)
    session.add(team)
    session.flush()
    return team


def _make_game(session, home_id: int, away_id: int, kickoff: datetime,
              sport: str = _PARLAY_SPORT):
    from harness.db.models import Game

    game = Game(sport=sport, home_team_id=home_id, away_team_id=away_id, kickoff_utc=kickoff)
    session.add(game)
    session.flush()
    return game


def _make_leg(session, *, game_id: int, market_type: str, side_team_id: int | None,
             side: str | None, threshold: Decimal | None, fair_p: Decimal, edge: Decimal,
             created_at: datetime, decision: str = "candidate", fair_source: str = "direct"):
    """One `venue_markets` + `fair_values` + `market_gap_snapshots` + `signals` row: one +EV
    pool candidate, keyed exactly the way `harness.parlay.build._POOL` reads it (`signals` ->
    `market_gap_snapshots` -> `fair_values`, never `signals.gap_snapshot_id` straight into
    `fair_values`)."""
    from harness.db.models import FairValue, MarketGapSnapshot, Signal, VenueMarket

    rid = _next_id()
    vm = VenueMarket(venue="kalshi", ticker=f"T-{rid}", event_ticker=f"E-{rid}",
                     series_ticker="S", game_id=game_id, market_type=market_type,
                     threshold=threshold, side_team_id=side_team_id, side=side,
                     first_seen_raw_id=rid, last_seen_at=created_at)
    session.add(vm)
    session.flush()
    fv = FairValue(run_id=rid, game_id=game_id, market_type=market_type,
                   outcome_team_id=side_team_id, outcome_side=side, threshold=threshold,
                   fair_p=fair_p, fair_source=fair_source, created_at=created_at)
    session.add(fv)
    session.flush()
    gs = MarketGapSnapshot(run_id=rid, venue_market_id=vm.id, fair_value_id=fv.id,
                           fair_source=fair_source, fair_p=fair_p, dow=created_at.weekday(),
                           hour_ct=12, created_at=created_at)
    session.add(gs)
    session.flush()
    sig = Signal(run_id=rid, variant_id="tiny", gap_snapshot_id=gs.id, venue_market_id=vm.id,
                side="yes", fair_p=fair_p, fair_source=fair_source, edge=edge,
                decision=decision, labels={}, created_at=created_at)
    session.add(sig)
    session.flush()
    return SimpleNamespace(venue_market=vm, fair_value=fv, gap_snapshot=gs, signal=sig)


def _make_dk_price(session, *, game_id: int, market_type: str, team_id: int | None,
                   side: str | None, price: Decimal, fetched_at: datetime,
                   book: str = "draftkings", point: Decimal | None = None):
    from harness.db.models import OddsSnapshot

    rid = _next_id()
    row = OddsSnapshot(raw_id=rid, run_id=rid, book=book, game_id=game_id,
                       market_type=market_type, outcome_team_id=team_id, outcome_side=side,
                       point=point, price_decimal=price, fetched_at=fetched_at)
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def seeded_dk_prices(db_session):
    """One game, one team, one fresh DraftKings moneyline price."""
    team = _make_team(db_session, "AAA")
    opp = _make_team(db_session, "AAB")
    game = _make_game(db_session, team.id, opp.id, PARLAY_NOW + timedelta(days=2))
    _make_dk_price(db_session, game_id=game.id, market_type="moneyline", team_id=team.id,
                   side=None, price=Decimal("2.00"), fetched_at=PARLAY_NOW - timedelta(minutes=5))
    return SimpleNamespace(game_id=game.id, team_id=team.id)


@pytest.fixture
def seeded_pinnacle_only(db_session):
    """The same shape as `seeded_dk_prices`, priced only at Pinnacle: no DraftKings row."""
    team = _make_team(db_session, "BAA")
    opp = _make_team(db_session, "BAB")
    game = _make_game(db_session, team.id, opp.id, PARLAY_NOW + timedelta(days=2))
    _make_dk_price(db_session, game_id=game.id, market_type="moneyline", team_id=team.id,
                   side=None, price=Decimal("2.00"), fetched_at=PARLAY_NOW - timedelta(minutes=5),
                   book="pinnacle")
    return SimpleNamespace(game_id=game.id, team_id=team.id)


def _pool_leg(session, *, team_abbr: str, opp_abbr: str, market_type: str, edge: Decimal,
             kickoff: datetime, price: Decimal, fair_p: Decimal = Decimal("0.55"),
             created_at: datetime = PARLAY_NOW - timedelta(hours=1)):
    """One priced +EV leg on its own game, for a team named `team_abbr`."""
    team = _make_team(session, team_abbr)
    opp = _make_team(session, opp_abbr)
    game = _make_game(session, team.id, opp.id, kickoff)
    _make_leg(session, game_id=game.id, market_type=market_type, side_team_id=team.id,
             side=None, threshold=None, fair_p=fair_p, edge=edge, created_at=created_at)
    _make_dk_price(session, game_id=game.id, market_type=market_type, team_id=team.id,
                   side=None, price=price, fetched_at=PARLAY_NOW - timedelta(minutes=5))
    return game


@pytest.fixture
def seeded_pool(db_session):
    """An LSU-anchored moneyline plus three more +EV legs, each on its own game: exactly the
    smart card's 3-4 legs from different games, all fresh and all priced."""
    kickoff = PARLAY_NOW + timedelta(days=2)
    _pool_leg(db_session, team_abbr="LSU", opp_abbr="OPP0", market_type="moneyline",
             edge=Decimal("0.05"), kickoff=kickoff, price=Decimal("1.80"))
    for i in range(1, 4):
        _pool_leg(db_session, team_abbr=f"OPP{i}", opp_abbr=f"OTH{i}", market_type="moneyline",
                 edge=Decimal("0.04") - Decimal(i) * Decimal("0.005"), kickoff=kickoff,
                 price=Decimal("1.90"))
    return None


@pytest.fixture
def seeded_total_anchor_pool(db_session):
    """A priced LSU **total** leg -- LSU is the home team, and there is no LSU moneyline or
    spread anywhere in the pool -- plus three more +EV legs on their own games. `vm.side_team_id`
    is always NULL on a total row, so this fixture is what exercises the game's home/away teams
    as the anchor's own source of truth (review round 1, Important 1)."""
    kickoff = PARLAY_NOW + timedelta(days=2)
    lsu = _make_team(db_session, "LSU")
    opp0 = _make_team(db_session, "OPP0")
    game = _make_game(db_session, lsu.id, opp0.id, kickoff)
    _make_leg(db_session, game_id=game.id, market_type="total", side_team_id=None, side="over",
             threshold=Decimal("55.5"), fair_p=Decimal("0.55"), edge=Decimal("0.05"),
             created_at=PARLAY_NOW - timedelta(hours=1))
    _make_dk_price(db_session, game_id=game.id, market_type="total", team_id=None, side="over",
                   price=Decimal("1.91"), fetched_at=PARLAY_NOW - timedelta(minutes=5),
                   point=Decimal("55.5"))
    for i in range(1, 4):
        _pool_leg(db_session, team_abbr=f"OPP{i}", opp_abbr=f"OTH{i}", market_type="moneyline",
                 edge=Decimal("0.04") - Decimal(i) * Decimal("0.005"), kickoff=kickoff,
                 price=Decimal("1.90"))
    return None


@pytest.fixture
def seeded_pool_no_anchor(db_session):
    """+EV legs with no LSU or Saints outcome anywhere in the pool: `build_card` must raise
    before it ever gets to counting legs."""
    kickoff = PARLAY_NOW + timedelta(days=2)
    _pool_leg(db_session, team_abbr="OPP1", opp_abbr="OTH1", market_type="moneyline",
             edge=Decimal("0.04"), kickoff=kickoff, price=Decimal("1.90"))
    return None


@pytest.fixture
def seeded_big_pool(db_session):
    """An LSU anchor plus six more +EV legs, each on its own game: the lottery card's 6-8 legs,
    uncorrelated."""
    kickoff = PARLAY_NOW + timedelta(days=2)
    _pool_leg(db_session, team_abbr="LSU", opp_abbr="OPP0", market_type="moneyline",
             edge=Decimal("0.06"), kickoff=kickoff, price=Decimal("1.80"))
    for i in range(1, 7):
        _pool_leg(db_session, team_abbr=f"OPP{i}", opp_abbr=f"OTH{i}", market_type="moneyline",
                 edge=Decimal("0.03"), kickoff=kickoff, price=Decimal("1.90"))
    return None


@pytest.fixture
def seeded_correlated_pool(db_session):
    """An LSU anchor plus a second LSU leg on the *same* game (a different market, so it is a
    distinct pool row), plus five more +EV legs on their own games: a lottery card that has to
    take both LSU legs to reach six, and has to say so."""
    kickoff = PARLAY_NOW + timedelta(days=2)
    game = _pool_leg(db_session, team_abbr="LSU", opp_abbr="OPP0", market_type="moneyline",
                     edge=Decimal("0.06"), kickoff=kickoff, price=Decimal("1.80"))
    from harness.db.models import Team

    lsu_team = db_session.query(Team).filter_by(sport=_PARLAY_SPORT, abbreviation="LSU").one()
    _make_leg(db_session, game_id=game.id, market_type="spread", side_team_id=lsu_team.id,
             side=None, threshold=Decimal("-7.0"), fair_p=Decimal("0.58"), edge=Decimal("0.05"),
             created_at=PARLAY_NOW - timedelta(hours=1))
    _make_dk_price(db_session, game_id=game.id, market_type="spread", team_id=lsu_team.id,
                   side=None, price=Decimal("1.91"), fetched_at=PARLAY_NOW - timedelta(minutes=5),
                   point=Decimal("-7.0"))
    for i in range(1, 6):
        _pool_leg(db_session, team_abbr=f"OPP{i}", opp_abbr=f"OTH{i}", market_type="moneyline",
                 edge=Decimal("0.03"), kickoff=kickoff, price=Decimal("1.90"))
    return None


@pytest.fixture
def seeded_stale_pool(db_session):
    """One LSU leg whose signal is seven hours old: outside the six-hour pool window, so
    `build_card` sees an empty pool and raises."""
    team = _make_team(db_session, "LSU")
    opp = _make_team(db_session, "OPP0")
    game = _make_game(db_session, team.id, opp.id, PARLAY_NOW + timedelta(days=2))
    _make_leg(db_session, game_id=game.id, market_type="moneyline", side_team_id=team.id,
             side=None, threshold=None, fair_p=Decimal("0.60"), edge=Decimal("0.05"),
             created_at=PARLAY_NOW - timedelta(hours=7))
    _make_dk_price(db_session, game_id=game.id, market_type="moneyline", team_id=team.id,
                   side=None, price=Decimal("1.80"), fetched_at=PARLAY_NOW - timedelta(minutes=5))
    return None


@pytest.fixture
def built_card(db_session, env_settings, seeded_pool):
    """One already-built smart card (with no key, so its own rationale is a template), for the
    rationale tests to run `write_rationale` against a second time with a different client."""
    from harness.db.models import ParlayLeg
    from harness.parlay.build import build_card

    card = build_card(db_session, env_settings, sport=_PARLAY_SPORT, week=38, kind="smart",
                      now=PARLAY_NOW)
    legs = db_session.query(ParlayLeg).filter_by(card_id=card.id).order_by(ParlayLeg.seq).all()
    return SimpleNamespace(card=card, legs=legs)


@pytest.fixture
def keyed_settings(env_settings, tmp_path):
    key = tmp_path / "anthropic_api_key"
    key.write_text("sk-ant-not-a-real-key")
    return env_settings.model_copy(update={"anthropic_api_key_file": key})


class _FakeResearchClient:
    """The same `call(...)` signature as `ResearchClient.call`, returning a `CallResult` built
    by hand rather than by a live request."""

    def __init__(self, text_out: str | None = None, error: str | None = None):
        self.calls: list[dict] = []
        self.text_out = text_out
        self.error = error

    def call(self, *, model, system, user, schema, effort, max_output_tokens=None, tools=(),
             thinking=None):
        from harness.research.client import CallResult
        from harness.research.spend import Usage

        self.calls.append({"model": model, "system": system, "user": user, "schema": schema,
                           "effort": effort, "max_output_tokens": max_output_tokens,
                           "tools": tools})
        usage = Usage(input_tokens=500, output_tokens=80, cache_read_tokens=0,
                     cache_write_tokens=0, searches=0)
        if self.error is not None:
            return CallResult(model=model, output=None, usage=usage, stop_reason="end_turn",
                              request_id="req_fake", latency_ms=5, tool_calls=[],
                              snippets={"items": [], "truncated": False}, error=self.error)
        return CallResult(model=model, output={"text": self.text_out}, usage=usage,
                          stop_reason="end_turn", request_id="req_fake", latency_ms=5,
                          tool_calls=[], snippets={"items": [], "truncated": False}, error=None)


@pytest.fixture
def fake_client():
    return _FakeResearchClient(text_out="LSU and the Saints on the same slip. Let us cook.")


@pytest.fixture
def erroring_client():
    return _FakeResearchClient(error="schema")


@pytest.fixture
def wordy_client():
    return _FakeResearchClient(text_out=("LSU's defense " * 60).strip())


class _LockCheckingClient:
    """The same `call(...)` signature as `ResearchClient.call`, except that the call itself, on
    a fresh connection, tries to take the exact ISO-week advisory lock `reserve_spend` used for
    this card's reservation. If `write_rationale` is still holding it (the pre-fix behaviour --
    the reservation and the call sharing one uncommitted transaction), the `pg_try_advisory_xact_lock`
    below returns false; once the reservation commits before the call is made, it is free and
    this returns true (review round 1, Important 2)."""

    def __init__(self, engine, monday, text_out: str):
        self.engine = engine
        self.monday = monday
        self.text_out = text_out
        self.calls: list[dict] = []
        self.lock_was_free: bool | None = None

    def call(self, *, model, system, user, schema, effort, max_output_tokens=None, tools=(),
             thinking=None):
        from sqlalchemy import text as sql_text

        from harness.research.client import CallResult
        from harness.research.spend import Usage

        with self.engine.connect() as conn:
            self.lock_was_free = bool(conn.execute(sql_text(
                "select pg_try_advisory_xact_lock(hashtext('research_spend:week:' || :monday))"),
                {"monday": self.monday}).scalar())
            conn.rollback()      # release the probe's own hold immediately either way
        self.calls.append({"model": model, "system": system, "user": user, "schema": schema,
                           "effort": effort, "max_output_tokens": max_output_tokens,
                           "tools": tools})
        usage = Usage(input_tokens=500, output_tokens=80, cache_read_tokens=0,
                     cache_write_tokens=0, searches=0)
        return CallResult(model=model, output={"text": self.text_out}, usage=usage,
                          stop_reason="end_turn", request_id="req_fake", latency_ms=5,
                          tool_calls=[], snippets={"items": [], "truncated": False}, error=None)


@pytest.fixture
def lock_checking_client(db_session):
    from harness.research.spend import chicago_day, iso_week_bounds

    monday, _sunday = iso_week_bounds(chicago_day(PARLAY_NOW))
    return _LockCheckingClient(db_session.get_bind(), monday,
                               "LSU and the Saints on the same slip. Let us cook.")
