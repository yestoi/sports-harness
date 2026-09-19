"""As-of features (ruling A-I3), the delta, and the three invalidators (0.2, ruling B-I2)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from harness.research.features import (BUCKET_MINUTES, ESPN_POLL_S, ESPN_STATUSES,
                                       FAIR_MOVE_INVALIDATOR, FEATURE_WINDOW,
                                       VALIDITY_WINDOWS, WEATHER_REFETCH, build_features,
                                       feature_delta, invalidated)
from tests.veto_fixtures import seed_game, seed_history, seed_signal, seed_weather

SIGNAL_AT = datetime(2026, 9, 19, 22, 0, tzinfo=timezone.utc)


def _view(signal, game):
    """The attribute shape `build_features` reads: the worker passes its own `QueuedSignal`."""
    return SimpleNamespace(id=signal.id, game_id=game.id, market_type="moneyline",
                           venue_market_id=signal.venue_market_id, side=signal.side,
                           fair_p=signal.fair_p, edge=signal.edge,
                           created_at=signal.created_at)


@pytest.fixture
def seeded_game_history(db_session):
    """One game with six hours of history behind the signal and ten minutes of it in front."""
    game, market = seed_game(db_session, kickoff=SIGNAL_AT + timedelta(hours=3),
                             score_status="scheduled",
                             score_ts=SIGNAL_AT - timedelta(hours=2))
    signal = seed_signal(db_session, market=market, created_at=SIGNAL_AT)
    seed_history(db_session, game=game, market=market, as_of=SIGNAL_AT)
    seed_weather(db_session, game=game, fetched_at=SIGNAL_AT - timedelta(hours=1))
    return SimpleNamespace(game=game, market=market, signal=_view(signal, game))


@pytest.fixture
def seeded_bare_signal(db_session):
    """A game with no fair values, no quotes, no score events and no weather at all."""
    game, market = seed_game(db_session, kickoff=SIGNAL_AT + timedelta(hours=3))
    signal = seed_signal(db_session, market=market, created_at=SIGNAL_AT)
    return _view(signal, game)


def test_the_window_and_bucket_constants():
    assert FEATURE_WINDOW == timedelta(hours=6) and BUCKET_MINUTES == 5
    assert FAIR_MOVE_INVALIDATOR == Decimal("0.02")


def test_features_stop_at_the_signal_not_at_now(db_session, seeded_game_history):
    """The whole point of A-I3: a fair value written after the signal must not appear."""
    numeric, _ = build_features(db_session, seeded_game_history.signal, SIGNAL_AT)
    stamps = [point["ts"] for point in numeric["fair_history"]]
    assert stamps and max(stamps) <= SIGNAL_AT.isoformat()
    # The seeded post-signal row is 0.90; nothing in the vector may carry it.
    assert max(point["fair_p"] for point in numeric["fair_history"]) < 0.6


def test_the_fair_history_is_five_minute_buckets_over_six_hours(db_session,
                                                                seeded_game_history):
    numeric, _ = build_features(db_session, seeded_game_history.signal, SIGNAL_AT)
    assert len(numeric["fair_history"]) <= int(FEATURE_WINDOW.total_seconds() // 60
                                               // BUCKET_MINUTES)
    assert all(set(p) == {"ts", "fair_p", "n"} for p in numeric["fair_history"])


def test_every_timestamp_is_explicit_in_both_zones(db_session, seeded_game_history):
    """Addendum 1.4: "Every timestamp explicit (UTC and America/Chicago), including
    `signal_created_at`". A model reasoning about a Saturday-night kickoff has to be able to see
    which Saturday night."""
    numeric, _ = build_features(db_session, seeded_game_history.signal, SIGNAL_AT)
    assert numeric["signal_created_at_utc"].endswith("+00:00")
    assert numeric["signal_created_at_ct"]
    assert numeric["as_of_utc"] == numeric["signal_created_at_utc"]
    assert numeric["kickoff_utc"] and numeric["kickoff_ct"]


def test_the_espn_status_is_a_fixed_enum(db_session, seeded_game_history):
    """Ruling A-M4: an enum, never the venue's own string. An unrecognised status becomes
    `unknown` rather than putting free text in the numeric block."""
    numeric, _ = build_features(db_session, seeded_game_history.signal, SIGNAL_AT)
    assert numeric["espn_status"] in ESPN_STATUSES


def test_an_unrecognised_status_becomes_unknown(db_session, seeded_game_history):
    from sqlalchemy import text

    db_session.execute(text("update game_score_events set status = 'status_suspended'"))
    numeric, _ = build_features(db_session, seeded_game_history.signal, SIGNAL_AT)
    assert numeric["espn_status"] == "unknown"


def test_free_text_lives_in_the_untrusted_block_only(db_session, seeded_game_history):
    """Ruling A-M4 and F60: `short_forecast` is the one free-text feature, it is sanitized, and
    it never appears in the numeric block the model is told to trust."""
    import json

    numeric, untrusted = build_features(db_session, seeded_game_history.signal, SIGNAL_AT)
    assert "short_forecast" not in json.dumps(numeric)
    assert untrusted["weather"]["short_forecast"]
    assert "<" not in untrusted["weather"]["short_forecast"]


def test_no_injury_feature_is_produced(db_session, seeded_game_history):
    """D21: the ESPN injury input and its cache invalidator are dropped this phase. No injury
    feed exists, and a feature that is always null teaches a model that there is never news."""
    import json

    numeric, untrusted = build_features(db_session, seeded_game_history.signal, SIGNAL_AT)
    assert "injur" not in json.dumps(numeric).lower()
    assert "injur" not in json.dumps(untrusted).lower()


def test_a_game_with_no_history_still_builds(db_session, seeded_bare_signal):
    numeric, untrusted = build_features(db_session, seeded_bare_signal, SIGNAL_AT)
    assert numeric["fair_history"] == [] and numeric["venue_history"] == []
    assert untrusted["weather"] is None


def test_the_venue_and_book_histories_are_present(db_session, seeded_game_history):
    """Addendum 1.4 names three histories, not one: fair across books, the venue's own mids, and
    the book lines the fair was made from."""
    numeric, _ = build_features(db_session, seeded_game_history.signal, SIGNAL_AT)
    assert numeric["venue_history"] and all(set(p) == {"ts", "mid", "n"}
                                            for p in numeric["venue_history"])
    assert "pinnacle" in numeric["book_history"]


# --- the delta and the invalidators ------------------------------------------------------------

def _features(**overrides):
    base = {"fair_p": 0.5100, "espn_status": "scheduled", "weather_fetched_at": None,
            "minutes_to_kickoff": 180, "disagreement": 0.004, "fair_staleness_s": 40}
    base.update(overrides)
    return base


def test_the_delta_names_only_what_moved():
    delta = feature_delta(_features(), _features(fair_p=0.5300, minutes_to_kickoff=175))
    assert set(delta) == {"fair_p", "minutes_to_kickoff"}
    assert delta["fair_p"] == pytest.approx(0.02)


def test_a_two_point_fair_move_invalidates():
    """Ruling B-I2 and addendum 0.2's two-point rule. The number is this phase's own constant
    with the addendum as its source: its numeric twin is `velocity_max_pts` under
    `harness/variants/`, which no task in this phase may edit."""
    assert invalidated(_features(), _features(fair_p=0.5301)) == "fair_move"
    assert invalidated(_features(), _features(fair_p=0.5299)) is None


def test_a_fair_move_downwards_invalidates_too():
    assert invalidated(_features(), _features(fair_p=0.4899)) == "fair_move"


def test_an_espn_status_change_invalidates():
    assert invalidated(_features(), _features(espn_status="in_progress")) == "espn_status"


def test_a_new_weather_snapshot_invalidates():
    a = _features(weather_fetched_at="2026-09-19T21:00:00+00:00")
    b = _features(weather_fetched_at="2026-09-19T21:30:00+00:00")
    assert invalidated(a, b) == "weather"


def test_nothing_moving_does_not_invalidate():
    assert invalidated(_features(), _features(minutes_to_kickoff=178)) is None


def test_a_missing_fair_never_invalidates():
    """A null fair on either side is not a two-point move; it is no measurement."""
    assert invalidated(_features(), _features(fair_p=None)) is None


# --- 6D.1 1.8(e): one explicit validity window per invalidator key -----------------------------

def test_every_invalidator_key_has_its_own_named_window():
    """1.8(e): a window **per key**, and exactly the three keys the three labels come from."""
    assert set(VALIDITY_WINDOWS) == {"espn_status", "weather", "fair_move"}
    assert VALIDITY_WINDOWS["espn_status"] == timedelta(seconds=ESPN_POLL_S)
    assert VALIDITY_WINDOWS["weather"] == WEATHER_REFETCH
    assert VALIDITY_WINDOWS["fair_move"] == FEATURE_WINDOW
    # Derived, not invented: 900 s, one hour and six hours, in the order they are tested in.
    assert [int(w.total_seconds()) for w in VALIDITY_WINDOWS.values()] == [900, 3600, 21_600]


def test_the_windows_are_the_cadences_the_harness_already_runs_on():
    """Each window's source, asserted rather than described (brief rule 2).

    `espn_status` is the recorder's own scoreboard cadence, `weather` is the weather source's
    own refetch interval, and `fair_move` is the fair history the vector is built over.
    """
    from pathlib import Path

    from harness.recorder import tick
    from harness.weather.snapshots import REFETCH_AFTER

    assert WEATHER_REFETCH == REFETCH_AFTER
    source = Path(tick.__file__).read_text()
    assert "self._due(store.get_source_state(session, key), now, 900)" in source
    assert VALIDITY_WINDOWS["espn_status"] == timedelta(seconds=900)


def test_a_cache_older_than_the_espn_window_returns_that_keys_label():
    """(a) Identical features, an elapsed window: the cached context for that key is no longer
    valid, and the label is the key's own so `veto_decisions.reason_code` keeps its vocabulary."""
    stale = SIGNAL_AT + VALIDITY_WINDOWS["espn_status"] + timedelta(seconds=1)
    assert invalidated(_features(), _features(), cached_at=SIGNAL_AT, as_of=stale) == "espn_status"
    # At the window exactly, the next poll is due and the cached context has elapsed with it.
    at_the_window = SIGNAL_AT + VALIDITY_WINDOWS["espn_status"]
    assert invalidated(_features(), _features(), cached_at=SIGNAL_AT,
                       as_of=at_the_window) == "espn_status"
    assert invalidated(_features(), _features()) is None          # the default path, unchanged


def test_inside_every_window_identical_features_do_not_invalidate():
    """(b) A cache inside every one of the three windows is still valid: no call, no label."""
    inside = SIGNAL_AT + min(VALIDITY_WINDOWS.values()) - timedelta(seconds=1)
    assert all(inside - SIGNAL_AT < window for window in VALIDITY_WINDOWS.values())
    assert invalidated(_features(), _features(), cached_at=SIGNAL_AT, as_of=inside) is None
    # And the shortest elapsed window is the one that names the decision, so the label is
    # deterministic when more than one has run out.
    long_gone = SIGNAL_AT + max(VALIDITY_WINDOWS.values()) + timedelta(hours=1)
    shortest = min(VALIDITY_WINDOWS, key=lambda key: VALIDITY_WINDOWS[key])
    assert invalidated(_features(), _features(), cached_at=SIGNAL_AT, as_of=long_gone) == shortest


def test_a_fair_move_at_the_threshold_beats_the_age_of_the_cache():
    """(c) 0.02 is news whatever the clock says, and an expired cache never turns a moved fair
    into a different label: the delta is tested first and wins."""
    inside = SIGNAL_AT + timedelta(minutes=1)
    stale = SIGNAL_AT + VALIDITY_WINDOWS["espn_status"] + timedelta(minutes=5)
    moved = _features(fair_p=0.5300)                  # exactly FAIR_MOVE_INVALIDATOR away
    assert invalidated(_features(), moved, cached_at=SIGNAL_AT, as_of=inside) == "fair_move"
    assert invalidated(_features(), moved, cached_at=SIGNAL_AT, as_of=stale) == "fair_move"
    # A changed status inside its own window is still the status change, not an expiry.
    assert invalidated(_features(), _features(espn_status="in_progress"),
                       cached_at=SIGNAL_AT, as_of=inside) == "espn_status"


def test_the_omitted_cached_at_path_is_todays_behaviour_exactly():
    """(d) With the two timestamps omitted -- and with either one of them missing -- the answer
    is the delta-based one on every existing fixture, byte for byte."""
    ancient = SIGNAL_AT - timedelta(days=7)
    pairs = ((_features(), _features()),
             (_features(), _features(minutes_to_kickoff=178)),
             (_features(), _features(fair_p=0.5301)),
             (_features(), _features(fair_p=0.5299)),
             (_features(), _features(fair_p=0.4899)),
             (_features(), _features(fair_p=None)),
             (_features(), _features(espn_status="in_progress")),
             (_features(weather_fetched_at="2026-09-19T21:00:00+00:00"),
              _features(weather_fetched_at="2026-09-19T21:30:00+00:00")))
    for trigger, other in pairs:
        today = invalidated(trigger, other)
        assert invalidated(trigger, other, cached_at=None, as_of=None) == today
        # One timestamp alone is no age at all, so a caller that has only one gets today's path.
        assert invalidated(trigger, other, cached_at=ancient) == today
        assert invalidated(trigger, other, as_of=SIGNAL_AT) == today
