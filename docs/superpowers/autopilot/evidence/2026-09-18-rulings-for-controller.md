# Rulings for the controller, Fri 2026-09-18 (docket items 21, 19, 22, 23 and 1)

Prepared by the 2026-09-18 review session (Fable, read-only, no controller lock) from the user's
decisions after an adversarial review of the recommendations. The loop adopts the unmarked text below
as the user's rulings: journal each item as a `decision` entry quoting it verbatim with the time, then
re-orient from files. Lines marked STRIKE-IF are choices the user may strike or change; everything else
stands as written. The loop edits none of the user-owned files (roadmap tables, calendar, `fixes.md`
preamble, `verify.md` outside a plan's last task); where a ruling needs one of them changed, the user
edits it. Production numbers are from read-only reads at 06:50-07:10 CT on runtime 1a12781; the
evidence section at the end carries them with file:line pointers so the loop can verify before acting.

---

## Item 21, first and time-critical: coverage-contract waiver, then the definition as phase work

**Waiver (apply before today's first game-day 6D read).** The 6D coverage-contract row may read FAIL on
the no-sharp-line shortfall without tripping "the same verify item failing twice running", from
2026-09-18 until I rule on the definition. The row still reads FAIL, `verify.md` is not edited, and a
FAIL on any other row gates as usual. Row 84 stays in `fixes.md` Watch by my ruling (this line removes
the preamble ambiguity about a verify-FAIL row leaving Open).

**Definition: not (a) as the docket worded it, not (c), and not a hotfix.** The docket's option (a)
("scheduled set = markets with a sharp line at enumeration") is withdrawn: at enumeration only direct gap
rows exist, and a direct row is written only where a fair already exists, so that wording would shrink
each variant's scheduled set from 943,190 units to 43,766 and throw away about 591,000 completed
evaluations per variant; the amended row would pass by construction. The all-null cell is 95 % of the
scheduled set, not a small excluded class, and the instrument's grain (sport, time to kickoff, feed,
market type) is non-functional today because those attributes are only known for markets that already
had a direct fair.

Sequence, as 6D acceptance work (a plan task or tasks on the still-in-progress 6D milestone, or 6F if
the loop judges 6D's ledger closed; name the vehicle in the journal):

1. Populate `sport`, `ttk_bucket` and `market_type` at enumeration for every unit in `market_order`
   (the gap build already holds the market and game objects when it captures the ordering; `feed` may
   stay null where no gap row exists). Additive code; no check changes; the completed/scheduled totals
   are unchanged; stays inside ruling I6's 336-cell bound. This is not gate 13.
2. On the first game day with populated cells, characterize the `no_sharp_line` class: distinct
   markets (not units), by sport, market type and time bucket, and split the label's causes (no sharp
   book prices the game; the margin model could not be built; lines older than the lookback; a total
   with no mean). Bring that to me as a docket item with a proposed excluded-class band.
3. Only then the amendment: a new dated §0 entry in the 6D addendum keeping the pre-registered 0.95 bar
   on the eligible denominator (units whose gap row closed carrying a fair), with `no_sharp_line`
   reported as a named excluded class with its own share per cell and a band that I set (the roadmap's
   decision 4 says the tolerance is agreed, not stated, so the number is mine, not the loop's).
   `verify.md` changes only through that plan's last task. Until it lands, the waiver above covers the
   row.

STRIKE-IF: if you would rather waive the row indefinitely and skip step 1, say so; the review's view is
that step 1 is the only way to learn what the class is.

## Item 19: caps unchanged; the study as a finding; a pacing brief

Caps unchanged (`veto_daily_usd_cap` $25, `veto_weekly_usd_cap` $150); that part needs no ruling and
the loop changes nothing under gate 6. Two things I do decide:

1. Today's "seven days after the veto goes live" calendar duty runs as a finding only: decided count,
   label distribution, primary/shadow disagreement rate (3 of about 1,830 in the last seven days), the
   decided share of intents, and the hours of day the decided sample comes from. No model re-runs: the
   study is itself a spend kind under the same $25 daily cap, which the veto exhausts before 09:00 CT
   every day. The calendar duty is deferred, not skipped; journal that reason. (The calendar is my
   text; this line is my word for the deferral.)
2. Bring me a one-page brief on pacing the daily budget across the day (an hourly or per-bucket
   sub-budget under the unchanged caps) so decided calls sample game hours. Today the worker claims the
   oldest buckets first from the midnight reset, so each day's $25 is spent on overnight signals for
   games hours from kickoff, where the veto's designed value (status and injury news) cannot appear.
   The 0-of-1,863 `proceed` rate therefore does not yet show that the veto never fires. The brief is a
   proposal, not a dispatch; it changes when the cap binds, so I rule on it before any code.
3. The daily 09:00 CT line notes, from today, whether the annotator or the parlay rationale was refused
   because the veto had spent the day's cap (the cap is shared across veto, annotate, parlay and study).

## Item 22: an additive index, not batched placement writes

The 145 ms per paper placement is `store.orders_for_intent`'s `select count(*) from orders where
intent_id = :i`, a sequential scan over a 952 MB heap (36,685 live rows after 105.7 M updates), measured
at 154 ms cold. Batched placement writes are not needed.

Ruling: an additive concurrent index on `orders(intent_id)`, declared in all three places the catalogue
test requires (`harness/db/schema.py`, `harness/db/models.py`, an Alembic migration using
`CREATE INDEX CONCURRENTLY`, precedent `0002_phase45.py`'s `ix_orders_key_placed`), released as a
**full** release in Monday's quiet window (an app-only release cannot carry a migration). Build
conditions: the executor's loops short (no expiry cohort in flight), `statement_timeout` raised for that
statement only, and `pg_index.indisvalid` checked immediately after and journaled. An invalid index is a
stop for me, not a retry: `orders` is not on the bulk-table deny list, so the next `migrate ensure`
would otherwise reindex it non-concurrently under a 5 s lock timeout. The hotfix is opus/opus and cites
`exec.phase_place_ms / exec.placed` before and after as its judge.

STRIKE-IF: if you want it sooner than Monday, the by-hand `CREATE INDEX CONCURRENTLY` in a quiet hour is
additive and allowed, but the three declarations still land in the next release; the hand statement only
moves the build earlier.

## Item 23: a design read today, read-only; no executor code before Sunday

Confirmed chain at Thursday's 19:05 CT cohort: every expiring row lands on the per-row path with cause
`book_query` (its cursor is behind the current book, so `_sim_book` reconstructs a historical book from
the tape per row, and per track), 127-225 ms a row; the cohort did not close on its expiry loop
(pending 24,839 -> 23,735 over four loops of 184/253/204/116 s while `tape_lag_tickers` went 6, 4, 4, 2).
Weekend cohorts now: Sat 10:50 CT 2,403 rows / 302 distinct (ticker, cursor); Sat 17:50 3,612 / 466;
Sun 11:50 5,532 / 1,338.

Ruling: dispatch one opus design read today, read-only (the controller runs any production SQL it
needs), delivered before Sat 2026-09-19 09:00 CT. Its order of work:

0. Establish from the per-ticker split which of lagging (truncated delta batch), unread (tape read
   failed) and deferred (§0.14 backoff) held each un-closed row at the 19:05 CT cohort, and why a row
   re-entered `book_query` on the following loop. `tape_lag_tickers` counts only truncated reads; six
   lagging tickers do not by themselves explain 868 rows persisting.
1. Then propose (i) memoizing the historical book per (ticker, cursor) within one loop, handing out
   copies (the historical branch returns the built book uncopied today), stated honestly as a 3-5 minute
   mitigation for Sunday's cohort, not a fix; and (ii) any value-changing option separately (advancing
   one reconstruction across a ticker's cursors uses `advance_book_at`, whose dropped-delta probe and
   re-anchor branch move dirty verdicts and fills) with a replay-comparison plan.
2. State plainly that there is no settings lever: `DELTA_BATCH_LIMIT` is a value-path constant, raising it
   turns lagging tickers into unread ones whose rows are skipped entirely, and the expiring cohort is
   exempt from the walk budget by my earlier condition.

Unchanged: expiry, version and lagging checks run on every row every loop; a track on a lagging ticker
still may not close. I decide Saturday morning whether (i) ships as a hotfix before Sunday or the
weekend is measured on the print-cache build and it ships Monday. No executor value-path release today
beyond fix 79.

## Item 1: option 1, with executable preconditions; nothing dropped until my dated yes per partition

Ruling: option 1 of `reports/2026-09-15-storage-retention-proposal.md`, one partition at a time,
`venue_trades_y2026w37` (1.9 GB) first as the rehearsal, then `orderbook_events_y2026w37`, and
thereafter one week after each Monday archive. Options 2 and 4 later under the same procedure; option 3
not taken. Preconditions, corrected from the proposal (the `.meta.json` sidecar hashes the plaintext
dump, not the ciphertext, and `drill.sh` never decrypts):

- (a) Mine, off-host: I decrypt the NAS-pulled `.dump.age` with my age key and its plaintext sha256
  matches the `sha256` in the unit's `.meta.json`. I tell the loop when that is done for each unit.
- (b) The loop's, after (a): run `deploy/backup/drill.sh` on the local plaintext `.dump` of the same
  unit and write the drill row only on a `RESTORE_OK` line (the script exits 0 on a disk-space SKIP, so
  exit status alone is not evidence), recording that a partition drill proves `pg_restore` completes and
  returns `ROWS_MATCH n/a` by design. Disk is not an obstacle (773 GB free on the one filesystem).
- (c) Already met per journal 216 / spec 0.18: no pending pre-boundary tape read for the re-score or the
  order 157 audit. Every tape read on main is `ts`-bounded and the checks never touch
  `orderbook_events`, so a dropped week returns empty rather than failing; `harness/rescore.py` and
  `harness/capsule.py` are the two consumers that would need a `pg_restore` into a scratch cluster.

Execution is mine: `set lock_timeout = '5s'` first, then `ALTER TABLE ... DETACH PARTITION` and
`DROP TABLE`, one unit per quiet hour, the loop observing and re-reading disk, DB size and the Monday
report afterwards. Record beside it that after the drop an insert stamped inside Sep 7-13 fails with
"no partition of relation found" instead of mis-stamping; the time-sync guard is the mitigation.
Timing: nothing urgent before the 480 GB band about Oct 10 (148 GB now, 14.5 GB/day trailing; the 600 GB
budget about Oct 19). Decide-by stays 2026-09-22; execute midweek.

STRIKE-IF: the proposal used GB and the live reads use GiB (79 GiB = 84 GB for the w37 tape); the
loop labels units explicitly in the journal from now on. Strike if you prefer one unit named here.

---

## Evidence the loop can verify before acting

- Coverage (last 24 h, per variant): scheduled 943,190; completed 634,526; no_fair 304,411 (= 0.673).
  All-null cell across 7 variants: scheduled 6,295,968 / completed 4,136,587 / no_fair 2,130,877; every
  populated cell 0.988-1.000. `market_gap_snapshots.no_fair_reason` last 24 h: null 637,374,
  `no_sharp_line` 305,816 (sum 943,190 = one unit per gap row). Code: `harness/ops/coverage.py`
  `evaluation_cells` (all-null cell for a market without a direct row); `harness/pricing/gaps.py:194-196`
  (direct row only where a fair exists) and `:126-131` (market and game in hand at capture);
  `harness/strategy/pipeline.py:471-478` and `:569-575` (`"no_fair" if row.fair_p is None else
  "completed"`); `harness/pricing/fair.py:311-341` and `harness/pricing/direct.py:8` (`SHARP_BOOKS`,
  the four causes behind the label). Spec: 6D addendum §1.1, §1.7; roadmap pre-loaded decision 4
  ("tolerance agreed before inference").
- Veto: `research_spend` by day, Sep 13-18: 24.42 / 24.42 / 24.41 / 24.47 / 24.40 / 24.56 (Fri spent by
  06:50 CT, 414 calls); ISO week Mon-Fri $122.26, so Saturday reaches about $146.7 and Sunday has about
  $3.30. `research_notes` last 7 days: opus 1,863 `proceed`, 0 other, 30 errors; sonnet 1,830 `proceed`,
  3 `reduce`. `veto_decisions`: 175,469 `veto_skipped_budget` vs 3,097 decided. Week's spend: opus
  $69.36 + sonnet $52.70 over 1,442 pairs ($0.085 a pair). Code: `harness/research/spend.py:51` (KINDS
  shared under one cap) and `:241-248`; `harness/research/veto.py:126-129` (oldest bucket first);
  `harness/report/tables.py:1596-1614` (t7 groups by decision only). Shadow-only confirmed: nothing under
  `harness/execution/` or `harness/strategy/` reads `veto_decisions`.
- Placement: `pg_indexes` on `orders` has no `intent_id` index; `EXPLAIN ANALYZE` of the count for one
  intent: Seq Scan, 121,880 buffers read, 153.9 ms; `pg_stat_user_tables`: 36,685 live / 2,324 dead,
  heap 952 MB, total 1,294 MB, 105.7 M updates (91.9 M HOT), autovacuum current. Code:
  `harness/execution/loop.py:2183-2260` (`_place`), `harness/execution/store.py:1011-1013`
  (`orders_for_intent`, plain `text()`, no autoflush), `migrations/env.py:28-29` (`BULK_TABLES`) and
  `:108-114` (300 s statement timeout, 5 s lock timeout), `harness/db/migrate.py:130-133` and `:165-172`
  (invalid-index handling and the reindex deny-list), `migrations/versions/0002_phase45.py:125`
  (precedent).
- Expiry cohort: `metric_samples` Thu 19:05:14-19:16:14 CT: loop_ms 184,438 / 253,471 / 204,287 /
  116,450; expiring_n 1,104 / 1,042 / 868 / 868; per_row_n 1,305 / 1,200 / 1,053 / 1,030;
  phase_per_row_ms 165,399 / 235,229 / 187,524 / 101,784; per_row_book_query 1,155 / 1,050 / 903 / 880;
  tape_lag_tickers 6 / 4 / 4 / 2; nw_pending 24,839 / 24,777 / 24,603 / 24,603 then 23,735; tape delta
  rows 518-571 k a loop. Code: `harness/execution/loop.py:372-382` (`_expiring`), `:1266-1281` (walked
  ahead of the rotation, charged to the budget), `:1494-1498` (`PER_ROW_BOOK_QUERY`), `:2061-2086`
  (`_sim_book`, historical branch uncopied), `:1815` and `:1865` (lagging = truncated reads only),
  `:2497` (`done` requires the ticker not lagging), `:1085-1093` (unread/deferred tickers skipped);
  `harness/execution/book.py:653-700` (`load_book_at` vs `advance_book_at`);
  `harness/execution/store.py:846-855` (`DELTA_BATCH_LIMIT`, fix 26 note).
- Storage: `pg_database_size` 148 GB; partitions `orderbook_events_y2026w37` 79 GiB, `_y2026w38` 16 GiB,
  `venue_trades_y2026w37` 1.8 GiB, `raw_responses_y2026w37` 3.7 GiB, legacy tape 15 GiB;
  `db.growth_gb_per_day` 14.5 (Sep 18). `backup_runs`: partition ok rows 40 and 41 (Sep 14), drill rows
  5/10/27 (nightly kind only). `/srv/sports-harness/backups/partitions/`: both w37 units with `.dump`,
  `.dump.age`, `.meta.json`, `.ok`; link count 2 on the ciphertext (published to `backup-export/`); NAS
  pull success is observable only on the NAS. `deploy/backup/drill.sh:9-16` (plaintext only, decrypt is
  off-host), `:29` and `:91-95` (SKIP exits 0), `:142-154` (partition units return `ROWS_MATCH n/a`);
  `harness/db/partition.py:191-192` (declarative range children: DETACH then DROP is the right pair);
  `harness/ops/checks.py:28`; `harness/report/tables.py:1064-1070`, `harness/dashboard/app.py:388-420`
  (`ts`-bounded reads).
- Review record: the two adversarial reviewer reports (opus, read-only) are reproduced verbatim in
  `rulings-review-2026-09-18.md` beside this file; the overturned points are folded into the rulings
  above (item 21's enumeration wording; item 19's "dormant Sunday costs nothing"; item 1's
  preconditions). Commit both files unchanged as evidence, as journal 262 did for Thursday's ruling.
