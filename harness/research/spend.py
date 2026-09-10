"""The U4 spend caps, enforced in code from the usage fields.

Roadmap invariant 7 and addendum 0.3: `veto_daily_usd_cap` = $25 and `veto_weekly_usd_cap` =
$150, and they are **totals** across the primary, the shadow, the annotator and the parlay
rationale. Nothing in the harness calls Anthropic without passing through `reserve_spend` first
and `release_spend` after; `tests/test_research_client.py` asserts that structurally.

**The reservation, and why it is a lock.** Ruling A-C3 rejects check-then-act: two workers that
both read today's total and both pass will both add. Its sketch is a conditional single-row
`UPDATE ... RETURNING`, which is atomic for a one-row-per-day table. The addendum's table is
`(day, kind, model)`, with the caps summed over every row of the day and of the ISO week, and a
conditional single-row update cannot enforce a sum over several rows. So the reservation takes
`pg_advisory_xact_lock` on the day key first: the second worker blocks until the first commits,
then reads the first's reservation and refuses. The lock is released by the transaction, taken
or refused -- which makes its scope the **caller's** transaction, not this function's. A caller
that commits as soon as `reserve_spend` returns holds it for one short read-and-update; a caller
that keeps the transaction open across its Anthropic call holds it for the length of that call
and blocks every other reservation on the same day meanwhile. See `reserve_spend`.

**The cost model.** Tokens at list price with cache reads at 0.1x the input rate and cache
writes at 1.25x, plus searches at $0.01 each. Web search is billed **per search on top of
tokens**, so a token-only model under-reports a search call by about 4x (A-C3 hole 2), which is
why `searches` is a column and a first-class term here.

**The worst case.** A server-tool call re-bills the accumulated context on every tool round, so
with `max_uses = 3` billed input is the sum over up to four rounds, not the prompt once. The
constants below are the addendum's opening values and are **re-fitted from the first live day
and journaled**; verify.md's phase 5 block carries the row that does it.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import ResearchSpend

log = logging.getLogger(__name__)

#: The four writers that share the caps (addendum 0.3, D10). `research_notes.kind` is the same
#: vocabulary and the column is String(8), which "annotate" fills exactly.
KINDS = ("veto", "annotate", "parlay", "study")

#: America/Chicago. The cap resets on the owner's day, not on UTC's: the 09:00 CT journal line
#: reads "today's spend" and has to mean the day the owner is living in.
_TZ = ZoneInfo("America/Chicago")

#: One million tokens, as a Decimal, so every price division stays exact.
_MTOK = Decimal("1000000")


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: Decimal
    output_per_mtok: Decimal


#: List prices, 2026-06-24 (verified-facts D4). Never a date-suffixed model id.
PRICES: dict[str, ModelPrice] = {
    "claude-opus-5": ModelPrice(Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5": ModelPrice(Decimal("2.00"), Decimal("10.00")),
    # No phase 5 caller uses this one: it is the veto study's blind pairwise judge (R:311), a
    # later phase. It is priced here so that phase's cost model needs no second table.
    "claude-fable-5-1": ModelPrice(Decimal("10.00"), Decimal("50.00")),
}
CACHE_READ_MULTIPLIER = Decimal("0.1")
CACHE_WRITE_MULTIPLIER = Decimal("1.25")
SEARCH_USD = Decimal("0.01")

#: The opening worst case per model, per call (ruling A-C3). Re-fitted after the first live day.
WORST_CASE_INPUT_TOKENS = 30_000
WORST_CASE_OUTPUT_TOKENS = 2_000
WORST_CASE_SEARCHES = 3


@dataclass(frozen=True)
class Usage:
    """One model's billed usage for one call, from `response.usage` plus the `server_tool_use`
    block count. Positional in the order the accounting reads them."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    searches: int = 0


@dataclass(frozen=True)
class Reservation:
    """What `reserve_spend` took, and what `release_spend` gives back."""

    day: date
    kind: str
    per_model: dict[str, Decimal]


@dataclass(frozen=True)
class SpendState:
    """What Pulse's `research_budget` rule and the 09:00 journal line read (addendum §1.4)."""

    day_usd: Decimal
    day_reserved: Decimal
    week_usd: Decimal
    week_reserved: Decimal
    daily_cap: Decimal
    weekly_cap: Decimal
    dormant: bool


class BudgetRefused(RuntimeError):
    """A reservation that would cross a cap. The caller writes `veto_skipped_budget` with a null
    `call_id` and makes no call at all."""

    def __init__(self, cap: str, total: Decimal, projection: Decimal, limit: Decimal) -> None:
        super().__init__(f"{cap} cap: {total} + {projection} would exceed {limit}")
        self.cap, self.total, self.projection, self.limit = cap, total, projection, limit


# --- the calendar ----------------------------------------------------------------------------

def chicago_day(now: datetime) -> date:
    """The America/Chicago calendar day `now` falls in."""
    return now.astimezone(_TZ).date()


def iso_week_bounds(day: date) -> tuple[date, date]:
    """[Monday, Sunday] of `day`'s ISO week, inclusive on both ends -- the shape a `between`
    predicate on a `date` column wants."""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


# --- the cost model --------------------------------------------------------------------------

def cost_usd(model: str, usage: Usage) -> Decimal:
    """One model's cost for one call. Raises `KeyError` on a model with no price: a call that
    cost an unknown amount must not be recorded as costing nothing."""
    price = PRICES[model]
    return (
        Decimal(usage.input_tokens) * price.input_per_mtok / _MTOK
        + Decimal(usage.output_tokens) * price.output_per_mtok / _MTOK
        + Decimal(usage.cache_read_tokens) * price.input_per_mtok * CACHE_READ_MULTIPLIER / _MTOK
        + Decimal(usage.cache_write_tokens) * price.input_per_mtok * CACHE_WRITE_MULTIPLIER / _MTOK
        + Decimal(usage.searches) * SEARCH_USD
    ).quantize(Decimal("0.000001"))


def worst_case_usd(model: str, searches: int = WORST_CASE_SEARCHES) -> Decimal:
    """What one call on `model` is reserved at before it runs."""
    return cost_usd(model, Usage(input_tokens=WORST_CASE_INPUT_TOKENS,
                                 output_tokens=WORST_CASE_OUTPUT_TOKENS,
                                 searches=searches))


# --- the gate ---------------------------------------------------------------------------------

_LOCK = text("select pg_advisory_xact_lock(hashtext('research_spend:' || :day))")
_DAY_TOTAL = text("select coalesce(sum(usd + usd_reserved), 0) from research_spend "
                  "where day = :day")
_WEEK_TOTAL = text("select coalesce(sum(usd + usd_reserved), 0) from research_spend "
                   "where day between :monday and :sunday")
_RESERVE = text("update research_spend set usd_reserved = usd_reserved + :amount "
                "where day = :day and kind = :kind and model = :model")
_RELEASE = text("""
    update research_spend
       set usd_reserved = greatest(usd_reserved - :reserved, 0),
           usd = usd + :actual,
           calls = calls + :calls,
           input_tokens = input_tokens + :input_tokens,
           output_tokens = output_tokens + :output_tokens,
           cache_read_tokens = cache_read_tokens + :cache_read_tokens,
           cache_write_tokens = cache_write_tokens + :cache_write_tokens,
           searches = searches + :searches
     where day = :day and kind = :kind and model = :model
""")


def _ensure_rows(session: Session, day: date, kind: str, models: Sequence[str]) -> None:
    for model in models:
        session.execute(insert(ResearchSpend).values(
            day=day, kind=kind, model=model, calls=0, input_tokens=0, output_tokens=0,
            cache_read_tokens=0, cache_write_tokens=0, searches=0,
            usd_reserved=Decimal("0"), usd=Decimal("0")
        ).on_conflict_do_nothing(index_elements=["day", "kind", "model"]))


def reserve_spend(session: Session, now: datetime, settings, kind: str,
                  models: Sequence[str], searches: int | None = None) -> Reservation:
    """Take the worst case for one call on each of `models` out of today's budget, atomically.

    Raises `BudgetRefused` when the day's or the week's total plus the projection would exceed
    its cap, having written nothing. The caller then records the call-less label its own
    component defines (`veto_skipped_budget` for the veto) and makes no request.

    `searches` defaults to `WORST_CASE_SEARCHES`; a call with no tools passes `0`, which is what
    keeps the annotator and the parlay rationale from reserving three searches they cannot make.

    **Commit as soon as this returns.** The advisory lock lives until the caller's transaction
    ends, so holding the transaction open across the Anthropic call blocks every other
    reservation on the same America/Chicago day for the length of that call, and
    `pg_advisory_xact_lock` has no timeout to cut it short.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown research kind {kind!r}")
    day = chicago_day(now)
    monday, sunday = iso_week_bounds(day)
    per_model = {model: worst_case_usd(
        model, WORST_CASE_SEARCHES if searches is None else searches) for model in models}
    projection = sum(per_model.values(), Decimal("0"))

    session.execute(_LOCK, {"day": day.isoformat()})
    _ensure_rows(session, day, kind, models)
    day_total = session.execute(_DAY_TOTAL, {"day": day}).scalar() or Decimal("0")
    if day_total + projection > settings.veto_daily_usd_cap:
        raise BudgetRefused("daily", day_total, projection, settings.veto_daily_usd_cap)
    week_total = session.execute(
        _WEEK_TOTAL, {"monday": monday, "sunday": sunday}).scalar() or Decimal("0")
    if week_total + projection > settings.veto_weekly_usd_cap:
        raise BudgetRefused("weekly", week_total, projection, settings.veto_weekly_usd_cap)

    for model, amount in per_model.items():
        session.execute(_RESERVE, {"amount": amount, "day": day, "kind": kind, "model": model})
    log.info("research reserved %s for %s on %s", projection, kind, day)
    return Reservation(day=day, kind=kind, per_model=per_model)


def release_spend(session: Session, reservation: Reservation,
                  actuals: dict[str, Usage]) -> Decimal:
    """Swap a reservation for what was actually billed. Returns the total actual cost.

    A model with no entry in `actuals` had no billed call -- the request raised, or the pair's
    second half was never made -- and its reservation is returned with a zero actual. Callers
    run this in a `finally`: a reservation that is never released eats the cap for the day.
    """
    total = Decimal("0")
    for model, reserved in reservation.per_model.items():
        usage = actuals.get(model)
        actual = cost_usd(model, usage) if usage is not None else Decimal("0")
        total += actual
        session.execute(_RELEASE, {
            "reserved": reserved, "actual": actual,
            "calls": 1 if usage is not None else 0,
            "input_tokens": usage.input_tokens if usage is not None else 0,
            "output_tokens": usage.output_tokens if usage is not None else 0,
            "cache_read_tokens": usage.cache_read_tokens if usage is not None else 0,
            "cache_write_tokens": usage.cache_write_tokens if usage is not None else 0,
            "searches": usage.searches if usage is not None else 0,
            "day": reservation.day, "kind": reservation.kind, "model": model})
    return total


def spend_state(session: Session, now: datetime, settings) -> SpendState:
    """The day's and the week's totals, and whether the next paired veto call would be refused.

    `dormant` is deliberately a *projection*, not a comparison against the cap: what an operator
    and Pulse need to know is whether the next call will happen, and the next call is a pair at
    the worst case. Dormancy lifts at the next America/Chicago day, or on Monday for the week.
    """
    day = chicago_day(now)
    monday, sunday = iso_week_bounds(day)
    day_row = session.execute(text(
        "select coalesce(sum(usd), 0), coalesce(sum(usd_reserved), 0) from research_spend "
        "where day = :day"), {"day": day}).first()
    week_row = session.execute(text(
        "select coalesce(sum(usd), 0), coalesce(sum(usd_reserved), 0) from research_spend "
        "where day between :monday and :sunday"), {"monday": monday, "sunday": sunday}).first()
    pair = worst_case_usd("claude-opus-5") + worst_case_usd("claude-sonnet-5")
    day_usd, day_reserved = day_row
    week_usd, week_reserved = week_row
    dormant = (day_usd + day_reserved + pair > settings.veto_daily_usd_cap
               or week_usd + week_reserved + pair > settings.veto_weekly_usd_cap)
    return SpendState(day_usd=day_usd, day_reserved=day_reserved, week_usd=week_usd,
                      week_reserved=week_reserved, daily_cap=settings.veto_daily_usd_cap,
                      weekly_cap=settings.veto_weekly_usd_cap, dormant=dormant)
