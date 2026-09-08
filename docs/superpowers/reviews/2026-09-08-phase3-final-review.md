# Phase 3 final whole-branch review

Branch `phase3-paper-execution` at 4570acc against `main` at ae1e86c. Reviewed in eight passes by
one reviewer, no subagents dispatched: (1) mechanical invariant greps, (2) `harness/execution/`,
(3) `harness/settlement/`, (4) `harness/report/`, (5) recorder / venues / feeds / normalize,
(6) dashboard / ops / telemetry, (7) `harness/db/` + compose + Makefile + CLI + replay,
(8) runbook and `verify.md` against the shipped CLI and `harness/ops/checks.py`. The test suite
was not run (809 pristine at 04:00 CT on this head, per the per-task reports). Nothing in the
working tree, index, HEAD or branch state was mutated.

Note on the diff base: `main` at ae1e86c already carries Tasks 1, 2, 2b, 3, 3b, 4 and 4b (the
three mid-phase deploys), so this diff is Tasks 5-14. The invariants below were checked against
the merged tree, not only the diff.

Nothing in any data I read looked like an instruction addressed to me.

---

## Invariants

**1. No code path can send an order, a quote or an RFQ answer — PASS.**
- `git grep -nE '\.(post|put|delete)\(' -- harness/venues harness/execution harness/settlement`
  prints nothing. Widening it to all of `harness` finds hits only in `harness/dashboard`
  (FastAPI route decorators for `/kill` and `/unkill`).
- `harness/venues/kalshi/` gains exactly two methods, both GET: `fetch_series`
  (`harness/venues/kalshi/public.py:128`) and `fetch_market`
  (`harness/venues/kalshi/public.py:135`), plus the `status` parameter on `fetch_markets_all`
  already on `main`.
- `docker-compose.yml:105-119`: the merged `app-exec` block has `build`, `user`, `command:
  ["exec"]`, `env_file: .env`, `depends_on`, `healthcheck`, `restart`, `stop_grace_period` and
  nothing else. No `volumes:` key, no `environment:` key, no `KALSHI_*` and no
  `ODDS_API_KEY_FILE`. `harness/scheduler.py:60-68` (`build_executor`) constructs the executor
  with a session factory and nothing else — no HTTP client is created on the `exec` path.

**2. `harness gate` cannot return `passed = true` — PASS.**
`harness/report/gate.py:605-614`: `legal_decision` and `live_trading_env` return
`_result(criterion, False, False, 0, 0, {"manual": True})` unconditionally, with no session,
settings or environment input. `evaluate_gate` sets `passed=all(r.passed for r in
criteria.values())` (`harness/report/gate.py:641`), so both constants force `False`. `harness
gate` exits 0 regardless (`harness/cli.py:282-318`), which is the documented contract.

**3. `harness/variants/` untouched; `MAX_PRIMARY`/`MAX_SECONDARY`; the six frozen ids — PASS.**
`git diff --name-status ae1e86c 4570acc -- harness/variants/` is empty.
`harness/strategy/variants.py:59-60` holds `MAX_PRIMARY = 1` and `MAX_SECONDARY = 6` (U2's
value, landed before this diff base) and `variants.py` is not in this diff at all. The six-id
test (`tests/test_variants.py:127`) and the yes-only golden digest
(`tests/test_strategy.py:638`, `9c40d9a5…`) both still stand on this head.

**4. Gate criteria, thresholds, benchmark and BH families, the 72-cell grid, the cut-off — PASS.**
- `harness/report/gate.py:119-185` reproduces the plan's Task 11 criteria list verbatim,
  in order, with thresholds 150 / 0.30 / 0.0 / 0.0 / -0.010 / 0.010 / 0.0 / 90 / 0.90 / 0 and
  the two manual constants. `criteria_hash` is over the sorted definition strings only
  (`gate.py:188-198`), matching the controller's reverted M3 ruling.
- `GATE_BENCHMARKS` (`gate.py:61-62`) is the seven gated types; `result` and
  `opening_first_seen` are excluded, as §0.7(a) requires.
- `harness/report/tables.py:58-73`: `BH_Q = 0.10`, `HOLM_ALPHA = 0.10`, four price buckets,
  three TTK buckets, two sports, three market types, four staleness strata. The grid at
  `tables.py:683-687` is 4×3×2×3 = 72 per fair source over `FAIR_SOURCES` (direct, derived).
  `HEADLINE_PANEL = "gap_mid"` (`tables.py:86`) is the panel the controller's ruling named.
- Week-38 selection artefact and the `--confirm` restriction are in
  `harness/report/weekly.py`; `harness/report/__init__.py` renders `CRITERIA_TEXT` from
  `gate.CRITERIA`, so the report's hash and the stored rows' hash cannot diverge.
- The pre-registration record is byte-identical on this branch
  (`git diff --stat ae1e86c 4570acc -- docs/superpowers/reviews/2026-09-07-phase2-preregistration.md`
  is empty).

**5. Schema additive only — PASS.**
`git diff ae1e86c..4570acc -- harness | grep -iE 'drop table|drop column|truncate|alter column
.* type|rename to|drop constraint'` returns only comments, docstrings and unrelated identifiers
(`truncate_ms`, a `--truncate` CLI flag, prose). Every statement in
`harness/db/schema.py:_COLUMN_DDL`, `_INDEX_DDL`, `_TAPE_DDL` and `TAPE_NEW_INDEXES` is
`add column if not exists` / `create index if not exists` / `create or replace view`; the model
indexes go in through `index.create(conn, checkfirst=True)` (`schema.py:371-374`). `drop_schema`
is generated from `Base.metadata` and is only ever called against `harness_test`. No new model
index was added in this diff (`git diff … -- harness/db/models.py | grep 'Index('` is empty), so
init-db builds no index on a large existing table.

**6. No new dependency — PASS.** `git diff ae1e86c 4570acc -- pyproject.toml constraints.txt`
is empty. The statistics in `harness/report/stats.py` are stdlib `math` only.

**7. No new outbound host — PASS.** The two new calls are `{kalshi_base_url}/series/{s}` and
`{kalshi_base_url}/markets/{ticker}`, the existing public host. No new client is constructed
anywhere; `build_settler` reuses `KalshiPublic` (`harness/scheduler.py:28-38`).

**8. Secrets never logged or rendered — PASS, with one Minor.** No brief-added code reads
anything under `secrets/`; `git diff … | grep -E 'secrets/|api_key|private_key'` on the diff is
empty. See Minor 7 for the one place an exception repr reaches a rendered column.

---

## Findings

### Critical (Must Fix)

None.

### Important (Should Fix)

**I1. `verify.md`'s Layer 2b block and `harness/ops/checks.py` no longer agree, so
`check_results` "all pass" is not "every Layer 2b invariant = 0".**
`docs/superpowers/autopilot/verify.md:145-208` holds 27 invariant statements. `CHECKS`
(`harness/ops/checks.py:42-202`) holds 19, and they line up exactly with `verify.md:145-190`.
The eight statements under `-- Task 12b telemetry tables` (`verify.md:191-208`:
`metric_samples` negative values, empty `operator_events.summary`, negative
`order_watch_samples` queues, `equity_snapshots.mtm_coverage` outside [0,1], a score going
down in `game_score_events`, an unknown `check_results.status`, a future `report_runs`, an
orphan `report_cell`) have no counterpart in the registry. The comment at `verify.md:163`
("the remaining CHECKS … verify.md and CHECKS agree") is therefore false as written, and the
Task 12b ruling that "CHECKS covers every Layer 2b invariant statement in verify.md" no longer
holds after Task 14 extended the file. This is exactly a cross-task seam: Task 12b built the
registry, Task 14 grew the contract afterwards. Fix either by adding the eight checks (all are
bounded, tape-free, and fit the existing `Check` shape) or by moving them under an explicit
"manual only, not in CHECKS" heading in `verify.md`. Cheapest is the eight checks.

**I2. `store.newest_event_ts(at=…)` cannot prune within its partition and will exceed the
executor's own 10 s statement timeout on any real replay.**
`harness/execution/store.py:311-325`:

```sql
select ts from orderbook_events where ts <= :at order by id desc limit 1
```

`orderbook_events` is weekly-partitioned on `ts` with a per-partition PK `(id, ts)`. `ts <= :at`
prunes partitions that start after the instant, but inside the partition holding `at` the plan
is a backward walk by `id` that filters every row taped *after* `at` before it reaches one at or
before it. Replaying a Sunday game day means walking from that week's last event back to the
grid instant — up to a full game day of tape, 30M+ rows at the measured 4M rows/h — and
`_body` calls it once per 15 s grid step (`harness/execution/loop.py:340`). The replay CLI binds
`EXEC_STATEMENT_TIMEOUT_MS` deliberately (`harness/cli.py:464-467`), and
`replay._execute` turns any step error into `ReplayStepError` and exits 1
(`harness/replay.py:232-235`), so the Monday replay-versus-live duty (R14, `verify.md`) fails at
step 1 rather than running slowly. The deferred Task 13 minor called this "a same-week replay"
cost; it is not restricted to the same week, and it is a hard failure rather than a slow query.
The loop only needs "is the tape's newest row older than `book_max_age_s`", so a lower bound
(`and ts >= :at - interval '10 minutes'`, answered off `ix_obe_ts_brin`) is enough.

**I3. `book._MAX_EVENT_ID_AT` is an unbounded `max(id)` over the whole tape.**
`harness/execution/book.py:120`:

```sql
select max(id) from orderbook_events where ts <= :fetched_at
```

No ticker predicate and no lower `ts` bound, so it has the same shape as I2: partition pruning
removes later weeks, then a backward id walk filters everything taped after `fetched_at` inside
the surviving partition. It runs on the REST-anchor branch of both `load_book`
(`book.py:348-349`) and `book_at` (`book.py:413-414`). `book_at` is what the markouts stage
calls for every horizon with no nearby quote (`harness/settlement/markouts.py:301-308`), at
instants hours or days in the past, under the 900 s batch timeout — so it degrades markouts
rather than failing them, but on the same partition-scan mechanism. Bound it below with the
same interval `load_book` already uses for its delta scan.

**I4. A game whose `compute_benchmarks` savepoint raises is permanently locked out of its eight
pre-kickoff benchmarks, and the `gap_outcomes` watermark then passes it as finished.**
`harness/settlement/benchmarks.py:301-310`, `_ELIGIBLE_GAMES`:

```sql
and not exists (select 1 from benchmarks b where b.game_id = g.id)
```

The predicate does not exclude `benchmark_type = 'result'`. Registration order protects the
normal path (`benchmarks` runs before `result_benchmarks`, confirmed by executing
`load_stages()`: `settle, venue_result, benchmarks, result_benchmarks, gap_outcomes_drain,
order_clv, markouts, housekeeping, report_wtd`). It does not protect the failure path: a game
whose `_process_game` raises inside its savepoint (`benchmarks.py:338-350`) rolls back with no
rows, `insert_result_benchmarks` then writes its `result` rows in the same pass, and from that
moment `not exists` is false forever — the game never gets `pinnacle_t5`, `consensus_t5/60/180`,
`kalshi_mid_t5`, `kalshi_last_trade_pre_kick`, `novig_devig_t5` or `opening_first_seen`. The
same `result` row makes `has_result` true in `_NEXT_GAP_SNAPSHOTS`
(`harness/settlement/order_clv.py:159-169`), so `drain_gap_outcomes` advances its watermark past
every one of that game's snapshots as "finished" and they can never pick up a later benchmark
either. The loss is silent apart from one `ctx["errors"]` entry on the pass that raised. One-line
fix: `and b.benchmark_type <> 'result'` in `_ELIGIBLE_GAMES`.

**I5. The runbook's `export-fixture` paragraph does not match the shipped CLI.**
`docs/runbooks/phase0-deploy.md:142-145` says `harness export-fixture` "writes a recorded game
day's raw responses, WS snapshot, deltas and prints out of the database as a self-contained
fixture **directory**, the same shape as `tests/fixtures/day_2026-09-13/`". Three problems:
`harness/cli.py:488-528` writes a single JSON document through `write_export(doc, out)`, not a
directory; `--out` is required and `--kind` selects `day` (needing `--from-run`/`--to-run`) or
`ws-tape` (needing `--ticker`/`--from`/`--to`), none of which the paragraph mentions; and
`tests/fixtures/day_2026-09-13/` does not exist — the committed fixture is
`tests/fixtures/day_synthetic/day.json`. An operator following the runbook gets a Typer usage
error. The `replay` paragraph (`phase0-deploy.md:134-141`) is correct and matches
`harness/cli.py:446-486` flag for flag, including `--file` and `--execute`; `settle`, `report`,
`gate` and `exec-once` also match. `harness note --kind {...}` (`harness/cli.py:324-344`) is not
listed among the phase-3 one-offs at all.

**I6. `report_wtd` runs the whole ten-table weekly build hourly inside `app-run`, and two of
those tables materialise a week of rows in Python.**
`harness/settlement/report_wtd.py:42-64` calls `weekly_tables(session, year, week, settings)`
once an hour, guarded only by `budget.remaining_s() >= 120` at entry — nothing bounds it once
it has started. `_T4_SNAPSHOTS` (`harness/report/tables.py:622-632`) loads every
`market_gap_snapshots` row of the week into a Python list, and `_T5_SNAPSHOTS`
(`harness/report/tables.py:823-830`) loads every WebSocket snapshot `raw` body for the week's
moved tickers, parsing each into a `BookState`. The NAS has ~1 GB of RAM available (roadmap /
R17 sizing), and this is the largest new allocation the phase adds. It also competes with the
`settle` stage for the same hourly slot. Not a blocker on Monday (the ISO week starts empty) but
it grows all week; see the deploy notes for what to measure by Saturday. If it bites, the
cheapest lever is raising `report_wtd.PERIOD` or skipping t4/t5 in the provisional run.

**I7. Markouts rebuild a book from the tape once per (anchor, horizon) with no reuse.**
`harness/settlement/markouts.py:297-309` returns a closure that calls `book_at(session, ticker,
instant)` afresh for every horizon that has no quote within 60 s, and `_process_order`
(`markouts.py:342-364`) walks up to four anchors × six horizons per order. Each call re-anchors
on the ticker's newest snapshot at or before the instant and replays every delta since it
(`book.py:422-424`); when a ticker has not been re-subscribed for hours, that is hours of deltas
per row. The fair rows are cached per shape (`markouts.py:322-328`) but the book is not. This is
the stage that feeds gate criteria 4 and 5, so a stage that keeps running out of budget shows up
as a gate that reads `insufficient` rather than as an error.

### Minor

**M1.** `Settings.gap_outcomes_batch` (`harness/config/settings.py:64`) is never read.
`gap_outcomes_drain_stage` calls `drain_gap_outcomes(session, budget=budget)`
(`harness/settlement/order_clv.py:293`), taking the function's own default of 50 000. Same value,
so no behaviour differs, but the setting is named in Amendment 2's frozen block and an operator
changing it would see nothing happen.

**M2.** `Order.fair_cross_fill` (`harness/db/models.py:447`) is declared and never written or
read anywhere in `harness/` or `tests/`. Addendum §2 keeps it "as a separately named flag for
the report only"; no code computes it and table 6 does not print it.

**M3.** `harness/ops/checks.py:188-197` (`fills_outside_placement_window`) hard-codes
`interval '10 minutes'` where the executor uses `Settings.exec_kickoff_cutoff_min`. If that
setting ever moves, the invariant reports false positives.

**M4.** `_ORDER_CLV_CANDIDATES` (`harness/settlement/order_clv.py:60-76`) tests for a missing
`(order, benchmark_type)` pair against benchmarks of the *game*, not of the order's *shape*. An
order whose shape legitimately lacks a type another shape in its game has (`novig_devig_t5` is
main-lines-only; `kalshi_last_trade_pre_kick` needs a trade) stays a candidate forever and is
re-scanned on every hourly pass, inserting nothing. Cheap per row, but the candidate list grows
monotonically with the season.

**M5.** Several stages report `budget_exhausted = not budget.ok()`
(`harness/settlement/settle.py:461-467`, `benchmarks.py:477-484`, `order_clv.py:291-300`,
`markouts.py:394-396`), which is true whenever the budget happened to run out *after* the stage
finished all its work. Already on the deferred list for Task 7; it makes the verify.md rule
"`budget_exhausted = true` on two consecutive rows is a carried fix" noisier than intended.

**M6.** Two queries hit every partition because they carry no `ts` predicate:
`store.event_ts` (`harness/execution/store.py:328-330`, `where id = :i`) and
`_FETCHED_BY_SOURCE` (`harness/recorder/tick.py`, `where run_id = :run_id` on `raw_responses`).
Both are index probes per partition rather than scans, so the cost is linear in partition count
and small today; worth a lower `ts` bound before the partition count gets into the dozens.

**M7.** `WsRecorder` writes `repr(e)[:200]` into `operator_events.summary` on disconnect
(`harness/venues/kalshi/ws.py`, `_write_ws_metrics` neighbourhood). `telemetry.sanitize_reason`
strips punctuation but does not apply the F55 redaction patterns, and the dashboard renders the
column. websocket-client's exception messages do not carry request headers today, so this is
theoretical — but it is the one path where an exception string reaches a rendered field without
going through the log redactor.

**M8.** `tests/test_checks.py:114`
(`test_run_checks_resets_statement_timeout_after_the_last_check`) has no `assert`: it proves its
point by not raising on `pg_sleep(0.15)`. It is a real test, but it is the only one in the suite
that reads as assertion-free to a scanner.

**M9.** `_benchmarks_for_shape` names two different functions in two modules
(`harness/settlement/benchmarks.py:243` builds rows to insert;
`harness/settlement/order_clv.py:97` reads rows back). Both are correct; the shared name makes
grepping the seam harder than it needs to be.

**M10.** `harness/db/models.py` is at 831 lines and `harness/report/tables.py` at 1210. Neither
is unreasonable for what it holds (one model per table; one function per report table), and both
are internally sectioned. No action, recorded so the next reviewer does not re-derive it.

### What I checked and found clean

- **The executor ↔ fills ↔ books contract.** Cursor discipline is right: `_state_columns`
  (`loop.py:945-960`) persists `FillResult.state.cursor_event_id`, never the cache head, and
  `_sim_book` (`loop.py:595-612`) hands the simulator `base.copy()` when the cursor sits at the
  previous step's book and `book_at(ts_of(cursor))` otherwise. The cache is never handed out.
  The once-per-order cross is gated three ways: `SimState.crossed` per track
  (`fills.py:364-369`, `fills.py:383-385`), the loop's `crossed_already = row.crossed or
  row.nw_crossed` (`loop.py:558`, `loop.py:566`), and `uq_fill_source` on the crossing delta id.
  The `no_watcher` track writes no ledger row and no position (`loop.py:582-590` passes
  `ledger=False`; `_POSITIONS` in `store.py:407-415` filters `fill_method = 'queue_model'`).
- **Settlement ↔ benchmarks ↔ CLV ↔ markouts ↔ report ↔ gate.** The late `result` row is handled
  by per-pair candidacy in `compute_order_clv` and by the `has_result` watermark rule in
  `drain_gap_outcomes` (both correct except for the failure path in I4). `target_ts` is stored
  per type (`benchmarks.py:263-296`), with `opening_first_seen` storing its own snapshot ts and
  `stale = False`. The family panel is `gap_mid` per the controller's ruling; `cluster_diff_ci`
  (`stats.py:190-236`) demeans within side, which is the right influence function for criterion
  6. Criteria 3, 6 and 7 all read `order_clv`, never `gap_outcomes` (`gate.py:303-312`,
  `gate.py:427-440`).
- **Stage registry order and the settler's budget.** Order verified by executing `load_stages()`;
  it matches addendum §3 exactly, with `report_wtd` appended. One shared `Budget`
  (`job.py:164`), a stage exception recorded and stepped over (`job.py:212-221`), `job_runs`
  written and never `runs.notes`.
- **Telemetry never changes a decision and never fires in replay.** Every executor writer is
  behind `if not self.replay` (`loop.py:243`, `loop.py:320-328`, `loop.py:362-387`) and inside
  its own `session.begin_nested()`; `_metrics_acc` is only advanced under the same guard. The
  recorder's metric batch rolls back only its own uncommitted work — `normalize_new` and
  `price_and_signal` commit internally (`harness/normalize/runner.py:144,151,189`,
  `harness/strategy/pipeline.py:192`).
- **The dashboard's `/api/summary` keys.** The eight phase-2 keys are unchanged and the nine new
  ones are appended (`harness/dashboard/app.py:558-577`). Every new section is bounded by a
  window, a `LIMIT` or both. `/kill` keeps the `Sec-Fetch-Site` rule and the F50 sanitizer, now
  shared with `telemetry.event`.
- **Partition-aware queries.** `_PRINTS`/`_DELTAS` (`store.py:333-402`), `_TRADE_AT_OR_BEFORE`
  and `_QUOTE_AT_OR_BEFORE` (`benchmarks.py:208-218`), `_T5_SNAPSHOTS`, `_T6_QUEUE_ACCURACY`,
  `_STORED_RESULTS` (`settle.py:307-321`, `fetched_at > :since`) and every dashboard tape read
  all carry a `ts`/`fetched_at` bound that prunes. The `market_gap_snapshots` lateral in
  `store._MARKETS` rides `ix_gap_market_created (venue_market_id, created_at)`, which already
  exists on the deployed build. I2, I3 and M6 are the only exceptions.
- **The recorder's dated ESPN refetch and the funnel's notes aggregation.** The dated body is
  stored under `params = {"dates": …}` and `_latest_body` excludes it with
  `~RawResponse.params.has_key("dates")`, so it cannot be served as "today's" body to the paid
  cadence planner. The Eastern day is computed from two independently converted midnights, so a
  DST transition does not shift the window. The funnel's uncapped 24 h notes scan replaces the
  86-92 s direct scan and keeps the rendered keys.

---

## Deferred-list triage

**Must fix before merge — one item, escalated out of the deferred list:** Task 13's
"`_NEWEST_EVENT_AT` backward walk cost for a same-week replay" is not a minor and is not
same-week-only; it is I2 above, and it fails the Monday replay-versus-live duty outright rather
than making it slow. Everything else I would fix before merge (I1, I4, I5) came out of this
review, not out of the deferred list.

**The remaining 19 deferred and parked items can wait.** In one clause each, the ones a reader
might otherwise worry about: Task 1's seven `feed_kind = NULL` fair values on run 2322 are
already visible as table 4's `feed unknown` column and fall back to the flat `stale_s` rule, so
they are labelled rather than lost; Task 6's "two position definitions" is answered by table 6's
per-variant scoping and the report headers naming which they read; Task 6's NULL
`fair_books_json` is a pricing-side column that does not exist yet and blocks nothing Task 9
reads; Task 7's `_STORED_RESULTS` cost is bounded to a 7-day `fetched_at` window that prunes;
Task 10's `--year 2026` and 17-column table 4 are plan-mandated; Task 11's eight unnamed minors
are all inside the criteria's `detail` rendering, not their arithmetic; the two-key economic
position for `sharp_two_sided` is Amendment 3's text and affects no primary number.

---

## Deploy notes for the controller

1. **`make deploy-nas` is the whole of it.** The Makefile order (`Makefile:37-41`) is build →
   `up -d postgres` → `init-db` → `seed-teams` → `variants register` → `up -d`, so `app-exec`
   starts only after the schema step. `partition-bulk-tables` is a no-op; the runbook already
   records the 2026-09-08 01:31-02:07 CT run.
2. **What `init-db` does on a database that already has phase 2 and the partitioned tape:** adds
   seven nullable/defaulted columns to `orders`, creates ten new (empty) telemetry and report
   tables, and adds seven indexes — all on new or small tables. No new model index touches
   `market_gap_snapshots`, `fair_values`, `signals` or the tape, so nothing takes a long
   `ShareLock`. `_takes_new_indexes` still refuses to build the tape btrees non-concurrently,
   and they already exist from the partition migration.
3. **`app-run` gains a volume** (`./pgdata:/pgdata-ro:ro`), so it is recreated rather than
   restarted. `os.statvfs("/pgdata-ro")` needs search permission on `/` only, not on the pgdata
   directory itself, so its 0700 postgres ownership should not block it; if it does,
   `_host_disk_free_gb` returns `(None, "mount absent")` and `host.disk_free_gb` is simply
   skipped with a log line. Nothing fails either way.
4. **`app-exec`'s first heartbeat lands about 15 s after start** (APScheduler interval jobs fire
   after one interval), well before the first `exec-health` probe at 60 s. There is no
   `start_period` on the healthcheck, so a first step that errors leaves the container
   `unhealthy` in `docker compose ps`; compose does not restart on unhealthy, so that is a
   signal to read, not an outage. Check `select last_loop_at, loops, last_error from
   exec_heartbeat;` within the first minute.
5. **The settler's first automatic pass is an hour out.** `build_scheduler` adds it as a plain
   `interval` job (`harness/scheduler.py:54-56`), so nothing settles until
   `settle_period_s = 3600` has elapsed. Run `docker compose run --rm -T app-run settle` once by
   hand after the deploy to get the first pass, the first `job_runs` row and the first
   `housekeeping` note (the growth projection needs a prior note before it can report anything).
6. **`Settings.gate_variant` is still `sharp_direct`** (`harness/config/settings.py:68`) and
   Amendment 3 in the pre-registration record is still a placeholder. Under U5 the gate row
   should be `sharp_two_sided` from amendment 3 onward; if `.env` on the NAS was not flipped at
   the Task 4b deploy, `harness gate` will mark the primary's row instead (the D1 fallback, which
   is correct behaviour, not a bug) and Amendment 3's "first evaluation on which the gate row
   switched" will be a later date. Decide which before running `harness gate` for the record.
7. **Watch through the first game day:** `app-run`'s RSS and the `settle` job's wall time
   (`select job, status, budget_exhausted, started_at, finished_at from job_runs order by id desc
   limit 5;`) — that is where I6 would show. And
   `select count(*) from market_gap_snapshots where created_at >= date_trunc('week', now());`
   sizes the hourly `report_wtd` load directly.
8. **If the Monday replay duty is on the checklist for this deploy**, expect it to fail on I2
   until that query is bounded; the unit test's strict equality still passes because its fixture
   tape is tiny.

---

## Assessment

**Merge readiness:** Ready after the Importants

**Reasoning:** No invariant is violated and no path can send, spend or silently lose data — the
executor has no client, no secrets and no volumes, the gate cannot pass, the frozen ids and every
pre-registered threshold are intact, and the schema is additive. Four of the seven Importants are
one-line or one-paragraph fixes that should land before the merge because they are cheap and
because two of them (I1, I4) quietly weaken the record the phase exists to produce; I2 and I3 are
the partition-scan defects the task reviews could not see across module boundaries, and I2 turns
the Monday replay duty into a hard failure. I6 and I7 are load risks to measure on the first game
day rather than blockers, and I5 is a runbook paragraph.
