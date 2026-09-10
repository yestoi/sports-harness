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

**The one read that is not bounded, and why.** `_EXPOSURE` reads the `positions` view, which
aggregates every money fill of every order that has not settled. Measured against twice a
season's volume (270,000 orders, 135,000 fills) it costs 26 ms with a hundred orders still
unsettled, 63 ms with 2,700, and 103 ms in the pathological case where settlement has stalled
and nothing has dropped out of the view at all. What it scales with is the *unsettled* set, not
the season, because `o.status <> 'settled'` is the view's own bound.

It is left unbounded deliberately, and every candidate bound was rejected for a reason:
restricting to games that are not final drops the window between the whistle and settlement,
when the position is still held and is exactly what an exposure display must not hide;
restricting by order status means restating a status vocabulary that has no canonical constant
to import; and a predicate on the view's own output columns cannot reach inside its aggregate.
Above all, the view is the *shared* definition of which fills are real, and
`harness/db/schema.py`'s `_POSITIONS_VIEW` and `harness.execution.store._POSITIONS` are required
not to diverge on that. So `serve.snapshot_ms` is the watch on this one read. `_EQUITY` **is**
bounded, because there the bound is exact and turns a 56 ms sort that spills 5.5 MB to disk into
a 4 ms one that does not: see `EQUITY_WINDOW`.

**Never shown here.** CLV, markouts or any figure that judges the strategy. Floor shows
activity, not quality, so that a good afternoon of fills is never mistaken for edge.
"""

import logging
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.dashboard import sentences
from harness.dashboard.queries import (WINDOW_6H, local_day_bounds_utc, recent_run_notes,
                                       signals_by_variant_from_notes)
from harness.dashboard.snapshots import base_payload, register_builder, section
from harness.db.models import StrategyVariant
from harness.execution.book import side_p
from harness.execution.store import MONEY_FILL_METHODS, OPEN_STATUSES
from harness.pricing.fair import MATCHED_STATUSES
from harness.pricing.fees import fee_model_for, fee_per_contract
from harness.telemetry import sanitize_reason

log = logging.getLogger(__name__)

#: 15 s inside a game window, 60 s outside; the scheduler decides which and passes it in through
#: `base_payload`, so the UI's staleness flags are measured against the cadence actually in use.
#: T16's scheduler imports these rather than restating 15 and 60 in a second home.
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
#: `_EQUITY` takes the newest snapshot per variant, and `order by variant_id, ts desc` cannot
#: ride `ix_equity_variant_ts` as a plain backward scan. Unbounded at a season's volume that is
#: a 181,440-row external merge sort spilling 5.5 MB to disk, 56 ms, four times a minute;
#: bounded it is a bitmap index scan and an in-memory quicksort, 4 ms. Seven days is generous
#: enough that a lane only disappears if the executor has written no equity row for a week,
#: which is a far louder failure than a stale lane.
EQUITY_WINDOW = timedelta(days=7)
#: The smoke note is written once per deploy, so "no smoke recorded" means none in a month. The
#: bound is what keeps that branch from walking the whole index on a database that has none.
SMOKE_WINDOW = timedelta(days=30)
FILLS_LIMIT = 50
ORDERS_LIMIT = 100
BOARD_LIMIT = 60
REASON_LIMIT = 20
#: Samples kept per order in `queue_history`. The 2 h `WATCH_WINDOW` at a 60 s sample is 120
#: pairs an order and 472 KB of payload at `ORDERS_LIMIT` orders, rewritten every 15 s and
#: served down a tunnel to a phone; the newest 30 is the shape of the queue bar's recent
#: history and measures out at a 168 KB ceiling. `WATCH_WINDOW` still bounds the read, so the
#: sparkline now covers roughly the last half hour rather than two hours -- worth a word in
#: T18's label copy.
QUEUE_HISTORY_LIMIT = 30

FLOOR_KEYS = frozenset({"build_sha", "now", "cadence_s", "sentences", "readings",
                        "board", "funnel", "orders", "fills", "exposure", "vitals", "venue",
                        "sentences_gaps"})

#: `matched`, `fuzzy` and `manual`, imported rather than restated: the pricing path owns this
#: vocabulary (`harness.pricing.fair`), and the board must count a market as matched on exactly
#: the statuses that make it priceable.
_BOARD = text("""
    select g.id, g.sport, g.status, g.kickoff_utc,
           h.display_name as home_name, a.display_name as away_name,
           (select count(*) from venue_markets m
            where m.game_id = g.id and m.match_status = any(:matched)) as matched_markets,
           (select count(*) from orders o
            where o.game_id = g.id and o.replay = false
              and o.status = any(:open_statuses)) as open_orders
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
    where o.replay = false and o.status = any(:open_statuses)
    order by o.queue_remaining nulls last, o.placed_at desc
    limit :limit
""")
#: Spec §2.2 layout (3) wants our price read against the book, so the newest sample's
#: `best_bid`/`best_ask` ride along with the queue history.
_WATCH = text("""
    select order_id, ts, queue_remaining, best_bid, best_ask
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
#: removed. `fee_type` and `fee_multiplier` come off `m`, which the lateral is already keyed on,
#: so the live edge can carry the same fee its placement edge did.
_FAIR_FOR_ORDERS = text("""
    select m.id as venue_market_id, m.fee_type, m.fee_multiplier,
           f.fair_p, f.staleness_s, f.created_at
    from venue_markets m
    join lateral (
        select fair_p, staleness_s, created_at from fair_values f
        where f.game_id = m.game_id and f.market_type = m.market_type
          and f.created_at >= :since
        order by f.created_at desc limit 1
    ) f on true
    where m.id = any(:market_ids)
""")

#: Spec §2.2 layout (4): the fill, whether the print traded *through* our price, the tape it
#: came off, and the game it was on -- a ticker is not a game name to a phone reader.
_FILLS = text("""
    select f.id, f.order_id, f.prob, f.contracts, f.fee, f.fill_method, f.filled_at,
           f.has_print, f.through, f.tape_source,
           o.variant_id, o.ticker, o.side, o.game_id,
           h.display_name as home_name, a.display_name as away_name
    from fills f
    join orders o on o.id = f.order_id
    left join games g on g.id = o.game_id
    left join teams h on h.sport = g.sport and h.id = g.home_team_id
    left join teams a on a.sport = g.sport and a.id = g.away_team_id
    where f.replay = false and f.filled_at >= :start and f.filled_at < :end
    order by f.filled_at desc
    limit :limit
""")

#: Deliberately unbounded; see the module docstring. `open_contracts` is the only column the
#: lane needs, so nothing else is selected.
_EXPOSURE = text("""
    select variant_id, sum(open_contracts) as contracts
    from positions group by variant_id
""")
_EQUITY = text("""
    select distinct on (variant_id) variant_id, ts, cash, open_stake, mtm_open, mtm_coverage,
           n_open_positions, n_open_orders
    from equity_snapshots where ts >= :since order by variant_id, ts desc
""")
#: The two halves of `daily_exposure`, which is what `cap_daily` is measured against
#: (`harness/execution/plan.py`: the open orders' stake plus today's fills, positions excluded
#: so a contract filled today is not counted twice). `coalesce(i.stake, o.prob * o.contracts)`
#: is `harness.execution.store`'s own expression: `orders` has no stake column, so the exposure
#: comes off the intent that sized the order. Both filter `o.replay`, not `f.replay`, because
#: that is the flag the enforcer measures the cap against; the fills *stream* above filters
#: `f.replay` because that is what the brief's own read does. The two agree in practice -- a
#: replay fill belongs to a replay order -- and each mirrors its own authority.
_OPEN_STAKE = text("""
    select o.variant_id, count(*) as n,
           sum(coalesce(i.stake, o.prob * o.contracts)) as stake
    from orders o
    left join intents i on i.id = o.intent_id
    where o.replay = false and o.status = any(:open_statuses)
    group by o.variant_id
""")
_FILLS_TODAY = text("""
    select o.variant_id, count(*) as n, sum(f.contracts * f.prob) as stake
    from fills f
    join orders o on o.id = f.order_id
    where o.replay = false and f.fill_method = any(:methods)
      and f.filled_at >= :start and f.filled_at < :end
    group by o.variant_id
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
    where ts >= :since and summary like 'demo smoke%' order by ts desc limit 1
""")

FOUR_PLACES = Decimal("0.0001")


def _dec(value):
    """A `Decimal` column as a JSON float, `None` kept as `None`. Payload JSON never leans on
    `json`'s own coercion of a `Decimal` (global constraint: units and types)."""
    return float(value) if value is not None else None


def _board(session: Session, now: datetime) -> dict:
    rows = list(session.execute(_BOARD, {"from_ts": now - BOARD_LOOKBACK,
                                         "to_ts": now + BOARD_WINDOW,
                                         "matched": list(MATCHED_STATUSES),
                                         "open_statuses": list(OPEN_STATUSES),
                                         "limit": BOARD_LIMIT}))
    ids = [row.id for row in rows]
    scores = {row.game_id: row for row in
              session.execute(_SCORES, {"game_ids": ids})} if ids else {}
    games = []
    for row in rows:
        score = scores.get(row.id)
        games.append({
            "game_id": row.id, "sport": row.sport,
            # ESPN's own strings, sanitized: `teams.display_name`, the scoreboard's clock, and
            # `games.status` -- which `link_espn_scoreboard` keeps raw and lowercased for an
            # unrecognised ESPN status, which is why the column is String(24) (ruling A-I6 /
            # B-I2; there is no exemption list).
            "status": sanitize_reason(row.status or ""),
            "kickoff": row.kickoff_utc.isoformat(),
            "kickoff_in_s": (row.kickoff_utc - now).total_seconds(),
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
    # Ruling A-M6: the raw code is our own vocabulary, but it is the only string in this payload
    # that travels both raw and sanitized -- one sanitize call for consistency with `plain`.
    skips = [{"reason": sanitize_reason(row.reason), "count": int(row.n),
              "plain": sentences.reason_phrase(row.reason)}
             for row in session.execute(_SKIPS, reasons)]
    cancels = [{"reason": sanitize_reason(row.reason), "count": int(row.n),
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


def _live_edge(fair_p, side: str, prob, fee_type, fee_multiplier):
    """The current edge on a resting order, on the same basis as `orders.edge_at_place`.

    `edge_at_place` is `harness/strategy/run.py`'s `fair - price_target - fee_at_target`, taken
    at the price the order came to rest at. This is the identical formula with the current fair
    substituted, so the two figures compare like with like and neither is a fee-gross number
    standing next to a fee-net one. `side_p` puts the fair price into this order's own side
    space, exactly as the pricing path does, so a NO order's edge is not the YES leg's edge with
    a sign error.

    Returns `None` when the fee shape is one `fee_model_for` cannot price. That raises
    `ValueError` by design (an unpriced shape must not be scored at football rates), and letting
    it out of here would mark the whole `orders` section `{"error": "ValueError"}` -- the
    surface's centre panel gone over one odd market. `None` falls back to *edge when we placed
    it*, which is what the front end already does for a market with no current fair.
    """
    if fair_p is None:
        return None
    try:
        model = fee_model_for(fee_type, fee_multiplier)
    except ValueError:
        log.warning("no fee model for fee_type %r; live edge omitted", fee_type)
        return None
    fee = fee_per_contract(model, "maker", prob, 100)
    return float((side_p(fair_p, side) - prob - fee).quantize(FOUR_PLACES,
                                                             rounding=ROUND_HALF_UP))


def _orders(session: Session, now: datetime) -> dict:
    """The resting simulated orders, each with its queue bar, its recent queue history, our
    price against the book, and the **current** fair and edge beside the ones frozen at
    placement.

    The live pair matters because the frozen one under a live label is exactly the failure this
    spec is written against: an order placed at a 3-point edge whose market has since moved is
    not a 3-point edge any more. Both travel, under their own keys, so the front end can label
    whichever it shows.
    """
    rows = list(session.execute(_OPEN_ORDERS, {"open_statuses": list(OPEN_STATUSES),
                                               "limit": ORDERS_LIMIT}))
    ids = [row.id for row in rows]
    market_ids = sorted({row.venue_market_id for row in rows})
    history: dict[int, list] = {}
    newest: dict[int, object] = {}
    if ids:
        # `_WATCH` is ordered `(order_id, ts)`, so the last sample seen for an order is its
        # newest and the history tail is its newest `QUEUE_HISTORY_LIMIT` in order.
        for sample in session.execute(_WATCH, {"order_ids": ids,
                                               "since": now - WATCH_WINDOW}):
            history.setdefault(sample.order_id, []).append(
                [sample.ts.isoformat(), _dec(sample.queue_remaining)])
            newest[sample.order_id] = sample
    fair = {}
    if market_ids:
        fair = {row.venue_market_id: row for row in session.execute(
            _FAIR_FOR_ORDERS, {"market_ids": market_ids, "since": now - FAIR_WINDOW})}
    orders = []
    for row in rows:
        current = fair.get(row.venue_market_id)
        sample = newest.get(row.id)
        orders.append({
            "id": row.id, "variant": row.variant_id,
            "ticker": sanitize_reason(row.ticker or ""),
            "side": row.side, "prob": _dec(row.prob), "contracts": _dec(row.contracts),
            "filled_contracts": _dec(row.filled_contracts),
            "queue_ahead_at_place": _dec(row.queue_ahead_at_place),
            "queue_remaining": _dec(row.queue_remaining),
            "book_source": row.book_source, "dirty_minutes": row.dirty_minutes,
            "age_s": (now - row.placed_at).total_seconds(),
            # Our price against the book, from the newest watch sample (spec §2.2 layout 3).
            "best_bid": _dec(sample.best_bid) if sample is not None else None,
            "best_ask": _dec(sample.best_ask) if sample is not None else None,
            "book_age_s": ((now - sample.ts).total_seconds() if sample is not None else None),
            "edge_at_place": _dec(row.edge_at_place),
            "fair_p": _dec(current.fair_p) if current is not None else None,
            "fair_staleness_s": current.staleness_s if current is not None else None,
            "fair_age_s": ((now - current.created_at).total_seconds()
                           if current is not None else None),
            "edge_live": (_live_edge(current.fair_p, row.side, row.prob, current.fee_type,
                                    current.fee_multiplier)
                          if current is not None else None),
            "queue_history": history.get(row.id, [])[-QUEUE_HISTORY_LIMIT:],
        })
    return {"orders": orders}


def _fills(session: Session, now: datetime, settings: Settings) -> dict:
    start, end = local_day_bounds_utc(now, settings.tz_local)
    rows = session.execute(_FILLS, {"start": start, "end": end, "limit": FILLS_LIMIT})
    return {"fills": [{"id": row.id, "order_id": row.order_id, "variant": row.variant_id,
                       "ticker": sanitize_reason(row.ticker or ""), "side": row.side,
                       "game_id": row.game_id,
                       "home": sanitize_reason(row.home_name or "") if row.home_name else None,
                       "away": sanitize_reason(row.away_name or "") if row.away_name else None,
                       "prob": _dec(row.prob), "contracts": _dec(row.contracts),
                       "fee": _dec(row.fee), "fill_method": row.fill_method,
                       "has_print": bool(row.has_print),
                       # Whether the print traded *through* our price rather than at it, and
                       # which tape it came off (spec §2.2 layout 4).
                       "through": bool(row.through), "tape_source": row.tape_source,
                       "filled_at": row.filled_at.isoformat()} for row in rows]}


def _exposure(session: Session, now: datetime, settings: Settings) -> dict:
    """One lane per variant: its newest equity row, the contracts it is holding, the fills it has
    taken today, and how much of its daily cap that spends.

    Cap use is reported only for variants that actually enforce the caps (`apply_caps`); for the
    others the caps are labelled but not blocking (`harness/strategy/run.py`), so a "92 % of cap"
    reading on one of them would describe a limit that does not exist. The cap itself is
    `daily_cap * bankroll` out of the variant's own registered config, which is the same pair
    `harness/execution/plan.py` measures `cap_daily` against -- never a number written here.
    """
    start, end = local_day_bounds_utc(now, settings.tz_local)
    positions = {row.variant_id: row for row in session.execute(_EXPOSURE)}
    open_stake = {row.variant_id: row for row in session.execute(
        _OPEN_STAKE, {"open_statuses": list(OPEN_STATUSES)})}
    today = {row.variant_id: row for row in session.execute(
        _FILLS_TODAY, {"methods": list(MONEY_FILL_METHODS), "start": start, "end": end})}
    configs = {row.variant_id: (row.config_json or {}) for row in session.execute(
        select(StrategyVariant.variant_id, StrategyVariant.config_json)).all()}

    lanes = []
    for row in session.execute(_EQUITY, {"since": now - EQUITY_WINDOW}):
        held = positions.get(row.variant_id)
        resting = open_stake.get(row.variant_id)
        fills = today.get(row.variant_id)
        cfg = configs.get(row.variant_id) or {}
        resting_stake = (resting.stake if resting is not None else None) or Decimal(0)
        fills_stake = (fills.stake if fills is not None else None) or Decimal(0)
        daily_exposure = Decimal(resting_stake) + Decimal(fills_stake)
        enforced = bool(cfg.get("apply_caps"))
        cap_money = None
        if cfg.get("daily_cap") is not None and cfg.get("bankroll") is not None:
            cap_money = Decimal(str(cfg["daily_cap"])) * Decimal(str(cfg["bankroll"]))
        cap_use = None
        if enforced and cap_money is not None and cap_money > 0:
            cap_use = float((daily_exposure / cap_money).quantize(FOUR_PLACES,
                                                                 rounding=ROUND_HALF_UP))
        lanes.append({"variant": row.variant_id, "ts": row.ts.isoformat(),
                      "cash": _dec(row.cash), "open_stake": _dec(row.open_stake),
                      "mtm_open": _dec(row.mtm_open), "mtm_coverage": _dec(row.mtm_coverage),
                      "n_open_positions": row.n_open_positions,
                      "n_open_orders": row.n_open_orders,
                      "open_contracts": _dec(held.contracts) if held else 0.0,
                      "fills_today": int(fills.n) if fills is not None else 0,
                      "fills_today_stake": _dec(fills_stake),
                      "daily_exposure": _dec(daily_exposure),
                      "daily_cap": _dec(cap_money),
                      "caps_enforced": enforced,
                      "cap_use": cap_use})
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
    smoke = session.execute(_LAST_SMOKE, {"since": now - SMOKE_WINDOW}).first()
    return {"window_h": int(VENUE_WINDOW.total_seconds() // 3600),
            "by_env_method": counts,
            "prod_non_get_24h": non_get,
            "tripwire_ok": non_get == 0,
            "status": status,
            "last_smoke": sanitize_reason(smoke.summary) if smoke else "no smoke recorded",
            "last_smoke_at": smoke.ts.isoformat() if smoke else None}


def build_floor(session: Session, now: datetime, settings: Settings) -> dict:
    payload = base_payload("floor", now, settings, CADENCE_IN_WINDOW_S)
    section(session, payload, "board", lambda: _board(session, now))
    section(session, payload, "funnel", lambda: _funnel(session, now))
    section(session, payload, "orders", lambda: _orders(session, now))
    section(session, payload, "fills", lambda: _fills(session, now, settings))
    section(session, payload, "exposure", lambda: _exposure(session, now, settings))
    section(session, payload, "vitals", lambda: _vitals(session, now))
    section(session, payload, "venue", lambda: _venue(session, now))

    def _dict(key):
        value = payload.get(key)
        return value if isinstance(value, dict) and "error" not in value else {}

    payload["sentences"] = {"board": sentences.floor_board(_dict("board")),
                            "funnel": sentences.floor_funnel(_dict("funnel")),
                            "orders": sentences.floor_orders(_dict("orders"))}
    payload["readings"] = {}
    # Ruling A-I2: the skip and cancel reason codes the funnel just rendered, so a code outside
    # `REASON_PHRASES` is not silently lost -- the next plan sees it.
    funnel = _dict("funnel")
    codes = [row["reason"] for row in funnel.get("skipped", [])]
    codes += [row["reason"] for row in funnel.get("cancelled", [])]
    payload["sentences_gaps"] = sentences.unknown_reason_codes(codes)
    return payload


register_builder("floor", build_floor)
