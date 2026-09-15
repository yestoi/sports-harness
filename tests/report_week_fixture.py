"""A week-shaped fixture for the weekly report's tables, sized by one knob.

Finding 49 round 3: the settle job's `report_wtd` stage steps the recorder process up ~370 MiB
on the live week and the process never gives it back. `weekly_tables` is the only thing in that
stage that touches a week of rows, so a measurement of it needs a week of rows in the shapes
`harness/report/tables.py` actually reads -- not a handful of rows like `tests/test_report.py`'s
correctness fixtures, and not a guess at the shapes either: every insert below is keyed to the
query that reads it, named in its own comment.

Everything is written with Core `executemany`, never the ORM: seeding a few hundred thousand
rows through the identity map would be slower than the render being measured and would put the
rig's own retention inside the measurement.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import insert, text

from harness.db.models import (
    Benchmark,
    FairValue,
    Fill,
    Game,
    GapOutcome,
    MarketGapSnapshot,
    Markout,
    Order,
    OrderClv,
    OrderbookEvent,
    Run,
    Signal,
    StrategyVariant,
    Team,
    VenueMarket,
)
from harness.db.schema import ensure_partitions

#: ISO week 37 of 2026: Monday 2026-09-07 05:00Z through 2026-09-14 05:00Z. Chosen over week 38
#: because `conftest`'s session fixture builds `orderbook_events` partitions for [this Monday,
#: +14 d) and this week sits wholly inside that range whatever day the suite runs; the seeder
#: calls `ensure_partitions` for its own week anyway.
YEAR, WEEK = 2026, 37
WEEK_START = datetime(2026, 9, 7, 5, 0, tzinfo=timezone.utc)
WEEK_END = WEEK_START + timedelta(days=7)

SPORTS = ("nfl", "ncaaf")
MARKET_TYPES = ("moneyline", "spread", "total")
FAIR_SOURCES = ("direct", "derived")
VARIANTS = ("sharp_direct", "sharp_derivd", "wide_band")

#: One run's `notes['pricing']` block at roughly the shape `harness/recorder/tick.py` writes:
#: t13 reads up to `T13_NOTES_LIMIT` of these (`recent_runs_pricing`), so their size is part of
#: the render's working set.
_STAGE_NAMES = ("fair_direct", "fair_derived", "gaps", "strategy", "as_measured", "persist")


def _pricing_note(i: int) -> dict:
    return {
        "ticks": 1,
        "gaps": 700 + (i % 40),
        "fair_direct": 190 + (i % 20),
        "fair_derived": 410 + (i % 30),
        "budget_exhausted": bool(i % 17 == 0),
        "no_sharp": 2100 + (i % 60),
        "stages": [{"name": name, "elapsed_ms": 100 + (i % 900)} for name in _STAGE_NAMES],
        "signals": {v: {"candidate": i % 5, "rejected": 20 + (i % 30),
                        "reasons": {"edge": i % 9, "price_band": i % 7, "stale": i % 4}}
                    for v in VARIANTS},
        "variants_run": list(VARIANTS),
    }


def _chunks(rows, size=5000):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def _bulk(session, model, rows):
    for chunk in _chunks(rows):
        session.execute(insert(model), chunk)
    session.commit()
    return len(rows)


def seed_week(session, *, games=40, gaps_per_market=90, fairs_per_shape=60,
              snapshots_per_ticker=400, orders=600, runs=3000) -> dict:
    """Seed one ISO week and return the row counts. Scales linearly in every knob."""
    ensure_partitions(session, WEEK_START)
    ensure_partitions(session, WEEK_END - timedelta(days=1))
    counts: dict[str, int] = {}

    # --- teams, games, markets -------------------------------------------------------------
    teams = []
    for sport in SPORTS:
        for n in range(2 * games):
            tid = 7000 + n
            teams.append({"sport": sport, "id": tid, "display_name": f"{sport}{n}",
                          "location": f"L{n}", "name": f"N{n}", "abbreviation": f"A{n:03d}",
                          "short_display_name": f"S{n}", "popularity_tier": n % 3})
    counts["teams"] = _bulk(session, Team, teams)

    game_rows = []
    for g in range(games):
        sport = SPORTS[g % len(SPORTS)]
        kickoff = WEEK_START + timedelta(hours=24 + (g % 6) * 24 + (g % 5))
        game_rows.append({"sport": sport, "home_team_id": 7000 + 2 * g,
                          "away_team_id": 7000 + 2 * g + 1, "kickoff_utc": kickoff,
                          "status": "final", "home_score": 21 + g % 14,
                          "away_score": 17 + g % 11})
    counts["games"] = _bulk(session, Game, game_rows)
    rows = session.execute(text(
        "select id, sport, home_team_id, kickoff_utc from games order by id")).all()
    games_meta = [(r.id, r.sport, r.home_team_id, r.kickoff_utc) for r in rows]

    market_rows = []
    for gid, sport, home, kickoff in games_meta:
        for mt in MARKET_TYPES:
            for k in range(2):
                threshold = None if mt == "moneyline" else Decimal(str(3.5 + 3 * k))
                market_rows.append({
                    "venue": "kalshi", "ticker": f"KX{gid:05d}{mt[:2].upper()}{k}",
                    "event_ticker": f"KXEVT-{gid}", "series_ticker": "KXNFLGAME",
                    "game_id": gid, "market_type": mt, "threshold": threshold,
                    "side_team_id": home if mt != "total" else None,
                    "side": None if mt != "total" else ("over" if k == 0 else "under"),
                    "match_confidence": Decimal("1.00"), "match_status": "matched",
                    "match_reason": "", "first_seen_raw_id": 1,
                    "last_seen_at": kickoff - timedelta(hours=2),
                    "price_level_structure": "linear_cent", "fee_type": "quadratic",
                    "fee_multiplier": Decimal("0.0700"), "exchange_index": 0})
    counts["venue_markets"] = _bulk(session, VenueMarket, market_rows)
    markets = session.execute(text(
        "select m.id, m.ticker, m.market_type, m.threshold, m.game_id, m.side_team_id, m.side,"
        " g.sport, g.kickoff_utc from venue_markets m join games g on g.id = m.game_id"
        " order by m.id")).all()

    counts["strategy_variants"] = _bulk(session, StrategyVariant, [
        {"variant_id": v, "name": f"variant {v}", "tier": "primary" if i == 0 else "secondary",
         "config_json": {}, "registered_at": WEEK_START, "active": True}
        for i, v in enumerate(VARIANTS)])

    # --- runs (t1 pricing ticks, t8 credits, t13 `recent_runs_pricing`) ---------------------
    run_rows = []
    for i in range(runs):
        started = WEEK_START + timedelta(seconds=int(i * (7 * 86400) / max(runs, 1)))
        run_rows.append({"started_at": started, "finished_at": started + timedelta(seconds=40),
                         "status": "ok", "n_requests": 12, "credits_used": 5,
                         "odds_remaining": 4000, "budget_exhausted": False,
                         "notes": {"pricing": _pricing_note(i), "normalized": {"kalshi": 12},
                                   "errors": [], "warnings": []},
                         "build_sha": "abc1234"})
    counts["runs"] = _bulk(session, Run, run_rows)

    # --- market_gap_snapshots (+ gap_outcomes): tables 1, 2 and 4 ---------------------------
    # `_T4_SNAPSHOTS` reads every row of the week with a `fair_p`, joined to its market, game and
    # `gap_outcomes` row, and materialises the lot in Python.
    price_mids = ("0.2500", "0.4000", "0.5500", "0.7000")
    ttk_choices = (2000, 600, 90)
    stale_choices = (60, 200, 600, 1500)
    feeds = ("featured", "featured", "featured", "alternate")
    gap_rows = []
    run_id = 1
    for idx, m in enumerate(markets):
        for j in range(gaps_per_market):
            created = WEEK_START + timedelta(
                seconds=int(((idx * gaps_per_market + j) * 7 * 86400)
                            / max(len(markets) * gaps_per_market, 1)))
            run_id += 1
            gap_rows.append({
                "run_id": run_id, "venue_market_id": m.id,
                "fair_source": FAIR_SOURCES[j % 2],
                "fair_p": Decimal("0.5500"),
                "venue_mid": Decimal(price_mids[j % 4]),
                "best_bid": Decimal("0.4900"), "best_ask": Decimal("0.5100"),
                "n_groups": 2, "staleness_s": stale_choices[j % 4],
                "feed_kind": feeds[j % 4], "feed_lag_s": 30,
                "gap_mid": Decimal(str(round(0.01 + 0.001 * (j % 40) - 0.02, 4))),
                "gap_maker_net": Decimal(str(round(0.005 + 0.001 * (j % 30) - 0.015, 4))),
                "gap_taker_net": Decimal("0.0010"),
                "ttk_minutes": ttk_choices[j % 3], "dow": created.weekday(),
                "hour_ct": created.hour, "created_at": created})
    counts["market_gap_snapshots"] = _bulk(session, MarketGapSnapshot, gap_rows)
    gap_ids = [r[0] for r in session.execute(text(
        "select id from market_gap_snapshots order by id")).all()]
    counts["gap_outcomes"] = _bulk(session, GapOutcome, [
        {"gap_snapshot_id": gid, "benchmark_type": "pinnacle_t5",
         "p_bench": Decimal("0.5600"), "clv_mid_p": Decimal(str(round(0.004 * ((i % 9) - 4), 4))),
         "p_used_kind": "target"}
        for i, gid in enumerate(gap_ids)])

    # --- signals (t1, t12) ------------------------------------------------------------------
    signal_rows = []
    for i, gid in enumerate(gap_ids[::3]):
        m = markets[i % len(markets)]
        signal_rows.append({
            "run_id": 1_000_000 + i, "variant_id": VARIANTS[i % len(VARIANTS)],
            "gap_snapshot_id": gid, "venue_market_id": m.id, "side": "yes",
            "price_target": Decimal("0.5000"),
            "decision": "candidate" if i % 7 == 0 else "rejected",
            "rejection_reason": None if i % 7 == 0 else ("edge" if i % 2 else "price_band"),
            "labels": {}, "replay": False,
            "created_at": WEEK_START + timedelta(seconds=int(i * 7 * 86400 / max(len(gap_ids) // 3, 1)))})
    counts["signals"] = _bulk(session, Signal, signal_rows)

    # --- fair_values: `_T4B_FAIRS` (whole week) and `_T5_FAIRS` (whole week, ordered) --------
    fair_rows = []
    rid = 5_000_000
    for gid, sport, home, kickoff in games_meta:
        for mt in MARKET_TYPES:
            for k in range(2):
                threshold = None if mt == "moneyline" else Decimal(str(3.5 + 3 * k))
                for j in range(fairs_per_shape):
                    rid += 1
                    created = WEEK_START + timedelta(
                        seconds=int(j * 7 * 86400 / max(fairs_per_shape, 1)))
                    # A saw-tooth of >= MOVE_PTS steps, so t5 finds sharp moves to time.
                    fair = 0.50 + (0.03 if j % 2 else -0.03)
                    for source in FAIR_SOURCES:
                        fair_rows.append({
                            "run_id": rid, "game_id": gid, "market_type": mt,
                            "outcome_team_id": home if mt != "total" else None,
                            "outcome_side": None if mt != "total" else ("over" if k == 0 else "under"),
                            "threshold": threshold,
                            "fair_p": Decimal(str(round(fair + (0.002 if source == "derived" else 0), 4))),
                            "fair_source": source, "n_groups": 2,
                            "newest_book_ts": created, "staleness_s": 45 + (j % 300),
                            "feed_kind": "featured", "feed_lag_s": 20 + (j % 120),
                            "stale_allowance_s": 220, "pricing_version": "p1",
                            "created_at": created})
    counts["fair_values"] = _bulk(session, FairValue, fair_rows)

    # --- orderbook_events: `_T5_SNAPSHOTS` parses every snapshot body into a BookState -------
    ob_rows = []
    tickers = [m.ticker for m in markets]
    for t_i, ticker in enumerate(tickers):
        for j in range(snapshots_per_ticker):
            ts = WEEK_START + timedelta(seconds=int(j * 7 * 86400 / max(snapshots_per_ticker, 1)))
            yes = f"0.{45 + (j % 10):02d}00"
            no = f"0.{45 + ((j + 3) % 10):02d}00"
            ob_rows.append({
                "ticker": ticker, "ts": ts, "sid": 1, "seq": j + 1, "kind": "snapshot",
                "raw": {"market_ticker": ticker,
                        "yes_dollars_fp": [[yes, "50.00"], [f"0.{40 + (j % 5):02d}00", "25.00"]],
                        "no_dollars_fp": [[no, "50.00"], [f"0.{40 + (j % 5):02d}00", "25.00"]]}})
    counts["orderbook_events"] = _bulk(session, OrderbookEvent, ob_rows)

    # --- orders, fills, clv, markouts: tables 1, 2, 3, 6, 13 --------------------------------
    import uuid as _uuid

    order_rows = []
    for i in range(orders):
        m = markets[i % len(markets)]
        placed = WEEK_START + timedelta(seconds=int(i * 7 * 86400 / max(orders, 1)))
        order_rows.append({
            "intent_id": _uuid.uuid4(), "variant_id": VARIANTS[i % len(VARIANTS)],
            "venue": "kalshi", "mode": "paper", "client_order_id": f"co-{i}-{_uuid.uuid4()}",
            "ticker": m.ticker, "venue_market_id": m.id, "side": "yes",
            "prob": Decimal("0.5000"), "contracts": Decimal("10.00"),
            "status": "filled" if i % 3 else "cancelled", "placed_at": placed,
            "cancel_reason": None if i % 3 else "expiry",
            "cancelled_at": None if i % 3 else placed + timedelta(minutes=9),
            "fair_p_at_place": Decimal("0.5500"), "venue_mid_at_place": Decimal("0.5000"),
            "queue_ahead_at_place": Decimal("20.00"), "book_source": "ws", "book_age_s": 3,
            "staleness_at_place": 60 + (i % 400), "feed_kind": "featured",
            "config_hash": "c" * 8, "game_id": m.game_id, "sport": m.sport,
            "kickoff_utc": m.kickoff_utc, "traded_at_price": Decimal("10.00"),
            "replay": False})
    counts["orders"] = _bulk(session, Order, order_rows)
    order_ids = [r[0] for r in session.execute(text(
        "select id from orders order by id")).all()]

    fill_rows = []
    for i, oid in enumerate(order_ids):
        if i % 3 == 0:
            continue
        for method in ("queue_model", "no_watcher", "snapshot_cross"):
            fill_rows.append({
                "order_id": oid, "prob": Decimal("0.5000"), "contracts": Decimal("10.00"),
                "fee": Decimal("0.2500"), "filled_at": WEEK_START + timedelta(minutes=5 + i),
                "simulated": True, "fill_method": method, "tape_source": "ws",
                "through": False, "has_print": True, "replay": False})
    counts["fills"] = _bulk(session, Fill, fill_rows)

    counts["order_clv"] = _bulk(session, OrderClv, [
        {"order_id": oid, "benchmark_type": b, "p_bench": Decimal("0.5600"),
         "p_used": Decimal("0.5000"), "p_used_kind": "order", "clv_p": Decimal("0.0250"),
         "clv_p_net": Decimal(str(round(0.004 * ((i % 11) - 5), 4))),
         "clv_roi_net": Decimal("0.0400"), "stale": False}
        for i, oid in enumerate(order_ids)
        for b in ("pinnacle_t5", "kalshi_last_trade_pre_kick")])

    markout_rows = []
    for i, oid in enumerate(order_ids):
        if i % 3 == 0:
            continue
        for anchor in ("fill", "nw_fill", "cross_fill"):
            for horizon in ("5m", "30m"):
                markout_rows.append({
                    "order_id": oid, "anchor": anchor, "horizon": horizon,
                    "at_ts": WEEK_START + timedelta(minutes=5 + i),
                    "horizon_ts": WEEK_START + timedelta(minutes=10 + i),
                    "p_used": Decimal("0.5000"), "fee_per_contract": Decimal("0.0025"),
                    "fair_p": Decimal(str(round(0.55 + 0.002 * ((i % 7) - 3), 4))),
                    "fair_changed": True, "venue_mid": Decimal("0.5200"), "mid_age_s": 5,
                    "source": "quote"})
    counts["markouts"] = _bulk(session, Markout, markout_rows)

    counts["benchmarks"] = _bulk(session, Benchmark, [
        {"game_id": gid, "market_type": "moneyline", "outcome_team_id": home,
         "benchmark_type": "pinnacle_t5", "p": Decimal("0.5600"), "stale": False,
         "kickoff_moved": False, "target_ts": kickoff - timedelta(minutes=5),
         "source_ts": kickoff - timedelta(minutes=10),
         "created_at": kickoff - timedelta(minutes=5)}
        for gid, sport, home, kickoff in games_meta])

    counts["_bytes_of_run_notes"] = sum(len(json.dumps(r["notes"])) for r in run_rows)
    return counts
