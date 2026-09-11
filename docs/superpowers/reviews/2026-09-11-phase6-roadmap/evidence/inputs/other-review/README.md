# Sportsbook harness: phase 5 checkpoint review (2026-09-11)

Multi-perspective review run 07:20 to 10:30 CT on 2026-09-11 against the repo at `6eed2d8` and the NAS on build `7c3d555`. Eight review lenses (viability, fair-value freshness, fill model, engineering, NAS-workaround audit, host decision, process, phase 6 triage), each critical or important finding judged by three adversarial verifiers (evidence audit, devil's advocate, materiality). 63 findings: 46 verified, 17 refuted, plus 42 minor passed through unverified.

## Files

| File | What |
|---|---|
| `README.md` | This file: the distilled answer and the corrections from the completeness pass |
| `full-report.md` | The consolidated report, eight sections plus the 63-finding appendix (verified and refuted, with reasons) |
| `evidence.md` | Field data pulled read-only from the NAS at 07:27-07:33 CT: host, Postgres settings, table sizes, executor history, recorder ticks, fair-value cadence, order and cancel statistics, order 157's full story, research tables, logs |
| `findings-digest.json` | Every lens's raw findings with the verifier votes |
| `brief.md` | The brief the reviewers were given, including the numbers I pulled by hand first |

## Corrections after the completeness pass

The critic pass found four gaps before the credit limit stopped the revision. They are folded into the answer below and marked in `full-report.md` where a number changed.

1. **The gate variant is `sharp_two_sided`** (`deploy/nas.env` GATE_VARIANT). Its real fill count is zero. The one real fill (order 157) is `sharp_direct`. The report's section 2 argues adverse selection with `sharp_direct`'s numbers (4.6 c under mid, 8,524 queue ahead); the gate variant posts 0.8 c under mid behind 15,336. Both facts point the same way: fills need level pulls, not flow.
2. **The disk deadline was too early.** The dump-skip floor fires at 71 % used on `/volume1`; from 7.5 T used of 10.9 T that is about 239 GB away, roughly early October at 9 GB a day, not 09-19 to 09-30. Corrected in `full-report.md`.
3. **`apply_caps: false` is by design**, not a hidden defect: spec section 9.6 makes `constrained` the priced control. The consequence the report omitted: the per-variant `max_open: 25` in every variant file is a recorded label, not a limit. The shared `exec_max_open_orders` 150 is the only binding cap, and it hit 150 in 36 of 53 hours with open orders. Any fix to the churn must split that cap per variant.
4. **Two Monday items were dropped.** Fix 33 (UTC week keys) must land before Sunday night 2026-09-13 or the Sunday game keys into week 38; the report had it in week 2. And README's gate list does not match `harness/report/gate.py` (two rules listed and not coded, two coded and not listed), which Monday's first gate report will expose.

## The answer

### 1. Is one open position the system working?

No. It is a spec arithmetic error, coded faithfully.

| Funnel stage | Count |
|---|---|
| Intents (135 markets, 58 games) | 88,107 |
| Orders placed | 7,997 |
| Distinct (variant, market, side) keys | 445 |
| Cancelled `fair_stale` | 7,825 (97.8 %), median 12 min in book |
| Would have filled if never cancelled (dedup by key) | 27 of 445 |
| Real `queue_model` fills | 1 order (`sharp_direct`, 38.92 contracts) |
| Real fills on the gate variant `sharp_two_sided` | 0 |
| Gate criterion 1 needs, per variant | 150 fill events, 40 games, both sports, 80 % clean |

The executor cancels a resting order once its fair value is older than `max(stale_s 180, stale_allowance_s)`. The allowance is a fixed 120 s plus the 100 s tick budget, 220 s (`harness/pricing/fair.py`). The weekday daytime recorder cadence is 900 s (`harness/recorder/cadence.py`), 300 s at weekends, 120 s only inside a game window, nothing 01:00 to 08:00 CT. So every daytime order is older than its allowance for about 680 of every 900 seconds. A second rule stretches the tail: a dirty book (no tape for 120 s, normal for days-out markets) holds the order before the staleness check runs, so p95 time in book is 147 minutes. Both are coded exactly as F11 and F36 specify. The phase 4.5 and phase 5 reports framed this as the owner's symmetric "cadence or staleness bound" choice. It is not symmetric.

Fixing it buys measurement, not profit. Never cancelling would have filled 27 of 445 keys in three days, about nine a day pooled across three variants, against roughly nine a day needed for the gate variant alone by 09-28. The reason is queue depth: the gate variant posts under a cent inside the venue mid behind about 15,000 resting contracts with a 110-contract order. That is hypothesis H1, pre-registered; the README already names "a clear, well-documented no" as the likely outcome. The section 9.6 dataset deliverables (mispricing map, convergence lag, adverse selection on the counterfactual track) need no watched fills and remain answerable.

Criterion 1 cannot pass as coded. `dirty_seconds` accrues while the post-cancel counterfactual track runs to kickoff (`harness/execution/store.py` working-orders query includes `nw_done = false`; `harness/execution/loop.py` dirty branch adds 15 s per loop). Order 157 carries 3,020 dirty minutes. With 92 % of orders placed a day or more out, the 80 % `dirty_minutes = 0` share is unreachable at any fill count. Changing it is an invariant-2 decision.

The one fill may be an artifact. The gap-recovery branch in `harness/execution/loop.py` clamps the queue and resets the tape cursor without resetting the print watermark, unlike the no-book branch beside it. The reviewers infer order 157's fill replayed prints it had already consumed. The code asymmetry is confirmed; the effect on this order is not. Check it before Monday, since it is the entire ledger.

Levers, in order:

| Lever | Touches | Note |
|---|---|---|
| Executor-only stale bound of 600 to 900 s | settings | smallest diff |
| Hold while a live intent still targets the resting price | executor | keeps F36's safety at a 15-min horizon |
| Per-variant capacity instead of one shared 150 | settings | required with any of the above |
| Rest to expiry | executor | ceiling is the 27-key counterfactual |
| New variant ids (join the bid, near kickoff) | invariant 1, owner's gate | the only arm that produces fills |

The first four need a dated Amendment 5 with the four Amendment 4 elements (deploy sha and time, pre-amendment order range, tables and criteria touched, replay re-score command) and touch no variant file or gate definition.

### 2. Quality of the work

Engineering B minus, process C plus.

Sound: idempotent writes, an advisory lock on the loop, savepoint containment per order and per game, five additive Alembic revisions with a catalogue-diff test, 1,583 of 2,926 tests against a real Postgres, idempotent settlement, and the counterfactual track existing at all.

Fragile, all in the fill model:

| Defect | Status |
|---|---|
| Gap recovery resets the cursor but not the print watermark (`loop.py` recovery branch vs no-book branch) | strong inference that order 157 is a double count; unconfirmed |
| Watched track simulates to `now`, not `min(now, expiry)`, while the counterfactual track clamps | latent, 0 occurrences; first exposure is a lagging ticker across a kickoff |
| Deltas fold in before prints at equal timestamps (`fills.py`) | reviewers call it a double count; the code documents the opposite rationale; contested, needs a fixture-level check |

Operational discipline: `state.md`'s header has read "Updated: 2026-09-10 14:10 CT" across 21 rewrites; no preflight output since 09-09 08:29 despite six deploys; two invariant-5 slips on the loop's own authority (fix 38's `rfqs` retention DELETE, journal 112's hand DELETE of four `job_state` rows). Nothing destroyed. Both broke a rule the project wrote for itself.

The escape pattern: of carried fixes 21 to 41, 20 were first found on the NAS.

| Cause class | Count | Catchable before merge? |
|---|---|---|
| External API shape or behaviour assumed, not recorded | 8 | partly: a proving call through the production request path |
| NAS performance or database size | 7 | yes: EXPLAIN ANALYZE against the restore drill's copy |
| Deploy plumbing (the DDL lock race, three times) | 2 | yes, by fixing the root cause the first time |
| Logic and scope | 4 | reviews already catch this class |

### 3. Did we sacrifice quality for the NAS?

Yes, in measurement cadence and coverage. Not in the recorded tape or the paper ledger.

| Class | What | Cost |
|---|---|---|
| Right on any host, keep | bounded indexed reads (fixes 16, 17, 19, 25), delta read by (ticker, id) (22), adaptive tape batch (26), bounded builders (31), BRIN autosummarize (32), cheap RFQ declines (35), annotator from stored cells (36), RFQ boundary filter (38, 40) | none |
| Measurement cost, labelled | pricing budget exhausted on 45 % of game-day priced ticks (secondaries dropped; primary and gate variant always score); loop p95 over 7.5 s on 3 of 4 days; three integrity checks skip on their 2 s timeout daily; the six-hour report stage has not run in 26 h | contrast coverage thinner; late decisions; the tape-dedupe invariant unverified |
| Ops only | dashboard cadences, self-guard, Study week caps, quiet-hour verification throttle | none to the experiment |

A better host removes the middle row. Code on any host: the stale bound, the shared cap, the three fill-model defects, the criterion-1 semantics, fixes 34 and 37, the checks' schedule.

### 4. Is the Mac mini clearly needed?

Not proven, and this weekend is the wrong time.

| Claim | Status |
|---|---|
| Memory-bound Thursday night | true then: 5,021 MB swapped, memory pressure full 16 % |
| Memory-bound now | false since the media stop: swap-out 0, pressure near 0, ~3 GB available |
| The residual is disk | true: pgdata is on `sda`, the single rotational HDD (RAID1 with one member), not the NVMe; `random_page_cost` 1.1 is an SSD value; Postgres read 1.89 TB in 3 days against a 4 GB cache |
| The roadmap trigger is met | partly: it wants two consecutive game windows over 7.5 s with fixes 31-32; Thursday is one, and the swap clause's cause is removed |
| A game window ever passed the row | no: 09-09 p95 8.0 s; 09-10 p95 114.8 s |
| The mini is adequate | unknown: its RAM and SSD are recorded nowhere; the gating fact |

| Option | Cost | Downtime | Risk | Expected game-night p95 |
|---|---|---|---|---|
| Stay as is | $0 | none | low | 9 to 15 s, fails the row |
| NVMe pool for pgdata (two M.2 slots) | $150 to 370 | 30 to 45 min | low | 4 to 8 s |
| Add 16 GB SO-DIMM (to 24 GB) | $145 to 400 | 15 min | low | 5 to 9 s |
| Mac mini, full move | external 2 TB NVMe $250 to 400 | a day plus a tape gap | medium-high | 3 to 5 s if 16 GB or more |

Hybrids are worse: the IO wait is inside Postgres, so moving only the apps changes nothing, and moving only Postgres adds LAN round-trips.

Recommendation: do not migrate before tonight's kickoff or over the weekend. This is the first real fill measurement and a host change would confound it. The migration cannot use the backup drill: the nightly dump excludes the five bulk tables, about 36 of 47 GB, so it needs a full logical dump and restore, hours, unrehearsed.

The settling experiment, tonight and Saturday, media stopped, build unchanged: at 17:00, 19:00, 21:00 and 23:00 CT capture hourly `exec.loop_ms` p50/p95 with `exec.open_orders`, `vmstat 5 3`, `/proc/pressure/io`, and `pg_stat_database` hit versus read.

| Reading | Conclusion |
|---|---|
| p95 at or under 7.5 s every hour with 100 or more open orders | stay; buy nothing for latency |
| p95 over 7.5 s, swap in/out near 0, IO wait 15 % or more | disk-bound: NVMe pool or the mini |
| swap in/out above 0 with media stopped | RAM-bound: RAM first |
| p95 over 7.5 s, IO wait under 5 %, swap 0 | the loop's own cost; no host change helps |

Cutover if moving: Tue 2026-09-15, 08:00 to 16:00 CT. D-1 confirm chip, RAM (16 GB or more), a 2 TB external NVMe, auto-login with FileVault off, `pmset autorestart 1 sleep 0`, pgdata on a named volume or native Postgres (never a bind mount), rehearse the dump timing, land fix 37 first. T0 stop the four app containers, keep `app-ws` recording, `pg_dump -Fd -j4`, then stop `app-ws` and Postgres and copy the tape partitions written since. `pg_restore -j8`, verify `max(id)` on orders, fills and orderbook_events against the NAS, repoint the env, deploy `app-ws` first, then check health, alembic version, heartbeat, gap rows and spend caps. Leave the NAS stack stopped, not removed, for 7 days as rollback. Seams: uid 1000/gid 10 versus macOS 501/20, and 19 hardcoded `/volume1` references across roadmap, runbooks, Makefile and backup scripts.

The disk clock: `/volume1` is at 69 %; the dump-skip floor is 71 %, about 239 GB away, roughly early October at 9 GB a day. After the first skip, `make deploy-nas` aborts at its backup precheck.

### 5. Phase 6: what matters

| When | Do |
|---|---|
| Today, before 17:45 CT | Decide the `fair_stale` bound (a decision, not code). Ship the expiry clamp in the watched track with a test, or not at all today. Do not migrate hosts. |
| Before Sun 09-13 evening | Fix 33 (UTC week keys), or Sunday night's game keys into week 38. |
| Before Mon 09-14 09:00 | Draft Amendment 5 (executor stale bound plus per-variant cap). Run the per-key counterfactual query so the report prints 27 of 445, not 224. Decide the `dirty_minutes` semantics. Land fix 41. Reconcile README's gate list with `gate.py`. |
| Week 2, by 09-21 | Deploy Amendment 5. Check the recovery watermark on order 157 and re-score by replay. Settle the delta-before-print question with a fixture. Move the three checks off the post-dump window. Act on the disk. Fixes 34 and 37. |
| Week 3, conditional | Item 4's normalizer only if Monday's tick coverage shows a secondary under ~50 %. The archive-and-drop proposal, written, never executed without a yes. |

Drop or defer: item 1 (invariant 1, adds cost to an exhausted pricing budget, cannot raise gate fills; run it as replay), item 3 (back-computable from `venue_trades`), item 6 (no failing consumer), the six cosmetic plan-next inputs. Item 5 is mostly done. Item 7's trigger has been met since 09-07.

Missing entirely: anything touching the fill model or the cancel policy; a cold-read gate before deploys (extend the bounded-read rule to `checks.py`, `execution/store.py` and `research/`, and add cold p95 timing to the restore drill); an environment lens in the review pipeline.

Monday's report will show zero gate-variant fills. That is honest and breaks nothing: `passed` is unreachable by construction in phase 3, the gate window is the whole run, and the week-2 selection uses `gap_mid` cells and Holm contrasts that need no fills. The report must say criterion 1 is out of reach and mis-scored, carry a data-completeness block (report stage 26 h stale, three checks skipping, 483 intents unexplained), and print `fill_method = 'queue_model'` explicitly, because one journal entry already read 75 counterfactual rows as fills.

### 6. Decisions only the owner can make

1. The `fair_stale` bound. Default: executor-only 600 to 900 s plus per-variant capacity under Amendment 5. Waiting costs ~2,600 orders a day and makes weeks 2 and 3 repeat week 1.
2. `dirty_minutes` in criterion 1. Default: accrue only while the watched order is open, for genuine gaps or a dead recorder. Waiting mis-scores every gate row from Monday.
3. The host. Default: stay through the weekend, run the experiment above, decide Tuesday.
4. The mini's specs. State them first. At 8 GB, do not move; tune the NAS.
5. Fix 38's `rfqs` retention DELETE. Approve it explicitly or say no. Nothing at risk before 2026-10-02; the precedent is the problem.
6. A fill-producing variant. Default: score join-the-bid and near-kickoff by replay over week 1's tape, decide before the 09-21 freeze.
7. The read-scoped Kalshi key (F64). Swap it when secrets are next recopied.

### Refuted along the way

Of 63 findings, 17 fell in verification. Notable: "`apply_caps: false` hides the caps" (by design; `constrained` prices them; the per-bet cap is enforced at sizing); "turn the RFQ listener off before kickoff" (growth under 1 GB a day after fix 38, and it is the box's smallest I/O consumer); "the 120 s book-age rule blocks placement where an order could fill" (backwards: a quiet book means no queue advanced; deleting it would have deleted the only fill); "game-night latency contaminated week 1" (09-10 had the longest time in book of the four days). The full list with reasons is the appendix of `full-report.md`.
