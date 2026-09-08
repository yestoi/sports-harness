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
                 offset_ms: int = 0):
        self._factory = session_factory
        # The recorder's clock offset to Kalshi's server, refreshed at every connect and
        # written onto every snapshot row (see `handle`). Public: the recorder assigns it.
        self.offset_ms = offset_ms
        self._session = session_factory()
        self._pending = 0
        self._last_commit = time.monotonic()
        self._commit_every, self._interval = commit_every, commit_interval_s
        self._last_seq: dict[int, int] = {}
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

    def clear_sequence(self, sid: int) -> None:
        """Forget one subscription's remembered seq. The recorder calls this after a gap
        recovery: the venue restarts the sid's numbering when its markets are re-added, so the
        old seq would make the very first frame of the fresh snapshot look like another gap."""
        self._last_seq.pop(sid, None)

    def reset_sequences(self) -> None:
        """Forget remembered seq numbers so a fresh subscription's restart-at-1 doesn't
        look like a gap against the previous connection's sequence."""
        self._last_seq.clear()

    def _check_seq(self, sid: int, seq: int, ticker: str, ts: datetime) -> None:
        """`seq` counts per subscription, and one `sid` carries up to 500 tickers, so a gap
        invalidates every ticker on that sid -- not just the one whose message exposed it.
        The row therefore goes in under `ticker = ""` (the whole-subscription sentinel) with
        the exposing ticker kept in `raw` (null, never "", when the message carried no
        ticker at all, so a malformed frame is not read as the sentinel). A first message on
        an unseen sid has nothing to follow, so it records its seq and writes no gap."""
        last = self._last_seq.get(sid)
        if last is not None and seq != last + 1:
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
        minute to batch `ws.events_per_min`/`ws.trades_per_min`/`ws.gaps` (design spec §3.1)."""
        counts = {"events": self._events_since, "trades": self._trades_since,
                 "gaps": self._gaps_since}
        self._events_since = self._trades_since = self._gaps_since = 0
        return counts

    def sink_lag_s(self, now: datetime) -> float | None:
        """Seconds between `now` and the newest tape row this sink has actually written, or
        None before it has written one."""
        return None if self._last_event_ts is None else (now - self._last_event_ts).total_seconds()

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
