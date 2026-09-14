"""The prop source inside the tick: cadence, allocation, rotation and the weekly roster half.

Addendum 3.2 and 4.1. Every test drives `Recorder._props` (and, through the same harness, the
two sources beside it) at a **fixed tz-aware `now`**: 2026-09-16 18:00 UTC is a Wednesday
13:00 CT, which `interval_for` reads as the weekday 900 s period -- the only cadence props are
allowed to run on. Nothing here reaches the network: the Odds and ESPN clients are fakes that
count their calls and hand back recorded shapes.
"""
import itertools
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from harness.db.models import Game, Player, SourceState, Team
from harness.feeds.espn import Kickoff
from harness.feeds.http import FetchError, FetchResult
from harness.recorder import store
from harness.recorder.tick import PROP_FAIL_BACKOFF_AFTER, Recorder, _Budget

NOW = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)        # Wed 13:00 CT -> cadence 900
NOW_IN_GAME_WINDOW = NOW                                       # with `_in_game_kickoffs` below
# Sat 13:00 CT, three days after NOW: no kickoff in `kickoffs` (empty by default) and outside
# the 01:00-08:00 CT quiet band, so `interval_for` falls through to the weekend-weekday branch
# and returns 300 (journal 209, ruling 11).
NOW_SATURDAY = datetime(2026, 9, 19, 18, 0, tzinfo=timezone.utc)
MONTH_KEY = "odds_props:2026-09"


def _in_game_kickoffs(now: datetime) -> list[Kickoff]:
    """A kickoff an hour old, which `interval_for` reads as the 120 s game window."""
    return [Kickoff("nfl", "in-progress", now - timedelta(hours=1), "H", "A", "STATUS_IN_PROGRESS")]


# --- the fakes ---------------------------------------------------------------------------

_ROSTER_BODY = {"athletes": [{"items": [
    {"id": "7", "displayName": "Test Quarterback", "position": {"abbreviation": "QB"}},
    {"id": "8", "displayName": "Test Receiver", "position": {"abbreviation": "WR"}},
]}]}


class _FakeOdds:
    """Counts prop calls and the credits its headers returned; never touches the network."""

    def __init__(self, now: datetime):
        self.now = now
        self.prop_calls = 0
        self.prop_credits_returned = 0
        self.calls: list[str] = []
        #: event id -> the body that event answers with; anything unset answers `[]`.
        self.bodies: dict[str, list] = {}
        self._fail = 0

    def fail_next(self, n: int) -> None:
        self._fail = n

    def fetch_event_props(self, sport: str, event_id: str) -> FetchResult:
        self.prop_calls += 1
        self.calls.append(event_id)
        if self._fail > 0:
            self._fail -= 1
            raise FetchError(f"{event_id}: boom")
        self.prop_credits_returned += 9
        return FetchResult(200, {"x-requests-last": "9", "x-requests-remaining": "4000000"},
                           self.bodies.get(event_id, []), self.now,
                           f"https://o/v4/{sport}/events/{event_id}/odds", 0.01)


class _FakeEspn:
    def __init__(self, now: datetime):
        self.now = now
        self.roster_calls: list[int] = []

    def fetch_roster(self, sport: str, team_id) -> FetchResult:
        self.roster_calls.append(int(team_id))
        return FetchResult(200, {}, _ROSTER_BODY, self.now, f"https://e/{sport}/teams/{team_id}/roster", 0.01)


class _Harness:
    """One `Recorder` plus the fakes, driving the three sources the way a tick does."""

    def __init__(self, settings, session, odds, espn, monotonic=time.monotonic):
        factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        self.rec = Recorder(settings, factory, odds, espn, kalshi=None,
                            clock=lambda: NOW, monotonic=monotonic)
        self.session, self.odds, self.espn = session, odds, espn
        #: What the featured fetch would have left in `ctx["remaining"]` this tick.
        self.last_remaining = 3_000_000
        self.tick_budget_s = 100

    @property
    def counters(self):
        return self.rec.counters

    def tick_ctx(self, now, kickoffs=None):
        run = store.start_run(self.session, now)
        ctx = {"n": 0, "credits": 0, "remaining": self.last_remaining, "errors": [],
               "warnings": [], "fetched": False}
        budget = _Budget(self.tick_budget_s, self.rec.monotonic)
        self.rec._props(self.session, run, now, kickoffs or [], budget, ctx)
        self.rec._player_stats(self.session, run, now, kickoffs or [], budget, ctx)
        self.rec._parlay_reprice(self.session, run, now, kickoffs or [], ctx)
        self.session.commit()
        return ctx


@pytest.fixture
def recorder(env_settings, db_session):
    return _Harness(env_settings, db_session, _FakeOdds(NOW), _FakeEspn(NOW))


# --- seeding ------------------------------------------------------------------------------

_ids = itertools.count(70001)


def _event(session, sport: str, index: int, kickoff: datetime, *, abbr: str | None = None,
           signal: bool = True) -> Game:
    """One watchable prop event: two teams, a game carrying an `odds_api_event_id`, and (unless
    it is an anchor event) one candidate signal with a direct fair inside the pool window."""
    from tests.conftest import _make_leg

    home_id, away_id = next(_ids), next(_ids)
    home = abbr or f"H{index:03d}"
    for team_id, name in ((home_id, home), (away_id, f"A{index:03d}")):
        session.add(Team(sport=sport, id=team_id, display_name=name, location=name, name=name,
                         abbreviation=name, short_display_name=name))
    game = Game(sport=sport, home_team_id=home_id, away_team_id=away_id, kickoff_utc=kickoff,
                odds_api_event_id=f"{sport}-{index}", espn_event_id=f"e{sport}{index}",
                status="scheduled")
    session.add(game)
    session.flush()
    if signal:
        _make_leg(session, game_id=game.id, market_type="moneyline", side_team_id=home_id,
                  side=None, threshold=None, fair_p=Decimal("0.55"), edge=Decimal("0.05"),
                  # Four hours before kickoff: inside `pool_window_hours` (6) of a `now` that is
                  # about two hours before kickoff, which is where every watched event sits.
                  created_at=kickoff - timedelta(hours=4))
    return game


def _watchable_events(session, nfl: int = 0, ncaaf: int = 0, with_anchor: str | None = None,
                      base: datetime = NOW) -> list[Game]:
    games = []
    for index in range(nfl):
        games.append(_event(session, "nfl", index, base + timedelta(hours=2, minutes=index)))
    for index in range(ncaaf):
        games.append(_event(session, "ncaaf", 100 + index,
                            base + timedelta(hours=2, minutes=index)))
    if with_anchor:
        # Deliberately the *latest* kickoff of its sport, so "anchor first" is not the same
        # ordering as "kickoff first", and with no signal at all: an anchor team is watched on
        # its own (3.2).
        games.append(_event(session, "ncaaf", 900, base + timedelta(hours=20),
                            abbr=with_anchor, signal=False))
    session.flush()
    return games


def _record_credits(session, month: str, used: int) -> None:
    session.execute(text(
        "insert into source_state (key, last_fetched_at, credits_used) values (:k, :ts, :used) "
        "on conflict (key) do update set credits_used = :used"),
        {"k": f"odds_props:{month}", "ts": NOW - timedelta(hours=1), "used": used})
    session.flush()


def _fetched_event_ids(props) -> set[str]:
    return set(props["event_ids"])


def _watched(recorder, session, sport: str):
    _rows, watched = recorder.rec._watched_prop_events(session, NOW)
    return [w for w in watched if w.sport == sport]


# --- the cadence guard ---------------------------------------------------------------------

def test_props_never_run_on_the_120_second_game_window_tick(recorder, db_session):
    """Addendum 3.2: only the 900 s tick, never the game-window cadence and never quiet hours.
    The alternates source's cadence is 120 s and is explicitly *not* what this reuses (B-I5)."""
    _watchable_events(db_session, nfl=2)
    ctx = recorder.tick_ctx(now=NOW_IN_GAME_WINDOW, kickoffs=_in_game_kickoffs(NOW))
    assert ctx["props"] == {"skipped": "cadence 120"}
    assert recorder.odds.prop_calls == 0


def test_props_never_run_in_quiet_hours(recorder, db_session):
    quiet = datetime(2026, 9, 16, 8, 0, tzinfo=timezone.utc)   # 03:00 CT, no game on the field
    _watchable_events(db_session, nfl=2, base=quiet)
    ctx = recorder.tick_ctx(now=quiet)
    assert ctx["props"] == {"skipped": "cadence None"}
    assert recorder.odds.prop_calls == 0


def test_props_run_on_the_weekend_300_second_cadence(recorder, db_session):
    """User decision 2026-09-14, journal 209, ruling 11: `interval_for` returns 300, never
    900, on a Saturday or Sunday outside every game window, and the old `cadence !=
    PROPS_CADENCE_S` guard left the prop source dormant every weekend as a result. The
    source's own 900 s stamp still bounds it to one pass a period, on any day."""
    _watchable_events(db_session, nfl=2, base=NOW_SATURDAY)
    ctx = recorder.tick_ctx(now=NOW_SATURDAY)
    assert ctx["props"] != {"skipped": "cadence 300"}
    assert "skipped" not in ctx["props"]
    assert recorder.odds.prop_calls > 0
    # Same 900 s period, thirty seconds later: the period's own stamp, not the cadence
    # value, is what bounds a weekend pass to once per 900 s.
    second = recorder.tick_ctx(now=NOW_SATURDAY + timedelta(seconds=30))
    assert second["props"] == {"skipped": "interval"}


def test_the_reprice_runs_on_the_weekend_300_second_cadence(recorder, db_session):
    """The same ruling for `_parlay_reprice`: the reprice must keep refusing the 120 s game
    window, the 20 s NFL pre-kickoff window and quiet hours (`cadence None`), and now runs
    on the weekend 300 s cadence too."""
    ctx = recorder.tick_ctx(now=NOW_SATURDAY)
    assert ctx["reprice"] != {"skipped": "cadence 300"}
    assert "skipped" not in ctx["reprice"]
    game_window = recorder.tick_ctx(now=NOW_IN_GAME_WINDOW, kickoffs=_in_game_kickoffs(NOW))
    assert game_window["reprice"] == {"skipped": "cadence 120"}
    quiet = datetime(2026, 9, 16, 8, 0, tzinfo=timezone.utc)   # 03:00 CT, no game on the field
    assert recorder.tick_ctx(now=quiet)["reprice"] == {"skipped": "cadence None"}


# --- the watched set and the rotation -------------------------------------------------------

def test_at_most_sixteen_events_a_sport_and_sixteen_calls_a_tick(recorder, db_session):
    _watchable_events(db_session, nfl=25, ncaaf=25)
    ctx = recorder.tick_ctx(now=NOW)
    assert ctx["props"]["events"] == 32 and ctx["props"]["calls"] == 16


def test_a_second_heartbeat_inside_the_900_second_period_spends_nothing(recorder, db_session):
    """Review C1. `maybe_tick` runs on the 30 s heartbeat; the cadence *value* being 900 says
    which period is in force, not that 900 s have passed. Without the pass's own `source_state`
    gate the rotation paid for sixteen events every 30 s -- 17,280 credits an hour against the
    design's 576, and the month's whole allocation in about a day."""
    _watchable_events(db_session, nfl=2)
    first = recorder.tick_ctx(now=NOW)["props"]
    assert first["calls"] == 2 and recorder.odds.prop_calls == 2
    second = recorder.tick_ctx(now=NOW + timedelta(seconds=30))
    assert second["props"] == {"skipped": "interval"}
    assert recorder.odds.prop_calls == 2
    assert recorder.tick_ctx(now=NOW + timedelta(seconds=899))["props"] == {"skipped": "interval"}
    assert recorder.odds.prop_calls == 2
    resumed = recorder.tick_ctx(now=NOW + timedelta(seconds=900))["props"]
    assert resumed["calls"] == 2 and recorder.odds.prop_calls == 4


def test_a_dormant_month_does_not_consume_the_period_or_stop_the_rosters(recorder, db_session):
    """Reviews M3 and C1. The free ESPN roster half runs above the paid guards -- the identity
    map must not go stale in the same moment the feed goes dormant -- and a skipped pass does
    not stamp the period."""
    _watchable_events(db_session, nfl=1)
    _record_credits(db_session, month="2026-09", used=300_000)
    ctx = recorder.tick_ctx(now=NOW)
    assert ctx["props"] == {"skipped": "budget"}
    assert recorder.espn.roster_calls and db_session.query(Player).count() > 0
    assert db_session.get(SourceState, "odds_props") is None


def test_the_rotation_takes_the_oldest_fetched_first_so_32_events_refresh_in_two_ticks(
        recorder, db_session):
    _watchable_events(db_session, nfl=16, ncaaf=16)
    first = recorder.tick_ctx(now=NOW)["props"]
    second = recorder.tick_ctx(now=NOW + timedelta(seconds=900))["props"]
    assert first["calls"] == second["calls"] == 16
    assert _fetched_event_ids(first) & _fetched_event_ids(second) == set()


def test_the_watched_set_is_anchor_first_then_kickoff(recorder, db_session):
    _watchable_events(db_session, nfl=20, with_anchor="LSU")
    assert _watched(recorder, db_session, "ncaaf")[0].anchor is True


def test_an_event_beyond_the_prop_window_is_never_watched(recorder, db_session):
    _event(db_session, "nfl", 1, NOW + timedelta(hours=30))
    db_session.flush()
    ctx = recorder.tick_ctx(now=NOW)
    assert ctx["props"]["events"] == 0 and ctx["props"]["calls"] == 0


def test_an_event_with_neither_an_anchor_nor_a_signal_is_never_watched(recorder, db_session):
    _event(db_session, "nfl", 1, NOW + timedelta(hours=2), signal=False)
    db_session.flush()
    assert recorder.tick_ctx(now=NOW)["props"]["events"] == 0


# --- the two budget guards -------------------------------------------------------------------

def test_the_month_s_allocation_makes_props_dormant(recorder, db_session):
    _watchable_events(db_session, nfl=2)
    _record_credits(db_session, month="2026-09", used=300_000)
    ctx = recorder.tick_ctx(now=NOW)
    assert ctx["props"] == {"skipped": "budget"}
    assert recorder.counters["prop_skipped_budget"] == 1
    assert recorder.odds.prop_calls == 0


def test_props_are_skipped_while_the_strategy_feed_is_under_forty_percent_remaining(
        recorder, db_session):
    """3.2: the strategy feed is protected first, before the 20 % gate-5 line is near.
    Computed independently: 40 % of 5,000,000 is 2,000,000."""
    _watchable_events(db_session, nfl=2)
    recorder.last_remaining = 1_999_999
    assert recorder.tick_ctx(now=NOW)["props"] == {"skipped": "remaining"}
    recorder.last_remaining = 2_000_001
    assert "skipped" not in recorder.tick_ctx(now=NOW)["props"]


def test_an_unknown_remaining_spends_nothing(recorder, db_session):
    """A tick whose featured fetch never reported `x-requests-remaining` does not know what is
    left of the strategy feed's month, and an unknown balance is never a licence to spend."""
    _watchable_events(db_session, nfl=2)
    recorder.last_remaining = None
    assert recorder.tick_ctx(now=NOW)["props"] == {"skipped": "remaining"}


def test_the_credit_counter_sums_x_requests_last_per_chicago_month(recorder, db_session):
    _watchable_events(db_session, nfl=2)
    recorder.tick_ctx(now=NOW)
    row = db_session.get(SourceState, MONTH_KEY)
    assert row.credits_used == recorder.odds.prop_credits_returned


def test_the_credit_counter_accumulates_across_ticks(recorder, db_session):
    _watchable_events(db_session, nfl=2)
    recorder.tick_ctx(now=NOW)
    recorder.tick_ctx(now=NOW + timedelta(seconds=900))
    row = db_session.get(SourceState, MONTH_KEY)
    assert row.credits_used == recorder.odds.prop_credits_returned == 4 * 9


def test_a_database_failure_mid_rotation_keeps_the_earlier_events_and_their_credits(
        recorder, db_session, monkeypatch):
    """Review I1. A failure on call 3 used to abort the transaction and roll the whole pass
    back: the venue had charged for every call, the paid bodies were gone, and the month's
    counter never learned about the spend, so the 300,000 allocation guard read low for ever."""
    from harness.recorder import store as store_module

    real = store_module.store_raw
    seen = {"n": 0}

    def flaky(session, run_id, source, endpoint, params, result):
        if source == "odds_api":
            seen["n"] += 1
            if seen["n"] == 3:
                session.execute(text("select 1/0"))       # aborts the transaction, as a real
                                                          # database error does
        return real(session, run_id, source, endpoint, params, result)

    monkeypatch.setattr(store_module, "store_raw", flaky)
    _watchable_events(db_session, nfl=4)
    ctx = recorder.tick_ctx(now=NOW)
    assert ctx["props"]["calls"] == 4 and ctx["errors"] == [] and len(ctx["warnings"]) == 1
    stored = db_session.execute(text(
        "select count(*) from raw_responses where source = 'odds_api'")).scalar()
    assert stored == 3                       # the three that wrote; the fourth call still ran
    # Every call the venue charged for is on the month's counter, including the one whose own
    # write failed.
    assert db_session.get(SourceState, MONTH_KEY).credits_used == 4 * 9
    assert ctx["props"]["credits"] == 4 * 9


def test_a_repeatedly_failing_event_stops_taking_a_call_slot(recorder, db_session):
    """Review I4. Sixteen events whose ids were re-keyed answer 404 for ever; stamped only on
    success they held the head of the oldest-first rotation and burned the whole allowance."""
    _watchable_events(db_session, nfl=1)
    recorder.odds.fail_next(1000)
    for tick in range(PROP_FAIL_BACKOFF_AFTER):
        ctx = recorder.tick_ctx(now=NOW + timedelta(seconds=900 * tick))
        assert ctx["props"]["calls"] == 1
    assert recorder.odds.prop_calls == PROP_FAIL_BACKOFF_AFTER
    rested = recorder.tick_ctx(now=NOW + timedelta(seconds=900 * PROP_FAIL_BACKOFF_AFTER))
    assert rested["props"]["calls"] == 0 and rested["props"]["rested"] == 1
    assert recorder.odds.prop_calls == PROP_FAIL_BACKOFF_AFTER
    # Past the backoff (the last failure was at NOW + 1800 s) and still inside the event's
    # 24 h kickoff window, it is tried again.
    recorder.odds.fail_next(0)
    back = recorder.tick_ctx(now=NOW + timedelta(seconds=5460))
    assert back["props"]["calls"] == 1 and back["props"]["rested"] == 0


def test_a_success_clears_the_failure_count(recorder, db_session):
    _watchable_events(db_session, nfl=1)
    recorder.odds.fail_next(2)
    for tick in range(3):
        recorder.tick_ctx(now=NOW + timedelta(seconds=900 * tick))
    row = db_session.get(SourceState, "odds_prop_fail:nfl-0")
    assert row is not None and row.credits_used == 0


def test_a_prop_fetch_failure_counts_against_the_sub_budget_and_never_fails_the_tick(
        recorder, db_session):
    _watchable_events(db_session, nfl=16)
    recorder.odds.fail_next(3)
    ctx = recorder.tick_ctx(now=NOW)
    assert ctx["props"]["calls"] == 16 and "error" not in ctx["props"]
    assert ctx["errors"] == [] and len(ctx["warnings"]) == 3


def test_the_sub_budget_stops_the_rotation_rather_than_the_tick(env_settings, db_session):
    """The 40 s sub-budget is a ceiling on the prop half of one tick, not on the tick."""
    clock = {"t": 0.0}

    def monotonic():
        clock["t"] += 30.0        # every budget read burns 30 s
        return clock["t"]

    harness = _Harness(env_settings, db_session, _FakeOdds(NOW), _FakeEspn(NOW),
                       monotonic=monotonic)
    _watchable_events(db_session, nfl=16)
    ctx = harness.tick_ctx(now=NOW)
    assert ctx["props"]["calls"] < 16 and ctx["errors"] == []


# --- the roster half -------------------------------------------------------------------------

def test_each_watched_team_s_roster_is_fetched_once_a_week(recorder, db_session):
    """IM-7 / addendum 4.1: `players` is filled from the roster endpoint, once per team per
    week, for the teams of the watched prop events -- and nothing else fills it, so without this
    the identity map is empty and every prop row stays unresolved."""
    game_ids = [game.id for game in _watchable_events(db_session, nfl=2)]
    first = recorder.tick_ctx(now=NOW)["props"]
    assert first["rosters"] == 4                      # two events, two teams each
    assert db_session.query(Player).count() > 0
    again = recorder.tick_ctx(now=NOW + timedelta(seconds=900))["props"]
    assert again["rosters"] == 0                      # inside the week, nothing re-fetched
    # The same two events, eight days later: a prop event is only watched inside the 24 h
    # kickoff window, so the games move with the clock (the sketch in the plan left them behind).
    later_now = NOW + timedelta(days=8)
    for game_id in game_ids:
        db_session.get(Game, game_id).kickoff_utc = later_now + timedelta(hours=2)
    db_session.execute(text("update signals set created_at = :ts"),
                       {"ts": later_now - timedelta(hours=2)})
    db_session.flush()
    later = recorder.tick_ctx(now=later_now)["props"]
    assert later["rosters"] == 4                      # past seven days, refreshed once


def test_a_roster_failure_never_fails_the_tick_and_the_prop_calls_still_run(recorder, db_session):
    def boom(sport, team_id):
        raise FetchError("roster down")

    _watchable_events(db_session, nfl=1)
    recorder.espn.fetch_roster = boom
    ctx = recorder.tick_ctx(now=NOW)
    assert ctx["props"]["rosters"] == 0 and ctx["props"]["calls"] == 1
    assert ctx["errors"] == [] and ctx["warnings"]


def test_the_source_never_fails_a_tick(recorder, db_session, monkeypatch):
    from harness.recorder import tick as tick_module

    monkeypatch.setattr(tick_module.Recorder, "_PROP_EVENT_CANDIDATES",
                        property(lambda self: (_ for _ in ()).throw(RuntimeError("boom"))))
    ctx = recorder.tick_ctx(now=NOW)
    assert ctx["props"] == {"error": "RuntimeError"} and ctx["warnings"]
    assert ctx["errors"] == []


# --- the raw row the normalizer picks up -----------------------------------------------------

def _prop_body(event_id: str, player: str = "Test Quarterback") -> list:
    """One event's DraftKings prop market, in the venue's own shape."""
    return [{"id": event_id, "bookmakers": [{
        "key": "draftkings", "last_update": "2026-09-16T17:59:00Z",
        "markets": [{"key": "player_pass_yds", "outcomes": [
            {"name": "Over", "description": player, "point": 225.5, "price": 1.91,
             "link": "https://sportsbook.draftkings.com/event/1", "sid": "0QA123"},
            {"name": "Under", "description": player, "point": 225.5, "price": 1.91}]}]}]}]


def test_a_stored_prop_body_normalizes_into_odds_prop_snapshots(recorder, db_session):
    """The end of the plumbing: props are stored under the per-event endpoint the normalizer's
    `odds_alternates` family already drains, so the rows Task 6's pool reads appear without a
    new family -- and the roster fetched earlier in the same tick is what resolves the player."""
    from harness.db.models import OddsPropSnapshot
    from harness.normalize.runner import normalize_new

    event_id = _watchable_events(db_session, nfl=1)[0].odds_api_event_id
    recorder.odds.bodies[event_id] = _prop_body(event_id)
    recorder.tick_ctx(now=NOW)
    normalize_new(db_session, ctx={"warnings": []})
    rows = db_session.query(OddsPropSnapshot).order_by(OddsPropSnapshot.outcome_side).all()
    assert [r.market_type for r in rows] == ["prop:pass_yds", "prop:pass_yds"]
    assert [r.outcome_side for r in rows] == ["over", "under"]
    assert rows[0].player_id is not None and rows[0].player_name == "Test Quarterback"
    assert rows[0].link == "https://sportsbook.draftkings.com/event/1"


def test_an_unrostered_prop_outcome_is_counted_unmatched(recorder, db_session):
    """4.2: ambiguity and absence never pick a player. The count is made where it is decided,
    in the normalizer, and the tick folds it into `ctx["props"]["unmatched"]`."""
    from harness.normalize.runner import normalize_new

    event_id = _watchable_events(db_session, nfl=1)[0].odds_api_event_id
    recorder.odds.bodies[event_id] = _prop_body(event_id, player="Nobody On This Roster")
    recorder.tick_ctx(now=NOW)
    ctx = {"warnings": []}
    normalize_new(db_session, ctx=ctx)
    assert ctx["prop_unmatched"] == 2
