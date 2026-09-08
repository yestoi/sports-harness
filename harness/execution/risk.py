"""The risk gate (spec §9.3): the drawdown stop, and nothing else.

One rule, stated once here and read from three places -- the executor's equity sampler, which
writes its verdict on every `equity_snapshots` row; the pricing pipeline, which annotates the
signals of a stopped variant; and the weekly report's table 1, which reports the stopped share
as a note. Keeping the rule in one pure function is what lets all three agree by construction.

**Equity is cash.** `equity_snapshots.cash` is `bankroll + ledger cash delta` (pre-loaded
decision 6, ruling B-C2). `mtm_open` is reported beside it and never enters the stop: an open
position marked at a live midpoint is an opinion about what we could get out at, and a stop
that could be talked out of tripping by our own mark is not a stop.

**What the stop does, and does not, do.** In paper it is *information*: the executor keeps
placing, the signals of a stopped variant carry the `drawdown_stop` annotation, and the
annotation is outside `FILTER_LABELS` and `CAP_LABELS`, so no decision of any registered
variant id can read it (ruling A-C2/B-C1). The live path is dormant and does treat it as a
brake: `KalshiGateway` trips the kill switch with reason `drawdown_stop:<variant>`. The rule
itself is identical in both; only the response differs.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

FOUR = Decimal("0.0001")
ZERO = Decimal("0")

#: Spec §9.3. A threshold, changed only by a user decision, never by a tuning pass.
DRAWDOWN_STOP_PCT = Decimal("-0.20")
#: The trailing window the peak is taken over. Also how far back a verdict stays readable:
#: a variant whose newest snapshot is older than this has not been sampled recently enough
#: for its last verdict to describe today.
DRAWDOWN_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class Drawdown:
    """The three additive `equity_snapshots` columns for one sample."""

    peak_equity_7d: Decimal | None
    drawdown_pct: Decimal | None
    drawdown_stop: bool


def compute_drawdown(cash: Decimal, peak_7d: Decimal | None) -> Drawdown:
    """Pure. `drawdown_pct = (cash - peak_7d) / peak_7d`, quantized to 4 places; the stop trips
    at `<= -0.20`.

    A None or non-positive peak yields `(peak, None, False)`: there is nothing to draw down
    from. A variant whose bankroll is zero, or which has already been taken to zero, would
    otherwise divide by it -- and a percentage drawdown off a zero base is not a number the
    gate can act on.
    """
    if peak_7d is None or peak_7d <= ZERO:
        return Drawdown(peak_equity_7d=peak_7d, drawdown_pct=None, drawdown_stop=False)
    pct = ((cash - peak_7d) / peak_7d).quantize(FOUR, rounding=ROUND_HALF_UP)
    return Drawdown(peak_equity_7d=peak_7d, drawdown_pct=pct,
                    drawdown_stop=pct <= DRAWDOWN_STOP_PCT)


_PEAK = text("""
    select max(cash) from equity_snapshots
    where variant_id = :variant_id and ts >= :since and ts <= :now
""")


def peak_equity_7d(session: Session, variant_id: str, now: datetime,
                   cash: Decimal) -> Decimal:
    """The maximum `equity_snapshots.cash` for the variant over the trailing 7 days, including
    the sample being written now.

    `cash` is folded in rather than read back, for two reasons: the row for this instant has
    not been inserted yet at the point the executor asks, and a first sample must be its own
    peak so that its drawdown is 0 rather than undefined. A variant on a new high therefore
    reads a drawdown of exactly zero, which is what the curve says.
    """
    peak = session.execute(
        _PEAK, {"variant_id": variant_id, "since": now - DRAWDOWN_WINDOW, "now": now}).scalar()
    return cash if peak is None else max(Decimal(peak), cash)


_NEWEST_VERDICT = text("""
    select distinct on (variant_id) variant_id, drawdown_stop
    from equity_snapshots
    where ts >= :since and ts <= :now and drawdown_stop is not null
    order by variant_id, ts desc
""")


def stopped_variants(session: Session, now: datetime) -> set[str]:
    """Every `variant_id` whose newest equity snapshot inside the window has
    `drawdown_stop = true`. Read by the pipeline to annotate signals and by the report's
    table 1 note.

    Newest *verdict*, not newest row: `drawdown_stop is null` is "not evaluated" -- every row
    written before this gate shipped, and the settler's own snapshot if it ever writes one
    without the columns -- and a row that never asked the question cannot answer it with a
    recovery. A variant that recovers writes `false` and leaves the set on its next sample.
    """
    rows = session.execute(_NEWEST_VERDICT,
                           {"since": now - DRAWDOWN_WINDOW, "now": now}).all()
    return {row.variant_id for row in rows if row.drawdown_stop}
