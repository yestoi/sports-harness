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


# --- Task T11: `harness parlay placed` / `show` fixtures ---------------------------------------
#
# Each fixture seeds one `teams` row per side, one `games` row inside this ISO week, one
# `venue_markets` row per leg, one `parlay_cards` row and its `parlay_legs`, and one
# `odds_snapshots` row per leg with `book = 'draftkings'` -- exactly the shape `mark_placed`
# reads (`newest_dk_price` off `odds_snapshots`, the card and its legs off `parlay_cards` /
# `parlay_legs`). Every leg here is a spread, so `card_threshold` and `dk_point` line up with
# `ParlayLeg.threshold` and `odds_snapshots.point` respectively. `_PLACEMENT_PRICED_AT` is when
# the DK price fixtures are stamped `fetched_at` -- close to `test_parlay_placement.py`'s own
# `NOW` (2026-09-18 21:00 UTC) rather than tied to `built_at`, so `mark_placed`'s 30-minute D14
# freshness check sees a live row regardless of how old the card itself is.

_PLACEMENT_PRICED_AT = datetime(2026, 9, 18, 20, 55, tzinfo=timezone.utc)


def _one_leg_game(session, prefix: str):
    team = _make_team(session, f"{prefix}A")
    opp = _make_team(session, f"{prefix}B")
    game = _make_game(session, team.id, opp.id, PARLAY_NOW + timedelta(days=2))
    return team, game


def _make_placement_leg(session, *, game_id, team_id, threshold: Decimal, point: Decimal,
                        fetched_at: datetime, price: Decimal = Decimal("1.9100")):
    """One `venue_markets` row plus the `odds_snapshots` row `newest_dk_price` reads. `threshold`
    is what the card's own leg will store; `point` is what the newest DraftKings row carries --
    equal for an untouched line, different for a moved one."""
    from harness.db.models import VenueMarket

    rid = _next_id()
    vm = VenueMarket(venue="kalshi", ticker=f"PT-{rid}", event_ticker=f"PE-{rid}",
                     series_ticker="S", game_id=game_id, market_type="spread",
                     threshold=threshold, side_team_id=team_id, side=None,
                     first_seen_raw_id=rid, last_seen_at=fetched_at)
    session.add(vm)
    session.flush()
    snapshot = _make_dk_price(session, game_id=game_id, market_type="spread", team_id=team_id,
                              side=None, price=price, fetched_at=fetched_at, point=point)
    return vm, snapshot


def _make_parlay_card(session, *, status: str, built_at: datetime, legs: list[tuple]):
    """`legs` is a list of `(game_id, team_id, card_threshold, dk_point)`."""
    from harness.db.models import ParlayCard, ParlayLeg

    iso = built_at.isocalendar()
    card = ParlayCard(year=iso.year, week=iso.week, sport=_PARLAY_SPORT, kind="smart",
                      built_at=built_at, stake=Decimal("25.00"), dk_payout_est=Decimal("100.00"),
                      true_prob_est=Decimal("0.500000"), hold_est=Decimal("0.0500"),
                      rationale="test card", status=status, correlated=False)
    session.add(card)
    session.flush()
    for seq, (game_id, team_id, card_threshold, dk_point) in enumerate(legs, start=1):
        _, snapshot = _make_placement_leg(session, game_id=game_id, team_id=team_id,
                                          threshold=card_threshold, point=dk_point,
                                          fetched_at=_PLACEMENT_PRICED_AT)
        session.add(ParlayLeg(card_id=card.id, seq=seq, game_id=game_id, market_type="spread",
                              side_team_id=team_id, side=None, threshold=card_threshold,
                              dk_american=-110, dk_decimal=Decimal("1.9100"),
                              plain_text=f"leg {seq}", odds_snapshot_id=snapshot.id,
                              status="pending"))
    session.flush()
    return card


@pytest.fixture
def proposed_card(db_session):
    """A `proposed` card whose stored `threshold` equals the newest DraftKings `point`."""
    team, game = _one_leg_game(db_session, "PC")
    return _make_parlay_card(db_session, status="proposed", built_at=PARLAY_NOW,
                             legs=[(game.id, team.id, Decimal("-3.5"), Decimal("-3.5"))])


@pytest.fixture
def proposed_cards_over_budget(db_session):
    """Two proposed cards and nothing in `parlay_ledger`."""
    team1, game1 = _one_leg_game(db_session, "OB1")
    team2, game2 = _one_leg_game(db_session, "OB2")
    first = _make_parlay_card(db_session, status="proposed", built_at=PARLAY_NOW,
                              legs=[(game1.id, team1.id, Decimal("-3.5"), Decimal("-3.5"))])
    second = _make_parlay_card(db_session, status="proposed", built_at=PARLAY_NOW,
                               legs=[(game2.id, team2.id, Decimal("-3.5"), Decimal("-3.5"))])
    return first, second


@pytest.fixture
def last_week_stake(db_session, proposed_card):
    """One `parlay_ledger` `stake` row dated in the previous ISO week."""
    from harness.db.models import ParlayLedger

    last_week = PARLAY_NOW - timedelta(days=7)
    iso = last_week.isocalendar()
    db_session.add(ParlayLedger(ts=last_week, card_id=proposed_card.id, kind="stake",
                                amount=Decimal("50.00"), year=iso.year, week=iso.week))
    db_session.flush()
    return None


@pytest.fixture
def card_with_moved_line(db_session):
    """A proposed card whose newest DraftKings row carries a different `point` for one leg.
    Returns `(card, moved_seq)`."""
    team, game = _one_leg_game(db_session, "ML")
    card = _make_parlay_card(db_session, status="proposed", built_at=PARLAY_NOW,
                             legs=[(game.id, team.id, Decimal("-3.5"), Decimal("-4.5"))])
    return SimpleNamespace(card=card, moved_seq=1)


@pytest.fixture
def placed_card(db_session):
    """A card whose `status` is already `placed`."""
    team, game = _one_leg_game(db_session, "PL")
    return _make_parlay_card(db_session, status="placed", built_at=PARLAY_NOW,
                             legs=[(game.id, team.id, Decimal("-3.5"), Decimal("-3.5"))])


@pytest.fixture
def old_proposed_card(db_session):
    """A proposed card `built_at` eight days ago."""
    team, game = _one_leg_game(db_session, "OP")
    return _make_parlay_card(db_session, status="proposed",
                             built_at=PARLAY_NOW - timedelta(days=8),
                             legs=[(game.id, team.id, Decimal("-3.5"), Decimal("-3.5"))])


@pytest.fixture
def old_placed_card(db_session):
    """The same age as `old_proposed_card`, but `placed`."""
    team, game = _one_leg_game(db_session, "OPL")
    return _make_parlay_card(db_session, status="placed",
                             built_at=PARLAY_NOW - timedelta(days=8),
                             legs=[(game.id, team.id, Decimal("-3.5"), Decimal("-3.5"))])


# --- Task T12: `parlay_grade` and the leg-probability writer's fixtures ------------------------
#
# `parlay_grade`'s fixtures each build one `placed` card by hand -- a moneyline leg per game,
# `side_is_home` choosing which team the leg backs -- plus the `parlay_placements` row and the
# `parlay_ledger` `stake` row `mark_placed` would already have written by the time a card reaches
# `placed`. The leg-probability writer's fixtures build the separate shape its own query reads:
# a `venue_markets` + `fair_values` (`fair_source = 'direct'`) pair per leg, and a DraftKings
# `odds_snapshots` row unless the fixture's name says there is none.

_GRADE_KICKOFF = PARLAY_NOW - timedelta(hours=3)
_LEGPROB_PRICED_AT = datetime(2026, 9, 20, 20, 0, tzinfo=timezone.utc)


def _make_graded_card(session, *, status: str, built_at: datetime,
                      stake: Decimal = Decimal("25.00")):
    """One `parlay_cards` row, no legs yet."""
    from harness.db.models import ParlayCard

    iso = built_at.isocalendar()
    card = ParlayCard(year=iso.year, week=iso.week, sport=_PARLAY_SPORT, kind="smart",
                      built_at=built_at, stake=stake, dk_payout_est=Decimal("100.00"),
                      true_prob_est=Decimal("0.500000"), hold_est=Decimal("0.0500"),
                      rationale="test card", status=status, correlated=False)
    session.add(card)
    session.flush()
    return card


def _pf_leg(session, card, seq: int, prefix: str, *, home_score: int | None,
           away_score: int | None, side_is_home: bool = True, status: str = "final",
           leg_status: str = "alive", market_type: str = "ml", side: str | None = None,
           threshold: Decimal | None = None, dk_decimal: Decimal = Decimal("1.9100")):
    """One team pair, one game with the given final score and status, and one leg on the card.
    Defaults to a moneyline backing the home side (or the away side, when `side_is_home` is
    False); pass `market_type="spread"` with `threshold` for a spread leg on the same side
    convention, or `market_type="total"` with `side` ("over"/"under") and `threshold` for a
    total, which carries no `side_team_id` at all."""
    from harness.db.models import Game, ParlayLeg

    home = _make_team(session, f"{prefix}H")
    away = _make_team(session, f"{prefix}A")
    game = Game(sport=_PARLAY_SPORT, home_team_id=home.id, away_team_id=away.id,
                kickoff_utc=_GRADE_KICKOFF, status=status, home_score=home_score,
                away_score=away_score)
    session.add(game)
    session.flush()
    side_team_id = None if market_type == "total" else (home.id if side_is_home else away.id)
    session.add(ParlayLeg(card_id=card.id, seq=seq, game_id=game.id, market_type=market_type,
                          side_team_id=side_team_id, side=side, threshold=threshold,
                          dk_american=-110, dk_decimal=dk_decimal,
                          plain_text=f"leg {seq}", odds_snapshot_id=None, status=leg_status))
    session.flush()
    return game


def _placement_and_stake(session, card, now: datetime, stake: Decimal | None = None):
    """The `parlay_placements` row plus the `parlay_ledger` `stake` row `mark_placed` writes."""
    from harness.db.models import ParlayLedger, ParlayPlacement
    from harness.research.spend import chicago_day

    stake = stake if stake is not None else card.stake
    iso = chicago_day(now).isocalendar()
    session.add(ParlayPlacement(card_id=card.id, placed_at=now, stake_actual=stake,
                                dk_payout_actual=card.dk_payout_est, dk_odds_actual=100,
                                note=None))
    session.add(ParlayLedger(ts=now, card_id=card.id, kind="stake", amount=stake,
                             year=iso.year, week=iso.week))
    session.flush()


@pytest.fixture
def placed_card_final(db_session):
    """Three legs, each on its own final game: one hit, one miss, one push (void)."""
    card = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card, 1, "PF1", home_score=24, away_score=17)   # side (home) wins: hit
    _pf_leg(db_session, card, 2, "PF2", home_score=10, away_score=20)   # side (home) loses: miss
    _pf_leg(db_session, card, 3, "PF3", home_score=14, away_score=14)   # tied: push -> void
    _placement_and_stake(db_session, card, PARLAY_NOW)
    return card


@pytest.fixture
def placed_card_push(db_session):
    """One moneyline leg whose game finished tied."""
    card = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card, 1, "PU1", home_score=21, away_score=21)
    _placement_and_stake(db_session, card, PARLAY_NOW)
    return card


@pytest.fixture
def placed_card_all_hit(db_session):
    """Two legs, each final, each backing the side that won."""
    card = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card, 1, "AH1", home_score=30, away_score=10)
    _pf_leg(db_session, card, 2, "AH2", home_score=27, away_score=24)
    _placement_and_stake(db_session, card, PARLAY_NOW)
    return card


@pytest.fixture
def placed_card_one_miss(db_session):
    """One leg hit, one leg's side lost."""
    card = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card, 1, "OM1", home_score=30, away_score=10)   # hit
    _pf_leg(db_session, card, 2, "OM2", home_score=10, away_score=20)   # miss
    _placement_and_stake(db_session, card, PARLAY_NOW)
    return card


@pytest.fixture
def placed_card_all_void(db_session):
    """One tied final game (a push) and one postponed game -- both grade to `void`. The
    postponed leg carries no score at all: a void needs no result (a postponed game will never
    be played), so it voids on its status alone."""
    card = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card, 1, "AV1", home_score=14, away_score=14, status="final")
    _pf_leg(db_session, card, 2, "AV2", home_score=None, away_score=None, status="postponed")
    _placement_and_stake(db_session, card, PARLAY_NOW)
    return card


@pytest.fixture
def placed_card_postponed_and_unscored_final(db_session):
    """One postponed leg with no score at all, and one `final` leg whose score has not been
    recorded. The postponed leg voids on its status alone; the `final` leg stays ungraded --
    `final`/`final_ot` are the only statuses "never guess a result" still applies to."""
    card = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card, 1, "PU1", home_score=None, away_score=None, status="postponed")
    _pf_leg(db_session, card, 2, "PU2", home_score=None, away_score=None, status="final")
    _placement_and_stake(db_session, card, PARLAY_NOW)
    return card


@pytest.fixture
def placed_card_one_live(db_session):
    """One leg whose game is still `in_progress`, with no score yet."""
    card = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card, 1, "OL1", home_score=None, away_score=None, status="in_progress")
    _placement_and_stake(db_session, card, PARLAY_NOW)
    return card


@pytest.fixture
def two_placed_cards_final(db_session):
    """Two independent one-leg cards, both fully final and both hits."""
    card1 = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card1, 1, "TC1", home_score=30, away_score=10)
    _placement_and_stake(db_session, card1, PARLAY_NOW)

    card2 = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card2, 1, "TC2", home_score=30, away_score=10)
    _placement_and_stake(db_session, card2, PARLAY_NOW)
    return (card1, card2)


# --- T12 fix round 1: spread/total grading fixtures (review C1, C2, I1, I4, I5) ----------------


@pytest.fixture
def placed_card_favourite_covers_by_three(db_session):
    """Favourite -7 wins by 3: covers by -4, a miss (review C1 -- `resolve_market`'s inverted
    sign would have called this a hit)."""
    card = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card, 1, "FC1", home_score=24, away_score=21, side_is_home=True,
           market_type="spread", threshold=Decimal("-7.0"))
    _placement_and_stake(db_session, card, PARLAY_NOW)
    return card


@pytest.fixture
def placed_card_underdog_covers(db_session):
    """Underdog +7 loses by 3: covers by 4, a hit (review C1)."""
    card = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card, 1, "UC1", home_score=24, away_score=21, side_is_home=False,
           market_type="spread", threshold=Decimal("7.0"))
    _placement_and_stake(db_session, card, PARLAY_NOW)
    return card


@pytest.fixture
def placed_card_spread_push(db_session):
    """Favourite -3 wins by exactly 3: a push (review I1 -- a whole-number line can land on the
    number)."""
    card = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card, 1, "SP1", home_score=24, away_score=21, side_is_home=True,
           market_type="spread", threshold=Decimal("-3.0"))
    _placement_and_stake(db_session, card, PARLAY_NOW)
    return card


@pytest.fixture
def placed_card_under_hits(db_session):
    """Under 44, final total 37: a hit (review C2 -- an under leg must be graded by its own
    side, never as an over)."""
    card = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card, 1, "UH1", home_score=20, away_score=17, market_type="total",
           side="under", threshold=Decimal("44"))
    _placement_and_stake(db_session, card, PARLAY_NOW)
    return card


@pytest.fixture
def placed_card_total_push(db_session):
    """Over 44, final total exactly 44: a push."""
    card = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card, 1, "TP1", home_score=22, away_score=22, market_type="total",
           side="over", threshold=Decimal("44"))
    _placement_and_stake(db_session, card, PARLAY_NOW)
    return card


@pytest.fixture
def placed_card_hit_and_push(db_session):
    """One leg hits, one pushes (a tied moneyline): the card cashes, but the return is
    re-priced off the surviving leg's own `dk_decimal` rather than the quoted
    `dk_payout_actual` (review I4 -- DraftKings drops a pushed leg and re-prices)."""
    card = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, card, 1, "HP1", home_score=24, away_score=17,
           dk_decimal=Decimal("1.9100"))                        # hit
    _pf_leg(db_session, card, 2, "HP2", home_score=14, away_score=14,
           dk_decimal=Decimal("2.5000"))                        # push -> void
    _placement_and_stake(db_session, card, PARLAY_NOW)
    return card


@pytest.fixture
def placed_card_malformed_beside_a_good_one(db_session):
    """One card with a spread leg that has no `threshold` (data the stage cannot grade) beside
    one ordinary final moneyline card (review I2): the malformed card must not cost the good one
    its grade.

    Committed, not just flushed: `grade_parlays` rolls back its own transaction when a card
    fails, and in production the cards it reads were already durable from an earlier pass (a
    prior `mark_placed`) -- a fixture that left this pair merely flushed in the same transaction
    `grade_parlays` runs in would have that rollback erase the good card too, which is an
    artifact of the test session, not the behavior under test."""
    from harness.db.models import ParlayLeg

    bad = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    game = _pf_leg(db_session, bad, 1, "BC1", home_score=24, away_score=17,
                  market_type="spread", threshold=Decimal("1.0"))
    # Overwrite the threshold to NULL after the fact: `_pf_leg` always sets one for a spread,
    # and this is the shape review I2 asks the stage to isolate rather than raise out of.
    leg = db_session.query(ParlayLeg).filter_by(card_id=bad.id, seq=1).one()
    leg.threshold = None
    db_session.flush()
    _placement_and_stake(db_session, bad, PARLAY_NOW)

    good = _make_graded_card(db_session, status="placed", built_at=PARLAY_NOW)
    _pf_leg(db_session, good, 1, "GC1", home_score=24, away_score=17)
    _placement_and_stake(db_session, good, PARLAY_NOW)
    db_session.commit()
    return SimpleNamespace(bad=bad, good=good, game=game)


def _lp_team_game(session, prefix: str, *, status: str):
    from harness.db.models import Game

    home = _make_team(session, f"{prefix}H")
    away = _make_team(session, f"{prefix}A")
    game = Game(sport=_PARLAY_SPORT, home_team_id=home.id, away_team_id=away.id,
                kickoff_utc=PARLAY_NOW, status=status)
    session.add(game)
    session.flush()
    return home, away, game


def _lp_leg_market(session, *, game_id: int, team_id: int, with_dk: bool):
    """One `venue_markets` row, one `fair_values` row (`fair_source = 'direct'`), and, unless
    `with_dk` is False, one DraftKings `odds_snapshots` row at `price_decimal = 2.50` -- the
    shape the leg was priced from."""
    from harness.db.models import FairValue, VenueMarket

    rid = _next_id()
    vm = VenueMarket(venue="kalshi", ticker=f"LP-{rid}", event_ticker=f"LPE-{rid}",
                     series_ticker="S", game_id=game_id, market_type="moneyline",
                     side_team_id=team_id, side=None, first_seen_raw_id=rid,
                     last_seen_at=_LEGPROB_PRICED_AT)
    session.add(vm)
    fv = FairValue(run_id=rid, game_id=game_id, market_type="moneyline",
                   outcome_team_id=team_id, outcome_side=None, threshold=None,
                   fair_p=Decimal("0.5500"), fair_source="direct", created_at=_LEGPROB_PRICED_AT)
    session.add(fv)
    session.flush()
    if with_dk:
        _make_dk_price(session, game_id=game_id, market_type="moneyline", team_id=team_id,
                       side=None, price=Decimal("2.50"), fetched_at=_LEGPROB_PRICED_AT)
    return vm, fv


def _make_lp_card(session, *, card_status: str, game, team, with_dk: bool = True):
    from harness.db.models import ParlayCard, ParlayLeg

    iso = PARLAY_NOW.isocalendar()
    card = ParlayCard(year=iso.year, week=iso.week, sport=_PARLAY_SPORT, kind="smart",
                      built_at=PARLAY_NOW, stake=Decimal("25.00"),
                      dk_payout_est=Decimal("100.00"), true_prob_est=Decimal("0.550000"),
                      hold_est=Decimal("0.0500"), rationale="test card", status=card_status,
                      correlated=False)
    session.add(card)
    session.flush()
    _lp_leg_market(session, game_id=game.id, team_id=team.id, with_dk=with_dk)
    leg = ParlayLeg(card_id=card.id, seq=1, game_id=game.id, market_type="ml",
                    side_team_id=team.id, side=None, threshold=None, dk_american=-110,
                    dk_decimal=Decimal("1.9100"), plain_text="leg 1", odds_snapshot_id=None,
                    status="alive" if card_status in ("placed", "alive") else "pending")
    session.add(leg)
    session.flush()
    return card, leg


@pytest.fixture
def live_card_in_window(db_session):
    """A `placed` card whose game is `in_progress`: the writer's condition is met."""
    _home, _away, game = _lp_team_game(db_session, "LW", status="in_progress")
    card, leg = _make_lp_card(db_session, card_status="placed", game=game, team=_home)
    return SimpleNamespace(card=card, leg_ids=[leg.id])


@pytest.fixture
def proposed_card_in_window(db_session):
    """The game is `in_progress`, but the card is still `proposed`: nothing to write."""
    home, _away, game = _lp_team_game(db_session, "PW", status="in_progress")
    card, leg = _make_lp_card(db_session, card_status="proposed", game=game, team=home)
    return SimpleNamespace(card=card, leg_ids=[leg.id])


@pytest.fixture
def live_card_before_kickoff(db_session):
    """A `placed` card whose game has not started yet: nothing to write."""
    home, _away, game = _lp_team_game(db_session, "BK", status="scheduled")
    card, leg = _make_lp_card(db_session, card_status="placed", game=game, team=home)
    return SimpleNamespace(card=card, leg_ids=[leg.id])


@pytest.fixture
def live_card_final(db_session):
    """A `placed` card whose game has already gone final: nothing to write."""
    home, _away, game = _lp_team_game(db_session, "LF", status="final")
    card, leg = _make_lp_card(db_session, card_status="placed", game=game, team=home)
    return SimpleNamespace(card=card, leg_ids=[leg.id])


@pytest.fixture
def live_card_no_book(db_session):
    """The same shape as `live_card_in_window`, priced with no DraftKings row at all."""
    home, _away, game = _lp_team_game(db_session, "NB", status="in_progress")
    card, leg = _make_lp_card(db_session, card_status="placed", game=game, team=home,
                              with_dk=False)
    return SimpleNamespace(card=card, leg_ids=[leg.id])


# --- Task T14: the RFQ quote's fixtures (rfq_created frames + the legs they resolve through) --
#
# Every quote fixture seeds the `venue_markets` + `games` + `fair_values` rows its legs resolve
# through, one `direct` fair per leg, `created_at` inside `resolve_legs`'s `FAIR_MAX_AGE` of
# `QUOTE_NOW` -- the same moment `tests/test_rfq_quote.py` hands `handle_frame`. `_rfq_game` and
# `_rfq_market` are reused by the `rfq_grade` fixtures below with their own moment and statuses.

QUOTE_NOW = datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)
_rfq_ids = itertools.count(40001)


def _rfq_game(session, prefix: str, *, sport: str = "nfl", status: str = "scheduled",
             kickoff: datetime | None = None, home_score: int | None = None,
             away_score: int | None = None):
    from harness.db.models import Game

    home = _make_team(session, f"{prefix}H", sport=sport)
    away = _make_team(session, f"{prefix}A", sport=sport)
    game = Game(sport=sport, home_team_id=home.id, away_team_id=away.id,
               kickoff_utc=kickoff or (QUOTE_NOW + timedelta(days=1)), status=status,
               home_score=home_score, away_score=away_score)
    session.add(game)
    session.flush()
    return game


def _rfq_market(session, *, ticker: str, event_ticker: str, series_ticker: str,
                game_id: int | None, fair_p: Decimal | None,
                disagreement: Decimal = Decimal("0.001"), created_at: datetime,
                fair_source: str = "direct", market_type: str = "moneyline",
                threshold: Decimal | None = None):
    """One `venue_markets` row and (when `fair_p` is given) the `fair_values` row it resolves
    through, keyed exactly the way `harness.venues.kalshi.rfq_quote.resolve_legs` (and
    `harness.settlement.rfq_grade`'s closing-leg query) read them. `threshold` is part of that
    shape's identity (review C1) alongside `game_id`/`market_type`/`side_team_id`/`side`."""
    from harness.db.models import FairValue, VenueMarket

    rid = next(_rfq_ids)
    vm = VenueMarket(venue="kalshi", ticker=ticker, event_ticker=event_ticker,
                     series_ticker=series_ticker, game_id=game_id, market_type=market_type,
                     threshold=threshold, first_seen_raw_id=rid, last_seen_at=created_at)
    session.add(vm)
    session.flush()
    if fair_p is not None:
        session.add(FairValue(run_id=rid, game_id=game_id, market_type=market_type,
                              threshold=threshold, fair_p=fair_p, fair_source=fair_source,
                              disagreement=disagreement, created_at=created_at))
        session.flush()
    return vm


def _rfq_leg(vm) -> dict:
    return {"market_ticker": vm.ticker, "event_ticker": vm.event_ticker, "side": "yes",
           "yes_settlement_value_dollars": "1.00"}


def _rfq_frame(rfq_id: str, legs: list[dict], *, market_ticker: str | None = None,
              contracts_fp: str | None = None, target_cost_dollars: str | None = None) -> dict:
    body = {"id": rfq_id,
           "market_ticker": market_ticker or (legs[0]["market_ticker"] if legs
                                              else f"MKT-{rfq_id}"),
           "event_ticker": legs[0]["event_ticker"] if legs else None,
           "created_ts": 1789999999, "mve_selected_legs": legs}
    if contracts_fp is not None:
        body["contracts_fp"] = contracts_fp
    if target_cost_dollars is not None:
        body["target_cost_dollars"] = target_cost_dollars
    return {"type": "rfq_created", "msg": body}


@pytest.fixture
def two_game_rfq(db_session):
    """Two legs, two games, two `KXNFLGAME` events: a cross-game combo that quotes clean."""
    g1, g2 = _rfq_game(db_session, "QA"), _rfq_game(db_session, "QB")
    vm1 = _rfq_market(db_session, ticker="KXNFLGAME-QA-T1", event_ticker="KXNFLGAME-QA",
                      series_ticker="KXNFLGAME", game_id=g1.id, fair_p=Decimal("0.60"),
                      created_at=QUOTE_NOW - timedelta(minutes=2))
    vm2 = _rfq_market(db_session, ticker="KXNFLGAME-QB-T1", event_ticker="KXNFLGAME-QB",
                      series_ticker="KXNFLGAME", game_id=g2.id, fair_p=Decimal("0.50"),
                      created_at=QUOTE_NOW - timedelta(minutes=2))
    frame = _rfq_frame("RFQ-TWO-GAME", [_rfq_leg(vm1), _rfq_leg(vm2)])
    return SimpleNamespace(frame=frame)


@pytest.fixture
def same_game_rfq(db_session):
    """Two legs on the same `game_id` and the same event: the plain same-game case."""
    g = _rfq_game(db_session, "SG")
    vm1 = _rfq_market(db_session, ticker="KXNFLGAME-SG-T1", event_ticker="KXNFLGAME-SG",
                      series_ticker="KXNFLGAME", game_id=g.id, fair_p=Decimal("0.55"),
                      created_at=QUOTE_NOW - timedelta(minutes=2))
    vm2 = _rfq_market(db_session, ticker="KXNFLGAME-SG-T2", event_ticker="KXNFLGAME-SG",
                      series_ticker="KXNFLGAME", game_id=g.id, fair_p=Decimal("0.45"),
                      created_at=QUOTE_NOW - timedelta(minutes=2))
    frame = _rfq_frame("RFQ-SAME-GAME", [_rfq_leg(vm1), _rfq_leg(vm2)])
    return SimpleNamespace(frame=frame)


@pytest.fixture
def same_game_two_events_rfq(db_session):
    """Ruling A-I2: the same `game_id` reached through two different `event_ticker` values --
    e.g. the game line and the spread line on one game. Still `same_game`."""
    g = _rfq_game(db_session, "SE")
    vm1 = _rfq_market(db_session, ticker="KXNFLGAME-SE-T1", event_ticker="KXNFLGAME-SE",
                      series_ticker="KXNFLGAME", game_id=g.id, fair_p=Decimal("0.55"),
                      created_at=QUOTE_NOW - timedelta(minutes=2))
    vm2 = _rfq_market(db_session, ticker="KXNFLSPREAD-SE-T1", event_ticker="KXNFLSPREAD-SE",
                      series_ticker="KXNFLSPREAD", game_id=g.id, fair_p=Decimal("0.45"),
                      created_at=QUOTE_NOW - timedelta(minutes=2), market_type="spread")
    frame = _rfq_frame("RFQ-SAME-GAME-2EV", [_rfq_leg(vm1), _rfq_leg(vm2)])
    return SimpleNamespace(frame=frame)


@pytest.fixture
def derived_fair_rfq(db_session):
    """One leg whose only `fair_values` row is `derived`, not `direct`: `resolve_legs` finds no
    quotable fair for it at all."""
    g1, g2 = _rfq_game(db_session, "DA"), _rfq_game(db_session, "DB")
    vm1 = _rfq_market(db_session, ticker="KXNFLGAME-DA-T1", event_ticker="KXNFLGAME-DA",
                      series_ticker="KXNFLGAME", game_id=g1.id, fair_p=Decimal("0.60"),
                      fair_source="derived", created_at=QUOTE_NOW - timedelta(minutes=2))
    vm2 = _rfq_market(db_session, ticker="KXNFLGAME-DB-T1", event_ticker="KXNFLGAME-DB",
                      series_ticker="KXNFLGAME", game_id=g2.id, fair_p=Decimal("0.50"),
                      created_at=QUOTE_NOW - timedelta(minutes=2))
    frame = _rfq_frame("RFQ-DERIVED", [_rfq_leg(vm1), _rfq_leg(vm2)])
    return SimpleNamespace(frame=frame)


@pytest.fixture
def disagreeing_rfq(db_session):
    """One leg over `DISAGREEMENT_MAX`."""
    g1, g2 = _rfq_game(db_session, "GA"), _rfq_game(db_session, "GB")
    vm1 = _rfq_market(db_session, ticker="KXNFLGAME-GA-T1", event_ticker="KXNFLGAME-GA",
                      series_ticker="KXNFLGAME", game_id=g1.id, fair_p=Decimal("0.60"),
                      disagreement=Decimal("0.05"), created_at=QUOTE_NOW - timedelta(minutes=2))
    vm2 = _rfq_market(db_session, ticker="KXNFLGAME-GB-T1", event_ticker="KXNFLGAME-GB",
                      series_ticker="KXNFLGAME", game_id=g2.id, fair_p=Decimal("0.50"),
                      created_at=QUOTE_NOW - timedelta(minutes=2))
    frame = _rfq_frame("RFQ-DISAGREE", [_rfq_leg(vm1), _rfq_leg(vm2)])
    return SimpleNamespace(frame=frame)


@pytest.fixture
def unmatched_leg_rfq(db_session):
    """One leg's `market_ticker` has no `venue_markets` row at all."""
    g = _rfq_game(db_session, "UM")
    vm = _rfq_market(db_session, ticker="KXNFLGAME-UM-T1", event_ticker="KXNFLGAME-UM",
                     series_ticker="KXNFLGAME", game_id=g.id, fair_p=Decimal("0.60"),
                     created_at=QUOTE_NOW - timedelta(minutes=2))
    unmatched_leg = {"market_ticker": "KXNFLGAME-GHOST-T9", "event_ticker": "KXNFLGAME-GHOST",
                     "side": "yes", "yes_settlement_value_dollars": "1.00"}
    frame = _rfq_frame("RFQ-UNMATCHED", [_rfq_leg(vm), unmatched_leg])
    return SimpleNamespace(frame=frame)


@pytest.fixture
def one_leg_rfq(db_session):
    """A single-market RFQ: an arrival, not a combo."""
    g = _rfq_game(db_session, "OL")
    vm = _rfq_market(db_session, ticker="KXNFLGAME-OL-T1", event_ticker="KXNFLGAME-OL",
                     series_ticker="KXNFLGAME", game_id=g.id, fair_p=Decimal("0.60"),
                     created_at=QUOTE_NOW - timedelta(minutes=2))
    frame = _rfq_frame("RFQ-ONE-LEG", [_rfq_leg(vm)])
    return SimpleNamespace(frame=frame)


@pytest.fixture
def big_rfq(db_session):
    """A clean two-game combo whose `target_cost_dollars` is over `rfq_collateral_cap_usd`
    (the default $50)."""
    g1, g2 = _rfq_game(db_session, "BA"), _rfq_game(db_session, "BB")
    vm1 = _rfq_market(db_session, ticker="KXNFLGAME-BA-T1", event_ticker="KXNFLGAME-BA",
                      series_ticker="KXNFLGAME", game_id=g1.id, fair_p=Decimal("0.60"),
                      created_at=QUOTE_NOW - timedelta(minutes=2))
    vm2 = _rfq_market(db_session, ticker="KXNFLGAME-BB-T1", event_ticker="KXNFLGAME-BB",
                      series_ticker="KXNFLGAME", game_id=g2.id, fair_p=Decimal("0.50"),
                      created_at=QUOTE_NOW - timedelta(minutes=2))
    frame = _rfq_frame("RFQ-BIG", [_rfq_leg(vm1), _rfq_leg(vm2)], target_cost_dollars="500.00")
    return SimpleNamespace(frame=frame)


@pytest.fixture
def wrong_line_rfq(db_session):
    """Review C1: one leg's game carries two `fair_values` rows at different thresholds -- its
    own line, and a second, wrong one primed more recently. Without `threshold` in the lateral
    join's match, `order by created_at desc limit 1` would return the wrong line."""
    from harness.db.models import FairValue

    g1, g2 = _rfq_game(db_session, "WA"), _rfq_game(db_session, "WB")
    vm1 = _rfq_market(db_session, ticker="KXNFLSPREAD-WA-T1", event_ticker="KXNFLSPREAD-WA",
                      series_ticker="KXNFLSPREAD", game_id=g1.id, market_type="spread",
                      threshold=Decimal("-3.5"), fair_p=Decimal("0.55"),
                      created_at=QUOTE_NOW - timedelta(minutes=5))
    # The wrong line: same game and market type, a different threshold, primed more recently --
    # the row `order by created_at desc limit 1` would pick without the fix.
    db_session.add(FairValue(run_id=next(_rfq_ids), game_id=g1.id, market_type="spread",
                             threshold=Decimal("-7.5"), fair_p=Decimal("0.90"),
                             fair_source="direct", disagreement=Decimal("0.001"),
                             created_at=QUOTE_NOW - timedelta(minutes=1)))
    db_session.flush()
    vm2 = _rfq_market(db_session, ticker="KXNFLGAME-WB-T1", event_ticker="KXNFLGAME-WB",
                      series_ticker="KXNFLGAME", game_id=g2.id, fair_p=Decimal("0.50"),
                      created_at=QUOTE_NOW - timedelta(minutes=2))
    frame = _rfq_frame("RFQ-WRONG-LINE", [_rfq_leg(vm1), _rfq_leg(vm2)])
    return SimpleNamespace(frame=frame)


@pytest.fixture
def non_football_leg_rfq(db_session):
    """Fix 35: one football leg with a `direct` fair, one leg on a series this harness never
    prices -- `KXMVECROSSCATEGORY-SHARD1-...`, the incident's own shape (journal 109). The leg
    still resolves in `venue_markets` (a game_id and all), it simply cannot have a fair: the
    combo must decline `no_fair` from the cheap `venue_markets`-only check alone, without
    `resolve_legs` ever touching `fair_values` for either leg."""
    g1, g2 = _rfq_game(db_session, "NFA"), _rfq_game(db_session, "NFB")
    vm1 = _rfq_market(db_session, ticker="KXNFLGAME-NFA-T1", event_ticker="KXNFLGAME-NFA",
                      series_ticker="KXNFLGAME", game_id=g1.id, fair_p=Decimal("0.60"),
                      created_at=QUOTE_NOW - timedelta(minutes=2))
    vm2 = _rfq_market(db_session, ticker="KXMVECROSSCATEGORY-SHARD1-NFB",
                      event_ticker="KXMVECROSSCATEGORY-SHARD1",
                      series_ticker="KXMVECROSSCATEGORY", game_id=g2.id, fair_p=None,
                      created_at=QUOTE_NOW - timedelta(minutes=2))
    frame = _rfq_frame("RFQ-NON-FOOTBALL", [_rfq_leg(vm1), _rfq_leg(vm2)])
    return SimpleNamespace(frame=frame)


@pytest.fixture
def mixed_family_rfq(db_session):
    """Review M9: one NFL leg and one NCAAF leg on two games. F72's independence test fails
    under both readings here (not every component event is `KXNFL*`), so Kalshi's real maker fee
    applies to both branches. Same fair (0.60 x 0.50 = 0.3000) as `two_game_rfq`, so the only
    thing that differs between the two tests is whether a fee applies."""
    g1, g2 = _rfq_game(db_session, "MA"), _rfq_game(db_session, "MB")
    vm1 = _rfq_market(db_session, ticker="KXNFLGAME-MA-T1", event_ticker="KXNFLGAME-MA",
                      series_ticker="KXNFLGAME", game_id=g1.id, fair_p=Decimal("0.60"),
                      created_at=QUOTE_NOW - timedelta(minutes=2))
    vm2 = _rfq_market(db_session, ticker="KXNCAAFGAME-MB-T1", event_ticker="KXNCAAFGAME-MB",
                      series_ticker="KXNCAAFGAME", game_id=g2.id, fair_p=Decimal("0.50"),
                      created_at=QUOTE_NOW - timedelta(minutes=2))
    frame = _rfq_frame("RFQ-MIXED-FAMILY", [_rfq_leg(vm1), _rfq_leg(vm2)])
    return SimpleNamespace(frame=frame)


# --- Task T14: the `rfq_grade` fixtures (a stored quote + the legs it grades against) ---------

GRADE_NOW = datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc)


def _graded_rfq(session, *, rfq_id: str, legs: list[dict], yes_bid=None, no_bid=None,
                declined_reason=None):
    from harness.db.models import Rfq, RfqQuote

    first_ticker = legs[0].get("market_ticker") if legs and isinstance(legs[0], dict) else None
    rfq = Rfq(id=rfq_id, received_at=GRADE_NOW, market_ticker=first_ticker or f"MKT-{rfq_id}",
             legs=legs, raw={"msg": {}, "truncated": False}, status="open")
    session.add(rfq)
    session.flush()
    quote = RfqQuote(rfq_id=rfq_id, computed_at=GRADE_NOW, legs=len(legs),
                     fair=Decimal("0.3000") if declined_reason is None else None,
                     margin_per_leg=Decimal("0.03"), yes_bid=yes_bid, no_bid=no_bid,
                     declined_reason=declined_reason, unmatched_legs=0)
    session.add(quote)
    session.flush()
    return rfq, quote


@pytest.fixture
def settled_quote(db_session):
    """Two games `final` with scores recorded, both closing fairs written before kickoff
    (review I3: `_CLOSING_LEG` bounds the close to `created_at <= kickoff_utc`, so a "closing"
    fair written after kickoff is not one): 0.70 x 0.50 = 0.35."""
    g1 = _rfq_game(db_session, "GS1", status="final", kickoff=GRADE_NOW - timedelta(hours=8),
                   home_score=24, away_score=17)
    g2 = _rfq_game(db_session, "GS2", status="final", kickoff=GRADE_NOW - timedelta(hours=6),
                   home_score=20, away_score=13)
    vm1 = _rfq_market(db_session, ticker="KXNFLGAME-GS1-T1", event_ticker="KXNFLGAME-GS1",
                      series_ticker="KXNFLGAME", game_id=g1.id, fair_p=Decimal("0.70"),
                      created_at=g1.kickoff_utc - timedelta(minutes=10))
    vm2 = _rfq_market(db_session, ticker="KXNFLGAME-GS2-T1", event_ticker="KXNFLGAME-GS2",
                      series_ticker="KXNFLGAME", game_id=g2.id, fair_p=Decimal("0.50"),
                      created_at=g2.kickoff_utc - timedelta(minutes=10))
    rfq, quote = _graded_rfq(db_session, rfq_id="RFQ-GRADE-SETTLED",
                             legs=[_rfq_leg(vm1), _rfq_leg(vm2)],
                             yes_bid=Decimal("0.3000"), no_bid=Decimal("0.6400"))
    return SimpleNamespace(rfq=rfq, quote=quote)


@pytest.fixture
def stale_closing_quote(db_session):
    """Same shape, but one leg's newest `fair_values` row predates its kickoff by more than
    `CLOSING_WINDOW` -- the closing fair simply is not there."""
    g1 = _rfq_game(db_session, "GT1", status="final", kickoff=GRADE_NOW - timedelta(hours=8),
                   home_score=24, away_score=17)
    g2 = _rfq_game(db_session, "GT2", status="final", kickoff=GRADE_NOW - timedelta(hours=6),
                   home_score=20, away_score=13)
    vm1 = _rfq_market(db_session, ticker="KXNFLGAME-GT1-T1", event_ticker="KXNFLGAME-GT1",
                      series_ticker="KXNFLGAME", game_id=g1.id, fair_p=Decimal("0.70"),
                      created_at=g1.kickoff_utc - timedelta(hours=8))
    vm2 = _rfq_market(db_session, ticker="KXNFLGAME-GT2-T1", event_ticker="KXNFLGAME-GT2",
                      series_ticker="KXNFLGAME", game_id=g2.id, fair_p=Decimal("0.50"),
                      created_at=g2.kickoff_utc - timedelta(minutes=10))
    rfq, quote = _graded_rfq(db_session, rfq_id="RFQ-GRADE-STALE",
                             legs=[_rfq_leg(vm1), _rfq_leg(vm2)],
                             yes_bid=Decimal("0.3000"), no_bid=Decimal("0.6400"))
    return SimpleNamespace(rfq=rfq, quote=quote)


@pytest.fixture
def declined_quote(db_session):
    """A declined quote: `declined_reason` set, bids null, never graded."""
    rfq, quote = _graded_rfq(db_session, rfq_id="RFQ-GRADE-DECLINED", legs=[],
                             declined_reason="same_game")
    return SimpleNamespace(rfq=rfq, quote=quote)


@pytest.fixture
def unsettled_quote(db_session):
    """One leg's game is still `in_progress`: nothing to grade against yet."""
    g1 = _rfq_game(db_session, "GU1", status="in_progress",
                   kickoff=GRADE_NOW - timedelta(hours=1))
    g2 = _rfq_game(db_session, "GU2", status="final", kickoff=GRADE_NOW - timedelta(hours=6),
                   home_score=20, away_score=13)
    vm1 = _rfq_market(db_session, ticker="KXNFLGAME-GU1-T1", event_ticker="KXNFLGAME-GU1",
                      series_ticker="KXNFLGAME", game_id=g1.id, fair_p=Decimal("0.70"),
                      created_at=g1.kickoff_utc - timedelta(minutes=10))
    vm2 = _rfq_market(db_session, ticker="KXNFLGAME-GU2-T1", event_ticker="KXNFLGAME-GU2",
                      series_ticker="KXNFLGAME", game_id=g2.id, fair_p=Decimal("0.50"),
                      created_at=g2.kickoff_utc - timedelta(minutes=10))
    rfq, quote = _graded_rfq(db_session, rfq_id="RFQ-GRADE-UNSETTLED",
                             legs=[_rfq_leg(vm1), _rfq_leg(vm2)],
                             yes_bid=Decimal("0.3000"), no_bid=Decimal("0.6400"))
    return SimpleNamespace(rfq=rfq, quote=quote)


@pytest.fixture
def postponed_leg_quote(db_session):
    """Review I4: one leg's game is `postponed` -- this combo can never settle, so the quote is
    voided rather than left waiting forever."""
    g1 = _rfq_game(db_session, "GP1", status="postponed",
                   kickoff=GRADE_NOW - timedelta(hours=8))
    g2 = _rfq_game(db_session, "GP2", status="final", kickoff=GRADE_NOW - timedelta(hours=6),
                   home_score=20, away_score=13)
    vm1 = _rfq_market(db_session, ticker="KXNFLGAME-GP1-T1", event_ticker="KXNFLGAME-GP1",
                      series_ticker="KXNFLGAME", game_id=g1.id, fair_p=Decimal("0.70"),
                      created_at=g1.kickoff_utc - timedelta(minutes=10))
    vm2 = _rfq_market(db_session, ticker="KXNFLGAME-GP2-T1", event_ticker="KXNFLGAME-GP2",
                      series_ticker="KXNFLGAME", game_id=g2.id, fair_p=Decimal("0.50"),
                      created_at=g2.kickoff_utc - timedelta(minutes=10))
    rfq, quote = _graded_rfq(db_session, rfq_id="RFQ-GRADE-POSTPONED",
                             legs=[_rfq_leg(vm1), _rfq_leg(vm2)],
                             yes_bid=Decimal("0.3000"), no_bid=Decimal("0.6400"))
    return SimpleNamespace(rfq=rfq, quote=quote)


@pytest.fixture
def final_no_score_quote(db_session):
    """Review I4: a `final` game with no recorded score is not settled -- "never guess a
    result" holds for `rfq_grade` the same way `harness.settlement.parlay_grade`'s
    `_score_state` holds it for a leg."""
    g1 = _rfq_game(db_session, "GN1", status="final", kickoff=GRADE_NOW - timedelta(hours=8))
    g2 = _rfq_game(db_session, "GN2", status="final", kickoff=GRADE_NOW - timedelta(hours=6),
                   home_score=20, away_score=13)
    vm1 = _rfq_market(db_session, ticker="KXNFLGAME-GN1-T1", event_ticker="KXNFLGAME-GN1",
                      series_ticker="KXNFLGAME", game_id=g1.id, fair_p=Decimal("0.70"),
                      created_at=g1.kickoff_utc - timedelta(minutes=10))
    vm2 = _rfq_market(db_session, ticker="KXNFLGAME-GN2-T1", event_ticker="KXNFLGAME-GN2",
                      series_ticker="KXNFLGAME", game_id=g2.id, fair_p=Decimal("0.50"),
                      created_at=g2.kickoff_utc - timedelta(minutes=10))
    rfq, quote = _graded_rfq(db_session, rfq_id="RFQ-GRADE-NO-SCORE",
                             legs=[_rfq_leg(vm1), _rfq_leg(vm2)],
                             yes_bid=Decimal("0.3000"), no_bid=Decimal("0.6400"))
    return SimpleNamespace(rfq=rfq, quote=quote)


@pytest.fixture
def malformed_quote_beside_a_good_one(db_session):
    """One quote whose stored `legs` holds an element that is not a mapping (review I1) beside
    an ordinary settled quote: the malformed quote must not cost the good one its grade.

    Committed, not just flushed, for the same reason `placed_card_malformed_beside_a_good_one`
    (T12's fixture, above) is: `grade_rfq_quotes` rolls back its own savepoint when a quote
    fails, and a fixture that left this pair merely flushed in the outer transaction would have
    that rollback erase the good quote too."""
    bad_rfq, bad_quote = _graded_rfq(db_session, rfq_id="RFQ-GRADE-MALFORMED",
                                     legs=["not-a-dict"], yes_bid=Decimal("0.30"),
                                     no_bid=Decimal("0.64"))

    g1 = _rfq_game(db_session, "GG1", status="final", kickoff=GRADE_NOW - timedelta(hours=8),
                   home_score=24, away_score=17)
    g2 = _rfq_game(db_session, "GG2", status="final", kickoff=GRADE_NOW - timedelta(hours=6),
                   home_score=20, away_score=13)
    vm1 = _rfq_market(db_session, ticker="KXNFLGAME-GG1-T1", event_ticker="KXNFLGAME-GG1",
                      series_ticker="KXNFLGAME", game_id=g1.id, fair_p=Decimal("0.70"),
                      created_at=g1.kickoff_utc - timedelta(minutes=10))
    vm2 = _rfq_market(db_session, ticker="KXNFLGAME-GG2-T1", event_ticker="KXNFLGAME-GG2",
                      series_ticker="KXNFLGAME", game_id=g2.id, fair_p=Decimal("0.50"),
                      created_at=g2.kickoff_utc - timedelta(minutes=10))
    good_rfq, good_quote = _graded_rfq(db_session, rfq_id="RFQ-GRADE-GOOD",
                                       legs=[_rfq_leg(vm1), _rfq_leg(vm2)],
                                       yes_bid=Decimal("0.30"), no_bid=Decimal("0.64"))
    db_session.commit()
    return SimpleNamespace(bad=bad_quote, good=good_quote)


@pytest.fixture
def empty_legs_quote(db_session):
    """Review M5: an `rfqs` row with no legs at all (unreachable through `compute_quote`, which
    declines anything under two legs) has no product to grade -- left waiting, never graded with
    the fabricated certainty of an empty product (1.0000)."""
    rfq, quote = _graded_rfq(db_session, rfq_id="RFQ-GRADE-EMPTY-LEGS", legs=[],
                             yes_bid=Decimal("0.30"), no_bid=Decimal("0.64"))
    return SimpleNamespace(rfq=rfq, quote=quote)
