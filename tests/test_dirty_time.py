"""`order_dirty_time`: elapsed dirty and unobserved seconds, derived rather than accrued.

The gate-read columns keep their nominal accrual (ruling I-4, §0.13c is the user's). These are
the parallel elapsed quantities, intersected at read time from the two interval tables, so
either answer to §0.13c is adoptable with no further code.

One bounded parameterised query, never a view (ruling I-8): `id > :boundary_order_id` with a
`limit`, expected to return inside 2 s over the ~8,800-order table.

The second half of the file is the writer's side: `_advance_books` opening and closing the two
interval tables, which is where every row the query reads comes from.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from harness.db.models import MarketDirtyInterval, MarketObservationInterval, Order
from harness.execution import store
from harness.execution.book import DIRTY_CAUSES, BookState
from harness.execution.dirty_time import order_dirty_time
from harness.execution.loop import Executor

T0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.fixture
def seeded_order(db_session):
    """One `orders` row with the columns `order_dirty_time` reads, built the way
    `tests/test_capsule.py:_seed_order` builds one: every `nullable=False` column present, no
    dependent rows (this query joins only `fills`, which each case seeds itself when it needs
    one). Returns the id so a case can bound the read at `order_id - 1`."""
    counter = iter(range(1, 1000))

    def make(**over):
        n = next(counter)
        values = dict(intent_id=uuid.UUID(int=n), variant_id="v1", venue="kalshi",
                      client_order_id=f"client-{n}", ticker="A", venue_market_id=1,
                      side="yes", prob=Decimal("0.30"), contracts=Decimal(10),
                      status="open", placed_at=T0, replay=False)
        values.update(over)
        order = Order(**values)
        db_session.add(order)
        db_session.flush()
        return order.id

    return make


def _interval(session, model, *, started, ended, cause=None):
    values = dict(venue_market_id=1, ticker="A", started_at=started, ended_at=ended,
                  replay=False)
    if cause is not None:
        values["cause"] = cause
    session.add(model(**values))
    session.flush()


def test_the_watched_and_counterfactual_windows_are_measured_separately(db_session, seeded_order):
    """Expected `watched_dirty_s` 30 and `counterfactual_dirty_s` 90.

    Derived independently: the order is placed at T0 and cancelled at T+60; its expiry is
    T+600. The market is dirty from T+30 to T+120. The watched interval is
    `[placed_at, min(cancelled_at, expiry)]` = [T0, T+60], whose intersection with [T+30, T+120]
    is 30 seconds. The counterfactual interval is `[placed_at, expiry]` = [T0, T+600], whose
    intersection with the same stretch is the whole 90 seconds -- F3's counterfactual keeps
    running whatever we did, which is why order 157 took fills two days after its cancel.
    """
    order_id = seeded_order(placed_at=T0, cancelled_at=at(60), expiry=at(600),
                            status="cancelled")
    _interval(db_session, MarketDirtyInterval, started=at(30), ended=at(120), cause="gap")
    _interval(db_session, MarketObservationInterval, started=T0, ended=at(600))
    row = order_dirty_time(db_session, at(3600), boundary_order_id=order_id - 1, limit=10)[0]
    assert row.order_id == order_id
    assert row.watched_dirty_s == 30
    assert row.counterfactual_dirty_s == 90
    assert row.by_cause == {"gap": 90}


def test_unobserved_time_is_reported_and_never_folded_into_clean_time(db_session, seeded_order):
    """Expected `unobserved_s` 400, and the clean total is the observed remainder only.

    Derived independently: the same order, with the executor's observation of this market
    ending at T+200. The counterfactual interval runs to T+600, so 400 seconds of it were never
    stepped at all. Absence of a row means "not observed", not "observed clean" (ruling IM-15):
    a market nobody looked at is not evidence of a clean book, and folding the two together
    would make a starved host look like a healthy one.
    """
    order_id = seeded_order(placed_at=T0, cancelled_at=at(60), expiry=at(600),
                            status="cancelled")
    _interval(db_session, MarketDirtyInterval, started=at(30), ended=at(120), cause="gap")
    _interval(db_session, MarketObservationInterval, started=T0, ended=at(200))
    row = order_dirty_time(db_session, at(3600), boundary_order_id=order_id - 1, limit=10)[0]
    assert row.unobserved_s == 400
    assert row.counterfactual_dirty_s == 90


def test_a_still_open_interval_is_clamped_to_the_deadline(db_session, seeded_order):
    """Expected: a null `ended_at` contributes only up to `least(now, deadline)`.

    Derived independently: an open interval means "still dirty as of the last observation", and
    a market whose last order closes while dirty leaves one open. Counting it to `now` would let
    a stretch that began before an order expired go on accruing against that order forever.
    """
    order_id = seeded_order(placed_at=T0, cancelled_at=at(60), expiry=at(120),
                            status="cancelled")
    _interval(db_session, MarketDirtyInterval, started=at(30), ended=None, cause="gap")
    _interval(db_session, MarketObservationInterval, started=T0, ended=None)
    row = order_dirty_time(db_session, at(3600), boundary_order_id=order_id - 1, limit=10)[0]
    assert row.counterfactual_dirty_s == 90       # T+30 to the expiry at T+120
    assert row.watched_dirty_s == 30              # T+30 to the cancel at T+60


# --- the writer: one open row per market per table, closed when the market leaves ------------

def _dirty_book(cause: str) -> BookState:
    book = BookState.from_levels("A", [[".30", "5"]], [[".60", "5"]], sid=1, seq=1,
                                 as_of=T0, source="ws", anchor_id=1)
    book.mark_dirty(cause)
    return book


def _executor_with_books(books: dict) -> Executor:
    """An `Executor` with only what `_advance_books` reads, and a `_book_now` that hands the
    cached book straight back: the case is about the interval bookkeeping, not about the tape.
    """
    executor = Executor.__new__(Executor)
    executor.books = dict(books)
    executor.replay = False
    executor.settings = NS(exec_period_s=15)
    executor.exec_settings = NS(book_max_age_s=120)
    executor._dirty_tickers = set()
    executor._book_now = lambda session, ticker, now, cached, ws_connect_at=None: cached
    return executor


def _intervals(session, model) -> list:
    return session.query(model).order_by(model.id).all()


def _open_count(session) -> int:
    return sum(len([r for r in _intervals(session, model) if r.ended_at is None])
               for model in (MarketDirtyInterval, MarketObservationInterval))


def test_a_step_opens_an_observation_row_and_a_dirty_row_with_the_books_cause(db_session):
    """Expected: one open observation row and one open dirty row with cause `gap`, and no
    second row when the next step sees the same market still dirty.

    Derived independently from the invariant, not from the writer: §2 requires at most one open
    row per market per table, so a step that opened a fresh row every loop would break the
    invariant on the second loop of every dirty stretch. An interval is one contiguous stretch,
    which means "open if not already open" is the whole write.
    """
    executor = _executor_with_books({"A": _dirty_book("gap")})
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(0), dead_recorder=False)
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(15), dead_recorder=False)

    dirty = _intervals(db_session, MarketDirtyInterval)
    observed = _intervals(db_session, MarketObservationInterval)
    assert [(r.cause, r.ended_at) for r in dirty] == [("gap", None)]
    assert [r.ended_at for r in observed] == [None]


def test_a_market_named_in_market_ids_but_absent_from_tickers_is_not_treated_as_gone(db_session):
    """Expected: neither row closes, because the market is still named in this step's
    `market_ids` (journal 224 item 4: `gone` is now database-derived, judged against
    `market_ids` rather than against a map carried across steps).

    This hands the departed ticker's id into `market_ids` at the departing step, a shape
    production never produces (`tickers` and `market_ids` are always built from the same
    `rows`, so a ticker absent from one is absent from both). Before the fix 70 leak was closed,
    this shape existed to guard the close itself, independent of the row 70 fix
    (`test_a_market_absent_from_both_sets_at_the_departing_step_still_closes` below is that);
    now that `gone` reads the database directly and excludes whatever this step's own
    `market_ids` still names, the same shape demonstrates the new rule instead: membership is
    judged by `market_ids`, not by `tickers`, and production's invariant is exactly why that
    never matters there.
    """
    executor = _executor_with_books({"A": _dirty_book("gap")})
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(0), dead_recorder=False)
    executor._advance_books(db_session, set(), {"A": 1}, at(30), dead_recorder=False)

    assert [r.ended_at for r in _intervals(db_session, MarketDirtyInterval)] == [None]
    assert [r.ended_at for r in _intervals(db_session, MarketObservationInterval)] == [None]
    assert _open_count(db_session) == 2


def test_a_market_that_stops_being_dirty_closes_its_dirty_row_and_keeps_observing(db_session):
    """Expected: the dirty row closed at the clean step, the observation row still open.

    Derived independently from what the two tables mean: the market is still being stepped, so
    observation is continuous and its row must not be broken; the book is trustworthy again, so
    the dirty stretch ended. Closing both would say the executor stopped looking at a market it
    is looking at every 15 s, and `unobserved_s` would then count clean, observed time.
    """
    clean = BookState.from_levels("A", [[".30", "5"]], [[".60", "5"]], sid=1, seq=1,
                                  as_of=T0, source="ws", anchor_id=1)
    executor = _executor_with_books({"A": _dirty_book("gap")})
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(0), dead_recorder=False)
    executor.books["A"] = clean
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(15), dead_recorder=False)

    assert [(r.cause, r.ended_at) for r in _intervals(db_session, MarketDirtyInterval)] == [
        ("gap", at(15))]
    assert [r.ended_at for r in _intervals(db_session, MarketObservationInterval)] == [None]


def test_a_dead_recorder_is_its_own_cause_on_a_book_that_names_none(db_session):
    """Expected cause `recorder_dead` on a market whose own book is clean.

    Derived independently: `recorder_dead` is the loop's verdict about the recorder rather than
    the book's about itself -- every ladder is stale because nothing is writing them, which the
    book cannot know. It is in `DIRTY_CAUSES` (amendment 0.19, journal 224 item 8: that tuple is
    the single vocabulary) but never passed to `mark_dirty` for the same reason it never was
    before, and a book that already named a cause of its own keeps it: the first cause wins,
    here as in `mark_dirty`.
    """
    clean = BookState.from_levels("A", [[".30", "5"]], [[".60", "5"]], sid=1, seq=1,
                                  as_of=T0, source="ws", anchor_id=1)
    executor = _executor_with_books({"A": clean})
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(0), dead_recorder=True)
    assert [r.cause for r in _intervals(db_session, MarketDirtyInterval)] == ["recorder_dead"]

    executor = _executor_with_books({"B": _dirty_book("session_boundary")})
    executor._advance_books(db_session, {"B"}, {"B": 2}, at(0), dead_recorder=True)
    assert [r.cause for r in _intervals(db_session, MarketDirtyInterval)
            if r.venue_market_id == 2] == ["session_boundary"]


def test_a_tape_silent_past_book_max_age_s_opens_an_event_age_row(db_session):
    """Expected: a quiet ticker with a live recorder and a clean book opens a dirty row with
    cause `event_age`, and is not written into the clean set; a step after a fresh event closes
    it.

    Derived independently from what the measure is for, not from the writer: `MarketNow.dirty`
    is true three ways, and the third is "this ticker's own newest row is older than
    `book_max_age_s`" (spec F4). `_simulate_order` accrues nominal dirty seconds on that route
    every loop, so the elapsed parallel (ruling I-4) has to record the same stretch; writing it
    into `clean` instead would say the stretch was observed *and* trustworthy, which is the
    direction ruling IM-15 forbids. The book cannot mark itself: nothing applied a row, so
    `as_of` simply stopped moving.
    """
    quiet = BookState.from_levels("A", [[".30", "5"]], [[".60", "5"]], sid=1, seq=1,
                                  as_of=T0, source="ws", anchor_id=1)
    executor = _executor_with_books({"A": quiet})
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(300), dead_recorder=False)

    assert [(r.cause, r.ended_at) for r in _intervals(db_session, MarketDirtyInterval)] == [
        ("event_age", None)]
    assert [r.ended_at for r in _intervals(db_session, MarketObservationInterval)] == [None]

    # A row lands on the tape at T+300, so the next step's book is 15 s old: the stretch ended.
    executor.books["A"] = BookState.from_levels("A", [[".30", "5"]], [[".60", "5"]], sid=1,
                                                seq=2, as_of=at(300), source="ws", anchor_id=1)
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(315), dead_recorder=False)

    assert [(r.cause, r.ended_at) for r in _intervals(db_session, MarketDirtyInterval)] == [
        ("event_age", at(315))]
    assert [r.ended_at for r in _intervals(db_session, MarketObservationInterval)] == [None]


def test_book_unreadable_is_its_own_cause_and_flows_into_the_by_cause_breakdown(
        db_session, seeded_order):
    """Row 69 (6B merge review, carried item 2): a ticker unreadable this step while its cached
    book is still fresh is `book_dirty` for `MarketNow` (fix 60's guard), which makes
    `MarketNow.dirty` true, but `_advance_books` opened no interval row for it at all -- no
    cause named "could not read" existed, so the interval ledger under-reported it.
    `book_unreadable` is the loop's own verdict, like `recorder_dead`, not a `BookState`
    self-diagnosis: the failed read never touches the cached book, which stays clean. It feeds
    `order_dirty_time`'s by-cause breakdown exactly like any other cause, with no change to
    that query -- the breakdown is grouped by whatever string `cause` holds.
    """
    clean = BookState.from_levels("A", [[".30", "5"]], [[".60", "5"]], sid=1, seq=1,
                                  as_of=T0, source="ws", anchor_id=1)
    executor = _executor_with_books({"A": clean})
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(0), dead_recorder=False)
    assert _intervals(db_session, MarketDirtyInterval) == []

    def explode(session, ticker, now, cached, ws_connect_at=None):
        raise ValueError("snapshot without yes_dollars_fp")

    executor._book_now = explode
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(15), dead_recorder=False)
    rows = _intervals(db_session, MarketDirtyInterval)
    assert [(r.cause, r.ended_at) for r in rows] == [("book_unreadable", None)]
    # The cached book that stayed fresh is untouched: `book_unreadable` is the loop's own
    # verdict, not a mark on the book.
    assert executor.books["A"].dirty_cause is None

    order_id = seeded_order(placed_at=T0, expiry=at(600))
    breakdown = order_dirty_time(db_session, at(30), boundary_order_id=order_id - 1, limit=10)
    assert breakdown[0].by_cause == {"book_unreadable": 15}


def test_a_market_absent_from_both_sets_at_the_departing_step_still_closes(db_session):
    """Row 70 (6B merge review, out-of-scope observation): production always builds `tickers`
    and `market_ids` from the same `rows` at the call site, so a departed market's ticker is
    absent from *both* at the step that no longer sees it -- unlike the older test above, which
    hands the departed ticker's id into `market_ids` anyway and so never exercised the real
    shape. `gone` used to be read off the *current* step's `market_ids`, which can never
    contain a ticker not in `tickers`, so it was always empty and neither `close_intervals`
    call ever fired: two production rows (markets 865/866, journal 219) were left open forever
    this way. `gone` is now read from the database (the open `market_observation_intervals`
    rows for this replay flag, minus this step's own `market_ids`), so a market that disappears
    from both sets at once still closes its dirty and observation intervals, stamped at `now`
    of the step that noticed it was gone -- the same instant a clean market's close carries.
    """
    executor = _executor_with_books({"A": _dirty_book("gap")})
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(0), dead_recorder=False)
    executor._advance_books(db_session, set(), {}, at(30), dead_recorder=False)

    assert [r.ended_at for r in _intervals(db_session, MarketDirtyInterval)] == [at(30)]
    assert [r.ended_at for r in _intervals(db_session, MarketObservationInterval)] == [at(30)]
    assert _open_count(db_session) == 0


def test_a_market_whose_first_read_raises_still_closes_its_observation_row_on_departure(
        db_session):
    """Review round 1, I-1: `self.books` is not the set of markets with an open interval row.
    A ticker whose read *raises* on its very first step never becomes a key of `self.books` at
    all -- the cache assignment lives inside the `try`, only reached on success -- while its
    observation row still opened unconditionally at the call below `market_ids` had its id.
    Deriving `gone` from `set(self.books) - tickers` therefore misses exactly this market when
    it later departs, and the row stays open forever (reviewer probe on the previous fix:
    `open_rows=1` after departure). `gone` is now read from the database itself (journal 224
    item 4, the fix 70 leak): the open `market_observation_intervals` rows for this replay flag,
    minus this step's own `market_ids`, so it does not matter whether the failed read -- or any
    later step -- ever touched an in-memory map at all.
    """
    executor = _executor_with_books({})

    def explode(session, ticker, now, cached, ws_connect_at=None):
        raise ValueError("no anchor yet")

    executor._book_now = explode
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(0), dead_recorder=False)

    # The failed read never enters the book cache, and a ticker with no book at all opens no
    # dirty row (review checks-cleared 1): only the observation row is at stake here.
    assert executor.books == {}
    assert [r.ended_at for r in _intervals(db_session, MarketObservationInterval)] == [None]
    assert _intervals(db_session, MarketDirtyInterval) == []

    executor._advance_books(db_session, set(), {}, at(30), dead_recorder=False)

    assert [r.ended_at for r in _intervals(db_session, MarketObservationInterval)] == [at(30)]
    assert _open_count(db_session) == 0


def test_a_step_earlier_than_an_open_rows_started_at_leaves_it_open(db_session):
    """Review batch A, I-1: `open_interval_market_ids` used to return every open row for this
    replay flag with no time bound, so a step whose own `now` is earlier than an already-open
    row's `started_at` -- a second replay over the same window (`harness/replay.py`'s
    documented case), or a backwards host clock jump (the 2026-09-13 Omarchy RTC reset was
    exactly this shape) -- named that row `gone` and closed it at an instant before it opened,
    `ended_at < started_at`, which `docs/superpowers/autopilot/verify.md:565` counts as a FAIL.
    `open_interval_market_ids` is now bounded on `started_at <= now`, the step's own instant, so
    a row that had not started yet as of this step's `now` is never a candidate for `gone` and
    stays open, in both tables, until a step at or after its `started_at` sees it depart.

    `db_session.expire_all()` between the two steps matters here: the bulk `UPDATE` behind
    `close_intervals` does not always refresh an ORM instance already loaded into this same
    session's identity map (its `synchronize_session` strategy can silently skip the in-memory
    object), so a naive read-after-write here can see the stale, still-open Python object even
    though the row underneath was wrongly closed -- expiring forces the second read to come from
    the database, the same thing that matters in production.
    """
    executor = _executor_with_books({"A": _dirty_book("gap")})
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(30), dead_recorder=False)

    dirty = _intervals(db_session, MarketDirtyInterval)
    observed = _intervals(db_session, MarketObservationInterval)
    assert [(r.started_at, r.ended_at) for r in dirty] == [(at(30), None)]
    assert [(r.started_at, r.ended_at) for r in observed] == [(at(30), None)]
    db_session.expire_all()

    # A step at an instant earlier than A's started_at, naming nothing: without the bound this
    # would close both rows at at(0), stamping ended_at < started_at.
    executor._advance_books(db_session, set(), {}, at(0), dead_recorder=False)
    db_session.expire_all()

    dirty = _intervals(db_session, MarketDirtyInterval)
    observed = _intervals(db_session, MarketObservationInterval)
    assert [(r.started_at, r.ended_at) for r in dirty] == [(at(30), None)]
    assert [(r.started_at, r.ended_at) for r in observed] == [(at(30), None)]


def test_dirty_causes_is_the_single_vocabulary(db_session):
    """Journal 224 item 8 (amendment 0.19): every cause the loop writes -- the book's own four
    plus its two loop-only verdicts, `recorder_dead` and `book_unreadable` -- is a member of
    `DIRTY_CAUSES`, and `open_interval` refuses to write anything else.

    The book's own causes are asserted against `mark_dirty`'s callers indirectly, by asserting
    the full set the loop is documented to write is exactly what `DIRTY_CAUSES` holds; the
    refusal is asserted directly, against the site the ruling names: rows are written through
    `store.open_interval`, not only through `BookState.mark_dirty`, so the vocabulary has to be
    enforced there too.
    """
    loop_written = {"gap", "session_boundary", "event_age", "malformed_row",
                    "recorder_dead", "book_unreadable"}
    assert loop_written == set(DIRTY_CAUSES)

    with pytest.raises(ValueError, match="bogus"):
        store.open_interval(db_session, "market_dirty_intervals", 1, "A", T0, False,
                            cause="bogus")
