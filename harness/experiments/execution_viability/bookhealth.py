"""§1.7: separating confirmed book inactivity from confirmed data loss, and refusing to guess.

231 of the 238 filled counterfactual orders were dirty only because the ticker's own newest row
was older than `exec_book_max_age_s = 120`, and that flag alone cannot tell "this market did not
trade" from "we lost the feed". `classify` answers it from continuity evidence the tape already
carries, or says `unresolved` and records why.

`classify` is a pure function of one `HealthInput`: every quantity it needs is read once, by the
caller, through T1's read-only reader. Nothing here revises production's book-dirty semantics
(§1.7d) - `market_dirty_intervals` is read by the loop that owns it and is never written, no
eligibility decision is touched, and no clean-fill percentage is recalculated.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from harness.execution.book import BookState, book_at

#: §1.7(c): 0.3 ms per snapshot lookup plus ~233 ms per 4,231-row delta replay (row 86's
#: measurement), so 200 sampled intervals is under 50 s of replay for a whole run.
BOOK_VERIFY_MAX = 200

CLASSIFICATIONS = ("inactive_confirmed", "data_loss_confirmed", "unresolved")

#: Printed with every result (§1.7c). Neither is a hedge: both are properties of a snapshot.
CAVEATS = (
    "a snapshot comparison cannot prove that no intervening change occurred between the two "
    "instants it compares",
    "a snapshot cannot preserve queue priority across a gap: a re-anchored book's queue position "
    "is unknown, not zero",
)


@dataclass(frozen=True, slots=True)
class HealthInput:
    """One ticker's one interval, as the caller read it at `interval_end`.

    `anchor_source`, `anchor_id`, `sid` and `seq_before` describe the anchor in force when the
    interval opened; a REST anchor has no sequence to continue and carries `sid = 0, seq = 0`
    (`BookState`). `reconnect_at` is `book.newest_ws_connect(session, at=interval_end)` and
    `first_gap_ts` is `store.first_gap_ts(session, sid, anchor_id, at=interval_end)`, whose gap
    is **per subscription**, not per ticker.
    """

    ticker: str
    interval_start: datetime
    interval_end: datetime
    anchor_source: str
    anchor_id: int
    sid: int
    seq_before: int
    seq_after: int
    events_in_interval: int
    reconnect_at: datetime | None
    first_gap_ts: datetime | None
    last_event_ts: datetime | None
    reanchored: bool
    prints_in_interval: int


@dataclass(frozen=True, slots=True)
class HealthRow:
    """One classified interval and the evidence the verdict was made on (§2's row)."""

    ticker: str
    interval_start: datetime
    interval_end: datetime
    classification: str
    evidence: dict


def _inside(instant: datetime | None, obs: HealthInput) -> bool:
    """Does `instant` fall in the half-open interval the caller's reads are bounded by?"""
    return instant is not None and obs.interval_start <= instant < obs.interval_end


def _row(obs: HealthInput, classification: str, ev: dict, *, cause: str) -> HealthRow:
    return HealthRow(ticker=obs.ticker, interval_start=obs.interval_start,
                     interval_end=obs.interval_end, classification=classification,
                     evidence={**ev, "cause": cause})


def classify(obs: HealthInput) -> HealthRow:
    ev: dict = {
        "anchor_source": obs.anchor_source, "sid": obs.sid, "anchor_id": obs.anchor_id,
        "events_in_interval": obs.events_in_interval,
        "seq_advance": obs.seq_after - obs.seq_before,
        "reconnect_inside_interval": _inside(obs.reconnect_at, obs),
        "gap_inside_interval": _inside(obs.first_gap_ts, obs),
        "reanchored": obs.reanchored, "prints_in_interval": obs.prints_in_interval,
    }
    # (1) Confirmed data loss: a real missing frame.
    if ev["gap_inside_interval"]:
        return _row(obs, "data_loss_confirmed", ev, cause="gap_row")
    if obs.sid and ev["seq_advance"] > obs.events_in_interval:
        return _row(obs, "data_loss_confirmed", ev, cause="seq_skip")
    if ev["reconnect_inside_interval"]:
        # This ticker's subscription was replaced mid-interval. A healthy global socket is not
        # per-ticker evidence (§1.7's third fixture).
        return _row(obs, "data_loss_confirmed", ev, cause="reconnect_inside_interval")
    # (2) Unknown continuity stays unknown: a REST anchor has no sequence to continue (sid 0,
    #     seq 0, `BookState`), and a re-anchor with prints in the interval cannot be told
    #     apart from a quiet market.
    if not obs.sid or obs.anchor_source != "ws" or obs.reanchored:
        ev["queue_consequence"] = (
            "a re-anchor loses queue priority information: the arm's queue_ahead cannot be "
            "carried across this interval and is recorded as unknown")
        return _row(obs, "unresolved", ev, cause="continuity_unknown")
    # (3) Confirmed inactivity: subscription intact, no gap, the sequence continues exactly.
    if ev["seq_advance"] == obs.events_in_interval:
        return _row(obs, "inactive_confirmed", ev, cause="quiet_and_continuous")
    return _row(obs, "unresolved", ev, cause="seq_inconsistent")


def _sample_key(obs: HealthInput, seed: int) -> str:
    material = f"{seed}|{obs.ticker}|{obs.interval_start.isoformat()}"
    return hashlib.sha256(material.encode()).hexdigest()


def sample_intervals(intervals: Sequence[HealthInput], *, seed: int) -> list[HealthInput]:
    """At most `BOOK_VERIFY_MAX` intervals, the same ones on every run (§1.7c).

    The order is `sha256(seed || ticker || interval_start)`, so the sample frame is a property of
    the seed and of the intervals themselves: a reader with the same slice can re-derive exactly
    which intervals were verified, which a random draw would not give them.
    """
    return sorted(intervals, key=lambda obs: _sample_key(obs, seed))[:BOOK_VERIFY_MAX]


def _snapshot(book: BookState | None) -> dict:
    """The recorded fields of one snapshot; `yes_depth`/`no_depth` count resting price levels."""
    if book is None:
        return {"as_of": None, "source": None, "anchor_id": None, "sid": None, "seq": None,
                "dirty": None, "yes_depth": 0, "no_depth": 0}
    return {"as_of": book.as_of.isoformat(), "source": book.source, "anchor_id": book.anchor_id,
            "sid": book.sid, "seq": book.seq, "dirty": book.dirty,
            "yes_depth": len(book.yes_bids), "no_depth": len(book.no_bids)}


def verify_snapshot(session, ticker: str, instant: datetime) -> dict:
    """One bounded snapshot comparison at a recorded instant (§1.7c).

    `book_at` re-anchors and replays the deltas since the anchor once, which is why this is
    called only for the intervals `sample_intervals` picked and never in a loop over the whole
    slice. Every field is None when nothing anchors the ticker at `instant`, or when the freshest
    thing the tape knew there was older than `book.BOOK_MAX_AGE` - `book_at`'s own F36 gate, which
    is part of the answer rather than an error. Both `CAVEATS` apply to what it returns.
    """
    return _snapshot(book_at(session, ticker, instant))


def summarize(rows: Sequence[HealthRow]) -> dict[str, int]:
    """The three counts of §1.7(d) and nothing else - never a recalculated clean-fill percentage.

    Every classification is present, zero included, so a run with no confirmed loss says so
    instead of leaving the reader to infer it from a missing key.
    """
    counts = dict.fromkeys(CLASSIFICATIONS, 0)
    for row in rows:
        counts[row.classification] += 1
    return counts


def persist(writer, rows: Sequence[HealthRow]) -> int:
    """Hand the classified rows to T1's writer for `exp_book_health` (§2), and return the count.

    The table is T3's. Until that migration exists `writer.table` refuses by name with
    `IsolationError`, which is `--persist`'s fail-closed behaviour and not a defect.
    """
    table = writer.table("exp_book_health")
    return writer.insert(table, [
        {"run_id": writer.run_id, "ticker": row.ticker, "interval_start": row.interval_start,
         "interval_end": row.interval_end, "classification": row.classification,
         "evidence": row.evidence}
        for row in rows])
