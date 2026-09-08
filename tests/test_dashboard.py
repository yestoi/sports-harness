import uuid
from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from harness.dashboard.app import create_dashboard
from harness.db.models import (ExecHeartbeat, Fill, JobRun, KillSwitch, Ledger, Order, OrderEvent, RawResponse,
                                Run, StrategyVariant, VenueSettlement, VenueTrade)
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
    assert set(body.keys()) == {"status", "last_run_at", "last_status", "seconds_since", "credits_remaining",
                                 "credits_budget", "credits_low", "build"}
    assert body["status"] == "ok" and body["last_status"] == "ok" and body["credits_remaining"] == 4000
    assert body["build"] == "dev"


def test_a_failing_section_does_not_take_the_page_down(monkeypatch, db_session, env_settings, tmp_path):
    from sqlalchemy.exc import OperationalError

    import harness.dashboard.app as dash

    _seed_full(db_session, env_settings)

    def boom(session, now):
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
    db_session.commit()

    body = _client(db_session, settings).get("/api/summary").json()
    dq = body["data_quality"]
    assert dq["fee_drift"]["checked"] is True
    assert dq["fee_drift"]["fee_type"] == "quadratic"
    assert dq["fee_drift"]["drift"] is True
    assert dq["post_only_reject_rate_1h"] == 0.5
    assert "non_linear_cent_share_1h" in dq
    assert "nonzero_exchange_index_1h" in dq
    assert "no_taker_side_share_24h" in dq


def test_no_taker_side_share_uses_matching_24h_windows(db_session, env_settings, tmp_path):
    """Fix round 1, I1: the brief's row is a 24h share. The numerator can only come from run
    notes (a dropped print never reaches venue_trades at all), so the fix widens the
    denominator to the same 24h window rather than trying to source the numerator from
    venue_trades."""
    game, run, markets = _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)

    # 3 dropped prints, all inside 24h but outside the old 1h denominator window.
    old_run = Run(started_at=NOW - timedelta(hours=20), status="ok",
                 notes={"taker_side_missing": 3})
    db_session.add(old_run)
    # 1 stored trade, also outside 1h but inside 24h.
    db_session.add(VenueTrade(venue="kalshi", trade_id="t-old", ticker=markets[0].ticker,
                              ts=NOW - timedelta(hours=20), yes_price=Decimal("0.40"),
                              count=Decimal("1"), taker_side="yes", source="rest"))
    db_session.commit()

    body = _client(db_session, settings).get("/api/summary").json()
    # denominator now spans the same 24h window as the numerator: 3 dropped / (3 dropped + 1
    # stored) = 0.75, not the 1h-denominator value of 3 / (3 + 0) = 1.0.
    assert body["data_quality"]["no_taker_side_share_24h"] == 0.75


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
