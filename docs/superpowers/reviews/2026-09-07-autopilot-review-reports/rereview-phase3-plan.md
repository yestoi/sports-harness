# Re-review of the revised phase 3 design, plan and pre-registration record

Date: 2026-09-07. Read-only verification pass over revision 2 of the addendum
(`docs/superpowers/specs/2026-09-07-phase3-paper-execution-design.md`), the plan
(`docs/superpowers/plans/2026-09-07-phase3-paper-execution.md`) and the pre-registration record
(`docs/superpowers/reviews/2026-09-07-phase2-preregistration.md`), against the review record
`docs/superpowers/reviews/2026-09-07-autopilot-adversarial-review.md` (section A rows, B(2), D2,
and the binding section F), the v2 spec, the roadmap, `verify.md`, and the working tree at
`b25c91c`.

This is a verification of the revision, not a fresh design review. Everything the review asked for
is checked for a landing place; then the tasks are checked against each other and against the code
they name.

## Verdict

**FIX-THEN-READY.** The revision absorbs the review almost completely: 48 of 48 in-scope findings
have a named landing place, section F is followed on every user decision and on 22 of 23 rulings,
and the pre-registration record now carries the amendment protocol, the analysis plan and the
calendar. Nothing in the plan sends an order, reads a secret, gives an implementer NAS access, or
drops/truncates a production table.

What blocks dispatch is not coverage but internal consistency: five interface or ordering defects
would make a task fail or silently produce wrong numbers, and a sonnet implementer would resolve
each of them by guessing. They are listed first. All five are one-paragraph edits to the plan.

---

## A. Coverage of section A findings

In scope: every section A finding tagged `MUST-FIX before loop` or `FIX in phase 3` whose target is
the addendum, the plan, the pre-registration record, or harness code that a phase 3 task touches.
That is F1-F21, F30 (its phase-3 review-checklist half), F32-F56 and F69: **48 findings**. F22-F29,
F55's SKILL.md half and F60 target SKILL.md, the roadmap or `deploy/nas.env` and are out of scope
here; F61-F68 and F70-F78 are phase 4-6.

The plan's own finding-to-task table was treated as a claim and checked row by row against the task
text. Where the task text does not contain what the table claims, that is recorded below.

### Placed and verified

| finding | lands at | verified against |
|---|---|---|
| F1 queue model | plan T4 `simulate_fills` rules; addendum §2 "Queue model" | all four rules present (through-print zeroes, at-price consumes then fills, negative delta via `traded_at_price`, positive delta inert); tests `test_queue_consumed_before_fill_at_price`, `test_sweep_through_zeroes_queue_and_fills`, `test_negative_delta_attributed_to_trades_first_then_cancels`, `test_positive_delta_never_changes_queue` |
| F2 cursors, id ordering | plan Global Constraints "Tape access"; T2 `Order` columns `queue_remaining`, `traded_at_price`, `tape_cursor_event_id`; T3 `load_book`/`advance_book`; T6 step 4 write-back | matches R9 exactly: no print cursor, delta cursor by `id` with a lower `ts` bound only, `tape_cursor_trade_ts` absent everywhere |
| F3 `no_watcher` track | addendum §2 "Two tracks"; T4 `fill_method` param; T6 `nw_*` quartet; T9 `nw_fill` anchor; T10 table 3; T11 criterion 4 | gate reads the `nw_fill` anchor, watched set reported beside it |
| F4 WS field names | plan "Verified data facts" bullet 2; T3 `from_ws_raw`; T6 `test_step_creates_intent_and_order_with_positive_queue_from_ws_snapshot` | facts bullet now says `yes_dollars_fp`/`no_dollars_fp`, matching `tests/test_kalshi_ws.py:34`; `from_ws_raw` falls back and raises when both keys are absent |
| F5 taker side | T3b item 1 `canonical_taker_side`; T4 `hits()`; addendum §2 | quant's option taken (drop and count, no `DROP NOT NULL`), consistent with `VenueTrade.taker_side` being `nullable=False` at `harness/db/models.py:168` |
| F6 gap per sid | T3b item 2; T3 `load_book` dirty-by-sid; test `test_dirty_by_sid_across_tickers` | `_check_seq` before `_last_seq[sid]` named; T3 reads the existing `sid` column so it works pre- and post-hotfix |
| F7 gap recovery, fresher anchor | T3b item 4 `_recover_sid`; T3 `load_book` anchors on the fresher of WS and REST; T2 heartbeat `book_dirty_markets`; T12 dashboard | addendum §0.12 records resubscribe-for-snapshot as our convention |
| F8 / R10 missing book | addendum §1 "Missing book"; T3 `None` contract; T3b item 5; T5 `no_book` flag; T6 step 4 late-book path; T12 skip breakdown; T14 verify query | R10 followed: placed with `queue_ahead_at_place = NULL`, `book_source = 'none'`, excluded until a book exists; `ws_lookahead_hours = 72`, `ws_lookback_hours = 8`, 500 cap kept |
| F9 tie payout | addendum §3; T2 `VenueSettlement.payout Numeric(3,2)`, `result` String(4); T7 `resolve_market -> Decimal in {0, 0.5, 1}` | `Benchmark.p` at `Numeric(6,4)` accepts 0.5; `test_result_rows_accept_half` |
| F10 / R11 venue result | T3b item 6 hourly `status=settled`; T7 `fetch_market`; T11 criterion 9 | `raw_responses` read filter `params->>'status' = 'settled'` is sound: the existing open-markets fetch at `harness/recorder/tick.py:170` stores `/markets` with no `status` key |
| F11 centicent fee | addendum §0.9; T1 `ceil_to_centicent`; Global Constraints; T4 per-fill fee | arithmetic checked: 0.0175x12x0.55x0.45 = 0.051975 ceils to 0.0520, /12 = 0.0043; n=1 gives 0.0044. Test values in T1 Step 1 are correct |
| F12 `has_print` | addendum §2 "Sanity and validity"; T4 `has_print()`; T10 table 6 panel; T11 criterion 1 | renamed from `confirmed`; gate counts fill events, not `fills` rows |
| F13 / R22 gate deviations | addendum §0.7; T11 `CRITERIA` with stored definitions and `criteria_hash` | see B below for one extra deviation beyond R22's list |
| F14 unit of analysis | addendum §4; T10 `cluster_ci` with t(G-1) and the finite-cluster correction; pre-registration Analysis plan | the record now states that "cells with n < 30" means fewer than 30 games |
| F15 markouts | addendum §3; T2 `Markout` PK `(order_id, anchor, horizon)` plus freshness columns; T9; T11 | formula, sign convention and `fair_changed` all written down |
| F16 / R13 shrinkage and families | addendum §4; T10 `eb_shrink`; pre-registration Analysis plan | R13 followed: posterior interval judges §9.6, BH intervals reported beside it |
| F17 pre-registration protection | T4b `OPTIONAL_KEYS`; T2 `orders.config_hash`, `runs.build_sha`, `fair_values.pricing_version`; T1 Step 5 Amendment 2; record Correction section | `no_velocity` mislabel corrected; the six ids pinned by `test_six_committed_yamls_hash_to_the_recorded_ids` with the exact hashes from the record |
| F18 / U2 NO side | addendum §0.11; new T4b; record Amendment 3 placeholder | new id `sharp_two_sided`, six existing ids untouched, `MAX_SECONDARY` 5 to 6 |
| F19 / U3 partitioning | addendum §0.6; new T2b; T12 days-to-ceiling | 2 TB ceiling, weekly ranges, `ATTACH PARTITION` path, R17 tuning |
| F20 / R14 replay | addendum §4; T13 15 s grid, `pricing_clock_for_run` for signal `created_at`, strict equality test | `pricing_clock_for_run(run, tick_budget_s)` matches `harness/strategy/pipeline.py:104` |
| F21 / R15 main-only deploys | Global Constraints; controller steps in T1, T2b, T4b, T14 | fast-forward-then-deploy spelled out in each |
| F30 (phase 3 half) | addendum §10 item 5; T6 `test_compose_app_exec_block_has_no_volumes_or_kalshi_env` | |
| F32 scheduler and timeouts | addendum §0.13, §3; T7 `Settler` + `job_runs` + `Budget`; T2 `make_engine(statement_timeout_ms)`; T8 watermark drain; T12 housekeeping | `test_engine_statement_timeout_per_engine` expects 10s/30s/15min, correct for 10 000/30 000/900 000 ms |
| F33 capacity and loop metrics | T2 `exec_max_open_orders = 150` and heartbeat fields; T5 `Skip(exec_capacity)` ordered by descending edge | |
| F34 intent selection | addendum §1 "Intake"; T6 steps 2 and 5 | see C4 for the one gap |
| F35 skip dedupe | T2 `uq_skip_once`; T6 `on_conflict_do_nothing` | |
| F36 fair staleness, dead recorder | T5 `Cancel(fair_stale)`; T6 dead-recorder rule; T3 `book_at` returns `None` when stale; T12 `ws_last_event_at` | |
| F37 second writer, advisory lock | addendum §0.8, §1; T6 `pg_try_advisory_lock`, `exec-health`, `stop_grace_period: 60s`; T7 restricted update | |
| F38 post-only reject | T5 place and reprice paths; T12 table 8 rate | |
| F39 / R8 expiry | addendum §0.3; T5 `test_no_renew_action_exists` | `Renew` removed from the action set entirely |
| F40 fractional counts | Global Constraints; T2 `Numeric(14,2)`; T4 `Decimal` throughout | |
| F41 worst case | addendum §2; T4 `crossed` on the maintained book | |
| F42 CLV definitions | addendum §3; T8 `GAP_OUTCOME_TYPES`, `p_used_kind`, net and ROI forms; §0.5 `novig_devig_t5` | see BLOCKING-4 for the gate consequence |
| F43 convergence lag | addendum §4; T10 `km_median`, featured-only, sampling floor | |
| F44 price grid | T2 columns; T3b `upsert_venue_markets`; T4b `snap_to_grid`; T12 alarm | partial, see below |
| F45 fee model from the venue | T2 columns; T3b `fetch_series`; T4 `fill_fee_fields`; T12 drift alarm | partial, see below |
| F46 kickoff moves | T7 `kickoff_moved`, `stale_unsettled`; T8 target times from the current kickoff | |
| F47 schema hygiene | T2 index-presence loop, generated `drop_schema`, session-scoped `_schema` fixture | matches the real defects at `harness/db/schema.py:60-67` and `tests/conftest.py:17-34` |
| F48 / R16 `--out -` | T10, T13 CLI; T14 verify row | |
| F49 verify.md | T14 Step 2 plus what already landed in `b25c91c` | see IMPORTANT-3: Step 2 is written against a stale copy |
| F50 `/kill` hardening | T12 | `Sec-Fetch-Site` rule and the 200-char strip both stated |
| F51 sink discard gap row | T3b item 3 | |
| F52 `seed-teams` guard | already on `main` (`Makefile:39, :71`) | |
| F53 dirty-tree refusal | already on `main` (`Makefile:16-17, :45-46`) | |
| F54 runbook | already on `main` (commit `9c785a9`); T14 Step 1 re-applies conditionally | |
| F55 (log filter half) | T3b item 8 | |
| F56 AS at the seed | addendum §0.10; T2 `signals.as_measured`; T9 `as_measured_table` | D12 defines the bucket |
| F57 key numbers | T10 table 4b | |
| F58 clock offset | T3b item 7 | |
| F59 conformance | addendum §10 | |
| F69 free space | T12 days-to-ceiling; verify.md `df -h` already present | partial, see below |
| F79 six decimals | addendum §0.6 | |
| F31 amendment protocol | pre-registration record, new section | complete: measurement vs strategy amendments, the 2026-09-21 cut-off, R1 as an invariant, the hotfix counterweight |

**MISSING: none.**

### Partial (placed, but part of the review's ask is absent)

- **F45.** The recording half lands (`venue_markets.fee_type`, `fee_multiplier`, the daily
  `GET /series/{ticker}`, per-fill `fee_type`/`fee_multiplier`/`maker_rate`, the drift alarm). The
  code half does not: `fee_model_for` still truncates the multiplier with `int()`
  (`harness/pricing/fees.py:22-26`, so a fractional multiplier gives zero fees) and still routes
  `flat` down the maker branch. Addendum §9 D14 defers it to phase 4 with a stated cost. That is a
  documented deviation, but it contradicts R21 and is not in B(4)'s phase 4 list. The function is
  currently dead code (only `tests/test_fees.py` calls it), so the risk is latent, not live.
- **F44.** The dashboard data-quality row for non-`linear_cent` markets is in Task 12; the
  `runs.notes` warning the finding also asked for is in no task.
- **F69.** Task 12's days-to-ceiling and the `df -h` row land, but `verify.md:86` still expects
  "DB size below 800 GB" while U3 sets the ceiling at 2 TB and `db_budget_gb = 2000`. Task 14
  Step 2 does not touch that row, so the contract keeps a threshold the policy has superseded.

**Coverage: 48 of 48 in-scope findings placed; 3 partial; MISSING: none.**

---

## B. Consistency with section F

Checked every user decision and every ruling against the addendum and the plan.

Followed correctly: **U1** (§0.1 keeps `staleness_s` as pricing time minus newest sharp
`last_update` and keeps the 90 s gate; the 5M cadence is T3b item 9), **U2** (§0.11 and T4b, new id,
amendment 3 before 2026-09-16, `MAX_SECONDARY` 6), **U3** (§0.6, T2b, 2 TB ceiling, archive/drop
out of scope in §8 and named a gate), **U4** (phase 5; nothing in phase 3 spends), **R1** (Global
Constraints name the gate definitions as invariants), **R2** (§8 lists a live `no_veto` as out of
scope; the record's Correction section explains H9 within the primary), **R4**, **R7** (record
Calendar section), **R8** (§0.3, `Renew` deleted, `test_no_renew_action_exists`), **R9** (Global
Constraints "Tape access" is R9 verbatim), **R10** (§1 "Missing book"), **R11**, **R12**
(T11 criterion 1 is R12 word for word), **R13** (T10 and the record's Analysis plan), **R14**
(strict equality in T13, 2 % in the Monday duty), **R15**, **R16**, **R17** (T2b's compose block is
R17's nine settings plus `shm_size: 512m`), **R19**, **R20**, **R23**.

Three contradictions:

1. **R22 / U1 / §0.1 versus Task 11 criterion 8 and Task 6 step 3 (IMPORTANT).** §0.1 says the
   staleness criterion is "evaluated on that quantity, unchanged", where the quantity is *pricing
   time* minus the newest sharp `last_update`. Task 11 criterion 8 evaluates
   `median staleness_at_place`, and Task 6 step 3 defines the executor's `MarketNow.staleness_s` as
   `now - newest_book_ts` with `now` being the loop clock, not the pricing clock. The pricing tick
   runs on a 120 s cadence and the executor on 15 s, so `staleness_at_place` exceeds the spec's
   `staleness_s` by up to a full tick. The gate would then be measuring a harsher, differently
   defined quantity than the one U1 and R22 say stands unchanged, and the whole point of the 5M
   upgrade is to make that number pass honestly.
   Fix: `MarketNow.staleness_s` carries the gap snapshot's stored `staleness_s`, `orders.staleness_at_place`
   records that, and the executor's own freshness check uses a separate `fair_age_s = now - fair_ts`.
2. **R21 versus addendum §9 D14 (MINOR).** R21 says nothing moves out of phase 3 except what B(4)
   lists; B(4)'s phase 6 list is F11, F18, F57/F17, F19/F69/F29 item 8 and F51. D14 moves the
   `fee_model_for` half of F45 to phase 4. Either fix it in Task 2 (three lines) or record it as a
   dated ruling naming the deviation from R21.
3. **§0.7 lists five deviations where R22 names four (MINOR).** R22's list is: `result` and
   `opening_first_seen` excluded, fills counted as events, the equivalence bound, the
   mismatched-market count. §0.7(d) adds a fifth, the "≥ 90 % of settled markets carry a
   `source = venue` row" clause on the settlement criterion. It comes from R11 and F13 and is a
   tightening, not a loosening, but it is an amendment to §9.5 that R22 did not enumerate. One
   sentence in §0.7 saying so closes it.

One authority question rather than a contradiction: **Task 2b renames live tables**
(`alter table T rename to T_legacy`, plus index and sequence renames). Roadmap invariant 5 lists
RENAME among the hard-forbidden non-additive operations and says only "creating partitions is
additive and authorized (U3)". The addendum (§10 item 4) and the plan both assert the renames are
pre-authorised by U3. U3's text says "metadata-only `ATTACH PARTITION`", which is the outcome, not
the mechanism. Recommend a one-line journal ruling, or a one-line user yes, before Task 2b Step 5
rather than reading the authorization broadly at the moment of running it.

---

## C. Interface consistency across tasks (SDD pre-flight scan)

Every pair of tasks that shares a file or a symbol was checked. Nine mismatches; five would break a
task or corrupt a number.

### BLOCKING-1. Task 1 consumes settings that Task 3b creates, and runs first

Task 1's Interfaces say `stale_allowance_s(feed, ttk_minutes, s)` reads
`s.odds_alt_interval_near_s` and `s.odds_alt_interval_far_s`, and name them as "the cadence
settings Task 3b names". Task 1's Files list does not include `harness/config/settings.py`, and
`Settings` (`harness/config/settings.py`) has neither field today. The plan's Order runs Task 1
first. Task 1 Step 1's `test_stale_allowance_table` would fail with `AttributeError`.

Fix: add `harness/config/settings.py` to Task 1's Files with
`odds_alt_interval_near_s: int = 120`, `odds_alt_interval_far_s: int = 900`, `odds_alt_window_h: int = 36`
(Task 3b then only changes defaults and wires `alternates_due`), or move Task 3b's item 9 ahead of
Task 1.

### BLOCKING-2. `simulate_fills` mutates a `BookState` that Task 6 shares across orders and tracks

Task 4: "every delta is applied to `book` when given and `crossed` becomes true the first time
`book.best_ask(side) <= prob`". Task 6 step 3: "one `load_book`/`advance_book` per ticker per step
(cache)". Task 6 step 4 then hands `simulate_fills` the deltas from each order's own
`tape_cursor_event_id`, for two tracks, for every order on that ticker.

So the cached book has already absorbed those deltas through `advance_book`, and each
`simulate_fills` call re-applies them to the same mutable object. Ladder sizes move twice per
order per track; `resting_at` (which sets `queue_ahead_at_place` for a late-book order under D7)
reads a corrupted ladder; and the cross condition is evaluated against a book that is neither "now"
nor "at the cursor". `BookState.apply_delta` also sets `dirty = True` on any seq discontinuity, and
nothing says what `simulate_fills` does with a book that goes dirty mid-walk.

Fix: state in Task 4 that `simulate_fills` never mutates its argument (it works on a private copy),
and in Task 6 that the copy is seeded from the book as of the order's cursor, not the advanced
cache. Add `test_simulate_fills_does_not_mutate_the_caller_s_book`.

### BLOCKING-3. `match_key` is used in four places and defined nowhere

`match_key` appears in addendum §1 (recorded at placement, and a changed value cancels with
`unmatched`), addendum §5 (`orders.match_key`), plan Task 5 (`MarketNow.match_key: str`,
`OpenOrderView.match_key`) and plan Task 11 criterion 10 ("zero orders whose current market
`match_key` differs from `orders.match_key`"). It exists in neither the codebase nor the v2 spec
(`git grep match_key harness/ tests/` returns nothing), and Task 2's list of columns added to
`VenueMarket` does not include it. Nothing says what it is composed of, where it is computed, or
which task writes it.

Fix: define it in Task 2 as a column on `venue_markets` with an explicit composition, for example
`sha1(game_id, market_type, coalesce(threshold,''), coalesce(side_team_id,''), coalesce(side,''))`
recomputed in `upsert_venue_markets`, and name Task 3b as the writer. Also define
`MarketNow.matched` (presumably `match_status in ('matched','manual')`, the `CONFIDENT_MATCHES`
tuple at `harness/strategy/run.py:49`).

### BLOCKING-4. The gate needs CLV against seven benchmarks; `gap_outcomes` holds four

Task 8 sets `GAP_OUTCOME_TYPES = ("pinnacle_t5", "consensus_t5", "kalshi_mid_t5", "result")` per
F42's volume restriction, and addendum §3 says "orders and fills inherit CLV through
`signals.gap_snapshot_id`". Task 11 criterion 7 requires mean `clv_target_p_net` >= 0 for each of
`pinnacle_t5`, `consensus_t5`, `consensus_t60`, `consensus_t180`, `kalshi_mid_t5`,
`kalshi_last_trade_pre_kick` and `novig_devig_t5`. Four of those seven can never have a row, and
the criterion's "where any row exists" clause makes it pass silently on the other three. §0.7(a)
only excludes `result` and `opening_first_seen`, so the gate is meant to see all seven.

A second head of the same problem: `drain_gap_outcomes` computes `p_used` from "the primary's
`price_target` from the same run's non-replay signal". A `sharp_two_sided` NO-side order inherits
the primary's YES target. Addendum §4 also says non-executed variants in table 2 "report snapshot
CLV at their own `price_target`", which `gap_outcomes` cannot supply.

Fix: keep `gap_outcomes` restricted for the whole-population map, and add a per-order CLV path in
Task 8 or Task 9 covering all nine benchmark types for the few hundred orders that exist, keyed by
`(order_id, benchmark_type)` with the order's own `prob`. Then say in Task 11 which of the two the
gate reads. The `clv` view in Task 2 already computes the right per-order form and should be the
model.

### BLOCKING-5. Task 7 imports functions that Tasks 8, 9 and 12 create later

`Settler.run()` in Task 7 "runs in order `run_settlement`, `compute_benchmarks` (Task 8),
`drain_gap_outcomes` (Task 8), `compute_markouts` (Task 9), `housekeeping` when due (Task 12)", and
`run_settlement` calls `insert_result_benchmarks(session, game_id, payouts)` from Task 8. The Order
is 7, 8, 9, ... and Tasks 8 and 9 do not list `harness/settlement/job.py` in their Files, so no task
owns the wiring. Task 7's own tests (`test_settler_writes_job_runs_and_never_touches_runs_notes`,
`test_build_scheduler_registers_the_settle_job`) cannot pass against imports that do not resolve.

Fix: either state that Task 7 ships `Settler.run` with a `STAGES` list containing only
`run_settlement`, and that Tasks 8, 9 and 12 each append their stage (adding
`harness/settlement/job.py` to their Files), or move `insert_result_benchmarks` and the benchmark
stage into Task 7. Task 12 already lists `job.py`, so the pattern is half-established.

### IMPORTANT-1. `cap_gate` events have no writer

Addendum §1 says cap decisions are "recorded as `cap_gate` events either way", and Task 2 creates
`uq_skip_once (intent_id, kind, reason) where kind in ('skipped','cap_gate')`. But Task 5 emits a
single `Skip(intent_id, reason)` action type with `reason = cap_<label>`, only for `apply_caps`
variants, and Task 6 step 5 says "`Skip` inserts through `uq_skip_once`" without saying which
`kind`. A literal implementer writes `kind = 'skipped'` for everything, `cap_gate` is never written,
and the "either way" promise for non-`apply_caps` variants is silently dropped.

Fix: give `Skip` a `kind` field defaulting to `skipped` and set it to `cap_gate` for `cap_*`
reasons; state in Task 5 that a cap label that is False emits a `cap_gate` skip for every exec
variant and only blocks placement for `apply_caps` ones.

### IMPORTANT-2. Task 6 never says who writes `has_print`, the fee fields, or the `snapshot_cross` fill

Task 4 supplies `has_print(fill, order, prints, window_s=60)` and
`fill_fee_fields(fee_model) -> (fee_type, fee_multiplier, maker_rate)`, and returns
`FillResult(fills, state, cross, crossed)`. Task 6 step 4's insert list mentions none of them: not
`has_print`, not the three fee columns, and not the persistence of `FillResult.cross` as a
`snapshot_cross` row. Yet `fills.has_print` carries a Layer 2b invariant, table 6 reports the
`has_print` share and the `union snapshot_cross` fill count, and the addendum says `has_print` "must
read 100 %; it is asserted". No task contains that assertion.

Fix: add the three writes to Task 6 step 4, say that `FillResult.cross` is inserted once with
`fill_method = 'snapshot_cross'` (and which track owns it, so the two tracks do not each write one),
and add `test_has_print_true_on_every_queue_model_fill` and
`test_no_watcher_fill_writes_no_ledger_row` to Task 6 Step 1.

### IMPORTANT-3. Task 14 Step 2 is written against a `verify.md` that no longer exists

Commit `b25c91c` already added "Layer 2b: invariants and plausibility bands" to
`docs/superpowers/autopilot/verify.md:119-167`, including the invariant queries, the five prose
invariants, and the bands table with `Queue-model fills on WS-covered, non-dirty books >= 80 % (R12)`.
Task 14 Step 2 instructs the implementer to "Add 'Layer 2b, invariants ...'", to "Add the plausibility
bands", and to "delete the `confirmed` band (F12)" -- a band that is not there. Followed literally it
produces a duplicated section. It also sets "Odds credits per day between 2,000 and 170,000" while
`verify.md:84` and the existing bands table already carry `500 to 3,000` and the tier language.

Fix: rewrite Step 2 as a delta against the current file. What is genuinely new is: `ws_last_event_at`
in the heartbeat query; the `(venue, trade_id)` duplicate invariant (D4); the `runs.build_sha <> DEPLOY_SHA`
invariant; the newest `job_runs` settle row; `gate_reports` >= 1 with `passed = false`; the
`report --week N --out -` row (R16); the reconciliation of the Odds-credit band to the 5M tier; and
`verify.md:86`'s "DB size below 800 GB" to the 2 TB ceiling (F69/U3).

### IMPORTANT-4. Task 9 has no anchor for the `queue_model union snapshot_cross` markout set

Addendum §4 table 3 reports markouts "under `queue_model`, `queue_model union no_watcher` and
`queue_model union snapshot_cross`" (F3's ask). Task 9 writes anchors `place`, `fill` (first
`queue_model` fill) and `nw_fill` (first fill in `queue_model union no_watcher`). There is no anchor
whose first fill includes `snapshot_cross`, so the third column of table 3 has no source. Either add
a fourth anchor or state in Task 10 how table 3 derives it from the existing rows.

### IMPORTANT-5. `venue_trades` millisecond truncation does not guarantee one row per trade

D4 says both writers truncate `ts` to milliseconds "so the partitioned key cannot duplicate a
trade". The two writers read different fields: the WS sink uses `ts_ms`
(`harness/recorder/ws_sink.py:72`) and the REST normalizer uses `created_time`
(`harness/normalize/kalshi.py:123`). Nothing establishes that Kalshi's `created_time` renders the
same millisecond as `ts_ms` for the same trade. With the PK widened to `(venue, trade_id, ts)`, a
one-millisecond difference inserts the trade twice, and `ON CONFLICT DO NOTHING` (which infers the
PK) will not catch it. Double-counted prints inflate fills and break the tape-conservation
invariant.

Fix: state in Task 2b that the REST writer looks up an existing `(venue, trade_id)` row and skips
when present, or that `simulate_fills` deduplicates prints by `trade_id`. The Layer 2b duplicate
count is a detector, not a guard.

### MINOR interface notes

- **T6 does not say which intents it passes to `plan_actions`.** Addendum §1 states the rule
  (newest per `(variant_id, venue_market_id, side)`, signal younger than `intent_ttl_s`, game
  before the cutoff); Task 6 step 5 does not repeat it. F34 asked for it in the Task 6 text.
- **`StrategyState.positions` is typed `dict[tuple[int, int], Decimal]`
  (`harness/strategy/run.py:88`).** Task 4b changes the dedupe key to `(game_id, side_team_id, side)`
  but does not name the type change; Task 5's `rebuild_state` builds the same structure.
- **`register_variants` will not refresh `config_json` for the six existing rows** (the
  `unchanged` branch at `harness/strategy/variants.py:161-163` skips the update), so the DB keeps
  configs without `sides` and `active_variants` returns them raw. The plan relies on exactly this
  ("rows registered before the key existed still read `[yes]`"), so it works, but it also means
  `variant_id_for(ConfigHistory.config_json)` no longer reproduces `config_hash` for new variants
  once `with_defaults` is stored. Nothing reads it today.
- **T4b's `_validate` wording** ("requires `sides` to be a non-empty list drawn from
  `("yes","no")`") reads as requiring the key to be present, which would break the six YAMLs.
  `test_six_committed_yamls_hash_to_the_recorded_ids` catches it, but one clause ("when present")
  saves a cycle.
- **`p95_loop_ms` "over the last 100 loops"** has no store; `exec_heartbeat` is a single row. An
  in-process deque is the obvious answer and is lost on restart. Say so.
- **`client_order_id = f"paper-{intent_id}-{int(now.timestamp())}"`** collides if a reprice places
  twice for one intent inside the same second; possible on the replay grid.
- **`VenueTrade.count` stays `Numeric(12,2)`** while addendum §5's header says every contract
  quantity is `Numeric(14,2)`. Harmless, but the header is not literally true.
- **Task 7's settled-body read** (`endpoint = '/markets' and params->>'status' = 'settled'`) has no
  `fetched_at` bound on a partitioned, season-sized `raw_responses`. The 900 s batch timeout covers
  it; a `fetched_at > now() - interval '7 days'` bound is one clause.

---

## D. Testability

Each Step 1 test list was read against the behaviour its task's interface claims, asking whether the
test could pass with the old defect present.

**Pins the behaviour (good):**

- **F1 fill rules (Task 4).** Four separate tests, one per rule, plus fractional counts, opposite
  side, and chunking invariance. The old double-counting model fails
  `test_negative_delta_attributed_to_trades_first_then_cancels`; the old queue-limited sweep fails
  `test_sweep_through_zeroes_queue_and_fills`; the late-joiner bug fails
  `test_positive_delta_never_changes_queue`. This is the strongest part of the plan.
- **Taker-side canonical rule.** `test_sink_canonical_side_prefers_outcome_side` and
  `test_sink_drops_print_without_side_and_counts` (Task 3b) fail against the current
  `taker_side=body.get("taker_side") or "yes"` at `harness/recorder/ws_sink.py:72`;
  `test_print_without_side_never_fills` (Task 4) pins the consumer.
- **Partition attach path.** `test_partition_bulk_tables_attaches_legacy_rows_and_continues_ids`
  builds the real legacy shape by raw DDL and asserts partitioned parents, readable rows, sequence
  continuation and idempotence. It would fail against any of the plausible wrong implementations.
- **Strict replay equality.** `test_replay_execute_reproduces_live_orders_and_fills_exactly`
  compares the full `(ticker, side, prob, contracts, placed_at)` and
  `(order key, prob, contracts, filled_at)` sets, which is R14's strict equality, not a tolerance.
- **`_schema` / truncate fixture.** `test_fixture_truncates_between_tests_a`/`_b` each assert
  `id == 1`, which is a real ordering guard.

**Does not pin what it claims:**

1. **The `has_print` sanity assertion (Task 4, Task 6).** `test_has_print_true_and_false` tests the
   pure predicate only. Nothing tests that Task 6 writes the column, and no code anywhere asserts
   the 100 % property the addendum promises. The Layer 2b query is a post-hoc detector on the NAS,
   not a test. Add a Task 6 DB test.
2. **The `no_watcher` track's isolation (Task 6).**
   `test_no_watcher_track_keeps_filling_after_a_cancel` proves it keeps filling. Nothing proves the
   negative half of the contract, that an `nw` fill writes no `ledger` row and does not enter
   `positions` or exposure. That negative is what makes the unwatched markout an honest gate
   quantity, so it deserves its own assertion.
3. **F1's premise test can never fail before merge.** `tests/test_fills_tape.py`'s
   `test_recorded_prints_have_a_matching_delta_within_1s` skips with a reason when
   `tests/fixtures/ws_tape_sample.json` is absent, and the controller exports that fixture in
   Task 14 Step 4, after the merge. Through the whole phase the test is a green skip. That is a
   defensible sequencing choice, but the plan should say the Task 14 controller step is where the
   assertion actually first runs, and that a failure there is a finding against Task 4, not a
   verification transient.
4. **The taker-side canonical rule over historical tape.** Rows written before the hotfix carry
   `taker_side` defaulted to `"yes"` whenever the field was absent. Nothing flags or excludes that
   run range, and Task 4's `hits()` will read those defaults as real sides. The amendment protocol
   exists precisely for this; one line in Amendment 2 naming the pre-hotfix run range would close
   it.
5. **Cutover partition routing.** Task 2b's test asserts a new row lands in the cutover-week
   partition. It does not assert that a row with `ts < bound` (the REST trade backfill case the
   plan's own Verified data facts call out) routes into the attached legacy partition. The design is
   correct, since the legacy partition is `(minvalue, bound)`; the test just does not prove it.

---

## E. Under-specification a sonnet implementer would guess wrong on

Beyond the five BLOCKING items, which are all under-specification of the same kind:

1. **Task 6, step 3:** "`MarketNow` per market from the newest `market_gap_snapshots` row and its
   fair value (`fair_ts = fair_values.created_at`, `staleness_s = now - newest_book_ts`)." The
   second half redefines `staleness_s` at the executor clock; see B(1). An implementer will do
   exactly what it says.
2. **Task 6, step 5:** "`Skip` inserts through `uq_skip_once` with `on_conflict_do_nothing`." No
   `kind` is given; see IMPORTANT-1.
3. **Task 5:** "cap gate for `apply_caps` variants gives `Skip(cap_<label>)`". Which label, when
   several are False? First in `CAP_LABELS` order is the sane answer and matches
   `_decide`'s first-False rule at `harness/strategy/run.py:262-267`, but it is not written.
4. **Task 4:** "`state.queue_remaining is None` (no book yet) means no fills and no cursor advance."
   Task 6 then sets `queue_ahead_at_place` from the first book. It is not stated whether the prints
   and deltas that arrived in the no-book window are replayed from `placed_at` (optimistic) or
   discarded (D7 says "start simulation at the first book", which implies discarded). One sentence.
5. **Task 6:** "on the first clean book after a dirty stretch `queue_remaining = min(queue_remaining,
   resting_at(side, prob))` and the cursor jumps to that snapshot's id." Which cursor, the watched
   one, the `nw_*` one, or both? D6 does not say either.
6. **Task 10:** table 3 must report "1 m and 5 m markouts come off the WS mid" and "fair markouts
   only when `fair_changed`" (addendum §3). Task 10's t3 description does not carry either rule, so
   the report would print fair markouts at 1 m that the addendum says are meaningless.
7. **Task 12:** `days_to_ceiling` is computed "from housekeeping notes of the trailing 7 days" while
   `housekeeping` is what writes those notes. The bootstrap ("fewer than seven days is `partial`")
   is stated; the very first run, with zero notes, is not.
8. **Task 2's `order_episodes` view:** "consecutive orders on one key linked by
   `cancel_reason = 'reprice'` share `episode_id = first order id`". Recursive linking over a chain
   is a recursive CTE; the view is described in one clause. Table 3 and gate criterion 6 both depend
   on episodes being right.

---

## F. Real orders, secrets, NAS access, destructive DDL

- **Real orders: absent.** Global Constraints forbid any code path that sends an order, quote or
  RFQ answer; `app-exec` has no network client; the only additions under
  `harness/venues/kalshi/` are `fetch_market` and `fetch_series`, both GET. Task 6's
  `test_compose_app_exec_block_has_no_volumes_or_kalshi_env` enforces the compose half. Addendum §10
  item 5 says "gains only `fetch_market` (GET)" and omits `fetch_series`; correct that sentence.
- **Secrets: absent.** No brief reads under `secrets/`; features switch on `Path.exists()`;
  `app-exec` mounts no `KALSHI_*_FILE`.
- **NAS access from an implementer: absent.** Global Constraints state it explicitly ("never run
  ssh, scp, `make deploy-nas`, `make deploy-nas-app`, `make status-nas` or docker; tests run only
  against localhost:5433"). Every `ssh` string in the plan is inside a step marked "(controller)" or
  inside verify.md text the controller runs (Task 1 Step 5, Task 2b Step 5, Task 4b Step 5, Task 14
  Steps 2 and 4).
- **DROP / RENAME / TRUNCATE on the production database:** the only production-side DDL is Task 2b's
  `partition-bulk-tables` (table, index and sequence renames plus `ATTACH PARTITION`), run by the
  controller under U3. See the authority note in section B. `drop_schema` and the conftest
  `truncate ... restart identity cascade` act on `harness_test` at localhost:5433 only; the plan's
  Global Constraints sentence forbidding TRUNCATE would read better with that carve-out stated, so a
  reviewer does not flag the fixture.

---

## G. Conformance (addendum §10) and Decisions (§9)

All eleven conformance items are present. Ten are true against the plan. Two need a correction:

- **Item 5** says `harness/venues/kalshi/` "gains only `fetch_market` (GET)". Task 3b also adds
  `fetch_series` and a `status` parameter on `fetch_markets_all`. Both are GETs, so the property
  holds; the sentence does not.
- **Item 4** says "no DROP, RENAME of a column, TRUNCATE or ALTER TYPE". True for production. The
  test-fixture `drop_schema` and per-test truncate are not carved out, and the primary-key changes
  on `orderbook_events` and `venue_trades` (achieved by the rename-and-recreate path) are a
  constraint change worth naming explicitly.

Item 2 (no new dependency or outbound host) is verified: the t distribution, BH, Holm, shrinkage and
Kaplan-Meier are all stdlib code in Task 10, and no task touches `pyproject.toml`.
Item 3 is verified against the pre-registration table: the six hashes in Task 4b's
`test_six_committed_yamls_hash_to_the_recorded_ids` match the record exactly.
Item 9's verify.md list matches Task 14 Step 2, subject to IMPORTANT-3.

All fourteen decisions in §9 (D1-D14) carry a cost if wrong and a reversal, and each lands in a
task: D1 to T11, D2 to T4b, D3 to T2/T6, D4 to T2b (with the caveat in IMPORTANT-5), D5 to T7, D6
and D7 to T6, D8 to T10, D9 to T12, D10 to T2/T4b, D11 to T1/T3, D12 to T9, D13 to T2b, D14 to T12
(with the F45 caveat). The §9 preamble's list of inherited rulings is accurate.

One §0 hygiene note: the addendum opens with "every difference [from the v2 spec] is listed in §0".
Three differences are made in later sections without a §0 entry: the ROI form (spec §10 says
`(1/p_used)/(1/p_benchmark) - 1`, §3 says `p_bench/(p_used + fee) - 1`), the markout anchors and the
`0m` horizon (spec §10 says markouts run "from placement time"), and spec §6.7's "A `no_veto`
secondary is mandatory" (handled by R2 and §8, not by §0). Three sentences in §0 close it.

---

## Summary of what to fix before dispatch

Blocking, in dispatch order:

1. Task 1: add the three Odds cadence settings to `harness/config/settings.py` in Task 1's Files, or
   move Task 3b item 9 ahead of Task 1.
2. Task 2: define `venue_markets.match_key` with its composition and name Task 3b as its writer;
   define `MarketNow.matched`.
3. Task 4 and Task 6: state that `simulate_fills` does not mutate the caller's `BookState`, and that
   the copy is seeded at the order's cursor.
4. Task 8 or 9, and Task 11: add a per-order CLV path covering all benchmark types, and say which
   table the gate reads.
5. Task 7: state the `Settler` stage-registration pattern, and which task owns each stage.

Important, before the tasks that touch them:

6. Task 6 step 3 and Task 11 criterion 8: record the pricing-time `staleness_s` at placement (U1,
   R22).
7. Task 5 and Task 6: give `Skip` a `kind` so `cap_gate` events exist.
8. Task 6 step 4: write `has_print`, the three fee columns and the `snapshot_cross` fill; add the
   two missing tests.
9. Task 14 Step 2: rewrite as a delta against the current `verify.md`, including the 800 GB to 2 TB
   change.
10. Task 9 or 10: name the source for table 3's `queue_model union snapshot_cross` column.
11. Task 2b: guard the REST trade writer against a millisecond mismatch with the WS row.

Minor, worth one sentence each: the `runs.notes` non-`linear_cent` warning (F44); the F45 deviation
from R21; the §0.7 fifth deviation; the three §0 hygiene entries; conformance items 4 and 5; the
Task 2b rename authority ruling; and the eight under-specifications in section E.

---

# Fix round 1 (2026-09-07, re-review)

Scope: the three revised files plus the newly committed fixture
`tests/fixtures/tape_sample_lou_miss_2026-09-07T03.json` (commit `30db680`). Untouched text was not
re-reviewed. The writer's three controller items (13 tests and the committed fixture, 14 the gate
variant, 15 the four gate deviations) are checked with the twelve findings.

## Verdicts on findings 1-12 and the testability gaps

| # | verdict | where |
|---|---|---|
| 1 Task 1 consumes settings Task 3b creates | ADDRESSED | Task 1 Files now lists `harness/config/settings.py` creating `odds_alt_interval_near_s = 120`, `odds_alt_interval_far_s = 900`, `odds_monthly_credits = 100000`; Task 3b item 9 only wires them and adds `odds_alt_window_h`; addendum §6 Settings says "Task 1 creates them" |
| 2 `match_key` undefined | ADDRESSED | addendum §5 defines `venue_markets.match_key String(64)` as `f"{game_id}:{market_type}:{side_team_id or ''}:{side or ''}:{threshold or ''}"`; Task 2 adds the column, the `create_schema` backfill and `test_match_key_backfilled_by_create_schema`; Task 3b item 5 writes it after matching; Task 5 defines `matched` and the open-order check; Task 11 criterion 10 reads `venue_markets.match_key` |
| 3 `simulate_fills` mutates a shared book | ADDRESSED | addendum §2 opening sentence; Task 3 `copy()`; Task 4 "never mutating its arguments ... walks `book.copy()`" plus `test_simulate_fills_does_not_mutate_the_caller_s_book`; Task 6 steps 3-4 define the `base = cache.copy()` protocol and the `book_at(..., ts_of(cursor))` fallback, with `test_simulation_book_copy_leaves_the_cache_unchanged` |
| 4 gate needs seven benchmarks, `gap_outcomes` has four | ADDRESSED | addendum §3 and §5 add `order_clv(order_id, benchmark_type, ...)` PK `(order_id, benchmark_type)` over all nine types in the order's side space at `p_used = orders.prob`; Task 2 adds the model and rewrites the `clv` view over it; Task 8 adds `harness/settlement/order_clv.py` and the `order_clv` stage; Task 10 t2 reads it for executed variants; Task 11 criteria 3, 6, 7 read it, criterion 7 now reads `insufficient` for a type with no row; `test_criteria_read_order_clv_not_gap_outcomes` |
| 5 Task 7 imports later tasks' functions | ADDRESSED | Task 7 adds `StageResult`, `STAGES`, `register_stage`, `STAGE_MODULES` and `load_stages()`; a later task registers by appending its module name, so Task 7 imports nothing that does not exist. `insert_result_benchmarks` is now a Task 8 stage, not a call inside `run_settlement`. `test_stage_registry_runs_stages_in_registration_order_under_one_budget` |
| 6 placement-clock staleness contradicts U1/R22 | ADDRESSED | addendum §1 Place: `staleness_at_place`/`stale_allowance_at_place` "copied from the signal's `market_gap_snapshots` row: pricing-time values, never recomputed at the loop clock"; the executor's own quantity is `fair_age_s`. Task 5 `MarketNow` repeats it; Task 6 steps 3 and 5 carry the gap-row values; Task 11 criterion 8 reads `fair_values.staleness_s` through `signals.gap_snapshot_id`, with `test_staleness_median_uses_pricing_time_fair_values` |
| 7 `cap_gate` had no writer | ADDRESSED | addendum §1 Exposure; Task 5 adds `CapGate(intent_id, reason, blocking)` and `Skip(..., kind = "skipped")`, with the first-False-in-`CAP_LABELS` rule matching `_decide`; Task 6 step 5 writes `order_events(kind = cap_gate)` whether or not it blocked; `test_cap_gate_recorded_for_every_variant_and_blocks_only_apply_caps` and `test_cap_gate_event_written_for_a_non_apply_caps_variant` |
| 8 Task 6 never wrote `has_print`, the fee fields or the cross fill | ADDRESSED | Task 6 step 4 now writes all of them and persists `FillResult.cross` once per order; addendum §2 says which track owns it and how the unique key dedupes. Four new tests: `test_has_print_reads_100_percent_on_queue_model_fills`, `test_fill_row_carries_has_print_fee_fields_through_and_tape_source`, `test_crossed_order_gets_exactly_one_snapshot_cross_fill_with_no_position_or_ledger`, `test_nw_fill_writes_no_ledger_or_position_row` |
| 9 Task 14 Step 2 written against a stale verify.md | ADDRESSED | Step 2 is now "a delta against the file at commit b25c91c", naming what already exists and must not be duplicated, then six lettered edits. (a) carries the DB-size row from 800 GB to the 2 TB ceiling, (e) reconciles the Odds-credit band to the U1 tier |
| 10 no anchor for `queue_model ∪ snapshot_cross` | ADDRESSED | addendum §0.15 and §3 add the `cross_fill` anchor; Task 9 computes it and names it as table 3's source; Task 10 t3 reads the three anchors; `test_fill_nw_fill_and_cross_fill_anchors` |
| 11 ms truncation does not dedupe a trade | ADDRESSED | addendum §5 and Task 2b: the REST writer skips a trade whose `(venue, trade_id)` already exists, through a new non-unique `ix_trades_venue_trade_id`; `test_rest_trade_already_recorded_by_ws_is_not_inserted_again` uses a `created_time` one millisecond off the WS `ts_ms` |
| 12 minors (F45, F44, §0.7, §0 hygiene, conformance 4-5, rename authority) | ADDRESSED | F45's `fee_model_for` moves into Task 2 with `Decimal` multiplier and a raise on `flat`/unknown (D14 rewritten, R21 satisfied); F44's `runs.notes` non-`linear_cent` counter is Task 3b item 5; §0.7 now says "exactly four deviations" and folds staleness and settlement into "nothing else deviates"; §0.14, §0.15 and §0.16 add the ROI form, the markout anchors and the no-veto control; conformance 4 and 5 corrected; D15 records the rename authority and commits the controller to asking for a one-line yes before Task 2b Step 5 |
| T1 `has_print` never asserted | ADDRESSED | Task 6 `test_has_print_reads_100_percent_on_queue_model_fills` |
| T2 `no_watcher` isolation untested | ADDRESSED | Task 6 `test_nw_fill_writes_no_ledger_or_position_row` |
| T3 premise test always skips | ADDRESSED | Task 4 reads the committed fixture, "it never skips ... it must pass before Task 4 merges" |
| T4 pre-hotfix `taker_side` range | ADDRESSED | Task 1 Step 5 records the pre-hotfix run-id range in Amendment 2, excluded from fill and flow analysis until re-scored |
| T5 cutover routing untested | ADDRESSED | Task 2b `test_row_with_ts_before_bound_routes_to_the_legacy_partition` |

## Fixture check

`tests/fixtures/tape_sample_lou_miss_2026-09-07T03.json` is a real 60-second slice: 616 deltas, 196
prints, taker sides 65 yes / 131 no, 157 of 196 counts fractional, and a snapshot whose `raw` carries
`yes_dollars_fp` and `no_dollars_fp`. It supports every use Task 4 puts it to (shape, fractional
counts, side-generic fills, the F1 premise). The `note` is accurate that the snapshot is 95 minutes
older than the window, and Task 3's book-rebuild tests correctly use `kalshi_orderbook.json` instead.

One consequence the plan's wording misses. Checked against all 196 prints:

| framing of the premise test | prints matched |
|---|---|
| hit (resting) side, price = `price_on_side(print, that side)` | 196 of 196 |
| taker side, price = the print's raw `yes_price` | 0 of 196 |

A print with taker side `yes` at `yes_price = 0.03` pairs with a `no` delta of `-156.06` at price
`0.97`. The premise holds, and the addendum's queue-model rule ("a delta on side `s` at price `q`")
is correct in the order's own side space. But Task 4 Step 1 and addendum §7 both say the delta is "at
the same price", which read literally means the print's `yes_price` and fails on every print. Under
the old wording that produced a skip; now the test must pass before Task 4 merges and a violation is
escalated to "a controller ruling on the fill rules". See New breakage A.

## New breakage in the fix area

- **A (IMPORTANT), Task 4 Step 1 and addendum §7.** The premise test's "a delta on the hit side at
  the same price" is ambiguous and, on the literal reading, fails 196 times out of 196 against the
  committed fixture, blocking Task 4 and triggering a spurious ruling that the fill rules are wrong.
  Fix: "a delta with `side = opp(canonical taker side)`, `price = price_on_side(print, that side)`
  and `delta < 0` with `abs(delta) >= count` within 1 s", and add the worked example (a taker-yes
  print at 0.03 pairs with a `no` delta at 0.97). Same sentence in §7.
- **B (IMPORTANT), addendum §0.11, §4, §9 D1 and the record's Amendment 3 addition.** Moving the
  go-live property onto `sharp_two_sided` is a change to what every gate criterion is computed over,
  which R1 and roadmap invariant 2 reserve to a dated user decision, and U2's text does not mention
  the gate. The stated justification ("U2's purpose is that the gate counts fills on the side retail
  flow hits") is an inference about intent. The record's own reversal clause concedes the user owns
  the choice in the other direction. It also shortens the gate's window: `sharp_two_sided` registers
  before 2026-09-16, so criterion 1's "≥ 150 fill events across ≥ 40 games" would be measured over
  roughly two of the three weeks. Keep the machinery, which is good and reversible (one row per
  variant, `gate_reports.gate_variant`, `Settings.gate_variant`), ship the default as
  `sharp_direct`, and put the switch to the user as a one-line gate.
  Mechanism note: `Settings.gate_variant` defaults to `sharp_two_sided` from Task 2 while §4 says
  "before amendment 3 the primary's row is the gate row", and Task 11 leaves the reconciliation to
  the controller. Between Tasks 2 and 4b no row would be marked and verify.md's new "exactly one
  with `gate_variant = true`" fails. Resolve it in code: the gate row is `Settings.gate_variant`
  when that variant has a row, else the primary.
- **C (IMPORTANT), Task 13.** "in replay mode books come from `book_at(session, ticker, now)`"
  contradicts Task 6's new contract that `simulate_fills` receives the book as of the order's
  cursor. Applying the window's deltas on top of the book at `now` re-introduces exactly the double
  application finding 3 removed, and diverges from the live path, so
  `test_replay_execute_reproduces_live_orders_and_fills_exactly` fails on `snapshot_cross` fills.
  Fix: replay uses `book_at(session, ticker, ts_of(track cursor))` for the simulation and
  `book_at(..., now)` only for `MarketNow`.
- **D (MINOR), Task 2b.** `ix_trades_venue_trade_id` is added by `create_schema` but is not in the
  "created only when the table is in `pg_partitioned_table` or empty" guard (which names only the
  three `orderbook_events` indexes) and not in step (2)'s index-rename list. At the Task 2b deploy,
  `init-db` runs before the CLI, so the index is built non-concurrently on the live `venue_trades`,
  which is the operation roadmap invariant 5 and F65 make a gate; the parent creation can then
  collide on the name. Add it to both lists.
- **E (MINOR), Task 6 step 5.** The `Place` column list never sets `tape_cursor_event_id` or
  `nw_tape_cursor_event_id`, so at the next step a new order's cursor does not equal
  `base.last_event_id` and the fallback runs with a NULL cursor. Set both to the book's
  `last_event_id` at placement.
- **F (MINOR), Task 8 Step 2.** The pytest line runs only `tests/test_benchmarks.py`; Step 1 also
  specifies tests in `tests/test_order_clv.py`.
- **G (MINOR), Task 5.** `matched = match_status in ('matched', 'fuzzy', 'manual')` is looser than
  `CONFIDENT_MATCHES` at `harness/strategy/run.py:49`. A market downgraded from `matched` to
  `fuzzy` keeps its `match_key` and its `matched` flag, so no `unmatched` cancel fires. Either drop
  `fuzzy` or say why it is deliberate.
- **H (MINOR), Self-review.** Three lines are stale after the fixes: "`insert_result_benchmarks`
  (T8) by T7's job" (now a registered stage), "the cadence settings (T3b) by T1" (reversed), and
  "the four actions" (there are five).

## Out-of-scope observations

- The `match_key` composition uses `or ''`, so a `threshold` or `side_team_id` of exactly 0 renders
  as empty. Kalshi thresholds are at k.5 so the collision is unreachable today; `is None` would be
  safer than `or`.
- Task 1 Step 5 lists `odds_alt_window_h` among the settings frozen in Amendment 2, but Task 3b
  creates it. The 36-hour window is a true fact at Task 1's deploy either way.

---

# Fix round 2 (2026-09-07, re-review)

Scope: the eight fix-round-1 items and the two out-of-scope notes, plus a breakage scan of the four
areas the fixes touched. Untouched text was not re-reviewed. User decision U5 (2026-09-07) settles
the gate variant, so item B is verified against U5 rather than against my earlier recommendation.

| item | verdict | location |
|---|---|---|
| A premise wording | ADDRESSED | Task 4 Step 1: "a delta with `side = opp(t)`, `price = price_on_side(print, opp(t))` and `delta < 0` with `abs(delta) >= count` within 1 s", with the worked example (taker-yes print at 0.03 for 156.06 pairs with a `no` delta of -156.06 at 0.97) and the 196-versus-0 counts. Addendum §7 carries the same predicate and example |
| B gate variant | ADDRESSED against U5 | U5 is cited as a user decision in addendum §0.11, §4 and §9 D1, and in the record's "Amendment 3 placeholder, second addition", which states that U5 supersedes the earlier controller inference. Machinery: `Settings.gate_variant` defaults to `sharp_direct` (Task 2, addendum §6); `evaluate_all(session, now, variant_ids, gate_variant)` marks the named variant's row and falls back to the active primary when it is not registered, so exactly one row is marked at every evaluation; Task 4b Step 5 flips the setting in the same deploy and Amendment 3 records the first switched evaluation. Tests `test_gate_variant_row_is_marked_and_primary_reported_beside_it` and `test_gate_row_falls_back_to_the_primary_when_the_named_variant_is_not_registered` |
| C replay book contract | ADDRESSED | Task 13: "in replay mode each order's simulation receives `book_at(session, ticker, ts_of(track cursor))`, the same book-at-the-cursor contract as Task 6 (never the book at `now`, which would apply the window's deltas twice)", with `MarketNow` reading `book_at(..., now)` |
| D Task 2b index guard | ADDRESSED | the guard now reads "the three new `orderbook_events` indexes and `ix_trades_venue_trade_id` created only when their table is in `pg_partitioned_table` or empty", and step (2)'s rename list gains "`ix_trades_venue_trade_id` if present" |
| E Place sets no cursor | ADDRESSED | Task 6 step 5: `tape_cursor_event_id` and `nw_tape_cursor_event_id` "set to the anchor book's `last_event_id` (NULL for a `no_book` order until its first book)" |
| F Task 8 Step 2 | ADDRESSED | `.venv/bin/pytest tests/test_benchmarks.py tests/test_order_clv.py -q` |
| G `matched` admits fuzzy | ADDRESSED | Task 5: `matched = match_status in CONFIDENT_MATCHES` (`matched`, `manual`), citing `harness/strategy/run.py` |
| H stale self-review lines | ADDRESSED | "the five actions (T5)"; "`insert_result_benchmarks` (T8 stage `result_benchmarks`, run through T7's registry)"; "the cadence settings (T1) by T3b and Amendment 2" |
| I(i) `match_key` falsy zero | ADDRESSED | composition is now `f"{game_id}:{market_type}:{'' if side_team_id is None else side_team_id}:{'' if side is None else side}:{'' if threshold is None else threshold}"` |
| I(ii) `odds_alt_window_h` ownership | ADDRESSED | Task 1 Files creates `odds_alt_window_h: int = 36` alongside the other three; Task 3b item 9 now only wires `alternates_due` and the credit alarm to "the settings Task 1 created" |

## New breakage in the fix area

None. The four scanned areas are internally consistent:

- **Gate row.** One owner (`Settings.gate_variant`), one default (`sharp_direct`), one fallback
  (the active primary, of which `MAX_PRIMARY = 1` guarantees exactly one), one flip point (Task 4b's
  deploy), and one record entry. Addendum §0.11, §4, §6, §9 D1, Task 2, Task 4b, Task 11 and the
  record all state the same rule, and the fallback test covers the window between Tasks 2 and 4b
  that the previous round left failing.
- **Premise wording.** Task 4 and addendum §7 now carry the identical predicate and example.
- **Replay book.** Task 13 mirrors Task 6's contract verbatim, including the reason, and separates
  the `MarketNow` book from the simulation book.
- **Partition indexes.** `ix_trades_venue_trade_id` is in both the creation guard and the rename
  list, so `init-db` cannot build it non-concurrently on the live table and the parent cannot
  collide on the name.

No new under-specification was introduced. `ts_of(<event id>)` is used undefined in both Task 6 and
Task 13, but identically in both, so the two paths cannot diverge on it.

## Verdict

READY. All twelve original findings, the five testability gaps and all eight round-1 items are
addressed, the two out-of-scope notes were taken as well, and the one governance question is now a
dated user decision rather than a controller inference.
