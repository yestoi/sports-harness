"""`opportunity_episodes` and `intent_episodes`: 6C's two deferred funnel units, delivered as
episodes (addendum §0.11, §1.7, decision D6).

The headline expectation is computed by hand in its docstring and is deliberately a case where
the episode count and the event count **disagree** -- that disagreement is the whole reason both
are reported.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from harness.db.models import IntentEpisode, OpportunityEpisode, Signal
from harness.ops import episodes
from harness.strategy import pipeline as pipeline_module
from harness.strategy.variants import load_variants, register_variants
from tests.test_exec_loop import NOW as EXEC_NOW
from tests.test_exec_loop import Clock, _book2, make_executor, refresh, world  # noqa: F401
from tests.test_pipeline import NOW as PIPELINE_NOW
from tests.test_pipeline import VARIANTS_DIR, _seed

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
KEY = ("aaaaaaaaaaaa", 4242, "yes")


def test_four_sightings_across_a_two_hour_hole_are_two_episodes(db_session):
    """Expected: 2 opportunity episodes and 4 candidate signal rows (§1.7's expected result).

    Computed by hand: one market scored candidate at 12:00, 12:02 and 12:04 on a 120 s cadence,
    and again at 14:00. The gap rule is `max(600, 3 x 120) = 600 s`, so the three sightings two
    minutes apart extend one episode and the 116-minute hole -- far beyond 600 s -- opens a
    second. The two units disagree by construction, which is the point of reporting both.
    """
    rule = episodes.gap_rule_s(120)
    assert rule == 600
    for minutes in (0, 2, 4, 120):
        episodes.upsert(db_session, OpportunityEpisode, [KEY], NOW + timedelta(minutes=minutes),
                        rule, kind="opportunity")
        db_session.commit()
    rows = db_session.query(OpportunityEpisode).order_by(OpportunityEpisode.started_at).all()
    assert len(rows) == 2
    assert rows[0].started_at == NOW and rows[0].ended_at == NOW + timedelta(minutes=4)
    assert rows[0].n_signals == 3
    assert rows[1].started_at == NOW + timedelta(minutes=120) and rows[1].n_signals == 1
    assert {row.gap_rule_s for row in rows} == {600}


def test_six_hours_of_continuous_sightings_are_one_episode(db_session):
    """Review Important 1: openness is a property of `ended_at`, not of `started_at`.

    Computed by hand: one market scored candidate every 120 s for six hours -- sightings at
    0, 2, 4, ..., 358 minutes, which is 180 of them -- under the 600 s rule. No hole anywhere
    reaches 600 s, so §1.7(b) makes that **one** episode, from the first sighting to the last,
    carrying `n_signals = 180`.

    The read that finds the open episode must therefore be bounded on `ended_at` (the last
    sighting) and ride its own index. Bounded on `started_at` instead, this episode fell out of
    the window two rule widths after it opened and was reopened as a new row every ~1,320 s:
    17 rows of 11 sightings each, where the unit means 1.
    """
    rule = episodes.gap_rule_s(120)
    sightings = list(range(0, 360, 2))
    assert len(sightings) == 180
    for minutes in sightings:
        episodes.upsert(db_session, OpportunityEpisode, [KEY], NOW + timedelta(minutes=minutes),
                        rule, kind="opportunity")
    db_session.commit()

    rows = db_session.query(OpportunityEpisode).all()
    assert len(rows) == 1
    assert rows[0].n_signals == 180
    assert rows[0].started_at == NOW
    assert rows[0].ended_at == NOW + timedelta(minutes=358)


def test_the_gap_rule_is_stored_on_the_row_so_a_recount_needs_no_new_data(db_session):
    """D6: "a different gap rule would group differently; the rule is stored per row so a
    re-count is possible without new data"."""
    episodes.upsert(db_session, OpportunityEpisode, [KEY], NOW, episodes.gap_rule_s(900),
                    kind="opportunity")
    db_session.commit()
    assert db_session.query(OpportunityEpisode).one().gap_rule_s == 2700


def test_the_write_is_one_read_and_one_upsert_however_many_keys(db_session):
    """Ruling I7: one multi-row upsert per run, not one statement per key. Counted off the wire,
    because "one statement" is the property that keeps ~145,000 upserts a day affordable."""
    from sqlalchemy import event as sa_event

    statements = []
    engine = db_session.get_bind()

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().lower().split()[0])

    keys = [("aaaaaaaaaaaa", market_id, "yes") for market_id in range(50)]
    sa_event.listen(engine, "before_cursor_execute", capture)
    try:
        episodes.upsert(db_session, OpportunityEpisode, keys, NOW, 600, kind="opportunity")
    finally:
        sa_event.remove(engine, "before_cursor_execute", capture)
    assert statements.count("insert") == 1
    assert statements.count("select") == 1
    assert db_session.query(OpportunityEpisode).count() == 50


def test_the_cap_bounds_the_write_and_says_so(db_session):
    """Ruling I7/D3's sibling cap. Computed by hand: 2,100 keys offered, `EPISODE_UPSERT_CAP` =
    2,048 written, 52 dropped, one `episodes.truncated` sample carrying 52."""
    keys = [("aaaaaaaaaaaa", market_id, "yes") for market_id in range(2_100)]
    written = episodes.upsert(db_session, OpportunityEpisode, keys, NOW, 600, kind="opportunity")
    db_session.commit()
    assert written == episodes.EPISODE_UPSERT_CAP == 2_048
    assert db_session.execute(text(
        "select value from metric_samples where name = 'episodes.truncated'")).scalar() == 52


def test_an_intent_episode_carries_n_intents_and_the_same_shape(db_session):
    """§1.7(c): the same shape keyed on `(variant_id, venue_market_id, side)` from `intents`,
    written by the executor where it writes the intent."""
    rule = episodes.gap_rule_s(15)
    assert rule == 600      # max(600, 3 x 15): the floor, not the period
    episodes.upsert(db_session, IntentEpisode, [KEY], NOW, rule, kind="intent")
    episodes.upsert(db_session, IntentEpisode, [KEY], NOW + timedelta(seconds=30), rule,
                    kind="intent")
    db_session.commit()
    row = db_session.query(IntentEpisode).one()
    assert row.n_intents == 2 and row.ended_at == NOW + timedelta(seconds=30)


def test_the_invariant_queries_of_addendum_2_return_zero(db_session):
    """§2's right-hand column, run verbatim against real rows."""
    episodes.upsert(db_session, OpportunityEpisode, [KEY], NOW, 600, kind="opportunity")
    db_session.commit()
    assert db_session.execute(text(
        "select count(*) from opportunity_episodes where ended_at < started_at or n_signals < 1"
    )).scalar() == 0


# --- wiring: the two write sites, exercised through their real callers ------------------------


def test_the_pricing_pass_writes_one_episode_per_candidate_key_under_its_cadence_rule(
        env_settings, db_session):
    """§1.7(b)'s write site, through `price_and_signal` itself: an episode table nothing writes
    to would leave every case above green while the funnel read 0.

    Computed by hand from `tests/test_pipeline`'s seeded tick: one episode row per
    `(variant_id, venue_market_id, side)` the run scored `candidate`, and no row for any other
    key. The cadence passed is 120 s, so the stored rule is `max(600, 3 x 120) = 600` -- the
    floor, and the proof that the rule travels from the caller rather than being assumed.
    """
    _game, run, _markets = _seed(db_session)
    register_variants(db_session, load_variants(VARIANTS_DIR), PIPELINE_NOW, prune=True)
    db_session.commit()

    pipeline_module.price_and_signal(db_session, run.id, PIPELINE_NOW, env_settings,
                                     budget_s=600, cadence_s=120)
    db_session.commit()

    candidates = {(row.variant_id, row.venue_market_id, row.side)
                  for row in db_session.query(Signal).filter(
                      Signal.decision == "candidate").all()}
    assert candidates, "the fixture scored no candidate, so this would prove nothing"
    rows = db_session.query(OpportunityEpisode).all()
    assert {(r.variant_id, r.venue_market_id, r.side) for r in rows} == candidates
    assert {r.gap_rule_s for r in rows} == {600}
    assert {r.n_signals for r in rows} == {1}
    assert all(r.started_at == r.ended_at == PIPELINE_NOW for r in rows)


def test_the_executor_writes_an_intent_episode_where_it_writes_the_intent(
        env_settings, db_session, world):
    """§1.7(c)'s write site, through a real `Executor.step()`. Computed by hand: the `world`
    fixture leaves exactly one candidate signal (T2's), so the loop writes one intent and one
    episode, whose rule is `max(600, 3 x exec_period_s = 15) = 600`."""
    _book2(db_session, EXEC_NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, Clock(EXEC_NOW))

    executor.step()
    refresh(db_session)

    rows = db_session.query(IntentEpisode).all()
    assert len(rows) == 1
    assert rows[0].n_intents == 1 and rows[0].gap_rule_s == 600
    assert rows[0].started_at == rows[0].ended_at == EXEC_NOW


def test_a_replay_executor_writes_no_intent_episode(env_settings, db_session, world):
    """The replay guard `test_replay_writes_no_telemetry` states for telemetry, stated for this
    write: an episode is a live unit, and a replay that wrote one would inflate every count in
    the window it replays."""
    _book2(db_session, EXEC_NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, Clock(EXEC_NOW), replay=True)

    executor.step()
    refresh(db_session)

    assert db_session.query(IntentEpisode).count() == 0
