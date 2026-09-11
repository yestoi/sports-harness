# Review: sportsbook harness at the phase 5 checkpoint

Eight review lenses, 2026-09-11. Numbers from `evidence.md` (NAS, 07:27-07:33 CT) and the repo at `6eed2d8`.

## 1. Verdict

1. **Quality:** engineering B-, process C+. The core (executor, ledger, settlement, migrations, 1,583 DB-backed tests) is sound. Boundaries and cross-subsystem arithmetic are not: 20 of 21 carried fixes 21-41 were first found on the NAS.
2. **One open position:** not the strategy working. 7,825 of 7,997 orders (97.8 %) died on `fair_stale`, a rule whose 220 s bound never matched the pricing cadence it bounds. Fix it and you get measurement, not profit: even never cancelling, only 27 of 445 posts would have filled in 3 days.
3. **NAS sacrifice:** yes, narrowly. Measurement cadence and coverage were traded, not the tape or the ledger. Most "NAS fixes" are correct engineering you should keep on any host.
4. **Mac mini:** not proven today, and today is the wrong day. The box is no longer memory-bound; the residual is a 47 GB database on a single rotational disk. Measure tonight's window, then move Tue 2026-09-15.
5. **Phase 6:** as written it is mostly busywork. Nothing in the seven items touches the fill model or the cancel policy, which is where the damage is.

---

## 2. Is one open position the system working?

### The funnel

| Stage | Count |
|---|---|
| Intents | 88,107 over 135 markets, 58 games |
| Orders placed | 7,997 (1,258 / 2,615 / 3,836 per day) |
| Distinct (variant, market, side) keys | 445, so ~18 orders per key |
| Cancelled `fair_stale` | 7,825 (97.8 %), median 12.2 min in book |
| `queue_model` fills | 2 rows, 38.92 contracts, on **one** order (157) |
| Ledger | 2 rows, -17.68, drawdown -0.59 % |

Source: brief and evidence G. No fill has landed since 2026-09-08 10:00 CT.

### The mechanism

`plan.py:446-456` cancels when `now - fair_values.created_at > max(stale_s 180, stale_allowance_s 220)`. The 220 s is `FEATURED_CADENCE_S` 120 plus `tick_budget_s` 100 (`fair.py:24,30-40`). But `created_at` advances only when a tick reprices, and that runs on `interval_for` (`cadence.py:16-33`): 900 s weekday, 300 s weekend, 120 s only inside a game window, nothing 01:00-08:00 CT.

Weekday daytime, the fair is older than 220 s for roughly 680 of every 900 s, so placement is skipped and clean-book orders die. Evidence D4: on 09-10, 09:00-13:00 CT, placed equals cancelled every hour (149/149, 134/134, 157/157, 276/276). 98.3 % of orders sit more than 24 h from kickoff (the brief's "7,388 / 92 %" is a transcription error; the buckets sum to 7,862). Inside a game window fair values refresh every 1-3.5 min (evidence F), so only a late refresh trips the bound: time in book by day was 4.8, 12.2, 15.5 min.

A second rule stretches the tail. `plan.py:499` holds an order on a dirty book **before** checking staleness, and a market reads dirty when its own tape is quiet for 120 s, normal on days-out markets (`exec.dirty_markets` 122.8 of 125). Stale orders sit invisibly until the book wakes; p95 time in book is 147 min.

Both regimes contribute: the daytime block explains the volume, the dirty hold the lifetimes. Neither is a coding error. `fair_stale` is coded exactly as the phase 3 spec and ruling F36 specify. The defect is that F11's allowance was written for feed staleness and F36 reused it against pricing-run age.

### The counterfactual, honestly

The never-cancel track shows 646 fill rows / 18,512 contracts on 224 orders. That is **8x inflated**: every re-placement on a key runs its own track against the same prints (`loop.py:866-884`), and `order_episodes` collapses only `reprice` chains (129 of 7,997 cancels). The honest figure is **27 of 445 keys filled in 3 days**, about 9 per day pooled across three variants. Never cancelling multiplies fills by ~27 and still leaves you short.

### What the gate needs

`gate.py:119-125`, cumulative over the run: >= 150 orders with a `queue_model` fill across >= 40 games, both sports, >= 80 % with `book_source='ws'` and `dirty_minutes = 0`. The gate variant on the NAS is `sharp_two_sided` (`deploy/nas.env:21`; the code default is `sharp_direct`). Either way: 0 or 1 against 150.

Two blockers, and the second is worse. **Volume:** ~9 fills/day pooled against ~9/day needed for the gate variant alone by 09-28; the spec puts the review at "realistically mid-October" and the adversarial review already called 150 "probably unreachable in three weeks". **The clean-share test cannot be met as coded:** `dirty_seconds` accrues 15 s per loop for the order's whole *working* life, including the post-cancel no_watcher track running to kickoff (`store.py:324-349`, `loop.py:799-804`); order 157 carries 3,020 dirty minutes of a ~3,180-minute window, and with 98 % of orders placed a day or more out, `dirty_minutes = 0` is nearly impossible. Criterion 1 fails at **any** fill count until this is amended.

`GateResult.passed` can never be true in phase 3 by construction (`gate.py:12-16`). Monday's gate is a diagnostic.

### The honest call

Even without the churn, fills are rare and adverse. `sharp_direct` posts 4.56c under the venue mid behind 8,524 resting contracts against a 110-contract order, ~78x its own size, so fills come from level pulls and sweeps rather than flow; order 157 filled only after its 6,401-contract queue vanished. That is hypothesis H1, pre-registered, and the README already names "a clear, well-documented no" as the likely outcome.

The split that matters: the section 9.6 deliverables (mispricing map, convergence lag, adverse selection anchored on `nw_fill`) need no watched fills and remain answerable. The go-live gate does, and it is not on pace.

### Levers

| # | Lever | Invariants | Effect |
|---|---|---|---|
| L3 | Executor-only `fair_stale_s` (600-900 s) in ExecSettings | none | smallest diff; saves daytime cancels, not overnight |
| L2 | Hold while a live intent still targets the resting price | none | keeps F36's safety at a 15-min horizon |
| L1 | Rest to expiry (delete the cancel) | none | ceiling is the 27-key counterfactual |
| L4 | Per-variant capacity instead of one shared 150 | none | **required** alongside L1/L2/L3 |
| L5 | Order the capacity queue by fillability, not edge | none | better sample, same count |
| L6 | New ids (join-the-bid, near-kickoff) | **invariant 1: user gate** | the only real fill-producing arm |

L1-L5 touch no variant file and no gate definition. Each carries a dated Amendment 5 with the four Amendment-4 elements: deploy sha and time, pre-amendment order range, tables and criteria touched, replay re-score command.

L4 is load-bearing. `exec_max_open_orders = 150` is shared across all three executed variants with no eviction, and it hit 150 in **36 of 53 hours** with open orders (recount from evidence D3; the note's "41" miscounts its own table). The churn currently recycles those slots across 445 keys. Stop the churn without splitting the cap and the first 150 far-out orders hold every slot until kickoff.

---

## 3. Quality of the work

**Engineering, B-.** Sound where it counts: idempotent writes, an advisory lock on the loop, savepoint containment per order and per game, five additive Alembic revisions with a catalogue-diff test, 1,583 of 2,926 tests against a real Postgres, 107 reasoned `except Exception` sites, idempotent settlement. The no_watcher track existing at all is why this review can answer question 2. Fragile in three places in the fill model:

| Defect | Status | Impact |
|---|---|---|
| Gap recovery (`loop.py:828-836`) clamps the queue and jumps the cursor but never moves the print watermark, which `loop.py:819-824` does, so prints already in the recovery book are replayed against it | Strong inference. Order 157's state (three at-price prints totalling 63.92 against a queue of 25, no delta offset) fits nothing else | The only money fill, the only open position and the whole drawdown are probably an artifact |
| Deltas sort before prints at equal ts (`fills.py:44-47`), so the trade's own book change is charged as a cancel and the print consumes the queue again | Confirmed. All 196 fixture prints share the ms with their delta; `test_fills.py:191-201` pins the wrong answer | Optimistic by up to the largest at-price print per order. Every replay inherits it |
| The watched track simulates to `now`, not `min(now, expiry)` (`loop.py:852` vs `:867`), while `plan.py:483-492` withholds Expire on a lagging ticker | Latent; 0 occurrences, 4 orders have ever reached expiry | Post-kickoff prints could fill a pre-game order into the ledger. First exposure is this weekend |

Also: `report_wtd` has not rendered in 26 h; three integrity checks skip on their 2 s timeout daily; `intents_without_order_or_skip` fails at 483; the gate reads no job state, so a starved stage looks like "no data".

**Process, C+.** Construction discipline is strong: 115 "cost if wrong" rulings, every hand action journaled with a sha, opus final reviews catching 7 Criticals pre-merge including `veto.py:225-227`, which would have made H9 measure nothing. Operating discipline is weak: `state.md`'s header has read "Updated: 2026-09-10 14:10 CT" across 21 rewrites and names three different NAS builds; no preflight output since 09-09 08:29 despite six deploys; and two invariant-5 slips (fix 38's `rfqs` DELETE on the loop's own amendment, journal 112's hand DELETE of four `job_state` rows). Neither destroyed data. Both broke a rule the project wrote for itself.

**The escape pattern.** Of carried fixes 21-41, 20 were found on the NAS and 1 by a later task review after its code was live.

| Cause class | Count | Would a pre-merge gate catch it? |
|---|---|---|
| External API shape or behaviour assumed, not recorded | 8 | Partly. A proving call through the **production** request path covers ~4. |
| NAS performance or database size | 7 | Yes. EXPLAIN ANALYZE on the restore drill's copy covers most. |
| Deploy plumbing (the DDL lock race three times: 13, 21, 37) | 2 | Yes, by fixing the root cause the first time. |
| Logic and scope | 4 | Reviews already catch this class. |

SDD catches what it can read and is blind to the environment. Honest coverage of the two gates is 11-15 of 21. The counter-example matters: phase 5 *did* record a live Anthropic proving call and fix 39 still escaped, because that call went through the SDK's `parse` helper while production sends raw dicts. A fixture proves only the path it was captured through.

---

## 4. Did we sacrifice quality for the NAS?

Yes, in measurement cadence and coverage. Not in the recorded tape or the paper ledger.

| Class | Change | Evidence | Cost |
|---|---|---|---|
| **A. Right on any host** | Fixes 16/17/19/25 bounded indexed reads, 22 delta read by (ticker, id), 26 adaptive batch, 31 bounded builders, 32 BRIN autosummarize, 35 cheap RFQ declines, 36 annotator from stored cells, 38/40 RFQ boundary filter, per-engine timeouts | roadmap 337-361; journal 93 (39.6 s to 2.8 ms) | none; keep all of it |
| **B. Measurable, labelled** | Pricing budget exhausted on 109 of 243 priced ticks 09-10 (45 %), dropping rotating secondaries | evidence E | family-C contrast coverage; gate variant and primary always score |
| **B** | Loop p95 over the 7.5 s ceiling on 3 of 4 days; 19:00 CT 09-10 avg 27.5 s, p95 114.8 s | evidence D1/D2 | late decisions; 521 of 17,138 loops skipped |
| **B** | Three integrity checks skip on a 2 s timeout daily; `report_wtd` moved hourly to 6 h and has not run in 26 h | evidence I; `checks.py:25`; `report_wtd.py:41-52` | the tape-dedupe invariant is unverified; Study's WTD cells are stale |
| **C. Ops only** | Dashboard cadences, self-guard, Study caps, quiet-hour verification throttle | fix 31; journal 89/101/109 | no experiment data touched |

A better host removes the loop latency, tape-read timeouts, check skips, budget exhaustion, and the deploy and dump storms. **Code regardless of host:** fix 34, fix 37, `duplicate_trades`' statement and schedule, the `fair_stale` bound, the shared 150 cap, the three fill-model defects, an `intent_expired` skip row, and the `dirty_minutes` gate semantics.

---

## 5. Mac mini: is it clear?

Not today. The lenses split; on the field data the host lens is right about timing and the NAS-audit lens is right about destination.

| Claim | Status |
|---|---|
| Memory-bound Thursday | True then: swap 5,021 MB, memory pressure full 16 % (journal 101) |
| Memory-bound now | False. Since the 20:12 CT media stop: swap-out 0, pressure ~0, 2,983 MB free (journal 107) |
| The residual is disk | True, and new. pgdata is on `sda`, a rotational RAID1 with **one member**, not the NVMe; `random_page_cost` 1.1 is an SSD value; Postgres read 1.89 TB in 3 days against a 3.9 GB cache |
| The roadmap trigger is met | Partly. `roadmap.md:315` wants two consecutive game windows over 7.5 s with fixes 31-32; Thursday is window one, and the swap clause that fired has had its cause removed |
| A game window ever passed the row | No. 09-09 SEA-NE p95 7,956 ms; 09-10 LAR-SF 114.8 s |
| The mini is adequate | Unknown. Its RAM and SSD are recorded nowhere. The gating fact |

| Option | Cost | Downtime | Risk | Est. p95 |
|---|---|---|---|---|
| Status quo | $0 | none | low | 9-15 s; fails the row |
| NVMe pool for pgdata on the NAS | $150-370 | 30-45 min | low | 4-8 s; also defers the disk cliff |
| +16 GB SO-DIMM (24 GB) | $145-400 | ~15 min | low | 5-9 s |
| Mac mini, full move | external SSD $250-400 | a day plus a tape gap | med-high | 3-5 s **if** >= 16 GB |


Both hybrids are worse: putting the apps on the mini changes nothing (the IO wait is inside Postgres), and putting Postgres on the mini adds LAN round-trips and a wrong-disk statvfs for no extra gain.

**Recommendation.** Do not migrate before tonight's kickoff or over the weekend. The weekend is the first real fill measurement and should not be confounded by a host change, and the migration **cannot use the backup/restore drill**: the nightly dump excludes the five bulk tables, ~36 GB of the 47 (`backups.md:3-5`). It needs a full logical dump and restore, hours, unrehearsed.

A separate clock is running. `/volume1` is 69 % used and `dump.sh:43,131` skips every dump at 71 %. At 9 GB/day that is about 239 GB away, roughly **early October** (corrected from an earlier "09-19 to 09-30"); after the first skip `make deploy-nas` aborts at its backup precheck (`Makefile:65`) and the drill stops. Moving pgdata off `/volume1` fixes that as well as the latency.

**The settling experiment.** Tonight and Saturday, media stopped, build unchanged. At 17:00, 19:00, 21:00 and 23:00 CT capture hourly `exec.loop_ms` p50/p95 with `exec.open_orders`, `vmstat 5 3`, `/proc/pressure/io`, and `pg_stat_database` hit vs read.

| Reading | Conclusion |
|---|---|
| p95 <= 7.5 s every hour with >= 100 open orders | Stay. Buy nothing for latency. |
| p95 > 7.5 s, si/so ~ 0, wa >= 15 % | Disk-bound. The mini or an NVMe pool. |
| si/so > 0 with media stopped | RAM-bound. RAM first. |
| p95 > 7.5 s, wa < 5 %, si/so 0 | The loop's own cost. No host change helps. |

**Cutover, if you move.** Window: Tue 2026-09-15, 08:00-16:00 CT (no football, outside R4, the dump, the Monday archive and the Monday report).

1. D-1: confirm chip, RAM and SSD. Require >= 16 GB, a 2 TB external NVMe, auto-login with FileVault off, `pmset autorestart 1 sleep 0`, and pgdata on a named volume or native Postgres, never the `./pgdata` bind mount. Rehearse the dump timing. Land fix 37 first.
2. T0: stop the four app containers, leave `app-ws` recording, `pg_dump -Fd -j4`, then stop `app-ws` and `postgres` and copy the tape partitions written since.
3. `pg_restore -j8`, verify `max(id)` on orders, fills and orderbook_events against the NAS, repoint `.env.nas` and deploy `app-ws` first, then check `/healthz`, `alembic_version`, `exec_heartbeat`, gap rows and spend caps.
4. Leave the NAS stack stopped, not removed, for 7 days as the rollback; repoint nightly dumps to the SSD and rsync back to `/volume1`.

Seams: `APP_UID=1000/APP_GID=10` versus macOS 501/20, and 19 hardcoded `/volume1` and host references across roadmap, runbooks, Makefile and backup scripts.

---

## 6. Phase 6: what matters

| When | Do |
|---|---|
| **Today, before 17:45 CT** | Answer the `fair_stale` question (a decision, not code; 44 h old). Ship the expiry clamp `loop.py:852` to `min(now, expiry)` plus a test, before ~16:20 CT or not at all. Do not migrate hosts. |
| **Before Mon 09-14 09:00** | Draft Amendment 5 (executor stale bound plus per-variant cap). Run the per-key no_watcher query so the report prints 27/445, not 224. Decide the `dirty_minutes` semantics. Land fix 41. Pre-declare the R14 replay caveat. |
| **Week 2, by 09-21** | Deploy Amendment 5 with L4. Fix the recovery print watermark, re-score by replay, annotate order 157. Swap prints before deltas and invert the test. Move the three checks off the post-dump window. Act on `/volume1`. Fixes 33, 34, 37. |
| **Week 3, conditional** | Item 4's normalizer only if Monday's `tick_coverage` shows a secondary under ~50 %. The archive-and-drop proposal, written, never executed without your yes. |

**Drop or defer:** item 1 (invariant 1, adds cost to an exhausted pricing budget, cannot raise the gate variant's fill count; run it as replay); item 3 (H3 is back-computable from `venue_trades` at zero data cost); item 6 (`derived_without_venue_row_48h` passes at 0, so no failing consumer); the six cosmetic plan-next inputs. Item 5 is mostly done; item 7's trigger has been met since 09-07.

**Missing entirely:** anything touching the fill model or the cancel policy; a cold-read gate before deploy (extend `test_snap_bounds.py`'s bounded-read rule to `checks.py`, `execution/store.py` and `research/`, then add cold p95 timing to the existing restore drill); an environment lens in the review pipeline.

**The Monday risk.** `gate_reports` has 0 rows; Monday is the first evaluation and criterion 1 will read 0 or 1 against 150. That is honest and breaks nothing: `passed` is unreachable by construction, the gate window is the whole run, and R7's week-2 selection comes from table-4 `gap_mid` cells and table-2 Holm contrasts, which need no fills. Week 1 still counts there. The report must say plainly that criterion 1 is out of reach *and* mis-scored on `dirty_minutes`, carry a data-completeness block (`report_wtd` 26 h stale, three checks skipping, 483 intents unexplained), and print `fill_method = 'queue_model'` explicitly, because one journal entry already read 75 counterfactual rows as fills.

---

## 7. Decisions only you can make

1. **The `fair_stale` bound.** Default: ship L3 plus L4 under a dated Amendment 5; neither touches a variant YAML or `gate.py`. Waiting costs ~2,600 orders a day and makes weeks 2-3 repeat week 1.
2. **`dirty_minutes` in criterion 1.** Default: accrue only while the watched track is open, and only for genuine gaps or a dead recorder. Keeping the age semantics means editing the criterion, an invariant-2 decision that moves `criteria_hash`. Waiting mis-scores every gate row from Monday.
3. **The host.** Default: stay through the weekend, run the section-5 experiment, move Tue 09-15 if the mini qualifies. Waiting into early October crosses the dump-skip floor (corrected date): backups stop, full deploys abort.
4. **The mini's specs.** Default: state them first. At 8 GB, do not move; tune the NAS. Waiting makes the cutover unschedulable.
5. **Fix 38's `rfqs` retention DELETE.** Default: approve it explicitly or say no. It shipped on the loop's own amendment, which invariant 5 reserves for you. Nothing is at risk before 2026-10-02; the precedent is the problem.
6. **A fill-producing arm (L6).** Default: score `join_bid` and `near_kick` by replay over week 1's tape, then decide before the week-2 freeze Mon 09-21. Anything registered later is exploratory and unconfirmable in week 3.
7. **The read-scoped Kalshi key (fix 20 / F64).** Default: swap it during the migration, when secrets are recopied anyway. Waiting keeps "nothing can send an order" a software property, not a physical one.

---

## Appendix: findings

Severity as adjudicated across the verification passes. "Verified" means the claim survived; "refuted" means it did not, with the reason.

### Verified

| id | lens | sev | claim | key evidence | status |
|---|---|---|---|---|---|
| V1 | viability | critical | `fair_stale` bounds pricing-run age with a feed-fetch allowance; 97.8 % of cancels | `plan.py:446-456`; `fair.py:24,30-40`; `cadence.py:16-33`; evidence D4/F/G | verified |
| V2 | viability | major | Criterion 1's 80 % clean share unattainable: `dirty_minutes` accrues post-cancel on quiet books | `gate.py:122-125,274-277`; `loop.py:799-804`; order 157 = 3,020 dirty min | verified |
| V3 | viability | major | Gate not on pace; section 9.6 dataset goals still answerable | `gate.py:20-22,66`; `deploy/nas.env:21` | verified |
| V4 | viability | important | no_watcher over-counts ~8x; honest counterfactual is 27 of 445 keys | `loop.py:866-884`; `schema.py:339`; evidence G | verified |
| V5 | viability | minor | `worst_case_fill` on 157 came from the counterfactual track two days later, not model pessimism | fills row 152; `loop.py:883-884` | verified (severity cut) |
| V6 | viability | important | Posting 4.56c under mid behind ~78x own size; fills need level pulls | evidence G per-variant; evidence J | verified |
| V7 | viability | important | Shared 150 cap bound in 36 of 53 hours; will bind harder once churn stops | `settings.py:64`; `plan.py:589-613`; evidence D3 | verified (count corrected from 41) |
| V10 | viability | important | Rule set structurally suppresses its own fills; H1 is the pre-registered expectation | `run.py:308-312`; spec section 9.7; README:180-183 | verified (velocity filter is **not** the binding suppressor) |
| V12 | viability | important | The strategy question was carried three times without the code-level diagnosis | phase 4.5 report:8; phase 5 report:11; journal 82 | verified |
| V13 | phase6 | important | Six levers exist; L1-L5 need no variant file | `plan.py:85-126`, `:479-556`; phase 4 spec section 0.7 | verified |
| FR-1 | freshness | major | Allowance hardcodes 120 s against a 900 s weekday cadence | `fair.py:22-35`; `cadence.py:16-33` | verified (in-window share also matters) |
| FR-2 | freshness | important | In-window pricing gaps exceed 220 s on 2-6 % of runs from tick-duration variance | evidence F (94-99 gaps); `scheduler.py:96-97` | verified (rate revised down) |
| FR-3 | freshness | important | The dirty hold defers the cancel; the only fill exists because of it | `plan.py:499-501`; order 157 watch samples | verified |
| FR-6 | process | important | The report mis-framed a spec inconsistency as a symmetric user choice | phase 4.5 report:8; journal 63 (Amendment 4 precedent) | verified |
| FM-1 | fillmodel | critical | Cancel policy plus queue depth makes queue_model fills near-zero | brief; evidence G; `gate.py:122` | verified |
| FM-2 | fillmodel | major | Order 157's fill is a double count from the gap-recovery branch | `loop.py:828-836` vs `:819-824`; order 157 row state | verified as strong inference; recovery-loop timestamps not pulled |
| FM-3 | fillmodel | important | Watched track simulates to `now`, not expiry, under tape lag | `loop.py:852` vs `:867`; `plan.py:483-492` | verified (latent; 0 occurrences so far) |
| FM-4 | fillmodel | important | Deltas before prints at equal ts double-counts the first at-price print | `fills.py:44-47`; fixture 196/196 same-ms; `test_fills.py:191-201` | verified |
| FM-5 | fillmodel | minor | Late joiners' cancels credited as queue ahead of us | `fills.py:307-320` | verified; magnitude unmeasured |
| FM-6 | fillmodel | important | no_watcher is not "what resting would have yielded" | `schema.py:323-351`; evidence G, D5 | verified |
| FM-7 | fillmodel | minor | The 150 cap is FCFS in retention, edge-ordered only in admission | `plan.py:588-609` | verified (severity cut; supply, not crowding) |
| E1 | engineering | critical | Same as FR-1 plus the dirty-hold ordering | `fair.py:22-35`; `plan.py:499-502` | verified |
| E2 | engineering | minor | Budget exhaustion and check skips invisible at the gate; WTD 26 h stale | `job.py:84-95`; evidence I | verified (severity cut) |
| E3 | engineering | important | Boundaries built by hand failed on first live contact; no write-path fixture | roadmap 341-361; `test_kalshi_writer.py:454` | verified |
| E9 | engineering | minor | Executor-side NAS fixes sound; measurement-side ones traded cadence | roadmap 22/26/31/32/35; `report_wtd.py:41-52` | verified (severity cut) |
| NAS-2 | nas_audit | important | Pricing budget drops secondaries on 45 % of game-day priced ticks | evidence E; `pipeline.py:131-150` | verified |
| NAS-4 | nas_audit | minor | Three integrity checks skip on the 2 s timeout daily | `checks.py:25`; evidence I | verified; fix 16's bounds already shipped, the schedule is the cause |
| NAS-8 | nas_audit | important | Postgres pinned to a 2026-09-07 memory picture; pgdata on rotational | `docker-compose.yml:10-31`; evidence A/B | verified |
| NAS-14 | nas_audit | important | Host-versus-code partition of every workaround | journal 64/68/70/102; evidence A/C | verified |
| H1 | host | major | The move is not proven; one of two windows; residual is disk | evidence D1/D2; journal 101/107; roadmap:315 | verified |
| H2 | host | important | `/volume1` crosses the dump-skip floor ~09-19 to 09-30 | `dump.sh:43,131`; `Makefile:65`; evidence A/C | verified (date range widened) |
| H3 | host | important | pgdata on rotational while two M.2 slots exist | evidence A; vendor docs | verified; price ($156) is stale, ~$330-370 now |
| H4 | host | important | RAM 8 to 24/32 GB in two SO-DIMM slots | evidence A; vendor docs | verified; read-amplification partly from since-fixed defects |
| H6 | host | important | Mini requirements; RAM is the gating unknown | Apple/Docker/OrbStack docs; `docker-compose.yml:9` | verified |
| H7 | host | important | The migration cannot use the backup/restore drill | `backups.md:3-5`; evidence C | verified |
| H9 | host | important | Decision table; hybrid A never | journal 107; evidence D2 | verified; options 2-7 p95 are estimates |
| P1 | process | major | 20 of 21 carried fixes escaped to production; two environment classes dominate | roadmap 341-361; journal 33/66/110 | verified (severity cut from critical) |
| P2 | process | important | Reviews catch the code-internal class only | journal 65/87/103; phase 5 final review:26 | verified |
| P3 | process | important | `state.md` not trustworthy for a resume | `state.md:3-15`; git log | verified |
| P8 | process | minor | "Nothing here can send an order" rests on software guards | README:154; roadmap:114; `authed.py:1367-1382` | verified (severity cut; four independent layers hold) |
| P9 | process | minor | Fix 38's `rfqs` prune crossed invariant 5 on a controller ruling | `housekeeping.py:69,99-108`; `0bd4b4c` | verified (severity cut; inert until 2026-10-02) |
| P10 | process | minor | Journal 112's hand DELETE had no ruling | journal 112:1346 | verified (severity cut; $0.52 not caused by it) |
| P12 | process | important | The "Needs you" channel does not close | phase 4.5 report:8; roadmap:340 | verified |
| P6-04 | phase6 | important | Phase 6 as written is mostly busywork against the blocker | roadmap 279-294 | verified |
| P6-07 | phase6 | important | The harness's own checks do not all run | evidence I; `checks.py:25` | verified; cause is the post-dump schedule, not unwritten code |
| P6-09 | phase6 | important | No load test against a NAS-sized database before a phase deploy | journal 89/109/110; roadmap:355 | verified |

### Refuted

| id | lens | claim | why it was dropped |
|---|---|---|---|
| FR-5 | freshness | Odds credits are not the constraint; the 15-min cadence's credit rationale expired at U1 | Arithmetic right (1-4 % of the pro-rata budget), but no credit rationale ever existed: the design spec budgeted the 5M tier from day 0. Changes nothing you do. |
| FM-8 | fillmodel | `apply_caps: false` disables per-game, daily and max_open, including live | Pre-registered by design (spec section 9.6): `constrained` exists to price what the caps cost. The per-bet cap **is** enforced at sizing. The live path fails closed at zero caps, the opposite of the claim. |
| FM-9 | fillmodel | The 120 s book-age rule blocks placement exactly where an order could fill | Backwards: age-dirty means no resting size was consumed, so the queue provably could not advance. The rule is verbatim spec (F36), and deleting it would have deleted the only fill. |
| NAS-1 | nas_audit | Game-night latency contaminates the week-1 measurement | Rests on misreading `staleness_at_place` (a pricing-time quantity, `plan.py:194-197`) as executor lag, and on "lifetimes compressed", which the data contradicts: 09-10 has the **longest** time in book of the four days. |
| NAS-3 | nas_audit | WS sink statement timeouts discard tape and dirty books | A timed-out commit is caught in `ws_sink.py:62-71` and writes **no** gap rows; `ws_disconnect` comes from the recorder's own ticker-selection query, not the sink. Dirty books are the 120 s age rule, with `ws.gaps = 0`. |
| NAS-5 | nas_audit | 483 intents silently aged past the TTL | Structurally impossible: intents are written and read back in the same loop body against the same bound. Better explanation: same-loop supersession plus non-excused hold-path rows. A check-definition gap. |
| NAS-11 | nas_audit | The Monday replay band will measure host latency | Replay builds a **single-variant** executor against a shared 150 cap, so the dominant divergence is structural and present on any host. Also, no code computes the comparison yet. |
| H5 | host | At 150 resting orders the loop's warm cost already fails the 7.5 s row | Tape reads are per **ticker**, not per order (`loop.py:676`), and the same box passed at 100-150 orders on 09-09 (p95 6.19 s). The zero-order floor, not order count, is the driver. |
| E4 | engineering | `intents_without_order_or_skip` is permanently red because it ignores `cap_gate` | Not permanent: 0 fails on 09-10 while `cap_gate` was already live. The cause is more likely supersession under slow loops. |
| P6 | process | README overstates freshness and hides the operative caps | The freshness half is contradicted by evidence F (game-window fair values refresh every ~2 min, which is what README:44 describes). The caps half survives as a smaller point: the README never mentions the shared 150. |
| P6-01 | phase6 | The allowance constant *is* the fill blocker | Over-stated: p25 time in book is 5.0 min, well past 220 s, and the never-cancel ceiling is only 27 of 445 keys. Queue position and absence of flow bind harder. |
| P6-02 | phase6 | The gate is unreachable; the weekend is the first near-kickoff measurement | 135 orders were already placed inside 24 h of kickoff, with zero fills. And the gate is unreachable **by construction** in phase 3, which is the invariant, not a defect. |
| P6-03 | phase6 | Week 1 can be excluded from the execution track by a loop-recorded amendment | `gate.py` has no from-bound and every criterion filters `replay = false`, so "exclude or re-score" is unavailable: it would require an invariant-2 edit or a DELETE. Also unnecessary: the week-2 freeze is computed per ISO week already. |
| P6-05 | phase6 | The RFQ listener is the one live ops risk; turn it off before kickoff | Measured growth after fix 38 is ~360 rows/min and ~0.9 GB/day, about 8x below the claim, and app-ws is now the smallest block-I/O container on the box. Turning it off would also remove the observation window most likely to hold a quotable football combo. |
| P6-06 | phase6 | Executor ceiling, the 150 cap and WS churn are one item: the host | Dirty markets rose as the host got healthier, so the correlation is ~0; the 150 cap is a constant a faster host leaves at 150; and the WS subscription is already pinned at its 500-ticker cap. |
| P6-08 | phase6 | The Monday replay duty will miss its 2 % band; pre-declare it | `loops_skipped 521` is a lifetime counter (of 17,138), not one hour's damage; the executor is level-triggered over a 900 s TTL; and "an integrity anomaly, not a headline" is already the verbatim calendar text at `roadmap.md:305`. |
| P6-10 | phase6 | Record a live response shape before coding against a new endpoint | Five of the eight cited fixes are not shape surprises (read-after-write lag, a sequence defect in our own script, frame volume), and the one endpoint where a live response **was** recorded still produced fix 39, because the defect was in the request schema. |
