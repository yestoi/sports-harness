**External review package**

This file contains the complete assessment, observed production evidence, captured reproduction results, reproduction code, and frozen source excerpts. Full cited source files are included under `evidence/source/` at the reviewed commit; the original review artifacts are preserved under `evidence/originals/`. `manifest.json` records file hashes and provenance.

Observations are from September 11, 2026 around 11:01–11:04 CT. The reviewed local commit was `6eed2d8d49a7bf1f107dfd83141b8eeef556b620`; the observed NAS build was `7c3d555`. This export does not refresh production evidence. Reproductions require the project's Python environment and reviewed checkout; changing the code can change their outputs. Production evidence below consists of summarized observations recorded during the review, rather than a raw database-response archive.

**Phase 5 review: the experiment is worth repairing, but its low activity is not yet a strategy result.**

Reviewed September 11, 2026 from implementation, execution, experimental-design, and infrastructure perspectives, including three independent reviewers. Local revision `6eed2d8`; live NAS revision `7c3d555`. The [observation record](#observed-evidence) preserves bounded production reads around 11:01–11:04 CT. The [reproduction script](reproduce.py) demonstrates five defects without database or network access. The review and its evidence are stored outside the repository; application code, thresholds, deployment, and experiment records were not changed.

My assessment is that the project has strong research intentions and useful infrastructure, but implementation and operational validation have not caught up with feature breadth. There is a concrete software defect suppressing participation, alongside actual hardware contention. The existing Mac mini is a sensible dedicated host if its memory and local storage are adequate. Migration alone would leave the main participation defect and several evidence-quality defects intact.

| Perspective | Assessment |
|---|---|
| Experimental design | Good safeguards: frozen variants, fees, independent game counts, uncertainty, counterfactual separation, and a demanding gate. Current executable sample is insufficient. |
| Execution correctness | Needs immediate repair: false dirty books, optimistic queue depletion, a rejected-intent placement path, and an expiry boundary error. |
| Operational engineering | Useful indexes, bounded reads and recovery machinery, but game-window headroom and complete pricing/reporting coverage are not established. |
| Test and review quality | Considerable work and many passing tests; several tests verify the implementation's assumptions rather than the economic or protocol invariant. |
| Product and reporting | Helpful surfaces, with misleading aggregate fill counts, a wrong-contract fair-value join, and some silently narrowed measurements. |

**What one position actually means.**

The live database confirms one `queue_model` filled order across one game: two partial fill records, 38.92 contracts, $17.51 stake, in `sharp_direct`. `sharp_two_sided`, the go-live gate variant, has zero such filled orders. `constrained` also has zero. Other recorded fills are `no_watcher` or `snapshot_cross` counterfactuals and do not create positions. The positions view correctly makes that distinction ([schema.py:277](evidence/source/harness/db/schema.py)); the Floor funnel's aggregate fill counter does not ([floor.py:228](evidence/source/harness/dashboard/snapshots/floor.py)).

One open position could ordinarily conceal many settled positions, but here the all-history method-specific query rules that out. The disappointment is grounded in the actual sample. It is too early to conclude that the strategy lacks opportunity, and incorrect to explain the whole result as prudent filtering. The system has not had a clean, functioning opportunity to demonstrate its achievable participation rate.

**Findings, in priority order.**

1. **High — valid multiplexed WebSocket traffic makes books falsely dirty, suppressing orders and fills. Confirmed in code and live input.** The recorder checks sequence continuity per subscription, correctly recognizing that one subscription carries many markets ([ws_sink.py:84](evidence/source/harness/recorder/ws_sink.py)). The executor reads each ticker separately, then incorrectly demands consecutive sequence numbers within that ticker ([book.py:56](evidence/source/harness/execution/book.py), [book.py:272](evidence/source/harness/execution/book.py)). A complete A1 → B2 → A3 stream therefore marks A dirty. The live sample contains a fully continuous sid-2 sequence 50604–50615, with TENNGT at 50604, 50606, 50611 and 50613. This is ordinary interleaving, not missing data. Dirty books skip fill simulation ([loop.py:799](evidence/source/harness/execution/loop.py)) and reject new placements ([plan.py:532](evidence/source/harness/execution/plan.py)). Existing orders hold before stale-fair and edge cancellation checks. The heartbeat had 132 dirty markets despite zero sampled tape backlog. This is a strong explanation for depressed participation; its exact contribution requires corrected replay. Validate continuity over the complete subscription stream and preserve real gap detection, including connection/recovery boundaries. Add a mixed-market end-to-end tape case. This is the first repair, regardless of host.

2. **High — scheduling and pricing budgets prevent sustained participation even on otherwise usable books. Observed behavior plus design interaction.** Weekday featured cadence is 900 seconds ([cadence.py:33](evidence/source/harness/recorder/cadence.py)); featured freshness allowance is fixed at 120 seconds plus the 100-second tick budget ([fair.py:22](evidence/source/harness/pricing/fair.py)). Affected clean orders can therefore rest for only roughly 220/900, or 24%, of a regular cycle before other delays. This is an illustration for affected markets, not a measured whole-system availability percentage; alternate feeds and game windows differ. Live orders show 8,095 stale-fair cancellations out of 8,273 placed rows, about 98%. In seven morning pricing runs, six exhausted budget and four scored no variants. Current budget is already 45 seconds, not the historical 20 seconds. Prioritizing gate and primary does not guarantee they run when fair/gap computation consumes the budget first ([pipeline.py:181](evidence/source/harness/strategy/pipeline.py)). The report's claim of 100% coverage by construction is false ([tables.py:387](evidence/source/harness/report/tables.py)). Align refresh and evaluation with intended participation windows, reserve resources for the core variants, and report every omitted run. Preserve freshness standards rather than merely allowing older information.

3. **High — the queue simulator can credit fills that one real trade cannot support. Confirmed reproduction; existing fill impact unknown.** Equal-time book deltas are processed before prints ([fills.py:44](evidence/source/harness/execution/fills.py)). The negative delta is interpreted as cancellation before its associated trade has been accounted for ([fills.py:307](evidence/source/harness/execution/fills.py)). With five contracts ahead, one three-contract trade and its matching −3 book decrement produce a one-contract simulated fill; they should leave two ahead and no fill. An existing test explicitly expects the erroneous result ([test_fills.py:191](evidence/source/tests/test_fills.py)). Reconcile trade-driven depletion with book changes, including equal timestamps and processing across batches. Audit order 157 and counterfactual outcomes under the corrected model. The review has not established that order 157 was affected. Replay matching live results cannot certify economic correctness when both use the same flawed arithmetic.

4. **High — two additional execution boundaries are wrong. Confirmed pure reproductions; historical impact not established.** An intent whose latest decision is rejected cancels an existing order, but with no open order the same intent produces a new `Place` because the placement chain omits the rejection check ([plan.py:511](evidence/source/harness/execution/plan.py), [plan.py:519](evidence/source/harness/execution/plan.py)). Separately, watched simulation runs through `now`, even beyond expiry; the counterfactual correctly uses `min(now, expiry)` ([loop.py:849](evidence/source/harness/execution/loop.py), [loop.py:867](evidence/source/harness/execution/loop.py)). An expiry at +10 seconds accepts a +11-second trade when processed at +15 seconds. The two actual production fill rows precede expiry, so no existing post-expiry queue fill was found. Fix both paths before expanding the sample; prolonged NAS delays enlarge the expiry defect's potential impact.

5. **High — research confirmation and amendment handling are not yet reliable enough for the scheduled conclusions.** Week-3 confirmation counts selected intervals excluding zero without the normal minimum of ten game clusters ([weekly.py:201](evidence/source/harness/report/weekly.py), versus [tables.py:808](evidence/source/harness/report/tables.py)). A two-game, greyed cell is counted as confirmed in the reproduction. Selection also omits effect direction, so an opposite-sign interval can count as confirmation; define the intended direction rule explicitly. Preregistration requires certain known measurement-error periods to be excluded or re-scored ([preregistration:96](evidence/source/docs/superpowers/reviews/2026-09-07-phase2-preregistration.md), [preregistration:119](evidence/source/docs/superpowers/reviews/2026-09-07-phase2-preregistration.md)), but default report SQL and CLI do not enforce an inclusion manifest. Close that analysis-control gap before Monday's report and the week-2 selection freeze. Preserve original rows and publish exclusions/corrections with reasons; do not silently discard difficult operational periods.

6. **Medium — the dashboard can show the wrong contract's current fair and an ambiguous funnel. Confirmed code defects/semantic changes.** Floor's fair-value lateral matches only game and market type, omitting outcome and threshold ([floor.py:264](evidence/source/harness/dashboard/snapshots/floor.py)). Another team's moneyline or another spread/total line can supply the displayed fair. The executor's contract-specific gap lookup is separate, so a suspicious Floor edge is not proof of a wrong execution decision. Also, NAS optimization changed the reported intent denominator into placements plus skip events; an intent can contribute multiple reasons and later place ([floor.py:472](evidence/source/harness/dashboard/snapshots/floor.py)). The six-hour funnel showed 1,001 candidates but 1,383 intents. Those are different populations/units, not a conversion rate. Expose unique candidates, distinct intents, placements, actual fills and counterfactuals separately, and join displayed prices by exact contract identity.

**Did NAS workarounds sacrifice quality? Yes, in specific ways; many optimizations should remain.**

| Change or incident | Effect on quality | Judgment |
|---|---|---|
| Indexed cursor reads, adaptive batches, cached immutable geography, stored report-cell rendering, RFQ deduplication | Less work for the same intended computation | Sound engineering; retain and validate on the new host. |
| Pricing deadlines and rotating secondary evaluation | Uneven coverage; sometimes no primary/gate evaluation | Affects what experiment was actually run. Record the missing opportunities. |
| Temporarily disabled workers/builders; skipped markout/report stages | Missing or delayed measurements | Reasonable containment, but a guarded failure is not successful collection. |
| Floor exposure restricted to fills from 14 days | Older unsettled exposure can disappear from the display | Add an explicit coverage signal or complete aggregate; execution still reads full positions. |
| Floor funnel redefined around available metrics | Ambiguous denominators and omitted paths | Restore meaningful definitions rather than making the query faster by changing its question. |
| Nightly dump/query storms, reconnects and documented deploy gaps | Timing degradation and interrupted collection; assess continuity for each window | These windows cannot automatically be treated as clean evidence of strategy behavior. |

The exposure bound is at [floor.py:314](evidence/source/harness/dashboard/snapshots/floor.py); execution's independent position read is at [loop.py:445](evidence/source/harness/execution/loop.py). Provisional reporting also changed to six-hourly and can yield under the shared settlement budget ([report_wtd.py:25](evidence/source/harness/settlement/report_wtd.py)). Five consecutive completed settlement runs in the live morning sample exhausted markout/report budgets despite status `ok`.

Some incidents attributed to the NAS were software failures: BRIN summarization changed a query from 39.6 seconds to 2.8 milliseconds on the same host ([journal:1203](evidence/source/docs/superpowers/autopilot/journal.md)); the annotator repeatedly rebuilt a whole report; RFQ ingestion was designed around a much smaller workload than the observed 11–14 thousand frames per minute. A larger machine does not cure amplification or unsupported API schemas. Phase 5's final review and subsequent hotfixes demonstrate substantial diligence, but a phase marked complete with key live behavior unverified is feature completion, not operational acceptance.

**Is migration justified? Yes, as a capacity and isolation decision, conditional on the available Mini's configuration.**

After the main NAS query fixes, September 10's 19:00 hour still had executor mean 27.5 seconds and p95 118 seconds against a 15-second period and 7.5-second target. The host had active paging, 25–32% I/O wait and memory-pressure stalls around 16% of the time ([journal:1261](evidence/source/docs/superpowers/autopilot/journal.md)). Stopping sixteen media containers helped but left evening p95 around 9.1–15.2 seconds ([journal:1302](evidence/source/docs/superpowers/autopilot/journal.md)). The project's own migration trigger was met.

The current morning snapshot is healthier: no active paging in the brief sample, fresh builders under 250 ms, and about 2.8 GB available memory. Nevertheless, sampled executor p95 was still about 14 seconds, and pricing/settlement remained incomplete. This supports inadequate headroom, not a claim of perpetual thrashing. The precise share attributable to hardware versus the defects above has not been isolated by a controlled comparison.

Use the available Mini as a dedicated host if it supplies materially more usable memory and local SSD capacity. Its specifications were not supplied at review time, so this is not a hardware-sizing certification or a reason to buy a particular model. Place the active database on local storage; use the NAS for backups/archives. If using Docker Desktop on macOS, explicitly allocate VM resources and use a named volume for the database rather than blindly carrying over the NAS's `./pgdata` bind mount. Docker documents a default VM allocation of half host RAM and recommends VM data volumes for database performance ([Docker settings](https://docs.docker.com/desktop/settings-and-maintenance/settings/)). Verify storage runway: the current database is already roughly 46 GB and was recently growing roughly 9 GB/day. A faster processor does not resolve limited SSD capacity.

Acceptance should cover two representative game windows, a nightly backup, and a cold restart with intended workers enabled. Require the existing 7.5-second executor p95 target, no persistent tape backlog or sustained paging, subscription-correct clean-book coverage, timely primary/gate pricing, and completed markout/report jobs. Compare operation on the same code and record the host change so hardware improvement is distinguishable from software improvement.

**Viability and the revised phase 6 order.**

The underlying question—whether sharp-book prices imply a fee-adjusted executable advantage at Kalshi—is testable. The project already has valuable raw recording, frozen configurations, Decimal money arithmetic, explicit alternative fill models, and game-clustered reporting. None of that establishes an edge. H1 is explicitly insufficient below 30 queue fills and ten games ([preregistration:71](evidence/source/docs/superpowers/reviews/2026-09-07-phase2-preregistration.md)); the go-live gate requires 150 queue-filled orders across 40 games and both sports plus the other criteria ([gate.py:119](evidence/source/harness/report/gate.py)). Current evidence is far below either. The phase-5 shadow veto is an observational analysis with later searches, not a proven executable improvement; its asynchronous latency also survives a host change.

The current phase-6 list starts with model refinements and optional labels ([roadmap:278](evidence/source/docs/superpowers/autopilot/roadmap.md)). I would reorder the work as follows:

1. Repair subscription sequence handling, queue depletion, expiry, and latest-rejection placement. Add realistic mixed-ticker and economic-invariant cases; review and version the resulting measurement changes.
2. Align pricing/refresh scheduling and record exact evaluation coverage. Build the useful funnel: unique candidate opportunities → eligible intent episodes → clean resting market-hours → actual queue-filled orders → independent games → mature CLV/markout coverage. Include skipped and degraded time beside it.
3. Migrate to an adequately configured dedicated host and demonstrate the operational acceptance conditions. Complete pending deploy/worker fixes and verify next-partition BRIN options; the journal records an existing-parent inheritance caveat ([journal:1232](evidence/source/docs/superpowers/autopilot/journal.md)).
4. Repair exact-contract dashboard values, confirmation sample floors, amendment inclusion controls and missing-outcome reporting. Re-score affected stored periods where the tape supports it; keep raw records and correction provenance.
5. Collect at least a complete healthy football weekend before judging achievable fill rate. Report fills per clean resting market-hour, games covered, and fresh mature outcome coverage separately. Forecast the time needed for 150 filled orders/40 games for `sharp_two_sided` alone, without pooling other variants or counterfactual fills; extend the schedule explicitly if required. This is an operational and sample-accrual checkpoint. Even the H1 minimum is an interpretation floor, not proof of profitability.
6. Then add key-number variants, taker imbalance and additional research features. Keep new strategy changes separately registered. Do not increase activity merely to make the dashboard more satisfying.

If healthy, correctly simulated exposure still produces almost no fills, that becomes a useful negative result about this maker strategy at its chosen prices and markets. Today, implementation defects and incomplete coverage prevent that conclusion. The immediate objective is an interpretable experiment, and the next phase should be judged by that outcome.


---

<a id="observed-evidence"></a>

**Phase 5 independent review: observation record — September 11, 2026**

Local revision: `6eed2d8d49a7bf1f107dfd83141b8eeef556b620`. Deployed revision observed through `/healthz`: `7c3d555`. Observations below were collected around 11:01–11:04 America/Chicago (16:01–16:04 UTC). Values are snapshots, not a continuously monitored interval. Production access was read-only: cached snapshot GETs, host metrics, and bounded SQL with `default_transaction_read_only=on` and `statement_timeout='3s'`. No service/configuration changes or full-table tape scans were performed.

**Actual and counterfactual fill rows at 16:02:30 UTC.** Query grouped non-replay `fills` joined to non-replay `orders`, by variant, fill method and `has_print`. Orders and games are distinct within each row, not additive across rows.

| Variant | Method | Has print | Fill rows | Orders | Games | Contracts |
|---|---|---|---:|---:|---:|---:|
| sharp_two_sided | no_watcher | true | 393 | 113 | 7 | 10,364.88 |
| sharp_two_sided | snapshot_cross | false | 6 | 6 | 1 | 575.00 |
| sharp_two_sided | snapshot_cross | true | 41 | 41 | 4 | 3,661.00 |
| sharp_direct | no_watcher | true | 258 | 85 | 7 | 7,668.85 |
| sharp_direct | queue_model | true | 2 | 1 | 1 | 38.92 |
| sharp_direct | snapshot_cross | false | 6 | 6 | 1 | 575.00 |
| sharp_direct | snapshot_cross | true | 18 | 18 | 3 | 1,517.16 |
| constrained | no_watcher | true | 23 | 10 | 3 | 994.00 |
| constrained | snapshot_cross | true | 5 | 5 | 1 | 445.00 |

There were no queue_model rows for sharp_two_sided or constrained. The two actual rows belong to order 157, ticker `KXNCAAFTOTAL-26SEP12MTUMRSH-59`, YES at 0.45. Both filled at `2026-09-08 15:07:15.332+00`, for 25 and 13.92 contracts. Placement was `2026-09-08 14:36:47.579399+00`; expiry `2026-09-12 22:50:00+00`; cancellation of the remainder `2026-09-08 15:12:06.083229+00`, reason `fair_stale`. Both rows have a WS print and neither is after expiry. This review did not replay that order to determine whether the queue arithmetic defect affected it.

**Non-replay orders at the same observation.**

| Variant | fair_stale cancels | reprice cancels | signal_rejected cancels | venue_move cancels | Expired | Open |
|---|---:|---:|---:|---:|---:|---:|
| sharp_two_sided | 5,024 | 88 | 2 | 7 | 2 | 4 |
| sharp_direct | 2,691 | 40 | 1 | 3 | 1 | 2 |
| constrained | 380 | 1 | 26 | 0 | 1 | 0 |

Total 8,273 orders; 8,095 cancelled for stale fair (97.85%). This is cancellation incidence across all placed order rows, not a distinct-opportunity rejection rate. Repeated placements/variants share market opportunities.

**Cached Floor snapshot generated 16:01:12.072106 UTC.** Six-hour funnel: 1,001 candidate rows, 1,383 reported intents, 276 placements, 42 reported fills; skips fair_stale 489, book_dirty 481, exec_capacity 137; cancellations fair_stale 270. The fills include counterfactual methods and the intent field sums decision events, so these figures must not be converted into an ordinary unique-candidate conversion funnel. Exposure showed one primary position, 38.92 contracts and $17.51 stake; gate and constrained had zero positions.

**Current performance and coverage.**

- Host memory 7,717 MB total, 2,836 MB available, 1,978 MB swap resident; the two one-second vmstat intervals had zero swap-in/out and zero I/O wait. Resident swap alone is not evidence of active thrashing.
- Cached snapshot elapsed times: floor 85 ms, pulse 165 ms, study 204 ms, gate 1 ms, ticket 4 ms; all fresh with error null.
- Executor heartbeat 16:02:11 UTC: 6 open orders, last loop 6,647 ms, rolling p95 10,552 ms, 538 accumulated skipped loops, 132 dirty markets, last_error null. The 538 figure is cumulative, not today's count.
- The preceding 30 minutes had 26 sampled `exec.loop_ms` values: mean 8,506 ms, p95 13,949.25 ms, max 17,059 ms. These are telemetry samples, not every executor iteration. `exec.tape_lag_tickers` summed to zero in that window.
- A bounded read of the newest 1,500 runs restricted to the last six hours found 7 runs with pricing diagnostics, from 13:15:42 to 15:33:42 UTC. Six exhausted their budget; four ran zero variants; gate and primary each ran in three. This small morning sample is not a full game-day rate.
- Run 11468 at 15:18:12 UTC recorded 451 direct fairs, 2,213 derived fairs, 1,937 no-sharp entries and `budget_exhausted=true`, but `variants_run=[]`, `variants_skipped=[]`, `gate_variant_missing=false`, `gate_variant_id=null`. Run 11489 at 15:33:42 ran gate, primary, wide_band and constrained, skipping three others. Both used a 45-second pricing budget.
- Five consecutive completed settlement runs starting 11:25:58 through 15:38:42 UTC had status ok but budget_exhausted true, with markouts exhausting budget and report_wtd skipped for budget. Earlier interrupted runs remained marked running.
- Housekeeping at 09:15 UTC reported database size 46.302 GB, estimated growth 8.942 GB/day; this is a historical rate estimate, not a storage forecast guarantee.
- At 16:04 UTC all six open orders had 27–28 accumulated dirty minutes.

**Real multiplexed tape, latest 12 rows at 16:04 UTC.** Query: `select id,ticker,sid,seq,kind,ts from orderbook_events where ts>now()-interval '1 minute' order by id desc limit 12`. All rows were deltas on sid 2. Listed in arrival order below. The stream has no sequence gap; individual tickers legitimately do.

| Event ID | seq | Ticker |
|---:|---:|---|
| 59955673 | 50604 | KXNCAAFSPREAD-26SEP12TENNGT-TENN12 |
| 59955674 | 50605 | KXNCAAFSPREAD-26SEP12TTUORST-TTU25 |
| 59955675 | 50606 | KXNCAAFSPREAD-26SEP12TENNGT-TENN12 |
| 59955676 | 50607 | KXNCAAFTOTAL-26SEP12TOWSSCAR-56 |
| 59955677 | 50608 | KXNCAAFTOTAL-26SEP12TOWSSCAR-56 |
| 59955678 | 50609 | KXNCAAFSPREAD-26SEP12WKUUGA-UGA40 |
| 59955679 | 50610 | KXNCAAFSPREAD-26SEP12TTUORST-TTU25 |
| 59955680 | 50611 | KXNCAAFSPREAD-26SEP12TENNGT-TENN12 |
| 59955681 | 50612 | KXNCAAFSPREAD-26SEP12WKUUGA-UGA40 |
| 59955682 | 50613 | KXNCAAFSPREAD-26SEP12TENNGT-TENN12 |
| 59955683 | 50614 | KXNFLTOTAL-26SEP13NYJTEN-39 |
| 59955684 | 50615 | KXNFLTOTAL-26SEP13NYJTEN-39 |

Timestamps span `16:04:42.928+00` to `16:04:43.010+00`. TENNGT's sequence is 50604, 50606, 50611, 50613. `BookState.apply_delta` treats those legitimate jumps as dirty. This establishes the live input condition behind the pure reproduction; attribution of every dirty minute requires a corrected replay.

**Validation.** The execution reviewer ran 109 existing tests in `test_exec_plan.py`, `test_fills.py`, and `test_fills_tape.py`: all passed. Root ran `env -u DATABASE_URL_TEST .venv/bin/pytest tests/test_book.py tests/test_cadence.py -q -rA`: 26 passed, 21 database-dependent tests skipped. Root independently ran the adjacent reproduction script, confirming false dirty books, rejected-intent placement, a post-expiry simulated fill, double queue depletion, and underpowered confirmation. No full integration suite or production replay was run. Passing existing tests does not negate the demonstrated defects; some existing assertions encode the defective behavior.


---

**Captured reproduction results**

To run the exported probes against the project checkout:

```bash
/Users/trey/dev/sports/.venv/bin/python /Users/trey/dev/sports-phase5-review-2026-09-11/reproduce.py --repo /Users/trey/dev/sports
```

Use the reviewed commit for the original results. The frozen source files included here support inspection; they are not a complete installable project environment.

Executed from the external script against the reviewed checkout during export. These probes print behavior; they do not change production or query a database.

```text
Complete multiplexed stream [1,2,3], A sees [1,3]: {'dirty': True}
Rejected intent, no order: [Place(intent_id=UUID('00000000-0000-0000-0000-000000000001'), side='yes', prob=Decimal('0.4500'), contracts=Decimal('20.00'), expiry=datetime.datetime(2026, 9, 13, 20, 50, tzinfo=datetime.timezone.utc), no_book=False)]
Rejected intent, existing order: [Cancel(order_id=1, reason='signal_rejected')]
Watched deadline=now, expiry=+10s: [('fill_seconds', 11.0, 'contracts', Decimal('10.00'))]
One trade=3, matching delta=-3, queue initially=5: {'fills': ['1.00'], 'remaining_queue': '0.00'}
Week-3 confirmation, only two games: confirmation set: restricted to the cells and contrasts selected in the week-38 freeze; nothing outside that set is evaluated here. 1 selected cell(s) evaluated; 1 whose posterior interval excludes zero (§9.6 needs 3).
```

**Reproduction code**

```python
"""Read-only review probes against HEAD 6eed2d8; no database or network.

Run from the repository root:
  /path/to/sports/.venv/bin/python /path/to/review/reproduce.py --repo /path/to/sports

These print observed behavior, not regression-test assertions of desired behavior.
They intentionally reuse the repository's committed pure fixture helpers.
"""
import argparse
import os
from pathlib import Path
import sys
import runpy

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--repo", type=Path, default=Path("/Users/trey/dev/sports"))
args = parser.parse_args()
repo_path = args.repo.resolve()
os.chdir(repo_path)
sys.path.insert(0, str(repo_path))
from datetime import timedelta
from decimal import Decimal

from harness.execution.book import BookState
from harness.execution.fills import PaperOrder, SimState, TapePrint, simulate_fills
from harness.report.tables import Table
from harness.report.weekly import CELL_KEY, restrict_to_selection

p = runpy.run_path("tests/test_exec_plan.py")
f = runpy.run_path("tests/test_fills.py")
t = p["NOW"]

# A complete subscription stream has A seq1, B seq2, A seq3.
b = BookState.from_levels("A", [["0.30", "5"]], [["0.60", "5"]],
                          sid=7, seq=1, as_of=t, source="ws", anchor_id=1)
b.apply_delta("yes", Decimal(".30"), Decimal("1"), seq=3,
              ts=t + timedelta(seconds=1), event_id=3)
print("Complete multiplexed stream [1,2,3], A sees [1,3]:", {"dirty": b.dirty})

rejected = p["intent"](decision="rejected")
print("Rejected intent, no order:", p["plan"](intents=[rejected]))
print("Rejected intent, existing order:",
      p["plan"](intents=[rejected], orders=[p["order"]()]))

order = PaperOrder(order_id=1, ticker="K1", side="yes", prob=Decimal(".45"),
                   contracts=Decimal("10"), placed_at=t,
                   expiry=t + timedelta(seconds=10), queue_ahead_at_place=Decimal("0"))
trade = TapePrint(trade_id="after-expiry", ts=t + timedelta(seconds=11),
                  yes_price=Decimal(".45"), count=Decimal("10"), taker_side="no", source="ws")
result = simulate_fills(order, SimState.initial(order), None, [trade], [],
                        t + timedelta(seconds=15), "queue_model")
print("Watched deadline=now, expiry=+10s:",
      [("fill_seconds", (x.filled_at-t).total_seconds(), "contracts", x.contracts)
       for x in result.fills])

result = f["run"](f["order"](queue="5"), prints=[f["tprint"](1, ".30", "3")],
                  deltas=[f["tdelta"](1, "yes", ".30", "-3")])
print("One trade=3, matching delta=-3, queue initially=5:",
      {"fills": [str(x.contracts) for x in result.fills],
       "remaining_queue": str(result.state.queue_remaining)})

key = ["direct", "35-50", "3-24 h", "nfl", "moneyline"]
table = Table("probe", "probe", [*CELL_KEY, "posterior", "bh"],
              [[*key, (0.02, 100, 2, 0.01, 0.03), "grey"]])
selection = {"cells": [dict(zip(CELL_KEY, key))], "contrasts": []}
print("Week-3 confirmation, only two games:",
      restrict_to_selection({"t4": table}, selection)["t4"].note)
```

---

**Frozen source excerpts**

Line numbers are from the reviewed commit. The full cited files accompany this document.

**harness/recorder/ws_sink.py:84–100**

```text
84:     def _check_seq(self, sid: int, seq: int, ticker: str, ts: datetime) -> None:
85:         """`seq` counts per subscription, and one `sid` carries up to 500 tickers, so a gap
86:         invalidates every ticker on that sid -- not just the one whose message exposed it.
87:         The row therefore goes in under `ticker = ""` (the whole-subscription sentinel) with
88:         the exposing ticker kept in `raw` (null, never "", when the message carried no
89:         ticker at all, so a malformed frame is not read as the sentinel). A first message on
90:         an unseen sid has nothing to follow, so it records its seq and writes no gap."""
91:         last = self._last_seq.get(sid)
92:         if last is not None and seq != last + 1:
93:             log.warning("seq gap sid=%s expected=%s got=%s exposed_by=%s", sid, last + 1, seq, ticker)
94:             self._session.add(OrderbookEvent(ticker="", ts=ts, sid=sid, seq=seq, kind="gap",
95:                                              raw={"sid": sid, "expected": last + 1, "got": seq, "exposed_by": ticker or None}))
96:             self._pending += 1
97:             self._pending_sids.add(sid)
98:             self.gap_sids.add(sid)
99:             self._gaps_since += 1
100:         self._last_seq[sid] = seq
```

**harness/execution/book.py:54–60**

```text
54: 
55: # The live delta scan (`ix_obe_ticker_id`): `id` order, lower `ts` bound only.
56: _DELTAS_BY_ID = text(
57:     "select id, side, price, delta, seq, ts from orderbook_events "
58:     "where ticker = :t and kind = 'delta' and id > :cursor and ts >= :lower order by id"
59: )
60: # The past-instant scan (`ix_obe_ticker_ts`): the *same* delta set the live path takes --
```

**harness/execution/book.py:263–278**

```text
263:     def apply_delta(self, side: str, price: Decimal, delta: Decimal, seq: int | None,
264:                     ts: datetime, event_id: int) -> None:
265:         """Fold one `orderbook_delta` row in, in place.
266: 
267:         `seq` out of step with the anchor means the tape lost a frame and the ladders can
268:         no longer be trusted, so the book goes dirty and stays dirty until it is re-anchored.
269:         `seq = None` skips the check, which is how a REST anchor takes deltas: it has no
270:         sequence of its own to continue.
271:         """
272:         if seq is not None:
273:             if int(seq) != self.seq + 1:
274:                 self.dirty = True
275:             self.seq = int(seq)
276:         book = self._book(side)
277:         key = _price(price)
278:         size = book.get(key, ZERO) + _qty(delta)
```

**harness/execution/loop.py:794–806**

```text
794:     def _simulate_order(self, session: Session, row, markets, bases, recovering, tape, lagging,
795:                         now: datetime, stats: ExecStats) -> tuple[str, Decimal]:
796:         s = self.exec_settings
797:         market = markets.get(row.venue_market_id)
798:         book = self.books.get(row.ticker)
799:         if market is not None and market.dirty(now, s):
800:             # A book we cannot read tells us nothing about the queue, so neither track advances
801:             # and the order records how long it spent in that state (D6).
802:             store.add_dirty_seconds(session, row.id, self.settings.exec_period_s)
803:             self._close_nw_if_expired(session, row, now)
804:             return row.status, row.filled_contracts
805: 
806:         watched = _state_of(row, "")
```

**harness/execution/loop.py:848–872**

```text
848: 
849:         if row.status in store.OPEN_STATUSES:
850:             result = simulate_fills(order, watched, self._sim_book(session, row, watched, bases,
851:                                                                    anchor),
852:                                     prints, deltas, now, QUEUE_MODEL)
853:             track = self._persist_track(session, row, order, result, prints, ledger=True,
854:                                         crossed_already=crossed_already)
855:             crossed_already = crossed_already or track.cross_written
856:             stats.fills += track.inserted
857:             filled = track.filled
858:             status = _next_status(row.status, filled, row.contracts)
859:             updates.update(filled_contracts=filled, status=status,
860:                            **_state_columns("", track.state))
861:             if track.crossed:
862:                 updates["worst_case_fill"] = True
863:         else:
864:             filled, status = row.filled_contracts, row.status
865: 
866:         if not row.nw_done:
867:             deadline = min(now, row.expiry) if row.expiry is not None else now
868:             result = simulate_fills(order, no_watcher,
869:                                     self._sim_book(session, row, no_watcher, bases, anchor),
870:                                     prints, deltas, deadline, NO_WATCHER)
871:             track = self._persist_track(session, row, order, result, prints, ledger=False,
872:                                         crossed_already=crossed_already)
```

**harness/recorder/cadence.py:16–33**

```text
16: def interval_for(sport: str, now: datetime, kickoffs: list[Kickoff], tz: str) -> int | None:
17:     loc = _local(now, tz)
18:     mine = [x for x in kickoffs if x.sport == sport]
19:     # A game of this sport is on the field: kickoff through kickoff + 4h.
20:     in_progress = any(timedelta(0) <= (now - x.kickoff_utc) <= timedelta(hours=4) for x in mine)
21:     if 1 <= loc.hour < 8 and not in_progress:
22:         return None
23:     if sport == "nfl":
24:         for x in mine:
25:             delta = x.kickoff_utc - now
26:             if timedelta(minutes=60) <= delta <= timedelta(minutes=100):
27:                 return 20
28:     today = [x.kickoff_utc for x in mine if _local(x.kickoff_utc, tz).date() == loc.date()]
29:     if in_progress or (today and (min(today) - timedelta(hours=3)) <= now <= max(today)):
30:         return 120
31:     if loc.weekday() >= 5:
32:         return 300
33:     return 900
```

**harness/pricing/fair.py:22–39**

```text
22: #: Featured-market lines refresh every tick; the allowance is that cadence (a fixed 120s,
23: #: independent of the actual tick interval) plus the tick's own compute budget (spec F11).
24: FEATURED_CADENCE_S = 120
25: #: Below this many minutes to kickoff, alternates are on the "near" cadence; at or beyond it,
26: #: the (slower) "far" cadence applies.
27: ALT_NEAR_CUTOFF_MIN = 180
28: 
29: 
30: def stale_allowance_s(feed: str, ttk_minutes: float | None, s: Settings) -> int:
31:     """How old a fair value keyed to `feed` may be before `not_stale` rejects it, given how
32:     far out the game is. Independent of `Settings.stale_s` -- `not_stale` takes the looser
33:     of the two (spec F11)."""
34:     if feed == "featured":
35:         return FEATURED_CADENCE_S + s.tick_budget_s
36:     interval = (
37:         s.odds_alt_interval_near_s if ttk_minutes is not None and ttk_minutes <= ALT_NEAR_CUTOFF_MIN
38:         else s.odds_alt_interval_far_s
39:     )
```

**harness/strategy/pipeline.py:153–191**

```text
153:     deadline = time.monotonic() + budget_s
154: 
155:     def ok() -> bool:
156:         return time.monotonic() < deadline
157: 
158:     result = {
159:         "fair_direct": 0,
160:         "fair_derived": 0,
161:         "no_sharp": 0,
162:         "fair_errors": 0,
163:         "gaps": 0,
164:         "signals": {},
165:         "budget_exhausted": False,
166:         "variants_run": [],
167:         "variants_skipped": [],
168:         "variant_ms": {},
169:         "order": [],
170:         "gate_variant_missing": False,
171:         "gate_variant_id": None,
172:     }
173: 
174:     # Stage 1 always runs, even with no budget left, so fair values keep advancing every tick.
175:     fair_counts = compute_fair_values(session, run_id, now, settings)
176:     result["fair_direct"] = fair_counts.direct
177:     result["fair_derived"] = fair_counts.derived
178:     result["no_sharp"] = fair_counts.no_sharp
179:     result["fair_errors"] = fair_counts.errors
180: 
181:     if not ok():
182:         result["budget_exhausted"] = True
183:         return result
184: 
185:     result["gaps"] = build_gap_snapshots(
186:         session, run_id, now, tz=settings.tz_local, errored_game_ids=fair_counts.errored_game_ids
187:     )
188: 
189:     if not ok():
190:         result["budget_exhausted"] = True
191:         return result
```

**harness/execution/fills.py:39–51**

```text
39: from decimal import ROUND_HALF_UP, Decimal
40: 
41: from harness.execution.book import FOUR, QTY, SIDES, ZERO, BookState, opp, side_p
42: from harness.pricing.fees import KALSHI_FOOTBALL, FeeModel, fee_for_order
43: 
44: #: Deltas are folded in before prints at the same instant. The venue publishes the book
45: #: change and the trade for one event with one timestamp; taking the print first would let
46: #: it consume queue that the delta is about to report as already traded.
47: _DELTA, _PRINT = 0, 1
48: 
49: CROSS = "snapshot_cross"
50: 
51: 
```

**harness/execution/fills.py:307–321**

```text
307: def _apply_queue_delta(order: PaperOrder, state: SimState, delta: TapeDelta) -> None:
308:     """Fold one delta into the queue: trades first, then genuine cancels.
309: 
310:     Only a shrinking level at our own price on our own side can move us up. A positive delta
311:     is a late joiner, who sits behind us. A negative one is the venue reporting the level's
312:     net change, which already includes the trades the prints told us about, so it is charged
313:     against `traded_at_price` first and only what is left is a cancel.
314:     """
315:     if delta.side != order.side or delta.price != order.prob or delta.delta >= ZERO:
316:         return
317:     size = -delta.delta
318:     cancels = max(ZERO, size - state.traded_at_price)
319:     state.traded_at_price = max(ZERO, state.traded_at_price - size)
320:     state.queue_remaining = max(ZERO, state.queue_remaining - cancels)
321: 
```

**tests/test_fills.py:191–198**

```text
191: def test_prints_after_deltas_at_the_same_timestamp():
192:     o = order(queue="5")
193:     # If the print were taken first it would consume 3 of the queue and the -3 delta would
194:     # then be attributed to those trades, leaving the queue at 2. Deltas go first, so the -3
195:     # is a pure cancel, the queue drops to 2 and the print fills 1 of its 3.
196:     res = run(o, prints=[tprint(1, "0.30", "3")], deltas=[tdelta(1, "yes", "0.30", "-3")])
197:     assert [f.contracts for f in res.fills] == [Decimal("1.00")]
198:     assert res.state.queue_remaining == Decimal("0.00")
```

**harness/execution/plan.py:499–515**

```text
499:     if market.dirty(now, s):
500:         return None
501:     if _fair_stale(market, cfg, now):
502:         return Cancel(order.order_id, FAIR_STALE)
503:     if _venue_moved(order, market, s):
504:         return Cancel(order.order_id, VENUE_MOVE)
505:     # An order placed without a recorded edge floor or adverse-selection seed cannot have its
506:     # edge repriced, so the rule holds rather than guessing at the threshold it decayed past.
507:     edge = _edge_now(order, market)
508:     if edge is not None and order.edge_min_at_place is not None:
509:         if edge < order.edge_min_at_place / 2:
510:             return Cancel(order.order_id, EDGE_DECAY)
511:     if intent is not None and intent.latest_decision == REJECTED:
512:         return Cancel(order.order_id, SIGNAL_REJECTED)
513:     if (intent is not None and intent.target_prob is not None
514:             and abs(intent.target_prob - order.prob) >= s.reprice_fair_move_pts):
515:         return Cancel(order.order_id, REPRICE)
```

**harness/execution/plan.py:519–555**

```text
519: def _intent_actions(intent: IntentView, market: MarketNow | None, cfg: dict,
520:                     state: StrategyState, kill_active: bool, now: datetime, s: ExecSettings,
521:                     has_capacity: bool) -> list[Action]:
522:     """The intent chain. Returns the actions for this intent, at most one of them a `Place`."""
523:     if kill_active:
524:         return [Skip(intent.intent_id, KILL_SWITCH)]
525:     # No kickoff is no expiry, and R8's expiry is the only guarantee an order stops resting.
526:     if intent.kickoff_utc is None or intent.kickoff_utc - now < s.cutoff:
527:         return [Skip(intent.intent_id, KICKOFF)]
528:     if market is None or not market.matched:
529:         return [Skip(intent.intent_id, UNMATCHED)]
530:     if _fair_stale(market, cfg, now):
531:         return [Skip(intent.intent_id, FAIR_STALE)]
532:     if market.dirty(now, s):
533:         return [Skip(intent.intent_id, BOOK_DIRTY)]
534:     # The first rule that needs the target: everything above it holds for an intent with no
535:     # price or size too, and this is where such an intent stops. `Place` still refuses a null
536:     # target, but as a backstop rather than as the way the loop finds out.
537:     if intent.target_prob is None or intent.target_contracts is None:
538:         return [Skip(intent.intent_id, NO_TARGET)]
539:     ask = market.best_ask(intent.side)
540:     if ask is not None and intent.target_prob >= ask:
541:         # A live post-only order at or through the ask is rejected by the venue (F38).
542:         return [Skip(intent.intent_id, POST_ONLY_REJECT)]
543: 
544:     out: list[Action] = []
545:     label = first_false_cap(cap_labels(intent, cfg, state))
546:     if label is not None:
547:         blocking = bool(cfg["apply_caps"])
548:         out.append(CapGate(intent.intent_id, label, blocking))
549:         if blocking:
550:             return out
551:     if not has_capacity:
552:         out.append(Skip(intent.intent_id, EXEC_CAPACITY))
553:         return out
554:     out.append(Place(intent.intent_id, intent.side, intent.target_prob, intent.target_contracts,
555:                      intent.kickoff_utc - s.cutoff, market.book is None))
```

**harness/report/weekly.py:193–207**

```text
193:     wanted_cells = {tuple(entry.get(name) for name in CELL_KEY)
194:                     for entry in selection.get("cells", [])}
195:     wanted_contrasts = {tuple(entry.get(name) for name in CONTRAST_KEY)
196:                         for entry in selection.get("contrasts", [])}
197: 
198:     t4 = tables.get("t4")
199:     if t4 is not None:
200:         index = [t4.columns.index(name) for name in CELL_KEY]
201:         rows = [row for row in t4.rows if tuple(row[i] for i in index) in wanted_cells]
202:         posterior = t4.columns.index("posterior")
203:         confirmed = sum(1 for row in rows if cell_excludes_zero(row[posterior]))
204:         note = (f"{CONFIRMATION_NOTE} {len(rows)} selected cell(s) evaluated; {confirmed} whose "
205:                 f"posterior interval excludes zero (§9.6 needs {SIGNIFICANT_CELLS_REQUIRED}).")
206:         out["t4"] = with_rows(t4, rows, note)
207: 
```

**harness/report/tables.py:808–813**

```text
808:     significant = sum(1 for key in grid
809:                       if cis[key].n_clusters >= GREY_CLUSTERS and cell_excludes_zero(posterior[key]))
810:     note = "; ".join([*family_notes,
811:                       f"cells whose posterior interval excludes zero: {significant} "
812:                       f"(§9.6 needs {SIGNIFICANT_CELLS_REQUIRED})", *notes])
813:     return Table("Table 4 (t4): mispricing map", header, _T4_COLUMNS, rows, note)
```

**harness/dashboard/snapshots/floor.py:264–278**

```text
264: _FAIR_FOR_ORDERS = text("""
265:     select m.id as venue_market_id, m.fee_type, m.fee_multiplier,
266:            f.fair_p, f.staleness_s, f.created_at
267:     from venue_markets m
268:     join lateral (
269:         select fair_p, staleness_s, created_at from fair_values f
270:         where f.game_id = m.game_id and f.market_type = m.market_type
271:           and f.created_at >= :since
272:         order by f.created_at desc limit 1
273:     ) f on true
274:     where m.id = any(:market_ids)
275: """)
276: 
277: #: Spec §2.2 layout (4): the fill, whether the print traded *through* our price, the tape it
278: #: came off, and the game it was on -- a ticker is not a game name to a phone reader.
```

**docs/superpowers/autopilot/journal.md:1202–1205**

```text
1202: ## 93. hotfix - root cause of the deploy-night storm: unsummarized BRIN ranges (fix 32) - 2026-09-10 06:14-06:24 CT
1203: - Diagnosis (quiet hour, 06:14-06:20 CT; stamps in this entry were first written eight minutes ahead of the clock and corrected): one `/api/summary` load traced through `pg_stat_activity` spent about 24 of its 42 s in `select count(*) from orderbook_events where ts >= now() - interval '5 minutes'`; the plan prunes the legacy partition and uses the week-37 BRIN, yet the count took **39.6 s**. `brin_summarize_new_values('orderbook_events_y2026w37_ts_idx')` summarized **1,078 ranges** (5.5 s); the same count then took **2.8 ms**. `fair_values`'s 24 h feed-lag check: 25.7 s before, 48 ranges summarized, 141 ms after. No BRIN on the NAS had `autosummarize` set. Evidence `evidence/2026-09-10-brin-0620.txt`.
1204: - Mechanism: a BRIN bitmap scan returns every unsummarized block range as a match, so every read bounded on a BRIN column walked every page inserted since the table's last vacuum: gigabytes on a game-night partition. This explains the legacy page at 35-50 s, the three check skips, the WS sink's statement timeouts (40 disconnects in 7 h), and a large share of the snapshot builders' 12 s ticks. Fix 19 measured well on 2026-09-08 because the partitions were fresh and summarized.
1205: - Applied on the NAS (operational, reversible, additive): `alter index ... set (autosummarize = on)` on all nine BRINs (both `orderbook_events` partitions and the legacy one, both `venue_trades` partitions and legacy, both `raw_responses` partitions, `ix_fair_created_brin`); tails summarized by hand (venue_trades w37 17 ranges, legacy trades 20, legacy orderbook 87, raw w37 15). Ruling: an index storage parameter is additive DDL, not a gated ALTER TYPE/DROP/RENAME - cost if wrong: a vacuum worker summarizes a few ranges per insert batch.
```

**docs/superpowers/autopilot/journal.md:1261–1265**

```text
1261: - **Executor loop FAIL**: `exec.loop_ms` in the window (30 min): 22 loops, avg 33.3 s, p95 155 s, max 163 s; pre-kick hour (18:35-19:35): 44 loops, avg 26.3 s, p95 103 s. By hour today: 07:00 avg 3.9 s p95 4.8 s; 10:00-13:00 avg 5.1-5.8 s, p95 7.8-9.8 s; 14:00-15:00 avg 6.2-6.4 s, p95 10.5-12.7 s; 16:00 avg 9.5 s p95 23.5 s; 18:00 avg 18.3 s p95 101 s; 19:00 avg 27.5 s p95 118 s. Heartbeat 20:07:12: loops 14,638, open_orders 145, last_loop_ms 8,680, p95_loop_ms 42,128, loops_skipped 354, no error. The executor's own work is flat: `exec.dirty_markets` 107-117 every hour, `exec.intents_considered` 500-1,260, `exec.open_orders` 53-144. Same work, five to six times the wall clock.
1262: - **NAS memory FAIL** (the cause): `free -m` total 7,717, used 6,660, free 312, available 1,056, swap 5,021 of 9,999 used; `vmstat 5 3` swap-in/out 382/1822 and 1016/250 per 5 s, IO wait 25-32 %, blocked procs 5-6; `/proc/pressure/memory` full avg60 14.6 %, avg300 16.0 % (the box stalls one sixth of the time on memory). Containers by `docker stats`: postgres 1.0 GiB, app-run 247 MiB, app-exec 202 MiB, app-ws 71 MiB, app-serve 45 MiB (the stack about 1.6 GiB); the media stack (dispatcharr 278, sonarr 163, radarr 162, jellyfin 129, ersatztv 109, prowlarr 92, others) about 1.3 GiB; the rest is the UGREEN OS. Postgres: shared_buffers 512 MB, work_mem 16 MB, effective_cache_size 1.5 GB, database 40 GB. `pg_locks` ungranted 0; no query over 5 s at read time.
1263: - **Snapshot self-guard FAIL**: `snapshot_disabled` at 17:55:31 CT: "snapshot builder study disabled after [2867, 2908, 3152] ms builds, all over 2500 ms" (app-serve log 22:55:31Z). Study is paused until an `app-serve` restart; per verify.md this is a carried fix (34), and a restart with nothing changed trips it again while the box swaps.
1264: - Other events (3 h): `budget_exhausted` x6 (markouts and report_wtd stages at 18:13 and 19:13, the hourly settlement job running out of budget on the slow box), `ws_disconnect` 33 / `ws_connect` 24 (WebSocket churn, last reconnect 20:00:08), executor "tape lag: 1 ticker behind" every 15 s on KXNCAAFTOTAL-26SEP12ASUTXAM-51.
1265: - Ruling: no scheduler switch (builders under budget); no deploy (game window, and nothing to deploy). Not a starvation incident of the phase 4 kind (no lock, no query storm): the box's working set exceeds its RAM and the executor is paying in page faults. Mac mini trigger per the roadmap's User-side TODO: **met** ("sustained swap traffic outside deploys": 5 GB of swap resident and continuous swap-in/out at 20:10 CT, 13 h after the last deploy). The loop flags it and does not move; the user decides. Until then the phase 5 deploy adds one container (`app-research`, idle outside its sweeps) and one hourly Anthropic call: small, but it lands on a box already over its RAM, so the phase 5 deploy waits for the user's word on the host as well as for the game window.
```

**docs/superpowers/autopilot/journal.md:1302–1306**

```text
1302: - Executor: `exec.loop_ms` last 30 min: 26 loops, avg 7.3 s, p95 12.2 s, max 15.2 s, min 4.7 s (19:00 hour: avg 27.5 s, p95 118 s). By half hour since the media stop: 20:00 avg 57 s p95 256 s (swap-back); 20:30 avg 15.0 s p95 56 s; 21:00 avg 24.1 s p95 64 s; 21:30 avg 9.0 s p95 11.5 s; 22:00 avg 8.1 s p95 13.2 s; 22:30 avg 7.7 s p95 15.2 s; 23:00 avg 7.1 s p95 9.1 s. Heartbeat 23:17:12: loops 15,315, open_orders 126, last 8.9 s, p95 9.1 s, loops_skipped 424 (354 at 20:07: +70 over 3 h 10 min, mostly the swap-back). No tape timeouts in the last hour. **Still over the 7.5 s p95 ceiling** (9.1-15.2 s p95 across the evening half hours) but recovered fourfold; the 21:00 half hour's second hump (avg 24 s) coincided with the hourly settlement job at 21:13 (`budget_exhausted` x4 in 3 h, last 22:13).
1303: - NAS memory: `free -m` used 5,281, free 323, available 2,435; swap resident 2,253 MB (stable since 20:30); `vmstat 5 3` swap-out 0, swap-in 139-781 pages per 5 s (occasional faults, not thrashing), IO wait 16-20 %; `/proc/pressure/memory` full avg300 0.89 % (16.0 % at 20:07). The box is no longer memory-bound; IO wait is now the residual.
1304: - Builders 30 min: gate p95 1 ms, ticket p95 5 ms (snapshots served from warm cache); no `snapshot_disabled` in 3 h; the study builder stays paused until the app-serve restart at the deploy (fix 34).
1305: - WebSocket: `ws_disconnect` 50 / `ws_connect` 21 in 3 h (last 23:02): churn continues and is a plan-next input (was 33/24 at 20:07).
1306: - Verdict: PASS with the executor ceiling row still red (a carried observation for plan-next: on a game night with 126-150 resting orders the loop needs its own budget, and the NAS's IO wait is the next limit after memory). Deploy at 23:41 CT per journal 106.
```
