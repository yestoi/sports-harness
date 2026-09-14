"""Confirming a hand-placed slip, and expiring the ones nobody placed (addendum §1.3).

This is the one place in the harness where real money is recorded. It records; it never places.
The operator types the slip into DraftKings and then tells the harness what they actually got,
and the two refusals here exist so that what the harness records is what they actually got:

* **The weekly cap is hard** (D15). $50 an ISO week against `parlay_ledger`, and a card that
  would cross it is refused rather than trimmed. The $10 the allocation leaves over stays
  unspent, deliberately. The week is keyed to America/Chicago, not UTC: `now.isocalendar()` on a
  raw UTC timestamp attributes Sunday-night activity (as late as 7 p.m. Central, 01:00 UTC
  Monday) to next week's cap for about five hours, which is wrong for a budget the operator
  thinks of as running Sunday's slate through Saturday's.
* **A moved line is refused** (ruling A-M7). If the newest DraftKings row's `point` differs from
  the card's for any leg, the operator has to say `--leg-line <seq>=<point>` for it. A moved line
  is a different bet, and confirming it silently would put a card in the ledger that is not the
  card that was placed.

* **A confirmation id is used once** (addendum 5.1, 5.3). The confirm sheet mints one per
  submit, so a double tap, a retry over a flaky phone connection or a second tab records one
  placement and one stake row. An id already spent on *another* card is refused before anything
  is written: the same figure against the wrong card is a corrupted ledger, not a duplicate.
* **The week is the card's, not the clock's** (D20). Every ledger row of a card -- the stake,
  every correction delta, the return, the void -- carries the card's own `(year, week)`, and the
  cap reads that key. A Saturday-built card placed on Monday belongs to its slate's week.

Corrections (addendum 5.2) are the other half of the same job. `apply_correction` implements a
**closed** table: a `(card state, field, new_value)` triple that is not in `TRANSITIONS` is
refused, and every accepted one writes exactly one `parlay_placement_corrections` row. Nothing
here is a general-purpose update path -- there is no way to reach `cashed`, `busted`, `placed`
or `alive` through it, because those are the grader's words about the world, not the owner's.

Everything this module writes to `parlay_ledger` is `source = 'confirmed'`: the owner typed the
figure off their own DraftKings slip. `computed` is the grader's arithmetic, and a confirmed
return or void **replaces** the computed row rather than sitting beside it -- one row per
`(card_id, kind)`, which the table has no unique key to enforce and this code does.

`void`, never `expired` (ruling B-C4): `parlay_cards.status` is
`proposed|placed|alive|cashed|busted|void` and `harness/dashboard/snapshots/ticket.py` filters on
that vocabulary in five places, so a status outside it is invisible on the surface built for it.
"""
import logging
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import (ParlayCard, ParlayLedger, ParlayLeg, ParlayPlacement,
                               ParlayPlacementCorrection)
from harness.parlay.config import load_config
from harness.parlay.pricing import newest_dk_price
from harness.telemetry import sanitize_reason
from harness.weeks import chicago_iso_week

log = logging.getLogger(__name__)

#: The harness market type each `parlay_legs.market_type` maps back to, for the price re-read.
#: `prop` maps to itself and is a routing key, not a table column: a prop leg's price lives in
#: `odds_prop_snapshots` under `prop:<stat>` (D22, D23), never in `odds_snapshots`, and the
#: lookup below branches on it rather than asking the game-line table a question it cannot
#: answer. `parlay_legs.market_type` is `prop` exactly for every prop family.
_BACK = {"ml": "moneyline", "spread": "spread", "total": "total", "prop": "prop"}

#: At most this many corrections to one card (addendum 5.2). A slip the owner has corrected
#: twenty times is not a slip any more; the twenty-first is a conversation, not a route call.
CORRECTIONS_MAX = 20

#: `parlay_placement_corrections.old_value` / `new_value` are String(32) and a value past that
#: is refused, never truncated (addendum 9): a truncated stake is a wrong number, silently.
VALUE_MAX = 32

#: `parlay_placement_corrections.field` is String(16), which holds the 5.2 names as written
#: (`leg_status:<seq>` is 13 characters at two digits). Longer is refused for the same reason.
FIELD_MAX = 16

#: The two field kinds of 5.2 that carry a `:<seq>` suffix. Every other kind carries none, and
#: a suffix on one of those is a field the table does not define, not a field with a comment.
_LEG_FIELDS = ("leg_line", "leg_status")


class CardNotPlaceable(RuntimeError):
    """No such card, or a card that is not `proposed`."""


class BudgetExceeded(RuntimeError):
    """This week's stakes plus the new one would exceed the weekly budget.

    Carries the two figures the route reports to the owner (`recorded`, `left`): a refusal that
    only said "no" would leave the sheet unable to say how much of the $50 is actually left.
    """

    def __init__(self, recorded: Decimal, left: Decimal, budget: Decimal) -> None:
        super().__init__(f"${recorded} already staked this week; only ${left} of "
                         f"${budget} remains")
        self.recorded, self.left = recorded, left


class ConfirmationReused(RuntimeError):
    """This confirmation id is already recorded against a different card (addendum 5.3)."""


class CorrectionNotAllowed(RuntimeError):
    """The `(card state, field, new_value)` triple is not in the closed table of 5.2."""


class CorrectionsCapped(RuntimeError):
    """This card already carries `CORRECTIONS_MAX` correction rows."""


class LineMoved(RuntimeError):
    """One or more legs' DraftKings lines have moved and were not confirmed."""

    def __init__(self, legs: dict[int, tuple]) -> None:
        moved = ", ".join(f"leg {seq}: {was} -> {now}" for seq, (was, now) in legs.items())
        super().__init__(f"the line moved and was not confirmed: {moved}. "
                         f"Re-run with --leg-line <seq>=<point> for each.")
        self.legs = legs


_WEEK_STAKED = text("""
    select coalesce(sum(amount), 0) from parlay_ledger
    where kind = 'stake' and year = :year and week = :week
""")   # `parlay_ledger` is small (a week of fun-money rows) and carries no index: a walk.

_WEEK_LOCK = text("select pg_advisory_xact_lock(hashtext('parlay_week:' || :year || ':' || :week))")
_CARD_FOR_UPDATE = text("select status from parlay_cards where id = :id for update")

#: The newest DraftKings prop row for one leg's outcome (addendum 3.3; D23). Keyed by the
#: player, which is exactly what `odds_snapshots` cannot key on, and by the internal
#: `prop:<stat>` market key the normalizer writes. Rides `ix_odds_prop_lookup
#: (game_id, market_type, player_id, fetched_at desc) where player_id is not null`, whose
#: leading three columns are this statement's three equalities and whose fourth is its order.
_NEWEST_PROP = text("""
    select point, fetched_at from odds_prop_snapshots
    where book = 'draftkings' and game_id = :game_id and market_type = :market_type
      and player_id = :player_id
      and outcome_side is not distinct from :side
      and fetched_at <= :now
    order by fetched_at desc
    limit 1
""")


def _lock_week(session: Session, year: int, week: int) -> None:
    """Serialize every write against one fun-money week (addendum 5.1 step 1).

    A transaction-scoped advisory lock, not a row lock: the thing being protected is the *sum*
    over `parlay_ledger` for a week, which no single row owns. Two different cards submitted at
    the cap from a phone and a laptop meet here; two submits of one card meet at the row lock
    below. `mark_placed` takes it too, so the CLI path is covered by the same serialization.
    """
    session.execute(_WEEK_LOCK, {"year": year, "week": week})


def _lock_card(session: Session, card: ParlayCard) -> str | None:
    """The card row's own lock, taken after the week lock, returning its committed `status`.

    `session.expire` first, so every attribute read after this point is the row under the lock
    rather than whatever this session loaded before it waited. `(year, week)` are read before
    the lock and never change on a card, which is why locking the week first is safe.
    """
    session.expire(card)
    return session.execute(_CARD_FOR_UPDATE, {"id": card.id}).scalar()


def week_staked(session: Session, year: int, week: int) -> Decimal:
    return session.execute(_WEEK_STAKED, {"year": year, "week": week}).scalar() or Decimal("0")


def _payout_from_american(stake: Decimal, american_odds: int) -> Decimal:
    """The slip's total return at the odds the operator actually got."""
    multiple = (Decimal("1") + Decimal(american_odds) / 100
                if american_odds >= 0
                else Decimal("1") + Decimal("-100") / Decimal(american_odds))
    return (stake * multiple).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _newest_prop_point(session: Session, leg: ParlayLeg, now: datetime,
                       max_age: timedelta) -> Decimal | None:
    """The line the newest fresh DraftKings prop row carries for this leg, or None.

    Local to this module on purpose while Task 6's `pricing.newest_dk_prop_price` is on another
    branch: a prop leg must never be resolved through `odds_snapshots`, and a placement that
    silently skipped the re-read because the helper was missing would confirm a moved line.
    Carry-forward: fold this into `pricing.newest_dk_prop_price` once the two branches meet.
    """
    if leg.player_id is None or not leg.stat:
        return None                # an unresolved prop has nothing to compare against
    row = session.execute(_NEWEST_PROP, {"game_id": leg.game_id,
                                         "market_type": f"prop:{leg.stat}",
                                         "player_id": leg.player_id, "side": leg.side,
                                         "now": now}).first()
    if row is None or now - row.fetched_at > max_age:
        return None
    return row.point


def _moved_legs(session: Session, legs: list[ParlayLeg], accepted: dict[int, Decimal],
                now: datetime, max_age: timedelta) -> dict[int, tuple]:
    """Every leg whose DraftKings line has moved and was not confirmed with `--leg-line`."""
    moved: dict[int, tuple] = {}
    for leg in legs:
        if leg.seq in accepted:
            leg.threshold = Decimal(str(accepted[leg.seq]))
            continue
        if leg.market_type == "prop":
            point = _newest_prop_point(session, leg, now, max_age)
        else:
            price = newest_dk_price(session, leg.game_id, _BACK[leg.market_type],
                                    leg.side_team_id, leg.side, now, max_age)
            point = price.point if price is not None else None
        if point is None:
            continue          # no fresh row to compare against; the operator's slip stands
        if point != leg.threshold:
            moved[leg.seq] = (leg.threshold, point)
    return moved


def mark_placed(session: Session, card_id: int, payout_american: int, stake: Decimal,
                now: datetime, leg_lines: dict[int, Decimal] | None = None, *,
                confirmation_id: str | None = None,
                note: str | None = None) -> ParlayPlacement:
    """Confirm one hand-placed slip. Writes the placement and the ledger's stake row.

    `confirmation_id` and `note` are keyword-only with defaults, so `harness parlay placed` is
    unchanged; the confirm sheet passes both (addendum 5.1).
    """
    config = load_config()
    card = session.get(ParlayCard, card_id)
    if card is None:
        raise CardNotPlaceable(f"card {card_id} is absent, not proposed")

    # The week lock before anything else, then the card row: the cap is a sum over a week that
    # no single row owns, and two different cards at the cap can only be serialized here.
    _lock_week(session, card.year, card.week)
    status = _lock_card(session, card)
    if status != "proposed":
        raise CardNotPlaceable(
            f"card {card_id} is {'absent' if status is None else status}, not proposed")

    if confirmation_id is not None:
        other = session.query(ParlayPlacement).filter(
            ParlayPlacement.confirmation_id == confirmation_id,
            ParlayPlacement.card_id != card.id).first()
        if other is not None:
            # Refused before a single write: the same confirmation against a second card is a
            # corrupted ledger, not a duplicate, and the unique index is the backstop not the
            # check.
            raise ConfirmationReused(
                f"confirmation {confirmation_id} is already recorded on card {other.card_id}")

    # D20: the card's own week, not the placement instant's. A Saturday-built card placed on
    # Monday counts against its slate's $50, and `week_staked` reads the same key.
    # ROUND_HALF_UP, as everywhere money is quantized in this repo and as `numeric(10,2)`
    # itself rounds: decimal's default half-even would store a slip's $25.005 as $25.00 while
    # the column's own cast of the same string gives $25.01 (fix round 1, IM-2).
    stake = Decimal(str(stake)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    already = week_staked(session, card.year, card.week)
    if already + stake > config.weekly_budget:
        raise BudgetExceeded(already, config.weekly_budget - already, config.weekly_budget)

    legs = session.query(ParlayLeg).filter_by(card_id=card.id).order_by(ParlayLeg.seq).all()
    moved = _moved_legs(session, legs, leg_lines or {}, now,
                        timedelta(minutes=config.leg_max_age_minutes))
    if moved:
        raise LineMoved(moved)

    placement = ParlayPlacement(card_id=card.id, placed_at=now, stake_actual=stake,
                                dk_payout_actual=_payout_from_american(stake, payout_american),
                                dk_odds_actual=payout_american,
                                note=sanitize_reason(note) if note else None,
                                confirmation_id=confirmation_id)
    session.add(placement)
    # `confirmed`, not `computed`: the owner typed this figure off their own slip.
    session.add(ParlayLedger(ts=now, card_id=card.id, kind="stake", amount=stake,
                             year=card.year, week=card.week, source="confirmed"))
    card.status = "placed"
    for leg in legs:
        leg.status = "alive"
    session.flush()
    log.info("parlay card %s placed: $%s at %+d", card.id, stake, payout_american)
    return placement


#: The two open-ended value kinds of the table: a decimal or an integer the owner read off
#: their slip. Spelled as sentinels so `TRANSITIONS` stays one closed table with its literal
#: values (`declined`, `void`) beside them, rather than a table plus a second rule elsewhere.
ANY_DECIMAL = "<decimal>"
ANY_INT = "<int>"

#: Addendum 5.2 exactly: `(card state, field kind) -> the accepted new values`. A triple that
#: is not in here is refused. Note what is absent and cannot be reached from any route:
#: `cashed`, `busted`, `placed` and `alive` are never writable -- they are the grader's words
#: about the world, not the owner's -- `stake`, `accepted_odds` and a leg's line freeze once the
#: card is graded, and a `proposed` card can only be declined.
TRANSITIONS: dict[tuple[str, str], set[str]] = {
    ("proposed", "status"): {"declined"},

    ("placed", "stake"): {ANY_DECIMAL},
    ("alive", "stake"): {ANY_DECIMAL},

    ("placed", "accepted_odds"): {ANY_INT},
    ("alive", "accepted_odds"): {ANY_INT},

    ("placed", "leg_line"): {ANY_DECIMAL},
    ("alive", "leg_line"): {ANY_DECIMAL},

    ("placed", "leg_status"): {"void"},
    ("alive", "leg_status"): {"void"},
    ("cashed", "leg_status"): {"void"},
    ("busted", "leg_status"): {"void"},

    ("placed", "return"): {ANY_DECIMAL},
    ("alive", "return"): {ANY_DECIMAL},
    ("cashed", "return"): {ANY_DECIMAL},

    ("placed", "status"): {"void"},
    ("alive", "status"): {"void"},
    ("cashed", "status"): {"void"},
    ("busted", "status"): {"void"},
}

#: A leg with one of these has been graded; its line is never corrected afterwards.
_LEG_GRADED = ("hit", "miss", "void")

#: Rides `ix_parlay_corrections_card_ts (card_id, ts)`, bounded to one card.
_CORRECTION_COUNT = text(
    "select count(*) from parlay_placement_corrections where card_id = :card_id")
#: `parlay_ledger` carries no index: it is a handful of fun-money rows a week ($50 of cards),
#: so this is a walk of a small table, bounded to one card and one kind.
_DROP_LEDGER_KIND = text("delete from parlay_ledger where card_id = :card_id and kind = :kind")


def _as_decimal(value: str) -> Decimal:
    """`value` as a finite Decimal, or `CorrectionNotAllowed`.

    `Decimal("nan")` and `Decimal("inf")` parse, and either one in the ledger is a number that
    poisons every sum taken over it afterwards.
    """
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError) as exc:
        raise CorrectionNotAllowed(f"{value!r} is not a decimal") from exc
    if not parsed.is_finite():
        raise CorrectionNotAllowed(f"{value!r} is not a finite decimal")
    return parsed


def _as_int(value: str) -> int:
    try:
        return int(str(value))
    except (ValueError, TypeError) as exc:
        raise CorrectionNotAllowed(f"{value!r} is not an integer") from exc


def _leg_of(session: Session, card: ParlayCard, seq_text: str) -> ParlayLeg | None:
    try:
        seq = int(seq_text)
    except (ValueError, TypeError):
        return None
    return session.query(ParlayLeg).filter_by(card_id=card.id, seq=seq).one_or_none()


def _confirm_pay(session: Session, card: ParlayCard, kind: str, amount: Decimal,
                 now: datetime) -> None:
    """The owner's own figure for a card's `return` or `void`, as the *only* row of that kind.

    One row per `(card_id, kind)` is the invariant the season figures and 9's return/void
    queries rest on, and `parlay_ledger` has no unique key to enforce it: a confirmed figure
    **replaces** the grader's computed row rather than being added beside it, and a second
    confirmation replaces the first. The stake rows are the deliberate exception -- they are
    signed deltas that sum to `stake_actual`.
    """
    session.flush()
    session.execute(_DROP_LEDGER_KIND, {"card_id": card.id, "kind": kind})
    session.add(ParlayLedger(ts=now, card_id=card.id, kind=kind,
                             amount=amount.quantize(Decimal("0.01"),
                                                    rounding=ROUND_HALF_UP),
                             year=card.year, week=card.week, source="confirmed"))


def _old_value(session: Session, card: ParlayCard, placement: ParlayPlacement | None,
               leg: ParlayLeg | None, kind: str, status: str) -> str | None:
    """What the correction row records as the value it replaced, or None when there is none."""
    if kind == "status":
        return status
    if kind == "stake":
        # Read before the table lookup refuses a placement-free card, so it stays None-safe.
        return None if placement is None else str(placement.stake_actual)
    if kind == "accepted_odds":
        if placement is None or placement.dk_odds_actual is None:
            return None
        return str(placement.dk_odds_actual)
    if kind == "leg_line":
        return None if leg is None or leg.threshold is None else str(leg.threshold)
    if kind == "leg_status":
        return None if leg is None else leg.status
    if kind == "return":
        paid = session.query(ParlayLedger).filter_by(card_id=card.id, kind="return").first()
        return None if paid is None else str(paid.amount)
    return None


def _check_allowed(status: str, kind: str, field: str, seq_text: str, new_value: str,
                   leg: ParlayLeg | None) -> None:
    """The closed lookup of 5.2, plus the two guards the table cannot express in a set."""
    accepted = TRANSITIONS.get((status, kind))
    if accepted is None:
        raise CorrectionNotAllowed(
            f"a {status} card has no correction {field!r} in the table")
    if ANY_DECIMAL in accepted:
        _as_decimal(new_value)
    elif ANY_INT in accepted:
        _as_int(new_value)
    elif new_value not in accepted:
        raise CorrectionNotAllowed(
            f"{field} = {new_value!r} is not accepted on a {status} card")
    if kind not in _LEG_FIELDS and seq_text:
        raise CorrectionNotAllowed(f"{field!r} is not a field the table defines")
    if kind in _LEG_FIELDS:
        if leg is None:
            raise CorrectionNotAllowed(f"card has no leg for {field!r}")
        if kind == "leg_line" and leg.status in _LEG_GRADED:
            raise CorrectionNotAllowed(
                f"leg {leg.seq} is {leg.status}: a graded leg's line is never corrected")


def _recorded_value(kind: str, new_value: str) -> str:
    """The value the correction row persists, which is the Effect column's, not always the one
    the owner submitted (fix round 1, IM-1).

    The only divergence is the decline: 5.4 has the route body carry `new_value = "declined"`
    and 5.2's Effect column records the row as `(status, proposed, void)`, because `void` is
    what the card becomes. The owner's own word is not lost -- `declined_reason` holds it.
    """
    return "void" if kind == "status" and new_value == "declined" else new_value


def _apply(session: Session, card: ParlayCard, placement: ParlayPlacement | None,
           leg: ParlayLeg | None, kind: str, new_value: str, now: datetime) -> None:
    """The Effect column of 5.2, and nothing else."""
    if kind == "status" and new_value == "declined":
        card.status, card.declined_reason = "void", "declined"
        return                                  # a card nobody placed moved no money
    if kind == "status":                        # new_value == "void" (the table's only other)
        # `parlay_cards.stake` is not-null, so the fallback is a figure, never None: a card
        # can reach this branch with no placement row only through hand-made data.
        stake = placement.stake_actual if placement is not None else card.stake
        _confirm_pay(session, card, "void", Decimal(str(stake)), now)
        card.status = "void"
        return
    if kind == "return":
        _confirm_pay(session, card, "return", _as_decimal(new_value), now)
        card.status = "cashed"
        return
    if kind == "stake":
        _correct_stake(session, card, placement, _as_decimal(new_value), now)
        return
    if kind == "accepted_odds":
        odds = _as_int(new_value)
        placement.dk_odds_actual = odds
        placement.dk_payout_actual = _payout_from_american(placement.stake_actual, odds)
        return
    if kind == "leg_line":
        leg.threshold = _as_decimal(new_value)
        return
    if kind == "leg_status":                    # new_value == "void"
        leg.status, leg.graded_at = "void", now


def _correct_stake(session: Session, card: ParlayCard, placement: ParlayPlacement,
                   new_stake: Decimal, now: datetime) -> None:
    """`stake_actual` set and the signed delta written, on the card's week.

    A delta rather than a replacement row, so `sum(stake rows) = stake_actual` stays the
    per-card invariant of 9 and the week's cap keeps reading one column.
    """
    config = load_config()
    new_stake = new_stake.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    delta = new_stake - Decimal(str(placement.stake_actual))
    if delta > 0:
        already = week_staked(session, card.year, card.week)
        if already + delta > config.weekly_budget:
            raise BudgetExceeded(already, config.weekly_budget - already, config.weekly_budget)
    placement.stake_actual = new_stake
    if delta:
        session.add(ParlayLedger(ts=now, card_id=card.id, kind="stake", amount=delta,
                                 year=card.year, week=card.week, source="confirmed"))


def apply_correction(session: Session, card_id: int, field: str, new_value: str,
                     now: datetime, note: str | None = None) -> ParlayPlacementCorrection:
    """One owner correction to one card, through the closed table of addendum 5.2.

    The order is deliberate and every step refuses before the next one writes: the card, the
    week lock and then the row lock; the 32-character limit (refused, never truncated); the
    twenty-row cap; the table itself; and only then the effect, one correction row, and the
    `confirmed` ledger row the three money-moving rows of the table call for.
    """
    card = session.get(ParlayCard, card_id)
    if card is None:
        raise CardNotPlaceable(f"card {card_id} is absent")
    _lock_week(session, card.year, card.week)
    status = _lock_card(session, card)
    if status is None:
        raise CardNotPlaceable(f"card {card_id} is absent")

    kind, _, seq_text = field.partition(":")
    if len(field) > FIELD_MAX:
        raise ValueError(f"a correction field is at most {FIELD_MAX} characters")
    leg = _leg_of(session, card, seq_text) if kind in _LEG_FIELDS else None
    placement = session.get(ParlayPlacement, card.id)
    old_value = _old_value(session, card, placement, leg, kind, status)
    for value in (old_value, new_value):
        if value is not None and len(str(value)) > VALUE_MAX:
            raise ValueError(
                f"a correction value is at most {VALUE_MAX} characters and is never truncated")

    if (session.execute(_CORRECTION_COUNT, {"card_id": card.id}).scalar() or 0) >= CORRECTIONS_MAX:
        raise CorrectionsCapped(
            f"card {card_id} already carries {CORRECTIONS_MAX} corrections")

    _check_allowed(status, kind, field, seq_text, new_value, leg)
    if placement is None and kind != "status":
        # 9's invariant: no corrections row whose card has no placement and whose field is not
        # `status`. `mark_placed` writes the placement and the stake row in one transaction, so
        # this is unreachable from either write path; refusing keeps it unreachable by
        # construction rather than by habit, and there is no money to correct here anyway.
        raise CorrectionNotAllowed(
            f"card {card_id} has no placement: only a status correction applies")
    _apply(session, card, placement, leg, kind, new_value, now)
    row = ParlayPlacementCorrection(card_id=card.id, ts=now, field=field, old_value=old_value,
                                    new_value=_recorded_value(kind, new_value),
                                    note=sanitize_reason(note) if note else None)
    session.add(row)
    session.flush()
    log.info("parlay card %s corrected: %s %r -> %r", card.id, field, old_value, new_value)
    return row


def expire_cards(session: Session, now: datetime) -> int:
    """Void every `proposed` card older than the config's expiry (ruling B-C4). Returns how many.

    No ledger row: a card nobody placed cost nothing and moved no money.
    """
    cutoff = now - timedelta(days=load_config().expiry_days)
    stale = session.query(ParlayCard).filter(
        ParlayCard.status == "proposed", ParlayCard.built_at < cutoff).all()
    for card in stale:
        card.status = "void"
        for leg in session.query(ParlayLeg).filter_by(card_id=card.id):
            leg.status = "void"
            leg.graded_at = now
    if stale:
        session.flush()
        log.info("expired %d proposed parlay cards to void", len(stale))
    return len(stale)


_SHOW = text("""
    select c.id, c.year, c.week, c.sport, c.kind, c.status, c.stake, c.dk_payout_est,
           c.true_prob_est, c.hold_est, c.correlated, c.rationale, p.placed_at, p.stake_actual,
           p.dk_payout_actual, p.dk_odds_actual
    from parlay_cards c
    left join parlay_placements p on p.card_id = c.id
    order by c.built_at desc
    limit 20
""")


def show_cards(session: Session, now: datetime) -> list[dict]:
    """The recent cards and this week's remaining budget, for `harness parlay show`."""
    config = load_config()
    # This is the one week key read off the clock rather than off a card: "how much of *this*
    # week's $50 is left", asked now. The ledger's own rows are keyed to their card (D20).
    year, week = chicago_iso_week(now)
    remaining = config.weekly_budget - week_staked(session, year, week)
    rows = []
    for row in session.execute(_SHOW):
        rows.append({"card_id": row.id, "year": row.year, "week": row.week, "sport": row.sport,
                     "kind": row.kind, "status": row.status, "stake": row.stake,
                     "payout_est": row.dk_payout_est, "payout_actual": row.dk_payout_actual,
                     "true_prob": row.true_prob_est, "hold": row.hold_est,
                     "correlated": row.correlated, "placed_at": row.placed_at,
                     "rationale": row.rationale, "week_remaining": remaining})
    return rows
