"""Read helpers shared by the legacy page and the snapshot builders.

Nothing here is new: these three functions were `harness/dashboard/app.py`'s privates and are
moved verbatim, docstrings included, so the legacy funnel and the Floor surface's funnel read
`runs.notes` through exactly one implementation and cannot drift apart (addendum §2).

Why `runs.notes` and not the source tables: a direct scan of `fair_values`,
`market_gap_snapshots` and `signals` took 86-92 s against the dashboard's 10 s bound once the
season's volume grew (fix 15), because `ix_gap_market_created` and `ix_signal_variant_created`
lead on market and variant, not on time. The per-run sums in `runs.notes` answer the same
question for the price of one index-ordered read of a ~2 MB table.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from harness.db.models import Run, StrategyVariant

WINDOW_24H = timedelta(hours=24)
#: The Floor funnel's window (spec §2.2).
WINDOW_6H = timedelta(hours=6)


def recent_run_notes(session: Session, cutoff: datetime, limit: int | None = None) -> list[dict]:
    """Every run row's `notes` JSON since `cutoff`, newest first -- the one query `_funnel`,
    `_candidates` and `_data_quality` scan `runs.notes` through (fix round 1, Minor 3: `_funnel`
    and `_data_quality` used to run this query twice; fix 17, journal 44/48: `build_summary` now
    fetches the 24h window once and hands the same list to all three, so none of them call this
    directly except in their own direct unit tests). `limit`, when given, caps the row count;
    omit it for a window meant to be read in full -- all three pass no limit as of fix 17 round
    1: a 500-row cap used to silently truncate a "24h" scan to about 4.2 hours at the default
    30s heartbeat (fix round 1, Important 2), and `_data_quality`'s `kalshi_trades_normalized`
    sum needs the same true 24h window `_funnel` and `_candidates` already get. ~2880 rows a day
    of this ~2MB table is cheap to scan in full.

    **Ordered by `id`, and a caller with a limit filters on `started_at` in Python** (fix 31,
    round 1). `runs` carries no index on `started_at`, which makes every shape of this read a
    full scan of the table -- the question is only what stops it. `order by started_at desc` was
    a full scan *plus* a sort of the `notes` JSONB, and a `limit` on top of it capped the rows
    returned without capping the rows read. `order by id desc` removes the sort, because `id` is
    the primary key and is assigned in insertion order by the one writer that inserts here, so
    newest-first by id is newest-first by time. But a `where started_at >= :cutoff` *beside* a
    `limit` does not stop the scan either: PostgreSQL walks the primary key backwards discarding
    rows that fail the predicate until it has `limit` matches, and a limit set above the
    window's true row count is never reached -- a 6 h window holds 400-700 rows against a limit
    of 2,000, so the backward walk ran to the start of the table on every Floor build.

    So the predicate and the limit do not travel together. With a limit, the SQL carries the
    limit alone and the read stops at exactly N rows; `started_at` comes back beside `notes` and
    the window is applied here, which is free on N rows already in memory. Without one, the SQL
    carries the predicate alone, which is what the legacy page's uncapped 24 h funnel wants and
    is unchanged from before fix 31 apart from the ordering.

    The cost of the limited form is that a limit *below* the window's row count silently returns
    the newest N rather than the whole window -- the same truncation fix 17 round 1 removed for
    the uncapped callers. That is why `FUNNEL_NOTES_LIMIT` is set at about triple the expected
    count and why it is documented where it is set, not here.
    """
    if limit is None:
        stmt = select(Run.notes).where(Run.started_at >= cutoff).order_by(desc(Run.id))
        return session.execute(stmt).scalars().all()
    stmt = select(Run.started_at, Run.notes).order_by(desc(Run.id)).limit(limit)
    return [notes for started_at, notes in session.execute(stmt).all() if started_at >= cutoff]


def recent_runs(session: Session, cutoff: datetime,
                limit: int | None = None) -> list[tuple[datetime, dict]]:
    """`recent_run_notes`' sibling, returning `(started_at, notes)` so a caller that needs an
    **upper** bound can apply one (plan review C1).

    Same read and the same reasoning as `recent_run_notes` above: `runs` carries no index on
    `started_at`, so with a `limit` the SQL carries the limit alone, the read stops at exactly N
    rows walking the primary key backwards, and the window is applied in Python on rows already
    in memory. The one difference is that the timestamp comes back instead of being discarded,
    because `recent_run_notes`' single-ended filter answers "since `cutoff`", which for a
    *closed* week means "from that week's Monday until now". The weekly report needs "inside that
    week", and the difference is a third of a day on a Monday-morning report of the previous
    week and the whole answer on any week before that.

    Without a `limit` the predicate goes in the SQL, exactly as above, and the caller still
    applies its own upper bound.
    """
    if limit is None:
        stmt = (select(Run.started_at, Run.notes)
                .where(Run.started_at >= cutoff).order_by(desc(Run.id)))
        return [(started_at, notes) for started_at, notes in session.execute(stmt).all()]
    stmt = select(Run.started_at, Run.notes).order_by(desc(Run.id)).limit(limit)
    return [(started_at, notes) for started_at, notes in session.execute(stmt).all()
            if started_at >= cutoff]


def recent_runs_pricing(session: Session, cutoff: datetime,
                        limit: int | None = None) -> list[tuple[datetime, dict | None]]:
    """`recent_runs`' own projection: `(started_at, notes['pricing'])` instead of the whole
    `notes` document (design review I4).

    `notes` also carries `normalized`, `leg_probs`, `weather`, `odds_dropped`,
    `unresolved_teams`, the errors and warnings lists and a per-variant `signals` block written
    directly under `notes` (a different thing from `notes['pricing']['signals']`,
    `harness/recorder/tick.py:946-961`). A caller that reads only `notes['pricing']` -- t13's
    `_t13_coverage` is the one today -- does not need the rest of that JSONB document
    deserialised into Python at up to `T13_NOTES_LIMIT` rows, 12.5x Floor's own comparable cap
    (`FUNNEL_NOTES_LIMIT`, `harness/dashboard/snapshots/floor.py:99`). Projecting
    `notes['pricing']` in SQL keeps `recent_runs`' plan, cap and cap-then-filter shape exactly --
    `Index Scan Backward using runs_pkey`, `Limit N`, no predicate -- and drops the transferred
    payload to the one part the caller reads.
    """
    if limit is None:
        stmt = (select(Run.started_at, Run.notes["pricing"])
                .where(Run.started_at >= cutoff).order_by(desc(Run.id)))
        return [(started_at, pricing) for started_at, pricing in session.execute(stmt).all()]
    stmt = select(Run.started_at, Run.notes["pricing"]).order_by(desc(Run.id)).limit(limit)
    return [(started_at, pricing) for started_at, pricing in session.execute(stmt).all()
            if started_at >= cutoff]


def signals_by_variant_from_notes(session: Session, run_notes: list[dict]) -> dict:
    """`{variant_name: {tier, candidate, rejected}}`, seeded with every active variant's `tier`
    from `strategy_variants` (a small table -- this join stays a live query) and summed from
    `run_notes`' `pricing.signals` (fix 15, journal 44). Factored out of `_funnel` in fix 17
    (journal 44/48) so `_candidates` can reuse it instead of issuing its own group-by over
    `signals`; both callers pass the *same* `run_notes` list so `runs.notes` is scanned once
    per page, not once per section.
    """
    signals_by_variant = {
        v.name: {"tier": v.tier, "candidate": 0, "rejected": 0}
        for v in session.execute(
            select(StrategyVariant).where(StrategyVariant.active.is_(True)).order_by(StrategyVariant.name)
        ).scalars().all()
    }
    for notes in run_notes:
        pricing = (notes or {}).get("pricing") or {}
        for variant, counts in (pricing.get("signals") or {}).items():
            agg = signals_by_variant.setdefault(variant, {"tier": None, "candidate": 0, "rejected": 0})
            agg["candidate"] += counts.get("candidate", 0) or 0
            agg["rejected"] += counts.get("rejected", 0) or 0
    return signals_by_variant


def local_day_bounds_utc(now: datetime, tz_local: str) -> tuple[datetime, datetime]:
    """[local midnight, next local midnight) for `now`'s local calendar day, in UTC."""
    tz = ZoneInfo(tz_local)
    start_local = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
