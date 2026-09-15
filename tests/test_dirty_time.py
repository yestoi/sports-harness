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
from harness.execution.book import BookState
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
    executor._market_ids = {}
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


def test_a_market_that_leaves_the_step_closes_both_of_its_rows(db_session):
    """Expected: both rows closed at the last observation that saw the market, and none left
    open (§2's `market_observation_intervals` invariant, review I-6).

    Derived independently: a market's last order closes and the market leaves the working set.
    Nothing will ever look at it again, so an open row would say "still dirty" for the rest of
    the season and `order_dirty_time` would clamp it to every later order's deadline. The close
    is stamped at `now` of the step that noticed, which is the last instant anyone observed it.
    """
    executor = _executor_with_books({"A": _dirty_book("gap")})
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(0), dead_recorder=False)
    executor._advance_books(db_session, set(), {"A": 1}, at(30), dead_recorder=False)

    assert [r.ended_at for r in _intervals(db_session, MarketDirtyInterval)] == [at(30)]
    assert [r.ended_at for r in _intervals(db_session, MarketObservationInterval)] == [at(30)]
    assert _open_count(db_session) == 0


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
    book cannot know. It is not in `DIRTY_CAUSES` for that reason, and a book that already
    named a cause of its own keeps it: the first cause wins, here as in `mark_dirty`.
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
    this way. `gone` is now read off the *previous* step's ticker map, so a market that
    disappears from both sets at once still closes its dirty and observation intervals,
    stamped at the last step that saw it.
    """
    executor = _executor_with_books({"A": _dirty_book("gap")})
    executor._advance_books(db_session, {"A"}, {"A": 1}, at(0), dead_recorder=False)
    executor._advance_books(db_session, set(), {}, at(30), dead_recorder=False)

    assert [r.ended_at for r in _intervals(db_session, MarketDirtyInterval)] == [at(30)]
    assert [r.ended_at for r in _intervals(db_session, MarketObservationInterval)] == [at(30)]
    assert _open_count(db_session) == 0
