# Fill starvation packet: why the paper book opens almost no positions, 2026-09-18 12:21 CT

Prepared at the user's request ("I am concerned we're not opening positions as much as I'd like. What is causing that and are we on the right track to fix that or improve it?", then "Lets put this in a packet for me to review with a another model."). Written by an independent review session on Omarchy, not the autopilot loop; every number comes from read-only queries against the production database, recorded verbatim with the SQL in `evidence/2026-09-18-fill-starvation-queries.txt`. Nothing was changed, dispatched or committed; the loop's session was mid-Task 11 at the time.

Sources: `docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md` (v2 charter), `2026-09-07-phase3-paper-execution-design.md`, `2026-09-13-phase6d-sustained-evaluation-design.md`, `docs/superpowers/reviews/2026-09-07-autopilot-adversarial-review.md`, `roadmap.md`, `state.md` (journal 282), `fixes.md`, `reports/2026-09-15-open-decisions-packet.md`, `reports/2026-09-18-veto-pacing-brief.md`, `.superpowers/sdd/2026-09-13-phase6d-sustained-evaluation/progress.md`, production tables `orders`, `fills`, `order_events`, `signals`, `intents`, `fair_values`, `odds_snapshots`, `gate_reports`, `report_cells`. Main at 072dde1; runtime 4066197.

The user answers by item letter in chat (§7); the loop journals each answer verbatim as a `decision` entry. The reviewing model is asked to attack §3 and §5 first.

## 0. For the reviewing model

This packet claims one root cause and one sanctioned remedy. Try to break it:

1. **The rule.** Does `now − fair_values.created_at > max(180 s, 220 s)` really fire on a fixed clock off-window? Check §3.1-3.3 against evidence sections D, F and K.
2. **The regime.** Is it true that fills only occur in the regime the rule kills? Check §3.6 against evidence F and G.
3. **The counterfactual.** Are the 770 "would have filled" orders informative, or are they fills at a stale price after the fair moved through them? See §5.1.
4. **The remedy.** Is running the existing `policy-compare` on live tape the right next step, or is a seventh alternative (allowance tied to the cadence in force) needed first? See §6 and item D.
5. **The priority call.** Is holding the executor hotfix queue (rows 79, 86, 87) until the comparison is read a reasonable trade, given §4? See item C.

## 1. The system in one paragraph

A paper-only maker strategy on Kalshi NFL and NCAAF game contracts. Fair value is a devigged, log-odds-weighted consensus of Pinnacle (0.65) and the BetOnline family (0.35) from The Odds API; the edge floor is 2 points; maker fee 0.0175·p·(1−p). A recorder tick fetches odds and Kalshi books on a cadence that depends on the regime (§3.2). The strategy runs after each tick, labels every matched contract with every filter, and emits candidate signals, which become intents. An executor loop every 15 s places paper orders once with `expiry = kickoff − 10 min` (ruling R8, no renewal), reprices on a fair move of 1 point or more, and cancels on an ordered rule list. Fills are simulated from the recorded tape: the `queue_model` track is the position of record (join behind resting size at our price, fill on prints on the opposite taker side at or through our price). Each order also carries a `no_watcher` counterfactual that asks what would have happened had the order been left alone until its natural expiry, and a `snapshot_cross` marker that is a worst case for adverse selection, never a position. The go-live gate (charter §9.5 as coded in `gate.py`) needs at least 150 orders with a `queue_model` fill across at least 40 games in both sports, 80 % of them on a clean WebSocket book, plus positive CLV and markout with cluster-robust confidence, plus the user's separate legal decision.

## 2. The finding

The executor cancels almost every paper order about four minutes after placing it, with reason `fair_stale`. Fills, when the counterfactual is allowed to run, arrive a day or more after placement. The regime the rule kills is the only regime that fills.

| Quantity (2026-09-08 to 2026-09-18, non-replay) | Value |
|---|---|
| Paper orders placed | 39,208 |
| Cancelled with reason `fair_stale` | 38,239 (97.5 %) |
| Median order lifetime, weekday, more than 3 h before kickoff | 225 s |
| Orders with a real `queue_model` fill | 3 (6 fills, 124 contracts) |
| Orders the `no_watcher` counterfactual would have filled | 770 (3,325 fills, 69,866 contracts) |
| Of those, filled after the order had already been cancelled | 767 |
| Distinct positions among the 770 (variant, market, side) | 92, on 58 market-sides, 36 games |
| Median placement-to-counterfactual-fill | 32.6 h (p25 23.7 h, p75 50.7 h) |
| Go-live gate requirement | 150 filled orders, 40 games, both sports |
| Weekly report t1, `sharp_direct` NFL, week 38 | 3,542 orders, 2 fills, fill rate 0.0003 |

## 3. Mechanism, step by step

**3.1 The rule.** `harness/execution/plan.py:455` `_fair_stale` cancels a resting order, and skips a placement, when `now − fair_ts > max(variant.stale_s, stale_allowance_s)`. `fair_ts` is `fair_values.created_at`, the instant the recorder priced the fair value, "never the snapshot's" (`store.py:401, 416`). `stale_s` is 180 in every registered variant. `stale_allowance_s` is 220 s on every one of the 39,208 orders, and every one came from the featured feed. The 220 comes from the phase 3 addendum §0.1: the feed's fetch interval plus the tick budget, "featured: 120 + 100 = 220 s".

**3.2 The cadence.** `harness/recorder/cadence.py:20-37` fetches featured lines every 120 s only from three hours before a sport's first kickoff of the day through its last kickoff, or while a game is in progress, with a 20 s burst 60 to 100 minutes before an NFL kickoff. Otherwise it fetches every 300 s on Saturday and Sunday and every 900 s on weekdays, and not at all 01:00 to 08:00 CT. So on a weekday, outside a game window, a fair value row is created every 900 s and is tradeable for the first 220 s of that interval.

**3.3 The timing, observed.** Over the last three days the median `fair_stale` cancel fell 233 s after the fair row's `created_at` (p90 403 s, n 25,868). Median order lifetime by regime: 225 s on weekdays more than 3 h before kickoff (n 37,892), 600 s at weekends more than 3 h out (n 323), 420 s inside 3 h (n 44). The rule fires on a clock, not on information: a fresher fair row would have reset the age, so by construction the fair had not been re-priced when the cancel fired.

**3.4 The rest of the cycle is dead time.** Once a fair row is older than 220 s, no new placement is allowed either: the executor wrote 106,398 `skipped fair_stale` events all time and 56,853 in the last three days. Upstream, the strategy's own `not_stale` label rejected 13,924 gate-variant signals in the last 24 h. Candidates are not the constraint: the gate variant produced 46,854 candidate signals in the same 24 h.

**3.5 Churn.** The same position is re-placed about 25 times a day: 9,929 orders on 395 distinct (variant, market, side) keys on 2026-09-16; 9,765 on 429 the day before. Each placement carries a new intent, a new counterfactual track and, when the veto is awake, a veto call.

**3.6 Where the fills are.** All 770 counterfactual fills came from orders placed more than three hours before kickoff: 742 on weekday placements, 28 on weekend placements. Of 537 orders placed inside three hours of kickoff, none would have filled. Median time to the counterfactual fill is 32.6 hours. The book that fills a maker order at a two-point edge is the quiet pre-game book, and the quiet pre-game book is exactly where the recorder polls every 15 minutes and the executor cancels after four.

**3.7 Is Pinnacle actually quiet?** For Thursday night's NFL game (game 115) between 2026-09-15 13:00Z and 2026-09-18 00:00Z, the recorded Pinnacle moneyline shows 263 distinct `last_update` values with a median gap of 15.0 minutes, which is the weekday fetch cadence itself: the line changes at least as often as it is fetched. One game only, and the executor's clock is the fair row's creation regardless, so this bounds the inference rather than carrying it. Staleness at placement (pricing time minus the newest sharp `last_update`) has a median of 75 s.

**3.8 Where the rule came from.** The v2 charter's cancel list (§9.2) is edge below half the floor, kickoff inside 10 minutes, kill switch, and a venue move of 2 points against. The stale cancel was added in phase 3 by adversarial-review finding F36, "the executor never checks fair-value staleness, and a dead WS recorder leaves a frozen book": a guard against a dead feed. The addendum §0.1 separated feed health (`feed_lag_s`) from line age (`staleness_s`) and the executor's test uses row age with an allowance sized to the game-window cadence. The 6D addendum §1.6 states the baseline with the 300 s and 900 s cadences in the same paragraph as the 220 s allowance, and lists a 600 to 900 s stale allowance as the first alternative to compare; the authors saw the mismatch and, correctly under the invariants, left the change to the user.

## 4. Second-order effects

- **The executor load saga is downstream of the churn.** 27,038 cancelled orders still carry an unfinished counterfactual track (`nw_done = false`); 19 open orders do. That pending population is what the tape rescan, the simulation walk, the expiry cohorts and the placement waves iterate over, which is the subject of fix 78 (three parts), 79, 82, 85, 86 and 87 and of every packet item since item 16. The `orders` table holds 1.29 GB for 39,659 rows. Under any policy that rests an order instead of re-placing it 25 times a day, that population shrinks by roughly the same factor.
- **The veto judges churn.** The veto brief (`reports/2026-09-18-veto-pacing-brief.md`): 3,149 decided of 178,618 intents in seven days, the $25 daily cap spent before 09:00 CT, none inside 5.7 h of kickoff. 178,618 intents are mostly re-placements of a few hundred keys.
- **Capacity already binds.** `exec_max_open_orders` is 150 shared across three executed variants; 51,234 `skipped exec_capacity` events in three days against about 400 distinct keys a day. Under a resting policy the slots fill on day one, so the capacity question (`per_variant_slots`, the spec's own `max_open: 25`) is the other half of any change.
- **A second data gate.** 34,426 `skipped book_dirty` events in three days (a WebSocket book the executor will not price against). 6B's territory; it caps the opportunity set independently of staleness.
- **The decisions packet.** 23 items, most about executor internals; the one that changes the experiment, item 11 (policy adoption), is listed as "not askable yet".

## 5. What could be wrong with this diagnosis

**5.1 The counterfactual may be adversely selected.** The `no_watcher` track never reprices. An order that fills 33 hours later at its original price may fill because the sharp fair moved through it, which is the adverse selection the charter says to measure before trusting any edge. Evidence either way: the 2026-09-15 gate report's 30-minute markout on the counterfactual fill anchor was +1.41 points net of fee (t 3.88, n 141, 10 clusters) for the gate variant and +1.65 (t 3.82, n 117, 7 clusters) for the primary; `adverse_drift` had no observations. Ten clusters is thin. A resting policy inside the executor would still reprice on fresh fair moves and cancel on `edge_decay`, which the counterfactual does not, so it should do no worse than the counterfactual on this axis.

**5.2 Recent days understate.** 2026-09-17 and 09-18 placements show 0 counterfactual fills because the track has not reached its typical fill time yet, not because they will not fill.

**5.3 Capacity becomes the binding constraint.** With 150 slots and orders resting for a day, the honest post-change fill count is bounded by slots times turnover, not by the 770. Only the comparison run quantifies it.

**5.4 The allowance may be a deliberate live-trading posture.** F36's caution is sound for a live book on a dead feed. But the test does not measure feed death (that is `feed_lag_s`), and in paper the posture costs the entire sample; in live it would forgo the same fills.

**5.5 Query caveats.** `fair_p_at_event` is NULL on every `fair_stale` cancel event, so "the fair had not changed" is inferred from the rule's construction, not observed. The Pinnacle cadence check covers one game. The 38,218 versus 38,239 difference is orders in status `cancelled` versus orders carrying a `fair_stale` cancel event.

**5.6 Not checked here.** The week-3 mispricing-map criterion (charter §9.6), the H2 and H3 datasets that accrue from snapshots and prints regardless of fills, and whether `book_dirty` skips fall on the same markets as the counterfactual fills.

## 6. Governance: what may change, and who changes it

- The loop may not edit `harness/variants/` (invariant 1), gate criteria (invariant 2, R1), or `ExecSettings` values, which are frozen into every order's `config_hash`. The holding policy is a measurement-affecting choice and therefore the user's dated decision (6D §0.15a: "Which policy do you adopt for the prospective period, and from what date?").
- The sanctioned instrument exists: `harness policy-compare --from-run --to-run --variant --policies --out -` (`cli.py:836`), stepping a replay at the recorded loop instants once per policy, equal resources, with a table per policy of orders placed, unique opportunities, actual queue-filled orders, clean resting seconds, coverage and exclusion classes. The six alternatives (`policy.py`): `stale_allowance_900`, `rest_to_expiry`, `per_variant_slots` (25), `fillability_admission`, `join_the_bid`, `near_kickoff_only` (180 min). Every output is labelled counterfactual and exploratory (M6).
- Registration of a selected policy is a new `config_history` hash or a new variant id by dated pre-registration amendment, never an edit to a registered id (6D §1.6c). The same clause says anything registered after Mon 2026-09-21 09:00 CT is exploratory and labelled so; U8 has already overridden R7's dates and 6F is to propose revised ones, so that clause is part of item E rather than a wall.
- Status of the instrument: the 6D ledger records that the comparison "is designed on fixtures and never run on the live tape" (progress.md line 10); the plan says "the comparison run waits for 6B and is a separate operate duty" (line 5166). 6B merged 2026-09-14 21:22 CT. The run appears in no state.md order of work, no journal entry and no report.

## 7. Items for decision

Answer by letter. This session's lean is marked; the reviewing model may argue for another.

**A. The diagnosis.** (a) Accepted as the working explanation for the fill count, the executor load and the veto spend. *Lean.* (b) Accepted for the fill count only; treat the load and veto links as hypotheses. (c) Not accepted; state what §3 or §5 misses.

**B. Run the comparison.** (a) The loop runs `policy-compare` as its next operate duty, before rows 79, 86 and 87 and before Task 11's release: slice 2026-09-15 00:00 CT to 2026-09-17 23:59 CT (two weekdays plus Thursday's window), gate variant and primary, all six alternatives, output as a report under `reports/` with the M6 label. *Lean.* (b) Run it after Monday's full release (fix 85) so the executor tree is stable. (c) Run a narrower slice first (one weekday) to size the cost. (d) Other.

**C. The hotfix queue in the meantime.** (a) Hold rows 79 (print cache follow-on), 86 (expiry cohort) and 87 (coverage cells) until the comparison table is read; let fix 85 (an additive index, already merged) ship Monday as ruled. *Lean.* (b) Continue the queue as scheduled in state.md. (c) Other.

**D. A seventh alternative.** The six do not include "allowance = the cadence in force plus the tick budget" (900 + 100 s on a weekday, 300 + 100 at the weekend, 120 + 100 in a window), which is what §0.1's own formula gives when the actual interval is used. (a) Add it to the comparison by a dated design note before the run, so the table compares the rule as written against the rule as intended. *Lean.* (b) Compare the six as designed; `stale_allowance_900` is close enough. (c) Other. Reviewer question: is a cadence-tied allowance better formed than a flat 900 s, given that the fair row's age is what the executor reads?

**E. Bring the gate arithmetic forward.** 6F's sample-accrual forecast is not planned. (a) Ask the loop for a one-page forecast now: fills per week at the current rate and under each compared policy, against 150 fills and 40 games, with the season's remaining weekends. *Lean.* (b) Wait for 6F as sequenced.

**F. Veto pacing (packet item 19).** (a) Defer: if churn falls by an order of magnitude the daily cap covers the intents that exist. *Lean.* (b) Decide pacing now regardless.

## 8. Charter alignment, goal by goal

| Charter goal (§1) | Status |
|---|---|
| Record a replayable dataset | On track. Storage decision due 2026-09-22 (packet item 1). |
| Paper-trade a maker strategy with an honest fill model, judged by CLV and markout | Machinery built and audited (6A, 6B); sample of record is 3 orders. Blocked by §3. |
| Nine pre-registered hypotheses | H2, H3 accrue from snapshots and prints regardless. H1 (maker adverse selection) has no sample. H5 off under RFQ0. H9 never asked inside 6 h of kickoff. |
| Fun LSU/Saints parlay cards | Phase 4.6 in progress; T18b/T19 wait on the first placed card. |
| Claude as shadow veto and annotator, never a pricer | Running; budget spent on churned far-from-kickoff intents (§4). |
| Go-live only after gate, legal decision, canary | Gate unreachable at the current fill rate; legal decision separate and untouched. |

## 9. Evidence

`evidence/2026-09-18-fill-starvation-queries.txt`: sections A to K, every statement and its result, with the code and document line references in K.

## Appendix: queries and results

Verbatim copy of `evidence/2026-09-18-fill-starvation-queries.txt`, so this file is self-contained for an outside reviewer.

```text
Fill starvation review: queries and results, 2026-09-18, review session (not the loop), main at 072dde1
Read-only. Every statement ran as
  cd /srv/sports-harness && docker compose exec -T postgres psql -U harness -d harness -X -A -c "set statement_timeout='<30-90>s'" -c "<sql>"
against the production database during the hour before 12:21 CT; per-statement clock times were not recorded.
Nothing was written. Runtime build 4066197 (app-only 07:23 CT), app-ws on 747791c.

== A. Registered variants ==
select variant_id, config_json->>'name', tier, registered_at::date from strategy_variants order by registered_at;
ff363c8ac08d | constrained        | secondary | 2026-09-07
e549e693e117 | nfl_only           | secondary | 2026-09-07
64ba3ef09642 | no_velocity        | secondary | 2026-09-07
f259ca109084 | sharp_direct       | primary   | 2026-09-07
49af716f8708 | sharp_plus_derived | secondary | 2026-09-07
c2bc45377328 | wide_band          | secondary | 2026-09-07
e82fcd0a1e99 | sharp_two_sided    | replay    | 2026-09-08
5632da729fa7 | sharp_two_sided    | secondary | 2026-09-08   (gate variant, U5)
Settings.exec_variants default: ["sharp_direct", "constrained", "sharp_two_sided"] (harness/config/settings.py:84-85).

== B. Fills by method ==
select fill_method, simulated, has_print, replay, count(*), count(distinct order_id), sum(contracts) from fills group by 1,2,3,4;
no_watcher     | t | t | f | 3325 | 770 | 69865.86
queue_model    | t | t | f |    6 |   3 |   124.46
snapshot_cross | t | f | f |  166 | 166 | 19454.00
snapshot_cross | t | t | f |  160 | 160 | 11890.25

Fills by Chicago day (non-replay): queue_model fills on 2026-09-08 (2 fills, 1 order) and 2026-09-15 (4 fills, 2 orders) only.
no_watcher fills by day: 09-08 15 | 09-09 78 | 09-10 525 | 09-11 371 | 09-12 280 | 09-13 176 | 09-14 195 | 09-15 283 | 09-16 1274 | 09-17 116 | 09-18 12.

== C. Orders by Chicago day of placement (non-replay) ==
select (placed_at at time zone 'America/Chicago')::date, count(*), count(distinct game_id), sum((filled_contracts>0)::int),
       sum((nw_filled_contracts>0)::int), sum((status='open')::int), sum((status='expired')::int), sum((status='cancelled')::int),
       round(avg(contracts)), round(avg(prob),3), round(avg(edge_at_place),4) from orders where not replay group by 1 order by 1;
day        | placed | games | filled | nw_filled | open | expired | cancelled | avg_contracts | avg_prob | avg_edge
2026-09-08 |   1258 |    48 |      1 |       110 |    0 |       0 |      1258 |           107 |    0.450 |   0.0421
2026-09-09 |   2615 |    44 |      0 |       186 |    0 |       4 |      2611 |           113 |    0.452 |   0.0431
2026-09-10 |   3836 |    49 |      0 |       119 |    0 |       0 |      3836 |           112 |    0.451 |   0.0425
2026-09-11 |   1120 |    65 |      0 |        27 |    0 |       3 |      1117 |           106 |    0.452 |   0.0407
2026-09-13 |    871 |    15 |      0 |        28 |    0 |      53 |       818 |            94 |    0.456 |   0.0399
2026-09-14 |   1165 |    50 |      0 |        62 |    0 |       1 |      1164 |           117 |    0.455 |   0.0438
2026-09-15 |   9765 |    55 |      2 |       224 |    0 |       0 |      9765 |           111 |    0.449 |   0.0413
2026-09-16 |   9929 |    53 |      0 |        14 |    0 |       0 |      9929 |           110 |    0.426 |   0.0409
2026-09-17 |   6194 |    53 |      0 |         0 |    0 |       0 |      6194 |           108 |    0.447 |   0.0413
2026-09-18 |   2455 |    57 |      0 |         0 |   56 |       0 |      2399 |           110 |    0.440 |   0.0423
(No orders on 2026-09-12: the host outage.) nw_filled on 09-17/18 reads 0 because the counterfactual track has not yet
reached its typical fill time (section G), not because those orders would not fill.

By variant: 5632da729fa7 placed 24,899, filled 1, nw_filled 450, games 148, nfl 9,268, ncaaf 15,631
            f259ca109084 placed 12,470, filled 2, nw_filled 285, games 143, nfl 4,600, ncaaf 7,870
            ff363c8ac08d placed  1,839, filled 0, nw_filled  35, games  96, nfl   513, ncaaf 1,326

Status and cancel reason (all non-replay orders):
cancelled | fair_stale      | 38218
cancelled | signal_rejected |   499
cancelled | reprice         |   351
expired   |                 |    61
open      |                 |    56
cancelled | venue_move      |    18
cancelled | edge_decay      |     5

== D. Order lifetimes by cancel reason ==
select cancel_reason, count(*), percentile_cont(0.5) within group (order by extract(epoch from cancelled_at-placed_at)),
       percentile_cont(0.9) ..., percentile_cont(0.99) ..., round(avg(staleness_at_place)), round(avg(stale_allowance_at_place))
from orders where not replay and cancelled_at is not null group by 1;
reason          |     n | median_s | p90_s  | p99_s  | stale_at_place | allowance
fair_stale      | 38239 |      225 |  1650  | 15735  |             78 |       220
signal_rejected |   499 |       15 |  4914  | 17762  |            121 |       220
reprice         |   351 |     2730 | 13230  | 22042  |             70 |       220
venue_move      |    18 |      900 |  4901  |  5545  |             75 |       220
edge_decay      |     5 |     6330 |  6330  |  6330  |            168 |       220
(38,239 orders carry a fair_stale cancel event; 38,218 of them are in status cancelled.)

Staleness at placement over all 39,208 orders: median 75 s, p90 113 s; stale_allowance_at_place min = max = 220;
feed_kind featured 39,208, alternate 0.

== E. Re-placement churn ==
select day, count(*), count(distinct (variant_id, venue_market_id, side)) from orders where not replay group by 1;
2026-09-15 | 9765 | 429      2026-09-16 | 9929 | 395      2026-09-17 | 6194 | 410      2026-09-18 | 2455 | 364
(Earlier days: 09-08 1258/319, 09-09 2615/333, 09-10 3836/352, 09-11 1120/389, 09-13 871/151, 09-14 1165/350.)

== F. Cancel instant against the fair value row's creation (last 3 days) ==
select count(*), percentile_cont(0.5) within group (order by extract(epoch from o.cancelled_at - fv.created_at)), percentile_cont(0.9) ...
from orders o join fair_values fv on fv.id=o.fair_row_id_at_place
where not o.replay and o.cancel_reason='fair_stale' and o.placed_at > now() - interval '3 days';
n 25868 | median 233 s | p90 403 s

Lifetimes by regime (fair_stale cancels):
regime  | ttk_window | n     | median_life_s | p90_life_s
weekday | inside_3h  |    44 |           420 |       3315
weekday | outside_3h | 37892 |           225 |       1650
weekend | outside_3h |   323 |           600 |       1800
(weekend = Chicago Saturday or Sunday placement; inside_3h = kickoff - placed_at < 3 h.)

Placements and counterfactual fills by regime (all non-replay orders):
weekday | inside_3h  |    87 placed |   0 would have filled
weekday | outside_3h | 38250 placed | 742 would have filled
weekend | inside_3h  |   450 placed |   0 would have filled
weekend | outside_3h |   421 placed |  28 would have filled

== G. The no_watcher counterfactual ==
with f as (select o.id, o.placed_at, o.cancelled_at, o.expiry, min(fl.filled_at) first_fill from fills fl join orders o on o.id=fl.order_id
           where fl.fill_method='no_watcher' and not fl.replay group by 1,2,3,4)
select count(*), sum((first_fill > coalesce(cancelled_at, expiry))::int), percentiles of (first_fill - placed_at) in minutes from f;
orders 770 | filled after cancel 767 | p25 1421.8 min (23.7 h) | median 1957.5 min (32.6 h) | p75 3042.4 min (50.7 h) | p90 4111.6 min (68.5 h)

Cancel reason of the 770: fair_stale 720 | signal_rejected 27 | reprice 11 | venue_move 9 | edge_decay 3.
Distinct positions among the 770: 92 (variant, market, side) keys; 58 (market, side); 36 games (ncaaf 28 games / 460 orders, nfl 8 games / 310 orders).
By variant: 5632da729fa7 55 keys / 450 orders; f259ca109084 28 / 285; ff363c8ac08d 9 / 35.

== H. Executor events (order_events, non-replay) ==
All time:                                         Last 3 days (ts > now() - 3 days):
skipped  | book_dirty      | 109835                cap_gate | cap_daily       | 63568
skipped  | fair_stale      | 106398                skipped  | fair_stale      | 56853
cap_gate | cap_daily       |  84177                skipped  | exec_capacity   | 51234
skipped  | exec_capacity   |  64912                skipped  | book_dirty      | 34426
place    |                 |  39208                place    |                 | 26036
cancel   | fair_stale      |  38239                cancel   | fair_stale      | 25885
cap_gate | cap_per_game    |  13828                cap_gate | cap_per_game    |  9220
cancel   | signal_rejected |    499                skipped  | signal_rejected |   263
skipped  | no_book         |    384                cancel   | reprice         |    93
cancel   | reprice         |    351                cancel   | signal_rejected |    51
skipped  | signal_rejected |    263                skipped  | no_book         |    38
skipped  | kickoff         |    138                skipped  | kickoff         |    18
expire   | expiry          |     61                cancel   | venue_move      |     6
cancel   | venue_move      |     18                skipped  | post_only_reject|     2
cancel   | edge_decay      |      5
skipped  | post_only_reject|      2
(cap_gate rows are the constrained variant's apply_caps labels.)
fair_p_at_event is NULL on all 38,239 fair_stale cancel events, so "the fair had not changed at cancel" is inferred from
the rule (a newer fair row resets the age), not read from the event.

Pending counterfactual rows: select status, count(*) from orders where nw_done=false and not replay group by 1;
open 19 | cancelled 27038

== I. Strategy stage, last 24 h, gate variant and constrained (signals, non-replay) ==
5632da729fa7 | candidate |                 | 46854
5632da729fa7 | rejected  | price_band      | 18982
5632da729fa7 | rejected  | not_stale       | 13924
5632da729fa7 | rejected  | disagreement_ok |  9974
5632da729fa7 | rejected  | volume          |  1944
5632da729fa7 | rejected  | ttk             |   316
5632da729fa7 | rejected  | velocity        |     4
ff363c8ac08d | rejected  | cap_daily       | 20147
ff363c8ac08d | rejected  | price_band      |  9491
ff363c8ac08d | rejected  | not_stale       |  6962
ff363c8ac08d | rejected  | disagreement_ok |  4987
ff363c8ac08d | candidate |                 |  2790
ff363c8ac08d | rejected  | volume          |   972
ff363c8ac08d | rejected  | cap_per_bet     |   278
ff363c8ac08d | rejected  | cap_per_game    |   212
ff363c8ac08d | rejected  | ttk             |   158
ff363c8ac08d | rejected  | velocity        |     2

Intents by Chicago day: 09-14 39,660 | 09-15 21,566 | 09-16 22,444 | 09-17 73,082 (123 markets, 53 games) | 09-18 5,138 (to ~11:00 CT).

== J. Pinnacle moneyline update cadence, one game (Thursday NFL, game_id 115), 2026-09-15 13:00Z to 2026-09-18 00:00Z ==
with u as (select distinct game_id, book_last_update from odds_snapshots where game_id=115 and book='pinnacle' and market_type='h2h' and fetched_at between ...),
gaps as (select extract(epoch from book_last_update - lag(book_last_update) over (order by book_last_update))/60 gap_min from u)
select count(*), percentiles, share of gaps > 220 s from gaps;
updates 263 | median gap 15.0 min | p75 15.4 | p90 15.7 | share of gaps over 220 s 0.63
Reading: distinct last_update values are bounded by the weekday fetch cadence (900 s), so the line updates at least as often as it is
fetched. One game only; the executor's clock is fair_values.created_at regardless (section K), so this bounds the inference rather than carrying it.

== K. Code and document references ==
harness/execution/plan.py:455-467   _fair_stale: age = market.fair_age_s(now); allowance = max(cfg["stale_s"], market.stale_allowance_s); policy.stale_allowance_s overrides
harness/execution/store.py:399-416  fair_ts is fair_values.created_at ("never the snapshot's")
harness/recorder/cadence.py:20-37   interval_for: None 01:00-08:00 CT unless in progress; 20 s NFL T-100..T-60; 120 s from 3 h before the sport's first kickoff of the day
                                    through its last kickoff or while in progress; 300 s Saturday/Sunday; 900 s otherwise
harness/config/settings.py:67,91    exec_period_s 15; exec_max_open_orders 150 (shared across executed variants)
harness/variants/*.yaml             stale_s 180, max_open 25, min_ttk_min 20 in every registered variant
docs/superpowers/specs/2026-09-07-phase3-paper-execution-design.md §0.1  stale_allowance_s = feed interval + tick budget (featured 120 + 100 = 220 s); §1 line 32 the watcher's cancel list including fair_stale (F36)
docs/superpowers/reviews/2026-09-07-autopilot-adversarial-review.md F36  "The executor never checks fair-value staleness, and a dead WS recorder leaves a frozen book"
docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md §9.2      the v2 cancel list: edge < edge_min/2, kickoff < 10 min, kill switch, venue mid >= 2 pts against
docs/superpowers/specs/2026-09-13-phase6d-sustained-evaluation-design.md §0.9, §0.15a, §1.6  baseline stated (incl. the 300/900 s cadences), policy-compare, six alternatives, registration, "after Mon 2026-09-21 09:00 CT is exploratory"
harness/execution/policy.py ALTERNATIVES  stale_allowance_900, rest_to_expiry, per_variant_slots (25), fillability_admission, join_the_bid, near_kickoff_only (180)
harness/cli.py:836                   @app.command("policy-compare")
.superpowers/sdd/2026-09-13-phase6d-sustained-evaluation/progress.md:10  "the policy comparison (T9) is designed on fixtures and never run on the live tape"
docs/superpowers/plans/2026-09-13-phase6d-sustained-evaluation.md:5166   "The comparison run waits for 6B and is a separate operate duty"
gate_reports id 9 (2026-09-15 05:41Z, 5632da729fa7): fill_events 0 (needs 150, 40 games, both sports, 80 % ws-clean); markout_30m anchor nw_fill 0.01406, t 3.88, n 141, 10 clusters, passed; adverse_drift n 0
gate_reports id 7 (f259ca109084): fill_events 1; markout_30m 0.01649, t 3.82, n 117, 7 clusters
report_runs id 29 (2026 week 38, provisional, 2026-09-18 15:22Z) t1 sharp_direct nfl: markets_scanned 885, signals 232,276, candidates 22,237, orders 3,542, fills 2, fill_rate 0.0003
reports/2026-09-18-veto-pacing-brief.md: 3,149 decided of 178,618 intents over 7 days; none inside 5.7 h of kickoff; the $25 cap spent before 09:00 CT daily
Table sizes: signals 23.1 M rows / 14 GB; orders 39,659 rows / 1,294 MB; fills 3,657 rows; fair_values 5.78 M rows; intents 270,602.
```
