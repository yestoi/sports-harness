"""The weekly futures and ladder snapshot (addendum §1.1, roadmap item (a), H7).

H7 asks whether long-horizon futures and win-total ladders are compressed toward 50c and how
that drifts week over week. This phase records the panel; the measurement is a later report
table. What the recording has to get right is that the panel is the **same** panel each week,
which is why the pass is deterministic end to end: categories in sorted order, series in sorted
order, a resume point when the budget binds, and the reached set written into `job_runs.notes`
so the Tuesday 09:30 duty (R:309) sees coverage rather than only `ok`.

**Discovery** (addendum 0.9). `GET /series` has no ticker-prefix filter, so the pass enumerates
the football categories, walks their series, keeps the tickers starting `KXNFL` or `KXNCAAF`, and
excludes the per-game series by **exact match** against `FOOTBALL_SERIES` -- imported, never
restated, and exact rather than by prefix so a future `KXNFLGAMEMVP` is kept (ruling A-I9).

The live `GET /search/tags_by_categories` body wraps the category map under a
`"tags_by_categories"` key (`{"tags_by_categories": {"Sports": [...], ...}}`), recorded in
`tests/fixtures/kalshi_tags_by_categories.json` -- the category names themselves are exactly the
documented shape (`Sports` is the live name, unchanged from the addendum's assumption). Discovery
unwraps that key when present and otherwise reads the body as the category map directly, which is
also what `tests/test_futures_snapshot.py`'s `FakeKalshi` returns, so one code path serves both.

**The budget** binds the pass, not the season (ruling, fix round 1: raised from the addendum's
200 to **500**). 458 football series survive discovery on the recorded fixture, one request each
in the common case, so 200 covered only ~198 of them -- a series was resnapshotted every third
Tuesday, not every one, which is not week-over-week drift. 500 covers a full pass (~460 requests)
with slack, still about 2.5 minutes of free reads at the unchanged 0.1 s page pause. The budget is
counted per **request** (a categories read, a series page, a markets page), checked before every
one of them, not once per series -- a multi-page series can no longer run past the budget before
the check catches up.

**A non-200 response stops the pass** (fix round 1, Important 2). The transport already retries a
429 once, honouring `Retry-After`, and returns whatever comes back as an ordinary `FetchResult`;
a page like that was previously read as empty. Reading `page.status` on every categories, series
and markets response means a refusal is never silently coverage -- see `_VenueHttpError` below.
This is distinct from a raised exception (a connection failure, a timeout): that still costs only
the one series and the pass continues, exactly as before.

**No Odds credits are spent here.** Every call is a free public Kalshi read.
"""
import json
import logging
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from harness.db.models import FuturesSnapshot, JobRun, JobState
from harness.recorder import store
from harness.research.text import sanitize_model_text
from harness.venues.kalshi.public import FOOTBALL_SERIES, decode_fixed_point

log = logging.getLogger(__name__)

#: The two football families H7 is about. A prefix test on the *series* ticker, which is the
#: coarse filter; the exact exclusion below is the fine one.
FUTURES_PREFIXES = ("KXNFL", "KXNCAAF")
#: Ruling, fix round 1: 500 requests per pass (was the addendum's 200 -- see the module
#: docstring), 0.1 s between pages, counted per request rather than per series.
REQUEST_BUDGET = 500
PAGE_PAUSE_S = 0.1
JOB_NAME = "futures"
#: `job_state.value` is BigInteger, so the resume point is the index into the deterministic
#: order and the ticker it stood for is recorded in `job_runs.notes.resume_after` (ruling A-I8).
RESUME_KEY = "futures.resume"
#: How a football category is recognised in `tags_by_categories`. Case-insensitive substring: the
#: category's display name is the venue's and it may be renamed between seasons, so a pass that
#: matches nothing falls back to every category rather than to an empty panel.
CATEGORY_HINT = "sport"
#: The live response wraps the category map under this key (Step 1, 2026-09-10); a hand-built
#: test body has no such key and is read directly.
_CATEGORIES_WRAPPER_KEY = "tags_by_categories"
#: Column widths, applied at write. Venue free text (ruling B-M8, F60).
_TITLE_MAX = 256
_SUBTITLE_MAX = 200
#: `job_runs.notes.errors`' body excerpt, per fix round 1 Important 2.
_BODY_EXCERPT_MAX = 120


class _VenueHttpError(Exception):
    """A non-200 Kalshi response during the futures pass (fix round 1, Important 2). Raised by
    `discover_series` and read for the same purpose in the `/markets` loop below; caught at the
    top level of `run_futures_snapshot` and turned into a stopped pass, never an empty page."""

    def __init__(self, endpoint: str, page):
        self.endpoint = endpoint
        self.status = page.status
        self.body = page.body
        super().__init__(f"{endpoint} -> {page.status}")


def _body_excerpt(body) -> str:
    if body is None:
        return ""
    text = body if isinstance(body, str) else json.dumps(body, default=str)
    return text[:_BODY_EXCERPT_MAX]


def _http_error_note(endpoint: str, status: int, body) -> dict:
    """One `job_runs.notes.errors` entry for a non-200 response. `rate_limited` marks a 429
    specifically -- the transport already retried it once, so a second 429 here is the venue
    still saying no, not a transient blip to read through."""
    note = {"endpoint": endpoint, "status": status, "body": _body_excerpt(body)}
    if status == 429:
        note["rate_limited"] = True
    return note


def snapshot_week(now: datetime, tz: str) -> str:
    """The ISO week the pass ran in, in local time: `2026-W38`. Local, because the pass is a
    Tuesday-morning-in-Louisiana event and the panel's calendar is stated in CT."""
    local = now.astimezone(ZoneInfo(tz))
    iso = local.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def discover_series(session: Session, run_id: int, kalshi, ctx: dict) -> list[str]:
    """Every football futures or ladder series ticker, sorted. Costs one categories read plus
    one series read per category kept.

    Every body is stored through `store_raw(source='kalshi_futures')`, the categories read once
    per pass (addendum §1.1): discovery is two thirds of a pass's requests, and a discovery that
    stored nothing would leave H7's panel unauditable against the venue's own answer.
    """
    categories_result = kalshi.fetch_tags_by_categories()
    ctx["n"] += 1
    store.store_raw(session, run_id, "kalshi_futures", "/search/tags_by_categories", {},
                    categories_result)
    if categories_result.status != 200:
        # Important 2: an unreadable categories body must not fall through to "walk all zero
        # categories" -- a silent empty panel that used to report `ok`.
        raise _VenueHttpError("/search/tags_by_categories", categories_result)
    raw = categories_result.body if isinstance(categories_result.body, dict) else {}
    wrapped = raw.get(_CATEGORIES_WRAPPER_KEY)
    body = wrapped if isinstance(wrapped, dict) else raw
    names = sorted(body)
    kept = [name for name in names if CATEGORY_HINT in name.lower()]
    ctx["category_fallback"] = not kept
    if not kept:
        log.warning("no category matched %r; walking all %d categories", CATEGORY_HINT,
                    len(names))
        kept = names
    ctx["categories"] = kept

    tickers: set[str] = set()
    for category in kept:
        for page in kalshi.fetch_series_all(category):
            ctx["n"] += 1
            store.store_raw(session, run_id, "kalshi_futures", "/series",
                            {"category": category}, page)
            if page.status != 200:
                raise _VenueHttpError("/series", page)
            page_body = page.body if isinstance(page.body, dict) else {}
            for series in page_body.get("series") or []:
                ticker = (series or {}).get("ticker")
                if not isinstance(ticker, str):
                    continue
                if not ticker.startswith(FUTURES_PREFIXES):
                    continue
                if ticker in FOOTBALL_SERIES:      # exact, never a prefix (ruling A-I9)
                    continue
                tickers.add(ticker)
    return sorted(tickers)


def parse_futures_markets(body, series_ticker: str) -> list[dict]:
    """One `GET /markets` page as normalized rows. Never raises on a shape it did not expect: a
    market without a ticker is dropped and the rest of the page is kept.

    `series_ticker` is the series the caller is walking -- the loop already knows it, and it is
    passed in rather than re-derived from `event_ticker.split("-")[0]` (fix round 1, Important 1):
    that derivation breaks for every hyphenated series, which is every `KXNFL*WINS-<TEAM>`
    win-total ladder -- exactly the "ladders" half of H7 -- collapsing all of them into one
    `KXNFLWINS` group.
    """
    if not isinstance(body, dict):
        return []
    markets = body.get("markets")
    if not isinstance(markets, list):
        return []
    rows: list[dict] = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        ticker = market.get("ticker")
        if not isinstance(ticker, str) or not ticker:
            continue
        event_ticker = str(market.get("event_ticker") or "")
        rows.append({
            "market_ticker": ticker,
            "event_ticker": event_ticker,
            "series_ticker": series_ticker,
            # The venue's own enum, under its own name: `market_type` in Kalshi's vocabulary is
            # binary|scalar and has nothing to do with the harness's moneyline|spread|total.
            "kalshi_market_type": str(market.get("market_type") or ""),
            "strike_type": str(market.get("strike_type") or "") or None,
            "title": sanitize_model_text(market.get("title"), _TITLE_MAX) or None,
            "yes_sub_title": sanitize_model_text(market.get("yes_sub_title"),
                                                 _SUBTITLE_MAX) or None,
            "floor_strike": _number(market.get("floor_strike")),
            "cap_strike": _number(market.get("cap_strike")),
            "yes_bid": decode_fixed_point(market.get("yes_bid_dollars")),
            "yes_ask": decode_fixed_point(market.get("yes_ask_dollars")),
            "last_price": decode_fixed_point(market.get("last_price_dollars")),
            "volume": decode_fixed_point(market.get("volume_fp")),
            "open_interest": decode_fixed_point(market.get("open_interest_fp")),
            "close_time": _ts(market.get("close_time")),
        })
    return rows


def _number(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - a venue number we cannot read is a null, never a raise
        return None


def _ts(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def run_futures_snapshot(session: Session, settings, kalshi, now: datetime, trigger: str,
                         request_budget: int = REQUEST_BUDGET) -> JobRun:
    """One pass. Writes a `job_runs` row whatever happens, so the Tuesday duty always has
    something to read."""
    if trigger not in ("cron", "manual"):
        raise ValueError(f"unknown futures trigger {trigger!r}")
    # The pass sets the client's own pause rather than sleeping itself, so the 0.1 s falls
    # between *pages* inside `fetch_markets_all` too, not only between series. Safe because every
    # caller (`harness/cli.py`'s `_futures` and `futures snapshot`) builds this client for this
    # pass alone; handing in a shared client would repace that client for good.
    kalshi._sleep_s = PAGE_PAUSE_S
    job = JobRun(job=JOB_NAME, started_at=now, status="running", notes={})
    session.add(job)
    session.flush()

    run_id = job.id
    week = snapshot_week(now, settings.tz_local)
    ctx: dict = {"n": 0, "errors": [], "categories": []}
    reached: list[str] = []
    resume_after: str | None = None
    resumed_from: str | None = None
    resume_reset = False
    exhausted = False

    try:
        series = discover_series(session, run_id, kalshi, ctx)
    except _VenueHttpError as exc:
        # Important 2: a non-200 during discovery stops the pass outright -- it is the venue
        # explicitly refusing, never an empty panel reported as `ok`.
        log.warning("futures discovery http error %s -> %s", exc.endpoint, exc.status)
        job.status, job.finished_at = "error", now
        job.notes = {"trigger": trigger, "week": week, "requests": ctx["n"],
                     "categories": ctx["categories"],
                     "category_fallback": bool(ctx.get("category_fallback")),
                     "series_discovered": 0, "series_reached": [],
                     "resume_after": None, "resumed_from": None, "resume_reset": False,
                     "errors": [_http_error_note(exc.endpoint, exc.status, exc.body)]}
        session.flush()
        return job
    except Exception as exc:  # noqa: BLE001 - a discovery failure is a recorded, finished pass
        log.exception("futures discovery failed")
        job.status, job.finished_at = "error", now
        job.notes = {"trigger": trigger, "week": week, "requests": ctx["n"],
                     "errors": [{"discovery": type(exc).__name__}], "series_reached": []}
        return job

    start = 0
    state = session.get(JobState, RESUME_KEY)
    if state is not None and state.value:
        index = int(state.value)
        previous = (session.query(JobRun).filter(JobRun.job == JOB_NAME, JobRun.id != job.id)
                    .order_by(JobRun.id.desc()).first())
        expected = ((previous.notes or {}).get("resume_after")) if previous else None
        if 0 <= index < len(series) and expected is not None and series[index - 1] == expected:
            start, resumed_from = index, expected
        else:
            resume_reset = True

    def _store_resume(index: int) -> None:
        if state is None:
            session.add(JobState(key=RESUME_KEY, value=index, updated_at=now))
        else:
            state.value, state.updated_at = index, now

    ordered = series[start:] + series[:start]
    for ticker in ordered:
        if ctx["n"] >= request_budget:
            exhausted = True
            break
        try:
            pages = kalshi.fetch_markets_all(ticker, status="open",
                                             extra_params={"mve_filter": "exclude"})
        except Exception as exc:  # noqa: BLE001 - one series's transport failure must not cost
                                   # the pass; distinct from a non-200, which does (see below).
            log.exception("futures series %s failed", ticker)
            ctx["errors"].append({ticker: type(exc).__name__})
            continue

        series_complete = True
        for page in pages:
            # Ruling, fix round 1: the budget is checked per request, not once per series -- a
            # multi-page series can no longer run past it before the check catches up.
            if ctx["n"] >= request_budget:
                exhausted, series_complete = True, False
                break
            ctx["n"] += 1
            store.store_raw(session, run_id, "kalshi_futures", "/markets",
                            {"series_ticker": ticker, "week": week}, page)
            if page.status != 200:
                # Important 2: a non-200 markets page stops the whole pass, not just this
                # series -- `series_reached` keeps only series whose pages all returned 200, and
                # no row is written from this page.
                ctx["errors"].append(_http_error_note("/markets", page.status, page.body))
                _store_resume((series.index(resume_after) + 1) if resume_after in series else 0)
                job.finished_at = now
                job.status = "error"
                job.notes = {"trigger": trigger, "week": week, "requests": ctx["n"],
                             "categories": ctx["categories"],
                             "category_fallback": bool(ctx.get("category_fallback")),
                             "series_discovered": len(series), "series_reached": reached,
                             "resume_after": resume_after, "resumed_from": resumed_from,
                             "resume_reset": resume_reset, "errors": ctx["errors"]}
                session.flush()
                return job
            for row in parse_futures_markets(page.body, ticker):
                session.add(FuturesSnapshot(run_id=run_id, snapshot_week=week,
                                            fetched_at=page.fetched_at, **row))
        if exhausted:
            break
        if series_complete:
            reached.append(ticker)
            resume_after = ticker

    # Important 3: a pass that finishes the whole panel stores 0, never `len(series)` -- the
    # latter reads back as an out-of-range resume index and falsely flags `resume_reset` on the
    # very next pass, which should mean "the discovery set changed", not "we finished on time".
    resume_index = 0
    if exhausted:
        resume_index = (series.index(resume_after) + 1) if resume_after in series else 0
    _store_resume(resume_index)

    job.finished_at = now
    job.budget_exhausted = exhausted
    job.status = "degraded" if ctx["errors"] else "ok"
    job.notes = {"trigger": trigger, "week": week, "requests": ctx["n"],
                 "categories": ctx["categories"],
                 "category_fallback": bool(ctx.get("category_fallback")),
                 "series_discovered": len(series), "series_reached": reached,
                 "resume_after": resume_after, "resumed_from": resumed_from,
                 "resume_reset": resume_reset, "errors": ctx["errors"]}
    session.flush()
    log.info("futures pass %s week=%s series=%d/%d requests=%d", job.status, week,
             len(reached), len(series), ctx["n"])
    return job
