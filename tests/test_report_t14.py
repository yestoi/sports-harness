"""Table t14, the coverage contract (addendum §0.10, §1.7(a), §1.7(d)).

t13 stays exactly as 6C built it; t14 is a new table rendered after it, and every row comes from
`coverage_samples` and the bounded metric reads -- never from `signals` (ruling I3).

**Each fixture seeds** ISO week 38 of 2026 (Monday 2026-09-14 05:00 UTC to 2026-09-21 05:00 UTC)
at `TS`, two hours before `NOW`, which is inside the week and before the settle margin. Two
active variants: the gate variant `sharp_direct` (the `Settings.gate_variant` default) and the
active primary `sharp_two_sided`. Every expectation is computed by hand in its docstring from the
seeded counts.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from harness.db.models import (CoverageSample, MarketGapSnapshot, MetricSample, OperatorEvent,
                               Run, Signal, StrategyVariant)
from harness.report.tables import (PLACEHOLDER, RENDER_ORDER, TABLE_KEYS, T14_COVERAGE_MIN,
                                   _T14_COLUMNS, weekly_tables)

NOW = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)
YEAR, WEEK = 2026, 38
#: Inside week 38 and older than `T14_SETTLE_MARGIN_S`, so a scheduled row seeded here is past
#: its cadence period at `NOW` and counts as unclosed if nothing closed it.
TS = NOW - timedelta(hours=2)

GATE, PRIMARY = "g00000000001", "p00000000001"
GATE_NAME, PRIMARY_NAME = "sharp_direct", "sharp_two_sided"


def _variants(session):
    """The gate variant (`Settings.gate_variant` is `sharp_direct`) and the active primary."""
    session.add(StrategyVariant(variant_id=GATE, name=GATE_NAME, tier="secondary",
                                config_json={}, registered_at=TS, active=True))
    session.add(StrategyVariant(variant_id=PRIMARY, name=PRIMARY_NAME, tier="primary",
                                config_json={}, registered_at=TS, active=True))
    session.flush()


def _coverage(session, variant_id, outcome, n, ts=None, overdue_ms=None, run_id=1):
    session.add(CoverageSample(run_id=run_id, ts=ts or TS, domain="evaluation", sport="nfl",
                               ttk_bucket="20m_3h", feed="featured", market_type="moneyline",
                               variant_id=variant_id, outcome=outcome, n=n,
                               overdue_ms=overdue_ms))


def _t14(db_session, env_settings, now=NOW):
    """t14 as a `{item: (value, unit, note)}` map, the shape every assertion below reads."""
    table = weekly_tables(db_session, YEAR, WEEK, env_settings, now=now)["t14"]
    assert table.columns == _T14_COLUMNS
    return {row[0]: (row[1], row[2], row[3]) for row in table.rows}


def test_t14_is_registered_after_t13_and_keyed_on_item():
    """§0.10: `TABLE_KEYS` gains `t14`; `RENDER_ORDER` places it after `t13`; `IDENTITY_COLUMNS`
    maps it to `item` through `_T14_COLUMNS`, which
    `test_identity_columns_match_every_table_s_real_first_column` requires."""
    from harness.report.render_for_model import IDENTITY_COLUMNS

    assert "t14" in TABLE_KEYS
    assert RENDER_ORDER[:2] == ("t13", "t14")
    assert _T14_COLUMNS[0] == "item"
    assert IDENTITY_COLUMNS["t14"] == "item"


def test_t14_reports_completed_over_scheduled_per_variant(db_session, env_settings):
    """Computed by hand from the seeded rows: the gate variant has 8 completed of 10 scheduled,
    so its coverage reads 0.80 -- below the declared tolerance of 0.95, which the row states
    beside it rather than hiding.

    Computed by hand: run 1 records 9 scheduled units and 8 completed for the gate variant, run
    2 records the tenth scheduled unit and nothing closes it, so the week is 8 completed of 10
    scheduled = 0.80. The primary is seeded 3 completed of 4 scheduled = 0.75 in the same week,
    so the two rows are per variant and neither pools the other. The `scheduled units` row reads
    the **recorded** 10, never the 8 that completed (§1.1's denominator rule), and run 2's cell
    is the one unexplained omission the reconciliation counts: 1.
    """
    _variants(db_session)
    _coverage(db_session, GATE, "completed", 8)
    _coverage(db_session, GATE, "scheduled", 9)
    _coverage(db_session, PRIMARY, "completed", 3)
    _coverage(db_session, PRIMARY, "scheduled", 4)
    # A second run whose scheduled cell no completion row ever closed: run 2's cell is the one
    # unexplained omission, and run 1's is closed by the `completed` row above.
    _coverage(db_session, GATE, "scheduled", 1, run_id=2)
    db_session.flush()

    rows = _t14(db_session, env_settings)
    gate_row = rows[f"coverage, gate variant, {GATE_NAME}"]
    assert gate_row[0] == 0.8 and gate_row[1] == "share"
    assert "8 completed of 10 scheduled" in gate_row[2]
    assert str(T14_COVERAGE_MIN) in gate_row[2] and "applied to" in gate_row[2]
    assert rows[f"coverage, primary, {PRIMARY_NAME}"][0] == 0.75
    scheduled_row = rows["scheduled units, gate variant"]
    assert scheduled_row[0] == 10 and scheduled_row[1] == "units"
    assert "never a count of what completed" in scheduled_row[2]
    assert rows["unexplained omissions"][0] == 1
    assert rows["unexplained omissions"][1] == "cells"


def test_t14_prints_the_three_denominators_and_names_the_one_shares_are_over(db_session,
                                                                            env_settings):
    """§3 row 6: `total_runs`, `non_skipped_runs` and `priced_runs` for the week, and the
    sentence that every exhaustion share is over `priced_runs`.

    Computed by hand from the five seeded runs: 5 total, of which 1 is `skipped` (4 non-skipped)
    and 3 carry a `notes.pricing` block (3 priced). One of those three records
    `budget_exhausted`, so the budget-exhausted share is 1/3 = 0.3333..., over `priced_runs` and
    over neither of the other two denominators.
    """
    _variants(db_session)
    priced = {"pricing": {"gaps": 1, "budget_exhausted": False}}
    exhausted = {"pricing": {"gaps": 1, "budget_exhausted": True}}
    db_session.add(Run(started_at=TS, status="ok", notes=priced))
    db_session.add(Run(started_at=TS, status="ok", notes=priced))
    db_session.add(Run(started_at=TS, status="ok", notes=exhausted))
    db_session.add(Run(started_at=TS, status="ok", notes={}))
    db_session.add(Run(started_at=TS, status="skipped", notes={}))
    db_session.flush()

    rows = _t14(db_session, env_settings)
    assert rows["total runs"][0] == 5 and rows["total runs"][1] == "runs"
    assert rows["non-skipped runs"][0] == 4
    assert rows["priced runs"][0] == 3
    assert rows["budget-exhausted share"][0] == 1 / 3
    assert "priced_runs" in rows["total runs"][2]
    assert "of 3 priced runs" in rows["budget-exhausted share"][2]


def test_t14_says_when_the_run_cap_bound_and_prints_no_share_it_cannot_compute(
        db_session, env_settings, monkeypatch):
    """Task 7's carry-forward, as a call-site check: when `total_runs` equals
    `COVERAGE_RUN_CAP` the three denominators describe the newest capped set and the row says so,
    and an exhaustion share over zero priced runs is the null cell, never 0.0.

    Computed by hand: with no run at all the share is `None` and renders `--`; with the cap
    monkeypatched to 2 and two runs seeded, `total runs` reads 2 and the note gains the cap
    sentence naming that number.
    """
    from harness.report import tables as tables_module

    _variants(db_session)
    assert _t14(db_session, env_settings)["budget-exhausted share"][0] == PLACEHOLDER

    db_session.add(Run(started_at=TS, status="ok", notes={}))
    db_session.add(Run(started_at=TS, status="ok", notes={}))
    db_session.flush()
    monkeypatch.setattr(tables_module, "COVERAGE_RUN_CAP", 2)
    total = _t14(db_session, env_settings)["total runs"]
    assert total[0] == 2 and "newest 2 runs" in total[2] and "the cap bound" in total[2]


def test_t14_sources_the_no_fair_count_from_coverage_and_not_from_signals(db_session,
                                                                         env_settings):
    """Ruling I3 and fix 55's coverage half. Those 54,435 rows a variant carry
    `rejection_reason = has_fair` and `fair_p` null, and they are produced by exactly the
    stage-6 pass Task 5 removed -- so a t14 that counted them from `signals` would read ~0 for
    six of the seven variants the day after the deploy. The count comes from
    `coverage_samples`' `no_fair` outcome and the split from `pricing.no_fair`.

    Computed by hand: two `no_fair` coverage rows of 30,000 and 24,435 units for the gate
    variant sum to 54,435, and the `pricing.no_fair` samples split the same total into
    40,000 `unmapped_market_type` and 14,435 `no_sharp_line`. The one stored `has_fair`
    signal row seeded beside them is counted nowhere: t14 issues no statement over `signals`.
    """
    _variants(db_session)
    _coverage(db_session, GATE, "no_fair", 30_000, overdue_ms=10)
    _coverage(db_session, GATE, "no_fair", 24_435, overdue_ms=10)
    db_session.add(MetricSample(ts=TS, source="recorder", name="pricing.no_fair",
                                value=Decimal("40000"),
                                labels={"reason": "unmapped_market_type"}))
    db_session.add(MetricSample(ts=TS, source="recorder", name="pricing.no_fair",
                                value=Decimal("14435"), labels={"reason": "no_sharp_line"}))
    # The population ruling I3 refuses to count: a stored rejection on `has_fair`.
    db_session.add(Signal(run_id=1, variant_id=GATE, gap_snapshot_id=1, venue_market_id=7,
                          side="yes", fair_p=None, decision="rejected",
                          rejection_reason="has_fair", labels={}, created_at=TS))
    db_session.flush()

    rows = _t14(db_session, env_settings)
    assert rows[f"markets with no fair value, {GATE_NAME}"][0] == 54_435
    assert rows["no fair, reason unmapped_market_type"][0] == 40_000
    assert rows["no fair, reason no_sharp_line"][0] == 14_435
    assert "never from `signals`" in rows["no fair, reason no_sharp_line"][2]
    # Structural, because the number above would be identical if the statement also read
    # `signals` on a fixture this small: t14's own SQL never names that table (ruling I3).
    import re

    from harness.report import tables as tables_module

    source = Path(tables_module.__file__).read_text()
    block = source.split("# --- table 14", 1)[1].split("# --- entry point", 1)[0]
    statements = " ".join(re.findall(r'text\("""(.*?)"""\)', block, flags=re.S)).lower()
    assert statements, "t14 defines no SQL, so this assertion would be checking nothing"
    assert "signals" not in statements
    for name in ("fair_values", "orderbook_events", "venue_trades", "odds_snapshots"):
        assert name not in statements, name


def test_t14_carries_the_measurement_boundary_note(db_session, env_settings):
    """Ruling I11: the deploy instant is printed on t14 with `rescore_suppressed` named as the
    bridge, so no reader compares a rejected-row series across it silently.

    Computed by hand: the newest `deploy` operator event inside the week is the 15:30 one, not
    the 13:00 one, so the row prints `2026-09-14T15:30:00+00:00`; its note is
    `RESCORE_BOUNDARY_NOTE` itself.
    """
    from harness.strategy.pipeline import RESCORE_BOUNDARY_NOTE

    _variants(db_session)
    empty = _t14(db_session, env_settings)
    assert empty["rejected-signal boundary"][0] == PLACEHOLDER

    db_session.add(OperatorEvent(ts=TS, kind="deploy", summary="6d deploy", ref={}))
    db_session.add(OperatorEvent(ts=TS + timedelta(hours=2, minutes=30), kind="deploy",
                                 summary="6d deploy, second recipe", ref={}))
    db_session.add(OperatorEvent(ts=TS + timedelta(hours=3), kind="note", summary="not a deploy",
                                 ref={}))
    db_session.flush()

    rows = _t14(db_session, env_settings, now=NOW + timedelta(hours=4))
    boundary = rows["rejected-signal boundary"]
    assert boundary[0] == "2026-09-14T15:30:00+00:00"
    assert boundary[1] == "timestamp"
    assert boundary[2] == RESCORE_BOUNDARY_NOTE
    assert "rescore_suppressed" in boundary[2]


def test_the_recorder_writes_the_no_fair_reason_split_this_table_reads(db_session):
    """The producer of the rows above, so t14's reason split cannot read a family nothing writes
    (§0.12, §1.7(d)).

    Computed by hand from four gap rows on one run: two with `fair_p` null and
    `no_fair_reason = 'unmapped_market_type'`, one with `fair_p` null and no reason at all
    (which the statement reports as `unknown`, never drops), and one priced row, which is not a
    missing fair value and is counted nowhere. The read is bounded by `run_id` on
    `uq_gap_run_market` and names no signal table (ruling I3).
    """
    from harness.recorder.tick import _pricing_samples

    for market_id, fair_p, reason in ((1, None, "unmapped_market_type"),
                                      (2, None, "unmapped_market_type"),
                                      (3, None, None),
                                      (4, Decimal("0.5500"), None)):
        db_session.add(MarketGapSnapshot(run_id=77, venue_market_id=market_id, fair_p=fair_p,
                                         no_fair_reason=reason, dow=1, hour_ct=12,
                                         created_at=TS))
    # A second run's rows, which the `run_id` bound must leave out.
    db_session.add(MarketGapSnapshot(run_id=78, venue_market_id=9, fair_p=None,
                                     no_fair_reason="no_sharp_line", dow=1, hour_ct=12,
                                     created_at=TS))
    db_session.flush()

    samples = {labels["reason"]: value
               for name, value, labels in _pricing_samples(db_session, 77, {})
               if name == "pricing.no_fair"}
    assert samples == {"unmapped_market_type": 2, "unknown": 1}
