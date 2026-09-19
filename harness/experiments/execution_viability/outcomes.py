"""§1.9(a) (ruling C2): the **common** outcome schedule, applied to every arm.

One horizon set, one maturity rule, one missing-value vocabulary and one writer of
`exp_outcome`. Every arm's numbers -- A's, B's and, when T7 runs it, C's prospective
observations -- are computed here on the same schedule from the same common observer, never
from an arm's own observation frequency, and `report.py` reads these rows rather than
computing a markout of its own.

Three statements the addendum makes and this module keeps:

* **A horizon that has not arrived is censored, never zero and never forward-filled.** An
  order filled twenty minutes ago has no thirty-minute markout: the row carries `matures_at`
  and no value.
* **An old quote is never a claim of fresh contemporaneous value.** A mid older than
  `MID_MAX_AGE_S` at the horizon is not that horizon's mid; the row is *missing* with a named
  reason, and the age of every mid that is used travels with the number it produced (§1.9d).
* **Matured, censored and missing are recorded separately**, so nobody has to infer which of
  the three a blank cell was.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.execution.book import side_p
from harness.pricing.fair import FEATURED_CADENCE_S

log = logging.getLogger("harness.exp")

#: §1.9(a): entry, 30 minutes, market close. `t0` is the fill instant itself -- the mid the
#: order actually traded against -- and is what the other two are read beside.
HORIZONS: tuple[str, ...] = ("t0", "1800", "close")

#: Why an outcome that *should* have been observable is not. A censored row -- a horizon that
#: has not arrived -- carries none of these: it is not missing, it is not yet.
MISSING_REASONS: tuple[str, ...] = ("no_mid_at_horizon", "book_absent", "market_settled_early")

#: The three statuses, recorded separately (§1.9a).
STATUSES: tuple[str, ...] = ("matured", "censored", "missing")

#: How old a quote may be and still be *this horizon's* mid: the same pricing-time allowance
#: arm A decides under, **derived** from the two production constants it is made of rather than
#: restated, so a settings change cannot leave the forward-fill refusal quietly wrong. Older
#: than this, the quote is a forward fill and §1.9(a) refuses to read it as contemporaneous.
#: The declared default is read, not a live `Settings()`: this is the schedule the outcome
#: table is defined on, and it may not vary with one deployment's environment file.
MID_MAX_AGE_S = FEATURED_CADENCE_S + int(Settings.model_fields["tick_budget_s"].default)

#: The newest quote at or before the horizon, on the market's own index
#: `ix_quotes_market_fetched (venue_market_id, fetched_at)`.
_MID_AT = text(
    "select yes_bid, yes_ask, fetched_at from venue_quotes "
    "where venue_market_id = :market and fetched_at <= :at "
    "order by fetched_at desc limit 1")
#: Does this market have any quote at all? A market with none is `book_absent`, which is a
#: different statement from a market whose quote is too old at one horizon.
_ANY_MID = text("select 1 from venue_quotes where venue_market_id = :market limit 1")
#: The market's own close, when the tape kept one.
_CLOSE_AT = text("select close_time, expected_expiration_time from venue_markets "
                 "where id = :market")


def _matures_at(horizon: str, *, filled_at: datetime, close_at: datetime | None):
    if horizon == "t0":
        return filled_at
    if horizon == "close":
        return close_at
    return filled_at + timedelta(seconds=int(horizon))


def _mid(session: Session, market_id: int, at: datetime):
    """`(mid_yes, age_s)` at `at`, or `(None, None)` where the tape has no usable quote."""
    row = session.execute(_MID_AT, {"market": market_id, "at": at}).first()
    if row is None or row.yes_bid is None or row.yes_ask is None:
        return None, None
    age = int((at - row.fetched_at).total_seconds())
    if age > MID_MAX_AGE_S:
        return None, None
    return (Decimal(row.yes_bid) + Decimal(row.yes_ask)) / 2, age


def _close_at(session: Session, market_id) -> datetime | None:
    if market_id is None:
        return None
    row = session.execute(_CLOSE_AT, {"market": market_id}).first()
    if row is None:
        return None
    return row.close_time or row.expected_expiration_time


def _reason(session: Session, market_id, *, matures_at: datetime,
            close_at: datetime | None) -> str:
    if market_id is None or session.execute(_ANY_MID, {"market": market_id}).first() is None:
        return "book_absent"
    if close_at is not None and close_at < matures_at:
        return "market_settled_early"
    return "no_mid_at_horizon"


def _row(session: Session, order: dict, horizon: str, *, run_id: str, arm_id: str,
         now: datetime, close_at: datetime | None) -> dict:
    market_id = order.get("venue_market_id")
    matures_at = _matures_at(horizon, filled_at=order["filled_at"], close_at=close_at)
    row = {"run_id": run_id, "arm_id": arm_id, "exp_order_id": order["id"], "horizon": horizon,
           "observed_at": now, "value": None, "source_age_s": None, "censored": False,
           "missing_reason": None, "status": "censored", "matures_at": matures_at}
    if matures_at is None or matures_at > now:
        # Not yet: the horizon has not arrived, or the market has no close the tape kept.
        row["censored"] = True
        return row
    mid, age = _mid(session, market_id, matures_at) if market_id is not None else (None, None)
    if mid is None:
        row["status"] = "missing"
        row["missing_reason"] = _reason(session, market_id, matures_at=matures_at,
                                        close_at=close_at)
        return row
    row["status"] = "matured"
    row["value"] = side_p(mid, order["side"]) - Decimal(order["prob"])
    row["source_age_s"] = age
    return row


def _table_row(row: dict) -> dict:
    """The `exp_outcome` columns alone (§2): `status` and `matures_at` are the in-memory
    reading of `censored`/`missing_reason` and of the horizon, and the table declares neither."""
    return {key: row[key] for key in ("run_id", "arm_id", "exp_order_id", "horizon",
                                      "observed_at", "value", "source_age_s", "censored",
                                      "missing_reason")}


def record_outcomes(session: Session, writer, *, run_id: str, arm_id: str,
                    orders: Sequence[dict], now: datetime,
                    horizons: Sequence[str] = HORIZONS) -> list[dict]:
    """One `exp_outcome` row per `(order, horizon)`, written through the writer only.

    `orders` are the arm's own filled orders as `exp_order` rows (`id` the surrogate key the
    fills point at, D22). An order that never filled has no outcome at all -- there is no
    entry price to measure from -- and is skipped rather than recorded as missing.

    This function **reads** production tables (`venue_quotes`, `venue_markets`) and writes
    `exp_outcome`: nothing else, in either direction.
    """
    rows: list[dict] = []
    closes: dict = {}
    for order in orders:
        if order.get("filled_at") is None:
            continue
        market_id = order.get("venue_market_id")
        if market_id not in closes:
            closes[market_id] = _close_at(session, market_id)
        for horizon in horizons:
            rows.append(_row(session, order, horizon, run_id=run_id, arm_id=arm_id, now=now,
                             close_at=closes[market_id]))
    if rows:
        writer.insert(writer.table("exp_outcome"), [_table_row(row) for row in rows])
    log.info("exp outcomes run=%s arm=%s rows=%d", run_id, arm_id, len(rows))
    return rows
