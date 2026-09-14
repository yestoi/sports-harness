import logging
import re
import time
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from harness.db.models import NormalizeState, RawResponse
from harness.matching.games import upsert_games_from_odds
from harness.normalize.espn import link_espn_scoreboard
from harness.normalize.kalshi import (apply_series_fee, insert_orderbook, insert_trades, insert_venue_quotes,
                                      upsert_venue_markets)
from harness.normalize.odds import parse_odds_body, upsert_odds_rows

log = logging.getLogger(__name__)
FAMILIES = ("espn", "odds_featured", "odds_alternates", "kalshi_events", "kalshi_markets", "kalshi_orderbook",
           "kalshi_trades", "kalshi_series")
# Tables rebuildable in full from raw_responses. venue_trades is deliberately absent: it also
# holds source='ws' rows that exist nowhere in raw_responses, so it is pruned by source instead.
NORMALIZED_TABLES = ("odds_snapshots", "venue_quotes", "orderbook_snapshots", "venue_markets", "games")
_EVENTS: dict[str, dict] = {}  # event_ticker -> event, refreshed from raw /events bodies
#: Fix 49: `_EVENTS` is process-wide and used to be pruned by nothing but `reprocess`, so a
#: recorder that stayed up for a season accumulated an entry for every event ticker football
#: ever had. The cap is far above one weekend's events across all six series (a Saturday slate
#: is on the order of a thousand) and far below unbounded; eviction is oldest-write-first, and
#: `_remember_event` re-inserts on every write so a ticker still in the feed keeps its place.
_EVENTS_MAX = 20_000
_OB_RE = re.compile(r"^/markets/([^/]+)/orderbook$")
_SERIES_RE = re.compile(r"^/series/([^/]+)$")


def _sport_from_endpoint(endpoint: str, params: dict) -> str | None:
    if "americanfootball_nfl" in endpoint or endpoint.startswith("/nfl/"):
        return "nfl"
    if "americanfootball_ncaaf" in endpoint or endpoint.startswith("/college-football/"):
        return "ncaaf"
    s = (params or {}).get("series_ticker", "")
    if s.startswith("KXNFL"):
        return "nfl"
    if s.startswith("KXNCAAF"):
        return "ncaaf"
    return None


def _family_filter(family: str):
    src, ep = RawResponse.source, RawResponse.endpoint
    return {
        "espn": src == "espn",
        "odds_featured": (src == "odds_api") & ep.like("/sports/%/odds") & ~ep.like("/sports/%/events/%"),
        "odds_alternates": (src == "odds_api") & ep.like("/sports/%/events/%/odds"),
        "kalshi_events": (src == "kalshi") & (ep == "/events"),
        "kalshi_markets": (src == "kalshi") & (ep == "/markets"),
        "kalshi_orderbook": (src == "kalshi") & ep.like("/markets/%/orderbook"),
        "kalshi_trades": (src == "kalshi") & (ep == "/markets/trades"),
        "kalshi_series": (src == "kalshi") & ep.like("/series/%"),
    }[family]


def family_of(source: str, endpoint: str) -> str | None:
    """Which normalize family a raw row belongs to -- the Python side of `_family_filter`.

    The two must agree for the same row or the backlog number would be about a different queue
    than the one that drains; `tests/test_coverage_denominator.py` pins one representative
    endpoint per family against this map. Anything else returns None and is counted nowhere.
    """
    if source == "espn":
        return "espn"
    if source == "odds_api" and endpoint.startswith("/sports/"):
        # `_family_filter`'s two LIKE patterns, in the same order and with the same
        # exclusion: `/sports/%/events/%/odds` is alternates, `/sports/%/odds` that is
        # *not* under `/events/` is featured (`runner.py` `_family_filter`).
        if endpoint.endswith("/odds"):
            return "odds_alternates" if "/events/" in endpoint else "odds_featured"
        return None
    if source == "kalshi":
        if endpoint == "/events":
            return "kalshi_events"
        if endpoint == "/markets":
            return "kalshi_markets"
        if endpoint == "/markets/trades":
            return "kalshi_trades"
        if endpoint.startswith("/markets/") and endpoint.endswith("/orderbook"):
            return "kalshi_orderbook"
        if endpoint.startswith("/series/"):
            return "kalshi_series"
    return None


#: 6D §1.3(c). The newest raw id this run wrote, per (source, endpoint). Bounded by `run_id` on
#: `ix_raw_run` (`harness/db/schema.py`) -- the ids the tick itself inserted, never a scan of the
#: tape.
_RUN_RAW_IDS = text(
    "select source, endpoint, max(id) as newest from raw_responses "
    "where run_id = :run_id group by source, endpoint")

#: The oldest unprocessed row's own timestamp, per family. `raw_responses` is range-partitioned
#: on `fetched_at` and its primary key is `(id, fetched_at)`, so whether this read is
#: index-ordered across partitions is a question about the plan rather than about the statement:
#: the plan task captures `EXPLAIN` for it and **abandons it for the id lag alone** if it is not
#: (§1.3c). `NORMALIZE_AGE_ENABLED` is that switch, and it is a module constant so the decision
#: is one line rather than a deletion.
#: The cursor is the family's own watermark but the row returned is the first row after it in
#: *any* family, so the age is an upper bound on that family's own backlog age -- which is the
#: direction that cannot understate a backlog. §1.3(d)'s rule reads it against one cadence, and
#: the id lag beside it is exact.
_OLDEST_UNPROCESSED = text(
    "select fetched_at from raw_responses where id > :cursor order by id limit 1")
NORMALIZE_AGE_ENABLED = True


def backlog_samples(session: Session, run_id: int, now: datetime) -> list[tuple[str, object, dict]]:
    """`normalize.backlog_ids` and `normalize.backlog_age_s` per family (§1.3c).

    Journal 154's finding -- the deployed stage order works but "about 12 h normalizer backlog
    yields zero current venue_quotes" -- is exactly this number, and §1.3(d) is the decision it
    feeds. A family at zero is still reported: evidence that a queue is empty is evidence.

    `now` is a parameter, never `datetime.now()`: every age in this suite is computed against a
    caller-supplied tz-aware instant, which is what lets a test assert an exact number.
    """
    newest: dict[str, int] = {}
    for source, endpoint, last_id in session.execute(_RUN_RAW_IDS, {"run_id": run_id}).all():
        family = family_of(source, endpoint)
        if family is not None:
            newest[family] = max(newest.get(family, 0), int(last_id))
    if not newest:
        return []
    cursors = {row.family: row.last_raw_id for row in session.execute(
        select(NormalizeState)).scalars().all()}
    samples: list[tuple[str, object, dict]] = []
    for family, last_id in newest.items():
        cursor = int(cursors.get(family, 0))
        samples.append(("normalize.backlog_ids", max(0, last_id - cursor), {"family": family}))
        if NORMALIZE_AGE_ENABLED and last_id > cursor:
            oldest = session.execute(_OLDEST_UNPROCESSED, {"cursor": cursor}).scalar()
            if oldest is not None:
                age = (now - oldest).total_seconds()
                samples.append(("normalize.backlog_age_s", max(0.0, round(age, 1)),
                                {"family": family}))
    return samples


def _remember_event(ev: dict) -> None:
    """Write one event into the process-wide cache, keeping it under `_EVENTS_MAX`."""
    ticker = ev.get("event_ticker")
    if not ticker:
        return
    _EVENTS.pop(ticker, None)  # re-insert so insertion order tracks last write
    _EVENTS[ticker] = ev
    while len(_EVENTS) > _EVENTS_MAX:
        _EVENTS.pop(next(iter(_EVENTS)))


def _events_in(body) -> list:
    return body.get("events", []) if isinstance(body, dict) else []


def _load_events_cache(session: Session) -> None:
    # Fix 45: `ix_raw_source_endpoint_id (source, endpoint, id)` supports this
    # (source='kalshi', endpoint='/events') lookup in id-descending order, avoiding
    # a backward primary-key walk with source and endpoint only as filters.
    # http_status remains a filter; the index does not cover the selected body.
    # Only the body is read, so only the body is selected (fix 49): loading the mapped
    # `RawResponse` pulled two hundred whole rows -- jsonb payloads included -- into the session
    # to copy a handful of dicts out of them.
    bodies = session.execute(select(RawResponse.body)
                             .where(_family_filter("kalshi_events"), RawResponse.http_status == 200)
                             .order_by(RawResponse.id.desc()).limit(200)).scalars().all()
    for body in reversed(bodies):
        for ev in _events_in(body):
            _remember_event(ev)


def _handle(session: Session, family: str, r, ctx: dict) -> None:
    """Normalize one raw response. `r` is any row exposing `id`, `run_id`, `endpoint`,
    `params`, `fetched_at` and `body` -- since finding 49 round 2 that is a projected `Row`, not
    the mapped `RawResponse`, which avoids building a mapped instance (and its `InstanceState`)
    per row."""
    sport = _sport_from_endpoint(r.endpoint, r.params)
    body = r.body
    if family == "espn" and sport:
        link_espn_scoreboard(session, sport, body, raw_id=r.id)
    elif family in ("odds_featured", "odds_alternates") and sport:
        if family == "odds_featured":
            res = upsert_games_from_odds(session, sport, body, r.id)
            ctx.setdefault("unresolved_teams", []).extend(res.unresolved)
        odds = upsert_odds_rows(session, sport, parse_odds_body(body, sport), r.id, r.run_id, r.fetched_at)
        dropped = ctx.setdefault("odds_dropped", {"unknown_game": 0, "unresolved_team": 0})
        dropped["unknown_game"] += odds.dropped_unknown_game
        dropped["unresolved_team"] += odds.dropped_unresolved_team
    elif family == "kalshi_events":
        for ev in _events_in(body):
            _remember_event(ev)
    elif family == "kalshi_markets" and sport:
        if (r.params or {}).get("status") == "settled":
            # Recorder._kalshi_settled (F10(b)/R11) stores this page for phase 3's settlement
            # task to read `result` from directly; it must not touch venue_markets or
            # venue_quotes, which would bump last_seen_at and re-derive match_reason for markets
            # that settled days ago (flooding the dashboard's last_seen_at-keyed "in play" views)
            # and would add a venue_quotes row that build_gap_snapshots would price as if live.
            return
        markets = (body or {}).get("markets", []) if isinstance(body, dict) else []
        upsert_venue_markets(session, sport, markets, _EVENTS, r.id, r.fetched_at, ctx)
        insert_venue_quotes(session, markets, r.id, r.run_id, r.fetched_at)
    elif family == "kalshi_orderbook":
        m = _OB_RE.match(r.endpoint)
        if m:
            insert_orderbook(session, m.group(1), body, r.id, r.fetched_at)
    elif family == "kalshi_trades":
        insert_trades(session, body, r.id, ctx)
    elif family == "kalshi_series":
        m = _SERIES_RE.match(r.endpoint)
        if m:
            apply_series_fee(session, m.group(1), body)


def _watermark(session: Session, family: str) -> NormalizeState:
    state = session.get(NormalizeState, family)
    if state is None:
        state = NormalizeState(family=family, last_raw_id=0)
        session.add(state)
        session.flush()
    return state


#: The columns `_handle` reads off a raw response. Finding 49 round 2: the mapped `RawResponse`
#: was never needed here -- nothing in `_handle` writes to it or navigates a relationship -- so
#: the projection also avoids building a mapped instance (and its `InstanceState`) per row.
_ROW_COLUMNS = (RawResponse.id, RawResponse.run_id, RawResponse.endpoint, RawResponse.params,
                RawResponse.fetched_at, RawResponse.body)


def _drain_batch(session: Session, family: str, batch: int, ctx: dict,
                 deadline: float | None = None) -> tuple[int, int, bool]:
    """Process one batch of a family. Returns (normalized, fetched, committed).

    Stops early (after at least one row) once ``deadline`` (a ``time.monotonic()`` value) passes,
    committing progress through the last processed row so the next call resumes there.

    Finding 49, round 2 -- the recorder's single largest allocation, and the one the first round
    missed. This used to read the batch with one `select(RawResponse) ... .scalars().all()`:
    psycopg buffers a whole result set client-side and SQLAlchemy then built every mapped row
    from it, so *every jsonb body in the batch was decoded into the process before the first row
    was handled*. Round 1's `rows[i] = None` dropped each row after handling it, which cannot
    lower a peak that has already happened at materialisation. `batch` is five hundred and a
    live `/markets` page is 2.3 MB of JSON (~1,000 markets) while a `/markets/trades` page is up
    to 320 KB (~1,000 prints), and the decoded dicts are several times the JSON's size: measured
    on 2026-09-13, one batch of 250 trade pages held 229 MiB resident at once, and the recorder
    does this every tick into an allocator that returns an arena only when it empties. That is
    the `RssAnon` series in the fix 49 brief (121.8 -> 1,935.4 MiB over five ticks, then ~1.2 GB
    steps per tick to 4.0 GiB).

    So the batch read now selects the two primary-key columns only -- the batch's *membership*,
    which is all the watermark needs -- and each row's body is fetched on its own, used, and
    dropped before the next one is read. Peak resident bodies: one, not `batch`; the local name
    is the only thing holding each body, and the projection avoids building a mapped instance
    (and its `InstanceState`) per row on top of it. Nothing about the ordering, the batch size,
    the per-row savepoint, the watermark or the commit changes.

    The per-row read is keyed on both primary-key columns (`id`, `fetched_at`), not `id` alone:
    `raw_responses` is range-partitioned on `fetched_at`, so a lookup without it would scan
    every weekly partition.
    """
    state = _watermark(session, family)
    last_committed = state.last_raw_id
    keys = session.execute(
        select(RawResponse.id, RawResponse.fetched_at)
        .where(_family_filter(family), RawResponse.http_status == 200,
               RawResponse.id > last_committed)
        .order_by(RawResponse.id).limit(batch)).all()
    if not keys:
        return 0, 0, True
    n, last_id, processed = 0, last_committed, 0
    for raw_id, fetched_at in keys:
        if deadline is not None and processed > 0 and time.monotonic() >= deadline:
            break
        processed += 1
        r = session.execute(select(*_ROW_COLUMNS).where(
            RawResponse.id == raw_id, RawResponse.fetched_at == fetched_at)).first()
        if r is None:
            # Gone between the membership read and this one. Nothing in the tree deletes
            # `raw_responses` rows today, so this is a manual prune or a partition detached out
            # from under us; either way there is nothing to normalize and the watermark still
            # has to move past it rather than re-reading the hole every batch.
            last_id = raw_id
            continue
        try:
            # One savepoint per row: a database error aborts only this row, leaving the rest
            # of the batch (and the watermark) intact.
            with session.begin_nested():
                _handle(session, family, r, ctx)
            n += 1
        except Exception as e:  # noqa: BLE001
            log.exception("normalize %s raw_id=%s failed", family, raw_id)
            ctx.setdefault("normalize_errors", []).append({family: {"raw_id": raw_id, "error": repr(e)[:300]}})
        # A poison row is skipped, never retried in a loop.
        last_id = raw_id
        # Drop this row's body before the next one is read, so the phase's resident set is one
        # body and not the batch (finding 49, round 2 -- see the docstring).
        r = None
    _watermark(session, family).last_raw_id = last_id
    try:
        session.commit()
    except Exception as e:  # noqa: BLE001
        log.exception("normalize %s commit failed", family)
        session.rollback()
        ctx.setdefault("normalize_errors", []).append({family: {"commit_error": repr(e)[:300]}})
        # The rollback restored the watermark to the last successfully committed row.
        _watermark(session, family).last_raw_id = last_committed
        session.commit()
        return 0, processed, False
    return n, processed, True


def normalize_new(session: Session, batch: int = 500, ctx: dict | None = None,
                  time_budget_s: float = 30.0) -> dict[str, int]:
    ctx = ctx if ctx is not None else {}
    if not _EVENTS:
        _load_events_cache(session)
    counts: dict[str, int] = {}
    deadline = time.monotonic() + time_budget_s
    for family in FAMILIES:
        n, first = 0, True
        while True:
            # Every family gets at least one (deadline-aware) batch per call so no family starves;
            # after that, stop as soon as the budget is spent and resume next tick.
            if not first and time.monotonic() >= deadline:
                log.warning("normalize %s: time budget %.0fs reached; resuming next tick", family, time_budget_s)
                break
            done, fetched, committed = _drain_batch(session, family, batch, ctx, deadline=deadline)
            first = False
            n += done
            if not committed or fetched < batch:
                break
        counts[family] = n
    return counts


def reprocess(session: Session, from_raw_id: int = 0, families: list[str] | None = None, truncate: bool = False) -> dict[str, int]:
    if truncate:
        session.execute(text("truncate " + ", ".join(NORMALIZED_TABLES) + " restart identity cascade"))
        # WebSocket trades are not rebuildable from raw_responses; only REST rows are.
        session.execute(text("delete from venue_trades where source = 'rest'"))
    for family in families or FAMILIES:
        state = session.get(NormalizeState, family) or NormalizeState(family=family)
        state.last_raw_id = from_raw_id
        session.add(state)
    session.commit()
    _EVENTS.clear()
    total: dict[str, int] = {f: 0 for f in FAMILIES}
    while True:
        counts = normalize_new(session, batch=2000)
        for k, v in counts.items():
            total[k] += v
        if sum(counts.values()) == 0:
            return total
