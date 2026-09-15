"""Episodes: the two funnel units 6C deferred, counted without a `distinct` scan.

Addendum §1.7 and decision D6. One bounded read of the open episodes, one multi-row upsert, both
per run -- never one statement per key, because the pricing pass offers up to 5,068 keys a tick
(724 gap rows x 7 variants) and run 14307's real number was 393.

The cap is `EPISODE_UPSERT_CAP`, with an `episodes.truncated` metric when it binds (ruling I7),
and the churn it bounds is stated in §2: ~145,000 upserts a day, each rewriting one row version
and two index entries, ~29 MB/day of dead tuples that autovacuum reclaims and §3 row 10 watches
as a dead-tuple ratio rather than as growth.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness import telemetry

log = logging.getLogger(__name__)

#: Keys per write. Above it the writer takes the first `EPISODE_UPSERT_CAP` keys, records how
#: many it dropped as `episodes.truncated`, and carries on -- a bound that binds is visible.
EPISODE_UPSERT_CAP = 2048

#: The floor under the gap rule. Ten minutes is longer than every cadence in force but the
#: 900 s weekday one, so on a game day the rule is the floor and an episode is not split by one
#: missed tick.
EPISODE_GAP_FLOOR_S = 600


def gap_rule_s(cadence_s: int) -> int:
    """`max(600, 3 x cadence)` (§1.7(b)). Three periods, so a single missed tick never splits an
    episode, and never below the floor."""
    return max(EPISODE_GAP_FLOOR_S, 3 * int(cadence_s))


def upsert(session: Session, model, keys, now: datetime, rule_s: int, *, kind: str) -> int:
    """Extend or open one episode per `(variant_id, venue_market_id, side)` key.

    One read -- the open episodes for the window `rule_s` defines, riding the table's
    `started_at` index -- and one multi-row upsert on the unique key. An extension hits the
    conflict target (the open episode's own `started_at`) and updates; a new episode misses it
    and inserts. Nothing here may fail its caller: the whole write is one savepoint and a
    failure is logged, exactly as `coverage.record` and `telemetry.record_many`'s callers do.
    """
    keys = list(dict.fromkeys(keys))
    if not keys:
        return 0
    dropped = 0
    if len(keys) > EPISODE_UPSERT_CAP:
        dropped = len(keys) - EPISODE_UPSERT_CAP
        keys = keys[:EPISODE_UPSERT_CAP]
    count_column = "n_signals" if kind == "opportunity" else "n_intents"
    since = now - timedelta(seconds=rule_s)
    try:
        with session.begin_nested():
            # Bound: `ended_at >= :since` on `ix_opportunity_ended` / `ix_intent_ended`. The
            # bound *is* the openness test (review Important 1): an episode is open when its
            # last sighting is within `rule_s`, and `started_at` says nothing about that. A
            # continuously extended episode keeps its original `started_at`, so bounding the
            # read on `started_at` lost that row two rule widths after it opened and reopened
            # the same key as a new row -- 17 segments for six hours of 120 s sightings where
            # §1.7(b) means one. An episode older than the rule is closed and is never
            # returned, so one key can never have two open rows here.
            open_rows = session.execute(
                select(model.variant_id, model.venue_market_id, model.side, model.started_at)
                .where(model.ended_at >= since)).all()
            open_start = {(variant_id, market_id, side): started_at
                          for variant_id, market_id, side, started_at in open_rows}
            values = []
            for variant_id, market_id, side in keys:
                started = open_start.get((variant_id, market_id, side), now)
                values.append({"variant_id": variant_id, "venue_market_id": market_id,
                               "side": side, "started_at": started, "ended_at": now,
                               "gap_rule_s": rule_s, count_column: 1})
            stmt = insert(model).values(values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["variant_id", "venue_market_id", "side", "started_at"],
                set_={"ended_at": stmt.excluded.ended_at,
                      count_column: getattr(model, count_column) + 1})
            session.execute(stmt)
            if dropped:
                telemetry.record(session, "recorder", "episodes.truncated", dropped,
                                 {"kind": kind}, ts=now)
    except Exception:  # noqa: BLE001 - an episode never fails a tick or a loop
        log.exception("episodes.upsert failed for %s", kind)
        return 0
    return len(values)
