import re
import uuid
from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import event, insert
from sqlalchemy.orm import sessionmaker

from harness.dashboard.app import (WINDOW_24H, _candidates, _data_quality, _executor, _funnel, _recent_run_notes,
                                    _websocket, create_dashboard)
from harness.db.models import (ExecHeartbeat, Fill, JobRun, KillSwitch, Ledger, MetricSample, OperatorEvent, Order,
                                OrderEvent, RawResponse, Run, StrategyVariant, VenueSettlement, VenueTrade)
from harness.execution.plan import POST_ONLY_REJECT
from harness.strategy.pipeline import price_and_signal
from harness.strategy.variants import load_variants, register_variants
from tests.test_pipeline import NOW, VARIANTS_DIR, _seed


def _dashboard_settings(env_settings, tmp_path, token: str | None = "supersecret", token_file_exists: bool = True):
    token_file = tmp_path / "dashboard_token"
    if token_file_exists:
        token_file.write_text(token or "")
    return env_settings.model_copy(update={"dashboard_token_file": token_file})


def _seed_full(db_session, env_settings):
    """Seed a run, markets, gap snapshots, registered ("tiny", tier=primary) variant, and signals."""
    game, run, markets = _seed(db_session)
    variants = load_variants(VARIANTS_DIR)
    register_variants(db_session, variants, NOW, prune=True)
    price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)
    return game, run, markets


def _client(db_session, settings, clock=lambda: NOW):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    app = create_dashboard(factory, settings, clock=clock)
    return TestClient(app)


def test_index_renders_seeded_run_and_signal(db_session, env_settings, tmp_path):
    game, run, markets = _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)

    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert str(run.id) in body
    # A signal for the primary ("tiny") variant should show up in the signals table.
    assert "tiny" in body


def test_api_summary_has_funnel_and_health_keys(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)

    r = client.get("/api/summary")
    assert r.status_code == 200
    body = r.json()
    assert "health" in body
    assert "funnel" in body
    assert "match_report" in body
    assert "signals" in body
    assert "unmatched_markets" in body
    assert "websocket" in body
    assert "data_quality" in body
    assert body["health"]["last_status"] == "running"  # run never finished in this fixture
    assert "sources" in body["funnel"]
    assert "signals_by_variant" in body["funnel"]
    assert len(body["signals"]) >= 1


def test_kill_flips_state_and_page_reflects_it(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)

    r = client.post("/kill", data={"reason": "manual pause for testing"})
    assert r.status_code == 200

    row = db_session.get(KillSwitch, 1)
    assert row is not None
    assert row.active is True
    assert row.reason == "manual pause for testing"

    page = client.get("/")
    assert "manual pause for testing" in page.text
    assert "ACTIVE" in page.text.upper()


def test_unkill_without_token_is_403(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)
    client.post("/kill", data={"reason": "x"})

    r = client.post("/unkill")
    assert r.status_code == 403

    row = db_session.get(KillSwitch, 1)
    assert row.active is True


def test_unkill_with_wrong_token_is_403(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)
    client.post("/kill", data={"reason": "x"})

    r = client.post("/unkill", headers={"X-Dashboard-Token": "wrong-token"})
    assert r.status_code == 403

    row = db_session.get(KillSwitch, 1)
    assert row.active is True


def test_unkill_with_right_token_is_200_and_inactive(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)
    client.post("/kill", data={"reason": "x"})

    r = client.post("/unkill", headers={"X-Dashboard-Token": "supersecret"})
    assert r.status_code == 200

    db_session.expire_all()
    row = db_session.get(KillSwitch, 1)
    assert row.active is False


def test_kill_and_unkill_write_operator_events(db_session, env_settings, tmp_path):
    """`/kill` and `/unkill` each write one `operator_events` row in the same transaction as
    the `kill_switch` row update (design spec §3.2), and a kill reason is sanitized the same
    way `harness.telemetry.sanitize_reason` sanitizes any other operator text."""
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)

    r = client.post("/kill", data={"reason": "manual pause <script>bad</script>"})
    assert r.status_code == 200

    kill_events = db_session.query(OperatorEvent).filter_by(kind="kill_on").all()
    assert len(kill_events) == 1
    assert "<" not in kill_events[0].summary and ">" not in kill_events[0].summary
    assert kill_events[0].summary == db_session.get(KillSwitch, 1).reason

    r = client.post("/unkill", headers={"X-Dashboard-Token": "supersecret"})
    assert r.status_code == 200

    unkill_events = db_session.query(OperatorEvent).filter_by(kind="kill_off").all()
    assert len(unkill_events) == 1
    assert unkill_events[0].summary == ""


def test_unkill_missing_token_file_is_403(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path, token_file_exists=False)
    client = _client(db_session, settings)
    client.post("/kill", data={"reason": "x"})

    r = client.post("/unkill", headers={"X-Dashboard-Token": "anything"})
    assert r.status_code == 403

    row = db_session.get(KillSwitch, 1)
    assert row.active is True


def test_page_and_summary_survive_two_active_primary_variants(db_session, env_settings, tmp_path):
    """Two active primary rows should never happen (load_variants enforces at most one at
    load time), but a hand-edited row or a registration race could still produce it. The
    dashboard must degrade to a deterministic choice instead of 500ing."""
    _seed_full(db_session, env_settings)
    duplicate_primary = next(v for v in load_variants(VARIANTS_DIR) if v.tier == "primary")
    db_session.add(StrategyVariant(
        variant_id="dup000000001",
        name="tiny_dup_primary",
        tier="primary",
        config_json=duplicate_primary.config,
        registered_at=NOW,
        active=True,
    ))
    db_session.commit()

    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)

    page = client.get("/")
    assert page.status_code == 200

    summary = client.get("/api/summary")
    assert summary.status_code == 200


def test_build_stamp_renders_from_settings(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path).model_copy(
        update={"build_sha": "abc1234", "build_time": "2026-09-07T06:00:00Z"}
    )
    client = _client(db_session, settings)

    page = client.get("/")
    assert page.status_code == 200
    assert "build abc1234" in page.text
    assert "deployed 2026-09-07T06:00:00Z" in page.text

    summary = client.get("/api/summary").json()
    assert summary["build"]["sha"] == "abc1234"

    healthz = client.get("/healthz").json()
    assert healthz["build"] == "abc1234"


def test_build_stamp_defaults_to_dev(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)

    page = client.get("/")
    assert page.status_code == 200
    assert "build dev" in page.text
    assert "deployed" not in page.text

    healthz = client.get("/healthz").json()
    assert healthz["build"] == "dev"


def test_healthz_keys_unchanged(db_session, env_settings, tmp_path):
    from harness.recorder.store import finish_run, start_run

    now = NOW
    run = start_run(db_session, now - timedelta(minutes=2))
    finish_run(db_session, run, "ok", n_requests=3, odds_remaining=4000, finished_at=now - timedelta(minutes=2))

    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings, clock=lambda: now)

    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    # U1 (2026-09-07): compute_health now also reports credits_budget and credits_low.
    # Task 6b (ruling A-C3): and venue_limits, the tier and buckets from GET /account/limits.
    assert set(body.keys()) == {"status", "last_run_at", "last_status", "seconds_since", "credits_remaining",
                                 "credits_budget", "credits_low", "build", "venue_limits"}
    assert body["venue_limits"] is None  # no reader on the Mac: no credential files
    assert body["status"] == "ok" and body["last_status"] == "ok" and body["credits_remaining"] == 4000
    assert body["build"] == "dev"


def test_a_failing_section_does_not_take_the_page_down(monkeypatch, db_session, env_settings, tmp_path):
    from sqlalchemy.exc import OperationalError

    import harness.dashboard.app as dash

    _seed_full(db_session, env_settings)

    def boom(*args, **kwargs):
        raise OperationalError("select 1", {}, Exception("canceling statement due to statement timeout"))

    monkeypatch.setattr(dash, "_websocket", boom)
    monkeypatch.setattr(dash, "_data_quality", boom)
    client = _client(db_session, _dashboard_settings(env_settings, tmp_path))

    page = client.get("/")
    assert page.status_code == 200
    assert "unavailable: OperationalError" in page.text
    summary = client.get("/api/summary").json()
    assert summary["websocket"] == {"error": "OperationalError"}
    assert summary["data_quality"] == {"error": "OperationalError"}
    assert "error" not in summary["funnel"]
    assert "tiny" in page.text  # the other sections still rendered after the rollback


# --- Task 12: executor, orders/fills, P&L, candidates, skips, database ceiling, data quality --


def _order_row(db_session, market, variant="tiny", side="yes", prob="0.40", contracts="10.00",
               status="open", book_source="ws", dirty_minutes=0, placed_at=None, replay=False) -> Order:
    o = Order(intent_id=uuid.uuid4(), variant_id=variant, venue="kalshi", mode="paper",
             client_order_id=f"co-{uuid.uuid4()}", ticker=market.ticker, venue_market_id=market.id,
             side=side, prob=Decimal(prob), contracts=Decimal(contracts), status=status,
             placed_at=placed_at or (NOW - timedelta(hours=1)), book_source=book_source,
             dirty_minutes=dirty_minutes, replay=replay)
    db_session.add(o)
    db_session.flush()
    return o


def _fill_row(db_session, order, contracts="10.00", prob=None, filled_at=None) -> Fill:
    f = Fill(order_id=order.id, prob=Decimal(prob) if prob else order.prob, contracts=Decimal(contracts),
            fee=Decimal("0.0100"), filled_at=filled_at or NOW, fill_method="queue_model",
            source_trade_id=f"tr-{uuid.uuid4()}", replay=order.replay)
    db_session.add(f)
    db_session.flush()
    return f


def _skip_event(db_session, reason, ts=None, replay=False) -> OrderEvent:
    e = OrderEvent(intent_id=uuid.uuid4(), ts=ts or (NOW - timedelta(minutes=5)), kind="skipped", reason=reason,
                   replay=replay)
    db_session.add(e)
    db_session.flush()
    return e


def test_each_new_section_renders_and_is_in_api_summary(db_session, env_settings, tmp_path):
    game, run, markets = _seed_full(db_session, env_settings)
    market = markets[0]

    db_session.add(ExecHeartbeat(id=1, last_loop_at=NOW - timedelta(seconds=10), loops=42, open_orders=1,
                                 last_loop_ms=120, p95_loop_ms=300, loops_skipped=0, book_dirty_markets=0,
                                 ws_last_event_at=NOW - timedelta(seconds=5), executor_version="v1"))
    open_order = _order_row(db_session, market, status="open")
    filled_order = _order_row(db_session, market, side="no", status="filled")
    _fill_row(db_session, filled_order, filled_at=NOW - timedelta(hours=1))
    db_session.add(Ledger(ts=NOW - timedelta(hours=1), variant_id="tiny", kind="fill", order_id=filled_order.id,
                          ticker=market.ticker, side="no", contracts=Decimal("10.00"), price=Decimal("0.40"),
                          fee=Decimal("0.01"), cash_delta=Decimal("-4.01"), replay=False))
    _skip_event(db_session, "fair_stale")
    db_session.add(JobRun(job="settle", started_at=NOW - timedelta(hours=1), finished_at=NOW - timedelta(hours=1),
                          status="ok", notes={"stages": [{"name": "housekeeping",
                                                          "counts": {"size_gb": 12.5, "tables_gb": {"orders": 1.0},
                                                                     "growth_gb_per_day": None,
                                                                     "days_to_ceiling": None, "partial": True},
                                                          "budget_exhausted": False, "error": None}]}))
    db_session.commit()

    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)

    r = client.get("/api/summary")
    assert r.status_code == 200
    body = r.json()
    for key in ("executor", "open_orders", "fills_today", "pnl", "candidates", "skip_reasons",
               "db_ceiling", "settlement_health"):
        assert key in body, key
        assert "error" not in body[key], (key, body[key])

    assert body["executor"]["present"] is True
    assert body["executor"]["loops"] == 42
    assert any(o["id"] == open_order.id for o in body["open_orders"])
    assert any(f["order_id"] == filled_order.id for f in body["fills_today"])
    assert body["pnl"]["by_variant_7d"]["tiny"]["cash_delta"] == -4.01
    assert body["skip_reasons"] == [{"reason": "fair_stale", "count": 1}]
    assert body["db_ceiling"]["measured"] is True
    assert body["db_ceiling"]["size_gb"] == 12.5

    page = client.get("/")
    assert page.status_code == 200
    assert str(open_order.id) in page.text
    assert "fair_stale" in page.text


def test_executor_colours(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)

    # Fresh heartbeat and a recent ws event: neither is red.
    db_session.add(ExecHeartbeat(id=1, last_loop_at=NOW - timedelta(seconds=30), loops=1, open_orders=0,
                                 ws_last_event_at=NOW - timedelta(seconds=30)))
    db_session.commit()
    body = _client(db_session, settings).get("/api/summary").json()
    assert body["executor"]["heartbeat_red"] is False
    assert body["executor"]["ws_red"] is False

    # A stale heartbeat (> 60s) and a stale ws event (> 120s): both red.
    db_session.query(ExecHeartbeat).filter_by(id=1).update(
        {"last_loop_at": NOW - timedelta(seconds=61), "ws_last_event_at": NOW - timedelta(seconds=121)})
    db_session.commit()
    body = _client(db_session, settings).get("/api/summary").json()
    assert body["executor"]["heartbeat_red"] is True
    assert body["executor"]["ws_red"] is True


def test_skip_breakdown(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)

    _skip_event(db_session, "fair_stale")
    _skip_event(db_session, "fair_stale")
    _skip_event(db_session, "kill_switch")
    _skip_event(db_session, "book_dirty", ts=NOW - timedelta(hours=30))  # outside the 24h window
    db_session.commit()

    body = _client(db_session, settings).get("/api/summary").json()
    counts = {row["reason"]: row["count"] for row in body["skip_reasons"]}
    assert counts == {"fair_stale": 2, "kill_switch": 1}


def test_kill_rejects_cross_site_and_accepts_same_origin_none_and_absent(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    client = _client(db_session, _dashboard_settings(env_settings, tmp_path))

    r = client.post("/kill", data={"reason": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert db_session.get(KillSwitch, 1) is None

    for header_value in ("same-origin", "none"):
        r = client.post("/kill", data={"reason": "x"}, headers={"Sec-Fetch-Site": header_value})
        assert r.status_code == 200

    r = client.post("/kill", data={"reason": "x"})  # no Sec-Fetch-Site header at all
    assert r.status_code == 200


def test_kill_reason_truncated_and_stripped(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    client = _client(db_session, _dashboard_settings(env_settings, tmp_path))

    dirty = "manual pause<script>alert(1)</script> " + ("x" * 250)
    r = client.post("/kill", data={"reason": dirty})
    assert r.status_code == 200
    body = r.json()
    assert len(body["reason"]) == 200
    assert "<" not in body["reason"] and ">" not in body["reason"]

    row = db_session.get(KillSwitch, 1)
    assert row.reason == body["reason"]
    assert len(row.reason) == 200


def test_data_quality_rows(db_session, env_settings, tmp_path):
    game, run, markets = _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)

    # Fee drift: the newest stored series body resolves to a different fee model than the
    # KALSHI_FOOTBALL default the executor assumes.
    db_session.add(RawResponse(run_id=run.id, source="kalshi", endpoint="/series/KXNFLGAME",
                               params={"series_ticker": "KXNFLGAME"}, http_status=200,
                               fetched_at=NOW - timedelta(minutes=10),
                               body={"series": {"fee_type": "quadratic", "fee_multiplier": "1"}}))
    _skip_event(db_session, POST_ONLY_REJECT, ts=NOW - timedelta(minutes=5))
    _skip_event(db_session, "fair_stale", ts=NOW - timedelta(minutes=5))
    # Fix 17 round 1: the no-taker-side denominator now comes from runs.notes'
    # `kalshi_trades_normalized`, not a live `venue_trades` count.
    run.notes = {"taker_side_missing": 1, "kalshi_trades_normalized": 3}
    db_session.commit()

    body = _client(db_session, settings).get("/api/summary").json()
    dq = body["data_quality"]
    assert dq["fee_drift"]["checked"] is True
    assert dq["fee_drift"]["fee_type"] == "quadratic"
    assert dq["fee_drift"]["drift"] is True
    assert dq["post_only_reject_rate_1h"] == 0.5
    assert "non_linear_cent_share_1h" in dq
    assert "nonzero_exchange_index_1h" in dq
    assert dq["no_taker_side_share_24h"] == 0.25


def test_no_taker_side_share_uses_matching_24h_windows(db_session, env_settings, tmp_path):
    """Fix round 1, I1: the brief's row is a 24h share. The numerator can only come from run
    notes (a dropped print never reaches venue_trades at all), so the fix widens the
    denominator to the same 24h window rather than trying to source the numerator from
    venue_trades. Fix 17 round 1: the denominator itself moved from a live `venue_trades` count
    to `runs.notes`' `kalshi_trades_normalized` (the same per-run sum `insert_trades` already
    writes for the rows it puts in `venue_trades`), so both halves of the ratio read from run
    notes over the same 24h window."""
    game, run, markets = _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)

    # 3 dropped prints and 1 normalized trade, both inside 24h but outside the old 1h
    # denominator window.
    old_run = Run(started_at=NOW - timedelta(hours=20), status="ok",
                 notes={"taker_side_missing": 3, "kalshi_trades_normalized": 1})
    db_session.add(old_run)
    db_session.commit()

    body = _client(db_session, settings).get("/api/summary").json()
    # denominator now spans the same 24h window as the numerator: 3 dropped / (3 dropped + 1
    # stored) = 0.75, not the 1h-denominator value of 3 / (3 + 0) = 1.0.
    assert body["data_quality"]["no_taker_side_share_24h"] == 0.75


def test_pre_fix17_run_rows_are_excluded_from_the_share_but_not_from_trade_gaps(db_session, env_settings, tmp_path):
    """Fix 17 round 2: a run row with no `kalshi_trades_normalized` key must feed neither half
    of the share (else it reads a false 1.0 right after deploy); `trade_gaps_24h` has no such
    asymmetry and keeps counting every row, keyed or not."""
    game, run, markets = _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)

    # Pre-fix-17 row: no `kalshi_trades_normalized` key at all.
    unkeyed_run = Run(started_at=NOW - timedelta(hours=2), status="ok",
                      notes={"taker_side_missing": 5, "trade_gaps": [{"ticker": "OLD"}]})
    db_session.add(unkeyed_run)
    # Post-fix-17 row: both halves keyed.
    run.notes = {"taker_side_missing": 1, "kalshi_trades_normalized": 3,
                "trade_gaps": [{"ticker": "NEW"}]}
    db_session.commit()

    body = _client(db_session, settings).get("/api/summary").json()
    dq = body["data_quality"]
    # The unkeyed row's 5 dropped prints must not appear in either half of the ratio: only the
    # keyed row's 1/(1+3) = 0.25 comes through, not 6/(6+3).
    assert dq["no_taker_side_share_24h"] == 0.25
    # trade_gaps has no such asymmetry: both rows' gaps still count.
    assert dq["trade_gaps_24h"] == 2


def test_replay_rows_excluded_from_open_orders_skip_reasons_and_post_only_reject_rate(
        db_session, env_settings, tmp_path):
    """Fix round 1, I2: a concurrent replay/backtest run writes into the same orders/order_events
    tables (`replay=True`) and must not leak into the live operator's readouts."""
    game, run, markets = _seed_full(db_session, env_settings)
    market = markets[0]
    settings = _dashboard_settings(env_settings, tmp_path)

    live_order = _order_row(db_session, market, status="open", replay=False)
    _order_row(db_session, market, side="no", status="open", replay=True)
    _skip_event(db_session, "fair_stale", replay=False)
    _skip_event(db_session, "fair_stale", replay=True)
    _skip_event(db_session, POST_ONLY_REJECT, replay=True)
    db_session.commit()

    body = _client(db_session, settings).get("/api/summary").json()

    order_ids = {o["id"] for o in body["open_orders"]}
    assert order_ids == {live_order.id}

    skip_counts = {row["reason"]: row["count"] for row in body["skip_reasons"]}
    assert skip_counts == {"fair_stale": 1}  # the replay fair_stale skip is invisible

    # Only the live fair_stale skip counts toward the denominator; the replay post_only_reject
    # must not appear in the numerator or the denominator.
    assert body["data_quality"]["post_only_reject_rate_1h"] == 0.0


def test_fee_drift_no_series_body_recorded(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)

    body = _client(db_session, settings).get("/api/summary").json()
    assert body["data_quality"]["fee_drift"] == {"checked": False}


def test_fee_drift_unsupported_fee_type(db_session, env_settings, tmp_path):
    game, run, markets = _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)

    db_session.add(RawResponse(run_id=run.id, source="kalshi", endpoint="/series/KXNFLGAME",
                               params={"series_ticker": "KXNFLGAME"}, http_status=200,
                               fetched_at=NOW - timedelta(minutes=10),
                               body={"series": {"fee_type": "flat", "fee_multiplier": "1"}}))
    db_session.commit()

    body = _client(db_session, settings).get("/api/summary").json()
    fd = body["data_quality"]["fee_drift"]
    assert fd["checked"] is True
    assert fd["fee_type"] == "flat"
    assert fd["drift"] is True
    assert fd["reason"] == "unsupported fee_type"


def test_new_list_and_dict_sections_show_unavailable_banner_on_failure(monkeypatch, db_session, env_settings,
                                                                        tmp_path):
    """Fix round 1, M1: open_orders/fills_today/pnl/candidates/skip_reasons must fail as visibly
    as every other section, not degrade to a silently-empty table."""
    from sqlalchemy.exc import OperationalError

    import harness.dashboard.app as dash

    _seed_full(db_session, env_settings)

    def boom(*args, **kwargs):
        raise OperationalError("select 1", {}, Exception("canceling statement due to statement timeout"))

    monkeypatch.setattr(dash, "_open_orders", boom)
    monkeypatch.setattr(dash, "_fills_today", boom)
    monkeypatch.setattr(dash, "_pnl", boom)
    monkeypatch.setattr(dash, "_candidates", boom)
    monkeypatch.setattr(dash, "_skip_reasons", boom)
    client = _client(db_session, _dashboard_settings(env_settings, tmp_path))

    page = client.get("/")
    assert page.status_code == 200
    assert page.text.count("unavailable: OperationalError") >= 5
    summary = client.get("/api/summary").json()
    for key in ("open_orders", "fills_today", "pnl", "candidates", "skip_reasons"):
        assert summary[key] == {"error": "OperationalError"}, key


def test_settlement_health_reports_mismatches_and_stale_unsettled(db_session, env_settings, tmp_path):
    game, run, markets = _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)

    ticker = markets[0].ticker
    db_session.add(VenueSettlement(venue="kalshi", ticker=ticker, source="derived", result="yes",
                                   payout=Decimal("1"), settled_at=NOW - timedelta(hours=1)))
    db_session.add(VenueSettlement(venue="kalshi", ticker=ticker, source="venue", result="no",
                                   payout=Decimal("0"), settled_at=NOW - timedelta(hours=1)))
    db_session.commit()

    body = _client(db_session, settings).get("/api/summary").json()
    sh = body["settlement_health"]
    assert sh["mismatch_count_7d"] == 1
    assert sh["mismatches_7d"][0]["ticker"] == ticker
    assert sh["stale_unsettled"] == 0


# --- Fix 15: the funnel reads runs.notes instead of scanning the pricing tables --------------

def test_funnel_sums_pricing_counts_from_run_notes(db_session, env_settings):
    """`_funnel`'s `fair_by_source`, `gaps` and `signals_by_variant` come from per-run sums of
    `runs.notes->'pricing'` over the trailing 24h, not from scanning `fair_values`,
    `market_gap_snapshots` or `signals` -- `_seed_full` below writes real (and different)
    counts into those three tables, so this only passes once the notes' numbers, not the
    tables', are what comes back (fix 15, journal 44: a 24h scan of those tables took 86-92s
    against the 10s bound and blew the statement timeout after a restart)."""
    game, run, markets = _seed_full(db_session, env_settings)
    run.notes = {"pricing": {"fair_direct": 111, "fair_derived": 222, "gaps": 333,
                             "signals": {"tiny": {"candidate": 44, "rejected": 55}}}}
    db_session.add(Run(started_at=NOW - timedelta(hours=2), status="ok",
                       notes={"pricing": {"fair_direct": 1, "fair_derived": 2, "gaps": 3,
                                          "signals": {"tiny": {"candidate": 1, "rejected": 1}}}}))
    db_session.flush()

    result = _funnel(db_session, NOW)

    assert result["fair_by_source"] == {"direct": 112, "derived": 224}
    assert result["gaps"] == 336
    assert result["signals_by_variant"]["tiny"]["candidate"] == 45
    assert result["signals_by_variant"]["tiny"]["rejected"] == 56
    assert result["signals_by_variant"]["tiny"]["tier"] == "primary"


def test_funnel_issues_no_statement_against_the_pricing_tables(db_session, env_settings):
    """The bounded query over `runs` must be the whole story: no statement `_funnel` issues
    may touch `fair_values`, `market_gap_snapshots` or `signals` (fix 15)."""
    _seed_full(db_session, env_settings)  # writes real rows to all three pricing tables
    engine = db_session.get_bind()
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        _funnel(db_session, NOW)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    joined = "\n".join(statements).lower()
    assert "fair_values" not in joined
    assert "market_gap_snapshots" not in joined
    assert "signals" not in joined


# --- Fix round 1: review findings -------------------------------------------------------

def test_funnel_aggregates_the_full_24h_window_not_just_the_row_cap(db_session, env_settings):
    """Fix round 1, Important 2: `RUNS_NOTES_LIMIT` (500) must not silently truncate the
    funnel's "24h" scan -- at the default 30s heartbeat that is only ~4.2 hours in production.
    A pricing-bearing run just inside the 24h window, with 510 newer (pricing-empty) heartbeat
    runs stacked after it, must still contribute; before the fix the old run falls past the
    500-row cap and its counts are dropped."""
    old_run = Run(started_at=NOW - timedelta(hours=23, minutes=55), status="ok",
                 notes={"pricing": {"fair_direct": 7, "fair_derived": 0, "gaps": 0, "signals": {}}})
    db_session.add(old_run)
    db_session.flush()
    heartbeats = [
        {"started_at": NOW - timedelta(hours=23, minutes=54) + timedelta(seconds=i * 5),
         "status": "skipped", "notes": {"pricing": {}}, "n_requests": 0, "credits_used": 0,
         "budget_exhausted": False}
        for i in range(510)
    ]
    db_session.execute(insert(Run), heartbeats)
    db_session.flush()

    result = _funnel(db_session, NOW)
    assert result["fair_by_source"]["direct"] == 7


def test_funnel_section_reports_unavailable_on_a_malformed_pricing_note(db_session, env_settings, tmp_path):
    """Fix round 1, Minor 4: a malformed `pricing` note (wrong shape -- here a string where a
    dict is expected) must degrade only the funnel section, via `_section`'s "unavailable"
    contract, not take down the whole `/api/summary` page."""
    db_session.add(Run(started_at=NOW, status="ok", notes={"pricing": "not-a-dict"}))
    db_session.commit()
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)

    r = client.get("/api/summary")
    assert r.status_code == 200
    body = r.json()
    assert "error" in body["funnel"]
    assert "health" in body  # the rest of the page still renders


# --- Fix 17: candidates served from run notes, not a live scan of `signals` ------------------

def test_candidates_sums_pricing_counts_from_run_notes_ignoring_stale_rows(db_session, env_settings):
    """`_candidates` sources its per-variant counts from `runs.notes->'pricing'->'signals'`
    (fix 17, journal 44/48), the same per-run sums `_funnel`'s `signals_by_variant` uses --
    not from a live group-by over `signals`. `_seed_full` writes real (and different) rows to
    `signals` via `price_and_signal`, so this only passes if the notes' numbers, not the
    table's, come back, and a run older than the 24h window is ignored."""
    game, run, markets = _seed_full(db_session, env_settings)
    run.notes = {"pricing": {"signals": {"tiny": {"candidate": 44, "rejected": 55}}}}
    db_session.add(Run(started_at=NOW - timedelta(hours=2), status="ok",
                       notes={"pricing": {"signals": {"tiny": {"candidate": 1, "rejected": 1}}}}))
    db_session.add(Run(started_at=NOW - timedelta(hours=25), status="ok",
                       notes={"pricing": {"signals": {"tiny": {"candidate": 1000, "rejected": 1000}}}}))
    db_session.flush()

    result = _candidates(db_session, NOW)

    assert result["by_variant"]["tiny"]["candidate"] == 45
    assert result["sides"] == "not split: served from run notes (fix 17)"


def test_funnel_candidates_and_data_quality_issue_no_statement_against_signal_tables(db_session, env_settings):
    """Fix 17 (journal 44/48) plus round 1 (roadmap row 17, Important): none of these three
    sections, called the way `build_summary` calls them (a shared `run_notes_24h` fetched once,
    passed to all three), may issue a statement against `signals`, `fair_values`,
    `market_gap_snapshots` or `venue_trades` -- even though `_seed_full` writes real rows to the
    first three via `price_and_signal`, and this test adds a real `venue_trades` row too. The
    previous `_candidates` grouped `signals` by variant and side directly (43s against the 10s
    page-time bound on the NAS's season-sized `signals` table); the previous `_data_quality`
    counted `venue_trades` live for the no-taker-side denominator, unindexed on `source`."""
    game, run, markets = _seed_full(db_session, env_settings)
    db_session.add(VenueTrade(venue="kalshi", trade_id="t-1", ticker=markets[0].ticker, ts=NOW,
                              yes_price=Decimal("0.40"), count=Decimal("1"), taker_side="yes", source="rest"))
    db_session.commit()
    engine = db_session.get_bind()
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        run_notes_24h = _recent_run_notes(db_session, NOW - WINDOW_24H)
        _funnel(db_session, NOW, run_notes_24h)
        _candidates(db_session, NOW, run_notes_24h)
        _data_quality(db_session, NOW, run_notes_24h)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    joined = "\n".join(statements).lower()
    assert "fair_values" not in joined
    assert "market_gap_snapshots" not in joined
    assert "signals" not in joined
    assert "venue_trades" not in joined


# --- Fix 19: dashboard cold first request -- ws_trades_1h from metric_samples, not venue_trades ---

def _ws_sample(ts, value, name="ws.trades_per_min", source="ws"):
    return MetricSample(ts=ts, source=source, name=name, value=Decimal(str(value)))


def test_ws_trades_1h_sums_ws_trades_per_min_metric_samples(db_session, env_settings):
    """`ws_trades_1h` sums the last hour's `ws.trades_per_min` metric_samples (fix 19, verify
    2026-09-08 09:28 CT), not a live count against `venue_trades`. A sample outside the 1h
    window, one under a different metric name, one under a different source, and a real
    `venue_trades` row with `source = 'ws'` inside the window must all be excluded -- only the
    two matching per-minute samples (12 + 30) should be summed."""
    db_session.add_all([
        _ws_sample(NOW - timedelta(minutes=50), 12),
        _ws_sample(NOW - timedelta(minutes=10), 30),
        _ws_sample(NOW - timedelta(hours=2), 999),  # outside the 1h window
        _ws_sample(NOW - timedelta(minutes=5), 500, name="ws.events_per_min"),  # wrong metric
        _ws_sample(NOW - timedelta(minutes=5), 777, source="exec"),  # wrong source
    ])
    db_session.add(VenueTrade(venue="kalshi", trade_id="t-1", ticker="KXNFL-1", ts=NOW - timedelta(minutes=1),
                              yes_price=Decimal("0.40"), count=Decimal("1"), taker_side="yes", source="ws"))
    db_session.flush()

    result = _websocket(db_session, NOW)

    assert result["ws_trades_1h"] == 42


def test_ws_trades_1h_is_zero_with_no_samples(db_session, env_settings):
    """No `ws.trades_per_min` rows yet (e.g. right after a fresh deploy) must read 0, not NULL
    or an error -- the sum is coalesced."""
    result = _websocket(db_session, NOW)
    assert result["ws_trades_1h"] == 0


def test_websocket_issues_no_statement_against_venue_trades(db_session, env_settings):
    """`_websocket` must not scan `venue_trades` at all (fix 19): the old live count over
    `source = 'ws' and ts >= now() - 1h` had no covering index for the `source` filter and, on
    the NAS after a Postgres restart, took 21.6s cold against the 10s page-time bound (verify
    2026-09-08 09:28 CT)."""
    db_session.add(VenueTrade(venue="kalshi", trade_id="t-1", ticker="KXNFL-1", ts=NOW,
                              yes_price=Decimal("0.40"), count=Decimal("1"), taker_side="yes", source="ws"))
    db_session.commit()
    engine = db_session.get_bind()
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        _websocket(db_session, NOW)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert "venue_trades" not in "\n".join(statements).lower()


def test_build_summary_issues_no_statement_against_venue_trades(db_session, env_settings, tmp_path):
    """The whole page -- not just `_websocket` in isolation -- must never touch `venue_trades`
    (fix 19)."""
    _seed_full(db_session, env_settings)
    db_session.add(VenueTrade(venue="kalshi", trade_id="t-1", ticker="KXNFL-1", ts=NOW,
                              yes_price=Decimal("0.40"), count=Decimal("1"), taker_side="yes", source="ws"))
    db_session.commit()
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)
    engine = db_session.get_bind()
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        r = client.get("/api/summary")
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert r.status_code == 200
    assert "venue_trades" not in "\n".join(statements).lower()


# --- Fix 19 addendum: clamp executor ages at 0, not negative -----------------------------

def test_executor_ages_clamp_at_zero_not_negative(db_session, env_settings):
    """A heartbeat written between the SQL read and the request's `now` (a `last_loop_at` a
    fraction of a second in the future), or a WS event timestamped ahead of the NAS clock (up
    to ~7s), must read 0.0 -- not a negative age that the template's "%.0f" rounds to a
    misleading "-1" (fix 19 addendum, walkthrough 2026-09-08 09:28 CT). Neither is stale, so
    0.0 is the honest floor, and the red thresholds are unaffected either way."""
    db_session.add(ExecHeartbeat(id=1, last_loop_at=NOW + timedelta(milliseconds=600), loops=1,
                                 open_orders=0, ws_last_event_at=NOW + timedelta(seconds=7)))
    db_session.flush()

    result = _executor(db_session, NOW)

    assert result["heartbeat_age_s"] == 0.0
    assert result["ws_last_event_age_s"] == 0.0
    assert result["heartbeat_red"] is False
    assert result["ws_red"] is False


def test_executor_ages_stay_none_without_timestamps(db_session, env_settings):
    """Clamping must not turn a missing timestamp into a fake 0.0."""
    db_session.add(ExecHeartbeat(id=1, loops=0, open_orders=0))
    db_session.flush()

    result = _executor(db_session, NOW)

    assert result["heartbeat_age_s"] is None
    assert result["ws_last_event_age_s"] is None


def test_executor_heartbeat_renders_green_when_not_stale(db_session, env_settings, tmp_path):
    """verify.md item 10: "Executor block in Health: heartbeat age under 60s, rendered
    green." Before this fix the value was bare text with a "stale" badge only when red -- no
    positive colour cue when it wasn't."""
    _seed_full(db_session, env_settings)
    db_session.add(ExecHeartbeat(id=1, last_loop_at=NOW - timedelta(seconds=5), loops=1, open_orders=0))
    db_session.commit()
    settings = _dashboard_settings(env_settings, tmp_path)
    page = _client(db_session, settings).get("/")

    match = re.search(r"Heartbeat age \(s\).*?</div></div>", page.text, re.S)
    assert match is not None
    fragment = match.group(0)
    assert 'class="badge ok"' in fragment
    assert "badge bad" not in fragment
