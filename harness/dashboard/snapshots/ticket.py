"""Ticket: the week's fun parlays.

Spec §2.5 and addendum §1. This is the one surface that shows **real money** -- the owner's
$50-a-week fun budget, placed by hand at DraftKings -- and the one that is allowed to be loud.
Its badge says so instead of saying PAPER, and paper and fun money never appear on the same
surface.

Three sections, in this order (addendum §1): **this week's ideas**, the draft slips the builder
stage proposed and the written reason for every slot that has none; **live tickets**, the placed
and settled cards; and **season**. The ideas read is its own bounded query over the week's
`proposed` cards (B-I17) -- `_LIVE_CARDS` never sees them, because a proposal is an idea and a
ticket is money that has moved.

**Where every number on a card comes from.** A prop leg's stat line is the newest
`player_stat_events` row for its `(game_id, player_id, stat)`, with its age and its source
named; a player absent from the latest update reads `unchanged`, never a zero. The settlement
line says who did the arithmetic: a `computed` ledger row is the harness's own (it reads
`expected ... awaiting your confirmation` and stamps nothing), and `CASHED` or `VOID` land only
on the owner's `confirmed` row (D18). A card the owner has corrected shows the mark and the
figures it replaced.

**Never shown here.** Any paper number, any research variant, any CLV. No DraftKings account
state. Placement and corrections are the owner's own writes through the LAN routes; this builder
only reads what they recorded, and every string it renders but `dk_link` passes `_display`,
this page's own display-safe sanitizer (the link is validated at normalize time, A-C2, and
sanitizing it at all would corrupt the query string the deep link needs).
"""

import logging
import re
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.dashboard import sentences
from harness.dashboard.snapshots import base_payload, register_builder, section
from harness.parlay.config import load_config
from harness.parlay.needs import FINAL_STATUSES, StatState, leg_spec, needs, score_state
from harness.parlay.placement import CORRECTIONS_MAX
from harness.settlement.parlay_build import BUILD_TIMES, SLOTS, read_slot_state, slot_key
from harness.telemetry import SUMMARY_MAX
from harness.weeks import CHICAGO, chicago_iso_week

log = logging.getLogger(__name__)

#: 30 s inside a game window while a card is live, 120 s otherwise. Halved from 15/60 by fix
#: 31: a parlay leg's score and its "sharps say NN %" move on the recorder's own tick, which is
#: 30 s, so polling twice a tick only ever re-read the same rows.
CADENCE_IN_WINDOW_S = 30
CADENCE_OUT_S = 120
#: The badge, in place of PAPER, on this surface and no other.
BADGE = "FUN MONEY - $50/WEEK - PLACED BY HAND"
#: How much of a leg's probability history the small bar shows (spec §2.5 item 1).
PROB_WINDOW = timedelta(hours=6)
#: The standing rule for what a card must carry (spec §2.5 item 3).
ANCHOR_RULE = "every card carries an LSU or Saints leg"
#: How far back `_SCORES` looks for a leg's newest score row (fix 31). A leg on a live or placed
#: card belongs to a game of this week, and twelve hours covers the longest one; it is what
#: prunes inside each game's range of `ix_game_score_events_game_ts` instead of walking it.
SCORES_WINDOW = timedelta(hours=12)
#: The cap on the streak walk (fix 31). A season is about twenty cards, so a streak longer than
#: this cannot exist; the cap is what keeps the read from growing with the table year on year.
STREAK_LIMIT = 60

#: A stat row older than this reads `unchanged ... last seen`, never a zero (addendum §1.3). Two
#: collector ticks of the in-game cadence this file already names: one missed poll is jitter,
#: two means the player was absent from the latest update. Derived, never a second number.
STAT_UNCHANGED_AFTER_S = 2 * CADENCE_IN_WINDOW_S
#: How far back `_STAT_LINES` looks. The same window as the scores: a prop leg's game is this
#: week's, and a value older than the game is not a live stat line.
STAT_WINDOW = SCORES_WINDOW
#: `_IDEAS`' row cap: the six slots of `SLOTS`, each able to carry a card and the one
#: replacement a decline earns it (addendum §2.4).
IDEAS_LIMIT = len(SLOTS) * 2
#: `_CORRECTIONS`' row cap: the write path caps one card at `CORRECTIONS_MAX` rows
#: (`harness/parlay/placement.py`), and `_LIVE_CARDS` shows at most ten cards.
CORRECTIONS_LIMIT = CORRECTIONS_MAX * 10
#: `_CARD_LEDGER`'s row cap: three kinds (`stake`, `return`, `void`) over the cards on the page,
#: with room for the signed stake deltas a correction writes.
LEDGER_LIMIT = 200
#: How soon the builder stage tries an empty slot again: it is a stage of the hourly settle job
#: (addendum §2.4), so a slot that recorded a reason at 14:05 is retried on the next hour's run.
BUILD_RETRY = timedelta(hours=1)

TICKET_KEYS = frozenset({"build_sha", "now", "cadence_s", "sentences", "readings",
                         "badge", "cards", "ideas", "season", "between", "sentences_gaps"})

#: Phase 4.6 adds columns to this select list, and one term to its predicate: a `proposed` card
#: is still invisible to it (B-I17), and so is a card the owner declined or one that expired
#: unplaced. Both of those are `void` **with a `declined_reason`**, they have no ledger row and
#: they moved no money (addendum §1.4, design §2.5): showing one among the live tickets put a
#: stake, a payout and the sentence "Real money, placed by hand at DraftKings" on a bet nobody
#: made, while the season strip showed the same card as a grey chip (review round 1, C1). A
#: `void` card with no reason -- the grader's refund on a fully pushed card, or the owner's own
#: confirmed void -- is money that did move and stays here.
#: The ordering and the cap are the shipped ones. The ideas section reads its own statement.
_LIVE_CARDS = text("""
    select c.id, c.year, c.week, c.sport, c.kind, c.built_at, c.stake, c.dk_payout_est,
           c.true_prob_est, c.hold_est, c.rationale, c.status, c.correlated, c.anchor_leg_id,
           c.policy_version, c.combined_kind, c.dk_combined_american, c.dk_combined_at,
           c.link_capability, c.p_source_min,
           p.placed_at, p.stake_actual, p.dk_payout_actual, p.dk_odds_actual
    from parlay_cards c
    left join parlay_placements p on p.card_id = c.id
    where c.status in ('placed', 'alive', 'cashed', 'busted', 'void')
      and c.declined_reason is null
    order by case when c.status in ('placed', 'alive') then 0 else 1 end, c.built_at desc
    limit 10
""")
#: Bound: the current Chicago `(year, week)` and a row cap. Index: `ix_parlay_cards_week
#: (year, week)`. Its own statement, so the live list above is untouched (B-I17): a proposal is
#: an idea, and the two lists can never show the same card. The four placement columns are
#: selected as nulls so one renderer serves a draft slip and a live slip from the same row
#: shape (design §8.1) -- a `proposed` card has no placement by definition.
_IDEAS = text("""
    select c.id, c.year, c.week, c.sport, c.kind, c.built_at, c.stake, c.dk_payout_est,
           c.true_prob_est, c.hold_est, c.rationale, c.status, c.correlated, c.anchor_leg_id,
           c.policy_version, c.combined_kind, c.dk_combined_american, c.dk_combined_at,
           c.link_capability, c.p_source_min,
           null::timestamptz as placed_at, null::numeric as stake_actual,
           null::numeric as dk_payout_actual, null::integer as dk_odds_actual
    from parlay_cards c
    where c.status = 'proposed' and c.year = :year and c.week = :week
    order by c.built_at desc
    limit :limit
""")
_LEGS = text("""
    select l.id, l.card_id, l.seq, l.game_id, l.market_type, l.side_team_id, l.side,
           l.threshold, l.dk_american, l.dk_decimal, l.plain_text, l.status,
           l.player_id, l.stat, l.period, l.operator, l.offered, l.p_at_build, l.p_source,
           l.context_text, l.dk_link,
           g.home_team_id, g.away_team_id, g.kickoff_utc, g.status as game_status
    from parlay_legs l
    left join games g on g.id = l.game_id
    where l.card_id = any(:card_ids)
    order by l.card_id, l.seq
""")
#: Bound: `ts >= :since` (`SCORES_WINDOW`, 12 h). Index: `ix_game_score_events_game_ts
#: (game_id, ts desc)` -- the game ids seek, the `ts` predicate prunes inside each game's range
#: so the `distinct on` reads the head of a bounded run (fix 31).
_SCORES = text("""
    select distinct on (game_id) game_id, ts, status, period, clock, home_score, away_score
    from game_score_events where game_id = any(:game_ids) and ts >= :since
    order by game_id, ts desc
""")
_LEG_PROBS = text("""
    select distinct on (leg_id) leg_id, ts, sharp_p, book_p
    from parlay_leg_probs where leg_id = any(:leg_ids)
    order by leg_id, ts desc
""")
#: Spec §2.5 asks for "sharps say NN %" **with a small history bar**, so the newest row is not
#: enough: the last 6 h of a leg's probability, which is what `legProbHistory` draws. Bounded by
#: the leg ids of the live cards and by the window, and `parlay_leg_probs` is a few hundred rows
#: per leg per game.
_LEG_PROB_HISTORY = text("""
    select leg_id, ts, sharp_p from parlay_leg_probs
    where leg_id = any(:leg_ids) and ts >= :since
    order by leg_id, ts
""")
#: Bound: the prop legs' own `(game_id, player_id)` id lists and `ts >= :since` (`STAT_WINDOW`,
#: 12 h). Index: `ix_player_stat_game_player_ts (game_id, player_id, ts desc)` -- the ids seek,
#: the `ts` predicate prunes inside each player's range, and `rn <= 2` takes the head of a
#: bounded run: the value the slip shows and the one before it, which is the `corrected from 12`
#: half of a correction note. `stat` is residual, exactly as in `parlay_grade._NEWEST_STAT`, so
#: the run walked is one player's rows in one game.
_STAT_LINES = text("""
    select game_id, player_id, stat, ts, value, source, correction, rn from (
        select game_id, player_id, stat, ts, value, source, correction,
               row_number() over (partition by game_id, player_id, stat
                                  order by ts desc, id desc) as rn
        from player_stat_events
        where game_id = any(:game_ids) and player_id = any(:player_ids) and ts >= :since
    ) newest
    where rn <= 2
    order by game_id, player_id, stat, rn
""")
#: Bound: the shown cards' ids and a row cap. Index: `ix_parlay_corrections_card_ts
#: (card_id, ts)`, whose leading column is the id list and whose second orders each card's rows
#: oldest first, which is the order the `<details>` shows them in.
_CORRECTIONS = text("""
    select card_id, ts, field, old_value, new_value from parlay_placement_corrections
    where card_id = any(:card_ids)
    order by card_id, ts
    limit :limit
""")
#: Bound: the shown cards' ids and a row cap. `parlay_ledger` carries no index and is a handful
#: of fun-money rows a week ($50 of cards), so this is the same small walk `_SEASON` already
#: does, narrowed to the cards on the page. `source` is the column that separates the harness's
#: computed arithmetic from the owner's confirmed figure (D18) and is why this read exists.
_CARD_LEDGER = text("""
    select card_id, kind, amount, source from parlay_ledger
    where card_id = any(:card_ids)
    order by card_id, id
    limit :limit
""")
#: Phase 4.6 (addendum §1.4) groups by `source` as well as by kind: the season tiles still read
#: one figure per kind, and `expected` is the part of `returned` the harness computed and the
#: owner has not confirmed yet, which the surface shows beside it rather than inside it.
_SEASON = text("""
    select kind, source, coalesce(sum(amount), 0) as total from parlay_ledger
    group by kind, source
""")
_WEEK_STAKED = text("""
    select coalesce(sum(amount), 0) from parlay_ledger
    where kind = 'stake' and year = :year and week = :week
""")
#: The season strip is every settled or live ticket, never a card still awaiting a hand
#: placement -- the same "not proposed" vocabulary `_LIVE_CARDS` reads, plus `void`.
_STRIP = text("""
    select id, year, week, kind, status, stake, dk_payout_est, declined_reason
    from parlay_cards
    where status in ('placed', 'alive', 'cashed', 'busted', 'void')
    order by built_at desc limit 40
""")
#: Spec §2.5 season-strip metric "best hit": the largest amount ever returned on a cashed card.
_BEST_HIT = text("""
    select l.card_id, c.week, l.amount
    from parlay_ledger l
    join parlay_cards c on c.id = l.card_id
    where l.kind = 'return' and c.status = 'cashed'
    order by l.amount desc
    limit 1
""")
#: Spec §2.5 season-strip metric "the current streak": walked in `_season` into a signed run of
#: consecutive cashed (+) or busted (-) cards, most recent first. A card still `placed` or
#: `alive` has no verdict yet and does not belong in a streak of verdicts.
_STREAK_CARDS = text("""
    select status from parlay_cards
    where status in ('cashed', 'busted')
    order by built_at desc
    limit :limit
""")


#: This page's own display sanitizer (review round 1, I4). `harness.telemetry.sanitize_reason`
#: strips everything outside `[\w \-.,:/()]`, which on this surface silently rewrites the bet
#: itself: "Nussmeier 225+ passing yards" becomes "Nussmeier 225 passing yards" and the
#: builder's "avg 262 · last 241 · ESPN" runs together. `+` says which side of a prop line
#: the bet is on and the middle dot is this surface's own separator, so both are kept here --
#: and nothing else is: the class below is the shared one plus those two characters, so `<`,
#: `>`, quotes, braces, backslashes, newlines and every other markup character are stripped
#: exactly as before, and the shared length cap still applies. `harness/telemetry.py` is not
#: touched: its class is the right one for reason codes and log summaries, which is what it
#: guards everywhere else (addendum §1.1 mandates *a* sanitizer on every string but `dk_link`;
#: this is that sanitizer, for the page whose strings are bet names).
_DISPLAY_SAFE = re.compile(r"[^\w \-.,:/()+\u00b7]")


def _display(text_value) -> str:
    """One recorded string, safe to render, with the bet's own punctuation intact."""
    return _DISPLAY_SAFE.sub("", text_value or "")[:SUMMARY_MAX]


def _dec(value):
    return float(value) if value is not None else None


def _money(value) -> str | None:
    """A money figure in a payload: a decimal string with two places, never a float (plan
    review MI-6). The two shipped keys `stake` and `payout` keep the floats the front end
    already draws; every figure this phase adds is a string."""
    if value is None:
        return None
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def _american(value) -> str:
    """A DraftKings price as the slip prints it: `+450`, `-115`."""
    return sentences.PLACEHOLDER if value is None else f"{int(value):+d}"


def _selection(leg, price_age_s) -> str:
    """The exact selection in technical terms (design §2.1): the market, the line, the period,
    the recorded DraftKings price and that price's age.

    Composed from the leg's own recorded columns and from nothing else. The player's and the
    team's names are in `plain_text`, which the builder wrote at build time -- this surface does
    not read `players` or `teams` to spell a name a second way, and the two lines are meant to
    be read together: the fan's sentence above, the exact bet below it.
    """
    if leg.market_type == "prop":
        parts = [leg.stat or "prop", leg.operator or "", sentences.fmt_stat(leg.threshold)
                 if leg.threshold is not None else ""]
    else:
        parts = [leg.market_type, leg.side or "",
                 sentences.fmt_stat(leg.threshold) if leg.threshold is not None else ""]
    head = " ".join(part for part in parts if part)
    return (f"{_display(head)} · {_display(leg.period or 'game')} · "
            f"DK {_american(leg.dk_american)} · {sentences.fmt_age(price_age_s)} ago")


def _price_age_s(card_row, now: datetime) -> float | None:
    """How old the prices on this card are.

    `dk_combined_at` is the moment the card was priced: the build, or the last reprice of the
    recorder's `_parlay_reprice` source, which re-reads every leg of a `proposed` card together
    (addendum §2.3). One age for the slip, because one pass wrote all of its prices.
    """
    if card_row.dk_combined_at is None:
        return None
    return max(0.0, (now - card_row.dk_combined_at).total_seconds())


def _price_note(leg, card_row, price_age_s, config) -> str | None:
    """The one warning a leg's price line can carry (addendum §1.5).

    `offered = false` is written only inside the price window after a successful fetch that no
    longer returned the selection, so it is a fact about DraftKings and is stated on any card.
    The `not repriced` note is about *this* card's prices being stale while its event sits
    outside the window the reprice covers, so it is only ever said about a draft: a placed card
    is never repriced at all (EXPERIENCE-CONTRACTS §1), and telling its owner their prices are
    old would be telling them about a bet they have already made.
    """
    if not leg.offered:
        return "no longer offered by DraftKings"
    if card_row.status != "proposed" or price_age_s is None:
        return None
    if price_age_s <= config.leg_max_age_minutes * 60:
        return None
    if leg.kickoff_utc is None:
        return None
    window = timedelta(hours=config.props.prop_window_hours)
    if leg.kickoff_utc - card_row.dk_combined_at > window:
        return "not repriced · outside the price window"
    return None


def _game_is_final(leg) -> bool:
    """Whether this leg's game is over, read from the `games` row `_LEGS` already joins.

    Not from the newest `game_score_events` row (review round 1, I3/M4): that read is bounded to
    `SCORES_WINDOW` (12 h), so a game that finished yesterday has no row inside the window and a
    leg that has been hung all night would quietly go back to reading "no stat yet" -- which a
    reader takes to mean the game has not started. `games.status` is a column on a table bounded
    by the shape of the season, it needs no window, and it is the same status `parlay_grade`
    settles the leg from.
    """
    return (leg.game_status or "") in FINAL_STATUSES


def _stat_state(leg, row) -> StatState | None:
    """The newest recorded value of a prop leg's stat as `needs` reads it.

    `final` is the *game's*, exactly as `parlay_grade._stat_state` decides it: nothing on a stat
    row says "this came from the final box score", so the sentence and the grade both read the
    game's status and can never disagree about whether a value is provisional.
    """
    if row is None:
        return None
    return StatState(stat=leg.stat, value=Decimal(str(row.value)), source_ts=None,
                     final=_game_is_final(leg))


def _stat_text(leg, rows, score_row, needs_phrase, now: datetime):
    """The stat line and the correction note for one prop leg (design §2.3).

    `rows` is the newest recorded value and the one before it. Four states, each one a recorded
    fact rather than an inference: a fresh value reads the full line; a value that has stood for
    more than two collector ticks keeps its figure and says `unchanged` with its age, because a
    player who has not touched the ball writes no row and a zero there would be the surface
    inventing a fact; no row at all while the game is over reads `no final stat · pending` (the
    hung leg of addendum §1.3), with the age since the final whistle when a final score row is
    still inside `SCORES_WINDOW` and without one when it is not -- an age this builder cannot
    read is left unsaid rather than guessed; and no row at all before the game is over reads
    `no stat yet`.
    """
    if leg.market_type != "prop":
        return None, None
    newest = rows[0] if rows else None
    previous = rows[1] if len(rows) > 1 else None
    if newest is None:
        if _game_is_final(leg):
            age = (max(0.0, (now - score_row.ts).total_seconds())
                   if score_row is not None and score_row.status in FINAL_STATUSES else None)
            return sentences.stat_pending_final(age), None
        return "no stat yet", None
    age = max(0.0, (now - newest.ts).total_seconds())
    if age > STAT_UNCHANGED_AFTER_S:
        line = sentences.stat_unchanged(leg.stat, newest.value, leg.threshold, age)
    else:
        line = sentences.stat_line(leg.stat, newest.value, leg.threshold, needs_phrase, age)
    note = None
    if newest.correction and previous is not None:
        note = sentences.stat_correction_note(previous.value, newest.value)
    return line, note


def _settlement(card_row, card_legs, ledger_rows) -> tuple[dict, str | None]:
    """Who did the arithmetic on this card, in one sentence, and the stamp it earns (D18).

    A `computed` ledger row is the harness's own multiplication of what the owner recorded: it
    reads `expected ... awaiting your confirmation` and stamps nothing. `CASHED` and `VOID` land
    only on a `confirmed` row -- the owner's own figure, typed off their DraftKings slip through
    the correction route -- because those two words are claims about what a book paid, and the
    harness has no account to read. A correlated card gets no computed return at all: the
    product of its legs assumes an independence they do not have, so the slip says DraftKings
    will state the return.
    """
    def newest(kind, source):
        found = [row for row in ledger_rows if row.kind == kind and row.source == source]
        return found[-1] if found else None

    confirmed_void = newest("void", "confirmed")
    if confirmed_void is not None:
        return ({"kind": "confirmed", "amount": _money(confirmed_void.amount),
                 "text": "voided by DraftKings · stake returned"}, "VOID")
    confirmed_return = newest("return", "confirmed")
    if confirmed_return is not None:
        amount = _money(confirmed_return.amount)
        return ({"kind": "confirmed", "amount": amount,
                 "text": f"paid {sentences.fmt_money(confirmed_return.amount)} · "
                         "confirmed from your DraftKings slip"}, "CASHED")
    if card_row.status == "busted" or any(leg["status"] == "miss" for leg in card_legs):
        return ({"kind": None, "amount": None,
                 "text": "missed on the final score"}, "BUSTED")
    landed = bool(card_legs) and all(leg["status"] in ("hit", "void") for leg in card_legs)
    hit = any(leg["status"] == "hit" for leg in card_legs)
    computed_return = newest("return", "computed")
    if landed and hit:
        if card_row.correlated:
            return ({"kind": None, "amount": None,
                     "text": "all legs hit · DraftKings will state the return · "
                             "awaiting your confirmation"}, None)
        if computed_return is not None:
            return ({"kind": "computed", "amount": _money(computed_return.amount),
                     "text": "all legs hit · expected "
                             f"{sentences.fmt_money(computed_return.amount)} at your recorded "
                             "odds · awaiting your confirmation"}, None)
        return ({"kind": None, "amount": None,
                 "text": "all legs hit · awaiting your confirmation"}, None)
    computed_void = newest("void", "computed")
    if computed_void is not None:
        return ({"kind": "computed", "amount": _money(computed_void.amount),
                 "text": f"stake returned · {sentences.fmt_money(computed_void.amount)} "
                         "at your recorded stake · awaiting your confirmation"}, None)
    return ({"kind": None, "amount": None, "text": None}, None)


def _footer(card_row, legs, price_age_s, week, config) -> dict:
    """The four lines under a slip's legs (design §2.1, addendum §1.1).

    The chance line names the source of the number it is showing: `sharps say` only when every
    leg was built from a sharp fair, and otherwise `no sharp read` with the legs that carry no
    probability at all named and excluded, because a combined figure that quietly dropped a leg
    is a different bet's number. The hold line is only meaningful over independent legs priced
    against a sharp fair, so a correlated or book-priced card gets none rather than a figure
    DraftKings will never quote (D4).
    """
    combined = {
        "quoted": f"DraftKings quotes {_american(card_row.dk_combined_american)} for the slip",
        "calculated": f"{_american(card_row.dk_combined_american)} calculated from the legs "
                      "· assumes independence",
    }.get(card_row.combined_kind,
          "no combined price · DraftKings will quote it in the app")
    if card_row.combined_kind == "calculated" and card_row.correlated:
        combined += " · DraftKings will quote lower than this"
    chance = f"sharps say {sentences.fmt_prob(_dec(card_row.true_prob_est))} for the slip"
    if card_row.p_source_min != "sharp":
        chance = (f"no sharp read · "
                  f"{sentences.fmt_prob(_dec(card_row.true_prob_est))} from DraftKings' own "
                  "prices")
        # `p_source` is nullable, and a leg with a NULL source is exactly as unpriced as one
        # that says `none`: naming only the literal left it silently inside the combined figure
        # (review round 1, M2), which is the thing B-C8/D4 forbids.
        unpriced = [leg["plain_text"] for leg in legs if leg["p_source"] in (None, "none")]
        if unpriced:
            chance += " · " + ", ".join(unpriced) + " not included"
    hold = None
    if (card_row.p_source_min == "sharp" and not card_row.correlated
            and card_row.hold_est is not None):
        hold = sentences.fmt_pct(_dec(card_row.hold_est))
    age_warning = None
    if (card_row.status == "proposed" and price_age_s is not None
            and price_age_s > config.leg_max_age_minutes * 60):
        age_warning = (f"prices older than {config.leg_max_age_minutes} min · repriced at "
                       "the next tick")
    return {"combined": combined, "chance": chance, "hold": hold, "age_warning": age_warning,
            "week": f"{sentences.fmt_money(week['recorded'])} recorded · "
                    f"{sentences.fmt_money(week['left'])} left of "
                    f"{sentences.fmt_money(week['budget'])}"}


def _stat_rows(session, legs, now: datetime) -> dict:
    """The newest two recorded values per prop leg's `(game_id, player_id, stat)`, newest
    first. Empty when the page carries no prop leg, so the read is not issued at all."""
    keys = [(leg.game_id, leg.player_id) for leg in legs
            if leg.market_type == "prop" and leg.game_id is not None
            and leg.player_id is not None]
    if not keys:
        return {}
    rows: dict[tuple, list] = {}
    for row in session.execute(_STAT_LINES,
                               {"game_ids": sorted({key[0] for key in keys}),
                                "player_ids": sorted({key[1] for key in keys}),
                                "since": now - STAT_WINDOW}):
        rows.setdefault((row.game_id, row.player_id, row.stat), []).append(row)
    return rows


def _corrections(session, card_ids) -> dict[int, list]:
    """Every owner correction on the shown cards, oldest first per card (addendum §5.2). The
    values are the strings the write path recorded, never re-parsed into numbers here: what the
    `<details>` shows is what the owner typed and what the table stored."""
    if not card_ids:
        return {}
    found: dict[int, list] = {}
    for row in session.execute(_CORRECTIONS, {"card_ids": card_ids,
                                              "limit": CORRECTIONS_LIMIT}):
        found.setdefault(row.card_id, []).append({
            "field": _display(row.field or ""),
            "old_value": _display(row.old_value) if row.old_value is not None else None,
            "new_value": _display(row.new_value) if row.new_value is not None else None,
            "ts": row.ts.isoformat()})
    return found


def _ledger(session, card_ids) -> dict[int, list]:
    if not card_ids:
        return {}
    found: dict[int, list] = {}
    for row in session.execute(_CARD_LEDGER, {"card_ids": card_ids, "limit": LEDGER_LIMIT}):
        found.setdefault(row.card_id, []).append(row)
    return found


def _render_cards(session: Session, rows: list, now: datetime, week: dict, config,
                  gaps: set) -> list[dict]:
    """One rendering for both states of a slip (design §8.1).

    A draft and a live ticket are the same object -- the same legs, the same selections, the
    same footer -- differing only in what has happened to them, so they are built by one
    function from one row shape. Two renderers would drift the first time a leg gained a field.
    """
    if not rows:
        return []
    card_ids = [row.id for row in rows]
    legs = list(session.execute(_LEGS, {"card_ids": card_ids}))
    game_ids = sorted({leg.game_id for leg in legs if leg.game_id is not None})
    leg_ids = [leg.id for leg in legs]
    scores = {row.game_id: row for row in
              session.execute(_SCORES, {"game_ids": game_ids,
                                        "since": now - SCORES_WINDOW})} if game_ids else {}
    probs = {row.leg_id: row for row in
             session.execute(_LEG_PROBS, {"leg_ids": leg_ids})} if leg_ids else {}
    prob_history: dict[int, list] = {}
    if leg_ids:
        for row in session.execute(_LEG_PROB_HISTORY,
                                   {"leg_ids": leg_ids, "since": now - PROB_WINDOW}):
            prob_history.setdefault(row.leg_id, []).append(
                [row.ts.isoformat(), _dec(row.sharp_p)])
    stat_rows = _stat_rows(session, legs, now)
    corrections = _corrections(session, card_ids)
    ledger = _ledger(session, card_ids)
    by_id = {row.id: row for row in rows}
    price_ages = {row.id: _price_age_s(row, now) for row in rows}

    by_card: dict[int, list] = {}
    for leg in legs:
        card_row = by_id[leg.card_id]
        score_row = scores.get(leg.game_id)
        # `home_score` and `away_score` are independently nullable (ruling A-I4): `score_state`
        # already checks both, unlike the hand-rolled `ScoreState` this replaced, which raised
        # `TypeError` on a row with a home score and no away score.
        state = score_state(leg, score_row) if score_row is not None else None
        rows_for_leg = stat_rows.get((leg.game_id, leg.player_id, leg.stat), [])
        spec = leg_spec(leg)
        stat = _stat_state(leg, rows_for_leg[0] if rows_for_leg else None)
        needs_phrase = needs(spec, state, stat)
        stat_line, correction_note = _stat_text(leg, rows_for_leg, score_row, needs_phrase, now)
        if leg.market_type == "prop":
            gaps.update(sentences.unknown_stat_keys([leg.stat]))
        prob = probs.get(leg.id)
        price_age = price_ages[leg.card_id]
        by_card.setdefault(leg.card_id, []).append({
            "leg_id": leg.id, "seq": leg.seq, "game_id": leg.game_id,
            "market_type": leg.market_type, "side": leg.side,
            "threshold": _dec(leg.threshold),
            "dk_american": leg.dk_american, "dk_decimal": _dec(leg.dk_decimal),
            # Model- or template-written, and its writer ships in phase 5c: sanitized here,
            # through this page's own display-safe class (review round 1, I4) -- markup out,
            # the bet's `+` and the separator kept.
            "plain_text": _display(leg.plain_text or ""),
            "status": leg.status,
            "needs": needs_phrase,
            "home_score": int(score_row.home_score)
                          if score_row and score_row.home_score is not None else None,
            "away_score": int(score_row.away_score)
                          if score_row and score_row.away_score is not None else None,
            "period": score_row.period if score_row else None,
            "clock": _display(score_row.clock or "") if score_row else None,
            "score_age_s": (now - score_row.ts).total_seconds() if score_row else None,
            "sharp_p": _dec(prob.sharp_p) if prob else None,
            "book_p": _dec(prob.book_p) if prob else None,
            "sharp_p_history": prob_history.get(leg.id, []),
            # Phase 4.6 (addendum §1.1, 1.3).
            "selection": _selection(leg, price_age),
            "context_text": _display(leg.context_text) if leg.context_text else None,
            "p_source": leg.p_source,
            # Validated at normalize time (A-C2) and deliberately not re-sanitized: the deep
            # link's query string is what makes it open the right selection.
            "dk_link": leg.dk_link,
            "offered": bool(leg.offered),
            "price_age_s": price_age,
            "price_note": _price_note(leg, card_row, price_age, config),
            "stat_line": stat_line,
            "correction_note": correction_note,
        })

    cards = []
    for row in rows:
        card_legs = by_card.get(row.id, [])
        remaining = [leg for leg in card_legs if leg["status"] in ("pending", "alive")]
        card_corrections = corrections.get(row.id, [])
        settlement, stamp = _settlement(row, card_legs, ledger.get(row.id, []))
        # The deep link is only as good as its weakest leg, and a leg DraftKings no longer
        # offers has no link at all: the button drops to `Copy selections` (addendum §1.5).
        capability = row.link_capability
        if any(not leg["offered"] for leg in card_legs):
            capability = "none"
        card = {
            "card_id": row.id, "year": row.year, "week": row.week, "sport": row.sport,
            "kind": row.kind, "status": row.status, "correlated": bool(row.correlated),
            "built_at": row.built_at.isoformat(),
            "stake": _dec(row.stake_actual if row.stake_actual is not None else row.stake),
            "payout": _dec(row.dk_payout_actual if row.dk_payout_actual is not None
                           else row.dk_payout_est),
            "true_prob_est": _dec(row.true_prob_est), "hold_est": _dec(row.hold_est),
            "dk_odds_actual": row.dk_odds_actual,
            "rationale": _display(row.rationale or ""),
            "placed": row.placed_at is not None,
            "placed_at": row.placed_at.isoformat() if row.placed_at else None,
            "legs": card_legs, "legs_remaining": len(remaining),
            # Phase 4.6 (addendum §1.1, 1.3, 5.2).
            "policy_version": row.policy_version,
            "combined_kind": row.combined_kind,
            "link_capability": capability,
            "p_source_min": row.p_source_min,
            "stake_text": _money(row.stake_actual if row.stake_actual is not None
                                 else row.stake),
            "payout_text": _money(row.dk_payout_actual if row.dk_payout_actual is not None
                                  else row.dk_payout_est),
            "price_age_s": price_ages[row.id],
            "footer": _footer(row, card_legs, price_ages[row.id], week, config),
            # Three buttons, and only on a draft: a placed card's money has already moved, and
            # the correction sheet is a different surface (addendum §5.2).
            "actions": ["open", "placed", "decline"] if row.status == "proposed" else [],
            "settlement": settlement,
            "stamp": stamp,
            "corrected": bool(card_corrections),
            "corrections": card_corrections,
        }
        card["sentences"] = sentences.ticket_card(card)
        cards.append(card)
    return cards


def _cards(session: Session, now: datetime, week: dict, config, gaps: set) -> list[dict]:
    """The live and settled tickets. A `proposed` card is never here (B-I17)."""
    return _render_cards(session, list(session.execute(_LIVE_CARDS)), now, week, config, gaps)


def _build_time(now: datetime, sport: str) -> datetime:
    """When this sport's cards are built in the America/Chicago week `now` falls in.

    The weekday and hour are `harness/settlement/parlay_build.py`'s own `BUILD_TIMES`, imported
    rather than restated: the surface must never tell the owner a build time the stage does not
    keep.
    """
    zone = ZoneInfo(CHICAGO)
    local = now.astimezone(zone)
    weekday, hour = BUILD_TIMES[sport]
    monday = local.date() - timedelta(days=local.weekday())
    return datetime.combine(monday + timedelta(days=weekday), time(hour=hour), tzinfo=zone)


def _next_build_at(now: datetime, sport: str, state: dict | None) -> str:
    """When this slot is next tried, from the stage's cadence and the slot's own `updated_at`.

    Three cases, and each one is a fact rather than a guess: before the build time, it is that
    time; after it, an empty slot is retried on the settle job's next hourly run (the stage runs
    as one of its stages), measured from the moment the slot recorded its reason; and a slot
    that already carries a card is done for the week, so the next build is next week's.
    """
    due = _build_time(now, sport)
    if now < due:
        return due.isoformat()
    if state and "at" in state:
        recorded = datetime.fromisoformat(state["at"])
        return max(now, recorded + BUILD_RETRY).isoformat()
    if state and "built" in state:
        return _build_time(now + timedelta(days=7), sport).isoformat()
    return now.isoformat()


def _shape_of(card: dict) -> str:
    """The shape a rendered card was actually built as, by `parlay_build._card_shape`'s rule: a
    `smart` card is cross-game by construction, and a `lottery` card is the same-game shape
    exactly when every leg shares one game. Only used for a card no slot pointer claims -- one
    built by hand through the CLI before the stage ever saw the slot."""
    if card["kind"] != "lottery":
        return "smart"
    game_ids = {leg["game_id"] for leg in card["legs"]}
    return "lottery_same_game" if len(game_ids) == 1 else "lottery"


def _ideas(session: Session, now: datetime, week: dict, config, gaps: set) -> dict:
    """This week's ideas: one entry per `(sport, shape)` slot that carries a draft or a written
    reason, and the week's money line (addendum §1.1, 1.2).

    The slot's own recorded state is authoritative and is read only through
    `parlay_build.read_slot_state` -- the encoding of `job_state.value` is that module's, and
    this surface never decodes an integer itself. A slot with neither a card nor a reason is not
    shown at all: an empty row with no sentence would say less than nothing.
    """
    year, week_no = week["year"], week["week"]
    rows = list(session.execute(_IDEAS, {"year": year, "week": week_no, "limit": IDEAS_LIMIT}))
    cards = _render_cards(session, rows, now, week, config, gaps)
    by_id = {card["card_id"]: card for card in cards}
    claimed: set[int] = set()
    states = {}
    for sport, shape in SLOTS:
        state = read_slot_state(session, slot_key(year, week_no, sport, shape))
        states[(sport, shape)] = state
        if state and "built" in state and state["built"] in by_id:
            claimed.add(state["built"])
    gaps.update(sentences.unknown_idea_codes(
        state["reason"] for state in states.values() if state and "reason" in state))

    slots = []
    for sport, shape in SLOTS:
        state = states[(sport, shape)]
        card = None
        if state and "built" in state:
            card = by_id.get(state["built"])
        if card is None:
            # A card the stage never recorded -- built by hand through `harness/cli.py` before
            # this slot had a `job_state` row. Resolved by its own legs, never by `correlated`
            # (parlay_build review round 1, Critical 1).
            for candidate in cards:
                if (candidate["card_id"] not in claimed and candidate["sport"] == sport
                        and _shape_of(candidate) == shape):
                    card = candidate
                    claimed.add(candidate["card_id"])
                    break
        reason = state["reason"] if (card is None and state and "reason" in state) else None
        if card is None and reason is None:
            continue
        slots.append({"sport": sport, "shape": shape, "card": card,
                      "reason_code": reason,
                      # The sentence travels with the code: the front end renders it verbatim
                      # and never composes prose of its own (spec §1.2).
                      "reason_text": sentences.idea_reason_phrase(reason, sport)
                                     if reason else None,
                      "next_build_at": _next_build_at(now, sport, state)})
    return {"slots": slots, "week_recorded": _money(week["recorded"]),
            "week_left": _money(week["left"])}


def _best_hit(session: Session) -> dict | None:
    row = session.execute(_BEST_HIT).first()
    if row is None:
        return None
    return {"card_id": row.card_id, "week": row.week, "amount": _dec(row.amount)}


def _streak(session: Session) -> int:
    """The current run of consecutive verdicts, most recent card first: positive for a run of
    cashes, negative for a run of busts, 0 when there is no verdict yet."""
    streak = 0
    for row in session.execute(_STREAK_CARDS, {"limit": STREAK_LIMIT}):
        delta = 1 if row.status == "cashed" else -1
        if streak != 0 and (streak > 0) != (delta > 0):
            break
        streak += delta
    return streak


def _season(session: Session) -> dict:
    # Decimals all the way through the arithmetic, floats only where the shipped payload keys
    # already are (review round 1, M3): `_SEASON` now groups by `source` as well as by kind, so
    # the per-kind total is summed here instead of in SQL, and summing the owner's money in
    # binary floats to do it would be a step backwards from the statement it replaced.
    by_source = {(row.kind, row.source): Decimal(str(row.total))
                 for row in session.execute(_SEASON)}
    totals: dict[str, Decimal] = {}
    for (kind, _source), total in by_source.items():
        totals[kind] = totals.get(kind, Decimal("0")) + total
    zero = Decimal("0")
    staked = totals.get("stake", zero)
    returned = totals.get("return", zero)
    # A refund is not a loss (fix round 1, ruling I3): a `void` ledger row hands the stake back,
    # so it adds into `net` rather than sitting uncounted while `staked` still carries it.
    voided = totals.get("void", zero)
    # Addendum §1.4: the part of `returned` that is still the harness's own arithmetic. Shown
    # *beside* the tile, never folded into it silently: a figure the owner has not confirmed is
    # a different kind of fact from one they typed off their slip (D18). A decimal string like
    # every other money figure this phase adds (review round 1, I2): nothing ships reading it,
    # so it starts out in the convention rather than needing a second key later.
    expected = by_source.get(("return", "computed"), zero)
    strip = [{"card_id": row.id, "year": row.year, "week": row.week, "kind": row.kind,
              "status": row.status, "stake": _dec(row.stake),
              "payout": _dec(row.dk_payout_est),
              # A declined or expired card is a `void` with a reason and no ledger row at all:
              # it moves no figure, and the strip shows it as a grey chip so the offered history
              # stays visible (addendum §1.4).
              "declined_reason": _display(row.declined_reason)
                                 if row.declined_reason else None}
             for row in session.execute(_STRIP)]
    return {"staked": float(staked), "returned": float(returned),
            "net": float(returned + voided - staked), "expected": _money(expected),
            "strip": strip, "best_hit": _best_hit(session), "streak": _streak(session)}


def _week_money(session: Session, now: datetime, config) -> dict:
    """This week's recorded stake and what is left of the week's budget, as Decimals.

    The budget is `parlay.yaml`'s `weekly_budget` and nothing else (review round 1, M7). It is
    the number `harness/parlay/placement.py` refuses a placement against and the one a config
    commit changes; a module constant beside it was a second source for the same $50 that
    happened to agree.

    Addendum §0.1: the $50 is a weekly budget on the owner's calendar, so the ledger sum and the
    week the surface prints are both the America/Chicago ISO week.
    """
    year, week = chicago_iso_week(now)
    recorded = Decimal(str(session.execute(
        _WEEK_STAKED, {"year": year, "week": week}).scalar() or 0))
    return {"year": year, "week": week, "recorded": recorded, "budget": config.weekly_budget,
            "left": config.weekly_budget - recorded}


def _between(session: Session, now: datetime, week: dict) -> dict:
    """The state the surface is in for most of the week (spec §2.5 item 3): when the next card
    is built, the anchor rule, and what is left of this week's $50."""
    # College cards are built on Friday, NFL cards on Saturday evening.
    next_day = "Friday" if now.weekday() < 4 else "Saturday evening"
    return {"next_build_day": next_day, "anchor_rule": ANCHOR_RULE,
            "budget_left": float(week["left"]),
            "weekly_budget": float(week["budget"]),
            "year": week["year"], "week": week["week"]}


def build_ticket(session: Session, now: datetime, settings: Settings) -> dict:
    payload = base_payload("ticket", now, settings, CADENCE_OUT_S)
    payload["badge"] = BADGE
    config = load_config()
    # Read once for the whole payload: the ideas line, every card's footer and the between-cards
    # sentence are all statements about the same $50, and reading it three times could print
    # three different numbers on one page.
    week = _week_money(session, now, config)
    # Every reason code and stat key this build could not put a sentence to, collected as the
    # sections render and written out below (spec §1.2, ruling A-I2): a vocabulary's blind spot
    # is reported, never blanked.
    gaps: set[str] = set()
    section(session, payload, "cards", lambda: _cards(session, now, week, config, gaps))
    section(session, payload, "ideas", lambda: _ideas(session, now, week, config, gaps))
    section(session, payload, "season", lambda: _season(session))
    section(session, payload, "between", lambda: _between(session, now, week))
    between = payload["between"] if isinstance(payload["between"], dict) else {}
    payload["sentences"] = {"between": sentences.ticket_between(between)}
    payload["readings"] = {}
    payload["sentences_gaps"] = sorted(gaps)
    return payload


register_builder("ticket", build_ticket)
