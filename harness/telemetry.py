"""Additive telemetry writers for the dashboard (U6, dashboard design spec §3): metric
samples, operator events and a small in-memory sampling clock.

Ruling 1 binds every caller of this module, not this module itself: telemetry never changes a
decision, every writer is additive, and a telemetry failure must never fail a tick, a step or a
stage. That means callers wrap these calls in their own try/except where that guarantee
matters (the executor step, the recorder tick, the settlement stages); this module raises
whatever the database raises, since swallowing it here would hide the failure from the caller
that actually knows what "continue anyway" means for its own loop.
"""

import sys
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


def rss_mb() -> float | None:
    """This process's resident set size in MiB, or None where neither source reads.

    Fix 49: `app-run` went 78 MiB -> 1.82 GiB in one tick on the NAS and nobody could see it
    without ssh, so the recorder now writes this as `recorder.rss_mb` every tick. Linux (the
    NAS, every container) gets the *current* size from `/proc/self/status`; elsewhere (a Mac
    running the tests) `getrusage` gives the process's *peak* instead, which is the closest
    thing BSD offers without adding a dependency, so a local number never decreases.

    No new dependency by design: `psutil` would read both platforms uniformly, but this metric
    is not worth a package on the deploy image.
    """
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        import resource

        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:  # noqa: BLE001 - a metric never fails its caller
        return None
    # Linux reports ru_maxrss in KiB, macOS and the BSDs in bytes.
    return maxrss / 1024 if sys.platform.startswith("linux") else maxrss / (1024 * 1024)


#: `ctypes.CDLL("libc.so.6").malloc_trim`, looked up once and cached. `False` means "looked and
#: there is none" (musl, macOS, a static build); `None` means "not looked yet".
_MALLOC_TRIM: Any = None


def _malloc_trim_fn():
    """glibc's `malloc_trim`, or None where the platform has no such symbol.

    Fix 49 round 3: the recorder process steps up ~370 MiB across one weekly report render and
    never gives it back. The render's Python objects *are* released -- the traced total returns
    to its baseline -- but glibc keeps the freed arenas mapped, so RSS stays at the render's
    high-water mark for the life of the process. `malloc_trim(0)` is the only call that hands
    those back to the kernel without restarting; nothing else here can lower RSS.

    No new dependency: `ctypes` is stdlib. Looked up once and cached, because `CDLL` on every
    tick would be a dlopen on every tick.
    """
    global _MALLOC_TRIM
    if _MALLOC_TRIM is None:
        try:
            import ctypes

            libc = ctypes.CDLL("libc.so.6")
            fn = libc.malloc_trim
            fn.argtypes = [ctypes.c_size_t]
            fn.restype = ctypes.c_int
            _MALLOC_TRIM = fn
        except Exception:  # noqa: BLE001 - a platform without it is a no-op, never a failure
            _MALLOC_TRIM = False
    return _MALLOC_TRIM or None


def malloc_trim() -> float | None:
    """Return freed heap to the kernel and answer how many MiB of RSS that recovered.

    `None` where the platform has no `malloc_trim` (a no-op) or where RSS cannot be read; a
    float -- possibly 0.0, possibly negative by a page or two of noise -- where it ran. The
    caller records it as `recorder.malloc_trim_mb` so the effect is visible on Pulse instead of
    having to be taken on faith.

    Ruling 1: this is telemetry-adjacent housekeeping, so it never raises at its caller.
    """
    fn = _malloc_trim_fn()
    if fn is None:
        return None
    before = rss_mb()
    try:
        fn(0)
    except Exception:  # noqa: BLE001 - never fail a tick or a stage for this
        return None
    after = rss_mb()
    if before is None or after is None:
        return None
    return round(before - after, 1)


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

    def forget(self, key: Any) -> None:
        """Drop a key's remembered clock, e.g. once its subject (an order) is gone for good and
        will never be sampled again -- otherwise the dict grows for the life of the process."""
        self._last.pop(key, None)
