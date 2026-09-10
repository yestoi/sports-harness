"""The Pulse builder: the status word, every named rule, the never-shown list, and the rule
that an absent input is `not evaluated` rather than fine."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from harness.dashboard.snapshots import pulse
from harness.dashboard.snapshots.pulse import (PULSE_KEYS, RuleResult, build_pulse, gather,
                                               status_word)
from harness.db.models import (CheckResult, DashboardSnapshot, EquitySnapshot, ExecHeartbeat,
                               Game, JobRun, KillSwitch, MetricSample, OperatorEvent, Run,
                               VetoDecision)

NOW = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)


def _absent_values() -> dict:
    """Every rule's input, absent, so a test can call `rule(_absent_values())` for any rule in
    `RULES` with no database and no real `gather()`. Mirrors `gather()`'s own keys and the
    absent-safe defaults its `_group` calls already fall back to."""
    return {
        "now": NOW,
        "settings": SimpleNamespace(db_budget_gb=2000, odds_monthly_credits=5_000_000),
        "run_age_s": None,
        "run_build_sha": None,
        "heartbeat_age_s": None,
        "ws_event_age_s": None,
        "book_dirty_markets": None,
        "executor_version": None,
        "heartbeat": None,
        "kill_active": None,
        "gaps_2h": 0.0,
        "disk_free_gb": None,
        "disk_total_gb": None,
        "mem_available_mb": None,
        "housekeeping": None,
        "credits_remaining": None,
        "sweep": [],
        "settle": [],
        "floor_ms": [],
        "snapshots": [],
        "games_live": 0,
        "stopped": [],
        "drawdown_by_variant": {},
        "disabled": [],
        "research_spend": None,
        "veto_rate": None,
    }


def _seed_decisions(session, *, proceed=0, reduce=0, veto=0, skipped=0, errored=0):
    """`proceed + reduce + veto` decided signals, plus `skipped` (`veto_skipped_budget`) and
    `errored` (`veto_error`) ones that must not count toward the denominator (D19)."""
    signal_id = 0
    for decision, count in (("proceed", proceed), ("reduce", reduce), ("veto", veto),
                            ("veto_skipped_budget", skipped), ("veto_error", errored)):
        for _ in range(count):
            signal_id += 1
            session.add(VetoDecision(
                signal_id=signal_id, call_id=None, decision=decision, confidence=None,
                from_cache=False, feature_delta={},
                signal_created_at=NOW - timedelta(minutes=5), decided_at=NOW - timedelta(hours=1),
                reason_code=None))
    session.flush()


def _ok_machine(session, settings):
    """Every input a rule reads, in its healthy state, so a test can break exactly one."""
    session.add(Run(started_at=NOW - timedelta(minutes=2), status="ok", notes={},
                    odds_remaining=4_000_000, build_sha="abc"))
    session.add(ExecHeartbeat(id=1, last_loop_at=NOW - timedelta(seconds=10), loops=100,
                              open_orders=3, ws_last_event_at=NOW - timedelta(seconds=10),
                              book_dirty_markets=0, executor_version="4.2"))
    session.add(KillSwitch(id=1, active=False, reason="", set_at=NOW - timedelta(days=1)))
    session.add(JobRun(job="settle", started_at=NOW - timedelta(minutes=20), status="ok",
                       notes={"stages": [{"name": "housekeeping",
                                          "counts": {"size_gb": 400.0, "tables_gb": {},
                                                     "growth_gb_per_day": 2.0,
                                                     "days_to_ceiling": 800}}]}))
    for name, value, labels in (("ws.gaps", 0, {}), ("host.disk_free_gb", 800.0, {}),
                                ("host.disk_total_gb", 2000.0, {}),
                                ("ws.events_per_min", 900, {}),
                                # Fix 31 (final review M7): the credits reading is the
                                # recorder's own per-tick sample, not a backward walk of `runs`.
                                ("recorder.credits_remaining", 4_000_000, {}),
                                ("recorder.fetched", 12, {"source": "odds"})):
        session.add(MetricSample(ts=NOW - timedelta(minutes=1), source="ws", name=name,
                                 value=value, labels=labels))
    job = JobRun(job="settle", started_at=NOW - timedelta(hours=1), status="ok", notes={})
    session.add(job)
    session.flush()
    session.add(CheckResult(job_run_id=job.id, ts=NOW - timedelta(hours=1),
                            check_name="fills_without_print", status="pass", value=0,
                            threshold="== 0"))
    session.add(DashboardSnapshot(name="pulse", generated_at=NOW - timedelta(seconds=5),
                                  elapsed_ms=40, payload={}, error=None))
    session.flush()


def _rules(session, settings, now=NOW):
    return {r.name: r for r in pulse.evaluate(gather(session, now, settings))}


def test_a_healthy_machine_reads_fine(db_session, env_settings):
    _ok_machine(db_session, env_settings)
    payload = build_pulse(db_session, NOW, env_settings)
    assert payload["status"]["status"] == "FINE"
    assert payload["status"]["rules"] == []


def test_the_payload_carries_only_the_allowed_keys_and_no_money(db_session, env_settings):
    """Spec §2.1 never-shown: no P&L, CLV or strategy figure anywhere on Pulse."""
    _ok_machine(db_session, env_settings)
    payload = build_pulse(db_session, NOW, env_settings)
    assert set(payload) == PULSE_KEYS
    for banned in ("pnl", "clv", "equity", "edge", "fills", "exposure"):
        assert banned not in payload


RULE_NAMES = [
    "recorder_stale", "heartbeat_watch", "heartbeat_broken", "ws_event_watch",
    "ws_event_broken", "tape_gap", "kill_switch", "disk_free", "db_ceiling",
    "check_fail", "check_skipped", "settle_error_24h", "snapshot_stale",
    "credits_low", "budget_exhausted", "book_dirty_in_game", "drawdown_stop",
    "snapshot_budget", "snapshot_disabled", "research_budget", "veto_rate",
]


@pytest.mark.parametrize("rule_name", RULE_NAMES)
def test_every_named_rule_is_evaluated(db_session, env_settings, rule_name):
    """Spec §2.1: "every rule has a name and the fired list shows it. This is the whole list;
    a red that is not one of these is a bug." So the set is pinned here."""
    _ok_machine(db_session, env_settings)
    assert rule_name in _rules(db_session, env_settings)


def test_the_pinned_list_is_the_whole_list(db_session, env_settings):
    """"This is the whole list" only means something if the list is closed at both ends: the
    parametrized test above catches a rule that was removed, and this one catches a rule that
    was added without being named here."""
    _ok_machine(db_session, env_settings)
    assert set(_rules(db_session, env_settings)) == set(RULE_NAMES)


def test_a_failing_gather_group_leaves_its_rule_not_evaluated_and_the_rest_intact(
        db_session, env_settings, monkeypatch):
    """Ruling A-I1: `gather()` runs outside every `section()` guard, and `status` is polled by
    the shell on every surface at 30 s -- its loss is the most visible failure the snapshot
    layer can have. A failing query group must cost only the rule(s) that read it, not the
    payload."""
    _ok_machine(db_session, env_settings)
    from sqlalchemy import text
    monkeypatch.setattr(pulse, "_NEWEST_RUN", text("select * from no_such_table"))

    payload = build_pulse(db_session, NOW, env_settings)

    not_evaluated = {r["name"] for r in payload["status"]["not_evaluated"]}
    assert "recorder_stale" in not_evaluated
    assert isinstance(payload["vitals"], dict) and "error" not in payload["vitals"]
    assert isinstance(payload["invariants"], dict) and "error" not in payload["invariants"]


def test_a_stale_recorder_is_broken(db_session, env_settings):
    from harness.health import STALE_AFTER_S

    _ok_machine(db_session, env_settings)
    db_session.query(Run).delete()
    db_session.add(Run(started_at=NOW - timedelta(seconds=STALE_AFTER_S + 60), status="ok",
                       notes={}, odds_remaining=4_000_000, build_sha="abc"))
    db_session.flush()

    rule = _rules(db_session, env_settings)["recorder_stale"]
    assert rule.level == "broken" and rule.threshold == STALE_AFTER_S
    assert build_pulse(db_session, NOW, env_settings)["status"]["status"] == "BROKEN"


def test_the_heartbeat_rules_use_the_imported_thresholds(db_session, env_settings):
    from harness.health import HEARTBEAT_BROKEN_S, HEARTBEAT_WATCH_S

    _ok_machine(db_session, env_settings)
    row = db_session.get(ExecHeartbeat, 1)
    row.last_loop_at = NOW - timedelta(seconds=HEARTBEAT_WATCH_S + 5)
    db_session.flush()
    rules = _rules(db_session, env_settings)
    assert rules["heartbeat_watch"].level == "watch"
    assert rules["heartbeat_watch"].threshold == HEARTBEAT_WATCH_S
    assert rules["heartbeat_broken"].level == "fine"
    assert rules["heartbeat_broken"].threshold == HEARTBEAT_BROKEN_S


def test_the_ws_event_rules_use_the_imported_thresholds(db_session, env_settings):
    from harness.health import WS_EVENT_BROKEN_S, WS_EVENT_WATCH_S

    _ok_machine(db_session, env_settings)
    row = db_session.get(ExecHeartbeat, 1)
    row.ws_last_event_at = NOW - timedelta(seconds=WS_EVENT_WATCH_S + 5)
    db_session.flush()
    rules = _rules(db_session, env_settings)
    assert rules["ws_event_watch"].level == "watch"
    assert rules["ws_event_watch"].threshold == WS_EVENT_WATCH_S
    assert rules["ws_event_broken"].level == "fine"
    assert rules["ws_event_broken"].threshold == WS_EVENT_BROKEN_S


def test_a_long_silence_breaks_the_heartbeat_and_the_ws_rules(db_session, env_settings):
    """The BROKEN tier of both ladders, which the WATCH-tier tests above leave `fine`."""
    from harness.health import HEARTBEAT_BROKEN_S, WS_EVENT_BROKEN_S

    _ok_machine(db_session, env_settings)
    row = db_session.get(ExecHeartbeat, 1)
    row.last_loop_at = NOW - timedelta(seconds=HEARTBEAT_BROKEN_S + 5)
    row.ws_last_event_at = NOW - timedelta(seconds=WS_EVENT_BROKEN_S + 5)
    db_session.flush()
    rules = _rules(db_session, env_settings)
    assert rules["heartbeat_broken"].level == "broken"
    assert rules["heartbeat_broken"].threshold == HEARTBEAT_BROKEN_S
    assert rules["ws_event_broken"].level == "broken"
    assert rules["ws_event_broken"].threshold == WS_EVENT_BROKEN_S
    assert build_pulse(db_session, NOW, env_settings)["status"]["status"] == "BROKEN"


def test_the_kill_switch_is_broken_while_it_is_active(db_session, env_settings):
    _ok_machine(db_session, env_settings)
    db_session.get(KillSwitch, 1).active = True
    db_session.flush()
    assert _rules(db_session, env_settings)["kill_switch"].level == "broken"
    assert build_pulse(db_session, NOW, env_settings)["status"]["status"] == "BROKEN"


def test_a_missing_kill_switch_row_is_not_evaluated_rather_than_fine(db_session, env_settings):
    """The rule that would otherwise render green over a row nobody wrote."""
    _ok_machine(db_session, env_settings)
    db_session.query(KillSwitch).delete()
    db_session.flush()
    rule = _rules(db_session, env_settings)["kill_switch"]
    assert rule.level == "not_evaluated" and rule.value is None


def test_credits_use_both_imported_fractions(db_session, env_settings):
    from harness.health import CREDITS_LOW_FRACTION, CREDITS_WATCH_FRACTION

    _ok_machine(db_session, env_settings)
    budget = env_settings.odds_monthly_credits
    for remaining, level, threshold in (
            (budget * CREDITS_WATCH_FRACTION - 1, "watch", CREDITS_WATCH_FRACTION),
            (budget * CREDITS_LOW_FRACTION - 1, "broken", CREDITS_LOW_FRACTION)):
        db_session.query(MetricSample).filter(
            MetricSample.name == "recorder.credits_remaining").delete()
        db_session.add(MetricSample(ts=NOW - timedelta(minutes=1), source="recorder",
                                    name="recorder.credits_remaining", value=int(remaining),
                                    labels={}))
        db_session.flush()
        rule = _rules(db_session, env_settings)["credits_low"]
        assert rule.level == level and rule.threshold == threshold


def test_credits_come_from_the_recorders_metric_and_not_from_a_walk_of_runs(db_session,
                                                                           env_settings):
    """Final review M7, fixed in fix 31. `select odds_remaining from runs where odds_remaining
    is not null order by id desc` walked the primary key backwards through every credit-less row
    with no bound at all. The number the recorder writes per tick answers the same question off
    `ix_metric_samples_name_ts`, bounded to 24 h -- so a `runs` row with credits and no metric
    beside it is now `not evaluated`, which is the honest word for a recorder that has not run.
    """
    _ok_machine(db_session, env_settings)
    db_session.query(MetricSample).filter(
        MetricSample.name == "recorder.credits_remaining").delete()
    db_session.flush()
    assert _rules(db_session, env_settings)["credits_low"].level == "not_evaluated"

    db_session.add(MetricSample(ts=NOW - timedelta(days=2), source="recorder",
                                name="recorder.credits_remaining", value=4_000_000, labels={}))
    db_session.flush()
    assert _rules(db_session, env_settings)["credits_low"].level == "not_evaluated", \
        "a reading older than the 24 h window is not a reading"


def test_a_gap_in_the_last_two_hours_is_broken_and_never_reads_orderbook_events(
        db_session, env_settings):
    """Ruling A-I2: the gap rule is `sum(ws.gaps)` over 2 h from metric_samples, which the WS
    recorder writes per minute -- never a count on the forbidden tape table."""
    _ok_machine(db_session, env_settings)
    db_session.add(MetricSample(ts=NOW - timedelta(minutes=30), source="ws", name="ws.gaps",
                                value=3, labels={}))
    db_session.flush()
    assert _rules(db_session, env_settings)["tape_gap"].level == "broken"


def test_the_disk_rule_is_not_evaluated_until_both_metrics_exist(db_session, env_settings):
    """Ruling A-C2: free gigabytes alone are not a share. Until housekeeping has run after the
    deploy the rule says `not evaluated` -- never a false FINE."""
    _ok_machine(db_session, env_settings)
    db_session.query(MetricSample).filter(
        MetricSample.name == "host.disk_total_gb").delete()
    db_session.flush()
    rule = _rules(db_session, env_settings)["disk_free"]
    assert rule.level == "not_evaluated" and rule.value is None
    payload = build_pulse(db_session, NOW, env_settings)
    assert payload["status"]["status"] == "FINE"
    assert any(r["name"] == "disk_free" for r in payload["status"]["not_evaluated"])


def test_disk_below_a_quarter_free_is_broken(db_session, env_settings):
    from harness.health import DISK_FREE_MIN_FRACTION

    _ok_machine(db_session, env_settings)
    db_session.add(MetricSample(ts=NOW, source="housekeeping", name="host.disk_free_gb",
                                value=100.0, labels={}))
    db_session.add(MetricSample(ts=NOW, source="housekeeping", name="host.disk_total_gb",
                                value=2000.0, labels={}))
    db_session.flush()
    rule = _rules(db_session, env_settings)["disk_free"]
    assert rule.level == "broken" and rule.threshold == DISK_FREE_MIN_FRACTION


def test_the_db_ceiling_uses_both_imported_fractions(db_session, env_settings):
    from harness.health import DB_BROKEN_FRACTION, DB_WATCH_FRACTION

    _ok_machine(db_session, env_settings)
    budget = env_settings.db_budget_gb
    for size, level in ((budget * DB_WATCH_FRACTION + 1, "watch"),
                        (budget * DB_BROKEN_FRACTION + 1, "broken")):
        db_session.query(JobRun).filter(JobRun.job == "settle").delete()
        db_session.add(JobRun(job="settle", started_at=NOW - timedelta(minutes=20), status="ok",
                              notes={"stages": [{"name": "housekeeping",
                                                 "counts": {"size_gb": size, "tables_gb": {}}}]}))
        db_session.flush()
        assert _rules(db_session, env_settings)["db_ceiling"].level == level


def test_a_failing_check_is_broken_and_a_skipped_one_is_a_watch(db_session, env_settings):
    """Ruling B-I1: `skip` is the state that hides things. A check that timed out has not
    passed, and the wall must not read green over it."""
    _ok_machine(db_session, env_settings)
    job = db_session.query(JobRun).filter(JobRun.job == "settle").first()
    db_session.add(CheckResult(job_run_id=job.id, ts=NOW - timedelta(hours=1),
                               check_name="duplicate_trades", status="skip", value=None,
                               threshold="== 0", detail="timeout"))
    db_session.flush()
    rules = _rules(db_session, env_settings)
    assert rules["check_skipped"].level == "watch"
    assert rules["check_fail"].level == "fine"


def test_a_failed_check_in_the_latest_sweep_is_broken(db_session, env_settings):
    _ok_machine(db_session, env_settings)
    job = db_session.query(JobRun).filter(JobRun.job == "settle").first()
    db_session.add(CheckResult(job_run_id=job.id, ts=NOW - timedelta(hours=1),
                               check_name="duplicate_trades", status="fail", value=2,
                               threshold="== 0"))
    db_session.flush()
    rules = _rules(db_session, env_settings)
    assert rules["check_fail"].level == "broken" and rules["check_fail"].value == 1
    assert rules["check_skipped"].level == "fine"
    assert build_pulse(db_session, NOW, env_settings)["status"]["status"] == "BROKEN"


def test_a_settle_error_is_found_behind_ten_newer_passes(db_session, env_settings):
    """The window is 24 h and the rule must see all of it. A row cap here used to hide an error
    twelve hours old behind ten newer passes, which is a missing red -- worse than a wrong one."""
    _ok_machine(db_session, env_settings)
    db_session.add(JobRun(job="settle", started_at=NOW - timedelta(hours=12), status="error",
                          notes={}))
    for hour in range(11):
        db_session.add(JobRun(job="settle", started_at=NOW - timedelta(hours=hour, minutes=30),
                              status="ok", notes={}))
    db_session.flush()
    rule = _rules(db_session, env_settings)["settle_error_24h"]
    assert rule.level == "broken" and rule.value == 1
    assert build_pulse(db_session, NOW, env_settings)["status"]["status"] == "BROKEN"


def test_a_settle_error_older_than_the_window_does_not_fire(db_session, env_settings):
    _ok_machine(db_session, env_settings)
    db_session.add(JobRun(job="settle", started_at=NOW - timedelta(hours=25), status="error",
                          notes={}))
    db_session.flush()
    assert _rules(db_session, env_settings)["settle_error_24h"].level == "fine"


def test_budget_exhausted_needs_both_of_the_two_newest_settle_runs(db_session, env_settings):
    _ok_machine(db_session, env_settings)
    for row in db_session.query(JobRun).filter(JobRun.job == "settle").all():
        row.budget_exhausted = True
    db_session.flush()
    assert _rules(db_session, env_settings)["budget_exhausted"].level == "watch"

    # One healthy run in front of them, so the newest two are no longer both exhausted.
    db_session.add(JobRun(job="settle", started_at=NOW - timedelta(minutes=5), status="ok",
                          budget_exhausted=False, notes={}))
    db_session.flush()
    assert _rules(db_session, env_settings)["budget_exhausted"].level == "fine"


def test_a_dirty_book_is_a_watch_only_while_a_game_is_in_progress(db_session, env_settings):
    """The only test that pins the conjunction: a distrusted book between games is not a fault."""
    _ok_machine(db_session, env_settings)
    db_session.get(ExecHeartbeat, 1).book_dirty_markets = 3
    db_session.flush()
    assert _rules(db_session, env_settings)["book_dirty_in_game"].level == "fine"

    db_session.add(Game(sport="nfl", home_team_id=1, away_team_id=2, kickoff_utc=NOW,
                        status="in_progress"))
    db_session.flush()
    rule = _rules(db_session, env_settings)["book_dirty_in_game"]
    assert rule.level == "watch" and rule.value == 3


def test_the_invariant_wall_carries_three_tile_states_with_the_skip_detail(db_session,
                                                                          env_settings):
    _ok_machine(db_session, env_settings)
    job = db_session.query(JobRun).filter(JobRun.job == "settle").first()
    db_session.add(CheckResult(job_run_id=job.id, ts=NOW - timedelta(hours=1),
                               check_name="duplicate_trades", status="skip", value=None,
                               threshold="== 0", detail="timeout"))
    db_session.flush()
    wall = build_pulse(db_session, NOW, env_settings)["invariants"]
    states = {tile["check_name"]: tile for tile in wall["tiles"]}
    assert states["fills_without_print"]["status"] == "pass"
    assert states["duplicate_trades"]["status"] == "skip"
    assert states["duplicate_trades"]["detail"] == "timeout"


def test_the_drawdown_rule_reads_the_risk_module(db_session, env_settings):
    """Addendum §0.3 / ruling A-C3: the threshold is DRAWDOWN_STOP_PCT, imported, and the
    verdict comes from risk.stopped_variants -- not a second query written here. In paper it is
    a WATCH and never a BROKEN."""
    from harness.execution.risk import DRAWDOWN_STOP_PCT

    _ok_machine(db_session, env_settings)
    db_session.add(EquitySnapshot(ts=NOW - timedelta(minutes=5), variant_id="sharp_direct",
                                  cash=Decimal("800.00"), open_stake=Decimal("0.00"),
                                  n_open_positions=0, n_open_orders=0,
                                  peak_equity_7d=Decimal("1000.00"),
                                  drawdown_pct=Decimal("-0.2000"), drawdown_stop=True))
    db_session.flush()
    rule = _rules(db_session, env_settings)["drawdown_stop"]
    assert rule.level == "watch"
    assert rule.threshold == float(DRAWDOWN_STOP_PCT)
    assert rule.value == pytest.approx(-0.2)


def test_the_drawdown_value_is_the_newest_reading_not_the_worst_of_the_window(db_session,
                                                                             env_settings):
    """Addendum §0.3 asks for the variant's newest `drawdown_pct`. A variant that fell to -0.35
    and recovered to -0.21 is still stopped, but -0.35 was true last week: showing it as today's
    reading is the quiet lie this surface exists to refuse."""
    _ok_machine(db_session, env_settings)
    for ts, pct in ((NOW - timedelta(days=3), Decimal("-0.3500")),
                    (NOW - timedelta(minutes=5), Decimal("-0.2100"))):
        db_session.add(EquitySnapshot(ts=ts, variant_id="sharp_direct", cash=Decimal("790.00"),
                                      open_stake=Decimal("0.00"), n_open_positions=0,
                                      n_open_orders=0, peak_equity_7d=Decimal("1000.00"),
                                      drawdown_pct=pct, drawdown_stop=True))
    db_session.flush()
    rule = _rules(db_session, env_settings)["drawdown_stop"]
    assert rule.level == "watch"
    assert rule.value == pytest.approx(-0.21)


def test_a_snapshot_older_than_twice_its_cadence_is_a_watch_and_three_times_is_broken(
        db_session, env_settings):
    from harness.dashboard.snapshots.pulse import JUDGED_CADENCES

    _ok_machine(db_session, env_settings)
    cadence = JUDGED_CADENCES["pulse"]
    row = db_session.get(DashboardSnapshot, "pulse")
    row.generated_at = NOW - timedelta(seconds=cadence * 2.5)
    db_session.flush()
    assert _rules(db_session, env_settings)["snapshot_stale"].level == "watch"
    row.generated_at = NOW - timedelta(seconds=cadence * 3.5)
    db_session.flush()
    assert _rules(db_session, env_settings)["snapshot_stale"].level == "broken"


def test_a_closed_study_week_never_makes_the_wall_stale(db_session, env_settings):
    """Addendum §0.1: a closed week is rebuilt on a new report run, not on a clock, so its age
    is not a fault. The current week is on a 10-minute cadence and is still judged."""
    _ok_machine(db_session, env_settings)
    iso = NOW.isocalendar()
    db_session.add(DashboardSnapshot(name="study:2026-30", generated_at=NOW - timedelta(days=40),
                                     elapsed_ms=90, payload={}, error=None))
    db_session.flush()
    assert _rules(db_session, env_settings)["snapshot_stale"].level == "fine"

    db_session.add(DashboardSnapshot(name=f"study:{iso.year}-{iso.week}",
                                     generated_at=NOW - timedelta(seconds=2400),
                                     elapsed_ms=90, payload={}, error=None))
    db_session.flush()
    assert _rules(db_session, env_settings)["snapshot_stale"].level == "broken"


def test_the_ages_panel_still_lists_the_closed_week_the_rule_skips(db_session, env_settings):
    _ok_machine(db_session, env_settings)
    db_session.add(DashboardSnapshot(name="study:2026-30", generated_at=NOW - timedelta(days=40),
                                     elapsed_ms=90, payload={}, error=None))
    db_session.flush()
    payload = build_pulse(db_session, NOW, env_settings)
    assert "study:2026-30" in [row["name"] for row in payload["snapshots"]]
    assert payload["status"]["status"] == "FINE"


def test_the_floor_budget_rule_watches_the_p95(db_session, env_settings):
    from harness.dashboard.snapshots import FLOOR_P95_BUDGET_MS

    _ok_machine(db_session, env_settings)
    for i in range(20):
        db_session.add(MetricSample(ts=NOW - timedelta(minutes=i % 9), source="serve",
                                    name="serve.snapshot_ms", value=400,
                                    labels={"name": "floor"}))
    db_session.flush()
    rule = _rules(db_session, env_settings)["snapshot_budget"]
    assert rule.level == "watch" and rule.threshold == FLOOR_P95_BUDGET_MS


def test_the_floor_budget_fires_strictly_above_the_budget(db_session, env_settings):
    """The scheduler backs off on `p95 > budget`, so the surface must not say WATCH at exactly the
    budget while the scheduler does nothing. One number, one comparison."""
    from harness.dashboard.snapshots import FLOOR_P95_BUDGET_MS

    _ok_machine(db_session, env_settings)
    for i in range(20):
        db_session.add(MetricSample(ts=NOW - timedelta(minutes=i % 9), source="serve",
                                    name="serve.snapshot_ms", value=FLOOR_P95_BUDGET_MS,
                                    labels={"name": "floor"}))
    db_session.flush()
    rule = _rules(db_session, env_settings)["snapshot_budget"]
    assert rule.level == "fine" and rule.value == float(FLOOR_P95_BUDGET_MS)


def test_the_build_tile_shows_all_three_shas(db_session, env_settings):
    """Ruling B-(c): build_sha_drift covers app-run only, so the tile shows the recorder's sha,
    the executor's version and the serving build side by side."""
    _ok_machine(db_session, env_settings)
    tile = build_pulse(db_session, NOW, env_settings)["build"]
    assert tile["recorder_build_sha"] == "abc"
    assert tile["executor_version"] == "4.2"
    assert tile["serving_build_sha"] == env_settings.build_sha


def test_each_vitals_tile_names_the_sparkline_series_it_draws(db_session, env_settings):
    """Ruling B-C3: `technical` is the glossary key and stays one; `metric` is the separate
    `metric_samples` name whose sparkline the tile draws, or `None` for the tile with no series
    of its own (the executor heartbeat tile draws from `exec_heartbeat`, not a metric)."""
    _ok_machine(db_session, env_settings)
    tiles = {t["label"]: t for t in build_pulse(db_session, NOW, env_settings)["vitals"]["tiles"]}

    assert tiles["executor heartbeat"]["metric"] is None
    assert tiles["loop time"]["metric"] == "exec.p95_loop_ms"
    assert tiles["loops skipped"]["metric"] == "exec.loops_skipped"
    assert tiles["markets with a book we distrust"]["metric"] == "exec.dirty_markets"
    assert tiles["last exchange message"]["metric"] == "exec.ws_event_age_s"
    assert tiles["credits left this month"]["metric"] == "recorder.credits_remaining"
    # `technical` is unchanged and still the glossary key.
    assert tiles["loop time"]["technical"] == "exec.p95_loop_ms"
    assert tiles["executor heartbeat"]["technical"] == "exec_heartbeat.last_loop_at"


def test_vitals_sparklines_carry_only_the_names_a_tile_reads(db_session, env_settings):
    """Ruling B-C3: `exec.loop_ms`, `ws.events_per_min`, `ws.trades_per_min` and `ws.reconnects`
    used to be collected and serialized into every payload with no tile reading them."""
    _ok_machine(db_session, env_settings)
    for name in ("exec.loop_ms", "ws.events_per_min", "ws.trades_per_min", "ws.reconnects",
                "exec.p95_loop_ms"):
        db_session.add(MetricSample(ts=NOW - timedelta(minutes=1), source="exec", name=name,
                                    value=1, labels={}))
    db_session.flush()

    sparklines = build_pulse(db_session, NOW, env_settings)["vitals"]["sparklines"]
    assert "exec.p95_loop_ms" in sparklines
    for dropped in ("exec.loop_ms", "ws.events_per_min", "ws.trades_per_min", "ws.reconnects"):
        assert dropped not in sparklines


def test_no_pulse_sql_names_a_forbidden_table():
    from pathlib import Path

    body = Path(pulse.__file__).read_text().lower()
    for table in ("orderbook_events", "venue_trades", "raw_responses", "odds_snapshots",
                  "venue_quotes"):
        assert table not in body


def test_status_word_takes_the_worst_level():
    fine = RuleResult("a", "fine", 1, 2, "")
    watch = RuleResult("b", "watch", 3, 2, "")
    broken = RuleResult("c", "broken", 9, 2, "")
    unevaluated = RuleResult("d", "not_evaluated", None, 2, "")
    assert status_word([fine, unevaluated]) == "FINE"
    assert status_word([fine, watch]) == "WATCH"
    assert status_word([watch, broken]) == "BROKEN"


def test_the_payload_carries_sentences_and_readings(db_session, env_settings):
    _ok_machine(db_session, env_settings)
    payload = build_pulse(db_session, NOW, env_settings)
    assert payload["sentences"]["status"] and payload["sentences"]["tape"]
    assert isinstance(payload["readings"]["rules"], list)


def test_operator_event_summaries_are_sanitized(db_session, env_settings):
    _ok_machine(db_session, env_settings)
    db_session.add(OperatorEvent(ts=NOW - timedelta(minutes=5), kind="note",
                                 summary="<img onerror=alert(1)>", ref={}))
    db_session.flush()
    events = build_pulse(db_session, NOW, env_settings)["operator_events"]
    assert all("<" not in e["summary"] and ">" not in e["summary"] for e in events)


def test_a_disabled_builder_is_broken_and_named_in_the_snapshot_ages(db_session, env_settings):
    """Fix 31. The scheduler stops a builder that costs ten times its budget three builds
    running; the surface has to say so, because a row frozen on its last payload with a growing
    age looks exactly like a healthy one to anybody not watching the clock."""
    from harness.dashboard import snapshots

    _ok_machine(db_session, env_settings)
    db_session.add(DashboardSnapshot(name="floor", generated_at=NOW - timedelta(seconds=5),
                                     elapsed_ms=9000, payload={}, error=None))
    db_session.flush()
    try:
        assert _rules(db_session, env_settings)["snapshot_disabled"].level == "fine", \
            "an empty set is a reading, not an absent one"

        snapshots.disable_builder("floor")
        rule = _rules(db_session, env_settings)["snapshot_disabled"]
        assert rule.level == "broken" and rule.value == 1

        payload = build_pulse(db_session, NOW, env_settings)
        rows = {row["name"]: row for row in payload["snapshots"]}
        assert rows["floor"]["disabled"] is True
        assert rows["pulse"]["disabled"] is False
        assert rows["floor"]["disabled_over_ms"] == snapshots.SNAPSHOT_DISABLE_MS
        assert payload["status"]["status"] == "BROKEN"
        assert "snapshot_disabled" in [r["name"] for r in payload["status"]["rules"]]
    finally:
        snapshots.reset_disabled_builders()


def test_a_disabled_study_builder_flags_every_one_of_its_weeks(db_session, env_settings):
    """The guard is keyed on the builder, and `study:2026-35` and `study:2026-36` are two rows
    of one builder on one job. Both stopped; both say so."""
    from harness.dashboard import snapshots

    _ok_machine(db_session, env_settings)
    for week in (35, 36):
        db_session.add(DashboardSnapshot(name=f"study:2026-{week}", generated_at=NOW,
                                         elapsed_ms=10, payload={}, error=None))
    db_session.flush()
    try:
        snapshots.disable_builder("study")
        rows = {row["name"]: row for row in build_pulse(db_session, NOW,
                                                        env_settings)["snapshots"]}
        assert rows["study:2026-35"]["disabled"] and rows["study:2026-36"]["disabled"]
    finally:
        snapshots.reset_disabled_builders()


# --- Phase 5 (addendum §1.4, D19): the research layer's two Pulse rules -----------------------

def test_the_two_research_rules_are_registered():
    from harness.dashboard.snapshots.pulse import RULES

    names = [rule(_absent_values()).name for rule in RULES]
    assert "research_budget" in names and "veto_rate" in names


def test_research_budget_is_fine_with_room_and_watch_when_dormant():
    from harness.dashboard.snapshots.pulse import rule_research_budget
    from harness.research.spend import SpendState

    room = SpendState(Decimal("1"), Decimal("0"), Decimal("5"), Decimal("0"),
                      Decimal("25"), Decimal("150"), dormant=False)
    out = SpendState(Decimal("24.9"), Decimal("0"), Decimal("40"), Decimal("0"),
                     Decimal("25"), Decimal("150"), dormant=True)
    assert rule_research_budget({"research_spend": room}).level == "fine"
    watch = rule_research_budget({"research_spend": out})
    assert watch.level == "watch" and watch.value == 24.9 and watch.threshold == 25.0


def test_research_budget_is_not_evaluated_before_anything_spent():
    """A wall that reads green over a measurement nobody took is the failure Pulse exists to
    prevent: no research_spend row at all is `not evaluated`, never `fine`."""
    from harness.dashboard.snapshots.pulse import rule_research_budget

    assert rule_research_budget({"research_spend": None}).level == "not_evaluated"


@pytest.mark.parametrize("rate,level", [(0.10, "fine"), (0.25, "watch"), (0.40, "watch")])
def test_veto_rate_watches_at_a_quarter_of_decided_signals(rate, level):
    from harness.dashboard.snapshots.pulse import rule_veto_rate
    from harness.health import VETO_RATE_WATCH

    assert VETO_RATE_WATCH == 0.25
    result = rule_veto_rate({"veto_rate": rate})
    assert result.level == level and result.threshold == VETO_RATE_WATCH
    assert result.unit == "fraction"


def test_veto_rate_is_not_evaluated_with_no_decided_signals():
    from harness.dashboard.snapshots.pulse import rule_veto_rate

    assert rule_veto_rate({"veto_rate": None}).level == "not_evaluated"


def test_the_veto_rate_denominator_is_the_decided_set(db_session):
    """D19: the rate is over `proceed | reduce | veto`. `veto_skipped_budget` and `veto_error`
    are the budget's and the machine's, not the model's, and counting them would make a dormant
    day look like a calm one."""
    from harness.dashboard.snapshots.pulse import _veto_rate

    _seed_decisions(db_session, proceed=6, reduce=1, veto=1, skipped=20, errored=20)
    assert _veto_rate(db_session, NOW) == pytest.approx(0.25)


def test_the_research_section_shows_the_spend_and_the_caps(db_session, env_settings):
    from harness.dashboard.snapshots.pulse import build_pulse

    payload = build_pulse(db_session, NOW, env_settings)
    section = payload["research"]
    assert set(section) >= {"day_usd", "day_reserved", "week_usd", "daily_cap", "weekly_cap",
                            "dormant", "veto_rate", "decided_24h", "rfq_quotes_24h",
                            "annotations_week"}


def test_the_pulse_payload_keys_gain_research():
    from harness.dashboard.snapshots.pulse import PULSE_KEYS

    assert "research" in PULSE_KEYS


def test_no_pulse_query_names_a_forbidden_table():
    """Ruling B-I9 as it lands on this surface: `research_notes` and `rfqs` hold model and venue
    free text and are forbidden to every builder. Pulse counts `rfq_quotes` instead."""
    from pathlib import Path

    body = Path(__import__("harness.dashboard.snapshots.pulse",
                           fromlist=["__file__"]).__file__).read_text().lower()
    for table in ("research_notes", "rfqs "):
        assert table not in body
