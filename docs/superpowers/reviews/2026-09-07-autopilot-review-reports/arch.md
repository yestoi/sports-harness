# Adversarial review — architecture and operations

**Date:** 2026-09-07
**Lens:** will the code, data, and deployment hold up for a full football season under
autonomous change, and are the phase 3–4 plans and the loop's verification contract
technically sound?
**Reviewed:** v2 spec §4/§5.5/§13/§14/§15; `2026-09-06-adversarial-review.md`;
`2026-09-07-phase3-paper-execution-design.md`; `2026-09-07-phase3-paper-execution.md`
(all 14 tasks); `.superpowers/sdd/2026-09-07-phase3-paper-execution/progress.md`;
`autopilot/roadmap.md`, `autopilot/verify.md`, `.claude/skills/autopilot/SKILL.md`,
`2026-09-07-autopilot-design.md`; `2026-09-07-phase2-final-review.md`;
`docs/runbooks/phase0-deploy.md`; and the code named in the brief, read-only.

---

## 0. Verdict

The build loop is sound and the phase 3 design is the right shape: a separate executor
process, pure decision functions with an explicit `now`, idempotent inserts everywhere,
and replay reconstructing live decisions from stored rows. Nothing in the plan is
architecturally wrong.

What the plan does not survive is **scale over a season and the passage of time**. Three
classes of problem:

1. **Storage and Postgres operations.** The 1 TB budget is not a comfortable margin, it is
   roughly the season's own projection. The remedies the addendum defers ("only if the
   budget line is crossed") become 30× more expensive at the moment they are needed, and
   one of them (anti-wraparound vacuum on a 780 GB heap) arrives on its own schedule
   whether or not the budget is crossed.
2. **The 15-second executor's per-loop cost is unbounded**, its tape cursors lose rows,
   and for most of its candidates the book it needs does not exist. As planned, phase 3
   produces far less paper execution than the reader of the plan would expect, and does
   not say so.
3. **Batch analytics are placed inside the recorder tick**, where they compete with a
   100-second fetch budget on a 120-second cadence and hit a 30-second statement timeout
   that nobody has noticed is there.

Everything below is a specific file, a specific section, and the exact change.

---

## 1. Storage, vacuum, and the 1 TB budget (brief item c)

### 1.1 The season projection is the budget, not a fraction of it

Measured: 19M rows and 8.7 GB of `orderbook_events` in four hours → **458 bytes/row**
including its three indexes. The rest of the database grew ~0.7 GB in 12 h → **~1.4 GB/day**.

Season = 2026-09-09 → ~2027-01-11 ≈ 124 days. Taking the observed 4M rows/h as the peak
and assuming a mean of 3M/h across a 12-hour CFB Saturday, 2.5M/h across a 12-hour NFL
Sunday, 1.5M/h for a 4-hour Thursday/Friday/Monday night, and a 150k/h baseline the rest
of the time (games inside `ws_lookahead_hours = 24` keep the subscription busy):

| Component | Rows | Size |
|---|---|---|
| Weeks 1–14, both sports (~109M rows/week) | 1.53B | ~700 GB |
| Weeks 15–18, NFL + bowls (~50M rows/week) | 0.20B | ~92 GB |
| **`orderbook_events` total** | **~1.7B** | **~780 GB** |
| Everything else at 1.4 GB/day | — | ~174 GB |
| `gap_outcomes` (phase 3, ~110M rows; §3.2) | 110M | ~9 GB |
| **Total** | | **~960 GB** |

Error bars are wide, call it ±40%, i.e. **550 GB to 1.35 TB**. The point is not the point
estimate. The point is that the budget line is inside the confidence interval, so the
"decide when it is crossed" policy has a real chance of firing, and it fires at the worst
possible time — mid-season, on a table too large to fix cheaply.

### 1.2 Two remedies stop being available at scale

- **A bulk `DELETE` does not return disk.** Dead tuples become reusable space, not free
  space; `pg_database_size` does not fall and the NAS volume does not recover. Reclaiming
  it needs `VACUUM FULL` (ACCESS EXCLUSIVE for the duration, plus a full second copy of the
  table on disk) or `pg_repack`, which is not installed and cannot be installed into
  `postgres:16` without a custom image. At 780 GB on a 3.5 TB volume this is a multi-hour
  outage that also has to fit its own copy.
- **Partitioning cannot be added retroactively without a full rewrite.** Converting an
  existing heap into a partitioned table means creating the partitioned parent and copying
  every row: 780 GB of read, 780 GB of write, 780 GB of transient space, and a WAL burst of
  the same order. At the current ~30 GB it is a few minutes.

### 1.3 The forcing function nobody has scheduled: anti-wraparound vacuum

`ws_sink` commits every ≤100 rows or 2 seconds. At 4M rows/h that is ~40k transactions/h
from the tape alone; add the tick, the executor at 4 loops/minute, and the dashboard, and
the cluster burns on the order of **100k XIDs/hour ≈ 2.4M/day ≈ 300M over the season**.

`autovacuum_freeze_max_age` defaults to 200M and `vacuum_freeze_table_age` to 150M. So
**roughly two-thirds of the way through the season the cluster will trigger an aggressive
freeze scan of the whole `orderbook_events` heap**, un-cancellable, at the default
`autovacuum_vacuum_cost_limit = 200` and `cost_delay = 2ms`. Dirtying pages costs 20 units
each, giving roughly 8 MB/s of throttled progress: **~27 hours of saturated NAS I/O plus a
WAL burst of the table's own size**, quite possibly landing on a Saturday.

With weekly partitions, each partition stops receiving inserts when its week ends, gets
frozen once shortly after, and is never scanned again. This alone justifies the change.

### 1.4 The change

**Add a new task to `docs/superpowers/plans/2026-09-07-phase3-paper-execution.md`, before
Task 3 (call it Task 2b, "Partition the bulk append-only tables").**

The codebase already contains the whole pattern. `RawResponse` in
`harness/db/models.py:28-38` carries `__table_args__ = {"postgresql_partition_by": "RANGE
(fetched_at)"}` with the composite PK `(id, fetched_at)` and the comment explaining why;
`harness/db/schema.py:17-38` has `_partition_name()` and `ensure_partitions()`; the tick
calls `ensure_partitions(session, now)` first thing in `maybe_tick`
(`harness/recorder/tick.py`, entry point). Repeat it exactly:

- `OrderbookEvent`: PK becomes `(id, ts)`, `__table_args__` gains
  `{"postgresql_partition_by": "RANGE (ts)"}`, keep `ix_obe_ticker_ts` and the BRIN.
- `VenueTrade`: PK becomes `(venue, trade_id, ts)`, partition by range on `ts`.
- Extend `ensure_partitions()` to create weekly partitions for all three tables, and make
  the executor's process call it too (or keep it in the tick only — the tick runs every
  30 s, which is sufficient).
- Migration on the NAS: `orderbook_events` is ~35 GB at deploy time. Rename it aside,
  create the partitioned parent, `INSERT INTO … SELECT` week by week (or simply attach the
  old table as the partition covering everything up to the cutover instant, which is free —
  `ALTER TABLE … ATTACH PARTITION` with a matching CHECK constraint added `NOT VALID` then
  validated). **The attach path is the right one: it is metadata-only and takes seconds.**
  Write it as a one-off `harness partition-bulk-tables` CLI command so it is tested, not a
  hand-typed psql session at 3 a.m.

**Amend `docs/superpowers/specs/2026-09-07-phase3-paper-execution-design.md`:**
- §0.6 Retention: strike "no partitioning of `orderbook_events` in phase 3"; replace with
  "`orderbook_events` and `venue_trades` are range-partitioned weekly from phase 3; the
  1 TB budget governs when partitions are archived and dropped, not whether partitioning
  exists."
- §8 Out of scope: strike "orderbook compaction (only if the budget line is crossed)";
  replace with "dropping sealed partitions — the mechanism ships in phase 3, the policy is
  a user decision at the 800 GB line."

**Amend `docs/superpowers/autopilot/roadmap.md` §Phase 6 item 8** ("Orderbook compaction
only if the database passes 800 GB") to "Archive-and-drop policy for sealed weekly
partitions; the partitioning itself ships in phase 3."

**Cost if ignored:** a mid-season choice between a multi-day table rewrite, a multi-hour
outage, and losing the tape — the one asset in this project that cannot be re-acquired.

### 1.5 Stock Postgres settings are the cheapest fix in the review

`docker-compose.yml` runs `image: postgres:16` with no `command:` and no tuning, so the
cluster ingesting 4M rows/hour is running `shared_buffers = 128MB`, `max_wal_size = 1GB`,
`maintenance_work_mem = 64MB`, `effective_cache_size = 4GB`, `random_page_cost = 4`,
`autovacuum_vacuum_cost_limit = 200`.

That explains the observed "one-hour count takes ~23 s cold": with 128 MB of shared
buffers, an hour of events (~2 GB of heap) is read from disk every time. And
`max_wal_size = 1GB` against a WAL rate of roughly 2–3 GB/hour at peak means a checkpoint
every 20–30 minutes, each one re-triggering full-page writes for every touched page — the
dominant term in the write amplification.

**Change `docker-compose.yml`, `postgres` service**, adding (sized after the loop reads the
NAS's actual RAM with `ssh … free -g`; the figures below assume 8 GB):

```yaml
    command:
      - postgres
      - -c
      - shared_buffers=2GB
      - -c
      - effective_cache_size=5GB
      - -c
      - maintenance_work_mem=1GB
      - -c
      - max_wal_size=16GB
      - -c
      - min_wal_size=2GB
      - -c
      - checkpoint_timeout=15min
      - -c
      - autovacuum_vacuum_cost_limit=1000
      - -c
      - random_page_cost=1.1
      - -c
      - work_mem=32MB
```

Note `shared_buffers` needs a matching `shm_size:` (compose default is 64 MB and Postgres
uses mmap'd shared memory, so this is usually fine, but set `shm_size: 1gb` to be safe).

This is a one-line-per-setting compose change, reversible, and it improves every query in
the system including the executor's.

### 1.6 pg_dump of bulk tables — the phase 4 backup decision does not survive contact (brief item h)

`roadmap.md` §Phase 4 item 7 specifies a nightly dump excluding the five bulk tables plus a
**weekly full dump Sunday 04:00 CT**, encrypted with `age`, retention 30 nightly and
8 weekly.

Two problems:

- **Space.** Eight retained weekly full dumps of a 0.5–1 TB database, even at 3:1
  compression, is 1.3–2.7 TB on a volume with 3.5 TB free that also holds the database
  itself. The retention policy and the database cannot both exist by December.
- **Vacuum.** `pg_dump` runs in one repeatable-read transaction. For the hours it takes to
  dump 780 GB it pins the cluster-wide xmin horizon, so **no vacuum anywhere in the cluster
  can remove a dead tuple for the duration**. On a Sunday at 04:00 CT that overlaps NFL
  pre-game recording.

**Change `roadmap.md` §Phase 4 item 7:**
- The weekly dump excludes the same five bulk tables as the nightly one. There is no
  "weekly full dump".
- Bulk-table durability comes from **per-partition archives**: once a weekly partition
  stops receiving rows, dump it once (`pg_dump -t orderbook_events_y2026w38 -Fc`, or
  `COPY … TO PROGRAM 'zstd -T2 > …'`), encrypt, store, and never touch it again. Cost is
  linear in the season, not quadratic in the retention count, and each archive is a short
  transaction over a sealed table. This is only cheap once §1.4 lands.
- Add one line: the tape's protection between the weekly archive and the RAID is nothing.
  State it so the user can decide, rather than discovering it.
- Add a loop rule: backups are not "done" until one restore has been exercised into a
  scratch database and row counts compared. A dump nobody has restored is a hypothesis.

---

## 2. The executor at 15 seconds (brief item a)

### 2.1 There is no ceiling on concurrent open orders

`harness/variants/sharp_direct.yaml` is the **primary** and sets `apply_caps: false`, so
`max_open: 25` is a label, not a gate — the phase 3 addendum §1 says so explicitly ("gates
in the executor for `constrained`, labels only for `sharp_direct`").

`harness/strategy/run.py:172` shows `ttk` is a **lower** bound only
(`row.ttk_minutes > cfg["min_ttk_min"]`), and `harness/pricing/fair.py` prices games in
`[now − 4h, now + 8d]`. So a candidate on a game eight days out is placed, never reaches the
kickoff cutoff, and stays open for eight days. With ~500 matched markets and a working
staleness rule, `sharp_direct` open orders accumulate into the hundreds and stay there.

Each open order costs, every 15 seconds: a book advance, a print scan, a fair lookup, and
a plan evaluation. At 150+ open orders the loop will not close in 15 s; APScheduler
`coalesce=True` silently drops the missed loops; the heartbeat is still updated by whichever
loop is running, so **the dashboard shows green while the executor is running at a quarter
of its nominal rate**.

**Change `docs/superpowers/plans/2026-09-07-phase3-paper-execution.md`:**
- Task 2, Settings: add `exec_max_open_orders: int = 150`.
- Task 5, `plan_actions`: after the cap checks, if placing would exceed
  `exec_max_open_orders` across all variants, emit `Skip(intent_id, "exec_capacity")`.
  Order the intents by descending `edge` so the ceiling truncates the least valuable
  candidates, not the alphabetically last ones (the phase 2 final review finding 3 made
  exactly this mistake and it is worth not repeating).
- Task 2, `ExecHeartbeat`: add `last_loop_ms int`, `p95_loop_ms int`, `loops_skipped int`.
  Without a loop-duration metric the failure above is invisible until it is total.

### 2.2 The tape cursors lose rows — two independent bugs

Task 6 step 3 specifies "prints/deltas with `id` beyond the order's `last_tape_event_id` /
`last_trade_ts` (persist both on the order as `tape_cursor_event_id`,
`tape_cursor_trade_ts`)".

**Bug A — `venue_trades` has no monotonic insertion key, and REST backfills go backwards.**
`harness/db/models.py:157-170`: the PK is `(venue, trade_id)`; there is no `id`. So the
plan's cursor is a **timestamp**. But the same trade arrives on two paths:

- `harness/recorder/ws_sink.py`, `handle()`: `source="ws"`, `ts = _ts(body["ts_ms"])`.
- `harness/normalize/kalshi.py:125-131`: `source="rest"`, `ts = _ts(t["created_time"])`,
  inserted when the tick polls `/markets/trades` every 2 minutes with
  `on_conflict_do_nothing`.

The REST path exists precisely to recover trades the WebSocket missed. It inserts them
**after** the cursor has already advanced past their `ts`. A `last_trade_ts` cursor
therefore **permanently skips exactly the prints the REST tape was added to recover** —
and those are the ones that matter, because they are the ones that arrive when the socket
was degraded, which is when fills are most in question. Nothing would ever detect it: the
`fill_confirmed` audit in §2 of the addendum reads the same table with the same window and
would simply also not see them.

**Bug B — mixing an `id` cursor with a `ts <= now` bound on `orderbook_events` skips rows.**
The pre-flight ruling in `progress.md` (T3 row) reads: "the snapshot is the newest with
`ts <= now`, then deltas with `id > snapshot.id and ts <= now`". `ts` on a delta comes from
the venue's `ts_ms` (`ws_sink.handle`, `orderbook_delta` branch), not from local receipt
time. If the venue's clock leads the NAS by even a few hundred milliseconds — and the
runbook's own "Clock accuracy" section documents that skew here is real and compensated
only for the handshake — then a delta with `id = N` and `ts = now + 0.3s` is excluded while
a delta with `id = N+1` and `ts = now − 1s` is returned. The cursor advances to `N+1` and
delta `N` is never applied. The book is then silently wrong for the rest of the order's
life, and `queue_ahead` with it.

**Change `docs/superpowers/plans/2026-09-07-phase3-paper-execution.md`, Tasks 3, 4 and 6,
and the T3/T4 rows of `progress.md`:**

- **Deltas, live path:** cursor on `id` only. `where ticker = :t and id > :cursor and ts >=
  :snapshot_ts` — the `ts >=` lower bound is there to let `ix_obe_ticker_ts` and (after
  §1.4) partition pruning do their work; there is **no `ts <=` upper bound**. Rows are
  inserted by a single process in receipt order, so `id` is the only monotonic key and it
  is the right one. A delta whose venue timestamp is a second in the future is real and
  should be applied.
- **Deltas, replay path:** bound on `ts` alone, order by `(ts, id)`, **no `id` cursor**.
  `now` is historical there and `ts` is the only meaningful clock.
- **Prints:** no cursor at all. Re-scan `where ticker = :t and ts >= :placed_at -
  interval '60 seconds'` every loop. Prints are three orders of magnitude rarer than deltas
  (a busy market sees hundreds per hour, not tens of thousands), so this is cheap, and
  fills are already idempotent on the unique key
  `(order_id, fill_method, source_trade_id, source_event_id)` that Task 2 defines. This
  makes the fill a pure function of the order plus the full window, which is what Task 13's
  replay reproducibility claim actually requires.
- Drop `Order.tape_cursor_trade_ts` from the Task 2 column list; keep
  `tape_cursor_event_id`.

**Cost if ignored:** the fill dataset — the phase's entire product, and the direct input to
the go-live gate's H1 markout — is missing an unknown, unmeasurable fraction of its fills,
biased toward the periods when the feed was degraded.

### 2.3 For most candidates there is no book at all, and the plan does not say what happens

This is the finding with the largest effect on what phase 3 actually produces.

A book for a ticker exists only if:

- **WebSocket:** `select_ws_tickers` (`harness/venues/kalshi/ws.py:47-79`) selects matched
  markets whose game kicks off in `[now − 4h, now + ws_lookahead_hours]` where
  `ws_lookahead_hours = 24` (`harness/config/settings.py:20`), ordered by 24-hour volume and
  **capped at `ws_max_tickers = 500`**.
- **REST ladder:** `select_ladders` (`harness/recorder/cadence.py:60-69`) returns nothing
  at all unless some kickoff is within `[-4h, +3h]`, and then only for markets whose
  `event_date == today`.

Candidates, meanwhile, are generated for games up to eight days out (§2.1).

So for the large majority of candidates, `load_book(session, ticker, now)` returns `None`:
no WS snapshot, no REST snapshot. **Task 3 does not specify what `load_book` returning
`None` means, and Task 5's `plan_actions` has a `book_dirty` branch but no `no_book`
branch.** A subagent will pick one of: crash on `None`, treat `None` as dirty (skip
silently), or treat `None` as a clean empty book (place an order with
`queue_ahead_at_place = 0` against a book that does not exist, which manufactures fills).
All three are wrong in different ways, and the third is the one that poisons the dataset.

Even the correct behaviour has a consequence the plan never states: **paper execution
collapses to the ~24 hours before kickoff, and mostly to the top 500 markets by volume.**
Meanwhile `market_gap_snapshots` — the input to the H2 mispricing map in report Table 4 —
covers every matched market at every TTK. Table 4 and Table 6 would then describe
different populations, and the week-3 "at least three cells whose 90% CI excludes zero"
criterion in spec §9.6 would be read as if they were the same one.

**Change:**
- Task 3, `load_book` interface: state explicitly that it returns `None` when neither a WS
  `snapshot` row nor a REST `orderbook_snapshots` row exists for the ticker at or before
  `now`, and add `BookState.source ∈ {ws, rest}`.
- Task 5, `plan_actions`: add the branch, ordered before `book_dirty` — no book →
  `Skip(intent_id, "no_book")`; and for an **open** order whose book has gone away, `Renew`
  only, never a fill or a cancel decision.
- Task 2, `Order`: add `book_source varchar(4)` recorded at placement.
- Task 12, dashboard: add the skip-reason breakdown (`order_events where kind='skipped'`
  grouped by reason, last 24 h) so `no_book` is visible on day one rather than inferred in
  week three.
- Task 14 / `verify.md` §"Phase 3 additions": add
  `select reason, count(*) from order_events where kind='skipped' and ts > now() - interval '24 hours' group by 1 order by 2 desc;`
  with the expectation that `no_book` dominates away from game day, and that it does **not**
  dominate within 3 h of kickoff.
- Task 2, Settings: raise `ws_lookahead_hours` to 72 and note the `ws_max_tickers = 500`
  ceiling in the pre-registration amendment, so the population difference between Table 4
  and Table 6 is on the record before the data is collected rather than after.

---

## 3. Batch jobs inside the recorder tick (brief item b)

### 3.1 The tick has no budget left to give

`harness/recorder/tick.py`, `maybe_tick()`: `_Budget(self.s.tick_budget_s)` = **100 s** for
the fetch phase, then `normalize_new(…, time_budget_s=30)`, then
`price_and_signal(…, self.s.price_budget_s)` = **20 s**. Worst case 150 s. The cadence
inside a game window is **120 s** (`interval_for`). The scheduler
(`harness/scheduler.py:22-25`) is `max_instances=1, coalesce=True`, so an overrun does not
queue — it **skips the next tick**, which is the correct choice and also means an overrun
costs recorded data.

Task 7 adds `run_settlement(session, now)` as an "hourly hook after pricing" in this same
function. Task 12 adds `housekeeping()` daily. Neither has a budget, and Task 7's
`run_settlement` transitively calls `compute_benchmarks` (Task 8), `compute_gap_outcomes`
(Task 8) and `compute_markouts` (Task 9).

Spec §4.1 schedules settlement "nightly 03:00 CT **and hourly during game windows**" —
i.e. it is specified to fire in precisely the window where the tick has no slack.

### 3.2 `compute_gap_outcomes` is the largest write in the system

Addendum §3 and Task 8: "`gap_outcomes(gap_snapshot_id, benchmark_type, …)` for **every gap
snapshot** with a direct or derived fair and **every benchmark**".

`market_gap_snapshots` is one row per matched venue market per priced run. With ~500 matched
markets and ~200 real ticks/day that is ~100k gap rows/day; over 124 days, ~12M. Nine
benchmark types gives **~110M `gap_outcomes` rows across the season**.

They are not written evenly. Benchmarks are computed once per game at kickoff + 5 min, and
`compute_gap_outcomes(session, game_id)` runs for that game's whole observation history —
which is every gap row for its 15-odd markets across the eight days it was priced, on the
order of 3k–10k gap rows per game, times nine benchmarks. **A CFB Saturday's 40 finals
therefore drain 1–4 million row inserts inside one hourly settlement run**, inside the
recorder tick, at 22:00 CT on a Saturday while games are still being recorded.

### 3.3 There is a 30-second statement timeout nobody has budgeted for

`harness/db/engine.py:6-8`:

```python
connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000"}
```

Every connection in every process — recorder, executor, dashboard, and every CLI command —
gets a **30-second statement timeout**. The phase 2 hotfix `97e760e` added the `_section`
wrapper to `dashboard/app.py` specifically because of it.

Nothing in the phase 3 plan mentions it. It will kill, in order of likelihood:

- Table 4 of the weekly report (mispricing map: group by sport × market type × price bucket
  × TTK bucket over a week of `gap_outcomes`, which by November is tens of millions of rows).
- `compute_gap_outcomes` for a game with a long observation history.
- `compute_markouts`, which calls `load_book` at five past horizons per order.
- `harness gate`, which reads the same tables.

Worse, a `QueryCanceled` inside the tick leaves the session in a failed transaction. The
tick handles that for `normalize` and `pricing` (`session.rollback()` then a warning), and
Task 7's one-line "hourly hook after pricing" does not say to do the same.

### 3.4 The change

**Change `harness/scheduler.py` (via plan Task 7) — move the batch work out of the tick:**

```python
def build_scheduler(recorder, heartbeat_s, settler=None, settle_period_s=3600):
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(recorder.maybe_tick, "interval", seconds=heartbeat_s, id="maybe_tick",
                  max_instances=1, coalesce=True, misfire_grace_time=60)
    if settler is not None:
        sched.add_job(settler.run, "interval", seconds=settle_period_s, id="settle",
                      max_instances=1, coalesce=True, misfire_grace_time=300)
    return sched
```

`BackgroundScheduler`'s default executor is a 10-thread pool, so this runs concurrently
with the tick on its own session and its own connection. Consequences to specify in the
plan:

- The settlement job takes its **own** session from the factory. It must never share the
  tick's session.
- It writes its own counters. **Task 7's "Tick notes gain `settlement: {…}`" must go** —
  two jobs writing `runs.notes` for the same row is a lost update. Write to a
  `source_state`-style row, or add a small `job_runs(job, started_at, finished_at, status,
  notes)` table, and have Task 12's dashboard read that.
- It gets a wall-clock budget (`settle_budget_s = 600`) checked between games, exactly the
  way `price_and_signal` checks between variants, and it records `budget_exhausted` so the
  next run resumes. Every write is already idempotent, so resuming is free.
- Each game is wrapped in `session.begin_nested()` and counted on failure, the way
  `compute_fair_values` already does (`harness/pricing/fair.py:265`).

**Change `harness/db/engine.py` (via plan Task 2):**

```python
def make_engine(url: str, statement_timeout_ms: int = 30000) -> Engine:
    return create_engine(url, pool_pre_ping=True, future=True,
                         connect_args={"connect_timeout": 5,
                                       "options": f"-c statement_timeout={statement_timeout_ms}"})
```

and use it as: executor **10000** (a query that cannot finish in 10 s must not eat a 15 s
loop), settlement/report/gate **900000**, everything else unchanged at 30000. Add a test
asserting `show statement_timeout` per engine so the values cannot drift.

**Change Task 8, `compute_gap_outcomes`** — make it a continuous drain rather than an
hourly spike: a `gap_outcomes_watermark` on `market_gap_snapshots.id`, a `LIMIT` per call
(say 50,000 rows), and a note in the run counters when it is still behind. It is already
PK-deduped, so this is a loop bound, not a semantics change.

**Change Task 12** — `housekeeping` at 09:00 UTC is 04:00 CT, inside quiet hours. Good.
Keep it, but move it onto the settlement job's schedule rather than the tick's.

---

## 4. init-db, Alembic, and rollback (brief item d)

### 4.1 `create_all` never adds an index to an existing table — and no test catches it

`harness/db/schema.py:41` calls `Base.metadata.create_all(engine)`, which with
`checkfirst=True` skips **existing tables entirely, including their indexes**. The authors
knew this for columns — hence the `ALTER TABLE … ADD COLUMN IF NOT EXISTS` lines at
`schema.py:44` — but the same trap applies to indexes declared in `__table_args__`.

Today the codebase gets away with it: `git log -S "ix_obe_ticker_ts"` shows the index
shipped in the same commit as the table (`1a07121`). But the tests cannot detect the class
of bug, because `tests/conftest.py`'s `db_session` fixture calls `drop_schema` then
`create_schema` on every test, so the test database always has a freshly created table
where every `__table_args__` index exists. Production, where the table already exists,
would not.

**Change plan Task 2, Step 1:** add a test that, after `create_schema` on a database whose
tables already exist, asserts every `Index` in `Base.metadata` appears in `pg_indexes`. It
is about fifteen lines and it closes the trap permanently for phases 4–6.

### 4.2 `drop_schema` is a hand-maintained list, and Task 2 does not extend it

`harness/db/schema.py:63-70` drops a **literal list** of twenty table names. Phase 3 adds
twelve tables (`intents`, `orders`, `order_events`, `fills`, `markouts`, `settlements`,
`venue_settlements`, `benchmarks`, `gap_outcomes`, `ledger`, `exec_heartbeat`,
`gate_reports`) and two views. **The plan's Task 2 never mentions `drop_schema`.**

Consequence: `db_session` no longer truncates the phase 3 tables between tests. 115 of the
current 257 test functions take that fixture, and phase 3 roughly doubles the DB-test
count. Rows leak forward; `orders.client_order_id` is unique and `intents.signal_id` is
unique, so collisions appear as **order-dependent, intermittent failures** — the worst
possible failure mode for an autonomous loop that reruns the suite dozens of times and
treats a red suite as a blocker.

**Change plan Task 2:** extend `drop_schema` with every new table and view, and add a test
`test_drop_schema_covers_every_model` asserting the dropped set equals
`set(Base.metadata.tables)`.

### 4.3 The phase 4 Alembic baseline will drop the hand-written DDL

`roadmap.md` §Phase 4 item 8: "Alembic baseline generated from the current models".

`create_schema` contains four things that are **not** in `Base.metadata` and that
`alembic revision --autogenerate` will therefore propose to **drop**:

- `ix_raw_fetched_brin`, `ix_obe_ts_brin`, `ix_trades_ts_brin` (BRIN, `schema.py:46-50`)
- `uq_odds_snapshot_row`, `uq_fair_value_row` (functional unique indexes with `coalesce()`,
  `schema.py:51-58`)
- `ix_raw_source_fetched`, `ix_raw_run`
- the partitioning of `raw_responses` (SQLAlchemy models the parent as an ordinary table;
  autogenerate has no concept of the partitions and will not know they exist)

An autogenerated baseline applied to the NAS would emit `DROP INDEX` for the BRIN indexes
and the functional unique constraints on a database that by then holds hundreds of GB. The
`uq_fair_value_row` drop alone would let duplicate fair values in.

**Change `roadmap.md` §Phase 4 item 8** to:

> Alembic baseline **hand-written from `create_schema`**, not autogenerated; autogenerate is
> used only to diff subsequent revisions, run with an `include_object` filter that excludes
> the BRIN indexes, the functional unique indexes, and the partitioned tables. The baseline
> is verified by creating an empty database from it and diffing `pg_dump --schema-only`
> against a `create_schema` database. Any migration touching an index on `raw_responses`,
> `orderbook_events`, `venue_trades`, `venue_quotes` or `odds_snapshots` uses
> `CREATE INDEX CONCURRENTLY` via `op.execute` under
> `transaction_per_migration = True` with `autocommit_block()`, never the default
> transactional `op.create_index` — a non-concurrent `CREATE INDEX` on `orderbook_events`
> takes a SHARE lock and blocks the WebSocket writer for the duration of the build.

### 4.4 Rollback is undocumented, and it works

There is no rollback path anywhere in the runbook or the skill. There is, in fact, a good
one, and the loop will need it at 3 a.m.: every schema change so far is **additive**
(`create_all` plus `ADD COLUMN IF NOT EXISTS`), so old code runs correctly against new
schema. `git checkout <previous sha> && make deploy-nas` rebuilds the old image, stamps
`BUILD_SHA` from the detached HEAD, and the freshness check in `verify.md` Layer 1 proves it
landed.

**Change `docs/runbooks/phase0-deploy.md`**, new section "Rolling back a deploy": the two
commands, the statement that additive schema makes it safe, the one exception (a migration
that drops or renames a column is **not** rollback-safe and phase 4's Alembic work must
keep every migration additive or the rollback path dies), and the note that `init-db` re-runs
harmlessly on the old code.

### 4.5 `seed-teams` is a third-party single point of deploy failure

`Makefile` `deploy-nas` chains
`init-db && seed-teams && variants register` in one ssh, and the next line runs
`docker compose up -d`. Make aborts the recipe on the first failing line, so **if
`seed-teams` fails, the new containers are never started** and the stack silently keeps
running the old image.

`harness seed-teams` (`harness/cli.py`) fetches two ESPN endpoints with bare
`urllib.request.urlopen(url, timeout=20)` and no retry. The runbook's own "Known external
quirks" section documents that ESPN sits behind Akamai and 403s on the wrong User-Agent.
`urllib` sends `Python-urllib/3.12`, which is not the `python-httpx/x.y` the rest of the
codebase deliberately relies on.

**Change the `Makefile`:** `docker compose run --rm app-run seed-teams || echo '[DEPLOY]
WARNING: seed-teams failed; teams unchanged'` — teams and aliases change weekly at most, and
a transient ESPN 403 must not be able to leave an autonomous deploy half-applied.

---

## 5. Deploy safety and the verification contract (brief item e)

### 5.1 `harness report --week N` cannot write a file on the NAS

Three facts compose into a hard failure:

- `.dockerignore` line 6 excludes `docs`, so `/app/docs` does not exist in the image.
- The `Dockerfile` builds as root (`WORKDIR /app`, `COPY`, `pip install .`) and only then
  `USER nobody`; `/app` is root-owned mode 755.
- `deploy/nas.env` sets `APP_UID=1000, APP_GID=10`, and compose runs every app service as
  that uid. There is no volume mount for anything but the three secret files.

So Task 10's `CLI writes docs/reports/2026-wNN.md` raises `PermissionError` inside
`docker compose run --rm app-run report`, and even if it did not, `--rm` discards the
container filesystem.

Both `verify.md` §"Phase 3 additions" ("`harness report --week 37` … writes
`docs/reports/2026-w37.md`") and `roadmap.md` §Operator calendar ("Monday 09:00 … commit
`docs/reports/2026-wNN.md`") depend on this working. The Monday operator duty is the loop's
primary weekly deliverable.

**Change:**
- `docker-compose.yml`, `app-run`: add `- ./reports:/app/reports` to `volumes`.
- `Makefile` `deploy-nas`: `mkdir -p $(NAS_STACK)/reports` alongside the existing
  `mkdir -p $(NAS_STACK)/secrets $(NAS_STACK)/pgdata`.
- Task 10 CLI: `--out` defaults to `/app/reports`; `--out -` writes to stdout.
- `verify.md` and the operator calendar: the command becomes
  `docker compose run --rm app-run report --week N --out /app/reports`, and the file is
  read back from `$NAS_STACK_DIR/reports/` over ssh before being committed on the Mac.

The same applies to Task 13's new `harness export-fixture` CLI, which "dumps
`raw_responses` for a `run_id` range to JSON" — give it the same `--out` handling.

### 5.2 Nothing stops the loop deploying into a live game window

`make deploy-nas` ends with `docker compose build && … && docker compose up -d`. A new
image recreates all four app containers, including `app-ws`. The brief already accounts for
"a few seconds of tape gap", and that is right for a Tuesday. On a CFB Saturday at 15:00 CT
it is a hole in the tape across ~40 simultaneous live games, plus a skipped recorder tick,
plus the WebSocket reconnect backoff — and the reconnect deliberately calls
`sink.reset_sequences()`, so **no `gap` row is written** and nothing in the data marks the
hole.

The autopilot is authorized to deploy without asking and will do so whenever a phase or a
hotfix finishes, at whatever hour that lands.

**Change `.claude/skills/autopilot/SKILL.md`, §Unit: deploy**, adding a step 0:

> **Deploy window.** Before `make deploy-nas`, check for a game in progress:
> `select count(*) from games where status in ('in','in_progress') or (kickoff_utc <= now()
> and kickoff_utc > now() - interval '4 hours');`. If it is non-zero, the deploy waits:
> `ScheduleWakeup` for the end of the window, unless the deploy is itself the fix for a
> failed verification, in which case deploy and journal the tape gap explicitly as an
> anomaly. Quiet hours (01:00–08:00 CT) are always safe.

Also add to `verify.md` a post-deploy tape-continuity check (below), so a deploy that did
cost a gap is at least recorded.

### 5.3 The build stamp check is sound

Worth stating because it is load-bearing and it survives scrutiny. `BUILD_SHA` is computed
at make **parse** time from `git rev-parse --short HEAD` with `-dirty` from `git status
--porcelain`, written into `build/nas.env`, scp'd as `.env`, and read by the container at
creation. A running container keeps the env it was created with, so if `docker compose up -d`
never runs (e.g. `variants register` fails), `/healthz` still reports the **old** sha and
Layer 1 correctly declares a deploy failure. The one gap is §5.1's: the env file lands before
the containers restart, so between the scp and the `up -d` the file on disk and the running
container disagree — which is exactly what the check is designed to catch.

### 5.4 What the verify contract cannot observe

`verify.md` is well built — the time-of-day table and the "deferred, not failed" rule are
the two things most deploy checklists get wrong, and both are right here. Six blind spots,
each with a one-line addition to Layer 2:

| Blind spot | Why it matters in phase 3 | Add to `verify.md` Layer 2 |
|---|---|---|
| **Tape continuity** | The fill model's honesty is exactly the tape's completeness. The contract checks "last event within 60 minutes" and counts, never holes. | `select kind, count(*) from orderbook_events where ts > now() - interval '2 hours' group by 1;` — expect `gap` = 0. |
| **Book coverage of open orders** | §2.3: an order on a ticker with no tape can never fill, and looks identical to an order with no edge. | `select count(*) from orders o where o.status='open' and o.replay=false and not exists (select 1 from orderbook_events e where e.ticker=o.ticker and e.ts > now() - interval '15 minutes');` |
| **Executor loop duration** | §2.1: coalesced loops are invisible; the heartbeat stays green at a quarter rate. | `select last_loop_ms, p95_loop_ms, loops_skipped from exec_heartbeat;` (needs the columns from §2.1). |
| **Postgres health** | §1: vacuum lag, dead tuples and WAL are the season's real failure mode and nothing looks at them. | `select relname, n_live_tup, n_dead_tup, last_autovacuum, last_autoanalyze from pg_stat_user_tables order by n_dead_tup desc limit 5;` and `select count(*), pg_size_pretty(sum(size)) from pg_ls_waldir();` |
| **Disk free** | The contract watches `pg_database_size` against 800 GB but never the volume. Another NAS service filling `/volume1` stops Postgres. | `ssh … 'df -h /volume1 \| tail -1'` |
| **Degraded dashboard sections** | `_section` logs at **WARNING**, so the "0 ERROR lines" check passes while half the page reads `unavailable:`. | `docker compose logs --since 10m app-serve \| grep -c "dashboard section"` — expect 0. |

Add one line to the walkthrough checklist too: **item 16 — the skip-reason table renders and
`no_book` is not the only reason present within three hours of a kickoff** (§2.3).

---

## 6. Process boundaries and failure isolation (brief item g)

Current and planned:

| Process | Owns | Failure blast radius |
|---|---|---|
| `app-run` | fetch, normalize, price, signal, **+ settle, benchmarks, markouts, housekeeping** | one APScheduler job; a slow or failing addition stops recording (§3) |
| `app-exec` | intents, orders, order events, fills, ledger(fill), heartbeat | isolated; no network client, no secrets — good |
| `app-ws` | the tape | isolated; `restart: on-failure` |
| `app-serve` | reads + kill switch | isolated; `_section` degrades gracefully |

Three concrete problems.

**6.1 `orders` has two writers, contradicting the addendum.** Addendum §1 states `app-exec`
"is the only writer of `intents`, `orders`, `order_events`, `fills`, `exec_heartbeat`". Task
7 then has `run_settlement` set `order status = settled` and write `ledger` rows. Both
processes issue bare `UPDATE orders SET status = …` with no row lock, so a cancel and a
settle racing on the same row is last-writer-wins — a cancelled order can end up `settled`,
or a settled one `cancelled`, and the ledger and the `positions` view disagree.

**Change:** amend addendum §1 to name settlement as a second, restricted writer; make Task 7's
update conditional — `update orders set status='settled' where id = :id and status in
('filled','partially_filled')` — and have it never touch an order whose status is
`open`/`cancelled`/`expired`.

**6.2 Nothing enforces a single executor.** `harness exec-once` run by hand (the plan adds
it, and the runbook will document it) while `app-exec` is up gives two writers. The unique
keys mostly save it, but not the read-modify-write of `filled_contracts`/`status`.

**Change:** `Executor.step()` begins with
`select pg_try_advisory_lock(hashtext('harness.exec'))`; if false, log and return an empty
`ExecStats`. Four lines, and it converts an assertion into a guarantee.

**6.3 `app-exec` has no healthcheck and no stop grace.** `app-run`, `app-ws` and the planned
`app-exec` show `Up` in `make status-nas` whether or not they are doing anything; only
`app-serve` has a healthcheck. A wedged executor is caught by `exec_heartbeat` age in the
verify pass, which runs after deploys and each morning — up to a day late.

**Change `docker-compose.yml` (plan Task 6):** give `app-exec` `stop_grace_period: 60s`
(the default 10 s can SIGKILL a step mid-transaction) and a healthcheck that queries
`exec_heartbeat` age, so Docker restarts it on its own:

```yaml
    healthcheck:
      test: ["CMD-SHELL", "harness exec-health"]
      interval: 60s
      timeout: 10s
      retries: 3
```

with a trivial `exec-health` CLI that exits non-zero when `now() - last_loop_at > 120s`.

---

## 7. Test-suite runtime and the shared test database (brief item f)

`tests/conftest.py`'s `db_session` fixture calls `drop_schema(engine)` then
`create_schema(engine)` **per test**. `create_schema` is `create_all` over 20 tables plus
nine explicit DDL statements including two BRIN builds and two functional unique indexes —
on the order of 35 round trips. **115 of the current 257 test functions take that fixture.**

Phase 3 makes both terms worse at once: 12 more tables and 2 views in `create_schema`
(~60% more DDL per fixture), and roughly double the DB-test count (Tasks 2, 6, 7, 8, 9, 10,
12, 13 all specify DB tests).

The loop's throughput cost is the multiplier, not the absolute time. Per plan Task:
implementer RED run, implementer GREEN run, reviewer, one to three fix rounds each with a
run, scoped re-review — call it four to six full-suite runs per task, times 14 tasks, plus
the pre-merge and pre-deploy runs the skill mandates. That is 60–90 serialized full-suite
runs, and the skill forbids running two implementers at once precisely because the test
database is shared.

**Change `tests/conftest.py` (as a step in plan Task 2, since it is the task that changes the
schema):**

```python
@pytest.fixture(scope="session")
def _schema():
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    engine = make_engine(url)
    drop_schema(engine)
    create_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(_schema):
    with sessionmaker(bind=_schema)() as s:
        yield s
        s.rollback()
    with _schema.begin() as conn:
        conn.execute(text(
            "truncate " + ", ".join(sorted(Base.metadata.tables)) + " restart identity cascade"))
```

One `TRUNCATE` statement instead of ~35 DDL round trips, and `RESTART IDENTITY` preserves
the predictable-id property that `drop`/`create` gave. `truncate` on the partitioned
`raw_responses` parent cascades to its partitions, so `ensure_partitions` must be called
once in the session fixture. This depends on §4.2's `drop_schema` completeness fix, which is
another reason to land them together.

Expected effect: the DB-fixture cost falls by roughly an order of magnitude, which is worth
more to the loop than any single feature in the plan.

---

## 8. Smaller phase 3 items that a subagent will guess wrong (brief item i)

Ordered by how much damage a wrong guess does.

**8.1 Which intents the executor considers is never stated.** Task 6 step 1 defines intake
(candidate signals `created_at >= now - 1h` without an intent). Steps 2 and 4 then say
`plan_actions(intents, …)` without saying which intents are loaded. Read literally it is
every intent ever created that has no open order, which grows monotonically all season and
re-evaluates months of dead candidates every 15 seconds.
**State it:** the newest intent per `(variant_id, venue_market_id, side)` whose signal is
newer than `exec_intent_ttl_s` (default 900) and whose game has not passed
`kickoff − exec_kickoff_cutoff_min`.

**8.2 Skip events are unbounded.** Task 6 step 4: `Skip` writes an `order_events` row with a
null `order_id`. An intent that is skipped has no open order, so it is re-evaluated and
re-skipped **every loop** — 240 rows/hour per skipped intent. With `no_book` dominating
(§2.3) and a few hundred candidates, that is millions of rows a day of pure noise, and it
makes the `order_events where kind='skipped'` count in `verify.md` meaningless.
**Fix:** unique index on `(intent_id, kind, reason)` where `kind in ('skipped','cap_gate')`,
inserted `ON CONFLICT DO NOTHING`, so a skip reason is recorded once per intent.

**8.3 WebSocket gap rows are per-subscription, not per-ticker.** `WsRecorder._subscribe`
sends **one** subscribe frame with every ticker
(`{"channels": ["trade","orderbook_delta"], "market_tickers": tickers}`), so one `sid` covers
all of them and `seq` is global to the subscription. `WsSink._check_seq` therefore writes the
`gap` row with whichever ticker happened to reveal the gap, while the gap invalidates
**every** ticker's book. Task 3's `load_book`, filtering `orderbook_events` by ticker, will
not see it. The addendum's "no decision on a dirty book" rule silently stops working.
**Fix:** `load_book` marks the state dirty if any `gap` row exists with the same `sid` and an
`id` greater than the snapshot's, regardless of ticker.

**8.4 `WsSink.handle`'s exception path silently drops up to 100 buffered events.** On any
exception it does `self._session.rollback(); self._pending = 0` — discarding every event
buffered since the last commit, with no `gap` row written. Rare, but it is invisible tape
loss in the one table that cannot be re-fetched.
**Fix (phase 3 or 6):** write a `gap` row recording the discarded count before rolling back.

**8.5 The executor never checks fair-value staleness.** Task 5's `MarketNow.fair_p` comes
from the newest `market_gap_snapshots` row for the market, which is refreshed only on a tick
(2–15 minutes, and not at all during quiet hours). The `edge_decay` branch and the `venue_move`
branch would then both reason from a fair value that can be hours old. Task 1 exists to make
staleness honest for the *pricer*; the executor bypasses it entirely.
**Fix:** `MarketNow` carries `fair_ts` and `stale_allowance_s`; `plan_actions` cancels with
reason `fair_stale` when `now - fair_ts > max(cfg["stale_s"], stale_allowance_s)`, and skips
placement for the same reason.

**8.6 Fee ceiling per fill versus per order.** Spec §6.0 says "Fees are dollars **per
order**, `ceil_to_cent(rate × contracts × p × (1−p))`"; Task 4 says "Fee per fill:
`ceil_to_cent(fee_for_order(model, "maker", prob, contracts))`". `fee_for_order`
(`harness/pricing/fees.py:29-33`) already ceils, so the outer call is a no-op, but the
per-fill application is not: a 1-contract fill at p = 0.50 pays `ceil($0.0044) = $0.01`, a
full probability point. The queue model will produce many small partial fills, so the
aggregate maker fee could be inflated several-fold — and "markout **net of maker fee** > 0
with t > 2" is a go-live gate criterion.
**Fix:** state the rule explicitly in the addendum §2 after checking Kalshi's per-execution
rounding against current docs, and have report Table 6 print realized fee per contract so
the bias is visible either way.

**8.7 `run_settlement`'s exception handling is unspecified.** Task 7 adds it as an "hourly
hook after pricing" with no try/except. The tick wraps `normalize` and `pricing` in
`try/except` with `session.rollback()` precisely because a DB error leaves the session's
transaction aborted and `finish_run`'s UPDATE then fails too (the comment at
`tick.py` says so). Once §3.4 moves settlement to its own job this is moot for the tick, but
the settlement job needs the same guard.

**8.8 Task 13's `--file` replay variant and the phase 2 finding.** Phase 2 finding 1 (a
replay variant joining the live set) was fixed by scoping `active_variants` to `LIVE_TIERS`.
Task 13 adds `replay --execute`, which now writes **orders and fills** as well as signals.
The `uq_open_order` partial index is correctly scoped `where … replay = false`, but the plan
does not say the executor's **intake** filters `replay`. The `progress.md` T6 ruling does
("intake reads signals with the same `replay` flag") — good, but it lives only in the ledger.
Promote it into the plan's Task 6 interface text so it survives a context summarization.

---

## 9. What is sound

Worth saying plainly, because the review above is entirely about what to change.

- **The phase separation is right.** A dedicated `app-exec` with no network client and no
  secrets mounted, pure decision functions taking an explicit `now`, and every insert carrying
  a real unique key is the correct architecture for a system whose product is a dataset that
  must be replayable. The `replay --execute` design genuinely does reconstruct decisions from
  stored rows, and the `progress.md` pre-flight scan caught the interface mismatches
  (`ExecSettings`, `PositionView`, `as_at_place`, the `load_book` past-`now` ambiguity) that
  would otherwise have cost fix rounds.
- **The existing code is careful in the places that matter.** Checkpointed transactions with
  savepoints per game, `on_conflict_do_nothing` against real constraints, one clock threaded
  through pricing, a budget checked between stages, `_section` isolation on the dashboard,
  redaction on the log filter, and `pricing_clock_for_run` — the phase 2 review's findings
  were all fixed properly rather than papered over.
- **The loop's verification contract is better than most human deploy checklists.** Freshness
  before behaviour, the time-of-day table, "deferred is not failed", and the controller
  re-reading the walker's screenshots rather than trusting its verdict are all the right
  calls. The gaps in §5.4 are additions to a sound structure, not repairs to a broken one.

---

## 10. Questions only the user can answer

1. **Is the 1 TB database budget a hard cap, or is the constraint really the 3.5 TB volume?**
   If ~2 TB is acceptable, the season fits with no compaction and no data ever deleted, and
   partitioning (§1.4) is purely an operational convenience. If 1 TB is hard, sealed
   partitions must start being archived and dropped around November and the loop needs that
   authorization in advance, because the skill currently classes it as a gate.
2. **Pre-authorization for the partitioning migration.** Converting `orderbook_events` and
   `venue_trades` to partitioned tables is a schema change on live data. Done via
   `ATTACH PARTITION` it is metadata-only and takes seconds, but `SKILL.md` §Gates lists
   "anything destructive on the NAS" and "compaction, changing retention" as stop conditions,
   so the loop will halt at it unless the roadmap says otherwise. Every week of delay makes
   it more expensive.
3. **Is a tape gap during a live game acceptable to fix a failed verification**, or should a
   failed verification wait for the window to close (§5.2)? The default proposed above is
   "wait, unless the deploy is the fix", but the tape is the irreplaceable asset and the call
   belongs to the user.
