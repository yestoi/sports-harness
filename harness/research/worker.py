"""The `app-research` container's loop: the passes that spend the Anthropic key.

Addendum §1.4 "Worker" and §4. One container on the same image, no ports, `restart:
unless-stopped`, mounting `secrets/anthropic_api_key` read-only and **nothing else** -- no
Kalshi key, no Odds key, no `pgdata` (ruling A-M2). It hosts the shadow veto (T15) and the
weekly report annotator (T18), which is where the annotator has to live: `app-run` has no
`anthropic_api_key` mount, so a `Path.is_file()` switch there would be false forever and
silently, and there is no scheduled weekly report for a clock trigger to attach to anyway
(review B, C2).

**Two switches, reported differently.** `research_worker_enabled` is the operator's off switch,
documented in `deploy/nas.env` beside `SNAPSHOTS_ENABLED` (ruling B-M14). The key's absence is
dormancy, not a fault: `research: dormant, no key` is the line the log carries and the phase
ships whether or not the file is there.

**The pass registry.** `PASS_MODULES` names modules; `load_passes()` imports each and each
registers its passes at import. That is `harness/settlement/job.py`'s shape, for the same
reason: a later task appends one string and never imports a module that does not exist yet.

**A raising pass never stops the loop.** It is rolled back, recorded by the **class name** of
its exception -- never `str(exc)`, whose text can carry SQL and row content -- and the next pass
runs. The loop's job is to keep spending the budget usefully, not to be the first casualty of a
bad week of data.
"""
import logging
import signal
import time
from collections.abc import Callable
from datetime import datetime, timezone
from importlib import import_module

from sqlalchemy.orm import Session, sessionmaker

from harness.config.settings import Settings

log = logging.getLogger(__name__)

#: How long the loop sleeps between sweeps. The veto's own latency is 10-30 s a call and the
#: annotator fires at most once a week, so half a minute is responsive without spinning.
POLL_S = 30

#: Addendum §1.4: "Two calls at most concurrently." The number lives here because it bounds the
#: whole container, not any one pass; T15 reads it.
MAX_CONCURRENT_CALLS = 2

#: A pass: one sweep of work, given a session, the instant and the settings, returning a
#: JSON-able counts dict. It commits nothing itself -- the worker commits after each pass.
PassFn = Callable[[Session, datetime, Settings], dict]

#: The modules `load_passes()` imports. T15 appends "harness.research.veto"; T18 appends
#: "harness.research.annotate". Never import one from here: the import happens at run time so a
#: module that does not exist yet cannot break this one.
PASS_MODULES: list[str] = []

#: Every registered pass, in registration order.
PASSES: list[tuple[str, PassFn]] = []


def register_pass(name: str, fn: PassFn) -> None:
    """Append one pass. Registering a name twice is a no-op, so a module imported again (or a
    `load_passes()` called twice) cannot double-run a pass."""
    if any(existing == name for existing, _ in PASSES):
        return
    PASSES.append((name, fn))


def load_passes() -> list[tuple[str, PassFn]]:
    """Import every module in `PASS_MODULES` and return the registry."""
    for module in PASS_MODULES:
        import_module(module)
    return PASSES


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchWorker:
    """The loop. `run_once` is the whole of it; `run_forever` sleeps between calls to it."""

    def __init__(self, settings: Settings, session_factory: sessionmaker,
                 clock: Callable[[], datetime] = _utcnow,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.s = settings
        self._factory = session_factory
        self._clock = clock
        self._sleep = sleep
        self._stop = False

    def stop(self, *_) -> None:
        self._stop = True

    def run_once(self) -> dict:
        """One sweep. Returns what happened, which is what the log line and the tests read."""
        if not self.s.research_worker_enabled:
            return {"status": "disabled", "reason": "research_worker_enabled is false",
                    "passes": []}
        if not self.s.has_anthropic_key():
            return {"status": "dormant", "reason": "no key", "passes": []}

        now = self._clock()
        results: list[dict] = []
        with self._factory() as session:
            for name, fn in load_passes():
                try:
                    counts = fn(session, now, self.s)
                    session.commit()
                    results.append({"name": name, "counts": counts, "error": None})
                except Exception as exc:  # noqa: BLE001 - one pass must not stop the loop
                    session.rollback()
                    log.exception("research pass %s failed", name)
                    results.append({"name": name, "counts": {},
                                    "error": type(exc).__name__})
        status = "degraded" if any(r["error"] for r in results) else "ok"
        return {"status": status, "passes": results}

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log.info("research worker started, poll=%ss passes=%s", POLL_S,
                 ", ".join(name for name, _ in load_passes()) or "none")
        while not self._stop:
            result = self.run_once()
            log.info("research sweep %s: %s", result["status"],
                     ", ".join(f"{p['name']}={p['error'] or p['counts']}"
                               for p in result["passes"]) or "nothing to do")
            for _ in range(POLL_S):
                if self._stop:
                    break
                self._sleep(1)
        log.info("research worker stopped")
