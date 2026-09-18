# Fill-starvation review: live-data confirmation, 2026-09-18

The cadence mismatch is confirmed. The proposed inference from additional counterfactual fills to a successful holding policy remains unsupported. Live queries also reveal that the packet's positive markout evidence is from the earlier simulator cohort, that repeated orders materially change the estimated mean, and that book event age is a second pervasive obstacle.

This is an independent review, not an autopilot decision or a policy adoption. The user authorized live SQL after the initial review. No production data, settings, registered variants, gate definitions, or runtime code were changed. Local review and evidence files were added; the original packet was left intact.

## Scope and reproducibility

Code reviewed at main `072dde1`; the reviewed execution/policy/store/veto files have no diff from runtime `4066197`. Production reads ran on 2026-09-18, 12:37-12:45 CT. Each substantive SQL transaction explicitly used `BEGIN READ ONLY`, with session default read-only, 25-second statement timeout, and 1-second lock timeout. Every logged successful query finished in less than one second.

Most event filters use the fixed cutoff **2026-09-18 17:37 UTC / 12:37 CT**. Order status/counters and spend are their state when read, not a reconstruction of a globally consistent snapshot at that cutoff. Historical cadence classification uses the current games schedule; time-to-kickoff in order-level analyses uses the kickoff frozen on the order. These are exploratory diagnostic reads, not a gate report or a causal policy experiment.

The initial connection probe used a multi-statement `-c` and showed that changing `default_transaction_read_only` did not retroactively change that already-open implicit transaction. It executed only settings and a SELECT. All subsequent data queries used explicit read-only transactions, verified in the evidence.

Evidence (SQL is echoed beside results):

- [Queries A-E: orders, repair cohorts, fill timing, repeated trade attribution](../evidence/2026-09-18-fill-review-confirmation-1.txt)
- [Queries F-J: fair at cancellation, actual cadence regimes, markouts, book counters](../evidence/2026-09-18-fill-review-confirmation-2.txt)
- [Queries K-N: veto, spend, dirty intervals before first fill](../evidence/2026-09-18-fill-review-confirmation-3.txt)
- [Query O: individual tape prints versus hypothetical quantities](../evidence/2026-09-18-fill-review-confirmation-4.txt)
- [Queries P-Q: game identity, fair age, adverse drift](../evidence/2026-09-18-fill-review-confirmation-4b.txt)
- [Markout export SQL](../evidence/2026-09-18-fill-review-markouts.sql), [571 exported observations](../evidence/2026-09-18-fill-review-markouts.csv), [analysis script](../evidence/2026-09-18-fill-review-analyze.py), [computed estimates](../evidence/2026-09-18-fill-review-analysis.json)

Query P initially used the wrong team-name column and stopped the connection; its error is preserved in `confirmation-4.txt`. The corrected query uses `display_name` and the full `(sport, id)` team key in `confirmation-4b.txt`. Query O succeeded before that error. The statistical script converts undefined confidence limits to JSON null and reuses the repository's `cluster_ci`; no production scoring function was invoked.

## 1. The cancellation mechanism is confirmed

There were **40,023 orders**, of which **39,086 (97.66%)** carried `fair_stale` as cancellation reason. Each executed variant's median stale-cancel lifetime was **225 seconds**. Every order's frozen stale allowance was **220 seconds**. Queue-model filled orders remained **one gate-variant order, two primary orders, zero constrained orders**.

The packet's cancellation-age query joined the fair row at placement. Query F instead reconstructs the latest gap/fair timestamp at cancellation for a deterministic sample (`id % 47 = 0`, post-repair, capped at 650):

- 617 orders sampled; all 617 had a fair age beyond their allowance.
- Median latest-fair age at cancellation: **232.53 seconds**; p90 **370.07 seconds**.
- 15 had a different fair row from the row at placement.

This supports the mismatch with a stronger timestamp reconstruction. It does not establish how many cancellations would instead have been caused by another rule after removing `fair_stale`, or reconstruct transaction-commit visibility at each historical instant.

## 2. New data supports the far-from-kickoff opportunity, with narrower claims

All **238 counterfactual-filled orders placed after the repair boundary** first filled **more than 24 hours before their own kickoff**. They cover **16 market-sides and eight games**. Their median wait was **27.90 hours** and median time remaining to kickoff at fill was **70.80 hours**.

Applying the sport-wide cadence logic at first fill yields:

| Cadence at first fill | Post-repair orders |
|---|---:|
| Weekday 900-second cadence | 233 |
| Sport-wide 120-second game window | 2 |
| Overnight quiet hours | 3 |

This strengthens the packet's far-out diagnosis. My initial concern that early placements might simply fill near kickoff does not explain this new-order cohort. It remains relevant to the mixed history: 84 earlier orders first filled inside three hours of kickoff, and 258 orders across all cohorts first filled during the sport-wide 120-second regime. The statement that the quiet regime is the *only* regime that fills is too strong.

The sample is immature: many recent placements remain unresolved, and these are still counterfactual orders. Eight games cannot establish a dependable weekly accrual rate.

## 3. Positive historical markouts do not carry over to the newer cohort

Split markouts by the first fill that establishes the `nw_fill` anchor. The boundary is **fill id 1878**; order id alone is insufficient because some earlier orders continued simulating after the repair. The export uses the earliest of `queue_model` and `no_watcher`, matching settlement's anchor semantics; every exported anchor timestamp matched its first fill.

Using the existing changed-fair condition, complete markouts, and kickoff-moved exclusions, the order-weighted net 30-minute markouts are:

| Variant | Earlier fill cohort: mean points | Post-repair fill cohort: mean points | Post-repair 90% game-clustered CI | Orders / games |
|---|---:|---:|---|---:|
| Gate: `sharp_two_sided` | +1.417 | **-0.755** | **[-2.035, +0.525]** | 191 / 11 |
| Primary: `sharp_direct` | +1.649 | **-1.215** | **[-2.490, +0.061]** | 95 / 8 |

Restricting further to orders placed after the repair gives **-0.604 points** for the gate variant (153 orders, eight games) and **-1.194 points** for the primary (77 orders, six games). Both intervals still include zero.

These results withdraw the packet's positive reassurance; they do **not** prove that the strategy loses or that the simulator repair caused a change in economics. The cohorts have different games and dates, small game counts, and overlapping hypothetical orders.

Weighting matters substantially. LSU at Ole Miss, game 469, supplies **96/191 gate observations** and **59/95 primary observations** in the post-repair fill cohort. Averaging within each market-side and then weighting market-sides equally changes the means to **+0.597** and **+0.365** points, respectively; both corresponding game-clustered intervals also include zero. These are sensitivity analyses, not replacement gate estimators or an executable portfolio. Clustering corrects uncertainty for dependence; it does not remove churn's weighting of the mean.

Freshness also limits interpretation. For newly placed post-repair orders, the median source-fair age used at the 30-minute markout is **783 seconds** for the gate variant and **829 seconds** for the primary. Restricting to markout fair age at most 220 seconds leaves negative, inconclusive means on only five and four games. That selection is another diagnostic, not a proposed gate change.

Counterfactual adverse drift is measurable even though the gate's watched-fill-restricted criterion has almost no observations: the new-order means are **-1.071 points** for the gate variant and **-0.635 points** for the primary. These use the recorded fair at fill, subject to its observation age; they are not interpolated true contemporaneous prices.

## 4. Counterfactual fill counts cannot be summed as executable liquidity

Within the gate variant's post-repair fills, **1,046 counterfactual fill rows refer to 151 distinct ticker/side/trade keys**. Of those keys, 107 are attributed to multiple orders, accounting for 1,002 fill rows.

A concrete example from the LSU-at-Ole-Miss market: one recorded **10-contract trade** is credited to **45 hypothetical orders**, totaling **346.60 hypothetical contracts**. Another 10-contract trade is credited as 250 contracts across 25 orders.

This is legitimate for separate hypothetical histories. It is not evidence that all those orders could fill simultaneously in one portfolio. A policy replay must carry the actual permitted order state and allocate liquidity consistently within each arm. The 770 orders are neither 770 independent observations nor a forecast of realizable filled orders.

## 5. Book event age is a second pervasive obstacle

For the 238 filled counterfactual orders placed after the repair, intersecting dirty intervals with **placement through first fill** gives:

| Variant | Orders | Zero recorded dirty time before first fill | Median dirty hours | Median wait hours |
|---|---:|---:|---:|---:|
| Gate | 153 | 6 | 8.73 | 27.53 |
| Primary | 77 | 1 | 8.34 | 28.31 |
| Constrained | 8 | 0 | 10.12 | 25.98 |

Every order had a WebSocket book at placement. **231 orders encountered `event_age`; 14 encountered `gap`** (these counts overlap). Event age accounts for most of the attributed dirty duration. The median unobserved duration is zero, which does not imply every order has complete observation.

The code marks a market dirty when its latest book event is older than `exec_book_max_age_s`, even apart from the recorder-dead and sequence-gap paths (`execution/plan.py:254`, `execution/loop.py:944`). In a quiet market, this needs investigation into whether the subscription remained intact and its book unchanged, or whether updates were actually missed. The stored cause alone cannot establish which occurred.

A longer fair allowance does not resolve this book-quality problem. Investigate transport/subscription continuity and bounded book verification as part of the experiment. Do not silently relabel old dirty intervals or relax gate criteria. These elapsed-time diagnostics are not the gate's nominal `dirty_minutes` measure and should not be reported as a recalculated clean-fill gate percentage.

## 6. Veto spending is independently misallocated

Between the repair and the cutoff, **198 of 896 uncached decided signal evaluations** concerned signals created while an unfilled order on the same variant/market/side was demonstrably resting. **28** were decided while such an order was still resting. These are conservative counts: the query requires a later recorded cancellation and excludes orders with any watched fill. They count uncached decision rows, not a direct dollar attribution.

This agrees with the implementation: candidate signals create intents and queue veto work before the planner decides whether to place another order. Longer holding does not remove the queue entries.

For the current Chicago week through the cutoff, **2,392 proceed decisions** included **zero signals inside six hours of kickoff**; **3,606 near-kickoff signals** were marked `veto_skipped_budget`. Recorded spending through Friday was **$122.2595**. Daily pacing and a weekly reserve remain necessary decisions regardless of the holding policy.

## 7. Revised action recommendation

1. **Retain the cadence diagnosis as a confirmed bottleneck.** Preserve the distinction between identifying a cancellation cause and estimating the effects of changing it.
2. **Do not use the current `policy-compare` filled column to select a holding policy.** `execution/policy.py:394-418` explicitly lacks resting-order reconstruction and reads outcomes of historical orders. Neither running it on production tape nor adding a seventh parameter repairs that limitation.
3. **Run a bounded stateful paper comparison**, using the existing executor and fill model with independent state per policy, as-of inputs, actual book-quality intervals, repricing, shared capacity, and adequate lifecycle follow-up. Compare cadence-aware freshness against faster fair collection with the existing allowance. Faster collection requires prospective observations.
4. **Investigate the meaning of book inactivity alongside fair freshness.** The opportunity exists largely in markets where both clocks routinely expire. More accurate observation may be more valuable than wider tolerances.
5. **Specify the independent opportunity and resource allocation before comparing results.** Report order counts, market-sides, game counts, queue turnover, and concentration together. Preserve frozen gate definitions; new exploratory weightings remain separately labelled.
6. **Decide veto pacing separately**, with coverage across kickoff windows and the week. Keep coverage correctness and backlog protection moving; the 27,867 currently closed-status pending counterfactuals do not disappear immediately after a policy change.

On the packet's letters: **A modified (b), B (d), C (c), D amended (a), E (a), F (b)**. E should give scenario ranges and explicit unknowns rather than extrapolating pooled counterfactual rows into a gate date.

The next success criterion should be a credible answer about executable edge on a defined population. The charter's dataset, mispricing-map and convergence-lag deliverables should continue alongside the execution experiment, with a dated retain/revise/stop decision for the maker hypothesis.
