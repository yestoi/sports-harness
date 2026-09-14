"""The player-stat collector and the draft reprice, inside the tick.

Addendum 4.3 and 2.3. The collector runs on the game-window tick only, writes rows for carded
players only and only on a change, and treats a later lower value as a correction; the reprice
runs on the 900 s tick, touches `proposed` cards only, and never moves a leg's line. Both use a
fixed tz-aware `now` and a fake ESPN client -- nothing here reaches the network.
"""
import itertools
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from harness.db.models import (Game, GameScoreEvent, OddsPropSnapshot, ParlayCard, ParlayLeg,
                               Player, PlayerStatEvent, Team)
from harness.feeds.http import FetchResult
from harness.recorder import store
from harness.weeks import chicago_iso_week
from tests.test_recorder_props import NOW, _FakeOdds, _Harness, _in_game_kickoffs

NOW_IN_GAME_WINDOW = NOW
NOW_FINAL = NOW
G = 4242
OLD_PRICE, NEW_PRICE = 100, 150          # decimal 2.00 and 2.50
_ids = itertools.count(60001)


# --- the fake summary feed -------------------------------------------------------------------

class _FakeEspnSummary:
    """One game's summary, as ESPN's box score and scoring plays. `summary_value` is the
    quarterback's passing yards as the source currently reports them."""

    def __init__(self, now=NOW):
        self.now = now
        self.summary_value = 225
        self.td_scorers: list[str] = []
        self.dropped: set[str] = set()
        #: The number of post-final fetches that answer with no box score at all.
        self.final_boxscore_after = 0
        self.calls: list[tuple[datetime, str]] = []
        self.roster_calls: list[int] = []

    def drop_player(self, espn_id: str) -> None:
        self.dropped.add(str(espn_id))

    def calls_at(self, now: datetime) -> int:
        return len([c for c in self.calls if c[0] == now])

    def fetch_roster(self, sport, team_id):
        self.roster_calls.append(int(team_id))
        return FetchResult(200, {}, {"athletes": []}, self.now,
                           f"https://e/{sport}/teams/{team_id}/roster", 0.01)

    def fetch_summary(self, sport, espn_event_id):
        self.calls.append((self.now, str(espn_event_id)))
        empty = len([c for c in self.calls if c[1] == str(espn_event_id)]) <= self.final_boxscore_after
        athletes = [] if ("7" in self.dropped or empty) else [
            {"athlete": {"id": "7"}, "stats": ["20/30", str(self.summary_value)]}]
        body = {
            "boxscore": {"players": [{"statistics": [
                {"name": "passing", "keys": ["C/ATT", "YDS"], "athletes": athletes}]}]},
            "scoringPlays": [{"type": {"abbreviation": "RUSHTD"}, "text": "1 yd run",
                              "athlete": {"id": scorer}} for scorer in self.td_scorers],
        }
        return FetchResult(200, {}, body, self.now,
                           f"https://e/{sport}/summary?event={espn_event_id}", 0.01)


@pytest.fixture
def recorder(env_settings, db_session):
    harness = _Harness(env_settings, db_session, _FakeOdds(NOW), _FakeEspnSummary(NOW))
    outer = harness.tick_ctx

    def tick_ctx(now, kickoffs=None):
        # The fake has no clock of its own: the tick's `now` is what its calls are stamped with.
        harness.espn.now = now
        return outer(now, _in_game_kickoffs(now) if kickoffs is None else kickoffs)

    harness.tick_ctx = tick_ctx
    return harness


# --- seeding -----------------------------------------------------------------------------------

def _game(session, game_id: int, status: str, kickoff: datetime, sport: str = "nfl") -> Game:
    home_id, away_id = next(_ids), next(_ids)
    for team_id, prefix in ((home_id, "H"), (away_id, "A")):
        name = f"{prefix}{str(team_id)[-4:]}"
        session.add(Team(sport=sport, id=team_id, display_name=name, location=name, name=name,
                         abbreviation=name, short_display_name=name))
    game = Game(id=game_id, sport=sport, home_team_id=home_id, away_team_id=away_id,
                kickoff_utc=kickoff, odds_api_event_id=f"oa-{game_id}",
                espn_event_id=f"espn-{game_id}", status=status)
    session.add(game)
    session.flush()
    return game


def _carded_prop_leg(session, player_id: int, game_id: int, game_status: str = "in_progress",
                     card_status: str = "placed", stat: str = "pass_yds") -> ParlayCard:
    kickoff = NOW - timedelta(hours=2) if game_status != "scheduled" else NOW + timedelta(hours=6)
    game = _game(session, game_id, game_status, kickoff)
    session.add(Player(id=player_id, sport="nfl", espn_id=str(player_id), name="Test Quarterback",
                       team_id=game.home_team_id, position="QB", updated_at=NOW))
    if game_status == "final":
        session.add(GameScoreEvent(game_id=game_id, ts=NOW_FINAL, status="final",
                                   home_score=20, away_score=17))
    year, week = chicago_iso_week(NOW)
    card = ParlayCard(year=year, week=week, sport="nfl", kind="smart", built_at=NOW,
                      stake=Decimal("25.00"), status=card_status, correlated=False,
                      policy_version="2026.09-1", p_source_min="book_devig")
    session.add(card)
    session.flush()
    session.add(ParlayLeg(card_id=card.id, seq=1, game_id=game_id, market_type="prop",
                          side="over", threshold=Decimal("225.5"), dk_american=OLD_PRICE,
                          dk_decimal=Decimal("2.00"), plain_text="Test Quarterback over 225.5",
                          status="pending", player_id=player_id, stat=stat, period="game",
                          operator="over", p_at_build=Decimal("0.5000"), p_source="book_devig"))
    session.flush()
    return SimpleNamespace(id=card.id, game_id=game_id, player_id=player_id)


def _prop_row(session, *, game_id: int, player_id: int, price: Decimal, fetched_at: datetime,
              point: Decimal = Decimal("225.5"), side: str = "over",
              market_type: str = "prop:pass_yds") -> OddsPropSnapshot:
    row = OddsPropSnapshot(raw_id=next(_ids), book="draftkings", game_id=game_id,
                           market_type=market_type, player_name="Test Quarterback",
                           player_id=player_id, outcome_side=side, point=point,
                           price_decimal=price, fetched_at=fetched_at,
                           link="https://sportsbook.draftkings.com/x", sid="s1")
    session.add(row)
    session.flush()
    return row


def _card(session, status: str, kickoff_in: timedelta = timedelta(hours=6),
          fresh_price: Decimal | None = Decimal("2.50")) -> ParlayCard:
    """A one-prop-leg card of the current week, priced from a two-minute-old row, with a fresh
    row at `fresh_price` for the reprice to find."""
    game_id = next(_ids)
    player_id = next(_ids)
    game = _game(session, game_id, "scheduled", NOW + kickoff_in)
    session.add(Player(id=player_id, sport="nfl", espn_id=str(player_id), name="Test Quarterback",
                       team_id=game.home_team_id, position="QB", updated_at=NOW))
    session.flush()
    built = _prop_row(session, game_id=game_id, player_id=player_id, price=Decimal("2.00"),
                      fetched_at=NOW - timedelta(minutes=10), point=Decimal("225"))
    if fresh_price is not None:
        _prop_row(session, game_id=game_id, player_id=player_id, price=fresh_price,
                  fetched_at=NOW - timedelta(minutes=1), point=Decimal("225"))
    year, week = chicago_iso_week(NOW)
    card = ParlayCard(year=year, week=week, sport="nfl", kind="smart", built_at=NOW,
                      stake=Decimal("25.00"), dk_payout_est=Decimal("50.00"),
                      status=status, correlated=False, policy_version="2026.09-1",
                      p_source_min="book_devig")
    session.add(card)
    session.flush()
    session.add(ParlayLeg(card_id=card.id, seq=1, game_id=game_id, market_type="prop",
                          side="over", threshold=Decimal("225"), dk_american=OLD_PRICE,
                          dk_decimal=Decimal("2.00"), plain_text="Test Quarterback over 225",
                          odds_prop_snapshot_id=built.id, status="pending", player_id=player_id,
                          stat="pass_yds", period="game", operator="over",
                          p_at_build=Decimal("0.5000"), p_source="book_devig", offered=True))
    session.flush()
    # Plain ids, not the ORM object: the prop source commits and expunges per event now
    # (review I1), so an instance held across a tick would be detached.
    return SimpleNamespace(id=card.id, game_id=game_id, player_id=player_id)


def _legs(session, card_id: int) -> list[ParlayLeg]:
    return (session.query(ParlayLeg).filter_by(card_id=card_id)
            .order_by(ParlayLeg.seq).all())


def _newest_stat(session, player_id: int, stat: str) -> PlayerStatEvent:
    return (session.query(PlayerStatEvent).filter_by(player_id=player_id, stat=stat)
            .order_by(PlayerStatEvent.ts.desc(), PlayerStatEvent.id.desc()).first())


def _return_no_row_for(session, card) -> None:
    """The last prop fetch for this card's event succeeded and carried no row for the selection:
    a `source_state` stamp newer than every prop row of the game (the reprice reads the database,
    not the client)."""
    game = session.get(Game, card.game_id)
    session.execute(text("delete from odds_prop_snapshots where game_id = :g"), {"g": card.game_id})
    store.set_source_state(session, f"odds_prop:{game.odds_api_event_id}", NOW)
    session.flush()


def _move_line(session, card, to: Decimal) -> None:
    session.execute(text("delete from odds_prop_snapshots where game_id = :g and point = 225"),
                    {"g": card.game_id})
    _prop_row(session, game_id=card.game_id, player_id=card.player_id, price=Decimal("2.50"),
              fetched_at=NOW - timedelta(minutes=1), point=to)
    session.flush()


# --- the collector --------------------------------------------------------------------------

def test_stat_rows_are_written_only_for_carded_players_and_only_on_a_change(recorder, db_session):
    _carded_prop_leg(db_session, player_id=7, game_id=G)
    recorder.tick_ctx(now=NOW_IN_GAME_WINDOW)
    first = db_session.query(PlayerStatEvent).count()
    assert first > 0
    recorder.tick_ctx(now=NOW_IN_GAME_WINDOW + timedelta(seconds=120))   # same body
    assert db_session.query(PlayerStatEvent).count() == first
    assert db_session.query(PlayerStatEvent).filter(PlayerStatEvent.player_id != 7).count() == 0


def test_the_collector_is_skipped_outside_the_game_window(recorder, db_session):
    _carded_prop_leg(db_session, player_id=7, game_id=G)
    ctx = recorder.tick_ctx(now=NOW, kickoffs=[])
    assert ctx["player_stats"] == {"skipped": "cadence 900"}
    assert db_session.query(PlayerStatEvent).count() == 0


def test_a_later_lower_value_is_written_as_a_correction(recorder, db_session):
    """B-I14: ESPN's summary carries no per-stat timestamp and sequential polls of one endpoint
    return the source's current state, so a lower value later is a correction, not an
    out-of-order arrival. `source_ts` stays null and the surface shows the fetch age."""
    _carded_prop_leg(db_session, player_id=7, game_id=G)
    recorder.espn.summary_value = 225
    recorder.tick_ctx(now=NOW_IN_GAME_WINDOW)
    recorder.espn.summary_value = 208
    recorder.tick_ctx(now=NOW_IN_GAME_WINDOW + timedelta(seconds=120))
    row = _newest_stat(db_session, player_id=7, stat="pass_yds")
    assert row.value == Decimal("208") and row.correction is True and row.source_ts is None


def test_a_higher_value_later_is_not_a_correction(recorder, db_session):
    _carded_prop_leg(db_session, player_id=7, game_id=G)
    recorder.tick_ctx(now=NOW_IN_GAME_WINDOW)
    recorder.espn.summary_value = 260
    recorder.tick_ctx(now=NOW_IN_GAME_WINDOW + timedelta(seconds=120))
    row = _newest_stat(db_session, player_id=7, stat="pass_yds")
    assert row.value == Decimal("260") and row.correction is False


def test_a_player_missing_from_an_update_writes_nothing(recorder, db_session):
    _carded_prop_leg(db_session, player_id=7, game_id=G)
    recorder.espn.drop_player("7")
    ctx = recorder.tick_ctx(now=NOW_IN_GAME_WINDOW)
    assert db_session.query(PlayerStatEvent).filter_by(player_id=7).count() == 0
    assert ctx["player_stats"]["missing"] == 1
    # Review M1: a player absent from an *in-progress* box score is normal play, so the
    # process counter a WATCH rule thresholds on does not move.
    assert recorder.counters["stat_missing"] == 0


def test_a_player_missing_from_a_final_box_score_is_counted_stat_missing(recorder, db_session):
    _carded_prop_leg(db_session, player_id=7, game_id=G, game_status="final")
    recorder.espn.drop_player("7")
    recorder.tick_ctx(now=NOW_FINAL)
    assert recorder.counters["stat_missing"] == 1


def test_a_listed_player_with_no_touchdown_gets_a_zero_row(recorder, db_session):
    """Task 5 review ruling I3: the collector writes a value-0 `anytime_td` row for every listed
    carded player, so the grader reads a miss rather than `stat_missing`."""
    _carded_prop_leg(db_session, player_id=7, game_id=G, stat="anytime_td")
    recorder.tick_ctx(now=NOW_IN_GAME_WINDOW)
    row = _newest_stat(db_session, player_id=7, stat="anytime_td")
    assert row is not None and row.value == Decimal("0")


def test_a_scoring_play_credits_its_scorer(recorder, db_session):
    _carded_prop_leg(db_session, player_id=7, game_id=G, stat="anytime_td")
    recorder.espn.td_scorers = ["7"]
    recorder.tick_ctx(now=NOW_IN_GAME_WINDOW)
    assert _newest_stat(db_session, player_id=7, stat="anytime_td").value == Decimal("1")


def test_the_collector_keeps_fetching_after_final_until_the_box_score_lands(recorder, db_session):
    _carded_prop_leg(db_session, player_id=7, game_id=G, game_status="final")
    recorder.espn.final_boxscore_after = 3
    for i in range(4):
        recorder.tick_ctx(now=NOW_FINAL + timedelta(seconds=120 * i))
    assert _newest_stat(db_session, player_id=7, stat="pass_yds").value is not None
    recorder.tick_ctx(now=NOW_FINAL + timedelta(hours=7))
    assert recorder.espn.calls_at(NOW_FINAL + timedelta(hours=7)) == 0      # 6 h ceiling


def test_the_six_hour_ceiling_stops_a_final_game_whose_box_score_never_lands(recorder, db_session):
    _carded_prop_leg(db_session, player_id=7, game_id=G, game_status="final")
    recorder.espn.final_boxscore_after = 1000          # the box score never arrives
    recorder.tick_ctx(now=NOW_FINAL + timedelta(hours=5))
    assert recorder.espn.calls_at(NOW_FINAL + timedelta(hours=5)) == 1
    recorder.tick_ctx(now=NOW_FINAL + timedelta(hours=7))
    assert recorder.espn.calls_at(NOW_FINAL + timedelta(hours=7)) == 0


def test_an_unchanged_final_box_score_stops_the_retry(recorder, db_session):
    """Review I3. The last in-progress poll usually already holds the final line, so the final
    box score writes nothing; a stop condition made of *write* timestamps never fires and the
    game is re-fetched every 120 s for six hours. The stop condition is a fetch fact."""
    _carded_prop_leg(db_session, player_id=7, game_id=G)
    recorder.tick_ctx(now=NOW_IN_GAME_WINDOW)                    # in progress: 312 recorded
    rows_before = db_session.query(PlayerStatEvent).count()
    db_session.execute(text("update games set status = 'final' where id = :g"), {"g": G})
    db_session.add(GameScoreEvent(game_id=G, ts=NOW_FINAL + timedelta(seconds=120),
                                  status="final", home_score=20, away_score=17))
    db_session.flush()
    recorder.tick_ctx(now=NOW_FINAL + timedelta(seconds=120))    # the same values, at final
    assert db_session.query(PlayerStatEvent).count() == rows_before   # nothing changed
    calls = len(recorder.espn.calls)
    recorder.tick_ctx(now=NOW_FINAL + timedelta(seconds=240))
    assert len(recorder.espn.calls) == calls                     # the box score landed


def test_a_proposed_card_is_not_collected_for(recorder, db_session):
    _carded_prop_leg(db_session, player_id=7, game_id=G, card_status="proposed")
    recorder.tick_ctx(now=NOW_IN_GAME_WINDOW)
    assert db_session.query(PlayerStatEvent).count() == 0


def test_the_collector_never_fails_a_tick(recorder, db_session):
    def boom(sport, event_id):
        raise RuntimeError("espn down")

    _carded_prop_leg(db_session, player_id=7, game_id=G)
    recorder.espn.fetch_summary = boom
    ctx = recorder.tick_ctx(now=NOW_IN_GAME_WINDOW)
    assert ctx["errors"] == [] and ctx["warnings"]


# --- the reprice ------------------------------------------------------------------------------

def test_the_reprice_touches_only_proposed_cards(recorder, db_session):
    proposed, placed = _card(db_session, "proposed"), _card(db_session, "placed")
    recorder.tick_ctx(now=NOW, kickoffs=[])
    assert _legs(db_session, proposed.id)[0].dk_american == NEW_PRICE
    assert _legs(db_session, placed.id)[0].dk_american == OLD_PRICE


def test_the_card_s_payout_and_combined_price_are_recomputed(recorder, db_session):
    card = _card(db_session, "proposed")
    ctx = recorder.tick_ctx(now=NOW, kickoffs=[])
    row = db_session.get(ParlayCard, card.id)
    assert row.dk_payout_est == Decimal("62.50")          # 25.00 x 2.50
    assert row.dk_combined_american == NEW_PRICE and row.dk_combined_at == NOW
    assert ctx["reprice"]["cards"] == 1 and ctx["reprice"]["legs"] == 1


def test_offered_goes_false_only_inside_the_window_after_a_successful_fetch(recorder, db_session):
    """B-I7/D10: outside the prop window nothing changes and the surface reads
    `not repriced - outside the price window`; a failed fetch is not evidence of removal."""
    card = _card(db_session, "proposed", kickoff_in=timedelta(hours=40))
    _return_no_row_for(db_session, card)
    recorder.tick_ctx(now=NOW, kickoffs=[])
    assert _legs(db_session, card.id)[0].offered is True
    card2 = _card(db_session, "proposed", kickoff_in=timedelta(hours=6))
    _return_no_row_for(db_session, card2)
    # A period later: the reprice runs once per prop rotation (review I2), never twice inside
    # one 900 s period.
    ctx = recorder.tick_ctx(now=NOW + timedelta(seconds=900), kickoffs=[])
    assert _legs(db_session, card2.id)[0].offered is False
    assert ctx["reprice"]["unoffered"] == 1


def test_a_failed_fetch_is_not_evidence_of_removal(recorder, db_session):
    """No successful prop fetch for the event at all: `offered` is left alone."""
    card = _card(db_session, "proposed", kickoff_in=timedelta(hours=6), fresh_price=None)
    db_session.execute(text("delete from odds_prop_snapshots where game_id = :g"),
                       {"g": card.game_id})
    db_session.flush()
    recorder.tick_ctx(now=NOW, kickoffs=[])
    assert _legs(db_session, card.id)[0].offered is True


def test_a_moved_line_leaves_the_price_stale_rather_than_changing_the_leg(recorder, db_session):
    card = _card(db_session, "proposed")
    _move_line(db_session, card, to=Decimal("227.5"))
    recorder.tick_ctx(now=NOW, kickoffs=[])
    leg = _legs(db_session, card.id)[0]
    assert leg.threshold == Decimal("225") and leg.dk_american == OLD_PRICE


def test_the_reprice_is_skipped_on_the_game_window_tick(recorder, db_session):
    card = _card(db_session, "proposed")
    ctx = recorder.tick_ctx(now=NOW_IN_GAME_WINDOW)
    assert ctx["reprice"] == {"skipped": "cadence 120"}
    assert _legs(db_session, card.id)[0].dk_american == OLD_PRICE


def test_the_reprice_never_fails_a_tick(recorder, db_session, monkeypatch):
    from harness.recorder import tick as tick_module

    _card(db_session, "proposed")
    monkeypatch.setattr(tick_module.Recorder, "_PROPOSED_CARDS",
                        property(lambda self: (_ for _ in ()).throw(RuntimeError("boom"))))
    ctx = recorder.tick_ctx(now=NOW, kickoffs=[])
    assert ctx["reprice"] == {"error": "RuntimeError"} and ctx["errors"] == []


def test_a_second_pass_with_no_new_price_leaves_the_card_alone(recorder, db_session):
    """Review I2: a card whose inputs did not move is not rewritten, so `dk_combined_at` stays
    the moment its price was actually current."""
    card = _card(db_session, "proposed")
    recorder.tick_ctx(now=NOW, kickoffs=[])
    first = db_session.get(ParlayCard, card.id)
    stamped, payout = first.dk_combined_at, first.dk_payout_est
    ctx = recorder.tick_ctx(now=NOW + timedelta(seconds=900), kickoffs=[])
    again = db_session.get(ParlayCard, card.id)
    assert ctx["reprice"] == {"cards": 0, "legs": 0, "unoffered": 0}
    assert again.dk_combined_at == stamped and again.dk_payout_est == payout


def test_the_reprice_runs_once_per_900_second_period(recorder, db_session):
    """Its own 900 s stamp, not the 30 s heartbeat (review I2)."""
    _card(db_session, "proposed")
    assert "skipped" not in recorder.tick_ctx(now=NOW, kickoffs=[])["reprice"]
    second = recorder.tick_ctx(now=NOW + timedelta(seconds=30), kickoffs=[])
    assert second["props"] == {"skipped": "interval"}
    assert second["reprice"] == {"skipped": "interval"}
    third = recorder.tick_ctx(now=NOW + timedelta(seconds=900), kickoffs=[])
    assert "skipped" not in third["reprice"]


def test_a_draft_is_repriced_while_the_prop_feed_is_dormant(recorder, db_session):
    """The controller's ruling on fix-round concern 1: the reprice reads stored rows and spends
    no credit, so a dormant month (or the 40 % watch fraction, or a weekend) must not stop a
    draft from following the prices that keep arriving."""
    from tests.test_recorder_props import _record_credits

    card = _card(db_session, "proposed")
    _record_credits(db_session, month="2026-09", used=300_000)
    ctx = recorder.tick_ctx(now=NOW, kickoffs=[])
    assert ctx["props"] == {"skipped": "budget"}
    assert ctx["reprice"]["cards"] == 1
    assert _legs(db_session, card.id)[0].dk_american == NEW_PRICE
    # ... and still only once per period.
    assert recorder.tick_ctx(now=NOW + timedelta(seconds=30),
                             kickoffs=[])["reprice"] == {"skipped": "interval"}
