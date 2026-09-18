# Rulings for the controller, Fri 2026-09-18, second file (fix 85 lock_timeout; Task 11 review; the Saturday question)

Prepared by the 2026-09-18 review session (Fable, read-only, no controller lock) from the user's decisions on
the two open questions journal 276 and 277 put to the user, after reading the fix 85 review
(`.superpowers/sdd/results/fix-85-review.md`, its `## lock_timeout` section), revision 0014, the release
script's stop-drain-migrate order, the Task 11 section and its rulings block (plan revision 3, e04ee33), and
the row 86 design read (`.superpowers/sdd/results/design-86-report.md`, done 07:48 CT) with the controller's
07:45 CT query results. Same conventions as `ruling-for-controller-2026-09-18.md`: unmarked text is the
user's ruling, journal each as a `decision` entry quoting it verbatim with the time; STRIKE-IF marks a value
or choice the user may change; the loop edits none of the user-owned files.

---

## 1. Fix 85: raise `lock_timeout` for the build statement only

The reviewer's Important is accepted and the Friday ruling's wording is corrected: the bound that cancels a
`CREATE INDEX CONCURRENTLY` is the migration connection's `lock_timeout = '5s'`, not `statement_timeout`
(a concurrent build's two waits on other transactions are lock waits; fix 71's `canceling statement due to
lock timeout` was this error). The raised `statement_timeout` in 0014 stays and is close to inert.

Ruling: amend revision `0014_orders_intent_index` so that `lock_timeout` is raised for the build statement
only, read-set-restore in the same `finally` block as `statement_timeout`, inside the same autocommit block;
`migrations/env.py` keeps its 5 s for every other migration and for the healer. Value **120 s** (STRIKE-IF:
change the number). Reasoning for the value: a full release stops the app containers and drains their
orphaned backends before `migrate ensure` runs (`scripts/release-omarchy.py:646-658`), so nothing legitimate
holds `orders` during the build beyond autovacuum, which yields in about a second; a queued
ShareUpdateExclusive request never blocks INSERT/UPDATE on `orders` in any case; and with the apps already
stopped, a long lock wait is downtime before a failure, so the bound stays short. The docstring paragraph the
reviewer added (a cancelled wait raises out of the build statement and never reaches the `indisvalid` read)
stays true and stays in the file.

Procedure: one scoped opus re-review round on the amended range (a behaviour change in a merged revision);
the merge rule's test requirement for a code change on main (the migration is in the release tree, so the
branch's full-suite receipt is re-taken at the amended commit before the fast-forward); still held for
Monday's quiet-window full release; the `pg_index.indisvalid` read by hand after the release, whatever it
reports, and an invalid index is a stop and never a re-run, as ruled Friday.

Release preconditions the controller checks at the start of Monday's release, and journals: no `psql` or
other unnamed client backend with an open transaction on the `harness` database (the drain reports them as
`unnamed_backends`, it does not wait for them); no controller SQL of its own while the release runs; no dump
running (the recipe already refuses one); the executor's last loops short, no expiry cohort in flight (the
apps are stopped either way, but a step still in flight at the stop is what the drain has to kill).

## 2. Task 11 (6D plan revision 3): run the review now, then execute and release

The section and the controller's six rulings on the writer's open questions are accepted as written (336 read
as the non-null-feed product; the raw `market.market_type` written, never a bucket; `feed` null where no gap
row exists; a §3 row 2 count change after the release journaled as granularity, not regression; wave 9 alone;
no new test module).

Ruling: dispatch the one-round opus plan review now. If it is clean, execute Task 11 under the 6D ledger with
the plan's model allocation (sonnet implementer, opus reviewer), and release it app-only **today before the
18:30 CT NCAAF window** if review and full suite are clean in time, else Saturday morning inside the
Thursday-Saturday NCAAF app-only allowance (journal 128). Saturday is then the first game day with populated
cells, which is what item 21 step 2 needs. Row 87 stays serial behind it on the same files.
STRIKE-IF: if you would rather not restart the executor a second time today, strike "today" and it ships
Saturday morning.

Note for the 18:25 CT after-window read: with two app-only releases today (07:23 CT fix 79, and Task 11 if it
ships this afternoon) the fills-per-hour series has two release hours; journal both instants beside the read
and judge the "step down at the release hour" alarm against each, not the second alone.

## 3. Row 86 (docket item 23): the design read is in; prepare the Saturday question

The design read finished at 07:48 CT, ahead of its deadline; nothing here is a ruling on it yet. Two findings
the controller carries into the Saturday-morning decision, with its own query 4 (tonight, before and about
20 minutes after the Fri 18:00 CT expiry) attached:

- The memoization (proposal i) is larger than Friday's estimate: after the first expiry loop a cohort's rows
  share one cursor per ticker (Thursday: 5 book positions for 1,104 rows), so the report's arithmetic for
  Sunday's 11:50 CT cohort is 312 s on the first loop and 87 s on later loops, against about 950 s without
  it; peak memory about 68 MB worst case on the first loop, so it needs a row ceiling with largest-first
  eviction on the print-cache pattern. Still a mitigation, not a fix; its identity test is the two-build
  replay of Thursday's 00:00-00:25Z window with equality on every `nw_*` column, fills, ledger, dirty
  intervals and the `book_dirty` skip counts.
- The root is upstream of the cohort: the controller's query 1 shows each cohort ticker's own backlog past
  the frozen cursor was 800-7,600 deltas, under the 20,000 batch limit, so the truncation came from older
  live tracks on the same tickers. Query 2b: the oldest pending cursor across the 25,301 open tracks is from
  2026-09-14 18:09Z with 28.7 M deltas behind it, and `exec.tape_batch_min` reads 20,000 on every loop with
  `walk_deferred_n` 14-18k. The per-ticker tape window is anchored at that oldest cursor, so batches
  truncate every loop, the ticker reads as lagging, expired rows cannot close, and their cursors stay frozen.
  The same delta re-read is why fix 79's effect judgement read FAIL this morning (journal 275).

The question to bring me Saturday morning, with the design read's replay plan as its test: a track past its
deadline admits no delta beyond that deadline (`_merge_events`'s `d.ts <= deadline` test), so waiting for its
ticker to catch up to the tape head cannot change that track's value. May a past-deadline track close once its
batch has been read through its deadline (the last consumed delta's `ts` at or past the deadline, or the batch
not truncated), rather than only when the ticker is no longer lagging? That is a change to the lagging-close
rule my Friday ruling held unchanged, so it is mine to decide; the loop prepares the argument (the code
citations for both sides, what a replay of Thursday's window would have to show, and what happens to the
Sep 14 tracks) and proposes nothing else on it. No executor code ships on it before I rule.

---

## Evidence

- `.superpowers/sdd/results/fix-85-review.md` `## lock_timeout` items 1-5 (reproduced cancellation; SUE does
  not conflict with RowExclusive; the raise belongs in the revision, not env.py).
- `migrations/versions/0014_orders_intent_index.py` (`BUILD_STATEMENT_TIMEOUT`, the read-set-restore pattern
  to copy for `lock_timeout`, the `indisvalid` guard); `migrations/env.py` `run_migrations_online` (5 s / 300 s
  on the connection); `scripts/release-omarchy.py:146-200` (fix 71 drain, `KNOWN_TOOLS`, `unnamed_backends`)
  and `:646-658` (stop, drain, then `migrate ensure`).
- `docs/superpowers/plans/2026-09-13-phase6d-sustained-evaluation.md` Task 11 section (line 5576 at e04ee33)
  and "Rulings on the Task 11 plan revision" (controller sports-ed, 08:10 CT); journal 275 (the app-only
  release allowance for Task 11 and row 87's serial order); journal 277.
- `.superpowers/sdd/results/design-86-report.md` (findings for step 0; proposal (i) bound and time table;
  proposal (ii) kept separate; levers; queries 1-6);
  `.superpowers/sdd/hotfix-2026-09-18-expiry-cohort/controller-queries-0745.txt` (Q1 per-ticker backlogs
  1,240/1,768/110/833/797 at 00:08:29Z; Q2b min cursor 170434701 at 2026-09-14 18:09:28Z, 28,774,525 deltas
  past it; Q3 `tape_batch_min` 20,000 every loop).
