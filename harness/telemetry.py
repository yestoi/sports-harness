"""Additive telemetry writers for the dashboard (U6, dashboard design spec §3): metric
samples, operator events and a small in-memory sampling clock.

Ruling 1 binds every caller of this module, not this module itself: telemetry never changes a
decision, every writer is additive, and a telemetry failure must never fail a tick, a step or a
stage. That means callers wrap these calls in their own try/except where that guarantee
matters (the executor step, the recorder tick, the settlement stages); this module raises
whatever the database raises, since swallowing it here would hide the failure from the caller
that actually knows what "continue anyway" means for its own loop.
"""

import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from re import compile as _re_compile
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session

from harness.db.models import MetricSample, OperatorEvent

#: F50: an operator-typed reason (a kill, a `harness note`) keeps only this shape. Shared by
#: `/kill` and `event()` so a kill reason and a note are sanitized by exactly one rule.
_SANITIZE_RE = _re_compile(r"[^\w \-.,:/()]")
#: `operator_events.summary` and `kill_switch.reason` are both this wide.
SUMMARY_MAX = 200


def sanitize_reason(text: str) -> str:
    """Strip everything but word characters, spaces and a short punctuation set, then
    truncate to the shared summary/reason width (F50)."""
    return _SANITIZE_RE.sub("", text or "")[:SUMMARY_MAX]


def _ts(value: datetime | None) -> datetime:
    return value or datetime.now(timezone.utc)


def record(session: Session, source: str, name: str, value: Any,
           labels: dict | None = None, ts: datetime | None = None) -> None:
    """One `metric_samples` row."""
    session.add(MetricSample(ts=_ts(ts), source=source, name=name, value=value,
                             labels=labels or {}))


def record_many(session: Session, source: str,
                samples: Iterable[tuple[str, Any, dict]], ts: datetime | None = None) -> int:
    """Every sample of one batch, as one `INSERT ... VALUES` statement. `samples` is
    `(name, value, labels)` triples; a sample with no labels of its own still needs `{}`, not
    `None`, since the column is `default=dict, nullable=False` but a bulk insert bypasses the
    ORM default."""
    when = _ts(ts)
    rows = [{"ts": when, "source": source, "name": name, "value": value, "labels": labels or {}}
            for name, value, labels in samples]
    if not rows:
        return 0
    session.execute(insert(MetricSample).values(rows))
    return len(rows)


def event(session: Session, kind: str, summary: str, ref: dict | None = None,
          ts: datetime | None = None) -> int:
    """One `operator_events` row. `summary` is sanitized and truncated first (F50), whatever
    kind of free text produced it, so a kill reason and a note share exactly one rule."""
    row = OperatorEvent(ts=_ts(ts), kind=kind, summary=sanitize_reason(summary), ref=ref or {})
    session.add(row)
    session.flush()
    return row.id


class Sampler:
    """An in-memory last-sample clock, one entry per key. The first call for a key is always
    due; a later call is due once `period_s` of `clock()` has passed since the key's last due
    call. Nothing here is persisted: a restart simply samples again on its first loop, which is
    the addendum's own answer for `order_watch_samples` (§3.3) and applies equally to the
    exec/equity samplers.
    """

    def __init__(self, period_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._period = period_s
        self._clock = clock
        self._last: dict[Any, float] = {}

    def due(self, key: Any) -> bool:
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < self._period:
            return False
        self._last[key] = now
        return True
