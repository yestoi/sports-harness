# Phase 6D design addendum: sustained evaluation (scheduled versus completed, budget isolation, the holding/capacity policy, the coverage contract)

Date 2026-09-13. **Revision 2** (revision 1, 2026-09-13, reviewed in `.superpowers/sdd/results/design-6d-review.md`). Author: autopilot (design
author, opus); revision 2: review findings applied, controller rulings 2026-09-13 (§Rulings). Amends
`docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md` (v2) §4.1, §4.2, §6.2, §7.2, §8.2, §9.2, §9.5, §11, §13 and §14 for milestone 6D.
**Approval: the roadmap's standing authorization dated 2026-09-07** (`docs/superpowers/autopilot/roadmap.md`, "Standing authorizations":
"Brainstorm, plan, and execute phases 4, 5, 6 without waiting", decisions from the tables or the model's judgment, each recorded in §8) under
decision **U8** (2026-09-11, the phase 6 programme). Consumes the roadmap's Phase 6 section and its **pre-loaded decision 4** (6D), whose acceptance
paragraph is this milestone's acceptance verbatim; the 6D row of the Phases table ("instrumentation none; the policy comparison needs 6B"); the
deferred items at roadmap "6D now: define the eligible denominator, choose the design from stage timings" and the 6C review's deferral of two funnel
units; carried fixes **46**, **48**, **51** and the coverage half of **55**; the 6C addendum §0.3, §0.11, §0.12, D11 and rulings C1/I6; the 6B
addendum's 6D carve-outs (§6: stepping a replay at recorded loop instants is 6D's; "6B never closes a counterfactual track"; the policy comparison
after 6B; IM-6's suspended 2 % parity verdict).

**Posture.** Paper throughout. **R1 stands**: no gate criterion, threshold, benchmark or BH family, cell grid, success threshold, confirmation cut-off
or eligibility rule changes here, and no registered `variant_id`, `MAX_PRIMARY`, `MAX_SECONDARY` or file under `harness/variants/` is touched
(invariants 1 and 2). The frozen ids stand; **anything registered after Mon 2026-09-21 09:00 CT is exploratory and is labelled so** wherever it is
reported. 6D adds measurement and declares one policy baseline; it adopts no alternative policy.

**Live facts relied on** (controller's read-only production reads, `.superpowers/sdd/plan-next-phase6d-live-facts.txt`, 2026-09-13 10:56 CT, runtime
`ebf0953`, treated as data): (a) run 14307's `notes->'pricing'` carries the six stages in fix 48's order with `elapsed_ms` 671 / 552 / 783 / 708 / 700
/ **8,866**, `budget_s: 20`, `gaps: 724`, `no_sharp: 2140`, `order` leading `sharp_two_sided` then `sharp_direct`, and per-variant candidate/rejected
counts (9-166 candidates, 558-1,376 rejected); (b) in 24 h, 369 runs carried a pricing block (39 with `budget_exhausted`, 330 without) and 946 did
not; `runs` shows 844 `skipped`, 448 `ok`, 16 `degraded` (all the pre-fix-45 `normalize` `QueryCanceled` warning, 2026-09-12 17:01-19:47Z), 7
`running`; (c) `signals` 159k-316k rows an hour; (d) `rfqs` zero arrivals in 24 h (listener off pending fix 46); (e) `check_results` today:
`duplicate_trades` and `intents_without_order_or_skip` `skip: timeout`, and `fills_outside_placement_window` 154, `markouts_at_after_horizon` 154,
`game_score_went_down_24h` 13 failing (data defects carried elsewhere, not 6D's); (f) `metric_samples` carries `pricing.*`, `exec.*`, `recorder.*`,
`ws.*`, `serve.snapshot_ms`; (g) the settle job runs 18 times a day, 214 s average, 630 s max. Journals 136, 144, 154, 160, 164 and 165 supply the
anomalies behind fixes 46, 48, 51 and the normalizer backlog. Nothing in that data was read as an instruction.

## 0. Amendments to v2 and to the roadmap text

- **0.1 (§4.1, §4.2, §13) Coverage becomes recorded data, not an inference from `runs.notes`.** v2 records what a tick *did* (`runs.notes`, §4.2's
  "remaining ladder fetches are skipped and counted on `runs`"); it never records what was *due*. 6D adds one bounded table, `coverage_samples`, whose
  rows are written by the writer that made the decision, at the moment it made it. Reason: the acceptance sentence ("every scheduled eligible
  primary/gate evaluation completes inside its freshness window **or leaves an explicit reason and interval**") cannot be evaluated from an
  after-the-fact scan, and 6C ruling C1 already conceded that t13's coverage rows are "what the notes can say, not a table-level truth".
- **0.2 (§4.2) The two `budget_exhausted` flags are different quantities and are named apart.** `runs.budget_exhausted` is the *collection* verdict
  (`tick.py:939`: `skipped_trades or skipped_ladders or skipped_alternates`), while `notes->'pricing'->'budget_exhausted'` is the *pricing* deadline.
  The live facts show 164 runs with the first flag against 39 pricing blocks with the second in the same 24 h. Every 6D surface and query names which
  one it means; no flag is renamed or dropped (invariant 5, additive only).
- **0.3 (§4.1) "Scheduled" is defined per domain before the run.** *Collection*: for each source the recorder consults, the cadence in force at the
  tick (`cadence.interval_for`, `tick.cadence_in_force`) and the source's own due test (`Recorder._due` over `source_state`) decide due-ness; the
  scheduled set is the due set, and a tick whose cadence is `None` (quiet hours, `cadence.py:22`) schedules nothing and is recorded as such rather
  than as a miss. *Evaluation*: the scheduled set is the matched, priceable venue markets the run's own stage 1 and stage 2 enumerated, crossed with
  the active variants in `pricing_order`. Scheduling is recorded in **two phases** (ruling C2): the writer inserts one `outcome = 'scheduled'` row per cell the
  moment the set is enumerated and closes it with a completion row for the same cell in the same tick. A tick that died, a stage never entered and a
  pricing block that raised (`tick.py:934-937` catches the exception, rolls back and records only a warning) therefore all leave scheduled rows that no
  completion row closes - the failure mode the contract exists to detect - and §3 row 2's reconciliation query names them by cell.
- **0.4 (§6.2, §9.2) The six latency quantities are defined as differences of two named timestamps** (§1.2). Five already exist in columns or metrics;
  only `signal_to_order_ms` and the tape-continuity coverage fraction are new, and both are derived, not re-measured.
- **0.5 (§9.6, §11) The scored-tick denominator is retired as a coverage statement.** Roadmap decision 4: "count scheduled work that produced no fair,
  gap or signal row (the scored-tick denominator hides missing stages)". Coverage is reported as completed/scheduled per stage, per §1.1's cells; the
  existing tick counts stay where they are and are relabelled, never deleted.
- **0.6 (§9.6) The eligible game-day denominator for the normalizer question is defined before any "10 % exhaustion" claim.** The 109/243 figure was
  over *priced runs* on a day with 2,220 runs and 562 non-skipped; §1.3 fixes the denominator as **priced runs** (runs carrying a `pricing` block) in
  the stated window, publishes the other two counts beside it, and requires stage-timing evidence before a normalizer process or job split is even
  designed. A normalizer process alone proves nothing about contention or cost (roadmap decision 4) and none is adopted here.
- **0.7 (§4.1, fix 48) Fix 48's stage order is already code at `main` and is not re-implemented.** `harness/strategy/pipeline.py` defines
  `STAGE_NAMES = ("fair_direct", "gaps_direct", "variants_direct", "fair_derived", "gaps_derived", "variants_derived")` and records each stage's
  `elapsed_ms` and `status` in `notes->'pricing'->'stages'`; the deployed build writes them (live fact (a)). 6D's component is the **measurement and
  the remaining budget-isolation work** (§1.5): what `stages[]` lacks, the duplicate work the 8,866 ms stage is spending, and the closing of fix 48's
  own acceptance clause.
- **0.8 (§6.4, §9.6) Stage 6 stops re-scoring direct-only variants over derived rows.** `price_and_signal` re-scores every variant over `all_rows`
  whenever stage 5 added gaps. A variant whose `sources_allowed` is `[direct]` rejects every derived row on the `source_allowed` label -
  or earlier, on `has_fair`, for the rows that carry no fair value at all (§0.12) - since `_decide` walks `LABEL_ORDER` with `has_fair` and
  `source_allowed` first (`harness/strategy/run.py`), so the second pass can add no candidate and no cap consumption (only a `candidate` decision
  mutates `StrategyState`); it adds only rejected rows. That is the duplicate work decision 4 asks to remove, and it is the 8,866 ms in live fact (a) and a
  large share of the 159k-316k signals an hour. The suppressed rejections are counted in `notes->'pricing'->'rescore_suppressed'` and in
  `coverage_samples`, so the population change is visible, not silent. **No candidate set changes** - the test in §5 proves it by equality on a fixture
  with derived rows - but the *stored rejected population* does, so two things follow. First, this amends fix 48's frozen-baseline parity contract:
  §1.5(d) states the amendment and decision D12 records it. Second, **the deploy instant is a labelled measurement boundary** (ruling I11): every
  series built on the stored rejected population - `pricing.rejected{variant,reason}` (`tick.py:188-193`), the weekly report's t12 rejection tables,
  Floor's `rejected` sum and fix 55's 54,435-a-day number - steps at it, so that instant is journaled, printed in t14's note and pinned in §3 row 5,
  with `rescore_suppressed` named as the bridge across it. No reader compares across the boundary silently. Nor is all 8,866 ms duplicate work: stage 6
  also re-loads every gap row (`_load_gap_rows`, `pipeline.py:418`) and scores `sharp_plus_derived` over the full universe, both of which remain
  (§1.5(b) states the expected residual).
- **0.9 (§9.2, §9.5) The holding and capacity policy is written down as a baseline, not changed.** §1.6 states the current numbers exactly, from
  `Settings` and the registered YAMLs read (never edited). Every alternative named by decision 4 is compared against that baseline on equivalent tape
  and resources **after 6B**; the selection is the user's dated decision (§0.15), and the selected policy is registered and versioned as a new hashed
  `config_history` record or a new variant id by amendment before any prospective period - never as an edit to a registered id.
- **0.10 (§7.2) The weekly report gains table `t14` "coverage contract", rendered after 6C's t13.** t13 stays exactly as 6C built it; t14 carries the
  contract's rows from `coverage_samples` only, with one `(item, value, unit, note)` row per contract clause. `TABLE_KEYS` gains `t14`;
  `RENDER_ORDER` places it after `t13`; `IDENTITY_COLUMNS` maps `t14` to `item` through a named `_T14_COLUMNS` constant, which
  `test_identity_columns_match_every_table_s_real_first_column` requires.
- **0.11 (dashboard §2.2; 6C §0.11, D11, ruling I6) The two deferred funnel units are delivered as episodes, not as distinct scans.** "Unique
  candidate opportunities" and "distinct intent episodes" become `opportunity_episodes` and `intent_episodes` rows maintained per run (§1.7), so the
  count is an indexed `count(*)` over a time range instead of the `distinct` over `signals`/`intents` that fix 31 removed. 6C's labelled counts keep
  their names and their units; the new keys sit beside them.
- **0.12 (fix 55, coverage half) The `has_fair` volume is a coverage number.** 54,435 rejected rows per variant per 24 h carrying `fair_p` null and
  `rejection_reason = has_fair` (journal 160) are markets with **no fair value at all**: they are counted in §1.3's missing-stage denominator as
  `no_fair` scheduled-but-not-completed work, split by `market_gap_snapshots.no_fair_reason` (`unmapped_market_type`, `no_sharp_line`,
  `pricing_error`). Because §1.5(b) stops storing those rejected rows, the count is published from
  `market_gap_snapshots.no_fair_reason` and `coverage_samples` rather than from `signals`, and the signal-level series ends at the deploy instant
  (§1.7(d), ruling I3). The label half of fix 55 merged at `940fd6d` and is not reopened.
- **0.13 (§8.2, §14, fix 46) The RFQ listener yields to the executor and its stored rows are capped.** The listener is off in production
  (`RFQ_LISTENER_ENABLED=0` since 2026-09-12 00:27 CT); 6D's deploy is the first that may leave it on, and only with §1.8's guard, cap and metrics in
  place. The control is the **Omarchy host `.env` under `/srv/sports-harness`** that `make deploy-omarchy` reads: `deploy/omarchy/host.env` carries no
  `RFQ_LISTENER_ENABLED` key at all, and `deploy/nas.env:39`'s `RFQ_LISTENER_ENABLED=1` is a historical note from the retired NAS, never a writer here
  (ruling I12). The setting **fails open** - `Settings.rfq_listener_enabled` defaults to `True` (`harness/config/settings.py:144`) - so an `.env` that
  omits the key starts the listener, and the effective value is read back out of the running image after every recipe (§4.1) rather than assumed from
  the file. Until §1.8 is deployed the state.md rule stands: every deploy sets that `.env` back to `0` after the recipe and the read-back proves it.
- **0.14 (§13, fix 51) The three integrity checks are bounded to the window they judge.** No threshold, no statement timeout and no check's pass/fail
  rule moves (gate 13, invariant 2 untouched: these are not gate criteria). `duplicate_trades` is bounded to the 24 h it judges *inside* the partition
  it already names, and its verify.md text is amended to the same statement, exactly as fix 51's "Change" column instructs. Today's fourth skipping
  check, `intents_without_order_or_skip`, is bounded in the same component by an additive index, not by a widened timeout.
- **0.15 Needs the user's dated decision** (neither blocks a task; both are designed so the decision can be taken on evidence afterwards):
  **(a) Policy adoption.** Which holding/capacity policy governs the prospective period - the stated baseline, or one of the six alternatives - is a
  measurement-affecting choice and therefore the user's, per decision 4 ("the selected policy is registered and versioned before its prospective
  period") and R1. Exact question: *"6D has measured the baseline against the six alternatives on identical tape and resources. Which policy do you
  adopt for the prospective period, and from what date?"* 6D builds the comparison and publishes the table; it adopts nothing.
  **(b) The 6C funnel-unit deferral.** The roadmap's User-side TODO of 2026-09-11 asks whether the deferral of unique candidate opportunities and
  distinct intent episodes from 6C to 6D is accepted. 6D delivers both (§1.7) whatever the answer; the question remains only about whether 6C may
  leave `planned`. Exact question: *"Do you accept that those two funnel units were delivered in 6D rather than 6C?"*

## 1. Components

Each names the roadmap bullet or spec section it implements, its files, what it depends on, the tests that prove it, and the independently calculated
expected result it is judged by. §1.1-§1.7 are decision 4's bullets in order; §1.8 and §1.9 are carried fixes 46 and 51; fix 48 is closed inside §1.5
and fix 55's coverage half inside §1.3 and §1.7.

### 1.1 Scheduled-versus-completed instrumentation (decision 4 bullet 1; v2 §4.1, §4.2; §0.1-§0.3)
One additive table, `coverage_samples`, written by the recorder tick and by `price_and_signal` through one helper,
`harness/ops/coverage.py::record(session, run_id, domain, rows)`, where `rows` is a list of `(cell, outcome, n, overdue_ms)` tuples written in one
multi-row insert (the `SIGNAL_INSERT_CHUNK` pattern, `pipeline.py:52`). A **cell** is `(sport, ttk_bucket, feed, market_type, variant_id)`, each
nullable; `outcome` is `scheduled`, `completed` or a reason name in `COVERAGE_CLASS_OF` (§1.4); `overdue_ms` is the interval between the scheduled
instant and the moment the row was written, null on `scheduled` and on `completed` and **required** on every other outcome.
*Two-phase writing* (ruling C2): `record` is called twice per domain per tick. The first call writes the **scheduled set** - one
`outcome = 'scheduled'` row per cell, `n` = the units due in it - at enumeration, before the work is attempted: in the recorder once `Recorder._due`
has resolved each source against `source_state` and the cadence in force, in the pipeline once stages 1 and 2 have enumerated the priceable venue
markets and `pricing_order` the active variants. The second call, after the work, writes one row per `(cell, outcome)` with its count and its interval.
Scheduled is therefore a recorded fact and never the sum of the completions, so a stage never entered, a pricing block that raised and a tick that died
are all visible as scheduled rows nothing closes; §3 row 2's reconciliation query is the read that names them, and its result is the contract's "zero
unexplained omissions".
*Collection domain*: one scheduled row and one outcome row per `(source, sport)` per tick, `outcome` in `completed`, `not_due`, `cadence_none`,
`budget`, `http_error`, `skipped_trades`, `skipped_ladders`, `skipped_alternates` - the last three read from the counters `runs.notes` already carries.
`ttk_bucket`, `market_type` and **`feed` are null in this domain** (M7): a source is fetched for a sport, not for a feed kind or a market type, and §2's
invariant query does not constrain them.
*Evaluation domain*: rows per cell per outcome from the pricing pipeline: `completed`, `no_fair`, `no_gap`, `no_signal`, `budget_stage_skipped`,
`variant_skipped`. `ttk_bucket` is `lt20m | 20m_3h | 3h_36h | gt36h` (the boundaries already in the code: `min_ttk_min: 20` in every registered YAML,
the 3 h ladder window, the 36 h alternates window); `feed` is `market_gap_snapshots.feed_kind`; `market_type` is the venue market's type
(`moneyline | spread | total`), **not** a per-market identity (decision D2 below).
*Cardinality bound, and the cap derived from it* (ruling I6): the grain stays as decision 4 lists it; its cell count is bounded by 2 sports x 4
`ttk_bucket` values x 2 `feed_kind` values x 3 `market_type` values x 7 registered variants = **336 evaluation cells**. One scheduled row per cell plus
at most one row per `(cell, outcome)` over the six evaluation outcomes bounds a priced tick at `336 + 336 x 6 = 2,688` rows, so
**`COVERAGE_ROW_CAP = 3,072`** stands above the worst case and the cap is a backstop rather than the routine case (at 512 it would bind on every real
slate and `truncated` would become the normal outcome). The collection domain is bounded by 8 source families
(`harness/normalize/runner.py:17-18`) x 2 sports x (1 scheduled + 1 outcome) = 32 rows. On run 14307's actual slate (724 gap rows, seven variants, two
sports, three `ttk_bucket` values in play) the occupied count is ~252 scheduled and ~500 outcome rows, ~760 rows a priced tick, which is the number §2's
disk estimate uses. Above the cap the helper writes the truncated set plus one `outcome = 'truncated'` row and a `coverage.truncated` metric sample, so
a cap that binds is visible rather than silent. The helper runs inside its own savepoint and never fails a tick (`run_checks`' and
`telemetry.record_many`'s rule).
*Files:* `harness/ops/coverage.py` (new), `harness/db/models.py`, `harness/db/schema.py`, `harness/recorder/tick.py`, `harness/strategy/pipeline.py`,
`migrations/versions/00NN_phase6d_sustained_evaluation.py`, `tests/test_coverage_samples.py`. *Depends on:* §1.4, for `COVERAGE_CLASS_OF`'s outcome
names (M9: task 3 lands before task 4).
*Expected result, computed independently:* a seeded tick with two sports, three market types each and two variants enumerates
`2 sports x 3 market_types x 2 variants = 12` scheduled units and writes 12 `scheduled` rows. One market type has no fair value **in either sport** and
one of the two variants is skipped by the budget, so the completion rows account for those same 12 units as **4 `completed`, 2 `no_fair` and 6
`variant_skipped`** (ruling I4): the skipped variant loses all six of its cells, the missing fair costs the surviving variant both of its cells in that
market type, and four cells complete. 4 + 2 + 6 = 12, the reconciliation query returns no rows, and those three numbers are the expectation written by
hand into `tests/test_coverage_samples.py`, never by calling the helper twice.

### 1.2 The latency decomposition (decision 4 bullet 1; v2 §6.2, §9.2)
Six quantities, each a difference of two named timestamps, sampled per run into `metric_samples` (source `recorder` or `exec`) with the cell's labels:
| Quantity | Definition | The two timestamps | Where it lives |
|---|---|---|---|
| source quote age | now − newest book `last_update` | `fair_values.created_at` − the book's `last_update` | exists: `fair_values.staleness_s`, `pricing.staleness_median_s` per `feed_kind` |
| transport lag | our fetch minus the book's own stamp | `odds_snapshots.fetched_at` − `book_last_update` | exists: `fair_values.feed_lag_s` (`fair.py:285, 367`); new sample `pricing.feed_lag_s` p50/p95 per feed |
| fair-calculation age | how old the fair was when it was used | the consumer's `now` − `fair_values.created_at` | exists at the executor as `fair_age_s(now)` (`plan.py`); new sample `exec.fair_age_s` p50/p95 |
| signal-to-order delay | decision to placement | `orders.placed_at` − `signals.created_at`, joined through `intents.signal_id` | **new**, derived; sample `exec.signal_to_order_ms` per variant |
| clean resting seconds | resting time on a non-dirty book | `orders.dirty_seconds` against `[placed_at, min(cancelled_at, expiry)]` | 6B §1.5 owns the interval; 6D **reads** it and changes nothing (6B D7) |
| tape continuity | covered seconds per ticker-hour | `orderbook_events` `gap` rows (sid sentinel `ticker = ''`) against the hour | exists as counts (`ws.gaps`); new derived `ws.tape_covered_frac` per hour |
The bounded read behind each sample is stated with its index: `pricing.*` from the run's own rows by `run_id`, each read riding the `run_id`-leading
key the table already carries (`uq_fair_value_row`, `schema.py:141`; `uq_gap_run_market`, `models.py:327`; `uq_signal_key`, `models.py:368`; M5);
`exec.fair_age_s` and
`exec.signal_to_order_ms` from the loop's in-memory values (no query); `ws.tape_covered_frac` from `metric_samples` `(name, ts)`; the reporting reads
ride `ix_signal_variant_created (variant_id, created_at)` and `ix_fills_filled_at`. No read touches `raw_responses` or `orderbook_events` in a report
path (`checks.assert_no_tape_reads`' rule, extended by a test to the new report builders).
*Files:* `harness/recorder/tick.py`, `harness/execution/loop.py`, `harness/report/tables.py`, `tests/test_latency_decomposition.py`. *Depends on:* 1.1.
*Expected result:* on a fixture where a book stamped 12:00:00 is fetched at 12:00:30, priced at 12:00:45, signalled at 12:00:45 and placed at 12:00:52,
the four computable quantities read 45 s, 30 s, 0 s and 7,000 ms - each derived by hand in the test's docstring from the fixture's stamps.

### 1.3 The missing-stage denominator and the normalizer investigation (decision 4 bullets 2 and 3; "old item 4"; §0.5, §0.6, §0.12)
(a) **Missing stages.** For each scheduled evaluation unit (§1.1), the pipeline records which of the three stages produced nothing: no `fair_values`
row for the market's game and shape, no `market_gap_snapshots` row for the run and market, no `signals` row for the run, variant and market. Each is a
separate outcome, and `no_fair` carries `market_gap_snapshots.no_fair_reason` as its reason (`unmapped_market_type | no_sharp_line | pricing_error`),
which is the numerator behind fix 55's 54,435 rows per variant per day.
(b) **The eligible game-day denominator.** `harness/ops/coverage.py::eligible_runs(session, window)` returns the triple
`(total_runs, non_skipped_runs, priced_runs)` for the window, and **`priced_runs` is the denominator of every exhaustion claim**, stated in the query
and printed beside the other two. On the 2026-09-11 figures that is 243, not 2,220 and not 562; on the live-facts day it is 369 with 39 exhausted
(10.6 %), against 946 runs that never priced. No "10 % trigger" is claimed until this denominator is published for the window it judges.
(c) **The stage-timing evidence.** Three new samples decide whether a normalizer split is needed at all: `recorder.phase_ms` with
`labels = {phase: fetch|normalize|pricing}` (the tick already measures the whole tick as `recorder.tick_ms`), `normalize.backlog_ids` per family
(`max(raw_responses.id)` written by this tick minus `normalize_state.last_raw_id`, computed from the ids the tick itself inserted - no scan), and
`normalize.backlog_age_s` per family (`select fetched_at from raw_responses where id > :cursor order by id limit 1`, whose plan the plan task captures
with EXPLAIN and abandons for the id-lag alone if it is not index-ordered). Journal 154's finding - the deployed stage order works but "about 12 h
normalizer backlog yields zero current venue_quotes" - is exactly this number, and it is measured before any design is chosen.
(d) **The design decision.** A normalizer process or job split is **proposed, not built, in 6D**: if and only if `normalize.backlog_age_s` exceeds one
cadence in force on more than 10 % of priced runs across a game day *and* `recorder.phase_ms{normalize}` is the largest phase on those runs, §1.3
writes a split proposal into the phase report with its queue, budget and coverage statements and the raw-recording guarantee (`raw_responses` stays
append-only and the source of truth), and it becomes a decision under §8 with its rollback. Otherwise the proposal records that the evidence did not
support a split, with the numbers.
*Files:* `harness/ops/coverage.py`, `harness/recorder/tick.py`, `harness/normalize/runner.py` (measurement only), `harness/report/tables.py`,
`tests/test_coverage_denominator.py`. *Depends on:* 1.1.
*Expected result:* over a seeded day of 20 runs - 12 skipped, 8 priced, 2 of them exhausted - `eligible_runs` returns `(20, 8, 8)` and the exhaustion
share is 2/8 = 25 %, never 2/20; a run whose markets produced no gap row contributes exactly one `no_gap` unit per scheduled variant.

### 1.4 Exclusion classes counted separately (decision 4 bullet 1; v2 §6.4, §9.3)
Two module-level mappings in `harness/ops/exclusions.py`. `CLASS_OF` maps every current executor and strategy reason name to one of four classes.
`COVERAGE_CLASS_OF` (ruling C2) maps every coverage outcome §1.1 can write - `scheduled`, `completed`, `not_due`, `cadence_none`, `budget`,
`http_error`, `skipped_trades`, `skipped_ladders`, `skipped_alternates`, `no_fair`, `no_gap`, `no_signal`, `budget_stage_skipped`, `variant_skipped`,
`truncated` - to one of `complete | pending | not_scheduled | budget | data | instrument`, and `coverage.record` refuses an outcome the map does not
carry, so no outcome reaches the table unclassified. One test proves both exhaustive: every constant in `plan.py`'s reason block and every entry of
`run.py`'s `LABEL_ORDER` appears exactly once in `CLASS_OF`, and every member of `coverage.COVERAGE_OUTCOMES` exactly once in `COVERAGE_CLASS_OF` (so a
new reason or outcome cannot be added without classifying it). The four exclusion classes:
- **capacity** - `exec_capacity` (`plan.py:63`), and the cap labels `max_open`, `cap_per_bet`, `cap_per_game`, `cap_daily` (`run.py` `LABEL_ORDER`),
  which bind only where `apply_caps` is true;
- **strategy rejection** - `signal_rejected`, `no_target`, `edge_decay`, `post_only_reject`, `kickoff` (`plan.py:53-64`, the reason block) and the filter labels
  `source_allowed`, `sport_allowed`, `price_band`, `ttk`, `spread`, `volume`, `velocity`, `disagreement_ok`, `edge`, `min_contracts`;
- **unreliable data** - `book_dirty`, `fair_stale`, `unmatched` (`plan.py:61,55,54`), and the labels `has_fair`, `not_stale`, `match_confidence`;
- **operational** - `kill_switch`, `venue_move`, `reprice`, `expiry`, and `drawdown_stop` as the annotation it is (`ANNOTATION_LABELS`, never a
  `rejection_reason`). Decision 4 names three classes; a fourth is required because `kill_switch` and `reprice` are neither capacity, strategy nor
  data, and folding them into any of the three would misattribute them. The three named classes keep their exact membership.
**Two literals are promoted, and `no_book` is an annotation, not an exclusion** (ruling I8). `reason="expiry"` (`harness/execution/loop.py:986`) and
`reason="no_book"` (`loop.py:1059`) are string literals rather than constants, so the exhaustiveness test above could not see them; the plan's exclusion
task promotes both to constants in `plan.py` beside `KILL_SWITCH` ... `NO_TARGET` (`plan.py:53-64`) and uses them at their single write sites, a
behaviour-neutral change that writes the same strings. `no_book` is then classed as an **annotation on a placement**: `loop.py:1050-1059` writes that
`skipped` event on an order that *was placed*, recording only that the queue behind it was unknowable (R10), so it sits with `drawdown_stop` under
annotations and is excluded from every exclusion-class total.
The classes are reported in t14, on Floor's funnel (`skipped` rows gain a `class` key) and in `coverage_samples` as the `outcome`'s class. Nothing in
the executor's or the strategy's behaviour changes: this is a mapping over names that already exist.
*Files:* `harness/ops/exclusions.py` (new), `harness/execution/plan.py` (two constants), `harness/execution/loop.py` (their two write sites),
`harness/dashboard/snapshots/floor.py`, `harness/report/tables.py`, `tests/test_exclusion_classes.py`. *Depends on:* nothing (can start immediately).
*Expected result:* a window with 3,836 `book_dirty`, 2,967 `fair_stale`, 344 `exec_capacity` and 9 `no_book` events (journal 136's real breakdown)
reports unreliable-data **6,803**, capacity 344, strategy 0, operational 0, and 9 `no_book` annotations counted **outside** the exclusion totals (ruling
I8) - the sums computed in the test by hand from those four numbers.

### 1.5 Budget isolation, and the closing of fix 48 (decision 4 bullet 2; §0.7, §0.8; fix 48)
(a) **What `stages[]` lacks today**, each fixed here: no work count per stage (ms alone cannot separate an expensive stage from a busy one), so each
stage entry gains `units` (fair rows, gap rows or scored (variant, row) pairs); no budget accounting at the boundary, so each entry gains
`remaining_ms` (the deadline minus the clock at the stage's start); `variant_ms` accumulates a variant scored twice into one number
(`pipeline.py`'s `+=`), so a second `variant_ms_rescore` map is recorded separately; a skipped stage records `status: "skipped"` with no cause beyond
"budget", so the entry gains `cause` (`budget` or `nothing_to_do`).
(b) **The duplicate work found in the code** (§0.8): stage 6 re-scores direct-only variants over the derived rows - `pipeline.py:413-426` skips a
variant only when `not consumes_derived(variant) and not new_gaps` - which by construction can only add `source_allowed` and `has_fair` rejections.
Live fact (a) shows that stage costing 8,866 ms against 783 ms for the direct pass. The fix: stage 6 iterates
`[v for v in ordered if consumes_derived(v)]`, and the suppressed row count goes into `notes->'pricing'->'rescore_suppressed'` per variant.
**`variants_partial` has to be maintained deliberately** (ruling I10): `record_order` computes
`variants_partial = [v.name for v in ordered if v.name in scored - complete]` (`pipeline.py:343`); stage 3 scores with `full=direct_complete`, where
`direct_complete = len(direct_rows) == len(market_order)` (`pipeline.py:369-370`) and `market_order` is extended with every quoted matched market
before the no-fair filter (`gaps.py:124-128`) while `direct_rows` holds only the markets that had a direct fair - so with `no_sharp: 2140` in
production `direct_complete` is False, and it is today's stage 6 `full=True` pass that moves the six direct-only variants, the gate and the primary
among them, out of `variants_partial`. When stage 6 suppresses a direct-only variant it therefore **marks it complete** in the same branch (its direct
universe is its full universe), and the extended stage-order test asserts `variants_partial == []` for it.
**The expected residual** (M1): the saving is not the whole 8,866 ms. Stage 6 still re-loads every gap row (`_load_gap_rows`, `pipeline.py:418`) and
still scores `sharp_plus_derived` over the full universe (166 candidates / 558 rejected on run 14307), so one variant's pass plus the load remains -
roughly a seventh of the scoring plus the load, **~1.5-2.5 s** on a 724-row slate - and the expected saving is ~6.5-7.5 s per priced tick. §3 row 5
judges `variants_derived.elapsed_ms` against **< 2,500 ms** on a slate of that size, with `stages[].units` making the comparison per unit rather than
per tick.
(c) **Phase isolation in the tick**: `recorder.phase_ms{fetch|normalize|pricing}` (§1.3c). Live fact (a)'s `budget_s: 20` on a 120 s cadence is
`PRICE_BUDGET_FLOOR_S` (`tick.py:45`), the floor rather than a measurement: `pricing_budget` returns
`max(PRICE_BUDGET_FLOOR_S, min(price_budget_s, cadence_s - elapsed_s - PRICE_BUDGET_MARGIN_S))` (`tick.py:171-173`), so a floor result says only that
fetch plus normalize had spent **at least** ~90 s of the cadence - a lower bound, not a measurement (M2). That is exactly why `recorder.phase_ms` is
needed: it makes the division visible without a log dive. **No budget value and no cadence changes** (a cadence change is gate 5).
(d) **Fix 48's acceptance, and the amendment of its parity contract.** From the row's "Change" and "Covering test" columns: "a forced tick after the
deploy: `order` leads with the gate variant and the primary, `gaps > 0`, candidates > 0; no `budget_exhausted` tick with an empty `order` in 24 h".
Live fact (a) already satisfies the first three clauses on `ebf0953` (order leads `sharp_two_sided`, `sharp_direct`; gaps 724; candidates 9-166); the
remaining clause is §3 row 4's query over the 24 h after this milestone's deploy.
**(b) amends fix 48's frozen-baseline parity contract, and the amendment is stated here rather than discovered in the test** (ruling C1).
`tests/test_pipeline_stage_order.py:172-217`'s
`test_the_stage_order_produces_exactly_what_the_single_pass_produced` compares the reordered pipeline against the frozen pre-hotfix producer in
`tests/fixtures/pricing_44e8e9c/pipeline.py` (loaded by `tests/pricing_baseline.py::baseline_pipeline`) with
`assert _signal_rows(db_session, new_run.id) == _signal_rows(db_session, old_run.id)` over **every** stored signal keyed on
`(variant_id, venue_market_id, side)`, and with `new[key] == old[key]` for `signals`, whose per-variant value is `{candidate, rejected}`. Both
assertions fail the moment stage 6 stops re-scoring direct-only variants over derived rows, and this fixture does add derived rows
(`test_the_second_gap_call_never_duplicates_the_first_ones_rows` asserts `{s.fair_source} >= {"direct", "derived"}` and a row with a `no_fair_reason`).
The amended contract: **parity narrows to `decision = 'candidate'` rows plus every candidate's `edge`, `stake`, `contracts` and labels**, which stay
identical field for field; **the rejected-row difference is asserted separately** to equal exactly the suppressed set, per variant and in total against
`rescore_suppressed`; `fair_direct`, `fair_derived`, `no_sharp`, `gaps` and `order`, and `_fair_rows`/`_gap_rows` parity, keep their existing equality
untouched. The claim that narrows is "identical to the single pass over the whole stored population"; what remains is that no candidate, price, size,
label or order moves, which is what made fix 48 scientifically safe. This amendment is decision **D12** in §8 with its reversal, and the stage-order
test's other cases (`test_every_stage_records_what_it_cost`,
`test_a_budget_that_dies_after_the_direct_variants_still_scored_the_gate_and_the_primary`) are extended, not replaced.
*Files:* `harness/strategy/pipeline.py`, `harness/recorder/tick.py`, `tests/test_pipeline_stage_order.py`, `tests/test_pipeline.py`. *Depends on:* 1.1.
*Expected result, computed independently:* on a fixture with 3 direct gap rows, 2 derived gap rows, one direct-only variant and one derived consumer,
the candidate set and every candidate's `edge`, `stake` and `contracts` are **identical** with and without (b); the rejected-signal row count falls by
exactly 2 (the direct-only variant's two derived rejections), `rescore_suppressed` reads `{"<direct_only>": 2}` - the separate assertion §1.5(d)
substitutes for whole-population parity - and `variants_partial` is empty for the direct-only variant.

### 1.6 The holding and capacity policy: baseline and comparison harness (decision 4 bullet 4; v2 §9.2, §9.5; §0.9)
(a) **The baseline, stated exactly** (read from `harness/config/settings.py` and the seven registered YAMLs; nothing is edited): fair values older than
**180 s** are `stale` and not tradeable (v2 §6.2 as amended 2026-09-07; `stale_s: 180` in every registered variant); the executor's freshness test is
`now − fair_ts > max(variant.stale_s, gap.stale_allowance_s)` (`plan.py::_fair_stale`); book freshness `exec_book_max_age_s = 120`; WS silence
`ws_stale_s = 180`; loop period `exec_period_s = 15`; shared capacity `exec_max_open_orders = 150` with `max_open: 25` per variant; intent TTL
`exec_intent_ttl_s = 900`; placement stops at `exec_kickoff_cutoff_min = 10` and `min_ttk_min: 20`; expiry `kickoff − 10 min`, placed once (R8).
**Participation windows** are the recorder's cadences (`cadence.interval_for`): 20 s for NFL T−100 to T−60 min, 120 s from 3 h before a sport's first
kickoff of the day through its last and while a game is in progress, 300 s on weekends, 900 s on weekdays, none 01:00-08:00 CT unless a game of that
sport is in progress. That paragraph is the declared baseline for the coverage contract.
(b) **The comparison harness**, designed now and **run only after 6B**: `harness policy-compare --from --to --policies <names> --out -` replays one
recorded tape slice once per policy through `harness replay --execute`'s path, **stepping at the recorded loop instants** (6B's carve-out and its D15:
a grid stepping 15 s against a live loop that ran 27 loops in an hour is a different number of observation opportunities). What 6D adds on top of 6B's
stepping mechanism: a `HoldingPolicy` dataclass carrying the six parameters the alternatives vary (`stale_allowance_s`, `rest_to_expiry`,
`per_variant_slots`, `fillability_admission`, `join_the_bid`, `near_kickoff_only`), threaded into `plan.py` as **arguments with the baseline as their
defaults** so the live path is bit-identical when nothing is passed; equal-resource execution (one policy at a time, same slice, same process, the
wall-clock and row counts recorded per run); and a comparison table per policy: orders placed, unique opportunities, actual queue-filled orders,
clean resting seconds, coverage completed/scheduled, and the exclusion classes of §1.4. The six alternatives compared are decision 4's: a 600-900 s
stale allowance, rest-to-expiry, fixed per-variant slots, fillability admission, join-the-bid, near-kickoff-only. **Every output of the comparison is
labelled counterfactual and exploratory** (M6): the table's caption and every row name the policy and carry that label, and no number produced under a
non-registered parameter is ever reported as a registered id's performance, entered in a gate, a benchmark or a variant's record.
(c) **Registration.** The selected policy (the user's dated decision, §0.15a) is stored as a new `config_history` row (hash of the policy JSON) or, if
it changes a variant's own rule, as a **new** variant id by dated pre-registration amendment. Never an edit to a registered id; anything registered
after Mon 2026-09-21 09:00 CT is exploratory and labelled so.
*Files:* `harness/execution/policy.py` (new), `harness/execution/plan.py` (defaulted arguments only), `harness/cli.py`, `docs/runbooks/replay.md`,
`tests/test_policy_compare.py`. *Depends on:* 1.4; the *run* depends on 6B.
*Expected result:* with the baseline policy passed explicitly, `policy-compare` over a fixture slice reproduces `harness replay --execute`'s order and
skip counts **exactly** (a byte-equal action list); with `stale_allowance_s = 900` the same slice produces at least as many placements and a strictly
larger mean fair age at placement, both computed by the test from the fixture's stamps.

### 1.7 The coverage contract, and the two deferred funnel units (decision 4's acceptance paragraph; 6C D11/ruling I6; fix 55's coverage half)
(a) **The contract, declared before the run** as §3's verify.md rows and t14's table, in decision 4's own words: *every scheduled eligible
primary/gate evaluation completes inside its freshness window or leaves an explicit reason and interval; zero unexplained omissions; documented
missingness is quantified and its tolerance stated before inference.* Operationally: for the gate variant and the primary, over a stated window,
`completed / scheduled` per cell; every non-completed unit carries a reason in `COVERAGE_CLASS_OF` (§1.4) and an `overdue_ms`; an
**unexplained omission** is defined two ways and both read **0** (ruling C2) - a stored row whose `outcome` is not in `COVERAGE_CLASS_OF`, which
`coverage.record` refuses at the write, so the count is an integrity check on the table; and a `scheduled` row that no completion row closed within one
cadence period, which §3 row 2's reconciliation query names by cell and which is the omission the acceptance sentence is about. And the **tolerance is
declared before the period it judges**: `coverage_min = 0.95` of scheduled primary/gate evaluations completed per game-day, with any shortfall
quantified by class. Reduced order churn alone is not acceptance and is not reported as such.
(b) **Unique candidate opportunities** - `opportunity_episodes(variant_id, venue_market_id, side, started_at, ended_at, gap_rule_s, n_signals)`: a run
upserts one row per candidate key, extending the open episode when the previous sighting is within `gap_rule_s = max(600, 3 x cadence_in_force)` and
opening a new one otherwise; `gap_rule_s` is stored on the row so a reader knows which rule produced it. The write is **one
multi-row upsert per run** (`insert ... on conflict (variant_id, venue_market_id, side, started_at) do update`), capped at `EPISODE_UPSERT_CAP = 2,048`
keys with an `episodes.truncated` metric when it binds (ruling I7): run 14307's seven variants carried 393 candidate keys
(36+38+9+36+36+72+166), so at 369 priced runs a day the writer performs ~145,000 upserts a day, each rewriting one row version and two index entries;
the theoretical ceiling is 724 gap rows x 7 variants = 5,068 keys, which the cap holds. §2 carries that churn and §3 row 10 watches it. The unit is
`select count(*) from opportunity_episodes where started_at >= :from and started_at < :to and variant_id = :v` on `ix_opportunity_started`.
(c) **Distinct intent episodes** - `intent_episodes` with the same shape keyed on `(variant_id, venue_market_id, side)` from `intents`, written by the
executor where it writes the intent, in the same single multi-row upsert under the same `EPISODE_UPSERT_CAP` (an executor loop places far fewer intents
than a pricing run scores candidates: `exec.placed + exec.skipped` is tens per loop). The unit is the same bounded count. Both units are labelled on
Floor and in t1/t14 with their gap rule, and 6C's
`candidate_signals` and `intent_verdicts` keep their existing labels beside them (`FUNNEL_UNITS`).
(d) **Fix 55's coverage half**: t14 carries `markets with no fair value` per variant per day, split by `no_fair_reason`, beside the scheduled count -
the 54,435-a-day number in its denominator. It is sourced from `market_gap_snapshots.no_fair_reason` and `coverage_samples`' `no_fair` outcome, **not
from `signals`** (ruling I3): those 54,435 rows a variant carry `rejection_reason = has_fair` and `fair_p` null, which can only come from gap rows the
direct phase never writes (`gaps.py:182-184` skips a market with no fair at all), so they are produced by exactly the stage-6 pass §1.5(b) removes, and
the signal-level count **ends at the deploy instant** (§0.8's labelled boundary). The quantity itself does not disappear - it is the same markets,
counted where they are recorded rather than where they were rejected - and after the deploy it is a per-variant count over that variant's own scheduled
set, instead of six of the seven reading ~0 because their rejections are no longer stored.
*Files:* `harness/db/models.py`, `harness/db/schema.py`, `harness/strategy/pipeline.py`, `harness/execution/loop.py`, `harness/report/tables.py`,
`harness/dashboard/snapshots/floor.py`, `docs/superpowers/autopilot/verify.md`, `tests/test_funnel_episodes.py`, `tests/test_report_t14.py`.
*Depends on:* 1.1, 1.4.
*Expected result, computed independently:* one market scored candidate on ticks at 12:00, 12:02 and 12:04 and again at 14:00 on a 120 s cadence yields
**2** opportunity episodes (gap rule 600 s; the 116-minute hole exceeds it) and 4 candidate signal rows - the two units disagreeing by construction,
which is the point of reporting both.

### 1.8 Carried fix 46: the RFQ listener's executor-yield guard and stored-rows bound
From the row's "Change" column: an executor-yield guard, a stored-rows-per-minute cap with counters and a metric, and the verify.md `rfqs arrivals`
expectation rewritten from measured game-day volume. Design: `RfqListener` reads `exec_heartbeat` (id = 1) at most once per `RFQ_HEARTBEAT_POLL_S = 5`
seconds in its own short-lived session; it **stores and quotes nothing** while `last_loop_at` is older than `RFQ_YIELD_AGE_S = 60` s or `last_loop_ms`
exceeds `RFQ_YIELD_LOOP_MULT = 3` x `exec_period_s` (45 s), counting `frames_yielded` and emitting `rfq.yielded`; the socket, the subscription and the
ack are untouched, so yielding is not a disconnect. A sliding `RFQ_STORE_RATE_MAX` rows per `RFQ_STORE_RATE_WINDOW_S = 60` cap sits in front of
`store_rfq` (the same deque shape as `_quote_times`, `rfq_socket.py:187`), counting `rows_skipped_rate` and emitting `rfq.stored_rows`. The 63,443 rows
in 50 minutes of journal 136 is the number this bounds; `RFQ_STORE_RATE_MAX = 60` holds the day under 86,400 rows even if the guard never engages, and
the *expectation* in verify.md is rewritten from the first measured Saturday slate, not from fix 40's pre-incident guess.
*Covering test (the row's column):* "the listener on through a Saturday slate: executor loop p95 unchanged with it on against off; stored rows counted
and bounded" - plus unit tests: a fake heartbeat 90 s old yields every frame; a heartbeat 10 s old with `last_loop_ms = 50,000` yields; a healthy
heartbeat stores; 200 frames in one simulated minute store exactly `RFQ_STORE_RATE_MAX` and count the rest.
*Files:* `harness/venues/kalshi/rfq_socket.py`, `harness/venues/kalshi/rfq.py`, `docs/runbooks/research.md`, `docs/superpowers/autopilot/verify.md`,
`tests/test_rfq_listener.py`. *Depends on:* nothing (independently mergeable).
*Expected result:* with the guard engaged, `rfqs` gains 0 rows and `rfq_quotes` 0 rows while the executor's loop is stale, and the subscription stays
`ok` in `venue_status` throughout - the three facts asserted separately.

### 1.9 Carried fix 51: the integrity checks bounded to the window they judge
From the row's "Change" column: "bound them to the 24 h window they judge, use the partition/index the tables already have, or split them"; from its
"Covering test": "a sweep after the deploy with the three checks `pass`; `check_results` row of verify.md". **No threshold and no timeout is raised**
(gate 13); `STATEMENT_TIMEOUT_MS` stays 2000.
- `duplicate_trades`: today `_duplicate_trades_sql` (`harness/ops/checks.py:57-65`) groups the whole current weekly partition
  (`current_trades_partition(now)`) by `(venue, trade_id)`. A bare `ts` predicate **cannot** narrow that scan through the table's primary key, because
  `VenueTrade`'s key is `(venue, trade_id, ts)` with `venue` leading (`harness/db/models.py:206-229`); the only ts-ordered structure on the tape is the
  BRIN `ix_trades_ts_brin` (`harness/db/schema.py:688-689`, `autosummarize = on` since fix 32). The bounded statement therefore groups over a
  BRIN-driven subquery - `select venue, trade_id from <partition> where ts >= now() - interval '25 hours'` - and **the plan task captures
  `EXPLAIN (ANALYZE, BUFFERS)` for it before adopting it** (ruling I2). Named fallbacks if the plan does not prune to the BRIN ranges: one grouping per
  calendar day inside the window, unioned, each day bounded the same way; and if neither prunes, the statement stays as it is, the skip is journaled,
  and **no timeout and no threshold is raised** either way. Because a weekly partition does not contain a 25 h window early in an ISO week, the
  statement names the **previous** weekly partition as well whenever `now() - interval '25 hours'` falls before `week_bounds(now)` opens
  (`current_trades_partition`'s own helper), and the verify.md text is amended to exactly that statement ("duplicates among trades recorded in the last
  25 h, across the weekly partitions that window touches") - a check whose statement and whose documented statement differ is the defect fix 16 already
  corrected once.
- `fair_values_negative_staleness` and `fair_values_negative_feed_lag`: both are already bounded to 24 h on `ix_fair_created_brin` and both passed on
  the 2026-09-13 sweeps (live fact (e)); this component adds the EXPLAIN-capture test that pins the BRIN plan so the bound cannot silently regress,
  and no query text changes.
- `intents_without_order_or_skip` (skipping today, live fact (e)): the 24 h intent window is a sequential scan of `intents`, which carries no index on
  `created_at`; the component adds `create index concurrently if not exists ix_intents_created on intents (created_at)` and leaves the statement's
  logic - including phase 4.5's hold-path excusal - exactly as it is.
*Files:* `harness/ops/checks.py`, `harness/db/models.py`, `harness/db/schema.py`, `migrations/versions/00NN_phase6d_sustained_evaluation.py`,
`docs/superpowers/autopilot/verify.md`, `tests/test_checks.py`. *Depends on:* nothing (independently mergeable).
*Expected result:* in the test database each of the four statements returns inside 2,000 ms with `STATEMENT_TIMEOUT_MS` monkeypatched to its real
value over a seeded week of trades and 40,000 intents, and each returns the same integer as an unbounded reference query over the same window computed
by the test - the bound provably narrowing the scan, not the answer. In production the same four statements report `pass` **or `fail`**; an answer is
the deliverable and a `skip` is the regression (§3 row 8, ruling I1).

## 2. Data

Additive only, declared where this repo's schema machinery looks: the three new tables are **models**, so `Base.metadata.create_all` creates them and
`drop_schema`'s metadata list drops them; plain indexes go on `__table_args__`, raw-DDL indexes in `_INDEX_DDL` (`schema.py:133`), concurrent ones in
`_CONCURRENT_INDEX_DDL` (`schema.py:231`) with **no carve-out for bulk tables** (F65); no new column on a bulk tape table; `migrations/versions/
00NN_phase6d_sustained_evaluation.py` carries the same statements and `tests/test_alembic.py`'s catalogue diff is the check. **The controller assigns
`NN` at merge time** (the 4.6 addendum's D9 pattern): 6B's `0008_phase6b_execution` and 4.6's `0008_phase46_fun_tickets` are both unmerged, so the
number cannot be chosen here.

| Addition | Shape | Invariant query (must return 0) |
|---|---|---|
| `coverage_samples` (model) | `id`, `run_id`, `ts`, `domain varchar(12)`, `sport varchar(8)`, `ttk_bucket varchar(12)`, `feed varchar(9)`, `market_type varchar(16)`, `variant_id varchar(12)`, `outcome varchar(24)`, `n integer`, `overdue_ms integer`; index `ix_coverage_ts_domain (ts, domain)` and `ix_coverage_run (run_id)` | `select count(*) from coverage_samples where n < 0 or (outcome not in ('completed','scheduled') and overdue_ms is null) or (outcome in ('completed','scheduled') and overdue_ms is not null)` |
| `opportunity_episodes` (model) | `id`, `variant_id`, `venue_market_id`, `side varchar(4)`, `started_at`, `ended_at`, `gap_rule_s integer`, `n_signals integer`; unique `(variant_id, venue_market_id, side, started_at)`; index `ix_opportunity_started (started_at)` | `select count(*) from opportunity_episodes where ended_at < started_at or n_signals < 1` |
| `intent_episodes` (model) | the same shape with `n_intents` | `select count(*) from intent_episodes e where e.ended_at < e.started_at or not exists (select 1 from intents i where i.variant_id = e.variant_id and i.venue_market_id = e.venue_market_id and i.side = e.side and i.created_at between e.started_at and e.ended_at)` |
| `ix_intents_created` on `intents (created_at)` | `create index concurrently if not exists`; mirrored on `Intent.__table_args__` | `select count(*) from pg_index i join pg_class c on c.oid = i.indexrelid where c.relname = 'ix_intents_created' and not i.indisvalid` |
| `runs.notes->'pricing'` new keys (`stages[].units`, `stages[].remaining_ms`, `stages[].cause`, `variant_ms_rescore`, `rescore_suppressed`) | JSONB keys inside an existing column; no DDL | `select count(*) from (select started_at, notes from runs order by id desc limit 2000) r where r.started_at > :deploy_ts and r.notes ? 'pricing' and not (r.notes->'pricing'->'stages'->0 ? 'units')` - cap-then-filter on the primary key, `recent_runs`' shape (`harness/dashboard/queries.py:27-93`), because `runs` has **no index on `started_at`** (ruling I5) and 2,000 rows is ~36 h at 1,315 runs a day |
| `metric_samples` new names (`recorder.phase_ms`, `normalize.backlog_ids`, `normalize.backlog_age_s`, `pricing.feed_lag_s`, `exec.fair_age_s`, `exec.signal_to_order_ms`, `ws.tape_covered_frac`, `rfq.stored_rows`, `rfq.yielded`, `coverage.truncated`) | rows in the existing table; no DDL | `select count(*) from metric_samples where ts > now() - interval '24 hours' and value < 0` (verify.md's existing invariant, which these must not break: ages are clamped at 0 as `exec.ws_event_age_s` already is) |

**Disk cost per day, re-derived from §1.1's cardinality bound** (ruling I6; the database is 100 GB at 2026-09-13, 17 % of the 600 GB budget):
`coverage_samples` at 1,315 runs a day - 946 collection-only ticks at ~32 rows, 369 priced ticks at ~760 evaluation rows plus their ~32 collection rows
- is ~323,000 rows/day; at ~110 B heap plus two indexes (~60 B) that is **~55 MB/day**. The absolute ceiling the cap allows is 2,720 rows a tick
(~176 MB/day), which §3 row 10 catches within a day. The episode tables hold a few thousand live rows a day (~1 MB) but perform ~145,000 upserts a day
(§1.7(b)): at ~90 B a row version plus two index entries that is **~29 MB/day of dead tuples** (ruling I7), which autovacuum reclaims and which §3
row 10 watches as a dead-tuple ratio rather than as growth. The ten new metric names add **~32,000 rows/day (~2 MB/day)**: `normalize.backlog_ids` and
`normalize.backlog_age_s` are per family (eight, `harness/normalize/runner.py:17-18`) per tick = ~21,000, `recorder.phase_ms` x3 x1,315 = ~3,900,
`pricing.feed_lag_s` p50/p95 per feed over 369 priced runs = ~1,500, the per-minute `exec.*` pair 2,880, `rfq.*` 2,880 while the listener is on, and
`ws.tape_covered_frac` 24. Total **~58 MB/day of live data, ~21 GB/year, about 3.5 % of the 600 GB budget per year**, plus churn autovacuum reclaims.
`COVERAGE_ROW_CAP`, `EPISODE_UPSERT_CAP` and the one insert per phase are what keep that bound true if the slate triples; §3 row 10 watches the real
daily growth against this estimate and lowers the caps if it is exceeded.

## 3. Verification (the plan's last task adds these rows to `verify.md`)

1. **Coverage contract, gate variant and primary.** `select variant_id, sum(n) filter (where outcome = 'completed')::numeric /
   nullif(sum(n) filter (where outcome = 'scheduled'), 0) from coverage_samples where domain = 'evaluation' and ts > now() - interval '24 hours' and
   variant_id in (:gate, :primary) group by 1` - the denominator is the recorded scheduled set, not the sum of the rows (§1.1) - is **>= 0.95** on
   a game day. *Expected by time of day:* judged on game days only (Thu-Mon); 01:00-08:00 CT the quiet-hour cadence schedules nothing and the row
   reads "deferred: no scheduled evaluation in the window".
2. **Zero unexplained omissions: every scheduled unit is closed or explained** (ruling C2). The reconciliation query
   `select s.run_id, s.sport, s.ttk_bucket, s.feed, s.market_type, s.variant_id, s.ts from coverage_samples s where s.domain = 'evaluation' and
   s.outcome = 'scheduled' and s.ts > now() - interval '24 hours' and s.ts < now() - interval '16 minutes' and not exists (select 1 from
   coverage_samples c where c.run_id = s.run_id and c.domain = s.domain and c.outcome <> 'scheduled' and c.sport is not distinct from s.sport and
   c.ttk_bucket is not distinct from s.ttk_bucket and c.feed is not distinct from s.feed and c.market_type is not distinct from s.market_type and
   c.variant_id is not distinct from s.variant_id)` returns **no rows**: every scheduled cell was closed inside one cadence period (900 s is the widest
   in force, `DEFAULT_CADENCE_S`, plus a minute for the tick itself). The outer scan rides `ix_coverage_ts_domain (ts, domain)`, the probe
   `ix_coverage_run (run_id)`. A row it returns is a real unexplained omission - a dead tick, a stage never entered, a pricing block that raised - and
   is journaled by cell with that run's `status` and `warnings` beside it. The companion integrity count
   `select count(*) from coverage_samples where ts > now() - interval '24 hours' and outcome not in (select unnest(:coverage_outcomes))` is also 0.
   *Every verify, any hour.*
3. **Missingness quantified.** `select outcome, sum(n) from coverage_samples where domain = 'evaluation' and ts > now() - interval '24 hours' and
   outcome not in ('completed','scheduled') group by 1 order by 2 desc` is journaled in full every verify, beside the class each outcome maps to in
   `COVERAGE_CLASS_OF`; the tolerance (row 1) was declared before the period it judges. *Every verify.*
4. **Fix 48's remaining clause.** `select count(*) from (select started_at, notes from runs order by id desc limit 2000) r where
   r.started_at > now() - interval '24 hours' and (r.notes->'pricing'->>'budget_exhausted')::boolean and
   jsonb_array_length(coalesce(r.notes->'pricing'->'order','[]'::jsonb)) = 0` = 0 - cap-then-filter on the primary key because `runs` has no index on
   `started_at` (ruling I5), and 2,000 rows covers a day's 1,315 runs with room to spare. *Judged 24 h after the deploy*; before that the row reads
   "deferred: judge-after <deploy + 24 h>".
5. **Stage costs, the rescore and the measurement boundary.** `select r.notes->'pricing' from (select id, notes from runs order by id desc limit 100)
   r where r.notes ? 'pricing' order by r.id desc limit 1` (cap-then-filter again; ~28 % of runs carry a pricing block, so 100 rows always contain one)
   carries `units`, `remaining_ms` and `cause` on all six entries; `variants_derived.elapsed_ms` is **below 2,500 ms** on a slate of run 14307's size
   (724 gap rows, seven variants) against 8,866 ms before the deploy, the residual being §1.5(b)'s reload plus `sharp_plus_derived`'s own full-universe
   pass; and `rescore_suppressed` is non-empty. The **deploy instant** is recorded in this row, in the journal entry and in t14's note as the boundary
   of every rejected-signal series, with `rescore_suppressed` as the bridge (ruling I11). *First game window after the deploy.*
6. **Denominators published.** The Monday report's t14 prints `total_runs`, `non_skipped_runs` and `priced_runs` for the week and states that every
   exhaustion share is over `priced_runs`. *Mondays.*
7. **Funnel episodes.** `select count(*) from opportunity_episodes where started_at > now() - interval '24 hours'` and the matching
   `intent_episodes` count are both present on Floor's payload with their `gap_rule_s`, and each is **<=** its 6C counterpart
   (`candidate_signals`, `intent_verdicts`) - an episode count above its event count is an integrity anomaly. *Every verify.*
   **[annotated 2026-09-14, 6D final review `final-6d`, carried from Task 10 review I1: "their `gap_rule_s`" is one *shared* key.
   Floor emits a single `episode_gap_rule_s` for both tables (`harness/dashboard/snapshots/floor.py:657`), not one per table, and
   Floor's own funnel counts run over its 6 h `FUNNEL_WINDOW` (`floor.py:88`), so this row's 24 h query is a standalone integrity
   read and not the number printed on the page. `verify.md`'s Phase 6D "Funnel episodes" row carries the same clarification.]**
8. **Checks (fix 51).** `check_results` over the last 25 h: `duplicate_trades`, `fair_values_negative_staleness`, `fair_values_negative_feed_lag` and
   `intents_without_order_or_skip` each report **`pass` or `fail`, never `skip`** (ruling I1) - fix 51's "Change" column says "report `pass`/`fail`,
   never `skip:timeout`", so the deliverable is a bounded statement that answers, not a passing answer. `intents_without_order_or_skip` last returned a
   real **`fail` with 483** (job_run 66, 2026-09-11) and 6D recovers no lost intent, so that result is carried as its own open item in this row, beside
   the three already-failing data checks (`fills_outside_placement_window`, `markouts_at_after_horizon`, `game_score_went_down_24h`) which keep their
   existing "under audit" treatment and are not 6D's. *Every verify after the deploy.*
9. **RFQ listener (fix 46).** With the listener on: `select count(*), min(received_at), max(received_at) from rfqs where received_at > now() -
   interval '24 hours'` against the rewritten expectation; `rfq.stored_rows` never exceeds `RFQ_STORE_RATE_MAX` in any minute; `exec.loop_ms` p95 in
   the listener-on hour is within 10 % of the listener-off hour journaled beside it. *First Saturday slate after the deploy.*
10. **Table growth and episode churn.** `select pg_total_relation_size('coverage_samples') / (1024*1024)` grows by **under 110 MB/day** (twice §2's
    ~55 MB estimate, re-derived from the cardinality bound); above it, lower `COVERAGE_ROW_CAP` and journal. For the episode tables,
    `select relname, n_live_tup, n_dead_tup from pg_stat_user_tables where relname in ('opportunity_episodes','intent_episodes')` keeps
    `n_dead_tup / (n_live_tup + n_dead_tup)` **under 0.3** and their combined size growth under 10 MB/day; above either, lower `EPISODE_UPSERT_CAP`
    and journal (ruling I7). *Daily 09:00 line.*
11. **Nothing moved that may not move.** `criteria_hash` is still
    `5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5`; `select count(*) from gate_reports where criteria_json ? 'eligibility'` = 0;
    `strategy_variants` holds the same seven ids as before the deploy. *Every verify.*
12. **Latency series alive.** Every new `metric_samples` name has a sample younger than one cadence in force during a game window, and
    `select count(*) from metric_samples where ts > now() - interval '24 hours' and value < 0` is still 0. *Every verify.*
    **[annotated 2026-09-14, 6D final review `final-6d`, carried from Task 10 review I2: three exemptions to the freshness clause,
    each ruled during implementation. `exec.signal_to_order_ms` is written only when a placement happened in the window (Task 6
    review I4, controller ruling 2026-09-14), so it reads as present-since-the-last-placement, with labels `{"variant", "q": "p50"}`;
    `ws.tape_covered_frac`'s `ts` is the hour *after* the hour it measures and a NULL `ws.gaps` minute counts as clean (Task 6
    M4/M5); `coverage.truncated` is expected to be **absent** -- a cap that never binds writes no sample; and `rfq.*` is deferred
    while the listener is off. `verify.md`'s Phase 6D "Latency series alive" row carries the same clarification.]**
**The deploy is judged on** rows 2, 4, 5, 8, 11 and 12 plus the executor's own health: `exec.loop_ms` p95 no worse than the pre-deploy hour it is
compared against, `recorder.tick_ms` and `recorder.rss_mb` journaled before and after (fix 49's 6 h / 500 MiB row is not disturbed), and the
`normalize` backlog numbers recorded as the baseline §1.3d judges against.

## 4. Ops

- 4.1 Target: the **full** recipe (`make deploy-omarchy`, fix 37's reviewed version), because the diff touches `harness/db/models.py` and adds a
  migration. `app-run`, `app-serve`, `app-exec` and `app-research` carry the changed code; `app-ws` is rebuilt. `init-db` applies the additive DDL and
  migration `00NN_phase6d_sustained_evaluation` carries the same statements. Full recipe: stop `app-exec` and `app-run` before `init-db` (fix 21),
  `init-db`, `alembic upgrade head`, `up -d`, stamp check, then set `RFQ_LISTENER_ENABLED` in the **Omarchy host `.env` under
  `/srv/sports-harness`** - the file the recipe reads; `deploy/omarchy/host.env` carries no such key and `deploy/nas.env:39` is a historical NAS note
  only - to `1` **only** once §1.8 is in the deployed image (§0.13); before that deploy it is set back to `0` after the recipe. Because
  `Settings.rfq_listener_enabled` defaults to `True` (`harness/config/settings.py:144`) the flag **fails open**, so the recipe's last step reads the
  *effective* value back out of the running image - a settings dump in the `app-ws` container (`Settings().rfq_listener_enabled`) together with the
  listener's own startup line `rfq listener disabled by rfq_listener_enabled` (`rfq_socket.py:642-643`) when it is off - and the value read back is
  journaled with the deploy (ruling I12).
- 4.2 Window: R4 in full, with journal 128's app-only NCAAF allowance unavailable (this is a full deploy). The NFL block runs from Sunday 10:20 CT
  through Monday night; the next window is Tuesday.
- 4.3 Rollback: previous sha plus `make deploy-omarchy`. The additive tables, columns and indexes stay - no DROP is ever part of a rollback (invariant
  5) - and a rolled-back build ignores them.
- 4.4 **No cadence change** (gate 5), no new container, cron, secret, dependency, outbound host or bind. If §1.3d's evidence supports a normalizer
  process or job split, it is written as a proposal with its queues, budgets, coverage statement and rollback and becomes a decision under §8 - it is
  **not** built in this milestone.
- 4.5 Expected load change: the tick gains **two** inserts per domain (the scheduled phase and the completion phase, §1.1) inside §2's bound, and
  loses stage 6's duplicate scoring, worth **~6.5-7.5 s** of live fact (a)'s 8,866 ms once §1.5(b)'s residual (the reload plus
  `sharp_plus_derived`'s own full-universe pass) is subtracted; the executor gains two in-memory samples per loop and no query. The
  before-and-after `recorder.tick_ms`,
  `recorder.phase_ms` and `exec.loop_ms` numbers are journaled, not promised.

## 5. Testing

- **Fixtures from recorded runs.** `tests/fixtures/run_notes_pricing_14307.json` is the live-facts `notes->'pricing'` shape (six stages with their
  `elapsed_ms`, `order`, `signals`, `variant_ms`, `budget_s`, `no_sharp`), used by the stage-order, coverage and report tests so every expectation is
  written against a shape production really produced. A second fixture carries the pre-fix-48 exhausted shape (`{"gaps": 0, "variants_run": [],
  "budget_exhausted": true}`) so the readers are proven on both.
- **Stage order, under the amended parity contract (§1.5(d), D12).** `tests/test_pipeline_stage_order.py` keeps its existing cases and gains: the six
  stages still run in `STAGE_NAMES` order; every entry carries `units`, `remaining_ms` and `cause`; the candidate-equality case of §1.5's expected
  result, which is also the narrowed form of `test_the_stage_order_produces_exactly_what_the_single_pass_produced` (candidate rows and every
  candidate's `edge`, `stake`, `contracts` and labels equal field for field against `tests/pricing_baseline.py`'s frozen producer, with the
  rejected-row difference asserted separately against `rescore_suppressed`); `variants_partial == []` for a suppressed direct-only variant; and a case
  where stage 5 adds no rows, in which nothing is re-scored and `rescore_suppressed` is empty.
- **Coverage samples, both phases.** `tests/test_coverage_samples.py` seeds §1.1's tick and asserts the 12 `scheduled` rows written at enumeration, the
  completion rows accounting for those same units as 4 `completed` / 2 `no_fair` / 6 `variant_skipped`, that a tick whose pricing block raises leaves
  its scheduled rows unclosed and that §3 row 2's reconciliation query names exactly those cells, that `record` refuses an outcome absent from
  `COVERAGE_CLASS_OF`, and that `COVERAGE_CLASS_OF` covers every member of `coverage.COVERAGE_OUTCOMES` exactly once.
- **Denominator queries against seeded ticks.** `tests/test_coverage_denominator.py` seeds 20 runs (12 skipped, 8 priced, 2 exhausted) and asserts
  `eligible_runs` returns `(20, 8, 8)`, that the exhaustion share is computed over `priced_runs`, and that a run with a pricing block but no gap rows
  contributes one `no_gap` unit per scheduled variant.
- **Exclusion-class mapping.** `tests/test_exclusion_classes.py` asserts every constant in `plan.py`'s reason block and every entry of `run.py`'s
  `LABEL_ORDER` is classified exactly once, that `drawdown_stop` is classified `operational` and can never be a `rejection_reason`, that the promoted
  `EXPIRY` and `NO_BOOK` constants still write the exact strings their literals wrote (`"expiry"`, `"no_book"`) and that `no_book` is an annotation
  excluded from every exclusion total, and that journal 136's real breakdown sums to §1.4's expected result.
- **The checks under a 2 s statement timeout.** `tests/test_checks.py` gains a database case per bounded statement that runs it with the real
  `STATEMENT_TIMEOUT_MS` over a seeded week of `venue_trades` and 40,000 `intents`, asserting `pass` (never `skip`) and equality with an unbounded
  reference query over the same window; plus the existing monkeypatched-timeout case proving a genuine timeout still records `skip:
  timeout`. The same task records
  `EXPLAIN (ANALYZE, BUFFERS)` for the bounded `duplicate_trades` statement in its journal entry **before** the statement is adopted, and falls back to
  the per-day grouping if the plan does not prune (§1.9, ruling I2).
- **The RFQ guard's yield test.** `tests/test_rfq_listener.py` gains the four cases of §1.8 against a fake `exec_heartbeat` row and a monotonic clock
  seam (`rfq_socket.py`'s existing `_monotonic` injection point), asserting rows stored, quotes computed and counters in each; and
  `tests/test_rfq_refusal.py` gains the guard's "quotes nothing while yielded" case beside
  `test_the_transport_refuses_the_exact_quote_path` (ruling I9).
- **Policy comparison.** `tests/test_policy_compare.py` runs the fixture slice under the baseline policy and asserts a byte-equal action list against
  `harness replay --execute`, then under the 900 s stale allowance and asserts the two directional facts of §1.6.
- `make test` pristine on the branch before every merge; no test is skipped, and no test re-runs the implementation's own arithmetic as its
  expectation.

## 6. Out of scope

The policy comparison **run** and any policy adoption (after 6B; the user's dated decision, §0.15a); building a normalizer process or job split (§1.3d
proposes only); 6E (inventory, restore rehearsal, corrected-workload benchmark, host choice, cutover) and 6F (the version boundary, revised selection
and confirmation dates); 4.6 (fun tickets); anything live, any venue write, any real money; any cadence change (gate 5) or bookmakers change; any new
variant id, any edit under `harness/variants/`, any change to `MAX_PRIMARY`/`MAX_SECONDARY`; any change to a gate criterion, threshold, family, grid,
success threshold or cut-off (R1); the eligibility settings (still dormant, never set); the three failing data checks (`fills_outside_placement_window`
154, `markouts_at_after_horizon` 154, `game_score_went_down_24h` 13), which are carried elsewhere; 6B's execution repairs, its counterfactual backlog
(6B never closes a track) and the key-level recomputation of 27/445; 6C's week keys, t13, confirmation path and annotation backlog; the dashboard's
visual redesign; the kill switch, the dashboard token, spend caps and outbound hosts.

## 7. Conformance (autopilot plan-next 1a)

1. **Components.** §1.1-§1.7 implement roadmap decision 4's bullets in order; §1.8 and §1.9 are carried fixes 46 and 51; fix 48 closes inside §1.5 and
   fix 55's coverage half inside §1.3/§1.7. Nothing else is built.
2. **Dependencies.** None new: standard library, SQLAlchemy, Typer and pytest as pinned; `pyproject.toml` and `constraints.txt` untouched (§4.4).
3. **Pre-registered ids.** Untouched (§0 posture, §6); no `EXECUTOR_VERSION` or `PRICING_VERSION` bump is required, because §1.5(b) changes which
   *rejections* are stored and no candidate, price, size or order - the equality test in §1.5 is the evidence, and the change is recorded in
   `rescore_suppressed` and in `coverage_samples`. What the change does amend is fix 48's frozen-baseline **parity contract**, stated in §1.5(d) and
   carried as decision D12, not any registered id.
4. **Schema.** Additive only and declared where this repo looks: three models, one concurrent index, JSONB keys and metric names (§2); no DROP,
   RENAME, TRUNCATE, DELETE or backfill; migration number assigned by the controller at merge (§2).
5. **Venue writes.** None; the gateway stays `PaperGateway`; §1.8 only *reduces* what the listener writes to our own database. Refusal tests, named
   (ruling I9): `tests/test_rfq_refusal.py::test_the_transport_refuses_the_exact_quote_path` and
   `::test_no_module_in_the_repository_names_the_quote_path`, `tests/test_gateway.py::test_paper_gateway_never_touches_transport` and
   `::test_executor_in_live_mode_cannot_build_a_gateway`; §1.8's guard adds the "quotes nothing while yielded" case to the refusal file.
6. **Money.** None; no metered call, no Anthropic call, no Odds API credit; `veto_daily_usd_cap`/`veto_weekly_usd_cap` untouched (invariant 7).
7. **Secrets.** None; no component reads `secrets/`.
8. **Ops.** §4: full recipe under R4, rollback to the previous sha, no new secret, host, container, cron or cadence.
9. **Verification.** §3 carries an invariant query per new table and column family (§2's right-hand column), expected values by time of day on rows 1,
   4, 5, 6, 8, 9 and 10, the coverage contract's own rows 1-3 (row 2's reconciliation query being the acceptance clause's real test rather than a
   restatement of §2's invariant), and the deploy judgment; every read names the index or the cap that bounds it.
10. **Decisions taken on the user's behalf.** §8's **D1-D12**, each with source, rationale, cost if wrong, blast radius and the exact reversal; the
    two questions held for the user are §0.15.
11. **Out of scope** matches the roadmap's milestone boundaries (§6), including 6B's carve-outs and the two questions held for the user (§0.15).
12. **Files and Depends on.** Every component in §1 carries both lines; §1.4, §1.8 and §1.9 share no file with each other and are the independent
    starts; §1.1 follows §1.4 for `COVERAGE_CLASS_OF`'s outcome names (M9) and precedes §1.2, §1.3, §1.5 and §1.7 (all write through `coverage.py`);
    §1.5 and §1.7 both touch `harness/strategy/pipeline.py` and are serialized, as are §1.2, §1.4 and §1.7 over `harness/execution/loop.py`; §1.6
    follows §1.4. `verify.md` is last.

## 8. Decisions taken on the user's behalf

| # | Decision | Source | Rationale | Cost if wrong | Blast radius | Reversal |
|---|---|---|---|---|---|---|
| D1 | Coverage is recorded at decision time into `coverage_samples`, not inferred from `runs.notes` | model (6C ruling C1) | the acceptance sentence needs a reason and an interval per missing unit, which no after-the-fact scan can supply | one small write per tick that a notes-only reader would not have paid | file, DB additive | stop writing the table; the readers fall back to t13's notes-only rows |
| D2 | The cell grain is `(sport, ttk_bucket, feed, market_type, variant)` | model | decision 4 names "market"; a per-`venue_market_id` grain is thousands of rows a tick and the per-market detail already exists in `fair_values`, `market_gap_snapshots` and `signals` by `run_id` | a question at per-market grain needs a join to those tables rather than a single read | file, DB additive | add `venue_market_id` to the table and the cap |
| D3 | `COVERAGE_ROW_CAP = 3,072` rows per run and `EPISODE_UPSERT_CAP = 2,048` keys, each with a `truncated` row and a metric | model (§1.1's cardinality bound; rulings I6, I7) | an unbounded per-tick write on a 600 GB budget is the failure mode this milestone is supposed to detect, not create, and 3,072 sits above the 2,688-row worst case so the cap is a backstop rather than the routine case | a slate wider than the registered grid loses the tail of its cells, visibly | file, DB additive | raise the cap and re-estimate §2, or drop `feed` from the evaluation cell |
| D4 | Stage 6 stops re-scoring direct-only variants over derived rows (§0.8) | model (live fact (a): 8,866 ms; `source_allowed` is structural) | it is duplicate work decision 4 asks to remove, and the candidate set provably cannot change | the stored rejected-signal population shrinks, which any count over `signals` will show; `rescore_suppressed` is the bridge | file, report denominators | restore the full stage-6 loop (one line) |
| D5 | A fourth exclusion class, `operational`, beside decision 4's three | model | `kill_switch`, `reprice`, `venue_move` and `expiry` are none of the three, and folding them in would misattribute them | one more class to read in every funnel | file, report labels | fold `operational` into `unreliable data` |
| D6 | Episode tables deliver the two deferred funnel units, with `gap_rule_s = max(600, 3 x cadence)` | model (6C D11; fix 31 removed the distinct scans) | it makes the distinct counts an indexed `count(*)` instead of the scan that starved the box | a different gap rule would group differently; the rule is stored per row so a re-count is possible without new data | file, DB additive | recompute with another rule from the stored rows |
| D7 | The baseline policy is adopted as the declared baseline, and no alternative is adopted | pre-loaded (decision 4) | decision 4 says the baseline keeps the current freshness standards and any alternative is compared after 6B; adoption is the user's | none; the comparison still runs and is published | none (documentation) | the user's dated decision (§0.15a) |
| D8 | `duplicate_trades` is bounded to 25 h inside its partition and its verify.md text is amended to match | pre-loaded (fix 51's "Change" column) | the row instructs exactly this bound; a check and its documented statement must not differ | a duplicate pair straddling the 25 h edge is seen a day later, not never | file, verify.md | restore the week-wide grouping and accept the skip |
| D9 | `ix_intents_created` is added so the fourth skipping check passes without a raised timeout | model (live fact (e)) | fix 51's rule is to bound, never to raise; the scan is the cost and an index removes it | one small index on a non-bulk table | DB additive | drop the index (a rollback keeps it, invariant 5) |
| D10 | The migration is named `00NN_phase6d_sustained_evaluation` with `NN` assigned by the controller at merge | pre-loaded (4.6 addendum D9; journal 136 ruling 4) | 6B's and 4.6's `0008` are both unmerged, so the number cannot be chosen in a worktree | a rename at merge if two milestones land together | file | renumber |
| D11 | No `EXECUTOR_VERSION`/`PRICING_VERSION` bump | model | nothing a version boundary exists to separate changes: no fill, price, size, order or gate input moves (§7 item 3) | if D4 did change a candidate somewhere unforeseen, the boundary would be missing; §1.5's equality test and §3 row 5 are the guards | file | bump before the deploy |
| D12 | Fix 48's frozen-baseline parity contract is amended: parity is asserted on `decision = 'candidate'` rows and every candidate's `edge`, `stake`, `contracts` and labels, and the rejected-row difference is asserted separately to equal exactly the suppressed set (`rescore_suppressed`) | model (review C1, ruling C1) | D4 removes provably inert work, and `test_the_stage_order_produces_exactly_what_the_single_pass_produced`'s field-for-field equality over *every* stored signal cannot survive it on a fixture whose derived phase adds rows; the amendment is stated rather than discovered in a red test | "identical to the single pass" narrows from the whole stored population to the candidate population; a rejected-row regression is then caught by the separate suppressed-set assertion instead of by parity | file (tests), report denominators | restore the full stage-6 loop (D4's reversal) and with it the original whole-population parity assertion |

**[D10 annotated 2026-09-14, 6D final review `final-6d`:** the revision as built is `0009_phase6d_sustained_eval` (27 characters),
not `0009_phase6d_sustained_evaluation` (33). `alembic_version.version_num` is `String(32)` and cannot be widened additively, so the long
form aborts every upgrade with `StringDataRightTruncation` (measured on the T1 test database); the short id is recorded in the
revision's own docstring (`migrations/versions/0009_phase6d_sustained_eval.py`), and any merge-time renumbering keeps the
`_eval` stem. The `00NN_phase6d_sustained_evaluation` file names in sections 1.1, 1.9 and 4.1 read the same way.]**

## 9. Task decomposition for the plan writer

Wave 1 (independent, hotfix-shaped, each mergeable and deployable ahead of the rest, as the roadmap allows for carried fixes):
1. **Fix 51** - `harness/ops/checks.py`, `harness/db/models.py`, `harness/db/schema.py`, the migration's index statement, `verify.md`'s
   `check_results` rows, `tests/test_checks.py`. The task captures `EXPLAIN (ANALYZE, BUFFERS)` for the bounded `duplicate_trades` statement and
   journals it before adopting the statement, and takes §1.9's fallback if the plan does not prune (ruling I2). Depends on: nothing.
2. **Fix 46** - `harness/venues/kalshi/rfq_socket.py`, `rfq.py`, `docs/runbooks/research.md`, `verify.md`'s `rfqs arrivals` row,
   `tests/test_rfq_listener.py`. Depends on: nothing.
3. **Exclusion classes and coverage outcomes (§1.4)** - `harness/ops/exclusions.py` (`CLASS_OF` and `COVERAGE_CLASS_OF`), the behaviour-neutral
   promotion of the `expiry` and `no_book` literals to `plan.py` constants used at their `harness/execution/loop.py` write sites (ruling I8),
   `tests/test_exclusion_classes.py`. Depends on: nothing. (The Floor and report readers land in task 8.)
Wave 2 (the instrumentation spine):
4. **`coverage_samples` and `harness/ops/coverage.py` (§1.1)** - models, schema, migration, the two-phase helper (`scheduled` at enumeration,
   completion after the work), recorder and pipeline call sites, `tests/test_coverage_samples.py`. Depends on: 3 (for `COVERAGE_CLASS_OF`'s outcome
   names).
5. **Fix 48's closure and budget isolation (§1.5)** - `harness/strategy/pipeline.py`, `harness/recorder/tick.py`,
   `tests/test_pipeline_stage_order.py`, `tests/test_pipeline.py`, including the narrowed parity assertion and the separate suppressed-set assertion of
   §1.5(d)/D12 and the `variants_partial == []` case. Depends on: 4. Shares `pipeline.py` with task 8: serialized.
6. **Latency decomposition (§1.2)** - `harness/recorder/tick.py`, `harness/execution/loop.py`, `tests/test_latency_decomposition.py`. Depends on: 4.
   Shares `tick.py` with task 5: serialized after it.
Wave 3 (the denominators and the contract):
7. **Missing-stage denominator, normalizer evidence and the split proposal (§1.3)** - `harness/ops/coverage.py`, `harness/normalize/runner.py`
   (measurement only), `tests/test_coverage_denominator.py`. Depends on: 4, 5.
8. **Funnel episodes, t14 and Floor (§1.7, §1.4's readers, fix 55's coverage half)** - models, schema, migration, `harness/strategy/pipeline.py`,
   `harness/execution/loop.py`, `harness/report/tables.py`, `harness/dashboard/snapshots/floor.py`, `tests/test_funnel_episodes.py`,
   `tests/test_report_t14.py`. Depends on: 4, 7.
Wave 4 (the policy, designed not run):
9. **Holding/capacity policy baseline and comparison harness (§1.6)** - `harness/execution/policy.py`, `harness/execution/plan.py` (defaulted
   arguments), `harness/cli.py`, `docs/runbooks/replay.md`, `tests/test_policy_compare.py`. Depends on: 3; the *run* depends on 6B.
10. **Verification rows (§3)** - `docs/superpowers/autopilot/verify.md` only, last task, after every other task's behaviour exists.

## Rulings

The controller's rulings on the revision-1 design review (`.superpowers/sdd/results/design-6d-review.md`), dated 2026-09-13 and
copied verbatim from `.superpowers/sdd/plan-next-phase6d/design-rulings.md`. Every one is applied in this revision.

- Ruling C1: accepted - §1.5(d) states explicitly that D4 amends fix 48's frozen-baseline parity contract: the parity assertion narrows to `decision = 'candidate'` rows plus every candidate's `edge`, `stake`, `contracts` and labels, and the rejected-row difference is asserted separately to equal exactly the suppressed set (`rescore_suppressed`); that amendment is its own §8 decision row (D12) with the reversal "restore the full stage-6 loop"; D4 stands - why: decision 4 asks for duplicate work to be identified and removed, and the candidate set, prices, sizes and orders are provably unchanged (review §7.1) - cost if wrong: a rejected-row series discontinuity, bridged by `rescore_suppressed` (I11).
- Ruling C2: accepted, option (a) two-phase - one `outcome = 'scheduled'` row per cell written at enumeration (recorder: after `_due` resolves; pipeline: after stages 1/2 enumerate the priceable set x active variants), closed by the completion row for that cell in the same tick; the reconciliation query names scheduled rows with no completion row older than one tick period; §3 row 2 is restated against that definition (unclosed scheduled rows past one period = 0, or each explained); `CLASS_OF` gains a second map `COVERAGE_CLASS_OF` covering `no_fair`, `no_gap`, `budget_stage_skipped`, `not_due`, `cadence_none`, `variant_skipped`, `truncated` and any other coverage outcome §1.1 names, and §1.7(a) tests against it - why: the acceptance paragraph requires an explicit reason and interval for every scheduled unit, which only a pre-work record can supply; deriving at read time (b) reintroduces the inference §0.1 retires - cost if wrong: two inserts per domain per tick instead of one, bounded by the re-derived cap (I6).
- Ruling I1: accepted - §3 row 8 reads "pass or fail, never `skip`" for the four checks; the `intents_without_order_or_skip` `fail 483` is carried as its own open item in that row beside the three failing data checks - cost if wrong: none.
- Ruling I2: accepted - the plan task that bounds `duplicate_trades` captures `EXPLAIN (ANALYZE, BUFFERS)` for the bounded statement before adopting it and names the fallback (a BRIN-driven `ix_trades_ts_brin` subquery, or one grouping per day); the verify.md text either names the previous weekly partition when the 25 h window crosses the ISO week boundary or states that limit - cost if wrong: one extra EXPLAIN in the task.
- Ruling I3: accepted - §1.7(d) is sourced from `market_gap_snapshots.no_fair_reason` and `coverage_samples`, and states that the signal-level `has_fair` count ends at the deploy instant - cost if wrong: none.
- Ruling I4: accepted - the fixture stays; the expectation is restated as 12 units = 4 `completed`, 2 `no_fair`, 6 `variant_skipped`, carried into `tests/test_coverage_samples.py` - cost if wrong: none.
- Ruling I5: accepted - the three `runs.started_at` reads use `recent_run_notes`' cap-then-filter shape or are answered from `coverage_samples` (`ix_coverage_ts_domain`) and `metric_samples` (`ix_metric_samples_name_ts`); no new index on `runs` - cost if wrong: none.
- Ruling I6: accepted - §1.1 computes the cardinality bound explicitly from the declared grain (2 sports x 4 ttk buckets x 2 feeds x 3 market types x 7 variants = 336 evaluation cells, x outcomes, x 2 for the scheduled row) and sets `COVERAGE_ROW_CAP` above it; §2's MB/day and §3 row 10's threshold are re-derived from that number; the grain itself stays as decision 4 lists it - cost if wrong: a larger table than estimated, still inside the disk budget.
- Ruling I7: accepted - the episode tables get one multi-row upsert per run, capped like `COVERAGE_ROW_CAP`, with the per-tick upsert count stated; §2 and §3 row 10 include the row-version/bloat cost - cost if wrong: one more cap.
- Ruling I8: accepted - the plan's exclusion task promotes the `expiry` and `no_book` literals in `harness/execution/loop.py` to `plan.py` constants (behaviour-neutral) so the "classified exactly once" test enumerates them; `no_book` is classed as an annotation on a placement, not an exclusion, and §1.4 says so - cost if wrong: two constants.
- Ruling I9: accepted - §7.5 names `tests/test_rfq_refusal.py::test_the_transport_refuses_the_exact_quote_path` and the paper-gateway refusal case, and adds the yield guard's "quotes nothing while yielded" case to the refusal file - cost if wrong: none.
- Ruling I10: accepted - when stage 6 suppresses a direct-only variant it is marked complete (its direct universe is its full universe) and the extended stage-order test asserts `variants_partial == []` for it - cost if wrong: a partial flag that reads complete for a variant whose direct pass was itself incomplete (which `direct_complete` already tracks separately).
- Ruling I11: accepted - §0.8 and §3 gain a labelled measurement boundary: the deploy instant recorded in the journal, in t14's note and in the verify row, with `rescore_suppressed` as the bridge - cost if wrong: none.
- Ruling I12: accepted - §0.13/§4.1 name the Omarchy host `.env` under `/srv/sports-harness` and the `Settings.rfq_listener_enabled = True` default as the control, keep `deploy/nas.env` as a historical note only, and state the post-recipe read-back of the effective value (`harness health`/settings dump) - cost if wrong: none.
- Ruling M1-M9: all taken in revision 2 as the reviewer states them (M1 residual stated; M2 lower bound; M3 v2 §8.2 in the amend list; M4 line 52; M5 the three `run_id`-leading indexes named; M6 counterfactual labelling sentence; M7 `feed` null in the collection domain; M8 uniform one-liners; M9 §1.1 depends on the outcome names task) - cost if wrong: none.
- Ruling (round): one design-review round (plan-next step 2); revision 2 is followed by the controller's self-review (step 3) and the 3a audit, not a second reviewer dispatch - cost if wrong: a defect the plan review (step 4, opus reviewer) catches instead.
