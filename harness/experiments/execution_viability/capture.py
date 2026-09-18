"""§1.3(a)-(d): the timestamped capture, the replay clock and what the capture may not see.

Three things live here, and the order matters:

* `resolve_instants()` is **the replay clock** (ruling C1). Per-loop instants are retained
  nowhere -- `exec_heartbeat` is one overwritten row and `exec.loop_ms` is sampled once every
  `metric_sample_s` -- so the clock is the ordered, deduplicated union of the stamps at which the
  executor *acted* (`orders.placed_at`, `order_events.ts`, `intents.created_at`,
  `fills.filled_at`) plus the `exec.loop_ms` samples. It is never a 15 s grid and never the
  loop clock.
* The limitations that stop a thinned clock from reading as a complete one: every run carries
  `loop_spacing_unreconstructable`, and every report prints the resolved instant count beside
  `live_loop_estimate()`.
* `capture_slice()` writes the slice to the hashed NDJSON tree, bounded and batched, through
  §1.1(b)'s read-only reader.

Every statement in this module is bounded by the slice window plus one more predicate, and
carries a comment naming the index it rides.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.experiments.execution_viability.manifest import canonical_json
from harness.experiments.execution_viability.storage import run_dir

log = logging.getLogger("harness.exp")

#: §1.3(a)'s thirteen streams, in the order the addendum lists them.
CAPTURE_STREAMS: tuple[str, ...] = ("books", "deltas", "prints", "fairs", "gaps", "signals",
                                    "intents", "orders", "order_events", "fills", "loop_instants",
                                    "games", "ws_events")

#: §1.3(b). What each stream's own timestamp means to a decision at instant *t*:
#: `event` - the venue's/source's clock only, so availability cannot be read off the row;
#: `availability` - the local receipt/insertion stamp, which is what a decision may read;
#: `both` - the row carries the two separately.
TIMESTAMP_SEMANTICS: dict[str, str] = {
    "books": "both",            # `ts` is the venue's, `raw.received_at`/`fetched_at` ours
    "deltas": "both",           # `ts` venue, `id` the monotone local insertion order
    "prints": "both",           # `ts` venue, `source` says whether it arrived by ws or rest
    "fairs": "availability",    # `fair_values.created_at` is when *we* priced, never the book's
    "gaps": "availability",     # the recorder's own record that it lost frames
    "signals": "availability",
    "intents": "availability",
    "orders": "availability",
    "order_events": "availability",
    "fills": "availability",
    "loop_instants": "availability",
    "games": "event",           # `kickoff_utc` is overwritten in place: event time, and not as-of
    "ws_events": "availability",
}

#: §2's six `exp_limitation` kinds, mirrored here so the writer and this model cannot drift.
LIMITATION_KINDS: tuple[str, ...] = ("loop_spacing_unreconstructable", "kickoff_not_asof",
                                     "availability_unreconstructable", "capture_hash_mismatch",
                                     "overnight_unanchored", "arm_unavailable")

#: How far back a kickoff snapshot may be taken from when the caller names no lower bound. The
#: read must stay bounded (§4.3), and a decision row older than this is not the window's.
KICKOFF_ASOF_LOOKBACK = timedelta(days=7)

#: `capture_slice`'s projected-size arithmetic. NDJSON of one delta row measured against the
#: recorded shape of `orderbook_events`/`venue_trades`; deliberately an over-estimate, because
#: this number exists to refuse a capture *before* the first write (§2's `EXP_CAPTURE_MAX_GB`).
AVG_NDJSON_BYTES = 320

#: §4.3's cooperative yield: how long to stand aside when the executor's own last loop says it
#: is behind, and how many times before the run stops rather than delaying the executor further.
YIELD_SLEEP_S = 5
YIELD_MAX_WAITS = 3


class CaptureRefused(RuntimeError):
    """The capture refused to start or to continue: too large, or the executor is behind."""


@dataclass(frozen=True, slots=True)
class Limitation:
    """One `exp_limitation` row (§2), built here and persisted by T3's writer."""

    run_id: str
    kind: str
    scope: dict
    detail: str
    created_at: datetime

    def __post_init__(self) -> None:
        if self.kind not in LIMITATION_KINDS:
            raise ValueError(f"{self.kind!r} is not one of §2's limitation kinds")
        if self.scope is None:
            raise ValueError("a limitation names the slice, arm or market it applies to")


# --- the replay clock (§1.3d, ruling C1) ------------------------------------------------

#: C1. Five bounded reads, unioned in Python so each keeps its own index. `replay = false`:
#: §0.12 forbids reusing the replay flag, so the clock is the production executor's own stamps.
_ORDER_INSTANTS = text(
    "select id, placed_at as ts from orders "
    "where placed_at >= :start and placed_at <= :end and replay = false")      # ix_orders_key_placed
#: order_events is indexed on the order id first -- `ix_order_events_order_ts (order_id, ts)`
#: (models.py:628) and the covering unique `uq_order_event (order_id, kind, ts)` (schema.py:236),
#: which is the one the planner picks for this projection -- so the bound must ride the order
#: ids the previous statement just returned for this window. A bare `ts between` here is a
#: sequential scan of the table.
_EVENT_INSTANTS = text(
    "select ts from order_events "
    "where order_id = any(:order_ids) and ts >= :start and ts <= :end")   # ix_order_events_order_ts
#: intents' only time index is `ix_intents_created` on (created_at) (models.py:473); the
#: `variant_id` filter is a residual predicate on the rows the range already selected, not a
#: second index term.
_INTENT_INSTANTS = text(
    "select created_at as ts from intents "
    "where created_at >= :start and created_at <= :end "
    "  and variant_id = any(:variant_ids)")                                     # ix_intents_created
_FILL_INSTANTS = text(
    "select filled_at as ts from fills where filled_at >= :start and filled_at <= :end")  # ix_fills_filled_at
_SAMPLE_INSTANTS = text(
    "select ts from metric_samples where name = 'exec.loop_ms' "
    "and ts >= :start and ts <= :end")                    # ix_metric_samples_name_ts (name, ts desc)


def resolve_instants(session: Session, *, warmup_start: datetime, observation_end: datetime,
                     variant_ids: Sequence[str]) -> tuple[datetime, ...]:
    """The replay clock: the retained action instants, sorted and deduplicated (C1).

    Not the loop clock -- that is retained nowhere -- and not a grid. The spacing between these
    instants is *not* the live loop's spacing, which is why every run carries
    `spacing_limitation()` and every report prints `live_loop_estimate()` beside the count.
    """
    bounds = {"start": warmup_start, "end": observation_end}
    orders = session.execute(_ORDER_INSTANTS, bounds).all()
    instants = {row.ts for row in orders}
    order_ids = [row.id for row in orders]
    if order_ids:
        # Only the orders of this window: the id list is the bound, so the events read stays
        # inside the same slice the first statement selected.
        instants |= {row.ts for row in session.execute(
            _EVENT_INSTANTS, bounds | {"order_ids": order_ids}).all()}
    if variant_ids:
        instants |= {row.ts for row in session.execute(
            _INTENT_INSTANTS, bounds | {"variant_ids": list(variant_ids)}).all()}
    instants |= {row.ts for row in session.execute(_FILL_INSTANTS, bounds).all()}
    instants |= {row.ts for row in session.execute(_SAMPLE_INSTANTS, bounds).all()}
    return tuple(sorted(instants))


def sample_instant(ts: datetime, value_ms: float, *, sensitivity: bool = False) -> datetime:
    """The decision instant an `exec.loop_ms` sample marks (ruling I1).

    `_locked_step` takes `now` before the body and `_write_metric_batch` records `ts=now`, so a
    24,165 ms sample at 12:00:00 is a step that **began** at 12:00:00 and took 24 s. The
    `ts - value_ms` reading is a labelled falsifier sensitivity, never the primary clock.
    """
    if not sensitivity:
        return ts
    return ts - timedelta(milliseconds=value_ms)


def live_loop_estimate(sample_count: int, span_s: float, p50_loop_ms: float) -> int:
    """How many times the executor plausibly stepped over `span_s` -- a number printed *beside*
    the resolved instant count, never used as a clock (C1).

    `sample_count` is carried because the estimate is only meaningful for a span the samples
    actually cover: 1,158 samples over 24 h is one per 74.6 s, and at the 24.165 s p95 the loop
    stepped about 3,575 times over the same day.
    """
    if span_s <= 0 or p50_loop_ms <= 0:
        raise ValueError("a loop estimate needs a positive span and a positive loop time")
    if sample_count <= 0:
        raise ValueError("no exec.loop_ms sample covers this span: the estimate is unidentified")
    return int(span_s / (p50_loop_ms / 1000))


def spacing_limitation(run_id: str, *, warmup_start: datetime, observation_end: datetime,
                       instants: int, live_estimate: int, now: datetime) -> Limitation:
    """C1's per-run row: the idle loops between the retained instants are unreconstructable."""
    return Limitation(
        run_id=run_id, kind="loop_spacing_unreconstructable",
        scope={"warmup_start": warmup_start.isoformat(),
               "observation_end": observation_end.isoformat(),
               "instants": instants, "live_loop_estimate": live_estimate},
        detail=("the replay clock is the retained action instants; loops that produced no row "
                "are retained nowhere and cannot be reconstructed, so the spacing between "
                "these instants is not the live loop's spacing"),
        created_at=now)


def kickoff_limitation(run_id: str, *, game_id: int, now: datetime) -> Limitation:
    """Ruling I6/I3: a game with no frozen kickoff snapshot is labelled, never filled in."""
    return Limitation(
        run_id=run_id, kind="kickoff_not_asof", scope={"game_id": game_id},
        detail=("no intent or order inside the window froze this game's kickoff, and "
                "games.kickoff_utc is overwritten in place, so the as-of value cannot be "
                "reconstructed; the game is excluded from the regime-sensitive rows"),
        created_at=now)


# --- as-of kickoffs (§1.3a, rulings I3/I6) -----------------------------------------------

#: The kickoff **as the decision saw it**. `games.kickoff_utc` is updated in place by the linker,
#: so it is today's schedule; the retained as-of values are the snapshots the executor froze on
#: its own decision rows, `intents.kickoff_utc` (models.py:455) and `orders.kickoff_utc`
#: (models.py:521), neither of which is ever rewritten.
_KICKOFF_ASOF = text("""
    select distinct on (snap.game_id) snap.game_id, snap.kickoff_utc, g.sport, g.status,
           g.espn_event_id
      from (
        select game_id, kickoff_utc, created_at as ts from intents
         where created_at > :since and created_at <= :at            -- ix_intents_created
        union all
        select game_id, kickoff_utc, placed_at as ts from orders
         where placed_at > :since and placed_at <= :at              -- ix_orders_key_placed
      ) snap
      join games g on g.id = snap.game_id                           -- games primary key
     where snap.game_id is not null and snap.kickoff_utc is not null and g.sport = :sport
     order by snap.game_id, snap.ts desc
""")


def kickoffs_asof(session: Session, *, at: datetime, sport: str,
                  since: datetime | None = None) -> list:
    """The kickoff list `interval_for` may be handed at `at` (rulings I3, I6, I7).

    Reads **neither** `games.kickoff_utc` nor any history table. Returns `Kickoff` rows
    (`harness/feeds/espn.py:19`) because `interval_for(sport, now, kickoffs, tz)` takes that type
    and nothing else; of its six fields that function reads only `sport` and `kickoff_utc`, so
    `home`/`away` are left empty rather than reconstructed from names that are not on the
    decision's own row. A game with no snapshot row is absent from this list and is the caller's
    `kickoff_limitation`, never filled in from `games`.
    """
    from harness.feeds.espn import Kickoff          # function scope: no import-time model graph

    lower = since if since is not None else at - KICKOFF_ASOF_LOOKBACK
    rows = session.execute(_KICKOFF_ASOF, {"since": lower, "at": at, "sport": sport}).all()
    return sorted((Kickoff(sport=row.sport, espn_event_id=str(row.espn_event_id or ""),
                           kickoff_utc=row.kickoff_utc, home="", away="", status=row.status)
                   for row in rows), key=lambda k: (k.kickoff_utc, k.espn_event_id))


def visible_at(row: dict, instant: datetime) -> bool:
    """§1.3(b): may a decision at `instant` read this row?

    The availability stamp decides, never the event time: a REST backfill taped at 12:05 is not
    an input to a 12:00 decision even though its event time is 11:58. A row whose availability
    cannot be reconstructed is **not** silently admitted -- it is invisible, and the caller
    records `availability_unreconstructable`.
    """
    available_at = row.get("available_at")
    if available_at is None:
        return False
    return available_at <= instant


# --- the capture (§1.3a) -------------------------------------------------------------------

#: One bounded statement per stream. Each is bounded by the slice window plus a ticker, id or
#: name predicate, and the comment names the index it rides.
_STREAM_SQL: dict[str, str] = {
    # ix_obe_snapshot (ticker, ts desc) where kind = 'snapshot'
    "books": ("select id, ticker, ts, sid, seq, raw from orderbook_events "
              "where ticker = any(:tickers) and kind = 'snapshot' "
              "and ts >= :start and ts <= :end order by ts, id"),
    # ix_obe_ticker_ts (ticker, ts)
    "deltas": ("select id, ticker, ts, sid, seq, side, price, delta from orderbook_events "
               "where ticker = any(:tickers) and kind = 'delta' "
               "and ts >= :start and ts <= :end order by ts, id"),
    # ix_trades_ticker_ts (ticker, ts)
    "prints": ("select venue, trade_id, ticker, ts, yes_price, count, taker_side, "
               "taker_outcome_side, is_block, source from venue_trades "
               "where ticker = any(:tickers) and ts >= :start and ts <= :end order by ts"),
    # ix_gap_market_created (venue_market_id, created_at); `fair_ts` is the fair value's own
    # created_at, never the snapshot's (store.py's note).
    "fairs": ("select s.id, s.venue_market_id, s.created_at, s.fair_p, s.fair_value_id, "
              "s.staleness_s, s.stale_allowance_s, s.feed_kind, s.best_bid, s.best_ask, "
              "s.venue_mid, fv.created_at as fair_ts from market_gap_snapshots s "
              "left join fair_values fv on fv.id = s.fair_value_id "
              "where s.venue_market_id = any(:market_ids) "
              "and s.created_at >= :start and s.created_at <= :end order by s.created_at, s.id"),
    # ix_obe_gap (sid, id) where kind = 'gap'. Every gap row carries `ticker = ''` (a gap is per
    # subscription and dirties every ticker on it, §0.12), so this rides the sids of the slice's
    # own snapshots and never filters on ticker.
    "gaps": ("select id, sid, seq, ts, raw from orderbook_events "
             "where kind = 'gap' and sid = any(:sids) and ts >= :start and ts <= :end "
             "order by ts, id"),
    # ix_signal_variant_created (variant_id, created_at)
    "signals": ("select id, run_id, variant_id, venue_market_id, side, fair_p, price_target, "
                "edge, edge_min, stake, contracts, decision, rejection_reason, created_at "
                "from signals where variant_id = any(:variant_ids) "
                "and created_at >= :start and created_at <= :end order by created_at, id"),
    # ix_intents_created (created_at)
    "intents": ("select id, signal_id, variant_id, venue_market_id, ticker, side, target_prob, "
                "target_contracts, edge, edge_min, fair_p, game_id, kickoff_utc, stake, "
                "signal_created_at, created_at from intents "
                "where created_at >= :start and created_at <= :end "
                "and variant_id = any(:variant_ids) order by created_at, id"),
    # ix_orders_key_placed (variant_id, venue_market_id, side, placed_at)
    "orders": ("select id, intent_id, variant_id, ticker, venue_market_id, side, prob, "
               "contracts, filled_contracts, status, placed_at, expiry, cancelled_at, "
               "cancel_reason, queue_ahead_at_place, kickoff_utc, game_id, sport, config_hash "
               "from orders where placed_at >= :start and placed_at <= :end "
               "and replay = false and variant_id = any(:variant_ids) order by placed_at, id"),
    # ix_order_events_order_ts (order_id, ts): bounded by the ids the orders stream just wrote.
    "order_events": ("select id, order_id, ts, kind, reason, prob, contracts "
                     "from order_events where order_id = any(:order_ids) "
                     "and ts >= :start and ts <= :end order by order_id, ts"),
    # ix_fills_filled_at (filled_at)
    "fills": ("select id, order_id, prob, contracts, fee, filled_at, fill_method, "
              "source_trade_id, source_event_id, taker_side, through, tape_source "
              "from fills where filled_at >= :start and filled_at <= :end order by filled_at, id"),
    # ix_metric_samples_name_ts (name, ts desc). C1: these are samples, not loops.
    "loop_instants": ("select id, ts, value, labels from metric_samples "
                      "where name = 'exec.loop_ms' and ts >= :start and ts <= :end order by ts"),
    # games primary key. Captured as the schedule *stands today* (I6): the as-of kickoff a
    # decision saw is `kickoffs_asof`, off the frozen snapshots, never this stream.
    "games": ("select id, sport, home_team_id, away_team_id, kickoff_utc, espn_event_id, status "
              "from games where id = any(:game_ids)"),
    # ix_operator_events_ts (ts desc)
    "ws_events": ("select id, ts, kind, summary, ref from operator_events "
                  "where kind like 'ws%' and ts >= :start and ts <= :end order by ts"),
}

_HEARTBEAT = text("select last_loop_ms from exec_heartbeat where id = 1")   # one row, by pk

#: The two streams that dominate the capture's size (§2's disk-cost note): the projection is
#: taken from their counts alone, and deliberately before the first byte is written.
_COUNT_SQL = {
    "deltas": ("select count(*) from orderbook_events where ticker = any(:tickers) "
               "and kind = 'delta' and ts >= :start and ts <= :end"),   # ix_obe_ticker_ts
    "prints": ("select count(*) from venue_trades where ticker = any(:tickers) "
               "and ts >= :start and ts <= :end"),                      # ix_trades_ticker_ts
}


def _yield_if_executor_busy(session: Session, s: Settings) -> bool:
    """§4.3: a run that trips the guard checkpoints and stops rather than delaying the executor.

    `exec_heartbeat` is a single row (id = 1) with no `replay` column: it is the live executor's
    own last loop, which is exactly the process this capture must not slow down.
    """
    last = session.execute(_HEARTBEAT).scalar()
    return last is not None and last > 3 * s.exec_period_s * 1000


def _json_default(value):
    from decimal import Decimal
    from uuid import UUID

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not a capture-serialisable value")


def _ndjson(rows) -> bytes:
    return b"".join(json.dumps(dict(row._mapping), default=_json_default,
                               sort_keys=True).encode() + b"\n" for row in rows)


def projected_bytes(session: Session, *, tickers: Sequence[str], warmup_start: datetime,
                    observation_end: datetime) -> int:
    """The capture's projected size, from the two dominant streams' bounded counts."""
    bounds = {"tickers": list(tickers), "start": warmup_start, "end": observation_end}
    rows = sum(session.execute(text(sql), bounds).scalar() or 0 for sql in _COUNT_SQL.values())
    return rows * AVG_NDJSON_BYTES


def capture_slice(s: Settings, session: Session, *, run_id: str, warmup_start: datetime,
                  observation_end: datetime, tickers: Sequence[str],
                  variant_ids: Sequence[str]) -> tuple[dict[str, str], list[Limitation]]:
    """Write the slice to `/srv/sports-harness/exp/<run_id>/` and return `(hashes, limitations)`.

    One NDJSON file per stream, each hashed with sha256 for the manifest. The size ceiling is
    enforced **before the first write**, every stream is walked in batches of `exp_batch_rows`
    on a server-side cursor, and the executor's own heartbeat is re-read between batches.
    """
    if observation_end <= warmup_start:
        raise ValueError("observation_end must be after warmup_start")
    ceiling = s.exp_capture_max_gb * 1024 ** 3
    projected = projected_bytes(session, tickers=tickers, warmup_start=warmup_start,
                                observation_end=observation_end)
    if projected > ceiling:
        raise CaptureRefused(
            f"projected capture {projected / 1024 ** 3:.1f} GB exceeds exp_capture_max_gb="
            f"{s.exp_capture_max_gb}; narrow the slice (§2). Nothing was written.")
    now = datetime.now(tz=warmup_start.tzinfo)
    limitations = [spacing_limitation(
        run_id, warmup_start=warmup_start, observation_end=observation_end, instants=0,
        live_estimate=0, now=now)]
    directory = run_dir(s, run_id)
    bounds = {"start": warmup_start, "end": observation_end, "tickers": list(tickers),
              "variant_ids": list(variant_ids)}
    # `fairs` is bounded by the slice's market ids, and it is written before the `orders` and
    # `intents` streams that would otherwise supply them, so the ids are resolved up front from
    # the tickers the caller named (`venue_markets.ticker` is unique, so this is a key lookup).
    market_ids = [row.id for row in session.execute(
        text("select id from venue_markets where ticker = any(:tickers)"),
        {"tickers": list(tickers)}).all()]
    context: dict[str, list] = {"sids": [], "order_ids": [], "game_ids": [],
                                "market_ids": sorted(market_ids)}
    hashes: dict[str, str] = {}
    for stream in CAPTURE_STREAMS:
        params = bounds | {key: context[key] for key in
                           ("sids", "order_ids", "game_ids", "market_ids")}
        digest = hashlib.sha256()
        path = directory / f"{stream}.ndjson"
        with path.open("wb") as handle:
            for rows in _batches(session, s, _STREAM_SQL[stream], params):
                body = _ndjson(rows)
                digest.update(body)
                handle.write(body)
                _collect(stream, rows, context)
        hashes[stream] = digest.hexdigest()
        log.info("exp capture stream=%s sha256=%s", stream, hashes[stream][:12])
    return hashes, limitations


def _batches(session: Session, s: Settings, sql: str, params: dict) -> Iterator[list]:
    """Stream one statement in `exp_batch_rows` batches, standing aside for the executor."""
    result = session.execute(text(sql), params,
                             execution_options={"stream_results": True,
                                                "max_row_buffer": s.exp_batch_rows})
    waits = 0
    for partition in result.partitions(s.exp_batch_rows):
        yield partition
        while _yield_if_executor_busy(session, s):
            waits += 1
            if waits > YIELD_MAX_WAITS:
                raise CaptureRefused(
                    "the executor's last loop is more than three periods long; the capture "
                    "stops here rather than delaying it further (§4.3)")
            time.sleep(YIELD_SLEEP_S)


def _collect(stream: str, rows: Sequence, context: dict[str, list]) -> None:
    """Carry the ids one stream selected into the streams whose bound is those ids."""
    if stream == "books":
        context["sids"] = sorted({row.sid for row in rows} | set(context["sids"]))
    elif stream == "orders":
        context["order_ids"] = sorted({row.id for row in rows} | set(context["order_ids"]))
        context["game_ids"] = sorted({row.game_id for row in rows if row.game_id is not None}
                                     | set(context["game_ids"]))
        context["market_ids"] = sorted({row.venue_market_id for row in rows}
                                       | set(context["market_ids"]))
    elif stream == "intents":
        context["game_ids"] = sorted({row.game_id for row in rows if row.game_id is not None}
                                     | set(context["game_ids"]))
        context["market_ids"] = sorted({row.venue_market_id for row in rows}
                                       | set(context["market_ids"]))


def capture_manifest_entries(hashes: dict[str, str]) -> str:
    """The canonical JSON of the stream hashes, as it enters the manifest (§1.2)."""
    return canonical_json({"streams": dict(sorted(hashes.items())),
                           "timestamp_semantics": TIMESTAMP_SEMANTICS})


#: The samples the estimate is computed from, bounded by the window on
#: `ix_metric_samples_name_ts (name, ts desc)`. `percentile_disc` over the same bounded set:
#: the p50 is the loop time the estimate divides the span by, never a wall-clock guess.
_LOOP_SAMPLE_STATS = text(
    "select count(*) as n, percentile_disc(0.5) within group (order by value) as p50 "
    "from metric_samples where name = 'exec.loop_ms' and ts >= :start and ts <= :end")


def loop_sample_stats(session: Session, *, warmup_start: datetime,
                      observation_end: datetime) -> tuple[int, float]:
    """`(sample_count, p50_loop_ms)` for the slice: the two inputs `live_loop_estimate` takes."""
    row = session.execute(_LOOP_SAMPLE_STATS,
                          {"start": warmup_start, "end": observation_end}).one()
    return int(row.n or 0), float(row.p50 or 0)
