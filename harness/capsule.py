"""`harness capsule`: a bounded, indexed extraction of one order's or one period's record.

This is never an audit scan. Every statement below is bounded by an id list or a time window,
every one names the index it rides or says in a comment that its table is small enough to walk,
every one carries `limit :cap`, and the session runs under a 60 s `statement_timeout`. A file
that reaches its cap is written, marked `truncated` in the manifest with the last integer id it
took where the table has one (`venue_trades` keys on a string `trade_id` and carries none), and
makes the command exit 2, so the controller narrows the window instead of receiving a slice
that silently stops.

The output is files, not a database copy (D1): one gzipped JSON-lines file per table plus a
`manifest.json` carrying the build, the extraction instant, the SQL text of every query, the
row counts, a sha256 per file, the limits hit and the slices that cannot be verified. `--out -`
writes the whole directory as a tar stream on stdout, which is how the controller pulls one
over ssh.
"""

import gzip
import hashlib
import io
import json
import sys
import tarfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.fixtures import _Encoder as _FixtureEncoder
from harness.fixtures import _WS_PRINTS, _capped, _rows, export_ws_tape


class _Encoder(_FixtureEncoder):
    """`_fixtures._Encoder` plus `uuid.UUID` as its string form.

    `intents.id` is a UUID primary key (`harness/db/models.py:392`-ish), and every order
    capsule's `intents` slice carries it; the fixtures encoder never needed this because none of
    `export_day`'s or `export_ws_tape`'s columns are UUIDs.
    """

    def default(self, o):
        if isinstance(o, uuid.UUID):
            return str(o)
        return super().default(o)

#: Rows per file. Also the memory bound: `_rows` materializes before anything is written
#: (review I-f), so this number is both the truncation point and the ceiling on one slice's
#: footprint in the container.
CAPSULE_ROW_CAP = 150_000
#: Per-statement ceiling on the NAS (D2). One capsule is six one-ticker windows plus small-table
#: reads; a statement that outlives this is a window that needs narrowing, not more patience.
CAPSULE_STATEMENT_TIMEOUT_MS = 60_000


@dataclass(frozen=True)
class Slice:
    """One table's rows for one capsule, with the statement that produced them."""

    table: str
    rows: list[dict]
    sql: str
    index_note: str
    truncated: bool
    last_id: int | None


#: The tape a capsule carries around an order: the window is padded at both ends because a book
#: anchors on the newest snapshot at or before the instant it is asked about and the simulator
#: looks back for prints (the same reason `harness.fixtures.PAD_S` exists).
ORDER_TAPE_PAD_MIN = 30


def _slice(session: Session, table: str, stmt, params: dict, index_note: str,
           cap: int) -> Slice:
    """One bounded read, capped, with the statement and the index recorded beside its rows.

    `last_id` is `int | None` (the `Slice` contract): most capsule tables key on an integer
    `id`, but `intents.id` is a UUID, and `Slice.last_id` exists so the controller can resume a
    truncated read from a bookmark, which a lookup-by-known-id table like `intents` never is.
    Capturing the UUID anyway would also break the manifest's own JSON round-trip, since the
    written file stringifies it while the in-memory value stays a `UUID`.
    """
    rows = _rows(session, stmt, dict(params, cap=cap))
    truncated = len(rows) >= cap
    last = rows[-1].get("id") if rows and isinstance(rows[-1].get("id"), int) else None
    return Slice(table=table, rows=rows, sql=" ".join(stmt.text.split()),
                 index_note=index_note, truncated=truncated, last_id=last)


_ORDER = text("select * from orders where id = :order_id limit :cap")
_ORDER_EVENTS = text("""
select * from order_events where order_id = :order_id order by ts, id limit :cap
""")
_ORDER_FILLS = text("""
select * from fills where order_id = :order_id order by filled_at, id limit :cap
""")
_ORDER_WATCH = text("""
select * from order_watch_samples where order_id = :order_id order by ts limit :cap
""")
_ORDER_LEDGER = text("""
select * from ledger where order_id = :order_id order by ts, id limit :cap
""")
_INTENT = text("select * from intents where id = :intent_id limit :cap")
_SIGNAL = text("select * from signals where id = :signal_id limit :cap")
_GAP_BY_ID = text("select * from market_gap_snapshots where id = :gap_id limit :cap")
_FAIR_BY_ID = text("select * from fair_values where id = :fair_id limit :cap")
_MARKET_BY_ID = text("select * from venue_markets where id = :vm_id limit :cap")
#: `config_history.config_hash` is keyed by the *strategy* id: `harness/strategy/variants.py:213`
#: inserts `config_hash=variant.variant_id`, a 12-hex value. `orders.config_hash` is a different
#: thing -- the executor's 64-hex sha256 of `{variant_id, ExecSettings, EXECUTOR_VERSION}`
#: (`harness/execution/plan.py:118`) -- and no table resolves it, so the order capsule carries it
#: on the order row itself and looks this row up by `variant_id`. Binding the order's
#: `config_hash` here would match nothing, silently, for every capsule ever taken.
_CONFIG_ROW = text("select * from config_history where config_hash = :variant_id limit :cap")


def order_slices(session: Session, order_id: int, *,
                 cap: int = CAPSULE_ROW_CAP) -> list[Slice]:
    """Everything the record holds about one order, by primary key and by its own id.

    The chain is order -> intent -> signal -> gap snapshot -> fair value, each a primary-key
    read, plus the order's own events, fills, watch samples and ledger rows. None of these needs
    a time bound: an order id is already the narrowest bound there is.
    """
    order = _slice(session, "orders", _ORDER, {"order_id": order_id},
                   "orders.pkey (primary key read)", cap)
    if not order.rows:
        raise ValueError(f"order {order_id} not found")
    row = order.rows[0]
    out = [order]
    out.append(_slice(session, "order_events", _ORDER_EVENTS, {"order_id": order_id},
                      "uq_order_event (order_id, kind, ts)", cap))
    out.append(_slice(session, "fills", _ORDER_FILLS, {"order_id": order_id},
                      "ix_fills_order (order_id)", cap))
    out.append(_slice(session, "order_watch_samples", _ORDER_WATCH, {"order_id": order_id},
                      "order_watch_samples.pkey (order_id, ts)", cap))
    # `ledger` has no index on order_id (only uq_ledger_fill on (fill_id, kind)): this is a
    # sequential scan of a small table, stated as such rather than claiming an index it does not
    # have (review I-a2).
    out.append(_slice(session, "ledger", _ORDER_LEDGER, {"order_id": order_id},
                      "no index on ledger.order_id: sequential scan, small table", cap))
    out.append(_slice(session, "intents", _INTENT, {"intent_id": row["intent_id"]},
                      "intents.pkey (primary key read)", cap))
    intent = out[-1].rows[0] if out[-1].rows else None
    signal_id = None if intent is None else intent.get("signal_id")
    signals = _slice(session, "signals", _SIGNAL, {"signal_id": signal_id},
                     "signals.pkey (primary key read)", cap) if signal_id else \
        Slice("signals", [], _SIGNAL.text, "signals.pkey (primary key read)", False, None)
    out.append(signals)
    gap_id = signals.rows[0].get("gap_snapshot_id") if signals.rows else None
    gaps = _slice(session, "market_gap_snapshots", _GAP_BY_ID, {"gap_id": gap_id},
                  "market_gap_snapshots.pkey (primary key read)", cap) if gap_id else \
        Slice("market_gap_snapshots", [], _GAP_BY_ID.text,
              "market_gap_snapshots.pkey (primary key read)", False, None)
    out.append(gaps)
    fair_id = gaps.rows[0].get("fair_value_id") if gaps.rows else None
    out.append(_slice(session, "fair_values", _FAIR_BY_ID, {"fair_id": fair_id},
                      "fair_values.pkey (primary key read)", cap) if fair_id else
               Slice("fair_values", [], _FAIR_BY_ID.text,
                     "fair_values.pkey (primary key read)", False, None))
    out.append(_slice(session, "venue_markets", _MARKET_BY_ID,
                      {"vm_id": row["venue_market_id"]},
                      "venue_markets.pkey (primary key read)", cap))
    out.append(_slice(session, "config_history", _CONFIG_ROW,
                      {"variant_id": row["variant_id"]},
                      "config_history.pkey (primary key read, keyed by the 12-hex variant id; "
                      "the order's own 64-hex executor config_hash rides on the orders row)",
                      cap))
    return out


def order_window(order_row: dict, fill_rows: list[dict]) -> tuple[datetime, datetime]:
    """The tape window of an order capsule: 30 min either side of its life (addendum §0.1)."""
    from datetime import timedelta

    pad = timedelta(minutes=ORDER_TAPE_PAD_MIN)
    ends = [order_row["placed_at"]]
    ends += [f["filled_at"] for f in fill_rows if f.get("filled_at")]
    if order_row.get("cancelled_at"):
        ends.append(order_row["cancelled_at"])
    return order_row["placed_at"] - pad, max(ends) + pad


#: The metric names a capsule carries. An explicit list, never `like 'exec.%'`:
#: `ix_metric_samples_name_ts` is a default-collation btree, so a LIKE prefix is not
#: index-usable and the read degrades to a sequential scan of a table written every 15 s since
#: phase 3 (review I-a1).
CAPSULE_METRIC_NAMES = (
    "exec.loop_ms", "exec.open_orders", "exec.loops_skipped", "exec.dirty_markets",
    "exec.tape_lag_tickers", "exec.tape_batch_min", "exec.intents_considered",
    "exec.placed", "exec.skipped", "ws.gaps", "db.rfqs_pruned", "db.brin_ranges_summarized",
)

_GAPS = text("""
select id, ts, sid, seq, ticker, raw from orderbook_events
where kind = 'gap' and ts >= :lower and ts <= :upper order by ts, id limit :cap
""")
_MARKET_BY_TICKER = text("select * from venue_markets where ticker = :t limit :cap")
_GAPS_BY_MARKET = text("""
select * from market_gap_snapshots where venue_market_id = :vm_id
  and created_at >= :lower and created_at <= :upper order by created_at, id limit :cap
""")
_FAIR_BY_GAME = text("""
select * from fair_values where game_id = :game_id and market_type = :market_type
  and created_at >= :lower and created_at <= :upper order by created_at, id limit :cap
""")
_WINDOW_ORDERS = text("""
select * from orders where placed_at >= :lower and placed_at <= :upper
order by id limit :cap
""")
_WINDOW_FILLS = text("""
select * from fills where filled_at >= :lower and filled_at <= :upper
order by filled_at, id limit :cap
""")
_EVENTS_BY_ORDERS = text("""
select * from order_events where order_id = any(:order_ids) order by ts, id limit :cap
""")
_LEDGER_BY_ORDERS = text("""
select * from ledger where order_id = any(:order_ids) order by ts, id limit :cap
""")
_METRICS = text("""
select * from metric_samples where name = any(:names) and ts >= :lower and ts <= :upper
order by ts, name limit :cap
""")
_OPERATOR_EVENTS = text("""
select * from operator_events where ts >= :lower and ts <= :upper order by ts, id limit :cap
""")
_HEARTBEAT = text("select * from exec_heartbeat limit :cap")
_CONFIG_ALL = text("select * from config_history order by first_seen limit :cap")


def period_slices(session: Session, tickers: list[str], lower: datetime, upper: datetime, *,
                  cap: int = CAPSULE_ROW_CAP) -> list[Slice]:
    """One named period: the tape of each ticker, the window's orders, and the settings."""
    if lower > upper:
        raise ValueError(f"--from {lower.isoformat()} is after --to {upper.isoformat()}")
    if not tickers:
        raise ValueError("--period needs at least one --ticker")
    out: list[Slice] = []
    events: list[dict] = []
    prints: list[dict] = []
    # Truncation is per read, not per merged file: with several tickers feeding one list,
    # `len(rows) >= cap` on the total would call a file capped that no statement capped, and
    # would miss a capped ticker once another ticker's rows are removed by dedup.
    deltas_capped = prints_capped = False
    for ticker in tickers:
        # `export_ws_tape` is reused unchanged: its predicates are the indexed ones the executor
        # itself uses -- ix_obe_ticker_ts for the deltas, ix_obe_snapshot for the anchor (now
        # bounded below by ANCHOR_LOOKBACK), ix_trades_ticker_ts for the prints. Its prints are
        # kept and become the `venue_trades` slice: a second `select *` over the same ticker and
        # window would double the trades read in the one quiet window the extraction gets, and
        # this projection is the one the fill simulator and the shipped tape fixture consume.
        # `cap` reaches both reads: without it neither carries a row ceiling, and the ceiling is
        # also the memory bound, because `_rows` materializes before anything is written.
        tape = export_ws_tape(session, ticker, lower, upper, cap=cap)
        for delta in tape["deltas"]:
            events.append(dict(delta, ticker=ticker, kind="delta"))
        if tape["snapshot"] is not None:
            events.append(dict(tape["snapshot"], ticker=ticker, kind="snapshot"))
        prints.extend(dict(row, ticker=ticker) for row in tape["prints"])
        deltas_capped = deltas_capped or tape["truncated"]["deltas"]
        prints_capped = prints_capped or tape["truncated"]["prints"]
    # The bookmark is the highest delta id actually taken, never a snapshot: `events` is built
    # deltas-then-snapshot per ticker, so `events[-1]` is always a snapshot -- usually the
    # *oldest* row in the slice, since a book anchors on the newest snapshot at or before the
    # window's start. A resume from a snapshot's id would re-read every delta already taken
    # (review I2). One integer also cannot bookmark several tickers' independent truncation
    # points, so it is None whenever more than one ticker fed this file; the `truncated` flag
    # alone then drives the retake.
    delta_ids = [e["id"] for e in events if e["kind"] == "delta"]
    last_delta_id = max(delta_ids) if delta_ids and len(tickers) == 1 else None
    out.append(Slice("orderbook_events", events, _tape_sql(cap),
                     "ix_obe_ticker_ts (ticker, ts) for deltas; ix_obe_snapshot "
                     "(ticker, ts desc) where kind = 'snapshot' for the anchor",
                     deltas_capped, last_delta_id))
    out.append(Slice("venue_trades", prints, " ".join(_capped(_WS_PRINTS, cap).text.split()),
                     "ix_trades_ticker_ts (ticker, ts) -- the _WS_PRINTS projection, not "
                     "select *: trade_id, ts, yes_price, count, taker_side, is_block, source",
                     prints_capped, None))
    out.append(_slice(session, "orderbook_events_gaps", _GAPS, {"lower": lower, "upper": upper},
                      "ix_obe_ts_brin (ts) -- partition-pruned; the gap row is "
                      "subscription-level and carries ticker = '' (review C1)", cap))
    for ticker in tickers:
        market = _slice(session, "venue_markets", _MARKET_BY_TICKER, {"t": ticker},
                        "venue_markets.ticker unique index", cap)
        out.append(market)
        for row in market.rows:
            out.append(_slice(session, "market_gap_snapshots", _GAPS_BY_MARKET,
                              {"vm_id": row["id"], "lower": lower, "upper": upper},
                              "ix_gap_market_created (venue_market_id, created_at)", cap))
            if row.get("game_id") is not None:
                out.append(_slice(session, "fair_values", _FAIR_BY_GAME,
                                  {"game_id": row["game_id"],
                                   "market_type": row["market_type"],
                                   "lower": lower, "upper": upper},
                                  "ix_fair_game_type_created "
                                  "(game_id, market_type, created_at)", cap))
    # `orders` is a small table (8,326 non-replay rows at 13:10 CT on 2026-09-11) with no index
    # a bare `placed_at` range can ride: this is a bounded walk, stated as such rather than
    # naming an index that cannot serve it (review I-a2). The two near misses, for a reader
    # checking: ix_orders_status is (status, replay), and ix_orders_key_placed
    # (`harness/db/schema.py:250`) is (variant_id, venue_market_id, side, placed_at) -- with no
    # equality on its three leading columns, its placed_at range is a filter, not a range scan.
    orders = _slice(session, "orders", _WINDOW_ORDERS, {"lower": lower, "upper": upper},
                    "no index on orders.placed_at: bounded walk, small table", cap)
    out.append(orders)
    order_ids = [r["id"] for r in orders.rows]
    out.append(_slice(session, "fills", _WINDOW_FILLS, {"lower": lower, "upper": upper},
                      "ix_fills_filled_at (filled_at)", cap))
    out.append(_slice(session, "order_events", _EVENTS_BY_ORDERS, {"order_ids": order_ids},
                      "uq_order_event (order_id, kind, ts)", cap))
    out.append(_slice(session, "ledger", _LEDGER_BY_ORDERS, {"order_ids": order_ids},
                      "no index on ledger.order_id: sequential scan, small table", cap))
    out.append(_slice(session, "metric_samples", _METRICS,
                      {"names": list(CAPSULE_METRIC_NAMES), "lower": lower, "upper": upper},
                      "ix_metric_samples_name_ts (name, ts desc) -- explicit name list, "
                      "never a LIKE prefix (review I-a1)", cap))
    out.append(_slice(session, "operator_events", _OPERATOR_EVENTS,
                      {"lower": lower, "upper": upper}, "ix_operator_events_ts (ts desc)", cap))
    out.append(_slice(session, "exec_heartbeat", _HEARTBEAT, {},
                      "single row (id = 1), full read", cap))
    out.append(_slice(session, "config_history", _CONFIG_ALL, {},
                      "small table, full read ordered by first_seen", cap))
    return merge_slices(out)


def _tape_sql(cap: int | None) -> str:
    """The two statements `export_ws_tape` runs, as it runs them, recorded in the manifest.

    The cap is part of the statement, so the manifest shows the ceiling that was in force rather
    than the uncapped text the module happens to declare.
    """
    from harness.fixtures import _WS_DELTAS, _WS_SNAPSHOT

    return " ".join(
        (_WS_SNAPSHOT.text + " ;; " + _capped(_WS_DELTAS, cap).text).split())


#: Each capsule table's primary key, as the row dicts carry it. `merge_slices` dedups on this,
#: because two selectors legitimately return the same row: an order capsule reads its own order
#: by id and then reads the window's orders by `placed_at`, and its own order is inside its own
#: window. A file that carried it twice would have a manifest whose count and sha256 faithfully
#: attest to the duplication, which is the failure the manifest exists to prevent.
_KEYS: dict[str, tuple[str, ...]] = {
    "orders": ("id",),
    "order_events": ("id",),
    "fills": ("id",),
    "ledger": ("id",),
    "intents": ("id",),
    "signals": ("id",),
    "market_gap_snapshots": ("id",),
    "fair_values": ("id",),
    "venue_markets": ("id",),
    "operator_events": ("id",),
    "metric_samples": ("id",),
    "exec_heartbeat": ("id",),
    # `config_history` is keyed by the hash itself (`harness/db/models.py:362`).
    "config_history": ("config_hash",),
    # The two tape tables are range-partitioned weekly, so `ts` is part of the primary key
    # (`models.py:217-219`, `models.py:195-198`). The gap slice is `orderbook_events` rows read
    # by a different statement and keyed the same way.
    "orderbook_events": ("id", "ts"),
    "orderbook_events_gaps": ("id", "ts"),
    # The `venue_trades` slice is `_WS_PRINTS`' projection, which carries `trade_id` and `ts`
    # but not `venue`; one venue writes this tape, so the pair is unique within a capsule.
    "venue_trades": ("trade_id", "ts"),
    "order_watch_samples": ("order_id", "ts"),
}


def _row_key(table: str, row: dict):
    """One row's identity inside its file. Unknown tables fall back to the whole row, which is
    always correct and only ever slower."""
    key = _KEYS.get(table)
    if key is None:
        return json.dumps(row, cls=_Encoder, sort_keys=True)
    return tuple(row.get(name) for name in key)


def merge_slices(slices: list[Slice]) -> list[Slice]:
    """One `Slice` per table name, each row once.

    Several tickers contribute to the same file, and on the order path the two selectors overlap
    on `orders`, `fills`, `order_events`, `ledger` and `venue_markets`. Rows are deduped on the
    table's primary key, first occurrence winning, so the manifest's counts are post-dedup counts
    and `counts["orders"]` on a one-order capsule is 1.

    The SQL and the index note of the first contributor are kept and every other statement is
    appended, so the manifest still shows everything that fed the file.
    """
    order: list[str] = []
    by_table: dict[str, Slice] = {}
    seen: dict[str, set] = {}
    for s in slices:
        if s.table not in by_table:
            order.append(s.table)
            keys = seen.setdefault(s.table, set())
            rows = []
            for row in s.rows:
                key = _row_key(s.table, row)
                if key not in keys:
                    keys.add(key)
                    rows.append(row)
            by_table[s.table] = Slice(s.table, rows, s.sql, s.index_note, s.truncated,
                                      s.last_id)
            continue
        prev = by_table[s.table]
        keys = seen[s.table]
        fresh = []
        for row in s.rows:
            key = _row_key(s.table, row)
            if key not in keys:
                keys.add(key)
                fresh.append(row)
        by_table[s.table] = Slice(
            table=s.table, rows=prev.rows + fresh,
            sql=prev.sql if s.sql in prev.sql else prev.sql + " ;; " + s.sql,
            index_note=prev.index_note,
            # Truncation is a property of a read, not of the merged file: one capped statement
            # means this table is incomplete however many rows the others contributed.
            truncated=prev.truncated or s.truncated,
            last_id=s.last_id if s.last_id is not None else prev.last_id)
    return [by_table[name] for name in order]


def unverifiable(slices: list[Slice], tickers: list[str]) -> list[dict]:
    """The slices 6B cannot replay from this capsule's own tape (addendum §0.2).

    Two reasons. A `gap` row inside the window means a frame of that subscription was lost, so
    every ticker on that `sid` has a hole; the entry carries the `sid` and the `ts` rather than
    a ticker, because the gap is subscription-level. A ticker with no anchoring snapshot in
    `[lower - ANCHOR_LOOKBACK, upper]` has no book to start from. Marked, never dropped.
    """
    by_table = {s.table: s for s in slices}
    out: list[dict] = []
    for row in by_table.get("orderbook_events_gaps", Slice(
            "orderbook_events_gaps", [], "", "", False, None)).rows:
        out.append({"reason": "gap", "sid": row["sid"], "ts": row["ts"],
                    "exposed_by": (row.get("raw") or {}).get("exposed_by")})
    anchored = {r["ticker"] for r in by_table.get("orderbook_events", Slice(
        "orderbook_events", [], "", "", False, None)).rows if r.get("kind") == "snapshot"}
    for ticker in tickers:
        if ticker not in anchored:
            out.append({"reason": "no anchor", "ticker": ticker})
    return out


def _jsonl_gz(rows: list[dict]) -> bytes:
    """One table as gzipped JSON lines, using the fixtures' encoder (Decimal as a JSON number,
    datetime as ISO-8601 UTC), so a capsule diffs readably and reloads without a decoder."""
    buf = io.BytesIO()
    # mtime=0 so two extractions of the same rows produce byte-identical files and the sha256
    # in the manifest identifies the content, not the minute it was written.
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        for row in rows:
            gz.write((json.dumps(row, cls=_Encoder, sort_keys=True) + "\n").encode("utf-8"))
    return buf.getvalue()


def write_capsule(slices: list[Slice], out: str, meta: dict, *,
                  cap: int = CAPSULE_ROW_CAP) -> dict:
    """Write one capsule to a directory, or as a tar stream on stdout when `out` is `-`.

    The manifest lands last and names every file with its row count, its sha256, the SQL that
    produced it, the index it rode and whether it hit the cap.

    `cap` must be the ceiling the reads actually ran under (review I1): the caller passes the
    same value it gave `order_slices`/`period_slices`, and the manifest records that number
    under `row_cap` rather than the module default, so a `--cap 1` capsule's one-row `fills`
    file is not read beside a claimed 150,000-row ceiling.

    Each slice is gzipped, written, and its bytes dropped before the next slice is gzipped
    (review I3): holding every member's gzipped bytes at once, on the tar path as well as the
    directory path, meant peak footprint was every slice's materialized rows *plus* every
    slice's gzipped bytes, hundreds of megabytes for a multi-ticker period capsule at the row
    cap. The manifest still lands last, once every member is on disk (or in the tar) and its
    sha256 known.
    """
    tar = tarfile.open(fileobj=sys.stdout.buffer, mode="w|") if out == "-" else None
    directory = None
    if tar is None:
        directory = Path(out)
        directory.mkdir(parents=True, exist_ok=True)
    files: list[dict] = []
    try:
        for s in slices:
            name = f"{s.table}.jsonl.gz"
            body = _jsonl_gz(s.rows)
            files.append({"name": name, "table": s.table, "rows": len(s.rows),
                          "sha256": hashlib.sha256(body).hexdigest(), "sql": s.sql,
                          "index_note": s.index_note, "truncated": s.truncated,
                          "last_id": s.last_id})
            if tar is not None:
                info = tarfile.TarInfo(name)
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
            else:
                (directory / name).write_bytes(body)
            del body  # one member's bytes at a time; nothing here outlives its own iteration
        manifest = dict(meta)
        manifest.update({
            "kind": "capsule",
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "row_cap": cap,
            "statement_timeout_ms": CAPSULE_STATEMENT_TIMEOUT_MS,
            "files": files,
            "counts": {f["table"]: f["rows"] for f in files},
            "truncated": [f["table"] for f in files if f["truncated"]],
        })
        manifest_body = (json.dumps(manifest, cls=_Encoder, indent=1, sort_keys=True) + "\n"
                        ).encode("utf-8")
        if tar is not None:
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest_body)
            tar.addfile(info, io.BytesIO(manifest_body))
        else:
            (directory / "manifest.json").write_bytes(manifest_body)
    finally:
        if tar is not None:
            tar.close()
    return manifest
