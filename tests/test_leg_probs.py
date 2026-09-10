"""The leg-probability writer: the model's own condition, and `book_p`.

**Each fixture seeds** one `teams` row per side, one `games` row with the `status` its name says
(`in_progress` for the `_in_window` fixtures, `scheduled` for `live_card_before_kickoff`, `final`
for `live_card_final`), one `venue_markets` row per leg, a `fair_values` row per leg with
`fair_source = 'direct'`, one `parlay_cards` row with the `status` its name says (`placed` for
the `live_*` fixtures, `proposed` for `proposed_card_in_window`), its `parlay_legs`, and one
`odds_snapshots` row per leg with `book = 'draftkings'` and `price_decimal = 2.50` -- except
`live_card_no_book`, which seeds no DraftKings row at all. Each returns an object carrying
`leg_ids`.
"""
import time
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from harness.recorder import store
from tests.test_tick import _recorder

NOW = datetime(2026, 9, 20, 23, 30, tzinfo=timezone.utc)


def _call(env_settings, db_session, ctx=None):
    """Build a recorder the one way the suite builds one, start a run, call the writer."""
    recorder, _clock = _recorder(env_settings, db_session, now=NOW)
    run = store.start_run(db_session, NOW)
    recorder._leg_probs(db_session, run, NOW, ctx if ctx is not None else {"warnings": []})
    return recorder, run


def test_the_condition_is_the_models_docstring(env_settings, db_session, live_card_in_window):
    """Ruling B-I8, quoting `harness/db/models.py::ParlayLegProb`: "once per recorder tick while
    a card is placed or alive and its game is inside the in-progress window"."""
    _call(env_settings, db_session)
    rows = db_session.execute(text(
        "select leg_id, sharp_p, book_p from parlay_leg_probs")).all()
    assert len(rows) == len(live_card_in_window.leg_ids)
    assert all(0 <= r.sharp_p <= 1 for r in rows)


def test_a_proposed_card_writes_nothing(env_settings, db_session, proposed_card_in_window):
    _call(env_settings, db_session)
    assert db_session.execute(text("select count(*) from parlay_leg_probs")).scalar() == 0


def test_a_card_whose_game_has_not_started_writes_nothing(env_settings, db_session,
                                                          live_card_before_kickoff):
    _call(env_settings, db_session)
    assert db_session.execute(text("select count(*) from parlay_leg_probs")).scalar() == 0


def test_a_final_game_writes_nothing(env_settings, db_session, live_card_final):
    _call(env_settings, db_session)
    assert db_session.execute(text("select count(*) from parlay_leg_probs")).scalar() == 0


def test_book_p_comes_from_the_draftkings_row_the_leg_was_priced_from(env_settings, db_session,
                                                                      live_card_in_window):
    _call(env_settings, db_session)
    book_p = db_session.execute(text(
        "select book_p from parlay_leg_probs order by leg_id limit 1")).scalar()
    assert book_p == Decimal("0.4000")      # the fixture's 2.50 decimal price


def test_a_leg_with_no_draftkings_row_gets_a_null_book_p(env_settings, db_session,
                                                         live_card_no_book):
    _call(env_settings, db_session)
    assert db_session.execute(text("select book_p from parlay_leg_probs")).scalar() is None


def test_a_second_tick_in_the_same_second_does_not_duplicate(env_settings, db_session,
                                                             live_card_in_window):
    """`parlay_leg_probs` is keyed `(leg_id, ts)`, so a repeated tick at the same instant is an
    upsert and never a duplicate-key error that would fail the tick."""
    _call(env_settings, db_session)
    _call(env_settings, db_session)
    assert db_session.execute(text("select count(*) from parlay_leg_probs")).scalar() == \
        len(live_card_in_window.leg_ids)


def test_the_writer_never_fails_a_tick(env_settings, db_session, live_card_in_window,
                                       monkeypatch):
    from harness.recorder import tick as tick_module

    # The class attribute, not the module one: `_leg_probs` reads `self._LEG_PROB_ROWS`.
    monkeypatch.setattr(tick_module.Recorder, "_LEG_PROB_ROWS",
                        property(lambda self: (_ for _ in ()).throw(RuntimeError("boom"))))
    ctx = {"warnings": []}
    _call(env_settings, db_session, ctx)
    assert ctx["warnings"]
