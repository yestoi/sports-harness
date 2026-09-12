# The sports harness, explained for a bettor

This is a home-built system that studies sports betting prices and places **pretend bets** to find out
whether a particular strategy has a real edge. It runs on a NAS in a closet, records everything it sees,
and grades itself every Monday. No money is at risk. If, after a few weeks, the numbers pass a set of
tests that were written down before any data existed, the question of betting for real gets asked
separately, as a legal decision by the owner, not by the software.

If you bet casually, this page is for you. It explains what the system bets on, how it decides, and how
it knows whether it is any good. The technical documents are linked at the end.

## 1. The idea in one paragraph

A few sportsbooks are known for setting the truest prices in the world: Pinnacle above all, with
BetOnline and LowVig close behind. They take huge action from professionals and move their lines fast.
Kalshi is different: it is a marketplace where ordinary people trade yes/no contracts on games, and its
prices can lag the sharp books by minutes. The strategy is simple to say: **watch the sharp books, work
out what a game is really worth, and when Kalshi's price is far enough off that number to cover the fees
and leave a cushion, post an order at our price and wait for someone to take it.** That is it. Everything
else in this repository is about doing that carefully and then being honest about whether it worked.

## 2. What a "bet" is here

Kalshi sells contracts on NFL and college football games. Each contract asks a yes/no question and pays
**$1** if the answer is yes and **$0** if it is no:

- **Game winner** (a moneyline): "Will LSU beat Florida?"
- **Spread**: "Will Alabama win by more than 7?"
- **Total**: "Will the two teams score more than 55 points combined?"

Contracts trade between 1 and 99 cents. The price is the market's probability. If "LSU wins" trades at
63 cents, the market thinks LSU has about a 63 % chance. If you buy at 63 and LSU wins, you get $1 back:
37 cents of profit on 63 cents risked. In sportsbook terms that is roughly a −170 favourite.

Because every contract has a yes and a no side, betting "no" on "Alabama wins by more than 7" is the same
as taking Wisconsin +7. The system can do either.

Kalshi charges a small fee on each trade, larger when the price is near 50 cents. The system pays the
lower "maker" rate because it posts orders and waits rather than hitting whatever is available. That fee
is deducted from every edge calculation before a bet is considered.

## 3. How it decides to bet

Every two minutes or so the system pulls the current lines from the sharp books and every price on
Kalshi. For each Kalshi contract it computes a **fair price**: the sharp books' odds with the bookmaker's
margin stripped out, weighted 65 % Pinnacle and 35 % BetOnline/LowVig. Then it compares.

A bet is considered only when all of the following hold. These are the rules of the main strategy,
called `sharp_direct`, and they were fixed in writing before the first bet:

| Rule | In plain words |
|---|---|
| Edge of at least 2 points | Our fair price must beat Kalshi's price by 2 cents or more, after fees and after a 1-cent cushion for the fact that the people who take our order tend to know something. |
| Edge of at most 6 points | A gap bigger than 6 cents is more likely a mistake in our data than free money, so it is skipped. |
| Price between 20 and 80 cents | Heavy favourites and long shots are avoided; fees and thin markets eat the edge there. |
| Kickoff more than 20 minutes away | The last minutes before a game are chaos. Nothing is placed after that, and every open order is cancelled 10 minutes before kickoff. |
| The market is real | At least 50 contracts traded in the last 24 hours, and the gap between the best buyer and seller is no wider than 8 cents in the NFL or 20 in college. |
| The sharp line is not sprinting | If the sharp books' price moved 2 or more cents in the last 5 minutes, wait. Betting into a moving line is how you get run over. |
| Our fair price is fresh | Sharp odds older than 3 minutes are not trusted. |
| We know which game it is | The contract must be matched to a game with full confidence. Kalshi's names and ESPN's names do not always agree. |

When a contract passes, the system does not take the price on offer. It posts its own order a little
below fair value and waits, the way a limit order works in a stock account. The target price is fair
value minus the edge cushion minus the fee. If nobody comes to that price, there is no bet.

### How much it bets

It uses a fraction of the Kelly criterion, which sizes a bet in proportion to the edge: a quarter of the
Kelly stake, on a pretend bankroll of **$3,000** per strategy. In practice bets are small. There are caps:
no more than 3 % of bankroll on one bet ($90), 5 % on one game, 15 % open at once on any day, at most 25
open orders, and never less than $10. Most strategy variants only note when a cap would have applied; one
variant, `constrained`, actually enforces them, so we can see what the caps cost.

## 4. The seven strategies

Rather than trust one set of rules, the system runs seven versions side by side on the same games. They
are variations on the main strategy, each changing one thing, so that in a few weeks we can see which
change mattered. All seven were registered, with their exact settings, before any of them bet.

| Variant | What it changes | Question it answers |
|---|---|---|
| `sharp_direct` | Nothing. This is the original. | Does the basic idea work? |
| `sharp_two_sided` | Also bets the "no" side of contracts. | Is there edge on both sides, or only on favourites? **This is the one the go-live tests are judged on.** |
| `constrained` | Enforces the bankroll caps instead of only noting them. | How much do the safety caps cost in missed bets? |
| `nfl_only` | Skips college football. | Is the edge really in the NFL, where the sharp books are deepest? |
| `no_velocity` | Ignores the "sharp line is moving" rule. | Does waiting for a moving line to settle actually help? |
| `sharp_plus_derived` | Also prices contracts the sharp books do not quote directly, using a model of how game margins are distributed. | Can we bet alternate spreads and totals the sharps do not post? |
| `wide_band` | Bets from 15 to 85 cents instead of 20 to 80. | Is there edge out in the tails? |

Only the first three actually place pretend orders. The other four are scored on paper from the same
prices, so we can measure them without pretending the market had room for seven of us at once.

## 5. What happens after an order is placed

The system does not assume its order filled. It records the whole Kalshi order book, every change, all
day, and replays that recording against its own order:

1. The order sits in line behind everyone who was already at that price.
2. It fills only when a **real trade printed** at our price after the people ahead of us were served.
3. Meanwhile the order can be pulled: if the sharp price moves against us by 2 cents, if the edge shrinks
   below half of what it was, if the fair price goes stale, or if the game is 10 minutes from kickoff.
4. Games settle from ESPN's final scores, cross-checked against Kalshi's own results. Ties on a
   moneyline pay half, as they do on Kalshi.

This is stricter than most backtests. It is easy to build a system that "would have won" if it had been
allowed to buy at every good price it ever saw. Here, a pretend bet counts only if the market actually
came to us.

## 6. How it knows if it is any good

Winning money over three weeks proves nothing; a coin can do that. The system uses the measure that
professionals use to judge themselves long before results settle: **closing line value**, or CLV.

The closing line is the market's price right before kickoff. It is the most informed price of the week,
because every sharp bettor has had their say. If you consistently buy at 60 cents things that close at
63, you are beating the market, and over hundreds of bets that turns into profit whether or not any one
bet wins. If you buy at 60 things that close at 58, you are the mark, however this week's results look.

Every pretend bet is scored against seven closing prices: Pinnacle's price 5 minutes before kickoff (the
main one), the sharp consensus at close, Novig's close, Kalshi's own midpoint and last trade before
kickoff, the first price ever seen, and the actual result. Every bet is also scored on **markout**: where
the fair price and the Kalshi price went 1, 5 and 30 minutes after we filled. If the price keeps moving
against us right after we buy, the people taking our orders know more than we do, and the system records
that too.

Two rules keep the scoring honest:

- **Count games, not bets.** Four bets on one game rise and fall together. Every number is reported with
  how many games it rests on. Under 10 games it is greyed out; under 30 it is flagged.
- **Every number has an honest range.** A mean CLV of +2 points sounds great until you learn the plausible
  range is −1 to +5. The range is always shown, and a claim is only made when the low end clears zero.

## 7. The tests for going live

Before any data existed, the owner wrote down the conditions the system must meet before the question of
real money is even raised. The software cannot change them. The weekly gate report stores the exact
definitions with a fingerprint, and the dashboard shows them. One plain-words line per coded test, each
naming the test the code runs:

- `fill_events`: at least **150 pretend fills** confirmed by real trades, across at least **40 games and
  both sports**, at least **80 %** of them from a live order-book feed rather than periodic snapshots
  **and with a clean book for the whole time the order rested**.
- `marquee_share`: at least **30 %** of those fills are on a tight market (a 4-cent spread or less) in the
  NFL or in college football.
- `clv_pinnacle_lb`: the **low end of the honest range** on closing line value against Pinnacle is **above
  zero**.
- `markout_30m`: the **30-minute markout is positive** after fees: the price does not run away from us
  after we buy.
- `adverse_drift`: the fair price does not drift against us between the bet and the fill by more than
  **1 cent**.
- `filled_vs_unfilled`: the bets that filled are **not meaningfully worse** than the ones that did not. If
  only the bad bets get taken, the edge is an illusion.
- `clv_every_benchmark`: mean CLV is **not negative under any** of the closing prices that count.
- `staleness_median`: the fair price we act on is **fresh**: median age under **90 seconds**.
- `settlement`: **zero** settlement disagreements with Kalshi, and a venue settlement row for at least
  **90 %** of the settled markets we had fills in.
- `mismatched_markets`: **zero** bets on the wrong game.
- `legal_decision`: the owner's separate, documented legal decision. False by construction while this is
  paper.
- `live_trading_env`: live trading switched on in the container and in the config. False by construction
  while this is paper.

Duties around the gate, not gate tests. These two are on the calendar and in the runbooks. No code checks
them, and neither is part of the stored fingerprint:

- A replay of the recorded week reproduces the live results within 2 %.
- Three weeks in a row.

Passing all of that produces a stored, passing gate report. That report plus the owner's separate legal
decision, which lives outside this system, would be required before a single real dollar moved, and even
then the first two weeks would be a canary: $25 a bet, $200 open at once. As of this writing none of
that has happened and nothing here can send an order to Kalshi.

## 8. The calendar

- **Week 1** starts with the first pretend orders in the week of September 7, 2026 and runs three weeks.
- **Every Monday morning** the system writes its weekly report and runs the gate tests. The owner reads
  it, checks that the recording reproduces the live results, and fixes team-name mismatches.
- **Game days** (Thursday to Sunday) the system is watched, not touched.
- **Mid-October** the numbers are read once as evidence, alongside the legal question.

## 9. Where to look

The dashboard has four pages, each answering one question, plus a page that explains the whole pipeline:

| Page | Question |
|---|---|
| Pulse | Is it alive and honest? One word, and the reason if not. |
| Floor | What is it doing right now? Games, orders waiting in line, fills, what it passed on. |
| Study | Is the strategy any good? Every claim with its game count and honest range. |
| Gate | Should this ever trade for real? The tests, their definitions, and their history. |

Everything on every page is pretend money and says so. Nothing on any page can place, cancel or change a
bet.

## 10. What this is not

It is not a tip service, and nothing here predicts who will win a game. It bets on prices, not teams.
Most edges in sports betting are small, short-lived, and gone the moment enough people find them; the
most likely outcome of this experiment is a clear, well-documented "no". That would still be a success:
a season of recorded prices and an honest answer.

Separately from all of this, there is a planned weekly **$50 fun budget** for parlays picked at
DraftKings prices with an LSU or Saints leg, placed by hand by the owner. That is entertainment, it is
kept apart from the research, and it is scored only for fun.

## 11. Technical documents

| What | Where |
|---|---|
| System design | `docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md` |
| Paper execution, scoring and the gate | `docs/superpowers/specs/2026-09-07-phase3-paper-execution-design.md` |
| Dashboard design | `docs/superpowers/specs/2026-09-07-dashboard-surfaces-design.md` and the mockups under `docs/superpowers/design/dashboard/` |
| The strategy variants, exactly as registered | `harness/variants/*.yaml` |
| Weekly reports | `docs/reports/` |
| Roadmap and the autopilot that builds and operates the system | `docs/superpowers/autopilot/roadmap.md` |
