"""`harness export-fixture`: dumps a slice of the record as JSON, so a day can be replayed
away from the NAS.

Two shapes, both read-only and both plain JSON documents rather than a database dump, because
what they are for is a test fixture that a reviewer can open:

* **`day`** -- the `raw_responses` of a run range plus every `orderbook_events` and
  `venue_trades` row inside the runs' own window. Those three tables are the whole input to a
  replay: everything else (`games`, `venue_markets`, `odds_snapshots`, `venue_quotes`,
  `fair_values`, `market_gap_snapshots`, `signals`) is rebuilt from them by `reprocess` and
  `price-once`, and a fixture that carried the derived rows too would freeze the very
  normalisation a replay is supposed to re-run. The runs themselves ride along under `meta`:
  they are the range's definition, not a fourth table, and a loader needs their clocks to know
  where the grid starts and stops.
* **`ws-tape`** -- one ticker's anchoring snapshot, its deltas and its prints over a window,
  which is the shape `tests/fixtures/tape_sample_lou_miss_2026-09-07T03.json` already carries
  and the fill tests already read.

Every timestamp is written as an ISO-8601 string in UTC and every `Decimal` as a JSON number,
so a fixture diffs readably and reloads without a custom decoder.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

#: The window of tape a `day` export carries around its runs. The recorder's own rows are
#: stamped by the venue's clock, which runs a little ahead of and behind a run's boundaries, and
#: the executor grid keeps stepping to the last run's pricing clock -- so the window is padded
#: rather than clipped exactly to `[first started_at, last finished_at]`.
PAD_S = 300


class _Encoder(json.JSONEncoder):
    """`Decimal` as a JSON number and `datetime` as ISO-8601 UTC; everything else is already
    JSON (the bodies are `jsonb` and come back as dicts and lists)."""

    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            return o.astimezone(timezone.utc).isoformat()
        return super().default(o)


_RUNS = text("""
select id, started_at, finished_at, status
from runs where id >= :a and id <= :b order by id
""")

_RAW = text("""
select id, fetched_at, run_id, source, endpoint, params, http_status, body
from raw_responses where run_id >= :a and run_id <= :b order by id
""")

_EVENTS = text("""
select id, ticker, ts, sid, seq, kind, side, price, delta, raw
from orderbook_events where ts >= :lower and ts <= :upper order by ts, id
""")

_TRADES = text("""
select venue, trade_id, ticker, ts, yes_price, count, taker_side, taker_outcome_side,
       taker_book_side, is_block, source
from venue_trades where ts >= :lower and ts <= :upper order by ts, trade_id
""")


def _rows(session: Session, stmt, params) -> list[dict]:
    return [dict(row._mapping) for row in session.execute(stmt, params).all()]


def export_day(session: Session, from_run: int, to_run: int, tick_budget_s: float) -> dict:
    """The three recorded tables of `[from_run, to_run]`, plus the runs that define the range.

    The tape window is the first run's start to the last run's pricing clock, padded by
    `PAD_S` at both ends: the executor's fill simulation looks back 60 s from an order's
    placement for prints, and a book anchors on the newest snapshot at or before the instant it
    is asked about, so a window clipped exactly to the runs would export a range that cannot
    rebuild its own first book.
    """
    if from_run > to_run:
        raise ValueError(f"--from-run {from_run} is after --to-run {to_run}")
    runs = _rows(session, _RUNS, {"a": from_run, "b": to_run})
    if not runs:
        raise ValueError(f"no runs in [{from_run}, {to_run}]")
    pad = timedelta(seconds=PAD_S)
    budget = timedelta(seconds=tick_budget_s)
    starts = [r["started_at"] for r in runs]
    ends = [r["finished_at"] or (r["started_at"] + budget) for r in runs]
    lower, upper = min(starts) - pad, max(ends) + pad
    window = {"lower": lower, "upper": upper}
    doc = {
        "kind": "day",
        "exported_at": datetime.now(timezone.utc),
        "meta": {"from_run": from_run, "to_run": to_run, "window": window, "runs": runs},
        "raw_responses": _rows(session, _RAW, {"a": from_run, "b": to_run}),
        "orderbook_events": _rows(session, _EVENTS, {"lower": lower, "upper": upper}),
        "venue_trades": _rows(session, _TRADES, {"lower": lower, "upper": upper}),
    }
    doc["counts"] = {k: len(doc[k])
                     for k in ("raw_responses", "orderbook_events", "venue_trades")}
    return doc


_WS_SNAPSHOT = text("""
select id, ts, sid, seq, raw from orderbook_events
where ticker = :t and kind = 'snapshot' and ts <= :upper order by ts desc, id desc limit 1
""")

_WS_DELTAS = text("""
select id, ts, sid, seq, side, price, delta from orderbook_events
where ticker = :t and kind = 'delta' and ts >= :lower and ts <= :upper order by ts, id
""")

_WS_PRINTS = text("""
select trade_id, ts, yes_price, count,
       coalesce(taker_outcome_side, taker_side) as taker_side, is_block, source
from venue_trades where ticker = :t and ts >= :lower and ts <= :upper order by ts, trade_id
""")


def export_ws_tape(session: Session, ticker: str, lower: datetime, upper: datetime) -> dict:
    """One ticker's snapshot, deltas and prints over `[lower, upper]` (the Task 4 tape shape).

    The snapshot is the newest one at or before `upper`, which is what a book anchors on; as in
    the shipped sample, the deltas between it and the window are not carried, so the file
    documents its own limits rather than pretending to rebuild the exact book at `lower`.
    """
    if lower > upper:
        raise ValueError(f"--from {lower.isoformat()} is after --to {upper.isoformat()}")
    params = {"t": ticker, "lower": lower, "upper": upper}
    snapshot = session.execute(_WS_SNAPSHOT, {"t": ticker, "upper": upper}).first()
    deltas = _rows(session, _WS_DELTAS, params)
    prints = _rows(session, _WS_PRINTS, params)
    return {
        "kind": "ws-tape",
        "ticker": ticker,
        "exported_at": datetime.now(timezone.utc),
        "window": {"from": lower, "to": upper},
        "snapshot": None if snapshot is None else dict(snapshot._mapping),
        "deltas": deltas,
        "prints": prints,
        "counts": {"deltas": len(deltas), "prints": len(prints)},
    }


def write_export(doc: dict, out: str) -> None:
    """`-` writes the document to stdout; anything else is a path, created if need be."""
    body = json.dumps(doc, cls=_Encoder, indent=1, sort_keys=False)
    if out == "-":
        sys.stdout.write(body + "\n")
        return
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "\n")
