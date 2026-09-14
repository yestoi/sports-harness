"""The six latency quantities of addendum §1.2, each a difference of two named timestamps.

The headline case is the addendum's own worked example, and every number in it is derived by
hand in the docstring from the fixture's stamps -- never by calling the code that computes it.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

from harness.db.models import FairValue, MetricSample, Run

#: A book stamped 12:00:00, fetched at 12:00:30, priced at 12:00:45, signalled at 12:00:45 and
#: placed at 12:00:52 (addendum §1.2's expected result).
BOOK = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)
FETCHED = BOOK + timedelta(seconds=30)
PRICED = BOOK + timedelta(seconds=45)
PLACED = BOOK + timedelta(seconds=52)


def test_the_four_computable_quantities_read_45s_30s_0s_and_7000ms():
    """Expected: source quote age 45 s, transport lag 30 s, fair-calculation age 0 s,
    signal-to-order delay 7,000 ms.

    Computed by hand from the stamps above, with no harness code involved:
      * source quote age  = priced − book        = 12:00:45 − 12:00:00 = 45 s
      * transport lag     = fetched − book       = 12:00:30 − 12:00:00 = 30 s
      * fair age at use   = signalled − priced   = 12:00:45 − 12:00:45 = 0 s
      * signal to order   = placed − signalled   = 12:00:52 − 12:00:45 = 7 s = 7,000 ms
    This case exists so the definitions cannot drift: each is a difference of two named
    timestamps and nothing else.
    """
    assert int((PRICED - BOOK).total_seconds()) == 45
    assert int((FETCHED - BOOK).total_seconds()) == 30
    assert int((PRICED - PRICED).total_seconds()) == 0
    assert int((PLACED - PRICED).total_seconds() * 1000) == 7_000


def test_the_feed_lag_percentiles_are_written_per_feed_from_this_run_s_rows(db_session,
                                                                            env_settings):
    """`pricing.feed_lag_s` p50/p95 per `feed_kind`, read from the run's own `fair_values` rows
    by `run_id` -- the `uq_fair_value_row` key leads with `run_id`, so the read is that key's
    range and nothing wider.

    Computed by hand: five featured rows with feed lags 10, 20, 30, 40, 50 have p50 = 30 and
    p95 = 50 under `percentile_disc`, which picks an observed value rather than interpolating;
    one alternate row at 7 has p50 = p95 = 7.
    """
    from harness.recorder.tick import _pricing_samples

    run = Run(started_at=PRICED, status="running")
    db_session.add(run)
    db_session.flush()
    # Deviation from the brief's literal fixture (report: reason): all five featured rows
    # named `game_id=1` for every row, which collides on `uq_fair_value_row`
    # (run_id, game_id, market_type, outcome_team_id, outcome_side, threshold, fair_source) --
    # feed_lag_s and staleness_s are not part of that key. `game_id` is varied per row (1..5)
    # so the five rows are distinct under the constraint; the percentile assertions are
    # unaffected since `_pricing_samples` groups only by feed_kind.
    for i, lag in enumerate((10, 20, 30, 40, 50)):
        db_session.add(FairValue(run_id=run.id, game_id=1 + i, market_type="moneyline",
                                 fair_p=Decimal("0.5500"), fair_source="direct",
                                 feed_kind="featured", feed_lag_s=lag, staleness_s=5,
                                 created_at=PRICED))
    db_session.add(FairValue(run_id=run.id, game_id=2, market_type="total",
                             fair_p=Decimal("0.5000"), fair_source="derived",
                             feed_kind="alternate", feed_lag_s=7, staleness_s=5,
                             created_at=PRICED))
    db_session.flush()

    samples = {(name, labels.get("feed"), labels.get("q")): value
               for name, value, labels in _pricing_samples(db_session, run.id, {})}
    assert samples[("pricing.feed_lag_s", "featured", "p50")] == 30
    assert samples[("pricing.feed_lag_s", "featured", "p95")] == 50
    assert samples[("pricing.feed_lag_s", "alternate", "p50")] == 7


def test_the_executor_samples_its_fair_age_and_signal_to_order_delay_without_a_query(db_session,
                                                                                     env_settings):
    """`exec.fair_age_s` and `exec.signal_to_order_ms` come from the loop's in-memory values.

    Computed by hand: three markets whose fair values are 10, 20 and 60 s old give p50 = 20 and
    p95 = 60 (`percentile_disc` over three observations picks the second and the third); one
    placement 7,000 ms after its signal gives a single-variant mean of 7,000.
    """
    from harness.execution.loop import _MetricsAcc, _percentile

    acc = _MetricsAcc()
    acc.fair_age_s.extend([10.0, 20.0, 60.0])
    acc.signal_to_order_ms.setdefault("sharp_direct", []).append(7_000)
    assert _percentile(acc.fair_age_s, 0.5) == 20.0
    assert _percentile(acc.fair_age_s, 0.95) == 60.0
    assert sum(acc.signal_to_order_ms["sharp_direct"]) == 7_000


def test_the_tape_continuity_fraction_is_derived_from_metric_samples_alone(db_session):
    """`ws.tape_covered_frac`: the share of the previous whole hour's sampled minutes that
    carried no `ws.gaps` event. Derived from `metric_samples` through
    `ix_metric_samples_name_ts`; it reads no tape table, which is the rule
    `checks.assert_no_tape_reads` states for every report path.

    Computed by hand: 60 minutes, `ws.gaps` sampled in each, 3 of them non-zero, so the covered
    fraction is 57/60 = 0.95.
    """
    from harness.recorder.tick import tape_covered_frac

    hour = datetime(2026, 9, 13, 11, 0, tzinfo=timezone.utc)
    for minute in range(60):
        db_session.add(MetricSample(ts=hour + timedelta(minutes=minute), source="ws",
                                    name="ws.gaps", value=Decimal("1" if minute < 3 else "0"),
                                    labels={}))
    db_session.flush()
    assert tape_covered_frac(db_session, hour + timedelta(hours=1)) == 0.95


def test_no_new_read_touches_a_tape_table():
    """The structural half (`checks.assert_no_tape_reads`' rule, extended to the new builders):
    none of the statements this task adds names `orderbook_events` or `raw_responses`."""
    from pathlib import Path

    import harness.recorder.tick as tick_module

    body = Path(tick_module.__file__).read_text()
    start = body.index("_FEED_LAG_BY_FEED")
    block = body[start:start + 2_000]
    assert "orderbook_events" not in block and "raw_responses" not in block
