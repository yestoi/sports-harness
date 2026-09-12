"""The Gate builder: stored evidence only, in the shape October will read it in."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from harness.dashboard.snapshots import gate
from harness.dashboard.snapshots.gate import GATE_KEYS, build_gate
from harness.db.models import GateReport, StrategyVariant

NOW = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)

#: The shape `harness.report.gate.CriterionResult.as_json()` actually writes, not an invented
#: one: nine keys, sample size as `n_obs`/`n_clusters`, and the three status strings the gate
#: defines. A fixture of the wrong shape would let every test pass over a broken surface.
#:
#: The three keys are real `CRITERIA` names (review I1 fix): `_criterion_rows`/`_history` now
#: filter to `{c.name for c in CRITERIA}`, so a fixture keyed by an invented name would be
#: silently dropped rather than exercise the surface. The values beside them stay illustrative,
#: not the real definitions -- only the key has to be real.
CRITERIA = {
    "clv_pinnacle_lb": {"value": 0.021, "threshold": "> 0", "passed": True, "status": "passed",
                        "n_obs": 240, "n_clusters": 62,
                        "definition": "mean CLV vs pinnacle_t5 > 0", "fn": "_clv_positive",
                        "detail": {}},
    "fill_events": {"value": 0.91, "threshold": ">= 0.95", "passed": False, "status": "failed",
                    "n_obs": 180, "n_clusters": 40,
                    "definition": "queue-model fills with a print", "fn": "_fill_realism",
                    "detail": {}},
    "filled_vs_unfilled": {"value": None, "threshold": ">= 30", "passed": False,
                          "status": "insufficient", "n_obs": 18, "n_clusters": 6,
                          "definition": "game clusters", "fn": "_sample_size", "detail": {}},
}


def _evaluation(session, *, at, passed=False, variant="sharp_two_s", gate_variant=True,
                criteria=None):
    session.add(GateReport(evaluated_at=at, variant_id=variant, gate_variant=gate_variant,
                           criteria_json=criteria or CRITERIA, criteria_hash="c0ffee",
                           passed=passed))
    session.flush()


def test_no_evaluation_says_so(db_session, env_settings):
    payload = build_gate(db_session, NOW, env_settings)
    assert payload["verdict"] == {}
    assert "No gate evaluation" in " ".join(payload["sentences"]["verdict"])


def test_a_failed_newest_rows_read_falls_back_to_no_evaluation_without_raising(
        db_session, env_settings, monkeypatch):
    """Ruling A-M2: `_NEWEST` and `_ROWS_AT` sat outside any guard, unlike `_history`. A
    database-level failure must roll back and render the same "no evaluation" shape, not crash
    the build -- and `history`, guarded separately, must still be reachable afterward."""
    session_variant = StrategyVariant(variant_id="sharp_two_s", name="sharp_two_sided",
                                      tier="secondary", config_json={},
                                      registered_at=NOW - timedelta(days=30), active=True)
    db_session.add(session_variant)
    _evaluation(db_session, at=NOW - timedelta(hours=1), variant="sharp_two_s")

    from sqlalchemy import text
    monkeypatch.setattr(gate, "_NEWEST", text("select * from no_such_table"))
    payload = build_gate(db_session, NOW, env_settings)

    assert payload["verdict"] == {} and payload["criteria"] == [] and payload["variants"] == []
    assert "No gate evaluation" in " ".join(payload["sentences"]["verdict"])
    assert payload["history"] == {} or isinstance(payload["history"], dict)


def test_the_verdict_reads_not_passing_and_names_the_hash_and_the_date(db_session,
                                                                      env_settings):
    session_variant = StrategyVariant(variant_id="sharp_two_s", name="sharp_two_sided",
                                      tier="secondary", config_json={},
                                      registered_at=NOW - timedelta(days=30), active=True)
    db_session.add(session_variant)
    _evaluation(db_session, at=NOW - timedelta(hours=1), variant="sharp_two_s")

    payload = build_gate(db_session, NOW, env_settings)
    assert payload["verdict"]["passing"] is False
    assert payload["verdict"]["criteria_hash"] == "c0ffee"
    assert payload["verdict"]["variant"] == "sharp_two_sided"
    assert "NOT PASSING" in " ".join(payload["sentences"]["verdict"])
    assert "separate legal decision" in " ".join(payload["sentences"]["verdict"])


def test_every_criterion_carries_its_stored_definition_threshold_value_and_n(db_session,
                                                                            env_settings):
    from harness.report.gate import PASSED

    _evaluation(db_session, at=NOW - timedelta(hours=1))
    payload = build_gate(db_session, NOW, env_settings)
    rows = {r["name"]: r for r in payload["criteria"]}
    assert rows["clv_pinnacle_lb"]["definition"] == "mean CLV vs pinnacle_t5 > 0"
    assert rows["clv_pinnacle_lb"]["threshold"] == "> 0"
    assert rows["clv_pinnacle_lb"]["value"] == 0.021
    # `n` is the game-cluster count, which is what the confidence phrase reads; `n_obs` travels
    # beside it. There is no `n` key in what the gate stores.
    assert rows["clv_pinnacle_lb"]["n"] == 62 and rows["clv_pinnacle_lb"]["n_obs"] == 240
    assert rows["clv_pinnacle_lb"]["status"] == PASSED
    assert payload["verdict"]["n_pass"] == 1
    assert payload["verdict"]["n_fail"] == 1
    assert payload["verdict"]["n_insufficient"] == 1


def test_a_real_gate_result_round_trips_through_the_builder(db_session, env_settings):
    """The guard against C3 ever coming back: the fixture above is hand-written, so one test
    stores what `harness.report.gate` itself produces and asserts the surface reads it."""
    from harness.report.gate import CriterionResult, PASSED

    result = CriterionResult(value=0.021, threshold="> 0", passed=True, n_obs=240,
                             n_clusters=62, definition="mean CLV vs pinnacle_t5 > 0",
                             fn="_clv_positive", status=PASSED)
    _evaluation(db_session, at=NOW - timedelta(hours=1),
                criteria={"clv_pinnacle_lb": result.as_json()})
    row = build_gate(db_session, NOW, env_settings)["criteria"][0]
    assert row["status"] == PASSED and row["n"] == 62 and row["value"] == 0.021
    assert row["definition"] == "mean CLV vs pinnacle_t5 > 0"


def test_an_insufficient_criterion_is_never_dressed_up(db_session, env_settings):
    _evaluation(db_session, at=NOW - timedelta(hours=1))
    payload = build_gate(db_session, NOW, env_settings)
    reading = next(r for r in payload["readings"]["criteria"]
                  if r.startswith("filled_vs_unfilled"))
    assert "not enough evidence" in reading
    assert "0.0" not in reading


def test_the_primary_is_shown_beside_the_gate_variant_and_labelled(db_session, env_settings):
    _evaluation(db_session, at=NOW - timedelta(hours=1), variant="sharp_two_s",
                gate_variant=True)
    _evaluation(db_session, at=NOW - timedelta(hours=1), variant="sharp_direct",
                gate_variant=False)
    rows = build_gate(db_session, NOW, env_settings)["variants"]
    labels = {r["variant_id"]: r for r in rows}
    assert labels["sharp_two_s"]["gate_variant"] is True
    assert labels["sharp_direct"]["gate_variant"] is False
    assert labels["sharp_direct"]["note"] == "reported, not gated"


def test_the_history_is_bounded_and_newest_first(db_session, env_settings):
    for days in range(5):
        _evaluation(db_session, at=NOW - timedelta(days=days))
    history = build_gate(db_session, NOW, env_settings)["history"]
    assert len(history["clv_pinnacle_lb"]) == 5
    stamps = [row["evaluated_at"] for row in history["clv_pinnacle_lb"]]
    assert stamps == sorted(stamps, reverse=True)
    assert gate.HISTORY_LIMIT == 200


def test_the_payload_carries_only_the_allowed_keys_and_no_projection(db_session, env_settings):
    _evaluation(db_session, at=NOW - timedelta(hours=1))
    payload = build_gate(db_session, NOW, env_settings)
    assert set(payload) == GATE_KEYS
    for banned in ("projection", "forecast", "estimate_at_go_live"):
        assert banned not in payload


def test_an_eligibility_key_beside_the_twelve_criteria_renders_no_phantom_row(db_session,
                                                                            env_settings):
    """Review I1: `evaluate_all` writes `criteria_json["eligibility"]` as a sibling of the
    twelve criterion names once the dormant gate eligibility mechanism (Task 4) is switched
    on. That key is not a criterion -- it carries no `status`, no `value`, no `n_clusters` --
    and the builder must not render it as a thirteenth row with every field `None`.
    """
    from harness.report.gate import CRITERIA as REAL_CRITERIA
    from harness.report.gate import PASSED

    criteria_json = {c.name: {"value": 1, "threshold": 0, "passed": True, "status": PASSED,
                              "n_obs": 1, "n_clusters": 1, "definition": c.definition,
                              "fn": c.fn, "detail": {}}
                     for c in REAL_CRITERIA}
    criteria_json["eligibility"] = {"from_order_id": 4200, "from_run_id": 9100}
    _evaluation(db_session, at=NOW - timedelta(hours=1), criteria=criteria_json)

    payload = build_gate(db_session, NOW, env_settings)
    names = {r["name"] for r in payload["criteria"]}
    assert len(payload["criteria"]) == 12
    assert names == {c.name for c in REAL_CRITERIA}
    assert "eligibility" not in names

    history = build_gate(db_session, NOW, env_settings)["history"]
    assert "eligibility" not in history


def test_gate_reads_only_its_two_tables():
    body = Path(gate.__file__).read_text().lower()
    assert "from gate_reports" in body and "from strategy_variants" in body
    for table in ("orders", "fills", "signals", "orderbook_events", "venue_trades",
                  "raw_responses", "odds_snapshots", "venue_quotes"):
        assert f"from {table}" not in body
