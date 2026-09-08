"""Every statement the executor loop runs. The loop itself holds no SQL.

Two rules shape this module and both come from the addendum:

* **Idempotency.** Every insert has a unique key and goes in as
  `on conflict do nothing ... returning id`. psycopg3 reports `rowcount = -1` for a conflict
  insert, so the returned row -- not the row count -- is what says whether anything was
  written, and only a returned row moves a counter or a running total. A step that dies
  half-way and is retried therefore writes each row exactly once.
* **Tape access.** The live path reads deltas by `id` (`id > cursor and ts >= lower`) with a
  lower `ts` bound only; `id` is the recorder's insertion order and the only monotone quantity
  on the tape. Prints have no cursor at all -- the executor rescans them from
  `placed_at - 60 s` every loop and the simulator's print watermark absorbs the re-feed.

Reads return frozen views from `harness.execution.plan` wherever the decision chain consumes
them, so the loop never passes a raw `Row` into a pure function.
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Sequence

from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import EquitySnapshot, Fill, Intent, Ledger, Order, OrderEvent, OrderWatchSample
from harness.execution.fills import TapeDelta, TapePrint
from harness.execution.plan import FillView, IntentView, PositionView
from harness.strategy.variants import with_defaults

log = logging.getLogger(__name__)

#: The advisory lock the single live executor holds for the length of one step. `hashtext` is
#: stable for a given string across a cluster's lifetime, which is all this needs.
LOCK_KEY = "harness.exec"

#: The prints a fill simulation rescans on every loop, measured back from `placed_at` (§1).
PRINT_LOOKBACK = timedelta(seconds=60)

OPEN_STATUSES = ("open", "partially_filled")


# --- the advisory lock ----------------------------------------------------------------


def try_lock(conn) -> bool:
    """Take the executor's session-level advisory lock, or report that someone else has it."""
    return bool(conn.execute(text("select pg_try_advisory_lock(hashtext(:k))"),
                             {"k": LOCK_KEY}).scalar())


def unlock(conn) -> None:
    conn.execute(text("select pg_advisory_unlock(hashtext(:k))"), {"k": LOCK_KEY})


# --- variants -------------------------------------------------------------------------


def resolve_variants(session: Session, names: Sequence[str]) -> list[str]:
    """`Settings.exec_variants` holds variant *names*; signals carry the 12-hex `variant_id`.

    An id is accepted too, so an operator can pin one registered configuration by hash.
    """
    if not names:
        return []
    rows = session.execute(text(
        "select variant_id from strategy_variants where name = any(:n) or variant_id = any(:n)"),
        {"n": list(names)}).scalars().all()
    return sorted(rows)


def variant_configs(session: Session, variant_ids: Iterable[str]) -> dict[str, dict]:
    """Every named variant's config, defaults filled in (Task 5 ruling 3).

    A DB-loaded config carries only the keys the YAML spelled out, and `plan_actions` raises on
    a missing key by design; `with_defaults` is what keeps the loop away from that raise.
    """
    ids = sorted(set(variant_ids))
    if not ids:
        return {}
    rows = session.execute(text(
        "select variant_id, config_json from strategy_variants where variant_id = any(:v)"),
        {"v": ids}).all()
    return {row.variant_id: with_defaults(row.config_json) for row in rows}


def kill_active(session: Session) -> bool:
    return bool(session.execute(text(
        "select active from kill_switch order by id limit 1")).scalar())


# --- intake ---------------------------------------------------------------------------

_CANDIDATES = text("""
select s.id as signal_id, s.variant_id, s.venue_market_id, s.side, s.price_target, s.contracts,
       s.edge, s.edge_min, s.fair_p, s.stake, s.created_at, s.as_estimate, s.gap_snapshot_id,
       m.ticker, m.venue, m.game_id, g.kickoff_utc, gs.fair_value_id
from signals s
join venue_markets m on m.id = s.venue_market_id
left join games g on g.id = m.game_id
left join market_gap_snapshots gs on gs.id = s.gap_snapshot_id
left join intents i on i.signal_id = s.id
where s.decision = 'candidate' and s.replay = :replay and s.variant_id = any(:variants)
  and s.created_at >= :lower and i.signal_id is null
order by s.id
""")


def candidate_signals(session: Session, variant_ids: Sequence[str], lower: datetime,
                      replay: bool) -> list:
    """Candidate signals of the executed variants inside the intent TTL that have no intent yet.

    Nothing is filtered out beyond that (§1): whether the loop can act on the signal is the
    decision chain's business, and the intent row is the record that it was offered one.
    """
    if not variant_ids:
        return []
    return session.execute(_CANDIDATES, {"replay": replay, "variants": list(variant_ids),
                                         "lower": lower}).all()


def insert_intents(session: Session, rows: Sequence, now: datetime, replay: bool) -> int:
    """One intent per candidate signal; returns how many rows this call actually wrote."""
    written = 0
    for row in rows:
        stmt = insert(Intent).values(
            signal_id=row.signal_id, variant_id=row.variant_id, venue=row.venue,
            venue_market_id=row.venue_market_id, ticker=row.ticker, side=row.side,
            target_prob=row.price_target,
            target_contracts=None if row.contracts is None else Decimal(row.contracts),
            edge=row.edge, edge_min=row.edge_min, fair_p=row.fair_p,
            fair_row_id=row.fair_value_id, game_id=row.game_id, kickoff_utc=row.kickoff_utc,
            stake=row.stake, signal_created_at=row.created_at, created_at=now, replay=replay,
        ).on_conflict_do_nothing(index_elements=["signal_id"]).returning(Intent.id)
        if session.execute(stmt).first() is not None:
            written += 1
    return written


_NEWEST_INTENTS = text("""
select distinct on (i.variant_id, i.venue_market_id, i.side)
       i.id, i.signal_id, i.variant_id, i.venue_market_id, i.ticker, i.side, i.target_prob,
       i.target_contracts, i.edge, i.edge_min, i.fair_p, i.fair_row_id, i.game_id,
       i.kickoff_utc, i.stake, i.signal_created_at, i.venue,
       s.as_estimate, s.gap_snapshot_id
from intents i
left join signals s on s.id = i.signal_id
where i.replay = :replay and i.variant_id = any(:variants)
  and i.signal_created_at >= :lower
order by i.variant_id, i.venue_market_id, i.side, i.signal_created_at desc, i.created_at desc,
         i.signal_id desc, i.id desc
""")

_NEWEST_DECISIONS = text("""
select distinct on (s.variant_id, s.venue_market_id, s.side)
       s.variant_id, s.venue_market_id, s.side, s.decision
from signals s
where s.replay = :replay and s.variant_id = any(:variants) and s.created_at >= :lower
order by s.variant_id, s.venue_market_id, s.side, s.created_at desc, s.id desc
""")


def load_intents(session: Session, variant_ids: Sequence[str], lower: datetime,
                 replay: bool) -> tuple[list[IntentView], dict]:
    """The intents the decision chain sees, plus the placement context they carry.

    The newest intent per `(variant_id, venue_market_id, side)` inside the TTL. The kickoff
    cutoff is deliberately *not* applied here: `plan_actions`' own `kickoff` rule is what
    declines an intent too close to kickoff, and filtering it out first would leave that
    decision with no record (F34, R8). `signal_id desc` breaks a timestamp tie, since
    `intents.id` is a uuid and orders nothing meaningful.

    `latest_decision` is read separately, from the newest *signal* on the same key whatever its
    decision, which is how an order placed by an intent whose signal has since been rejected
    gets cancelled.
    """
    if not variant_ids:
        return [], {}
    params = {"replay": replay, "variants": list(variant_ids), "lower": lower}
    decisions = {(r.variant_id, r.venue_market_id, r.side): r.decision
                 for r in session.execute(_NEWEST_DECISIONS, params).all()}
    views: list[IntentView] = []
    extras: dict = {}
    for row in session.execute(_NEWEST_INTENTS, params).all():
        key = (row.variant_id, row.venue_market_id, row.side)
        views.append(IntentView(
            intent_id=row.id, signal_id=row.signal_id, variant_id=row.variant_id,
            venue_market_id=row.venue_market_id, ticker=row.ticker, side=row.side,
            target_prob=row.target_prob, target_contracts=row.target_contracts, edge=row.edge,
            edge_min=row.edge_min, fair_p=row.fair_p, game_id=row.game_id,
            kickoff_utc=row.kickoff_utc, stake=row.stake,
            signal_created_at=row.signal_created_at,
            latest_decision=decisions.get(key, "candidate")))
        extras[row.id] = {"venue": row.venue, "as_estimate": row.as_estimate,
                          "gap_snapshot_id": row.gap_snapshot_id}
    return views, extras


# --- orders, markets and the tape -----------------------------------------------------

_WORKING_ORDERS = text("""
select o.id, o.intent_id, o.variant_id, o.ticker, o.venue_market_id, o.side, o.prob,
       o.contracts, o.filled_contracts, o.status, o.placed_at, o.expiry, o.fair_p_at_place,
       o.venue_mid_at_place, o.edge_min_at_place, o.as_at_place, o.kickoff_utc, o.game_id,
       coalesce(i.stake, o.prob * o.contracts) as stake,
       o.match_key, o.queue_ahead_at_place, o.queue_remaining, o.traded_at_price,
       o.tape_cursor_event_id, o.crossed, o.last_print_ts, o.last_print_ids,
       o.nw_filled_contracts, o.nw_queue_remaining, o.nw_traded_at_price,
       o.nw_tape_cursor_event_id, o.nw_crossed, o.nw_last_print_ts, o.nw_last_print_ids,
       o.nw_done, o.book_source, o.dirty_seconds, o.worst_case_fill
from orders o
left join intents i on i.id = o.intent_id
where o.replay = :replay and (o.status in ('open', 'partially_filled') or o.nw_done = false)
order by o.id
""")


def working_orders(session: Session, replay: bool) -> list:
    """Orders the fill step still has something to say about: open ones, plus every order whose
    no-watcher counterfactual has not reached its own expiry.

    `orders` has no stake column of its own, so the exposure the caps are measured against comes
    off the intent that placed the order -- the same number the strategy sized with. An order
    whose intent has been pruned falls back to its own cost basis.
    """
    return session.execute(_WORKING_ORDERS, {"replay": replay}).all()


_MARKETS = text("""
select m.id as venue_market_id, m.ticker, m.venue, m.match_status, m.match_key, m.game_id,
       m.side_team_id, g.sport, g.kickoff_utc,
       gap.id as gap_snapshot_id, gap.fair_p, gap.fair_value_id, gap.staleness_s,
       gap.stale_allowance_s, gap.feed_kind, gap.best_bid, gap.best_ask, gap.venue_mid,
       fv.created_at as fair_ts
from venue_markets m
left join games g on g.id = m.game_id
left join lateral (
    select s.* from market_gap_snapshots s
    where s.venue_market_id = m.id order by s.created_at desc, s.id desc limit 1
) gap on true
left join fair_values fv on fv.id = gap.fair_value_id
where m.id = any(:ids)
""")


def market_rows(session: Session, venue_market_ids: Iterable[int]) -> dict[int, object]:
    """One row per market: its identity plus its newest gap snapshot and that snapshot's fair
    value. `fair_ts` is the fair value's own `created_at`, never the snapshot's."""
    ids = sorted(set(venue_market_ids))
    if not ids:
        return {}
    return {row.venue_market_id: row for row in session.execute(_MARKETS, {"ids": ids}).all()}


def newest_event_ts(session: Session) -> datetime | None:
    """The `ts` of the tape's newest row by `id` -- the recorder's own liveness signal."""
    return session.execute(text(
        "select ts from orderbook_events order by id desc limit 1")).scalar()


def event_ts(session: Session, event_id: int) -> datetime | None:
    return session.execute(text("select ts from orderbook_events where id = :i"),
                           {"i": event_id}).scalar()


_PRINTS = text("""
select trade_id, ts, yes_price, count, coalesce(taker_outcome_side, taker_side) as taker_side,
       source
from venue_trades where ticker = :t and ts >= :lower order by ts, trade_id
""")


def load_prints(session: Session, ticker: str, lower: datetime) -> list[TapePrint]:
    """Prints for one ticker since `lower`, deduplicated by `trade_id`.

    The same trade reaches us from the WebSocket and from REST, and `venue_trades` is
    partitioned on `ts` -- so `ts` is part of its primary key and cannot deduplicate a pair of
    rows a millisecond apart. The first row per `trade_id` in `ts` order wins.
    """
    seen: set[str] = set()
    out: list[TapePrint] = []
    for row in session.execute(_PRINTS, {"t": ticker, "lower": lower}).all():
        if row.trade_id in seen:
            continue
        seen.add(row.trade_id)
        out.append(TapePrint(trade_id=row.trade_id, ts=row.ts, yes_price=row.yes_price,
                             count=row.count, taker_side=row.taker_side, source=row.source))
    return out


_DELTAS = text("""
select id, ts, side, price, delta, sid, seq from orderbook_events
where ticker = :t and kind = 'delta' and id > :cursor and ts >= :lower order by id
""")


def load_deltas(session: Session, ticker: str, cursor: int, lower: datetime) -> list[TapeDelta]:
    """Deltas after `cursor`, in `id` order, with a lower `ts` bound and no upper one."""
    rows = session.execute(_DELTAS, {"t": ticker, "cursor": cursor, "lower": lower}).all()
    return [TapeDelta(event_id=r.id, ts=r.ts, side=r.side, price=r.price, delta=r.delta,
                      sid=r.sid, seq=r.seq)
            for r in rows if r.side is not None and r.price is not None and r.delta is not None]


# --- exposure -------------------------------------------------------------------------

_POSITIONS = text("""
select o.variant_id, o.game_id, m.side_team_id, o.side,
       sum(f.contracts * f.prob) as stake, max(o.edge_at_place) as edge
from fills f
join orders o on o.id = f.order_id
join venue_markets m on m.id = o.venue_market_id
where f.fill_method = 'queue_model' and o.replay = :replay and o.status <> 'settled'
group by o.variant_id, o.game_id, m.side_team_id, o.side
""")

_FILLS_TODAY = text("""
select o.variant_id, sum(f.contracts * f.prob) as stake
from fills f join orders o on o.id = f.order_id
where f.fill_method = 'queue_model' and o.replay = :replay and f.filled_at >= :since
group by o.variant_id
""")


def load_positions(session: Session, replay: bool) -> list[PositionView]:
    """Unsettled positions, keyed `(game_id, side_team_id, side)` (Task 4b ruling): NO on A and
    YES on B are two positions, never one."""
    return [PositionView(variant_id=r.variant_id, game_id=r.game_id,
                         side_team_id=r.side_team_id, side=r.side,
                         stake=r.stake or Decimal("0"), edge=r.edge)
            for r in session.execute(_POSITIONS, {"replay": replay}).all()]


def load_fills_today(session: Session, replay: bool, since: datetime) -> list[FillView]:
    """Every watched `queue_model` fill since local midnight, settled or not (Task 5 ruling)."""
    return [FillView(variant_id=r.variant_id, stake=r.stake or Decimal("0"))
            for r in session.execute(_FILLS_TODAY, {"replay": replay, "since": since}).all()]


def local_midnight(now: datetime, tz) -> datetime:
    """00:00 of `now`'s local day, back in UTC. The daily cap's day boundary (amendment 2)."""
    local = now.astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


# --- writes ---------------------------------------------------------------------------


def orders_for_intent(session: Session, intent_id) -> int:
    """How many orders this intent has already placed -- the `n` in its client order id."""
    return int(session.execute(text("select count(*) from orders where intent_id = :i"),
                               {"i": intent_id}).scalar() or 0)


def insert_order(session: Session, values: dict) -> int | None:
    """Insert one paper order, keyed on its client order id. None means it was already there."""
    stmt = (insert(Order).values(**values)
            .on_conflict_do_nothing(index_elements=["client_order_id"])
            .returning(Order.id))
    row = session.execute(stmt).first()
    return None if row is None else row[0]


def insert_event(session: Session, **values) -> int | None:
    """One `order_events` row. `uq_order_event` keys a placement/cancel on `(order, kind, ts)`
    and `uq_skip_once` keys a skip or cap gate on `(intent, kind, reason)` (F35)."""
    stmt = insert(OrderEvent).values(**values).on_conflict_do_nothing().returning(OrderEvent.id)
    row = session.execute(stmt).first()
    return None if row is None else row[0]


def insert_fill(session: Session, **values) -> int | None:
    """One `fills` row, keyed by `uq_fill_source` on its own tape row, so the same print or
    crossing delta cannot be counted twice -- including by the other track."""
    stmt = insert(Fill).values(**values).on_conflict_do_nothing().returning(Fill.id)
    row = session.execute(stmt).first()
    return None if row is None else row[0]


def insert_ledger_fill(session: Session, **values) -> int | None:
    """The cash movement of one watched fill; `uq_ledger_fill` keys it on `(fill_id, kind)`."""
    stmt = insert(Ledger).values(**values).on_conflict_do_nothing().returning(Ledger.id)
    row = session.execute(stmt).first()
    return None if row is None else row[0]


def update_order(session: Session, order_id: int, values: dict) -> None:
    """Write back one order's columns through the model, so the JSONB and Numeric ones are
    bound with their real types rather than whatever psycopg would infer from a Python value."""
    if not values:
        return
    session.execute(update(Order).where(Order.id == order_id).values(**values))


def add_dirty_seconds(session: Session, order_id: int, seconds: int) -> None:
    """A dirty book buys the order nothing this loop but the record that it happened (D6).

    `dirty_minutes` is an integer column and one 15 s loop is a quarter of a minute, so the
    seconds are what accumulate and the minutes are derived from them here.
    """
    session.execute(text(
        "update orders set dirty_seconds = dirty_seconds + :s, "
        "dirty_minutes = (dirty_seconds + :s) / 60 where id = :i"),
        {"s": int(seconds), "i": order_id})


def cancel_order(session: Session, order_id: int, reason: str, now: datetime) -> bool:
    """Cancel a resting order; False when it was not resting, so a re-applied cancel is a no-op.

    Bounded on the open statuses for the same reason every insert is bounded on a unique key: a
    retried step must not move a counter twice or overwrite a cancel reason already recorded.
    """
    stmt = (update(Order)
            .where(Order.id == order_id, Order.status.in_(OPEN_STATUSES))
            .values(status="cancelled", cancel_reason=reason, cancelled_at=now)
            .returning(Order.id))
    return session.execute(stmt).first() is not None


def expire_order(session: Session, order_id: int) -> bool:
    """Expire a resting order; False when it was not resting (R8's guarantee, applied once)."""
    stmt = (update(Order)
            .where(Order.id == order_id, Order.status.in_(OPEN_STATUSES))
            .values(status="expired")
            .returning(Order.id))
    return session.execute(stmt).first() is not None


def count_open_orders(session: Session, replay: bool) -> int:
    return int(session.execute(text(
        "select count(*) from orders where status = any(:st) and replay = :r"),
        {"st": list(OPEN_STATUSES), "r": replay}).scalar() or 0)


_HEARTBEAT = text("""
insert into exec_heartbeat (id, last_loop_at, loops, open_orders, last_error, last_loop_ms,
                            p95_loop_ms, loops_skipped, book_dirty_markets, ws_last_event_at,
                            executor_version)
values (1, :last_loop_at, 1, :open_orders, :last_error, :last_loop_ms, :p95_loop_ms,
        :loops_skipped, :book_dirty_markets, :ws_last_event_at, :executor_version)
on conflict (id) do update set
    last_loop_at = excluded.last_loop_at,
    loops = exec_heartbeat.loops + 1,
    open_orders = excluded.open_orders,
    last_error = excluded.last_error,
    last_loop_ms = excluded.last_loop_ms,
    p95_loop_ms = excluded.p95_loop_ms,
    loops_skipped = exec_heartbeat.loops_skipped + excluded.loops_skipped,
    book_dirty_markets = excluded.book_dirty_markets,
    ws_last_event_at = excluded.ws_last_event_at,
    executor_version = excluded.executor_version
""")


def write_heartbeat(session: Session, **values) -> None:
    """The single row the dashboard reads to tell a stalled executor from an idle one.

    `loops` and `loops_skipped` accumulate in the statement rather than in the process, so a
    restart continues the count instead of resetting it.
    """
    session.execute(_HEARTBEAT, values)


def read_heartbeat(session: Session):
    return session.execute(text(
        "select last_loop_at, extract(epoch from (now() - last_loop_at)) as age_s, loops, "
        "last_error from exec_heartbeat where id = 1")).first()


# --- Task 12b telemetry -----------------------------------------------------------------


def read_heartbeat_executor_version(session: Session) -> str | None:
    """The `EXECUTOR_VERSION` the last process to write the heartbeat ran, or None before the
    first heartbeat ever lands."""
    return session.execute(text(
        "select executor_version from exec_heartbeat where id = 1")).scalar()


def newest_open_order_config_hash(session: Session, variant_id: str) -> str | None:
    """The `config_hash` of `variant_id`'s newest currently-open non-replay order, or None
    when it has none (nothing to compare a startup config change against)."""
    return session.execute(text(
        "select config_hash from orders where variant_id = :v and replay = false "
        "and status = any(:st) order by placed_at desc limit 1"),
        {"v": variant_id, "st": list(OPEN_STATUSES)}).scalar()


_ORDER_STATUS_SNAPSHOT = text(
    "select id, status, queue_remaining, nw_queue_remaining from orders where id = any(:ids)")


def order_status_snapshot(session: Session, order_ids) -> dict[int, tuple]:
    """Each order's current `(status, queue_remaining, nw_queue_remaining)`, read fresh after
    this step's fills and actions have already been applied in the same transaction."""
    ids = list(order_ids)
    if not ids:
        return {}
    rows = session.execute(_ORDER_STATUS_SNAPSHOT, {"ids": ids}).all()
    return {r.id: (r.status, r.queue_remaining, r.nw_queue_remaining) for r in rows}


def insert_order_watch_samples(session: Session, rows: list[dict]) -> int:
    """One `order_watch_samples` row per dict, as one `INSERT ... VALUES` batch. `(order_id,
    ts)` is the primary key, so a retried step cannot double-sample the same order the same
    instant."""
    if not rows:
        return 0
    session.execute(insert(OrderWatchSample).values(rows).on_conflict_do_nothing())
    return len(rows)


def ledger_cash_delta(session: Session, variant_id: str) -> Decimal:
    """One variant's all-time non-replay cash movement -- the whole of `equity_snapshots.cash`
    beyond its starting bankroll."""
    return session.execute(text(
        "select coalesce(sum(cash_delta), 0) from ledger where variant_id = :v and replay = false"),
        {"v": variant_id}).scalar() or Decimal("0")


_POSITIONS_FOR_VARIANT = text(
    "select ticker, side, open_contracts, avg_price from positions where variant_id = :v")


def positions_for_variant(session: Session, variant_id: str):
    """One variant's open positions, from the `positions` view (`harness/db/schema.py`)."""
    return session.execute(_POSITIONS_FOR_VARIANT, {"v": variant_id}).all()


def count_variant_open_orders(session: Session, variant_id: str, replay: bool) -> int:
    return int(session.execute(text(
        "select count(*) from orders where variant_id = :v and status = any(:st) and replay = :r"),
        {"v": variant_id, "st": list(OPEN_STATUSES), "r": replay}).scalar() or 0)


def insert_equity_snapshot(session: Session, **values) -> None:
    """One `equity_snapshots` row; `(ts, variant_id)` is the primary key, so a retried step
    cannot double-sample the same variant the same instant."""
    session.execute(insert(EquitySnapshot).values(**values).on_conflict_do_nothing())
