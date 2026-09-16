import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from harness import telemetry
from harness.db.models import OrderbookEvent, VenueTrade
from harness.normalize.kalshi import taker_side_of, truncate_ms

log = logging.getLogger(__name__)

# Fix 77 (roadmap row 77, journal 235). The venue spends one `seq` number of a subscription on
# every `update_subscription` frame the recorder sends on it -- on 2026-09-15 each plan that
# changed the wanted set was booked as a lost message (`expected=6956 got=6957`) and each
# `_recover_gap` pair as two (`expected=6962 got=6964`). The recorder therefore tells the sink
# how many advances it has just paid for (`expect_advances`), and the sink spends that
# allowance instead of writing a gap row. Two bounds keep the allowance from ever hiding a
# real loss: it is never more than the frames actually sent (capped), and it is dropped if the
# venue has not spent it within `SEQ_ADVANCE_TTL_S`. Production put ~130 ms and four frames
# between a recovery pair and the seq numbers it spent, so the window is seconds, not minutes.
SEQ_ADVANCE_TTL_S = 10.0
MAX_PENDING_ADVANCES = 8


def _is_int(v) -> bool:
    """A JSON `true` is an `int` in Python; a frame carrying one is malformed, not a seq."""
    return isinstance(v, int) and not isinstance(v, bool)


def _dec(v):
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except InvalidOperation:
        return None


def _ts(ms, fallback: datetime) -> datetime:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return fallback


class WsSink:
    def __init__(self, session_factory: sessionmaker, commit_every: int = 100, commit_interval_s: float = 2.0,
                 offset_ms: int = 0, advance_ttl_s: float = SEQ_ADVANCE_TTL_S):
        self._factory = session_factory
        # The recorder's clock offset to Kalshi's server, refreshed at every connect and
        # written onto every snapshot row (see `handle`). Public: the recorder assigns it.
        self.offset_ms = offset_ms
        self._session = session_factory()
        self._pending = 0
        self._last_commit = time.monotonic()
        self._commit_every, self._interval = commit_every, commit_interval_s
        self._last_seq: dict[int, int] = {}
        # Fix 77: per sid, how many seq numbers the recorder's own `update_subscription` frames
        # have bought and the monotonic instant that allowance expires at: {sid: (count, deadline)}.
        self._advances: dict[int, tuple[int, float]] = {}
        self._advance_ttl_s = advance_ttl_s
        # Fix 77 telemetry, drained by `drain_counts` with the other per-minute counters
        # (round 1, Minor 4): seq numbers accounted for as subscription updates rather than
        # gaps, and non-data frames whose seq did not fit the chain and were ignored.
        self._advances_since = 0
        self._acks_out_since = 0
        # Which subscriptions have rows in the batch right now. `_pending` is one count for the
        # whole batch, but the batch spans every sid that landed a row since the last commit and
        # a rollback discards all of them, so the exception mark needs the set as well as the
        # count. It is filled next to each `_pending` increment, never at the top of `handle`: a
        # frame that is dropped or deduplicated leaves nothing at risk on its subscription.
        self._pending_sids: set[int] = set()
        # Subscriptions that lost events since the recorder last looked. The sink cannot fix a
        # gap -- only a resubscribe makes the venue re-send a snapshot -- so it records which
        # sid needs one and lets `WsRecorder` drain the set after every message.
        self.gap_sids: set[int] = set()
        self.errors = 0
        self.missing_side = 0
        # Task 12b: per-minute counters `WsRecorder.maybe_write_metrics` batches and resets;
        # `_last_event_ts` is the newest tape row this sink has actually written, for
        # `ws.sink_lag_s`.
        self._events_since = 0
        self._trades_since = 0
        self._gaps_since = 0
        self._last_event_ts: datetime | None = None

    def _maybe_commit(self, force: bool = False) -> None:
        if force or self._pending >= self._commit_every or time.monotonic() - self._last_commit >= self._interval:
            try:
                self._session.commit()
            except Exception:
                log.exception("ws sink commit failed")
                self._session.rollback()
                self.errors += 1
            self._pending, self._last_commit = 0, time.monotonic()
            self._pending_sids.clear()

    def expect_advances(self, sid: int, n: int) -> None:
        """Fix 77: the recorder has just sent `n` `update_subscription` frames on `sid`, and the
        venue spends one of that subscription's seq numbers on each of them. Book the allowance
        so the next frame that lands `n` ahead is read as those updates rather than as lost
        messages. This is deliberately *not* `clear_sequence`: the chain is kept, so a skip
        wider than the frames we sent is still a gap and still goes on the tape."""
        if n <= 0:
            return
        now = time.monotonic()
        pending = self._pending_advances(sid, now)   # prunes a stale entry before it is added to
        deadline = now + self._advance_ttl_s
        if pending:
            # Round 1 (Minor 2): keep the live entry's own deadline. Re-dating it would let a
            # steady trickle of updates carry an unspent expectation indefinitely, and it could
            # then absorb a loss long after the frame that bought it.
            deadline = min(self._advances[sid][1], deadline)
        self._advances[sid] = (min(pending + n, MAX_PENDING_ADVANCES), deadline)

    def _pending_advances(self, sid: int, now: float) -> int:
        """How much allowance `sid` still has, dropping it once it has gone stale: an
        expectation the venue never spent must not sit there absorbing a later real loss."""
        entry = self._advances.get(sid)
        if entry is None:
            return 0
        count, deadline = entry
        if now >= deadline:
            del self._advances[sid]
            return 0
        return count

    def _spend_advances(self, sid: int, n: int) -> None:
        entry = self._advances.get(sid)
        if entry is None:
            return
        count, deadline = entry
        if count <= n:
            del self._advances[sid]
        else:
            self._advances[sid] = (count - n, deadline)

    def reset_sequences(self) -> None:
        """Forget remembered seq numbers so a fresh subscription's restart-at-1 doesn't
        look like a gap against the previous connection's sequence. The pending advances go
        with them: they were bought on the sids of a subscription that no longer exists."""
        self._last_seq.clear()
        self._advances.clear()

    def _check_seq(self, sid: int, seq: int, ticker: str, ts: datetime) -> None:
        """`seq` counts per subscription, and one `sid` carries up to 500 tickers, so a gap
        invalidates every ticker on that sid -- not just the one whose message exposed it.
        The row therefore goes in under `ticker = ""` (the whole-subscription sentinel) with
        the exposing ticker kept in `raw` (null, never "", when the message carried no
        ticker at all, so a malformed frame is not read as the sentinel). A first message on
        an unseen sid has nothing to follow, so it records its seq and writes no gap.

        Fix 77: a skip the recorder's own `update_subscription` frames paid for (see
        `expect_advances`) is not a loss -- it is spent from the allowance and the chain
        follows it. Anything wider still writes the row."""
        last = self._last_seq.get(sid)
        if last is not None and seq != last + 1:
            now = time.monotonic()
            skipped = seq - (last + 1)
            if 0 < skipped <= self._pending_advances(sid, now):
                # Fix 77: the recorder's own `update_subscription` frames spent these seq
                # numbers, so nothing was lost. Spend the allowance and follow the chain.
                self._spend_advances(sid, skipped)
                self._advances_since += skipped
                log.info("seq advance sid=%s expected=%s got=%s accounted by %d subscription update(s)",
                         sid, last + 1, seq, skipped)
                self._last_seq[sid] = seq
                return
            # A forward skip the allowance cannot explain is a real loss. The pending advances
            # go with the row: their seq numbers are inside the range this gap already covers,
            # and carrying them forward would let them absorb the next message instead. Round 1
            # (Minor 1): only forward. A replayed or out-of-order frame (`seq <= last`) covers
            # no such range, and dropping the allowance there turned one row into two.
            if skipped > 0:
                self._advances.pop(sid, None)
            log.warning("seq gap sid=%s expected=%s got=%s exposed_by=%s", sid, last + 1, seq, ticker)
            self._session.add(OrderbookEvent(ticker="", ts=ts, sid=sid, seq=seq, kind="gap",
                                             raw={"sid": sid, "expected": last + 1, "got": seq, "exposed_by": ticker or None}))
            self._pending += 1
            self._pending_sids.add(sid)
            self.gap_sids.add(sid)
            self._gaps_since += 1
        self._last_seq[sid] = seq

    def handle(self, msg: dict, received_at: datetime) -> str | None:
        kind, body = msg.get("type"), msg.get("msg") or {}
        sid, seq = msg.get("sid"), msg.get("seq")
        ticker = body.get("market_ticker", "")
        try:
            if kind == "trade":
                price, count = _dec(body.get("yes_price_dollars")), _dec(body.get("count_fp"))
                side, outcome, book = taker_side_of(body)
                if not (body.get("trade_id") and ticker and price is not None and count is not None):
                    log.warning("ws trade dropped: missing fields %s",
                                sorted(k for k in ("trade_id", "market_ticker", "yes_price_dollars", "count_fp") if not body.get(k)))
                elif side is None:
                    # Kalshi's `taker_side` is deprecated; defaulting a missing side to "yes" would
                    # record every print as a YES taker. This process writes no runs row, so the
                    # counter and this line are the only record of the drop.
                    log.warning("ws trade dropped: taker side missing %s", ticker)
                    self.missing_side += 1
                else:
                    # `ts` is part of venue_trades' primary key now that the table is partitioned
                    # on it, so it is truncated to the millisecond Kalshi reports: the REST writer
                    # truncates the same way, and a sub-millisecond difference between the two
                    # feeds would put the same print on the tape twice.
                    stmt = insert(VenueTrade).values(venue="kalshi", trade_id=body["trade_id"], ticker=ticker,
                                                     ts=truncate_ms(_ts(body.get("ts_ms"), received_at)),
                                                     yes_price=price, count=count, taker_side=side,
                                                     taker_outcome_side=outcome, taker_book_side=book,
                                                     is_block=bool(body.get("is_block_trade")), source="ws", raw_id=None
                                                     ).on_conflict_do_nothing().returning(VenueTrade.trade_id)
                    # psycopg3 reports rowcount -1 for ON CONFLICT DO NOTHING; count returned rows instead (Task 6 ruling)
                    landed = len(self._session.execute(stmt).fetchall())
                    self._pending += landed
                    if landed:
                        self._events_since += landed
                        self._trades_since += landed
                        self._last_event_ts = truncate_ms(_ts(body.get("ts_ms"), received_at))
                    if landed and sid is not None:
                        self._pending_sids.add(sid)
            elif kind == "orderbook_snapshot":
                if sid is not None and seq is not None:
                    # Assigning `_last_seq[sid]` here erased any gap that coincided with a
                    # snapshot; `_check_seq` records the same seq and writes the row first.
                    # The ordering is load-bearing: phase 3's book loader dirties a book on
                    # `gap.sid = anchor.sid and gap.id > anchor.id`, so the gap must take the
                    # lower id and leave the snapshot that refreshes the book clean. Swapping
                    # these two `session.add` calls would break that silently.
                    self._check_seq(sid, seq, ticker, received_at)
                # F58: a snapshot is stamped with the recorder's local clock (deltas carry the
                # venue's own `ts_ms`), so the offset to Kalshi's server goes on the row -- the
                # only way that stamp can be corrected afterwards. Copy, never mutate the frame.
                self._session.add(OrderbookEvent(ticker=ticker, ts=received_at, sid=sid or 0, seq=seq or 0, kind="snapshot",
                                                 raw={**body, "recorder_offset_ms": self.offset_ms}))
                self._pending += 1
                self._events_since += 1
                self._last_event_ts = received_at
                if sid is not None:
                    self._pending_sids.add(sid)
            elif kind == "orderbook_delta":
                ts = _ts(body.get("ts_ms"), received_at)
                if sid is not None and seq is not None:
                    self._check_seq(sid, seq, ticker, ts)
                self._session.add(OrderbookEvent(ticker=ticker, ts=ts, sid=sid or 0, seq=seq or 0, kind="delta", side=body.get("side"),
                                                 price=_dec(body.get("price_dollars")), delta=_dec(body.get("delta_fp")), raw=body))
                self._pending += 1
                self._events_since += 1
                self._last_event_ts = ts
                if sid is not None:
                    self._pending_sids.add(sid)
            else:
                # Fix 77, the other reading of row 77: if the venue acknowledges an
                # `update_subscription` with a frame that carries the subscription's own `sid`
                # and `seq`, that frame *is* the seq number the update spent. It is not tape
                # data -- no row -- but following it keeps the chain continuous, so the delta
                # behind it is in sequence. `subscribed` and `error` never reach the sink;
                # `WsRecorder` handles both.
                if _is_int(sid) and _is_int(seq):
                    self._note_ack_seq(sid, seq)
                return None
        except Exception:
            log.exception("ws sink failed on %s %s", kind, ticker)
            # The rollback throws away every row the batch was holding -- up to `commit_every`
            # of them -- and the log line is not on the tape, so analysis would read the hole as
            # a quiet stretch of market. Mark it: `gap` rows under the same whole-subscription
            # `ticker = ""` sentinel `_check_seq` uses, carrying the discarded count and the
            # message that failed. `exposed_by = "sink_exception"` is what tells the two apart.
            # One row per sid in the batch, not just the failing message's: phase 3's book
            # loader dirties a book on `gap.sid = anchor.sid and gap.id > anchor.id`, so a sid
            # that loses deltas without a row of its own keeps serving a book that is a lie.
            # The failing sid goes in too, even with nothing pending -- its message is lost.
            discarded = self._pending
            pending = set(self._pending_sids)
            self._session.rollback()
            self._pending = 0
            self._pending_sids.clear()
            self._last_commit = time.monotonic()
            self.errors += 1
            try:
                # Everything that can raise on a malformed frame belongs inside this guard: a
                # `sid` that is a string leaves the set unorderable, and sorting it out here
                # would throw from inside an `except` and take the recorder down with it.
                # Sid 0 is phase 3's REST anchor, so a mark parked there dirties every
                # REST-anchored book for the rest of the tape. A frame with no sid of its own
                # is therefore folded into the subscriptions that were holding rows, and 0 is
                # used only when the batch was empty and there is nothing else to name.
                failing = sid if sid is not None else 0
                sids = sorted(pending | ({sid} if sid is not None else set())) or [0]
                raw = {"discarded": discarded, "exposed_by": "sink_exception", "kind": kind,
                       "ticker": ticker, "sids": sids}
                for marked in sids:
                    # Only the failing frame's own subscription carries that frame's seq. The
                    # others carry their own last seq, so each mark reads against its stream.
                    marked_seq = (seq or 0) if marked == failing else self._last_seq.get(marked, 0)
                    self._session.add(OrderbookEvent(ticker="", ts=received_at, sid=marked, seq=marked_seq,
                                                     kind="gap", raw=dict(raw)))
                    self._gaps_since += 1
                self._session.commit()
            except Exception:
                # Whatever broke the message may be the database itself. Losing the mark is bad;
                # letting one failed message kill the recorder that is still taping is worse.
                log.exception("ws sink could not write the exception mark")
                self._session.rollback()
            return None
        self._maybe_commit()
        return kind

    def _note_ack_seq(self, sid: int, seq: int) -> None:
        """Follow the seq of a non-data frame -- conservatively. The venue does not document
        what its `update_subscription` acknowledgement carries, so a frame whose seq is not the
        next number this subscription owes (a command echo, an id, a counter of its own) is
        counted and dropped: it writes no gap and does not move `_last_seq`. Moving the chain
        to an arbitrary number would desynchronise every delta behind it, and writing a gap
        from one would put a false row on the tape."""
        last = self._last_seq.get(sid)
        if last is None:
            return  # nothing to follow yet; the first data frame opens the chain
        pending = self._pending_advances(sid, time.monotonic())
        skipped = seq - (last + 1)
        # The ack occupies one seq number itself; anything it is ahead by must be other update
        # frames' numbers, so it can never account for more than the allowance bought. Round 1
        # (Important 1): with nothing pending it is not ours to follow at all. Such a frame is
        # a message this tape never stored, and swallowing it here would take with it the `gap`
        # row the next delta would otherwise write.
        if pending <= 0 or skipped < 0 or skipped > pending - 1:
            self._acks_out_since += 1
            log.debug("ws frame seq out of sequence sid=%s expected=%s got=%s; ignored", sid, last + 1, seq)
            return
        self._spend_advances(sid, skipped + 1)
        self._advances_since += skipped + 1
        self._last_seq[sid] = seq
        log.info("seq advance sid=%s expected=%s got=%s accounted by %d subscription update(s) (acknowledged)",
                 sid, last + 1, seq, skipped + 1)

    def flush(self) -> None:
        """Commit whatever the batch holds right now. The recorder calls this whenever the
        socket drops, so a partial batch never sits in an open transaction across a reconnect
        (its row locks would block the REST normalizer's unique-index probe on venue_trades)."""
        self._maybe_commit(force=True)

    def close(self) -> None:
        self._maybe_commit(force=True)
        self._session.close()

    # --- Task 12b telemetry -------------------------------------------------------------

    def drain_counts(self) -> dict:
        """Message counts since the last drain, reset to zero. `WsRecorder` calls this once a
        minute to batch `ws.events_per_min`/`ws.trades_per_min`/`ws.gaps` (design spec §3.1)
        and, since fix 77, `ws.seq_advances_accounted`/`ws.acks_out_of_sequence`."""
        counts = {"events": self._events_since, "trades": self._trades_since,
                 "gaps": self._gaps_since, "seq_advances_accounted": self._advances_since,
                 "acks_out_of_sequence": self._acks_out_since}
        self._events_since = self._trades_since = self._gaps_since = 0
        self._advances_since = self._acks_out_since = 0
        return counts

    def sink_lag_s(self, now: datetime) -> float | None:
        """Seconds between `now` and the newest tape row this sink has actually written, or
        None before it has written one. Floored at zero: the venue clock can run a fraction of
        a second ahead of the host clock, and a negative lag is not a real one."""
        return None if self._last_event_ts is None else max(0.0, (now - self._last_event_ts).total_seconds())

    def write_metrics(self, ts: datetime, samples) -> None:
        """`ws.*` metric_samples through this sink's own session -- the brief's "through the
        sink's session", since only the sink holds a database session at all."""
        telemetry.record_many(self._session, "ws", samples, ts=ts)
        self._maybe_commit(force=True)

    def write_event(self, kind: str, summary: str, ref: dict | None = None,
                    ts: datetime | None = None) -> None:
        """One `operator_events` row through this sink's own session (`ws_connect`,
        `ws_disconnect`)."""
        telemetry.event(self._session, kind, summary, ref=ref, ts=ts)
        self._maybe_commit(force=True)

    def rollback(self) -> None:
        """Fix round 1, I3: `WsRecorder` calls this when a `write_metrics`/`write_event` call
        raises, so a failed telemetry insert cannot leave this sink's session in a failed
        transaction that then poisons every tape row `handle()` tries to write next."""
        self._session.rollback()
        self._pending = 0
        self._pending_sids.clear()
        self._last_commit = time.monotonic()
