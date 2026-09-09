"""The Pulse builder: the status word, every named rule, the never-shown list, and the rule
that an absent input is `not evaluated` rather than fine."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from harness.dashboard.snapshots import pulse
from harness.dashboard.snapshots.pulse import (PULSE_KEYS, RuleResult, build_pulse, gather,
                                               status_word)
from harness.db.models import (CheckResult, DashboardSnapshot, EquitySnapshot, ExecHeartbeat,
                               JobRun, KillSwitch, MetricSample, OperatorEvent, Run)

NOW = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)


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


@pytest.mark.parametrize("rule_name", [
    "recorder_stale", "heartbeat_watch", "heartbeat_broken", "ws_event_watch",
    "ws_event_broken", "tape_gap", "kill_switch", "disk_free", "db_ceiling",
    "check_fail", "check_skipped", "settle_error_24h", "snapshot_stale",
    "credits_low", "budget_exhausted", "book_dirty_in_game", "drawdown_stop",
    "snapshot_budget",
])
def test_every_named_rule_is_evaluated(db_session, env_settings, rule_name):
    """Spec §2.1: "every rule has a name and the fired list shows it. This is the whole list;
    a red that is not one of these is a bug." So the set is pinned here."""
    _ok_machine(db_session, env_settings)
    assert rule_name in _rules(db_session, env_settings)


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


def test_a_snapshot_older_than_twice_its_cadence_is_a_watch_and_three_times_is_broken(
        db_session, env_settings):
    _ok_machine(db_session, env_settings)
    row = db_session.get(DashboardSnapshot, "pulse")
    row.generated_at = NOW - timedelta(seconds=75)      # 2.5 x a 30 s cadence
    db_session.flush()
    assert _rules(db_session, env_settings)["snapshot_stale"].level == "watch"
    row.generated_at = NOW - timedelta(seconds=200)
    db_session.flush()
    assert _rules(db_session, env_settings)["snapshot_stale"].level == "broken"


def test_the_floor_budget_rule_watches_the_p95(db_session, env_settings):
    _ok_machine(db_session, env_settings)
    for i in range(20):
        db_session.add(MetricSample(ts=NOW - timedelta(minutes=i % 9), source="serve",
                                    name="serve.snapshot_ms", value=400,
                                    labels={"name": "floor"}))
    db_session.flush()
    rule = _rules(db_session, env_settings)["snapshot_budget"]
    assert rule.level == "watch" and rule.threshold == 250


def test_the_build_tile_shows_all_three_shas(db_session, env_settings):
    """Ruling B-(c): build_sha_drift covers app-run only, so the tile shows the recorder's sha,
    the executor's version and the serving build side by side."""
    _ok_machine(db_session, env_settings)
    tile = build_pulse(db_session, NOW, env_settings)["build"]
    assert tile["recorder_build_sha"] == "abc"
    assert tile["executor_version"] == "4.2"
    assert tile["serving_build_sha"] == env_settings.build_sha


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
