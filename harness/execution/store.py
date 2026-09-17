"""Every statement the executor loop runs. The loop itself holds no SQL.

Two rules shape this module and both come from the addendum:

* **Idempotency.** Every insert has a unique key and goes in as
  `on conflict do nothing ... returning id`. psycopg3 reports `rowcount = -1` for a conflict
  insert, so the returned row -- not the row count -- is what says whether anything was
  written, and only a returned row moves a counter or a running total. A step that dies
  half-way and is retried therefore writes each row exactly once.
* **Tape access.** The live path reads deltas by `id` alone (`id > cursor`, capped at the
  caller's `limit`, `DELTA_BATCH_LIMIT` by default and as low as `DELTA_BATCH_FLOOR` for a
  ticker whose reads keep timing out, fix 26); `id` is the recorder's insertion order and the
  only monotone quantity on the tape, so a cursor bounds the scan by itself and a `ts` beside it
  only buys the planner a second index to AND (fix 22). The `ts >= lower` bound survives on the
  first read of a ticker, where there is no cursor yet. Prints have no cursor at all -- the
  executor rescans them from `placed_at - 60 s` every loop and the simulator's print watermark
  absorbs the re-feed.
* **The past instant (`at`).** A replay executor's clock is a grid instant days in the past, so
  every read it makes has to stop there: the tape at its head, the signals of a later run, a gap
  snapshot priced an hour afterwards would all be information the live loop could not have had.
  Each reader below therefore takes an `at: datetime | None` -- None is the live path and reads
  the head, a value is the replay path and bounds the read on `ts <= at` (`created_at <= at` for
  the pricing tables), ordered by `(ts, id)` for the tape (Task 13, ruling 2).

  The horizon reaches every table that has a history to bound. It cannot reach the dimension
  tables: `venue_markets.match_status` / `match_key` and `games.kickoff_utc` are read at their
  current values, so a market matched (or a kickoff moved) *after* the replayed instant reads
  that way in the replay. Bounding those would need a history the schema does not keep, so this
  is a stated limitation of a replay rather than something the reader can fix (review M7).

Reads return frozen views from `harness.execution.plan` wherever the decision chain consumes
them, so the loop never passes a raw `Row` into a pure function.
"""

import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable, NamedTuple, Sequence

from sqlalchemy import bindparam, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import (
    EquitySnapshot, Fill, Intent, Ledger, MarketDirtyInterval, MarketObservationInterval,
    Order, OrderEvent, OrderWatchSample,
)
from harness.db.schema import OPEN_FILL_SQL
from harness.ops.clock import exclude_unsynced_runs
from harness.execution.book import DIRTY_CAUSES
from harness.execution.fills import TapeDelta, TapePrint
from harness.execution.plan import FillView, IntentView, PositionView
from harness.strategy.variants import with_defaults

log = logging.getLogger(__name__)

#: The advisory lock the single live executor holds for the length of one step. `hashtext` is
#: stable for a given string across a cluster's lifetime, which is all this needs.
LOCK_KEY = "harness.exec"
#: A replay executor's own key. It writes only `replay = true` rows, so it races nothing the
#: live loop owns -- and sharing one key would let an operator's `replay --execute` and the
#: service take turns skipping each other's steps for the length of a replayed day.
REPLAY_LOCK_KEY = "harness.exec.replay"

#: The prints a fill simulation rescans on every loop, measured back from `placed_at` (§1).
PRINT_LOOKBACK = timedelta(seconds=60)

OPEN_STATUSES = ("open", "partially_filled")


# --- the advisory lock ----------------------------------------------------------------


def try_lock(conn, key: str = LOCK_KEY) -> bool:
    """Take the executor's session-level advisory lock, or report that someone else has it."""
    return bool(conn.execute(text("select pg_try_advisory_lock(hashtext(:k))"),
                             {"k": key}).scalar())


def unlock(conn, key: str = LOCK_KEY) -> None:
    conn.execute(text("select pg_advisory_unlock(hashtext(:k))"), {"k": key})


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


def _live_and_at(sql: str, bound: str) -> tuple:
    """The live and past-instant forms of one statement, both compiled once at import.

    `sql` carries a `{at}` placeholder in its `where`: the live form drops it, the `at` form
    fills it with `bound`. Building the two up front rather than per call keeps SQLAlchemy's
    compiled-statement cache working, which a `text()` made inside the loop would defeat.
    """
    return text(sql.format(at="")), text(sql.format(at=bound))


#: Fix 57, ruling 1 (journal 184): these three statements are the in-game readers that reach a
#: run's gap snapshots through `signals.run_id`, so each excludes a run recorded under an
#: unsynchronized kernel clock (the key is named once in `harness/ops/clock.py`). An absent run
#: row, a NULL `notes` and a run without the key all keep their rows, so no live number moves.
#: `{{at}}` in these f-strings stays the literal `{at}` `_live_and_at`'s own `.format` fills.
_CANDIDATES, _CANDIDATES_AT = _live_and_at(f"""
select s.id as signal_id, s.variant_id, s.venue_market_id, s.side, s.price_target, s.contracts,
       s.edge, s.edge_min, s.fair_p, s.stake, s.created_at, s.as_estimate, s.gap_snapshot_id,
       m.ticker, m.venue, m.game_id, g.kickoff_utc, gs.fair_value_id
from signals s
join venue_markets m on m.id = s.venue_market_id
left join games g on g.id = m.game_id
left join market_gap_snapshots gs on gs.id = s.gap_snapshot_id
left join intents i on i.signal_id = s.id
where s.decision = 'candidate' and s.replay = :replay and s.variant_id = any(:variants)
  and s.created_at >= :lower{{at}} and i.signal_id is null
  and {exclude_unsynced_runs('s.run_id')}
order by s.id
""", " and s.created_at <= :at")


def candidate_signals(session: Session, variant_ids: Sequence[str], lower: datetime,
                      replay: bool, at: datetime | None = None) -> list:
    """Candidate signals of the executed variants inside the intent TTL that have no intent yet.

    Nothing is filtered out beyond that (§1): whether the loop can act on the signal is the
    decision chain's business, and the intent row is the record that it was offered one.

    `at` is a replay executor's grid instant: a replayed range holds every run's signals from
    the first step, and acting at 19:00 on a signal a 19:30 run produced would be the one thing
    a replay must never do.
    """
    if not variant_ids:
        return []
    params = {"replay": replay, "variants": list(variant_ids), "lower": lower}
    stmt = _CANDIDATES
    if at is not None:
        stmt, params = _CANDIDATES_AT, params | {"at": at}
    return session.execute(stmt, params).all()


_MARKET_TYPES = text("select id, market_type from venue_markets where id = any(:ids)")


def _market_types_of(session: Session, rows: Sequence) -> dict[int, str]:
    """Every candidate's market type, in one statement.

    `veto_queue.market_type` is NOT NULL and part of the bucket key, and `candidate_signals` does
    not select it, so it is read here -- once for the batch rather than once per intent. The
    executor's loop ceiling is 7.5 s and a burst is exactly when both the row count and the
    contention are highest, which is the wrong moment for an N+1 (review round 1, minor).
    """
    ids = sorted({row.venue_market_id for row in rows})
    if not ids:
        return {}
    return {r.id: r.market_type for r in session.execute(_MARKET_TYPES, {"ids": ids})}


def _queue_values(row, now: datetime, market_types: dict[int, str], minutes: int) -> dict:
    """One `veto_queue` row's values for one intent this call wrote (addendum 0.2, ruling B-C1).

    One row per **signal**, never one per bucket: a unique index on the bucket key would refuse
    the second and later signals of a burst and their ids would never be persisted anywhere,
    which is selection rather than attenuation -- the deduped signals are exactly the ones on a
    moving line.

    `bucket_start` is a plain column with a partial index; the worker claims every unclaimed row
    of a bucket at once. Pure -- no session, no I/O -- so building the whole batch's values
    cannot itself be the thing a savepoint has to guard against (fix round 2, I2).
    """
    from harness.research.veto import bucket_start

    return dict(signal_id=row.signal_id, game_id=row.game_id,
               market_type=market_types.get(row.venue_market_id) or "unknown",
               bucket_start=bucket_start(row.created_at, minutes),
               enqueued_at=now, claimed_at=None)


def _write_queue_batch(session: Session, queue_values: list[dict]) -> None:
    """The whole batch's `veto_queue` rows, one insert."""
    from harness.db.models import VetoQueue

    session.execute(insert(VetoQueue).values(queue_values)
                    .on_conflict_do_nothing(index_elements=["signal_id"]))


def insert_intents(session: Session, rows: Sequence, now: datetime, replay: bool,
                   keys_out: list | None = None) -> int:
    """One intent per candidate signal; returns how many rows this call actually wrote.

    Phase 5 hangs the veto queue off this loop, because it is already exactly once per signal:
    the insert is `on conflict do nothing` on `signal_id` and only a returned row counts.

    `keys_out`, when given, collects one `(variant_id, venue_market_id, side)` tuple per intent
    this call actually wrote, for the caller's `intent_episodes` upsert (6D §1.7(c)). It is an
    out-parameter rather than a second return value because `stats.intents_new =
    store.insert_intents(...)` reads at every call site and the count is what those sites want.
    """
    from harness.config.settings import get_settings

    written = 0
    # Collected here, not queued yet: building the batch (the market-type lookup included) has
    # to sit inside the same guard as the write, or a lookup that raises -- a statement timeout
    # under contention, say -- escapes this function and costs every intent this call wrote
    # (fix round 3, guarding `_market_types_of` too). A replay writes nothing: H9 is measured on
    # live signals only.
    written_rows: list = []
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
            if keys_out is not None:
                keys_out.append((row.variant_id, row.venue_market_id, row.side))
            if not replay:
                written_rows.append(row)
    if written_rows:
        # Phase 5: the veto queue, built and written together in one savepoint (fix round 2, I2;
        # fix round 3 folds the market-type lookup into the same guard). `candidate_signals` has
        # no LIMIT, so a recorder/executor gap can put well over sixty-four intents through the
        # loop above in one transaction, and PostgreSQL's `pg_subtrans` SLRU overflows past 64
        # subtransactions -- a cost every concurrent reader pays, cluster-wide, on a 2 GB
        # Postgres. One savepoint around the lookup and the batched insert buys the isolation
        # the comment above promises (a queue failure, of any kind, never costs an intent) at one
        # subtransaction regardless of batch size.
        try:
            with session.begin_nested():
                minutes = get_settings().veto_bucket_minutes
                market_types = _market_types_of(session, written_rows)
                queue_values = [_queue_values(row, now, market_types, minutes)
                               for row in written_rows]
                _write_queue_batch(session, queue_values)
        except Exception:  # noqa: BLE001
            log.exception("veto enqueue failed for a batch of %d intent(s)", len(written_rows))
    return written


_NEWEST_INTENTS, _NEWEST_INTENTS_AT = _live_and_at(f"""
select distinct on (i.variant_id, i.venue_market_id, i.side)
       i.id, i.signal_id, i.variant_id, i.venue_market_id, i.ticker, i.side, i.target_prob,
       i.target_contracts, i.edge, i.edge_min, i.fair_p, i.fair_row_id, i.game_id,
       i.kickoff_utc, i.stake, i.signal_created_at, i.venue,
       s.as_estimate, s.gap_snapshot_id
from intents i
left join signals s on s.id = i.signal_id
where i.replay = :replay and i.variant_id = any(:variants)
  and i.signal_created_at >= :lower{{at}}
  and {exclude_unsynced_runs('s.run_id')}
order by i.variant_id, i.venue_market_id, i.side, i.signal_created_at desc, i.created_at desc,
         i.signal_id desc, i.id desc
""", " and i.signal_created_at <= :at")

_NEWEST_DECISIONS, _NEWEST_DECISIONS_AT = _live_and_at(f"""
select distinct on (s.variant_id, s.venue_market_id, s.side)
       s.variant_id, s.venue_market_id, s.side, s.decision
from signals s
where s.replay = :replay and s.variant_id = any(:variants) and s.created_at >= :lower{{at}}
  and {exclude_unsynced_runs('s.run_id')}
order by s.variant_id, s.venue_market_id, s.side, s.created_at desc, s.id desc
""", " and s.created_at <= :at")


def load_intents(session: Session, variant_ids: Sequence[str], lower: datetime,
                 replay: bool, at: datetime | None = None) -> tuple[list[IntentView], dict]:
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
    intents_stmt, decisions_stmt = _NEWEST_INTENTS, _NEWEST_DECISIONS
    if at is not None:
        # The `at` bound belongs on both halves: a decision a later run reversed must not
        # cancel an order the replayed instant had every reason to still be holding.
        intents_stmt, decisions_stmt = _NEWEST_INTENTS_AT, _NEWEST_DECISIONS_AT
        params = params | {"at": at}
    decisions = {(r.variant_id, r.venue_market_id, r.side): r.decision
                 for r in session.execute(decisions_stmt, params).all()}
    views: list[IntentView] = []
    extras: dict = {}
    for row in session.execute(intents_stmt, params).all():
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
       -- 6B §1.3's ledger, per track. `state._state_of` reads `cancels_ahead` and `recon_state`
       -- off this row for each track, so leaving them out of the projection is an AttributeError
       -- on the first order of every step; `print_unmatched` is a derived sum the reader does not
       -- need (round 1, I5) and is projected beside them because it is the row's own ledger and a
       -- caller comparing it with the document should not need a second read.
       o.print_unmatched, o.cancels_ahead, o.recon_state,
       o.nw_print_unmatched, o.nw_cancels_ahead, o.nw_recon_state,
       -- Fix 78. The counterfactual's own two bucket sums and the version that last wrote any
       -- of these columns are read, not written, by `loop._unchanged`: it decides whether the
       -- per-row write this loop would make on a cancelled pending row would move anything at
       -- all, and a column it cannot see is a column it cannot compare -- including
       -- `nw_executor_version`, which `update_order` stamps itself, so a row last written by an
       -- older build is never mistaken for one with nothing to write.
       o.nw_pending_unmatched, o.nw_pending_surplus, o.nw_executor_version,
       o.nw_done, o.book_source, o.dirty_seconds, o.worst_case_fill,
       -- 6B §1.5. `cancelled_at` bounds the watched resting interval the dirty accrual is
       -- clamped to (`loop._clamped`); the two retry columns are read by `_tape`, which decides
       -- before any read whether this row's counterfactual is inside its backoff. All three are
       -- read off the row, so leaving them out of the projection is an AttributeError on the
       -- first dirty observation of every step.
       o.cancelled_at, o.nw_next_attempt_at, o.nw_attempts
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


_MARKETS, _MARKETS_AT = _live_and_at("""
select m.id as venue_market_id, m.ticker, m.venue, m.match_status, m.match_key, m.game_id,
       m.side_team_id, g.sport, g.kickoff_utc,
       gap.id as gap_snapshot_id, gap.fair_p, gap.fair_value_id, gap.staleness_s,
       gap.stale_allowance_s, gap.feed_kind, gap.best_bid, gap.best_ask, gap.venue_mid,
       fv.created_at as fair_ts
from venue_markets m
left join games g on g.id = m.game_id
left join lateral (
    select s.* from market_gap_snapshots s
    where s.venue_market_id = m.id{at} order by s.created_at desc, s.id desc limit 1
) gap on true
left join fair_values fv on fv.id = gap.fair_value_id
where m.id = any(:ids)
""", " and s.created_at <= :at")


def market_rows(session: Session, venue_market_ids: Iterable[int],
                at: datetime | None = None) -> dict[int, object]:
    """One row per market: its identity plus its newest gap snapshot and that snapshot's fair
    value. `fair_ts` is the fair value's own `created_at`, never the snapshot's.

    `at` picks the newest snapshot the replayed instant could have seen. Without it every
    market in a replay would be priced off the last snapshot of the whole range, which is the
    one number the decision chain must not be given."""
    ids = sorted(set(venue_market_ids))
    if not ids:
        return {}
    params = {"ids": ids}
    stmt = _MARKETS
    if at is not None:
        stmt, params = _MARKETS_AT, params | {"at": at}
    return {row.venue_market_id: row for row in session.execute(stmt, params).all()}


# The live statement with the instant added, bounded below as well as above (final review I2).
# `orderbook_events` is weekly-partitioned on `ts` with a per-partition PK of `(id, ts)`, so
# `ts <= :at` alone prunes only the partitions that start after the instant: inside the one
# holding it, the plan was a backward walk by `id` that filtered every row taped *after* the
# instant before it reached one at or before it. Replaying a Sunday game day is a walk over
# most of that week's tape, once per 15 s grid step, under the executor's own 10 s statement
# timeout -- a hard failure of the Monday replay duty rather than a slow query. A lower `ts`
# bound is answered off `ix_obe_ts_brin`, and the loop's question ("is the tape's newest row
# older than `book_max_age_s`") never needs to look further back than the first window.
# `(ts desc, id desc)` rather than `id desc`: the question is which row is newest by the
# recorder's clock, not which was inserted last.
_NEWEST_EVENT_AT = text(
    "select ts from orderbook_events where ts <= :at and ts > :lower "
    "order by ts desc, id desc limit 1")

#: The bounded tape lookback and its one widening. 10 minutes is five times `book_max_age_s`,
#: so any answer the loop would act on differently is inside it; 24 h is the backstop for a
#: replay that starts after a long recorder outage. Nothing older than that can read as
#: anything but a dead recorder, so the second empty answer is `None` rather than a third,
#: unbounded scan.
TAPE_WINDOW = timedelta(minutes=10)
TAPE_WINDOW_WIDE = timedelta(hours=24)


def newest_event_ts(session: Session, at: datetime | None = None) -> datetime | None:
    """The `ts` of the tape's newest row -- the recorder's own liveness signal.

    Live reads the head by `id`, the tape's insertion order. A replay executor asks the same
    question of its own instant instead, so a recorder outage inside the replayed range still
    reads as one (F36) rather than being papered over by rows taped hours later.

    The instant-bounded read looks back `TAPE_WINDOW`, then once more over `TAPE_WINDOW_WIDE`,
    and answers `None` beyond that -- which `Executor._body` already treats exactly as it
    treats a row older than `book_max_age_s`: `dead_recorder`.
    """
    if at is not None:
        for window in (TAPE_WINDOW, TAPE_WINDOW_WIDE):
            got = session.execute(_NEWEST_EVENT_AT, {"at": at, "lower": at - window}).scalar()
            if got is not None:
                return got
        return None
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

_PRINTS_AT = text("""
select trade_id, ts, yes_price, count, coalesce(taker_outcome_side, taker_side) as taker_side,
       source
from venue_trades where ticker = :t and ts >= :lower and ts <= :at order by ts, trade_id
""")


def load_prints(session: Session, ticker: str, lower: datetime,
                at: datetime | None = None) -> list[TapePrint]:
    """Prints for one ticker since `lower`, deduplicated by `trade_id`.

    The same trade reaches us from the WebSocket and from REST, and `venue_trades` is
    partitioned on `ts` -- so `ts` is part of its primary key and cannot deduplicate a pair of
    rows a millisecond apart. The first row per `trade_id` in `ts` order wins.

    `at` bounds the scan at a replay executor's own instant. `simulate_fills` already drops a
    print past its deadline, but `has_print` does not: it scans the whole list, so an unbounded
    replay would explain a fill with a trade printed after the instant that produced it.

    Fix 79 moved the winner rule itself into `_dedupe_prints`, which `PrintCache` derives its
    own output through, so the two paths cannot disagree about which row wins by drifting
    apart. The statement, its parameters and the list this returns are unchanged; the one
    difference is that a losing duplicate is now built before it is dropped, which cannot fail
    (`yes_price`, `count`, `taker_side` and `source` are all NOT NULL).
    """
    params = {"t": ticker, "lower": lower}
    stmt = _PRINTS
    if at is not None:
        stmt, params = _PRINTS_AT, params | {"at": at}
    return _dedupe_prints([_print_of(row) for row in session.execute(stmt, params).all()])


def _print_of(row) -> TapePrint:
    """One `_PRINTS`-projection row as the simulator's print. The only place the projection is
    turned into a `TapePrint`, so every path below produces the same object for the same row."""
    return TapePrint(trade_id=row.trade_id, ts=row.ts, yes_price=row.yes_price,
                     count=row.count, taker_side=row.taker_side, source=row.source)


def _dedupe_prints(rows: Sequence[TapePrint]) -> list[TapePrint]:
    """The first row per `trade_id` in the statement's order wins (`load_prints`' rule, §1).

    One implementation, shared by the full read and by `PrintCache`: "the same dedupe winner"
    is then identity by construction rather than two implementations that happen to agree.
    """
    seen: set[str] = set()
    out: list[TapePrint] = []
    for print_ in rows:
        if print_.trade_id in seen:
            continue
        seen.add(print_.trade_id)
        out.append(print_)
    return out


# --- fix 79: the live executor's print cache -------------------------------------------
#
# `_tape` re-reads every ticker's whole print window every loop because prints carry no
# cursor (§1). On production that is ~112,000 `venue_trades` rows a loop and 6.5 s of
# `exec.phase_tape_ms`, almost all of it client-side: the rows themselves, two `Decimal`
# quantizes per row and a `TapePrint` per row, rebuilt from scratch every 15 s.
#
# The cache below keeps the built `TapePrint`s per ticker and re-reads only the tail of the
# window, but it may never hand back a list that differs in any way from what `load_prints`
# would have returned at the same snapshot: a print the simulator does not see is a fill that
# is silently never inferred, no live check catches an under-fill, and a correction can only
# ever be a new row (§0.12). So every serve is guarded, and the guard is exact:
#
# * **One statement, never two.** Under READ COMMITTED each statement gets its own snapshot,
#   so a guard statement followed by an incremental read can be split by two commits that
#   reconcile the guard's count and still drop a print. The guard's aggregates are therefore
#   scalar subqueries in the *same* statement as the tail rows, evaluated on the one snapshot.
# * **Exact, not approximate.** `count(*)`, `min(ts)` and `max(ts)` over the window miss a
#   deletion and an insertion that cancel out between two loops -- and `venue_trades` does
#   have deleters (`harness/normalize/runner.py` drops `source = 'rest'` on the reprocess
#   path, and a retention policy would drop partitions). A fourth aggregate, the sum of the
#   per-row `_PRINT_TOKEN`, closes that: the sum changes whenever the multiset of primary keys
#   in the window changes. Python never recomputes a token, it only adds up tokens the server
#   itself produced and the cache stored beside the rows.
# * **Anything unexpected is today's full read.** A tail that comes back empty, a `lower` that
#   moved earlier, a window whose aggregates or whose merged rows disagree by so much as one
#   token: all of them re-seed from `_PRINTS_SEED`, which is `_PRINTS`' own projection, order
#   and predicate with the token beside it.
#
# It is live-only: a replay executor never gets one (`Executor.__init__`), and `_tape` refuses
# to use it whenever `at` is not None, because a past-instant read is a different window.

#: One print's primary key `(venue, trade_id, ts)` as a single 64-bit number, computed by the
#: server. `hashtextextended`'s second argument is the hash seed, so folding the epoch in there
#: makes the token depend on `ts` as well as on the venue and the trade id, with no dependence
#: on how Postgres would have rendered a timestamp as text (`DateStyle` is a session setting;
#: this is arithmetic). Microseconds, which is `timestamptz`'s own resolution, so no two
#: distinct stored instants share a seed: `extract(epoch from ts)` is `numeric` on PG 14 and
#: later and 1.8e9 seconds of epoch scaled by a million is nowhere near a `bigint`'s range.
#: Two different rows collide only by a 64-bit accident, and nothing here is adversarial input.
_PRINT_TOKEN = ("hashtextextended(venue || '/' || trade_id, "
                "(extract(epoch from ts) * 1000000)::bigint)")

#: The re-seed: `_PRINTS` exactly -- same predicate, same projection, same order -- plus the
#: token, so a seeded cache carries the server's own token for every row it holds.
_PRINTS_SEED = text(f"""
select trade_id, ts, yes_price, count, coalesce(taker_outcome_side, taker_side) as taker_side,
       source, {_PRINT_TOKEN} as tok
from venue_trades where ticker = :t and ts >= :lower order by ts, trade_id
""")

#: The cached ticker's whole loop, in one statement. The four scalar subqueries describe the
#: window the window `ts >= lower` -- how many rows, its two end instants and its token sum -- and the rows
#: are the tail from the cache's own maximum instant onward, in `_PRINTS`' projection and
#: order. The subqueries are uncorrelated, so the planner evaluates each once as an InitPlan,
#: and all of them see this statement's single snapshot along with the rows. Deliberately not
#: a CTE over the window: nothing here may materialise the rows the cache already holds.
_PRINTS_TAIL = text(f"""
select (select count(*) from venue_trades where ticker = :t and ts >= :lower) as win_n,
       (select min(ts) from venue_trades where ticker = :t and ts >= :lower) as win_min_ts,
       (select max(ts) from venue_trades where ticker = :t and ts >= :lower) as win_max_ts,
       (select sum({_PRINT_TOKEN}) from venue_trades
         where ticker = :t and ts >= :lower) as win_tok,
       trade_id, ts, yes_price, count, coalesce(taker_outcome_side, taker_side) as taker_side,
       source, {_PRINT_TOKEN} as tok
from venue_trades where ticker = :t and ts >= :since order by ts, trade_id
""")

#: Every print the cache may hold at once, across every ticker. A constant, not a setting: it
#: is a bound on one process's memory, and an operator who could raise it could turn the
#: executor into the thing that gets OOM-killed. One cached print measures 515 B, measured
#: rather than guessed at (`tracemalloc`, a 36-character trade id: the frozen dataclass, two
#: `Decimal`s, the trade id, the timestamp, the token and the three list slots), so this cap is
#: about 129 MB. Production reads ~121,000 prints a loop today, which is ~62 MB, so the cap is
#: roughly twice the current working set: room for the season to grow without the cap quietly
#: becoming the normal state, and far short of the swap this host has already been seen to
#: touch. Past it the *largest* tickers fall back to full reads -- they are the ones whose
#: windows a cache helps least per byte -- and every fallback is counted in
#: `exec.prints_cap_fallbacks`, so nothing is dropped silently.
PRINT_CACHE_MAX_ROWS = 250_000

#: The marker `_tape` reads to tell a failure of the cache's own statement from a failure of
#: `load_deltas` or of a full `load_prints`. Fix 26 shrinks a ticker's *delta* batch on any
#: statement timeout inside the tape block; a timeout on this statement says nothing about the
#: delta read's size, so it must not shrink it (review of the draft brief, defect 6).
PRINT_CACHE_STATEMENT_ATTR = "_harness_print_cache_statement"


@dataclass
class PrintReadCounts:
    """What one loop's print reads did, published as the `exec.prints_*` gauges."""

    #: Tickers served from the cache this loop (unchanged or merged), and tickers that took
    #: today's full read -- for whatever reason, including a first sight of the ticker.
    cached_tickers: int = 0
    full_reads: int = 0
    #: Rows the cache statements' tails brought back, summed over the tickers they served.
    incremental_rows: int = 0
    #: Admissions refused and entries evicted by `PRINT_CACHE_MAX_ROWS` this loop.
    cap_fallbacks: int = 0


@dataclass
class _CachedPrints:
    """One ticker's cached window, and the four facts it was read under.

    `rows` is every row of the window `ts >= lower` in `_PRINTS`' order, *before* the dedupe; `deduped` is
    `_dedupe_prints(rows)` and is what a caller is handed a copy of. `tokens[i]` is the
    server's `_PRINT_TOKEN` for `rows[i]`. `count`, `min_ts`, `max_ts` and `token_sum` describe
    the same window and are what the next loop's statement is compared against.
    """

    lower: datetime
    rows: list[TapePrint]
    tokens: list[int]
    deduped: list[TapePrint]
    count: int
    min_ts: datetime
    max_ts: datetime
    token_sum: int


def _entry_of(lower: datetime, rows: list[TapePrint], tokens: list[int]) -> _CachedPrints:
    """A cache entry whose four facts are derived from the rows themselves -- used where the
    rows *are* the whole window (a re-seed, or a window trimmed up to a later `lower`)."""
    return _CachedPrints(lower=lower, rows=rows, tokens=tokens, deduped=_dedupe_prints(rows),
                         count=len(rows), min_ts=rows[0].ts, max_ts=rows[-1].ts,
                         token_sum=sum(tokens))


class PrintCache:
    """Per-ticker print windows held in this process, refreshed by one statement per ticker
    per loop, value-identical to `load_prints` at the same snapshot (fix 79).

    The only entry point is `read`, which returns exactly what
    `load_prints(session, ticker, lower, None)` would have returned -- same rows, same order,
    same dedupe winners, equal `TapePrint` values -- and never the cache's own list: callers
    filter the list they are given (`_tape`'s hold-back) and scan it per fill (`has_print`),
    so a caller that mutated it would corrupt the next loop's answer.

    `evict` drops the tickers that no longer have a working row, exactly as `_delta_batch` is
    dropped for them. A ticker whose read *failed* keeps its entry: the entry describes a
    snapshot that really happened, and the next loop's guard re-validates it anyway.
    """

    def __init__(self, max_rows: int = PRINT_CACHE_MAX_ROWS) -> None:
        self._entries: dict[str, _CachedPrints] = {}
        self._rows = 0
        self.max_rows = max_rows

    @property
    def rows(self) -> int:
        """Prints held right now, across every ticker: `exec.prints_cached_rows`."""
        return self._rows

    def tickers(self) -> set[str]:
        return set(self._entries)

    def evict(self, keep) -> None:
        keep = set(keep)
        for ticker in list(self._entries):
            if ticker not in keep:
                self._drop(ticker)

    def read(self, session: Session, ticker: str, lower: datetime,
             counts: PrintReadCounts) -> list[TapePrint]:
        entry = self._entries.get(ticker)
        if entry is not None and lower != entry.lower:
            # A window that moved earlier is a window this cache has never seen all of; one
            # that moved later is the same window with a prefix that will never be read again,
            # and trimming it physically is what keeps a long-lived ticker's entry bounded.
            entry = self._restrict(ticker, entry, lower)
        if entry is None:
            return self._full(session, ticker, lower, counts)
        rows = self._tail(session, ticker, lower, entry.max_ts)
        counts.incremental_rows += len(rows)
        if not rows:
            # The row the cached maximum instant came from is gone: the tail cannot be empty
            # while it exists, so this is a deletion (or a dropped partition), never a quiet
            # loop. Nothing here tries to reason about it -- it is a full read.
            return self._full(session, ticker, lower, counts)
        # The four aggregates ride on every row of the tail; they describe the whole window
        # `ts >= lower`, on this statement's one snapshot, not just the rows fetched.
        guard = rows[0]
        window = (int(guard.win_n), guard.win_min_ts, guard.win_max_ts, int(guard.win_tok))
        tail = [_print_of(row) for row in rows]
        tail_tokens = [int(row.tok) for row in rows]
        cut = _cut_at(entry.rows, entry.max_ts)
        if (window == (entry.count, entry.min_ts, entry.max_ts, entry.token_sum)
                and tail == entry.rows[cut:]):
            # Nothing in the window moved, and the rows at its maximum instant are the rows
            # the cache already holds -- re-read, not assumed, because a row deleted and
            # another inserted at that same instant would otherwise reconcile every aggregate.
            counts.cached_tickers += 1
            return list(entry.deduped)
        merged = entry.rows[:cut] + tail
        merged_tokens = entry.tokens[:cut] + tail_tokens
        if (len(merged) != window[0] or sum(merged_tokens) != window[3]
                or merged[0].ts != window[1] or merged[-1].ts != window[2]):
            # The part of the window the cache kept is not the part the server still has.
            return self._full(session, ticker, lower, counts)
        counts.cached_tickers += 1
        return self._store(ticker, _CachedPrints(
            lower=lower, rows=merged, tokens=merged_tokens, deduped=_dedupe_prints(merged),
            count=window[0], min_ts=window[1], max_ts=window[2], token_sum=window[3]), counts)

    def _tail(self, session: Session, ticker: str, lower: datetime, since: datetime):
        try:
            return session.execute(
                _PRINTS_TAIL, {"t": ticker, "lower": lower, "since": since}).all()
        except Exception as exc:
            with suppress(Exception):  # an exception class that refuses attributes
                setattr(exc, PRINT_CACHE_STATEMENT_ATTR, True)
            raise

    def _full(self, session: Session, ticker: str, lower: datetime,
              counts: PrintReadCounts) -> list[TapePrint]:
        """Today's read of the whole window, and a re-seed from it. One statement, so the four
        facts the next loop is guarded against come from the same snapshot as the rows."""
        counts.full_reads += 1
        rows: list[TapePrint] = []
        tokens: list[int] = []
        for row in session.execute(_PRINTS_SEED, {"t": ticker, "lower": lower}).all():
            rows.append(_print_of(row))
            tokens.append(int(row.tok))
        if not rows:
            # An empty window has no maximum row to anchor a tail on, so there is nothing to
            # cache and nothing stale to keep.
            self._drop(ticker)
            return _dedupe_prints(rows)
        return self._store(ticker, _entry_of(lower, rows, tokens), counts)

    def _restrict(self, ticker: str, entry: _CachedPrints,
                  lower: datetime) -> _CachedPrints | None:
        if lower < entry.lower:
            self._drop(ticker)
            return None
        keep = 0
        while keep < len(entry.rows) and entry.rows[keep].ts < lower:
            keep += 1
        rows, tokens = entry.rows[keep:], entry.tokens[keep:]
        if not rows:
            self._drop(ticker)
            return None
        trimmed = _entry_of(lower, rows, tokens)
        self._rows -= keep
        self._entries[ticker] = trimmed
        return trimmed

    def _store(self, ticker: str, entry: _CachedPrints,
               counts: PrintReadCounts) -> list[TapePrint]:
        """Admit an entry under `max_rows`, evicting larger tickers before refusing this one.

        The rule is stable rather than round-robin: the set that stays cached is the smaller
        windows, the largest ones take a full read every loop, and a ticker is never dropped
        without `counts.cap_fallbacks` saying that it was.
        """
        self._drop(ticker)
        n = len(entry.rows)
        while self._rows + n > self.max_rows:
            victim = max(self._entries, default=None,
                         key=lambda t: (len(self._entries[t].rows), t))
            if victim is None or len(self._entries[victim].rows) <= n:
                counts.cap_fallbacks += 1
                return list(entry.deduped)
            self._drop(victim)
            counts.cap_fallbacks += 1
        self._entries[ticker] = entry
        self._rows += n
        return list(entry.deduped)

    def _drop(self, ticker: str) -> None:
        gone = self._entries.pop(ticker, None)
        if gone is not None:
            self._rows -= len(gone.rows)


def _cut_at(rows: list[TapePrint], ts: datetime) -> int:
    """The index of the first row at or after `ts` in a list ordered by `ts`. Scanned from the
    end because that is the length of the tie group at the window's maximum instant, not the
    length of the window."""
    cut = len(rows)
    while cut and rows[cut - 1].ts >= ts:
        cut -= 1
    return cut


#: The most rows one live delta read hands back for one ticker in one loop. A ticker with more
#: tape than this behind its cursor catches up over successive loops -- each one starting where
#: the last stopped -- instead of asking for the whole backlog in a single statement that runs
#: past the executor engine's 10 s statement timeout, dies, and leaves the next loop the
#: identical read to fail on (fix 22, journal 68). 20 000 delta rows is far more than a 15 s
#: loop can accrue on any real ticker, so the live path never truncates in steady state;
#: this is the catch-up bound.
DELTA_BATCH_LIMIT = 20_000

#: The smallest that cap is ever allowed to shrink to when a ticker's read keeps timing out
#: (fix 26). It is a measurement, not a guess: on the NAS under I/O pressure (a video transcode
#: holding `/proc/pressure/io` full at 60-70 %) a 500-row read at a cursor a million ids behind
#: cost 183 disk page reads, so a few hundred rows is what a cold read can finish inside the
#: executor engine's `EXEC_STATEMENT_TIMEOUT_MS = 10_000`; 20 000 needs thousands of pages and
#: cannot. Below this floor a lagging ticker would never walk off its backlog before its market
#: settled, so the floor is where shrinking stops and the read is simply allowed to fail.
DELTA_BATCH_FLOOR = 250


class DeltaBatch(NamedTuple):
    """One live delta read. `truncated` is "the limit was reached, so assume more to come" --
    the caller must treat the last row as a tape position it is still behind, never as the head
    of the tape."""

    deltas: list[TapeDelta]
    truncated: bool


# The live scan once a cursor exists: `(ticker, id)` alone, no `ts` predicate. `id` is the
# recorder's insertion order and is monotone, so every row past the cursor is also past the
# cursor's `ts` and the `ts >= :lower` bound adds nothing but work. On the production tape it
# added a great deal of it: `EXPLAIN` showed the planner ANDing a bitmap of the `ts` index
# (1 668 859 estimated rows for a 9-hour lower bound) with `(ticker, id)` to return 3 349 rows,
# once per open-order ticker per loop -- 30-140 s loops, rising `loops_skipped`, and finally
# the 30 s statement timeout (fix 22, journal 68).
_DELTAS = text("""
select id, ts, side, price, delta, sid, seq from orderbook_events
where ticker = :t and kind = 'delta' and id > :cursor
order by id limit :limit
""")

# The first read of a ticker, where `cursor = 0` bounds nothing and `ts >= :lower` is the only
# thing keeping a newly placed order off the whole season's tape.
_DELTAS_FIRST = text("""
select id, ts, side, price, delta, sid, seq from orderbook_events
where ticker = :t and kind = 'delta' and id > :cursor and ts >= :lower
order by id limit :limit
""")

# The past-instant scan (`ix_obe_ticker_ts`), matching `book._DELTAS_BY_TS`: bounded on `ts` at
# both ends and ordered by `(ts, id)`, never by `(sid, seq)`. Unchanged by fix 22 -- a replay
# reads a closed range at its own pace and has no loop deadline to miss.
_DELTAS_AT = text("""
select id, ts, side, price, delta, sid, seq from orderbook_events
where ticker = :t and kind = 'delta' and id > :cursor and ts >= :lower and ts <= :at
order by ts, id
""")


def tape_deltas(rows) -> list[TapeDelta]:
    """Delta rows as tape, dropping any row whose side, price or delta is NULL.

    Public because it is not only this module's (T9 review Minor 5): `harness/rescore.py` builds
    the same tape from its own bounded read, and a second copy of this filter -- or a reach into
    a private name across modules -- is how the replay and the loop would come to disagree about
    which rows are tape at all.
    """
    return [TapeDelta(event_id=r.id, ts=r.ts, side=r.side, price=r.price, delta=r.delta,
                      sid=r.sid, seq=r.seq)
            for r in rows if r.side is not None and r.price is not None and r.delta is not None]


def load_deltas(session: Session, ticker: str, cursor: int, lower: datetime,
                at: datetime | None = None, limit: int = DELTA_BATCH_LIMIT) -> DeltaBatch:
    """Deltas after `cursor`; `at` is the replay path's upper bound.

    Live orders by `id` with no upper bound -- the head is where the loop's clock is -- and is
    bounded to `limit` rows (`DELTA_BATCH_LIMIT` unless the caller has shrunk this ticker's
    batch after a timeout, fix 26), which is what makes a backlog cost several bounded loops
    instead of one unbounded read. The `ts >= lower` bound is carried only by the first
    read of a ticker (`cursor = 0`), where it is the only bound there is; once a cursor exists
    `id > cursor` is strictly stronger and the `ts` predicate only costs the planner a second
    index (fix 22).

    A replay executor stops at its own grid instant and orders by `(ts, id)`, which is the same
    order `_merge_events` re-imposes anyway, so the two paths hand the simulator the same
    sequence. That path keeps both bounds and takes no limit at all: it reads a closed range,
    at its own pace, so `limit` is not its business either.
    """
    if at is not None:
        rows = session.execute(
            _DELTAS_AT, {"t": ticker, "cursor": cursor, "lower": lower, "at": at}).all()
        return DeltaBatch(tape_deltas(rows), False)
    if cursor > 0:
        rows = session.execute(
            _DELTAS, {"t": ticker, "cursor": cursor, "limit": limit}).all()
    else:
        rows = session.execute(
            _DELTAS_FIRST,
            {"t": ticker, "cursor": cursor, "lower": lower, "limit": limit}).all()
    # Truncation is measured against the limit this read actually ran with, never against the
    # cap: a shrunk batch that came back full is exactly the ticker still behind the tape.
    return DeltaBatch(tape_deltas(rows), len(rows) >= limit)


# --- exposure -------------------------------------------------------------------------

#: The fill methods that are *money*: one the queue model inferred against the recorded tape,
#: and one the venue reported. Every exposure read in this module takes both (Task 11 ruling,
#: widened to the position reads in fix round 1, Important 2). `no_watcher` and
#: `snapshot_cross` are excluded because they are counterfactuals, never a trade of ours:
#: `no_watcher` is what an order would have done if we had left it alone, and `snapshot_cross`
#: is a book crossing.
#:
#: Leaving `venue` out would put the two halves of the exposure story out of step -- the daily
#: cap counting a live fill while `cap_per_game`, `max_open` and `open_stake` saw no position
#: at all -- and would let the live path spend the paper path's caps a second time. Nothing
#: changes in the deployed posture: `fill_method = 'venue'` rows exist only in live mode, which
#: is dormant, so every paper and replay number is byte-identical.
MONEY_FILL_METHODS = ("queue_model", "venue")

#: `OPEN_FILL_SQL` is the shared "this fill has not been settled yet" predicate, imported so the
#: view, this read and Floor's exposure read cannot drift apart (carried fix 56: an order that
#: was partially filled and then cancelled or expired keeps its own status forever, so its
#: settled fill stayed in the caps).
_POSITIONS = text(f"""
select o.variant_id, o.game_id, m.side_team_id, o.side,
       sum(f.contracts * f.prob) as stake, max(o.edge_at_place) as edge
from fills f
join orders o on o.id = f.order_id
join venue_markets m on m.id = o.venue_market_id
where f.fill_method = any(:methods) and o.replay = :replay and {OPEN_FILL_SQL}
group by o.variant_id, o.game_id, m.side_team_id, o.side
""")

_FILLS_TODAY = text("""
select o.variant_id, sum(f.contracts * f.prob) as stake
from fills f join orders o on o.id = f.order_id
where f.fill_method = any(:methods) and o.replay = :replay and f.filled_at >= :since
group by o.variant_id
""")


def load_positions(session: Session, replay: bool) -> list[PositionView]:
    """Unsettled positions, keyed `(game_id, side_team_id, side)` (Task 4b ruling): NO on A and
    YES on B are two positions, never one."""
    return [PositionView(variant_id=r.variant_id, game_id=r.game_id,
                         side_team_id=r.side_team_id, side=r.side,
                         stake=r.stake or Decimal("0"), edge=r.edge)
            for r in session.execute(
                _POSITIONS, {"replay": replay,
                             "methods": list(MONEY_FILL_METHODS)}).all()]


def load_fills_today(session: Session, replay: bool, since: datetime) -> list[FillView]:
    """Every watched fill since local midnight, settled or not (Task 5 ruling), on either fill
    method that is money: the queue model's and the venue's own (`MONEY_FILL_METHODS`)."""
    return [FillView(variant_id=r.variant_id, stake=r.stake or Decimal("0"))
            for r in session.execute(
                _FILLS_TODAY, {"replay": replay, "since": since,
                               "methods": list(MONEY_FILL_METHODS)}).all()]


def local_midnight(now: datetime, tz) -> datetime:
    """00:00 of `now`'s local day, back in UTC. The daily cap's day boundary (amendment 2)."""
    local = now.astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


# --- writes ---------------------------------------------------------------------------


def orders_for_intent(session: Session, intent_id) -> int:
    """How many orders this intent has already placed -- the `n` in its client order id."""
    return int(session.execute(text("select count(*) from orders where intent_id = :i"),
                               {"i": intent_id}).scalar() or 0)


#: The `nw_` column that is not a counterfactual measurement but the record of who wrote them.
NW_VERSION_COLUMN = "nw_executor_version"


def executor_version_numeric() -> Decimal:
    """`EXECUTOR_VERSION` as the numeric `orders.nw_executor_version` stores.

    Read off the package at call time rather than bound at import, exactly as
    `execution.plan.config_hash` reads it: a test that moves the version sees the move, and a
    build that bumps it stamps the new value without a reload. `Decimal` rather than the string,
    because the column is `numeric` and psycopg would otherwise send a text parameter for it.

    The column being `numeric` (spec amendment 0.17) makes the version's *shape* load-bearing: a
    bump to `4.5.1` or `4.6-rc1` would raise `decimal.InvalidOperation` from inside every order
    insert and every counterfactual step, which reads as a loop crash rather than as a decision
    someone has to make (review M-1). So the failure is re-raised here with the constant, the
    value and the column named, and the constraint is stated beside `EXECUTOR_VERSION` itself.
    """
    from harness import execution

    version = execution.EXECUTOR_VERSION
    try:
        return Decimal(version)
    except InvalidOperation as exc:
        raise ValueError(
            f"EXECUTOR_VERSION {version!r} is not a plain decimal numeral, and "
            "orders.nw_executor_version is a numeric column (spec amendment 0.17): keep the "
            "version a decimal numeral, or move the column to text in its own additive "
            "revision and amendment first") from exc


def stamp_nw_writer(values: dict) -> dict:
    """`values` plus `nw_executor_version`, when it writes any counterfactual column.

    Spec amendment 0.17 (roadmap row 72): every counterfactual write carries its writer's
    version, and nothing else touches the column -- a statement that writes no `nw_` column
    returns its own dict unchanged, so a purely watched update and a row nobody is writing are
    both left exactly as they were. Never a backfill.
    """
    if any(key.startswith("nw_") and key != NW_VERSION_COLUMN for key in values):
        return {**values, NW_VERSION_COLUMN: executor_version_numeric()}
    return values


def insert_order(session: Session, values: dict) -> int | None:
    """Insert one paper order, keyed on its client order id. None means it was already there.

    Placement writes the counterfactual's opening state (`nw_queue_remaining`,
    `nw_tape_cursor_event_id`), so it is a counterfactual write and carries the writer's version
    like every other one (amendment 0.17). `stamp_nw_writer` is the single rule, so this and
    `update_order` cannot drift apart.
    """
    stmt = (insert(Order).values(**stamp_nw_writer(values))
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
    bound with their real types rather than whatever psycopg would infer from a Python value.

    Spec amendment 0.17 (roadmap row 72): a write that touches any `nw_` column also stamps
    `nw_executor_version` with this build's `EXECUTOR_VERSION`. The stamp lives here rather
    than at the call sites because this is the one statement every counterfactual column of an
    order goes through, so a future `nw_` writer cannot forget it. It is never a backfill: a
    row nobody is writing is not in this statement at all, and a purely watched update (no
    `nw_` key) leaves the column exactly as it was.
    """
    if not values:
        return
    session.execute(update(Order).where(Order.id == order_id)
                    .values(**stamp_nw_writer(values)))


#: The ceiling on the counterfactual retry delay, in *elapsed wall seconds* (§0.14, ruling
#: CR-5). Never a loop count: the executor ran 27 loops in the 19:00 CT hour against a daytime
#: cadence of one every 7-10 s, so a loop-counted bound would stretch by an order of magnitude
#: in exactly the conditions it exists for.
NW_RETRY_MAX_S = 3600


def add_dirty_seconds(session: Session, order_id: int, seconds: int, *,
                      watched: bool = True) -> None:
    """A dirty book buys the order nothing this loop but the record that it happened (D6).

    Accrual stays **nominal** -- `exec_period_s` per observation -- because `dirty_minutes` is
    integer division of these seconds (the classification boundary is 60 accrued seconds, not
    one) and the gate reads it. Changing the units would move that classification in both
    directions, which is "which resting interval counts" under R1 and goes to the user as
    §0.13c. Elapsed truth is recorded in `market_dirty_intervals` and derived by
    `harness.execution.dirty_time.order_dirty_time`; the two are different quantities by design
    and both are reported (ruling I-4).

    `watched` picks the track. The watched columns accrue only while the order is in
    `OPEN_STATUSES`, which the caller enforces, and the counterfactual's accrual has its own
    column: a cancelled order whose counterfactual still runs must not go on accruing against
    the record of what the order we placed did (§0.9).
    """
    if seconds <= 0:
        return
    if watched:
        session.execute(text(
            "update orders set dirty_seconds = dirty_seconds + :s, "
            "dirty_minutes = (dirty_seconds + :s) / 60 where id = :i"),
            {"s": int(seconds), "i": order_id})
        return
    # The counterfactual's own accrual carries the writer's version with it (amendment 0.17),
    # like every other `nw_` write; the watched branch above writes no `nw_` column and so
    # leaves the version alone.
    session.execute(text(
        "update orders set nw_dirty_seconds = coalesce(nw_dirty_seconds, 0) + :s, "
        "nw_executor_version = :v where id = :i"),
        {"s": int(seconds), "v": executor_version_numeric(), "i": order_id})


#: Fix 78: the counterfactual dirty accrual of a whole loop's cancelled pending population, in
#: one statement. The clamp is `loop._clamped(period, row, now, watched=False)` written in SQL
#: and nothing else: an order with no expiry accrues the whole period, and one with an expiry
#: accrues the seconds between `now` and it, floored at zero and capped at the period.
#:
#: The arithmetic is equal to Python's term for term. `expiry` and `:now` are both
#: `timestamptz`, whose difference is an interval between two instants: the SQL side is
#: zone-free, and `timestamptz - timestamptz` yields no month field, so `extract(epoch ...)` is
#: exact rather than 30-day-approximated. The Python side agrees with it because the executor's
#: clock is `datetime.now(timezone.utc)` and psycopg returns UTC-offset instants, so its
#: subtraction is offset-aware too. That agreement is the clock's, not the arithmetic's:
#: `datetime.__sub__` ignores a *common* `tzinfo` and subtracts wall clocks, so a clock and an
#: expiry sharing one DST `ZoneInfo` across a fold would make `_clamped` -- the per-row path --
#: the wrong one of the two, and this statement the right one (round 1 review, M3). `trunc` is
#: Python's `int()` on the resulting seconds: both truncate toward zero, and where they could
#: disagree (below zero, on a fraction) `greatest(0, ...)` sends both to the same 0. The `::int`
#: cast rounds rather than truncates, which is why it is applied *after* `least(:p, ...)`: the
#: value it sees is a whole number no larger than the period, so the rounding is exact and a
#: far-future expiry cannot overflow the cast.
#:
#: `least()` ignoring NULL is never reached: a null expiry is answered by the `case` above it.
#: `seconds > 0` is `add_dirty_seconds`'s own early return, so a row whose clamp is zero is not
#: written at all and keeps whatever version last wrote it.
_NW_DIRTY_BATCH = text("""
update orders o
   set nw_dirty_seconds = coalesce(o.nw_dirty_seconds, 0) + c.seconds,
       nw_executor_version = :v
  from (select id,
               (case when expiry is null then :p
                     else greatest(0, least(:p, trunc(extract(epoch from (expiry - :now)))))
                end)::int as seconds
        from orders where id = any(:ids) and not nw_done) c
 where o.id = c.id and c.seconds > 0
""")

#: Fix 78's other set-based write: `loop._close_nw_if_expired` for the same population. The
#: predicate is that helper's own (`not nw_done and expiry is not None and expiry <= now`) and
#: the stamp is the one `update_order` adds to any `nw_` write (amendment 0.17).
_NW_CLOSE_BATCH = text("""
update orders set nw_done = true, nw_executor_version = :v
 where id = any(:ids) and not nw_done and expiry is not null and expiry <= :now
""")


def add_nw_dirty_seconds_batch(session: Session, order_ids: Sequence[int], period: int,
                               now: datetime) -> None:
    """One loop's no-watcher dirty accrual for many orders, in one statement (fix 78).

    Same rows and same values as calling `add_dirty_seconds(..., watched=False)` per row with
    `loop._clamped(period, row, now, watched=False)`: the clamp is that function transcribed
    into SQL (see `_NW_DIRTY_BATCH`), the stamp is the same `nw_executor_version`, and a row
    whose clamp is zero is left alone exactly as the per-row writer's early return leaves it.
    A finished track (`nw_done`) is excluded here as the caller's `if not row.nw_done` excludes
    it there.

    The watched columns are never touched. `dirty_seconds` accrues only while the order is
    actually resting and derives its `dirty_minutes` twin in the same statement; both belong to
    the per-row path, which this batch does not serve -- the caller passes cancelled rows only.
    """
    if not order_ids:
        return
    session.execute(_NW_DIRTY_BATCH, {"ids": list(order_ids), "p": int(period),
                                      "now": now, "v": executor_version_numeric()})


def close_nw_expired_batch(session: Session, order_ids: Sequence[int], now: datetime) -> None:
    """Close every past-expiry counterfactual in `order_ids`, in one statement (fix 78).

    The per-row twin is `loop._close_nw_if_expired`, which writes `nw_done = true` through
    `update_order` and is stamped by it; this writes the same column on the same predicate with
    the same stamp. A track with no expiry is never closed, which is R8's point: the expiry is
    the only guarantee an order stopped resting.
    """
    if not order_ids:
        return
    session.execute(_NW_CLOSE_BATCH,
                    {"ids": list(order_ids), "now": now, "v": executor_version_numeric()})


#: Fix 78b: the most rows one `UPDATE ... FROM (VALUES ...)` carries. A bound on the *driver*,
#: not on the population: psycopg sends at most 65,535 parameters in one statement, and one
#: counterfactual write is 14 or 15 columns -- the 13 or 14 of `_nw_columns` plus the
#: `nw_executor_version` stamp `update_orders_batch` adds itself -- plus the id, so a backlog
#: past ~4,100 rows would fail a whole batch on a limit that has nothing to do with what is
#: being written. 500 rows is 8,000 parameters, far under it, and keeps the statement's own
#: parse time small: the
#: 6,387 pending tracks of 16:01 CT on 2026-09-15 cost 13 statements at worst rather than 6,387.
VALUES_BATCH_ROWS = 500

#: One compiled SQL type per `orders` column, per dialect. `Numeric(14, 2)` compiles to
#: `NUMERIC(14, 2)`, `JSONB` to `JSONB`, `DateTime(timezone=True)` to `TIMESTAMP WITH TIME ZONE`
#: -- the model's own type, which is the whole point of casting to it below.
_VALUES_CAST: dict[tuple[str, str], str] = {}


def _values_cast(col, dialect) -> str:
    key = (dialect.name, col.name)
    sql = _VALUES_CAST.get(key)
    if sql is None:
        sql = _VALUES_CAST[key] = col.type.compile(dialect)
    return sql


def _update_orders_values(session: Session, columns: Sequence[str],
                          rows: Sequence[tuple[int, dict]]) -> None:
    """One `UPDATE orders ... FROM (VALUES ...)` for rows that write the same columns.

    **Types.** Every cell is a bind parameter of the model column's own type -- the same
    `Order.__table__.c[name].type` `update_order` binds through, so JSONB goes out through the
    dialect's JSONB bind processor and a contract count as a `numeric` rather than as whatever
    psycopg would infer -- inside an explicit `cast(... as <that column's SQL type>)`. The cast
    is not decoration: a bare parameter inside a `VALUES` list in a `FROM` clause is typed by
    PostgreSQL's own resolution of the list, and a column whose every row is NULL would resolve
    to `text` and then fail against a `numeric`, `jsonb` or `timestamptz` column. With the cast,
    a NULL in the dict is that column's typed NULL and writes NULL, exactly as the per-row
    statement does.

    **Identifiers.** Every name is looked up in `Order.__table__.c` first, so anything that is
    not a real column of `orders` raises here rather than reaching the database, and the SQL is
    built from the model's own column names, never from caller text.
    """
    table = Order.__table__
    cols = [table.c[name] for name in columns]
    dialect = session.get_bind().dialect
    id_cast = _values_cast(table.c.id, dialect)
    tuples, binds = [], []
    for i, (order_id, values) in enumerate(rows):
        cells = [f"cast(:id_{i} as {id_cast})"]
        binds.append(bindparam(f"id_{i}", order_id, type_=table.c.id.type))
        for j, col in enumerate(cols):
            cells.append(f"cast(:c{j}_{i} as {_values_cast(col, dialect)})")
            binds.append(bindparam(f"c{j}_{i}", values[col.name], type_=col.type))
        tuples.append(f"({', '.join(cells)})")
    assignments = ", ".join(f'"{col.name}" = v."{col.name}"' for col in cols)
    names = ", ".join(['"id"'] + [f'"{col.name}"' for col in cols])
    sql = (f"update orders o set {assignments} "  # noqa: S608 - model column names only
           f"from (values {', '.join(tuples)}) as v ({names}) where o.id = v.\"id\"")
    session.execute(text(sql).bindparams(*binds))


def update_orders_batch(session: Session, updates: Sequence[tuple[int, dict]], *,
                        chunk: int = VALUES_BATCH_ROWS) -> int:
    """Write many orders' columns in one statement per column set (fix 78b). Returns how many.

    `updates` is `(order_id, values)` pairs, each `values` exactly the dict that row's own
    `update_order(session, order_id, values)` would have been given. Same columns and same
    values as that call, row for row: the stamp goes on through `stamp_nw_writer`, the one rule
    that owns it (amendment 0.17), so a counterfactual write carries `nw_executor_version` here
    exactly as it does there, and a row whose dict writes no `nw_` column is left unstamped.

    Rows are grouped by the *set of columns* they write, and each group is one statement (per
    `chunk` rows, which is psycopg's parameter limit, not a policy -- see `VALUES_BATCH_ROWS`).
    Grouping rather than unioning the column sets is deliberate: a `VALUES` list has one shape,
    and filling a column a row did not ask to write with the value it already holds would make
    this statement touch columns the per-row path does not name. There are two groups in
    practice -- `worst_case_fill` is in the dict only for a track that has crossed -- so the
    cost stays flat in the population.

    Nothing here decides *which* rows are written: the caller has already established that each
    of these rows would take this write on the per-row path, and that no row in the list is also
    written per-row in the same loop.
    """
    groups: dict[tuple[str, ...], list[tuple[int, dict]]] = {}
    for order_id, values in updates:
        if not values:
            continue
        stamped = stamp_nw_writer(values)
        groups.setdefault(tuple(sorted(stamped)), []).append((order_id, stamped))
    statements = 0
    for columns, rows in groups.items():
        for start in range(0, len(rows), chunk):
            _update_orders_values(session, columns, rows[start:start + chunk])
            statements += 1
    return statements


def set_nw_backoff(session: Session, order_id: int, attempts: int,
                   next_attempt_at: datetime | None) -> None:
    """Record one counterfactual's retry position. `next_attempt_at` None resets it.

    Nothing here closes a track: `nw_done` alone distinguishes a completed counterfactual from a
    pending one (ruling I-16), and every consumer of the `nw_*` columns filters or labels on it.
    """
    session.execute(update(Order).where(Order.id == order_id)
                    .values(nw_attempts=attempts, nw_next_attempt_at=next_attempt_at,
                            nw_executor_version=executor_version_numeric()))


#: One statement per table, built once at import and indexed by the table literal, so a caller
#: passing anything else raises a `KeyError` here rather than composing SQL and the table name is
#: never a runtime format argument (review MI-4). Built from the models rather than as `text()`
#: for one reason the plan's draft could not have known: `Session.execute` autoflushes an
#: ORM-enabled statement and **not** a plain `text()` one, and both of these have to see the rows
#: this same step has already `session.add`ed -- otherwise a step opens a second row for a market
#: that already has one open, and a close misses the row it was called for. The statements are
#: still built once, so the compiled cache is exactly what a module-level `text()` would get.
_DIRTY, _OBSERVED = "market_dirty_intervals", "market_observation_intervals"
_MODELS = {_DIRTY: MarketDirtyInterval, _OBSERVED: MarketObservationInterval}
# Rides `ix_mdi_market_started` / `ix_moi_market_started` (venue_market_id, started_at).
_OPEN_INTERVAL = {
    name: (select(model.id)
           .where(model.venue_market_id == bindparam("vm"), model.ended_at.is_(None),
                  model.replay == bindparam("replay"))
           .order_by(model.started_at.desc()).limit(1))
    for name, model in _MODELS.items()}
# `p_`-prefixed names: a bind parameter sharing a column's name is reserved for the SET clause
# of an UPDATE and raises `CompileError` if it also appears in the WHERE.
_CLOSE_INTERVAL = {
    name: (update(model)
           .where(model.venue_market_id.in_(bindparam("p_vms", expanding=True)),
                  model.ended_at.is_(None), model.replay == bindparam("p_replay"))
           .values(ended_at=bindparam("p_ts")))
    for name, model in _MODELS.items()}


def open_interval(session: Session, table: str, venue_market_id: int, ticker: str,
                  ts: datetime, replay: bool, cause: str | None = None) -> None:
    """Open an interval for this market, unless one is already open.

    Both tables carry at most one open row per market per replay flag, which is the invariant
    §2 checks. The read rides `ix_mdi_market_started` / `ix_moi_market_started` with `limit 1`.

    `cause`, when given, must be a member of `harness.execution.book.DIRTY_CAUSES` (amendment
    0.19, journal 224 item 8): that tuple is the single dirty-cause vocabulary, and rows land
    here whether they came from `BookState.mark_dirty`'s own check or from one of the loop's two
    causes (`recorder_dead`, `book_unreadable`), which never pass through `mark_dirty` at all --
    so this is where the vocabulary is enforced for those two, and a second, cheap check for the
    book's own four.
    """
    if cause is not None and cause not in DIRTY_CAUSES:
        raise ValueError(f"unknown dirty cause {cause!r}")
    if session.execute(_OPEN_INTERVAL[table],
                       {"vm": venue_market_id, "replay": replay}).first() is not None:
        return
    values = {"venue_market_id": venue_market_id, "ticker": ticker, "started_at": ts,
              "ended_at": None, "replay": replay}
    if cause is not None:
        values["cause"] = cause
    session.add(_MODELS[table](**values))


def close_intervals(session: Session, table: str, venue_market_ids: list[int], ts: datetime,
                    replay: bool) -> None:
    """Close every open interval for these markets, stamped at `ts`.

    Called with the markets that left the step's set and with the markets the step found clean,
    stamped at the last observation that saw them (review I-6), so a market whose last order
    closes while dirty cannot leave a row open forever. One statement for the whole list, never
    one per ticker (controller note): about 110 statements a step on a 55-ticker loop is what the
    per-ticker shape would cost against a p95 loop of 227 s. It is a no-op on an empty list.
    """
    if not venue_market_ids:
        return
    session.execute(_CLOSE_INTERVAL[table],
                    {"p_vms": list(venue_market_ids), "p_ts": ts, "p_replay": replay})


#: Fix 78c (review rev-fix-78c, I2/I3): the first gap on a subscription after the tape position
#: an anchor accounts for. `book._GAP_AFTER_AT` is the same predicate -- it is the bounded test
#: the live loop uses to call a *historical* book dirty (the unbounded `book._GAP_AFTER` is the
#: live-book test and has no `ts` bound to share) -- and this takes the instant of the earliest
#: such (review rev-fix-78c-r1, M1)
#: row rather than asking whether one exists. `min`, never `max`: the walk that reads it stops
#: at the *first* stretch of dirtiness after its own position, so a track deferred across two
#: dirty cycles cannot walk through the earlier one as though the market had been clean.
_FIRST_GAP_AFTER = text("""
select min(ts) from orderbook_events
where kind = 'gap' and sid = :sid and id > :anchor_id and ts <= :at
""")


def first_gap_ts(session: Session, sid: int, anchor_id: int,
                 at: datetime) -> datetime | None:
    """When the tape this book was anchored on next broke, or None if it never did.

    The instant is the gap row's own `ts` -- when the tape actually broke -- not the instant a
    loop noticed it. `market_dirty_intervals.started_at` is the latter: `_advance_books` opens
    the interval with its own `now`, which is the first 15 s loop whose book read came back
    dirty, so it is at or after the gap by construction and the stretch between them is tape
    the dirty verdict exists to distrust (review rev-fix-78c, I2).

    Bounded at `at` for the reason every read of this module is: an event the step's own
    instant had not reached is not something the step knows. A gap is per subscription and
    dirties every ticker on it (§0.12), which is why this asks about `sid` and not about a
    ticker -- the hotfix gap rows carry `ticker = ''`.
    """
    return session.execute(_FIRST_GAP_AFTER,
                           {"sid": sid, "anchor_id": anchor_id, "at": at}).scalar()


def open_interval_market_ids(session: Session, table: str, replay: bool,
                             now: datetime) -> set[int]:
    """Every `venue_market_id` with a currently-open row in `table`, for this replay flag.

    Fix 70 leak (journal 224 item 4): `_advance_books` used to derive `gone` from
    `self._market_ids`, an in-memory map of the *previous* step's tickers. A step that raises
    after `_advance_books` returns rolls that step's own inserts back with the rest of its
    transaction, and a process restart starts every in-memory map back at empty -- either way,
    a market whose row is still open in the database can become invisible to every later step's
    `gone`, and the row never closes. This is the database's own answer to "what is still open",
    which nothing in memory can get out of sync with: `_advance_books` reads it once per step
    and keeps no cross-step map of its own. The predicate leads on `ended_at`, not on
    `venue_market_id`, so it does not ride `ix_mdi_market_started` / `ix_moi_market_started`
    the way `open_interval`'s own read does: it is one sequential scan of a table whose live
    (open) set is a few dozen rows, and a partial index on the open rows is the answer if the
    table itself ever grows enough for the scan to matter.

    Bounded on `started_at <= now` (review batch A, I-1): without this bound a row that started
    after this step's own instant -- a second replay over the same window, or a backwards host
    clock jump -- could still be named `gone` and closed at `now`, stamping `ended_at <
    started_at`. A step never closes a row that had not started at its own instant.
    """
    model = _MODELS[table]
    stmt = select(model.venue_market_id).where(model.ended_at.is_(None),
                                               model.replay == replay,
                                               model.started_at <= now)
    return {row[0] for row in session.execute(stmt).all()}


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
