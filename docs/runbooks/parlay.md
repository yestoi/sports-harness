# Parlay runbook

This is real money, and it is fun money. The harness records; it never places. The Ticket
surface is the only one in the dashboard that shows this figure, and it says so in its badge
(`FUN MONEY · $50/WEEK · PLACED BY HAND`).

## The weekly rhythm

Friday for CFB, Saturday evening for NFL:

```
harness parlay build --sport cfb --kind smart
```

proposes a card. Read it, type the slip into DraftKings by hand, then:

```
harness parlay placed <id> --payout <the odds you got> --stake <what you staked>
```

## The two refusals, and what to do about each

`no anchor priced` (exit 2, `harness/parlay/build.py`) means no LSU or Saints outcome
(`parlay.yaml`'s `anchors: [LSU, "NO"]`) has a DraftKings price under 30 minutes old
(`leg_max_age_minutes`): wait for a tick and build again.

`the line moved and was not confirmed` (`harness/parlay/placement.py`) means a leg's DraftKings
line at `placed` time differs from the card's line at build time. If you took the new line,
re-run with `--leg-line <seq>=<point>` for each leg it named; if you did not, the card is stale
and you should build a new one instead.

## The budget

$50 an ISO week, hard, checked against `parlay_ledger` and keyed to the America/Chicago day
(`harness.research.spend.chicago_day`) the same way the U4 research caps are — never to a UTC
week. `parlay.yaml`: $25 on the smart card (`smart_stake`) and $5 on each of up to three lottery
cards (`lottery_stake`, `lottery_cards_max`) leaves $10 unallocated by design (D15) — the budget
is a ceiling `placed` enforces, not an allocation it has to spend.

```
harness parlay show
```

prints what is left.

## Cards nobody places

`harness parlay show` voids any proposed card older than seven days (`expiry_days` in
`parlay.yaml`) as it runs, writing `declined_reason = expired`. A voided card moves no money and
writes no ledger row.

## Grading

`parlay_grade` (`harness/settlement/parlay_grade.py`) runs hourly with the settlement chain,
immediately after `settle`. It is idempotent and resumes: a leg already graded is skipped. A
raise from one card is caught, counted in `counts["errors"]`, and every other card in the pass
still grades — one bad card never blocks the rest.

A leg grades once its game is `final` or `final_ot` and has a recorded score, through the same
push rules the paper book uses (`harness.parlay.needs.leg_outcome`, DraftKings' own handicap
convention): a tied moneyline, a spread that lands exactly on the whole-number line, or a total
that lands exactly on the line is a **push** and grades `void`, not a hit; an under leg's side is
explicit, never inferred. A `postponed` or `canceled` game voids its leg on that status alone,
with no score needed. A card is `cashed` when at least one leg hit and none missed, `busted` when
one leg missed, and `void` when no leg hit (every leg pushed or voided) — the stake comes back on
a `void`. Those three words are `parlay_cards.status`'s own vocabulary and the Ticket surface's;
nothing here ever writes `won` or `lost`.

DraftKings drops a pushed leg and re-prices the slip off the surviving legs rather than paying
the card at its original quoted odds: a `cashed` card with one or more pushed legs is re-priced
as the stake times the product of the hit legs' own decimal odds, not the card's quoted payout. A
card with no recorded payout still cashed; the ledger records the stake back rather than
inventing a number.

## This is real money

The harness records; it never places. No code path in this repository submits a DraftKings slip
or a Kalshi order from a parlay card. `harness parlay placed` writes only the harness's own
tables — `parlay_cards`, `parlay_legs`, `parlay_ledger` — after the operator has already placed
the slip by hand.
