"""The U4 caps as behaviour: the cost model, the reservation, the release, and two workers.

Every price in here is a literal from the addendum, not a value read back out of the module: a
test that read the constant would pass against any constant at all, and this file is where the
phase's money arithmetic is pinned.
"""
import threading
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from harness.research.spend import (BudgetRefused, SEARCH_USD, Usage, WORST_CASE_INPUT_TOKENS,
                                    WORST_CASE_OUTPUT_TOKENS, WORST_CASE_SEARCHES,
                                    chicago_day, cost_usd, iso_week_bounds, release_spend,
                                    reserve_spend, spend_state, worst_case_usd)

OPUS, SONNET = "claude-opus-5", "claude-sonnet-5"
#: 2026-09-15 03:00 UTC is 2026-09-14 22:00 in America/Chicago: the day boundary the caps use is
#: the owner's, not UTC's, and this instant is on the wrong side of UTC midnight on purpose.
NOW = datetime(2026, 9, 15, 3, 0, tzinfo=timezone.utc)


def _settings(env_settings, daily="25", weekly="150"):
    return env_settings.model_copy(update={"veto_daily_usd_cap": Decimal(daily),
                                           "veto_weekly_usd_cap": Decimal(weekly)})


# --- the cost model -------------------------------------------------------------------------

def test_the_day_is_an_america_chicago_day():
    assert chicago_day(NOW) == date(2026, 9, 14)
    assert chicago_day(datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)) == date(2026, 9, 15)


def test_the_day_flips_at_america_chicago_midnight_in_both_offsets():
    """23:59 and 00:01 CT, once in CDT and once in CST.

    The pair above straddles CT midnight by hours, so a fixed -5 or -6 offset would satisfy it
    just as well as the zone. These four are one minute either side of the flip, and the two
    seasons disagree about what 05:00-06:00 UTC means: only a real America/Chicago zone puts
    both winter instants and both summer instants on the right day.
    """
    utc = timezone.utc
    # CDT, UTC-5: 2026-09-14 23:59 CT and 2026-09-15 00:01 CT.
    assert chicago_day(datetime(2026, 9, 15, 4, 59, tzinfo=utc)) == date(2026, 9, 14)
    assert chicago_day(datetime(2026, 9, 15, 5, 1, tzinfo=utc)) == date(2026, 9, 15)
    # CST, UTC-6: 2026-01-15 23:59 CT and 2026-01-16 00:01 CT. 05:59 UTC is still the 15th here
    # and was already the 15th's *next* day in September, which is the assertion that bites.
    assert chicago_day(datetime(2026, 1, 16, 5, 59, tzinfo=utc)) == date(2026, 1, 15)
    assert chicago_day(datetime(2026, 1, 16, 6, 1, tzinfo=utc)) == date(2026, 1, 16)


def test_the_week_is_the_iso_week_of_that_day():
    assert iso_week_bounds(date(2026, 9, 16)) == (date(2026, 9, 14), date(2026, 9, 20))


def test_opus_list_price():
    """$5.00 / $25.00 per MTok: 1,000,000 in and 1,000,000 out is $30.00 exactly."""
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=0,
                  cache_write_tokens=0, searches=0)
    assert cost_usd(OPUS, usage) == Decimal("30.000000")


def test_sonnet_list_price():
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=0,
                  cache_write_tokens=0, searches=0)
    assert cost_usd(SONNET, usage) == Decimal("12.000000")


def test_cache_reads_are_a_tenth_and_writes_are_one_and_a_quarter():
    reads = Usage(0, 0, cache_read_tokens=1_000_000, cache_write_tokens=0, searches=0)
    writes = Usage(0, 0, cache_read_tokens=0, cache_write_tokens=1_000_000, searches=0)
    assert cost_usd(OPUS, reads) == Decimal("0.500000")
    assert cost_usd(OPUS, writes) == Decimal("6.250000")


def test_searches_are_billed_on_top_of_tokens():
    """A token-only cost model under-reports a search call; the searches come from the
    `server_tool_use` blocks the client counts."""
    assert cost_usd(OPUS, Usage(0, 0, 0, 0, searches=3)) == Decimal("0.030000")
    assert SEARCH_USD == Decimal("0.01")


def test_an_unknown_model_raises_rather_than_costing_nothing():
    with pytest.raises(KeyError):
        cost_usd("claude-made-up", Usage(1, 1, 0, 0, 0))


def test_the_worst_case_constants_are_the_addendum_s():
    """4,096, not the addendum's opening 2,000: raised in review round 1 (Important 4) to the
    controller's proving-call ceiling, so T10/T15/T18 can pass this same constant as
    `max_tokens` and the reservation and the request cannot part.

    60,000, not the addendum's opening 30,000 (fix 41, journal 112): the first live annotator
    calls on the corrected schema measured 46,528 input tokens, past the old 30,000 the
    reservation was priced against.
    """
    assert (WORST_CASE_INPUT_TOKENS, WORST_CASE_OUTPUT_TOKENS, WORST_CASE_SEARCHES) == \
        (60_000, 4_096, 3)
    # 60,000 in at $5/MTok = $0.30; 4,096 out at $25/MTok = $0.1024; 3 searches = $0.03.
    assert worst_case_usd(OPUS, 3) == Decimal("0.432400")
    # 60,000 in at $2/MTok = $0.12; 4,096 out at $10/MTok = $0.04096; 3 searches = $0.03.
    assert worst_case_usd(SONNET, 3) == Decimal("0.190960")


# --- the reservation ------------------------------------------------------------------------

def test_a_reservation_writes_usd_reserved_for_every_model(db_session, env_settings):
    reservation = reserve_spend(db_session, NOW, _settings(env_settings), "veto", [OPUS, SONNET])
    rows = {(r.kind, r.model): r for r in db_session.execute(
        text("select kind, model, usd, usd_reserved from research_spend")).all()}
    assert set(rows) == {("veto", OPUS), ("veto", SONNET)}
    assert rows[("veto", OPUS)].usd_reserved == Decimal("0.4324")
    assert rows[("veto", SONNET)].usd_reserved == Decimal("0.1910")
    assert reservation.day == date(2026, 9, 14)
    assert reservation.per_model == {OPUS: Decimal("0.432400"), SONNET: Decimal("0.190960")}


def test_the_release_swaps_the_reservation_for_the_actual(db_session, env_settings):
    reservation = reserve_spend(db_session, NOW, _settings(env_settings), "veto", [OPUS, SONNET])
    actual = release_spend(db_session, reservation, {
        OPUS: Usage(10_000, 500, 4_000, 0, 2),
        SONNET: Usage(10_000, 500, 4_000, 0, 2),
    })
    rows = {r.model: r for r in db_session.execute(text(
        "select model, calls, input_tokens, searches, usd, usd_reserved from research_spend")).all()}
    for row in rows.values():
        assert row.usd_reserved == Decimal("0")
        assert row.calls == 1 and row.input_tokens == 10_000 and row.searches == 2
    # opus: 10,000 in = $0.05; 500 out = $0.0125; 4,000 cache reads = $0.002; 2 searches = $0.02
    assert rows[OPUS].usd == Decimal("0.0845")
    # sonnet: $0.02 + $0.005 + $0.0008 + $0.02
    assert rows[SONNET].usd == Decimal("0.0458")
    assert actual == Decimal("0.130300")


def test_a_failed_call_releases_the_reservation_with_a_zero_usage(db_session, env_settings):
    """The `finally` path. A reservation that is never released eats the cap for the rest of
    the day, so a call that raised before it produced a usage still returns its reservation."""
    reservation = reserve_spend(db_session, NOW, _settings(env_settings), "veto", [OPUS, SONNET])
    release_spend(db_session, reservation, {})
    totals = db_session.execute(text(
        "select coalesce(sum(usd), 0), coalesce(sum(usd_reserved), 0) from research_spend")).first()
    assert totals == (Decimal("0.0000"), Decimal("0.0000"))


def test_the_daily_cap_refuses_the_call_that_would_cross_it(db_session, env_settings):
    settings = _settings(env_settings, daily="0.50")
    reserve_spend(db_session, NOW, settings, "veto", [OPUS])       # 0.4324 <= 0.50
    with pytest.raises(BudgetRefused) as caught:
        reserve_spend(db_session, NOW, settings, "veto", [OPUS])   # 0.8648 > 0.50
    assert caught.value.cap == "daily"
    assert caught.value.limit == Decimal("0.50")


def test_the_weekly_cap_refuses_independently_of_the_daily_one(db_session, env_settings):
    settings = _settings(env_settings, daily="25", weekly="0.50")
    reserve_spend(db_session, NOW, settings, "veto", [OPUS])
    with pytest.raises(BudgetRefused) as caught:
        reserve_spend(db_session, NOW, settings, "veto", [OPUS])
    assert caught.value.cap == "weekly"


def test_the_cap_covers_every_kind_not_just_the_veto(db_session, env_settings):
    """0.3: the caps are totals across the primary, the shadow, the annotator and the parlay
    rationale. An annotator call eats the veto's budget and must."""
    settings = _settings(env_settings, daily="0.50")
    reserve_spend(db_session, NOW, settings, "annotate", [OPUS])
    with pytest.raises(BudgetRefused):
        reserve_spend(db_session, NOW, settings, "veto", [OPUS])


def test_a_refused_reservation_reserves_and_spends_nothing(db_session, env_settings):
    """`_ensure_rows` runs before the cap check, so a refusal can leave zero-valued rows behind.
    That is deliberate and harmless -- they are the day's accounting rows and the next
    reservation needs them -- but the money columns must both be untouched, which is what this
    asserts. The name says "reserves and spends nothing", not "writes nothing"."""
    settings = _settings(env_settings, daily="0.10")
    with pytest.raises(BudgetRefused):
        reserve_spend(db_session, NOW, settings, "veto", [OPUS, SONNET])
    totals = db_session.execute(text(
        "select coalesce(sum(usd_reserved), 0), coalesce(sum(usd), 0) from research_spend")).first()
    assert totals == (Decimal("0"), Decimal("0"))


def test_yesterday_s_spend_does_not_count_against_today(db_session, env_settings):
    settings = _settings(env_settings, daily="0.50", weekly="150")
    earlier = datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc)   # 2026-09-13 CT, the day before
    release_spend(db_session, reserve_spend(db_session, earlier, settings, "veto", [OPUS]),
                  {OPUS: Usage(1_000_000, 0, 0, 0, 0)})           # $5.00 on the 13th
    reserve_spend(db_session, NOW, settings, "veto", [OPUS])      # the 14th is still empty
    assert spend_state(db_session, NOW, settings).day_reserved == Decimal("0.4324")


def test_spend_state_reports_the_day_the_week_and_dormancy(db_session, env_settings):
    settings = _settings(env_settings, daily="0.50")
    reserve_spend(db_session, NOW, settings, "veto", [OPUS])
    state = spend_state(db_session, NOW, settings)
    assert state.day_reserved == Decimal("0.4324") and state.day_usd == Decimal("0")
    assert state.daily_cap == Decimal("0.50") and state.weekly_cap == Decimal("150")
    assert state.dormant is True          # another opus pair would cross the daily cap
    assert spend_state(db_session, NOW, _settings(env_settings)).dormant is False


#: Widens the read-then-check window so two racing threads reliably overlap inside it. Without
#: this, the natural window is microseconds and the two threads never interleave -- the un-widened
#: version of this test passes even with the lock statement deleted, which means it wasn't
#: actually testing the lock (review round 1, Important 1).
#:
#: `pg_sleep` must be cross-joined in, not tucked into an unreferenced CTE: Postgres prunes a CTE
#: nothing else refers to -- confirmed with `EXPLAIN`, `with s as (select pg_sleep(0.75)) select
#: ...` and even `with s as materialized (...)` both return in ~1ms, not 750ms, because the outer
#: query never scans `s`. A cross join is scanned, so the sleep is unconditionally paid.
_SLOW_DAY_TOTAL = text("select coalesce(sum(usd + usd_reserved), 0) from research_spend, "
                       "(select pg_sleep(0.75)) s where day = :day")
_NOOP_LOCK = text("select 1")


def _prewarm(settings, factory):
    """Create the day's (day, kind, model) row and commit it before the race starts.

    Without this, both threads' first `_ensure_rows` call is an `INSERT ... ON CONFLICT DO
    NOTHING` racing on the same brand-new key, and Postgres's speculative-insertion protocol
    makes the second inserter block until the first's transaction resolves -- an incidental
    serialization that has nothing to do with `_LOCK` and would make the negative-control test
    pass by accident. Pre-creating and committing the row means both threads' inserts see an
    already-committed conflict and return immediately, so only the advisory lock (or its
    deliberate absence) governs the race.
    """
    with factory() as session:
        release_spend(session, reserve_spend(session, NOW, settings, "veto", [OPUS]), {})
        session.commit()


def _race(settings, factory, start):
    outcomes = []

    def attempt():
        with factory() as session:
            start.wait(timeout=10)
            try:
                reserve_spend(session, NOW, settings, "veto", [OPUS])
                session.commit()
                outcomes.append("reserved")
            except BudgetRefused:
                session.rollback()
                outcomes.append("refused")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    return outcomes


def test_two_workers_racing_one_slot_produce_exactly_one_reservation(
        db_session, env_settings, monkeypatch):
    """A-C3 hole 1, as a test. Two sessions on two connections, released together, against a cap
    that fits exactly one reservation but not two. The day-total read is slowed so the two
    threads' read-then-check windows overlap; the advisory lock still serializes them and
    exactly one reservation survives."""
    import harness.research.spend as spend_module
    settings = _settings(env_settings, daily="0.50")   # one opus reservation (0.4324) fits, two do not
    factory = sessionmaker(bind=db_session.get_bind().engine, expire_on_commit=False)
    db_session.commit()               # make the empty table visible to the other connections
    _prewarm(settings, factory)
    monkeypatch.setattr(spend_module, "_DAY_TOTAL", _SLOW_DAY_TOTAL)

    outcomes = _race(settings, factory, threading.Barrier(2))

    assert sorted(outcomes) == ["refused", "reserved"]
    with factory() as session:
        assert session.execute(text(
            "select coalesce(sum(usd_reserved), 0) from research_spend")).scalar() == \
            Decimal("0.4324")


def test_without_the_lock_two_workers_both_reserve(db_session, env_settings, monkeypatch):
    """The negative control for the test above: same widened window, same cap, same pre-warmed
    row, but the lock statement is a no-op. Both threads read the empty total, both pass the
    check, and the cap is broken -- proving the lock in the previous test is load-bearing, not
    coincidental (review round 1, Important 1)."""
    import harness.research.spend as spend_module
    settings = _settings(env_settings, daily="0.50")
    factory = sessionmaker(bind=db_session.get_bind().engine, expire_on_commit=False)
    db_session.commit()
    _prewarm(settings, factory)
    monkeypatch.setattr(spend_module, "_DAY_TOTAL", _SLOW_DAY_TOTAL)
    monkeypatch.setattr(spend_module, "_LOCK", _NOOP_LOCK)

    outcomes = _race(settings, factory, threading.Barrier(2))

    assert outcomes == ["reserved", "reserved"]
    with factory() as session:
        assert session.execute(text(
            "select coalesce(sum(usd_reserved), 0) from research_spend")).scalar() == \
            Decimal("0.8648")
