"""Floor: what is it doing right now?

Spec §2.2. Six sections in reading order: the game board, the funnel, the resting simulated
orders with their queue bars, today's fills, the exposure lanes, and the executor's vitals --
plus the venue tile the roadmap's phase 4.5 item 5 adds.

**Where the funnel's numbers come from, and why it matters.** Ticks, gaps, candidates and
rejections are per-run sums out of `runs.notes`, read through `harness.dashboard.queries` --
the same path the legacy funnel has used since fix 15. A direct 6 h scan of the gap and signal
tables took 86-92 s against the legacy page's 10 s bound, because `ix_gap_market_created` leads
on market and `ix_signal_variant_created` leads on variant, and neither can serve a bare
`created_at` predicate. Under this phase's statement timeout that section would be permanently
`{"error": ...}` -- the centre panel of the surface, dead on every game day.

Only `intents`, `order_events`, `orders` and `fills` are read directly here, and each is
bounded by **size**, not by an index. Of the four, only `fills` has a time-leading index
(`ix_fills_filled_at`): `intents` carries `ix_intents_key` alone, `order_events` only its two
partial unique indexes, and `orders` has nothing on `placed_at`. All four take thousands of
rows a day, so a 6 h aggregate over any of them scans comfortably inside the statement timeout.
No index is added here -- that would be a second decision -- and the Floor `serve.snapshot_ms`
row in verify.md is what watches the assumption rather than an index that does not exist.

**Never shown here.** CLV, markouts or any figure that judges the strategy. Floor shows
activity, not quality, so that a good afternoon of fills is never mistaken for edge.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.dashboard import sentences
from harness.dashboard.queries import (WINDOW_6H, local_day_bounds_utc, recent_run_notes,
                                       signals_by_variant_from_notes)
from harness.dashboard.snapshots import base_payload, register_builder, section
from harness.execution.book import side_p
from harness.telemetry import sanitize_reason

log = logging.getLogger(__name__)

#: 15 s inside a game window, 60 s outside; the scheduler decides which and passes it in through
#: `base_payload`, so the UI's staleness flags are measured against the cadence actually in use.
CADENCE_IN_WINDOW_S = 15
CADENCE_OUT_S = 60

FUNNEL_WINDOW = WINDOW_6H
BOARD_WINDOW = timedelta(hours=24)
BOARD_LOOKBACK = timedelta(hours=4)
WATCH_WINDOW = timedelta(hours=2)
#: How far back a fair value still counts as "current" for an open order's live edge.
FAIR_WINDOW = timedelta(hours=2)
VITALS_WINDOW = timedelta(hours=2)
VENUE_WINDOW = timedelta(hours=24)
FILLS_LIMIT = 50
ORDERS_LIMIT = 100
BOARD_LIMIT = 60
REASON_LIMIT = 20

FLOOR_KEYS = frozenset({"build_sha", "now", "cadence_s", "sentences", "readings",
                        "board", "funnel", "orders", "fills", "exposure", "vitals", "venue"})

_BOARD = text("""
    select g.id, g.sport, g.status, g.kickoff_utc,
           h.display_name as home_name, a.display_name as away_name,
           (select count(*) from venue_markets m
            where m.game_id = g.id and m.match_status in ('matched', 'fuzzy', 'manual'))
             as matched_markets,
           (select count(*) from orders o
            where o.game_id = g.id and o.replay = false
              and o.status in ('open', 'partially_filled')) as open_orders
    from games g
    left join teams h on h.sport = g.sport and h.id = g.home_team_id
    left join teams a on a.sport = g.sport and a.id = g.away_team_id
    where g.status = 'in_progress'
       or (g.kickoff_utc >= :from_ts and g.kickoff_utc <= :to_ts)
    order by case when g.status = 'in_progress' then 0 else 1 end, g.kickoff_utc
    limit :limit
""")

_SCORES = text("""
    select distinct on (game_id) game_id, ts, status, period, clock, home_score, away_score
    from game_score_events
    where game_id = any(:game_ids)
    order by game_id, ts desc
""")

_INTENTS = text("""
    select count(*) from intents
    where replay = false and created_at >= :since
""")
_SKIPS = text("""
    select reason, count(*) as n from order_events
    where kind = 'skipped' and replay = false and ts >= :since and reason is not null
    group by reason order by n desc limit :limit
""")
_CANCELS = text("""
    select reason, count(*) as n from order_events
    where kind in ('cancel', 'expire') and replay = false and ts >= :since
      and reason is not null
    group by reason order by n desc limit :limit
""")
_ORDERS_COUNT = text("""
    select count(*) from orders where replay = false and placed_at >= :since
""")
_FILLS_COUNT = text("""
    select count(*) from fills where replay = false and filled_at >= :since
""")

_OPEN_ORDERS = text("""
    select o.id, o.variant_id, o.ticker, o.side, o.prob, o.contracts, o.filled_contracts,
           o.queue_ahead_at_place, o.queue_remaining, o.book_source, o.dirty_minutes,
           o.placed_at, o.venue_market_id, o.edge_at_place
    from orders o
    where o.replay = false and o.status in ('open', 'partially_filled')
    order by o.queue_remaining nulls last, o.placed_at desc
    limit :limit
""")
_WATCH = text("""
    select order_id, ts, queue_remaining, best_bid, best_ask, fair_p, book_dirty
    from order_watch_samples
    where order_id = any(:order_ids) and ts >= :since
    order by order_id, ts
""")
#: Spec §2.2: "current fair and edge per open order -- `fair_values` newest per market (bounded
#: by open orders)". The translation is the load-bearing part: `fair_values` is keyed
#: `(game_id, market_type)` while an order is keyed `venue_market_id`, so the join goes through
#: `venue_markets`. The lateral takes the newest row per market inside the fair window, which is
#: what lets `ix_fair_game_type_created (game_id, market_type, created_at)` serve it as an index
#: seek per market -- at most `ORDERS_LIMIT` markets, so a hundred seeks, never the scan fix 15
#: removed.
_FAIR_FOR_ORDERS = text("""
    select m.id as venue_market_id, f.fair_p, f.staleness_s, f.created_at
    from venue_markets m
    join lateral (
        select fair_p, staleness_s, created_at from fair_values f
        where f.game_id = m.game_id and f.market_type = m.market_type
          and f.created_at >= :since
        order by f.created_at desc limit 1
    ) f on true
    where m.id = any(:market_ids)
""")

_FILLS = text("""
    select f.id, f.order_id, f.prob, f.contracts, f.fee, f.fill_method, f.filled_at,
           f.has_print, o.variant_id, o.ticker, o.side
    from fills f
    join orders o on o.id = f.order_id
    where f.replay = false and f.filled_at >= :start and f.filled_at < :end
    order by f.filled_at desc
    limit :limit
""")

_EXPOSURE = text("""
    select variant_id, sum(open_contracts) as contracts, count(*) as positions
    from positions group by variant_id
""")
_EQUITY = text("""
    select distinct on (variant_id) variant_id, ts, cash, open_stake, mtm_open, mtm_coverage,
           n_open_positions, n_open_orders
    from equity_snapshots order by variant_id, ts desc
""")

_VITALS = text("""
    select name, labels, value, ts from metric_samples
    where name in ('exec.loop_ms', 'exec.dirty_markets', 'exec.skipped', 'ws.events_per_min')
      and ts >= :since
    order by ts
""")

_VENUE_COUNTS = text("""
    select env, method, count(*) as n from venue_requests
    where ts >= :since group by env, method order by env, method
""")
_VENUE_PROD_NON_GET = text("""
    select count(*) from venue_requests
    where ts >= :since and env = 'prod' and method <> 'GET'
""")
_VENUE_STATUS = text("""
    select distinct on (env) env, status, reason, since, updated_at
    from venue_status order by env, updated_at desc
""")
_LAST_SMOKE = text("""
    select summary, ts from operator_events
    where summary like 'demo smoke%' order by ts desc limit 1
""")


def _dec(value):
    """A `Decimal` column as a JSON float, `None` kept as `None`. Payload JSON never leans on
    `json`'s own coercion of a `Decimal` (global constraint: units and types)."""
    return float(value) if value is not None else None


def _board(session: Session, now: datetime) -> dict:
    rows = list(session.execute(_BOARD, {"from_ts": now - BOARD_LOOKBACK,
                                         "to_ts": now + BOARD_WINDOW,
                                         "limit": BOARD_LIMIT}))
    ids = [row.id for row in rows]
    scores = {row.game_id: row for row in
              session.execute(_SCORES, {"game_ids": ids})} if ids else {}
    games = []
    for row in rows:
        score = scores.get(row.id)
        games.append({
            "game_id": row.id, "sport": row.sport, "status": row.status,
            "kickoff": row.kickoff_utc.isoformat(),
            "kickoff_in_s": (row.kickoff_utc - now).total_seconds(),
            # ESPN's own strings, sanitized: teams.display_name and the scoreboard's clock are
            # copied verbatim from the feed (ruling A-I6 / B-I2).
            "home": sanitize_reason(row.home_name or ""),
            "away": sanitize_reason(row.away_name or ""),
            "home_score": score.home_score if score else None,
            "away_score": score.away_score if score else None,
            "period": score.period if score else None,
            "clock": sanitize_reason(score.clock or "") if score else None,
            "score_age_s": (now - score.ts).total_seconds() if score else None,
            "matched_markets": int(row.matched_markets or 0),
            "open_orders": int(row.open_orders or 0),
        })
    return {"games": games}


def _funnel(session: Session, now: datetime) -> dict:
    since = now - FUNNEL_WINDOW
    notes = recent_run_notes(session, since)
    by_variant = signals_by_variant_from_notes(session, notes)
    ticks = gaps = 0
    for note in notes:
        pricing = (note or {}).get("pricing") or {}
        # Named integer counts only, never the object: `runs.notes` is written from feed data
        # and a passthrough of it would carry feed-derived keys into the payload (ruling B-(e)).
        ticks += int(pricing.get("ticks", 0) or 0)
        gaps += int(pricing.get("gaps", 0) or 0)
    candidates = sum(v["candidate"] for v in by_variant.values())
    rejected = sum(v["rejected"] for v in by_variant.values())
    window = {"since": since}
    reasons = {"since": since, "limit": REASON_LIMIT}
    skips = [{"reason": row.reason, "count": int(row.n),
              "plain": sentences.reason_phrase(row.reason)}
             for row in session.execute(_SKIPS, reasons)]
    cancels = [{"reason": row.reason, "count": int(row.n),
                "plain": sentences.reason_phrase(row.reason)}
               for row in session.execute(_CANCELS, reasons)]
    return {
        "window_h": int(FUNNEL_WINDOW.total_seconds() // 3600),
        "ticks": ticks, "gaps": gaps, "candidates": candidates,
        "rejected_total": rejected,
        "by_variant": by_variant,
        "intents": int(session.execute(_INTENTS, window).scalar() or 0),
        "orders": int(session.execute(_ORDERS_COUNT, window).scalar() or 0),
        "fills": int(session.execute(_FILLS_COUNT, window).scalar() or 0),
        "skipped": skips, "cancelled": cancels,
    }


def _orders(session: Session, now: datetime) -> dict:
    """The resting simulated orders, each with its queue bar, its queue history, and the
    **current** fair and edge beside the ones frozen at placement.

    The live pair matters because the frozen one under a live label is exactly the failure this
    spec is written against: an order placed at a 3-point edge whose market has since moved is
    not a 3-point edge any more. Both travel, under their own keys, so the front end can label
    whichever it shows.
    """
    rows = list(session.execute(_OPEN_ORDERS, {"limit": ORDERS_LIMIT}))
    ids = [row.id for row in rows]
    market_ids = sorted({row.venue_market_id for row in rows})
    history: dict[int, list] = {}
    if ids:
        for sample in session.execute(_WATCH, {"order_ids": ids,
                                               "since": now - WATCH_WINDOW}):
            history.setdefault(sample.order_id, []).append(
                [sample.ts.isoformat(), _dec(sample.queue_remaining)])
    fair = {}
    if market_ids:
        fair = {row.venue_market_id: row for row in session.execute(
            _FAIR_FOR_ORDERS, {"market_ids": market_ids, "since": now - FAIR_WINDOW})}
    orders = []
    for row in rows:
        current = fair.get(row.venue_market_id)
        fair_p = _dec(current.fair_p) if current is not None else None
        # `side_p` puts the fair price into this order's own side space, exactly as the pricing
        # path does, so a NO order's edge is not the YES leg's edge with a sign error.
        #
        # The two edges are on different bases, which is why they travel under separate keys and
        # separate labels rather than as one number. `edge_at_place` is the strategy's own
        # figure, net of the maker fee at the target price (`harness/strategy/run.py`'s
        # `fair - price_target - fee_at_target`); `edge_live` is the gross distance from the
        # current fair to the resting price, because the fee this order would actually pay
        # depends on the market's fee shape and is not what the Floor is asking. T18 labels each
        # one for what it is and never subtracts them from each other.
        edge_live = (float(side_p(current.fair_p, row.side)) - float(row.prob)
                     if current is not None and current.fair_p is not None else None)
        orders.append({
            "id": row.id, "variant": row.variant_id,
            "ticker": sanitize_reason(row.ticker or ""),
            "side": row.side, "prob": _dec(row.prob), "contracts": _dec(row.contracts),
            "filled_contracts": _dec(row.filled_contracts),
            "queue_ahead_at_place": _dec(row.queue_ahead_at_place),
            "queue_remaining": _dec(row.queue_remaining),
            "book_source": row.book_source, "dirty_minutes": row.dirty_minutes,
            "age_s": (now - row.placed_at).total_seconds(),
            "edge_at_place": _dec(row.edge_at_place),
            "fair_p": fair_p,
            "fair_staleness_s": current.staleness_s if current is not None else None,
            "fair_age_s": ((now - current.created_at).total_seconds()
                           if current is not None else None),
            "edge_live": edge_live,
            "queue_history": history.get(row.id, []),
        })
    return {"orders": orders}


def _fills(session: Session, now: datetime, settings: Settings) -> dict:
    start, end = local_day_bounds_utc(now, settings.tz_local)
    rows = session.execute(_FILLS, {"start": start, "end": end, "limit": FILLS_LIMIT})
    return {"fills": [{"id": row.id, "order_id": row.order_id, "variant": row.variant_id,
                       "ticker": sanitize_reason(row.ticker or ""), "side": row.side,
                       "prob": _dec(row.prob), "contracts": _dec(row.contracts),
                       "fee": _dec(row.fee), "fill_method": row.fill_method,
                       "has_print": bool(row.has_print),
                       "filled_at": row.filled_at.isoformat()} for row in rows]}


def _exposure(session: Session) -> dict:
    positions = {row.variant_id: row for row in session.execute(_EXPOSURE)}
    lanes = []
    for row in session.execute(_EQUITY):
        held = positions.get(row.variant_id)
        lanes.append({"variant": row.variant_id, "ts": row.ts.isoformat(),
                      "cash": _dec(row.cash), "open_stake": _dec(row.open_stake),
                      "mtm_open": _dec(row.mtm_open), "mtm_coverage": _dec(row.mtm_coverage),
                      "n_open_positions": row.n_open_positions,
                      "n_open_orders": row.n_open_orders,
                      "open_contracts": _dec(held.contracts) if held else 0.0})
    return {"lanes": lanes}


def _vitals(session: Session, now: datetime) -> dict:
    lines: dict[str, list] = {}
    for row in session.execute(_VITALS, {"since": now - VITALS_WINDOW}):
        key = row.name if not (row.labels or {}).get("reason") else \
            f"{row.name}:{sanitize_reason(str(row.labels['reason']))}"
        lines.setdefault(key, []).append([row.ts.isoformat(), _dec(row.value)])
    return {"window_h": int(VITALS_WINDOW.total_seconds() // 3600), "sparklines": lines}


def _venue(session: Session, now: datetime) -> dict:
    """Roadmap phase 4.5 item 5: phase 4's `venue_requests` table as a Floor tile. The
    production non-GET count is the paper-posture tripwire, and it is 0 or the phase has a
    control breach; a demo smoke's own writes never touch it."""
    window = {"since": now - VENUE_WINDOW}
    counts = [{"env": row.env, "method": row.method, "count": int(row.n)}
              for row in session.execute(_VENUE_COUNTS, window)]
    non_get = int(session.execute(_VENUE_PROD_NON_GET, window).scalar() or 0)
    status = [{"env": row.env, "status": row.status,
               "reason": sanitize_reason(row.reason) if row.reason else None,
               "since": row.since.isoformat(), "updated_at": row.updated_at.isoformat()}
              for row in session.execute(_VENUE_STATUS)]
    smoke = session.execute(_LAST_SMOKE).first()
    return {"window_h": int(VENUE_WINDOW.total_seconds() // 3600),
            "by_env_method": counts,
            "prod_non_get_24h": non_get,
            "tripwire_ok": non_get == 0,
            "status": status,
            "last_smoke": sanitize_reason(smoke.summary) if smoke else "no smoke recorded",
            "last_smoke_at": smoke.ts.isoformat() if smoke else None}


def build_floor(session: Session, now: datetime, settings: Settings) -> dict:
    payload = base_payload("floor", now, settings, CADENCE_IN_WINDOW_S)
    section(payload, "board", lambda: _board(session, now))
    section(payload, "funnel", lambda: _funnel(session, now))
    section(payload, "orders", lambda: _orders(session, now))
    section(payload, "fills", lambda: _fills(session, now, settings))
    section(payload, "exposure", lambda: _exposure(session))
    section(payload, "vitals", lambda: _vitals(session, now))
    section(payload, "venue", lambda: _venue(session, now))

    def _dict(key):
        value = payload.get(key)
        return value if isinstance(value, dict) and "error" not in value else {}

    payload["sentences"] = {"board": sentences.floor_board(_dict("board")),
                            "funnel": sentences.floor_funnel(_dict("funnel")),
                            "orders": sentences.floor_orders(_dict("orders"))}
    payload["readings"] = {}
    return payload


register_builder("floor", build_floor)
