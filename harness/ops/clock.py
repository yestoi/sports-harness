"""Is the kernel's clock disciplined? (carried fix 57)

After the Sunday crash the host booted with a stale clock (the RTC had reset; systemd advanced
the clock to the saved timesync file) and the compose stack started 32 s before NTP corrected
it, so a recorder run and every child row it wrote carry Sunday stamps for Monday work and
nothing in the app can tell them from Sunday in-game data. The cure is to write nothing at all
while the kernel says its clock is not synchronized, which is exactly what `adjtimex` reports:
`STA_UNSYNC` in the status word, or the `TIME_ERROR` return state.

This is a *read*: `modes = 0` asks the kernel for the current state and adjusts nothing, so it
needs no privilege at all and is safe inside the container. It is 64-bit Linux only; on any
other platform, and on any failure of the probe itself, `clock_synchronized()` returns None and
its callers proceed exactly as they did before this module existed. A clock probe must never
be the reason a tick does not run.
"""

import ctypes
import logging
import sys

log = logging.getLogger(__name__)

#: `include/uapi/linux/timex.h`: the clock is not synchronized to an external source.
STA_UNSYNC = 0x0040
#: `adjtimex(2)` clock states. Only the last one matters here: the kernel's own verdict that
#: the clock is not synchronized (an unset or expired discipline).
TIME_OK, TIME_INS, TIME_DEL, TIME_OOP, TIME_WAIT, TIME_ERROR = 0, 1, 2, 3, 4, 5

#: The period a caller may repeat its `clock_unsynced` warning at (fix 57: "once a minute").
CLOCK_WARN_PERIOD_S = 60.0

#: The one `runs.notes` key that marks a run recorded while the kernel clock was unsynchronized
#: (the user's ruling of 2026-09-14, journal 184 item 1). The guard above means no *new* run is
#: ever written under such a clock; this key is what the controller sets by hand on the runs
#: already written that way -- 14485 and its 2,697 gap snapshots, stamped Sunday for Monday
#: work -- and it is an **exclusion**, not documentation: every reader that reaches a run's
#: child rows for a time window must drop them. Named once, here, so the readers and the
#: annotation can never disagree about the spelling.
UNSYNCED_NOTE_KEY = "clock"
UNSYNCED_NOTE_VALUE = "unsynced"
#: The annotation itself, for a writer (`notes = notes || UNSYNCED_NOTE`).
UNSYNCED_NOTE = {UNSYNCED_NOTE_KEY: UNSYNCED_NOTE_VALUE}
#: The predicate for a query that already reads `runs` itself, e.g. `where ... and <this>`.
UNSYNCED_RUN_PREDICATE = f"notes->>'{UNSYNCED_NOTE_KEY}' = '{UNSYNCED_NOTE_VALUE}'"


def exclude_unsynced_runs(run_id_expr: str) -> str:
    """A SQL predicate that keeps only rows whose run is *not* marked unsynchronized.

    `run_id_expr` is the caller's own column expression (`s.run_id`, `f.run_id`, ...), never
    anything that came from outside the process. The anti-join is `not exists` on the runs
    primary key rather than `run_id not in (select ...)`, so it costs one index probe per
    candidate row and answers True for a child row whose run has no row at all (a fixture, a
    backfill), which keeps every reader's existing numbers exactly as they were.

    It deliberately puts no predicate on `runs.started_at`: design review C1 forbids that, and
    this reaches the run by primary key instead.
    """
    return (f"not exists (select 1 from runs _cr where _cr.id = {run_id_expr}"
            f" and _cr.{UNSYNCED_RUN_PREDICATE})")


def notes_are_unsynced(notes) -> bool:
    """The same exclusion for a reader that has already read `runs.notes` into Python
    (`harness/dashboard/queries.py`'s cap-then-filter readers)."""
    return isinstance(notes, dict) and notes.get(UNSYNCED_NOTE_KEY) == UNSYNCED_NOTE_VALUE


class _Timex(ctypes.Structure):
    """`struct timex` exactly as `<sys/timex.h>` (glibc) declares it on 64-bit Linux, which is
    the same layout as `include/uapi/linux/timex.h` there: `long` is 8 bytes and ctypes inserts
    the same alignment padding the C compiler does, so `sizeof` is 208 (pinned by a test).

    Every field is declared even though only `modes` (written, 0) and `status` (read) are used:
    a short buffer handed to a syscall that writes the whole structure would corrupt the
    stack behind it.
    """

    _fields_ = [
        ("modes", ctypes.c_int),
        ("offset", ctypes.c_long),
        ("freq", ctypes.c_long),
        ("maxerror", ctypes.c_long),
        ("esterror", ctypes.c_long),
        ("status", ctypes.c_int),
        ("constant", ctypes.c_long),
        ("precision", ctypes.c_long),
        ("tolerance", ctypes.c_long),
        ("time_sec", ctypes.c_long),       # struct timeval time
        ("time_usec", ctypes.c_long),
        ("tick", ctypes.c_long),
        ("ppsfreq", ctypes.c_long),
        ("jitter", ctypes.c_long),
        ("shift", ctypes.c_int),
        ("stabil", ctypes.c_long),
        ("jitcnt", ctypes.c_long),
        ("calcnt", ctypes.c_long),
        ("errcnt", ctypes.c_long),
        ("stbcnt", ctypes.c_long),
        ("tai", ctypes.c_int),
        ("_pad", ctypes.c_int * 11),
    ]


_libc = None


def _adjtimex(buf: _Timex) -> int:
    """One `adjtimex(&buf)` through libc. Split out as a module function so a test can stand in
    for the syscall (an unsynchronized kernel, a raising probe) without a kernel of its own."""
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
    return _libc.adjtimex(ctypes.byref(buf))


def clock_synchronized() -> bool | None:
    """False when the kernel says its clock is not synchronized, True when it is, None when the
    probe is unavailable (non-Linux, no libc symbol, a failed call, anything raising).

    None is deliberately not False: a caller that cannot ask must behave exactly as it did
    before the probe existed, because the alternative is a harness that records nothing on a
    platform where the question cannot be asked.
    """
    if not sys.platform.startswith("linux"):
        return None
    try:
        buf = _Timex()
        buf.modes = 0  # a read: adjust nothing
        state = _adjtimex(buf)
        if state < 0:
            return None
        if state == TIME_ERROR:
            return False
        return not bool(buf.status & STA_UNSYNC)
    except Exception:  # noqa: BLE001 - a probe never fails its caller
        log.debug("adjtimex clock probe unavailable", exc_info=True)
        return None
