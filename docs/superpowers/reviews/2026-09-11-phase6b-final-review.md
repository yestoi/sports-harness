# Phase 6B final whole-branch review (`final-6b`)

- Worktree `/home/trey/dev/sports-wt/phase6b-integration` at `fd70692`; merge-base with main `a2287da`.
- Package `/home/trey/dev/sports/.superpowers/sdd/2026-09-11-phase6b-repair-execution/review-final-a2287da..fd70692.diff`
  (32 commits, 48 files, +8,030 / -436 confirmed by `git diff --stat a2287da..fd70692`).
- Reviewed 2026-09-14 20:00-20:25 CT. Worktree left clean (`git status --short` empty).

## Verdict

**Mergeable with one Important fix before the release commit.** No acceptance item is unmet, no
accounting is wrong, no data is lost, the R1 invariant holds, and the migration is additive-only.
The single Important is a release-time constant, not a code defect; its remedy is one line the
controller already owns per `verify.md:487-488`.

- **Critical: 0**
- **Important: 2** (IMP-1 release-time constant; IMP-2 a false coverage claim in the phase's own
  acceptance artifact -- remedy is docstring-only and is included in the Minor diff below)
- **Minor: 14** (all still-open items are documented limits or carried notes; none blocks merge)

## Gate invariance (R1)

```
PYTHONPATH=. .venv/bin/python -c 'from harness.report.gate import criteria_hash; print(criteria_hash())'
5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5
```

Matches the required value exactly. `git diff --name-only a2287da..fd70692 -- harness/variants/`
is empty. `harness/report/gate.py`'s only change is two text lines appended after the results
(`gate.py:868-875`: `corrections_in_force=` and the mixed-population note) plus the
`harness.corrections` import; no criterion, threshold, family, grid or eligibility expression is
touched, which the hash independently confirms.

## Tests

**Not run.** The controller's full unsharded suite started 19:59 CT and holds the shared test
lock until about 20:45 CT; the report deadline is 20:30 CT, so the scoped set
(`tests/test_execution_regressions.py tests/test_execution_pure.py tests/test_checks.py
tests/test_rescore.py tests/test_corrections.py`) could not be started without blocking past the
deadline. The controller's own suite is the merge gate. The hash command above was run and is the
only execution this review performed besides read-only greps and one import check
(`PYTHONPATH=. .venv/bin/python -c 'import harness.execution.dirty_time, tests.test_execution_regressions'`
-> `imports ok`, run against the edited copies before they were restored).

## Acceptance table

Roadmap 6B row: "Realistic tests and copied-tape audit pass; actual/counterfactual accounting and
replay inputs are explicit"; plus roadmap.md Pre-loaded decision 2's list.

| # | Acceptance item | Status | Cited evidence |
|---|---|---|---|
| 1 | Realistic mixed-market fixtures | Met | `tests/test_pipeline.py:59-100` seeds ten venue markets over moneyline / spread (3 thresholds) / total (2) / draw with `unmatched` and `fuzzy` match statuses, and is the seed every executor test uses (`tests/test_exec_loop.py:47`, `:125`); `tests/test_fills_tape.py:36`+`:68` reads a committed **real** 60 s NCAAF tape slice (`fixtures/tape_sample_lou_miss_2026-09-07T03.json`) with no skip path; `tests/test_execution_regressions.py:269-289` uses a realistic 150-order / 40-game / two-sport population |
| 2 | A genuine gap/reconnect case | Met | `tests/test_execution_regressions.py:82-110` (a real missing subscription frame still writes a gap row); `tests/test_book.py:610-645` (a book anchored before a reconnect is dirty `session_boundary` until it re-anchors), `:676-708` (a pre-reconnect snapshot does not clear the boundary), `:790-813` (`load_book` itself returns `session_boundary`), `:343` (a sid-0 gap after the REST anchor's tape position dirties), `:710` (a book rebuilt at a past cursor carries its subscription's gap verdict) |
| 3 | A recovery containing an older trade | Met | `tests/test_execution_regressions.py:177-201` (recovery takes no fill from a trade inside the gap); `tests/test_execution_pure.py:92-116` (a recovery anchors the print floor with the queue), `:117-133` (a recovery never lengthens the queue), `:134-164` (a late REST print above the floor is applied exactly once) |
| 4 | Liquidity conservation | Met | `tests/test_fills_tape.py:167-191` (route (b): fill <= volume that printed at or through our price, queue never grows, one-call == 20 chunks == persisted boundary, on the real slice), `:194-211` (monotonicity asserted after each of 20 pieces); `tests/test_fills.py:812-841` (600 decrements in one horizon all still explained), `:825` and `harness/execution/fills.py:445`, `:464` (bucket-cap overflow **merges** under the older timestamp, conserving volume, per ruling I1) |
| 5 | No post-expiry fills | Met | `tests/test_execution_regressions.py:229-250` (the watched track takes no fill after expiry); `tests/test_execution_pure.py:165-189` (a print stamped exactly at the expiry still fills - the boundary); `tests/test_fills.py:348-358`, `:260-275` (no cross from a book past the deadline), `:276-290` (a cross at the deadline instant is taken); production code `harness/execution/loop.py:1023`; operational detector `docs/superpowers/autopilot/verify.md:374-377` (query 2, unfiltered by `fill_method`) |
| 6 | No placement from rejected targets | Met | `tests/test_execution_regressions.py:251-268` (a rejected latest verdict yields no Place); `tests/test_exec_loop.py:744-787` (DB-backed: exactly one `signal_rejected` skip row after two loops, and no order) - spec §1.4's idempotence assertion, added by T5 ruling I2; `tests/test_replay.py:466-471` asserts the rejected test precedes the capacity test (D14); production code `harness/execution/plan.py:511`, `:519` |
| 7 | Batch/restart consistency | Met | `tests/test_fills.py:542-562` and `:563-596` (chunking invariance, with and without a book), `:715-736` (the ledger survives a persisted loop boundary), `:865-887` (2,000 already-seen prints change nothing), `:390-401` (re-feeding the same prints changes nothing); `tests/test_fills_tape.py:212-234` (the real slice reconciles across a persisted loop boundary via `_state_columns`/`_state_of`); `tests/test_exec_loop.py:1094` (every restart reads with the cursor behind the head), `:1200` (a partial delta batch advances the cursor and the next loop continues), `:1411`/`:1476` (per-ticker batch shrink and recovery) |
| 8 | An independently calculated expected queue/ledger result (not a repeat of the implementation's assumption) | Met | `tests/test_fills.py:643-665` ("Expected queue 2, fill 0 for each of the four splittings. Derived independently ... An implementation that matched whole events rather than volume would pass one and fail three"), `:666-683` ("Expected queue 0, fill 3 - in either arrival order"), `:684-701` ("Expected queue 0, fill 3, `cancels_ahead` 2 - the case a horizon-less ledger breaks"), `:737-769` (the `behind` policy agrees with `ahead` whenever nothing is retired); `tests/test_replay.py:429-486` derives 12 keys x 2 slots -> 30 planner actions and 10 rows from the planner's ordering rule, and asserts the two quantities separately; every case in `tests/test_execution_regressions.py` and `tests/test_execution_pure.py` opens with an "Expected ... / Computed independently" docstring |
| 9 | Timing-policy differences recorded, never hidden under a parity claim | Met | `harness/replay.py:56-68`: `grid_steps` and `live_steps` are "published instead of a parity verdict, which is suspended while the timing policies differ (D15)", `live_steps` stays `None` = "not measured here, never zero"; `tests/test_replay.py:581-605` (the counts carry both step counts and both correction sets); `tests/test_replay_execute.py:272-283` records the live/replay anchoring divergence and its cause in the R14 docstring - the strict xfail is gone because the **fixture** was corrected (`_Tape` delivers on the clock), not because the difference was suppressed; `harness/report/gate.py:868-875` prints the mixed-population note on the gate itself; ledger ruling 70 and 98 are consistent with the shipped code |
| 10 | The order 157 audit publishes validated / corrected / unverifiable with the supporting tape | Met | `docs/superpowers/reviews/order-157-audit.md:113-156` (Result): **`unverifiable` on the second definition** (it differs and no hypothesis is met), run 2026-09-14 17:38 CT on the committed capsule `docs/superpowers/reviews/2026-09-11-phase6-roadmap/capsule/order-157`, JSON `docs/superpowers/autopilot/evidence/2026-09-14-audit-order-157-1738.json`; the three hypotheses' observed counts are tabulated whether met or not; `manifest_slices_total` 6 / `in_interval` 0; `repaired_filled` 63.92 vs recorded 38.92; `harness/corrections.py:140` carries the verdict, `tests/test_corrections.py:57-61` pins the vocabulary; `harness/audit.py` (398 lines) + `tests/test_audit_order.py` (571 lines) are the instrument; the one-snapshot capsule limit is disclosed at `order-157-audit.md:76-84` |
| 11 | No-watcher outcomes re-scored only after the same repairs | Met | `harness/rescore.py` (476 lines): both tracks are re-simulated by the repaired `simulate_fills` under a **named** correction set (`correction_ids`, `rescore.py:455`), the denominator is `nw_done = true` (`rescore.py:1-14` and `tests/test_rescore.py:213-229`), and the result is a three-way partition with the right-censoring caveat, never a single ratio (`tests/test_rescore.py:134-168`, `:421-463`); `tests/test_rescore.py:169-187` (`test_the_originals_are_untouched`) pins that no `orders`/`fills`/`ledger` row is edited; the manifest names the instrument per correction (`docs/superpowers/reviews/2026-09-11-correction-manifest.md:11-17`, C1-C5 `harness rescore --correction C1,C2,C3,C4,C5`, C6 `harness replay --population range`) |
| 12 | Raw historical cleanliness measurements retained | Met | `harness/execution/dirty_time.py:1-17`: the gate-read columns `orders.dirty_seconds` / `dirty_minutes` **keep** their nominal accrual because moving to elapsed accrual would move the 60-second classification boundary (an R1 measurement change, deferred to the user as §0.13c); the elapsed mechanism is a parallel derived query over two new tables, and unobserved time is reported rather than folded into clean time (ruling IM-15); C0's `eligible_measurements` is "raw historical cleanliness and counts, labelled retrospective" (`harness/corrections.py:161`); C1's and C5's manifest entries exclude the pre-boundary classifications rather than rewriting them; `verify.md:366-372` query 1 compares the pre-deploy sums of the original rows |
| 13 | Gate meaning/eligibility unchanged (R1) | Met | `criteria_hash()` prints the required sha (above); `harness/variants/` untouched; `harness/report/gate.py` adds text lines only; `tests/test_rescore.py:281-307` (`test_no_gate_criterion_reads_order_rescores`), `:308-338` (no report builder counts a pending counterfactual as complete), `:250-280` (the render discloses the mixed population) |
| -- | Migration additive-only | Met | `migrations/versions/0008_phase6b_execution.py`: 13 x `alter table orders add column if not exists`, two `op.create_table(..., if_not_exists=True)` interval tables + two indexes, one `order_rescores` table; `downgrade()` is `pass` (roadmap invariant 5). No drop, no rename, no type change, no `not null` on an existing column, nothing CONCURRENTLY. The 0008 -> 0011 renumber at the main merge (D9) is out of scope here |
| -- | No non-GET venue call, no new outbound host | Met | A scan of every added line under `harness/` for `requests.post|put|patch|delete`, `session.post`, `httpx`, `aiohttp`, `urlopen`, a literal `http(s)://` or `socket.` returns nothing. `harness/audit.py` reads a capsule directory only ("no database and no NAS access"), `harness/rescore.py` and `harness/execution/dirty_time.py` are SQL-only |
| -- | No gate criterion change | Met | See R1 above |

## Findings

### IMP-1 (Important) `NO_WATCHER_CUTOFF_FIXED_AT` is a placeholder that already lies in the past

`harness/ops/checks.py:46`:

```python
NO_WATCHER_CUTOFF_FIXED_AT: datetime = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
```

2026-09-15T00:00Z is 2026-09-14 **19:00 CT**, i.e. already elapsed at the time of this review and
before the 6B deploy. The user's ruling (journal 206, quoted verbatim at `checks.py:29-38`) bounds
`fills_outside_placement_window` and `markouts_at_after_horizon` to fills written **after the
no-watcher cutoff fix ships**, and the loop fix (`harness/execution/loop.py:1045-1047`,
`NO_WATCHER_KICKOFF_MARGIN`) ships only with this merge. Left as shipped, any fill the still-deployed
pre-fix build writes between 19:00 CT and the actual deploy falls inside the bound and can fail the
daily checks for a defect the deployed build still has - which is precisely the amnesty the ruling
granted. The code comment (`checks.py:39-45`) and `verify.md:487-488` already assign the controller
the duty of setting the constant to the release instant at the release commit, and nothing pins the
value (`tests/test_checks.py` seeds relative to whatever it holds), so this is a one-line edit with
no test to follow it. **Action: set the constant to the deploy instant in the release commit.** No
behaviour else depends on it.

### IMP-2 (Important) The acceptance artifact names a guard that no longer exists

`tests/test_execution_regressions.py:15-17` and `:50-55` cite `tests/test_book.py:84`
(`test_seq_gap_marks_dirty`) as the test that "instead catches" the naive C1 repair. That test does
not exist anywhere in `tests/` (`grep -rn "test_seq_gap_marks_dirty" tests/` matches only these two
docstrings) - C1 retired it, exactly as the case-1a docstring's own last sentence required, because
gap detection moved to the subscription level. `harness/execution/book.py:273-274`, also cited, is
now the `anchor_as_of` field, not the per-object check. The file is the phase's own acceptance
artifact ("Realistic tests ... pass") and ledger line 51 put this dangling cross-reference on the
whole-branch list, so it should not merge claiming coverage that is gone. No assertion depends on
the text; the remedy is docstring-only and is the first two hunks of the Minor diff below, which
re-point the claim at the two subscription-level guards that do hold the line
(`tests/test_book.py:343`, `:710`).

### Minors (all dispositioned as still-open documented limits or carried notes)

| # | Minor | Where | Disposition |
|---|---|---|---|
| m-1 | `_order_key` omits `queue_ahead_at_place`, so R14 compares orders on `(ticker, side, prob, contracts, placed_at)` only; a live/replay divergence in queue depth that leaves fills identical would not fail the parity test | `tests/test_replay_execute.py:113-114` | Still open. Ledger 96 made it conditional on T4 unifying the anchoring instant, which T4 did. Not proposed as a diff here because I could not run R14 to confirm the field agrees; recommend adding it in 6C after one green R14 run |
| m-2 | The §1.5 interval writes sit on the step critical path with no savepoint of their own (T6 F4) | `harness/execution/loop.py:565-581` | Still open, documented limit. A savepoint is a behaviour change; `exec.loop_ms` (T12 verify row) is the detector |
| m-3 | `open_interval` does one SELECT per stepped market per step (T6 F5) | `harness/execution/store.py:789-790` | Still open, documented limit; the close side was already batched into one statement (`store.py:799-812`). Measure `exec.loop_ms` after deploy, batch if it shows |
| m-4 | The elapsed measure excludes NULL-expiry orders while `_clamped` grants them the whole period (T6 F6) | `harness/execution/dirty_time.py:74` | Still open, documented limit; `harness/rescore.py:457-459` writes NULL rather than zero for them. Recorded in the module docstring by the Minor diff below |
| m-5 | No sweeper closes an interval left open across an executor **restart**: `gone` is derived from this process's own `self.books` cache, which is empty after a restart (T6 F7) | `harness/execution/loop.py:540-542`, `:580-581` | Still open, documented limit with a detector (verify §3 row 5, "no open row older than two hours at 01:00-08:00 CT"). Error direction is safe: an orphaned open dirty row over-reports dirtiness, never clean time |
| m-6 | `exec.nw_pending` is written every step but is absent from `CAPSULE_METRIC_NAMES`, so it never reaches a capsule | `harness/execution/loop.py:374` vs `harness/capsule.py:191` | Still open observation (ledger 144). No accounting effect; 6C/6D capsule work |
| m-7 | `heartbeat["tape_deferred"]` has no reader | `harness/execution/loop.py:898` | Still open observation (ledger 144) |
| m-8 | `_note_backoff` is O(tickers x working) | `harness/execution/loop.py` | Still open observation (ledger 144); small constants at 55 tickers |
| m-9 | `harness/report/tables.py:1107` derives queue consumption from the now-NULL `traded_at_price`, so post-boundary orders drop out of that table | `harness/report/tables.py:1107-1111` | Still open, **documented** in C3's manifest entry (`2026-09-11-correction-manifest.md` C3 "Excluded measurements"). 6C report work reads the reconciliation columns instead |
| m-10 | `_scratch_engine` builds its admin URL by string surgery on the test URL instead of `make_url`/`url.set`, which is why 21-22 errors appear under the worker sandbox's Unix-socket URL | `tests/test_alembic.py:69-75` | Still open; standing ruling (ledger 71) - worker-sandbox limitation only, the controller's TCP suite runs the file green. Not an acceptance risk |
| m-11 | C1-C6 `config_hashes`, `affected_order_id_range`, `affected_run_id_range` and `deploy_sha` are `<filled at merge>` / `()` placeholders | `harness/corrections.py:114-131` and the manifest's six entries | By design (D11, agents have no NAS access). **Controller merge-time duty**, with the count assertions in the same commit |
| m-12 | Amendment 6 "excludes nothing while the record's placeholders are unfilled" | `harness/report/amendments.py` (commit e878da9) | Carried: must be revisited in the same commit that fills the ranges |
| m-13 | The plan's Task 11 `EXECUTOR_VERSION` pin list was incomplete (named `test_book`/`test_fills` only; four more files pinned 4.4) | ledger 204; fixed on the branch by `0e3f3b3` | Fixed on the branch; recorded as a plan-quality carry for the next phase's file lists |
| m-14 | `tests/conftest.py` merge note: the branch's `FIXTURE_PARTITION_WEEKS` block is byte-identical to main's fix 59, but main has since added fix 56 (`truncate_all`, the pool-checkin `reset all`) and the branch edited `db_session` adjacent to where main did | `tests/conftest.py:62-86` on the branch vs `main` | Not a defect. Flagged so the main merge resolves `db_session` deliberately rather than by luck |

## Whole-branch list and deferred-Minor disposition

Ledger = `/home/trey/dev/sports/.superpowers/sdd/2026-09-11-phase6b-repair-execution/progress.md`.

| Ledger line | Item | Disposition |
|---|---|---|
| 46, 51 | T1 Minor 2 (alembic stderr traceback, pre-existing) | Documented limit; unchanged and pre-existing (see m-10 for the related socket-URL item) |
| 46, 51 | T2 Minor 6: dangling cross-reference in `tests/test_execution_regressions.py` | **Still open -> IMP-2**; the cited guard was retired by C1. Remedy in the Minor diff |
| 46, 51 | T2 Minor 6: stale comments in `fills.py`, `state.py`, `venue.py` | Fixed on the branch - no stale line-number citation remains in those three files (grepped) |
| 50, 51 | T2 re-review surface: `_sim_book` issues one extra reconnect read per cursor rebuild and can hand a `session_boundary`-dirty book to `simulate_fills` (CR-7) | Documented limit; `_sim_book` (`loop.py:~1075`) returns the book as it stood at the track's cursor by design ("the honest answer is the book as it stood at that tape position"), and a dirty book blocks decisions - the conservative direction (`book.py:181`) |
| 50, 51 | T2 re-review surface: the probe's blind spot for a delta taped after a REST fetch but stamped > 5 s before it | Documented limit, ruling 44's own cost line; the module already assumes skew <= 5 s |
| 71 | `_scratch_engine` `make_url`/`url.set` repair | Still open, Minor m-10; standing ruling, worker-sandbox only |
| 73 | T4 brief must state the no-book branch already calls `SimState.anchor(...)` | Satisfied by T4 (C2 landed on both re-anchor branches; `tests/test_execution_pure.py:92`) |
| 73 | `tables.py:1107` reads the now-NULL `traded_at_price` | Still open, Minor m-9; documented in C3's manifest entry |
| 73 | Captured-log `anthropic.lib.credentials ImportError: TokenCache` in exec_loop/replay tests | Pre-existing sandbox artifact, out of branch scope (memory note "worker sandbox credentials-mask bug") |
| 79, 89, 96 | I4 jsonb volume at the cap (~88 KB per track per step) and `_retire`'s per-event rebuild of the prints tuple | Documented limits with a named detector (T12's `exec.loop_ms` verify row); caps unchanged per D5 |
| 89 | T3 Minor 10 | Documented limit beside I4's cost note |
| 95 | Two test-only T3 Minors deferred to the fix wave | Fixed on the branch (T3 rounds 1-2, `e6975ab`/`13f891b`) |
| 96 | The one-call/chunked divergence at `TRADE_ID_CAP` | Documented, bounded limit (`harness/execution/fills.py:79-85`); chunking invariance is asserted below the cap (`tests/test_fills.py:542`, `:563`) and on the real slice |
| 96, 106 | `_order_key` should gain `queue_ahead_at_place` | **Still open, Minor m-1** |
| 102, 106 | R14 marker ownership (item 6) | **Fixed**: the strict xfail is gone; `tests/test_replay_execute.py:272` is a plain assertion and the divergence's cause is recorded in the docstring, not hidden (acceptance 9) |
| 102, 106 | Case 3's stale line citations (to T12/whole-branch) | Partly still open: `plan.py:607` is now `:616` (in the Minor diff); `plan.py:511`/`:519` verified accurate; `test_book.py:84` -> IMP-2 |
| 105 | T4 Minors 1-7 (reason wording, no-book comment, `executor.replay = False`, `states: Iterable[SimState]`, `anchor_as_of` note, a DB-level recovery-floor assertion) | Fixed on the branch across T5/T6 rounds; `anchor_as_of`'s note is at `book.py:272-275`, the DB-level floor case is `tests/test_exec_loop.py:1094` |
| 106 | Placement reads `queue_ahead_at_place` and the cursor from the cache head with no correction id owning the placement instant | Documented limit; the placement instant is not a 6B correction, and the R14 fixture no longer folds a future delta before placement |
| 106 | `anchor` clears print claims (one docstring sentence) | Fixed (`harness/execution/fills.py`/`state.py` docstrings; `tests/test_execution_pure.py:92-116` asserts it) |
| 106 | Helper duplication between `test_execution_pure.py` and `test_execution_regressions.py` (IM-4); pure case 1 replicates regression case 3's fixture | Documented limit; `test_execution_pure.py` imports the shared helpers from `tests.test_fills` and the duplication is now the deliberate pure/DB split |
| 111 | T4 complete, 7 Minors to the whole-branch list | Dispositioned in the rows above |
| 127 | §3 row 2 scoped to `simulated = true` or explained | Fixed by T12 (`verify.md` Phase 6B rows, review APPROVED 0/0/0, ledger 210) |
| 127 | The lagging past-expiry order's two extra `load_book_at` queries per loop | Documented limit beside I4's `exec.loop_ms` note |
| 127 | C4's manifest entry and §0.13a name the pre/post `crossed`/`worst_case_fill` difference | Fixed: `2026-09-11-correction-manifest.md` C4 states it explicitly ("pre-boundary orders could carry a fill stamped after their own expiry") |
| 127 | One file owns the watched-track fixtures before T6 (IM-4) | Fixed: `tests/test_fills.py` owns them (`DEADLINE`, `T0`, `at`, `order`, `run`, `tdelta`, `tprint`), imported by the pure, regression and tape files |
| 127 | No test exercises `row.expiry is None` on either track | **Fixed**: `tests/test_execution_pure.py:230-248` (an order with no expiry accrues the whole period on both tracks) and `tests/test_rescore.py:366-388` |
| 144 | T6 F4 (no savepoint), F5 (per-market SELECT), F6 (NULL expiry), F7 (no restart sweeper) | Still open as Minors m-2, m-3, m-4, m-5 - each a documented limit with a named detector; F6 recorded in the Minor diff |
| 144 | `exec.nw_pending` not in `CAPSULE_METRIC_NAMES`; `tape_deferred` has no reader; `_note_backoff` O(tickers x working) | Still open, Minors m-6, m-7, m-8 |
| 152, 156 | Spec §1.6's "(config_history by the range's runs)" is not implementable; the placed-order-chain population is a lower bound | Documented limit, shipped as such: `harness/replay.py` resolves from the placed-order chain and the manifest's C6 entry states the lower bound and the out-of-scope per-run comparison in bold |
| 159 | `ReplayCounts.capacity_skips` carries the row count, not the planner-action count | Fixed and disclosed: `harness/replay.py:70-78` says so, and `tests/test_replay.py:474-486` asserts the 30 occurrences and the 10 rows separately |
| 165 | The manifest gate makes hypothesis (ii) unreachable on a gapped capsule | Documented limit, recorded for the user at `order-157-audit.md:99-112` |
| 166 | Task 8's three layout-forced deviations | Judged and approved by the T8 review; each has its own test (`tests/test_audit_order.py`) |
| 173 | T8 suite run 1 teardown `QueryCanceled` | Ruled an IO-load fixture artifact; main's fix 56 (`truncate_all`) now addresses it and arrives with the merge (see m-14) |
| 178, 179 | T9 rulings (strict `<` boundary; audit.py's `<=` aligned in Task 11) | **Fixed**: `harness/rescore.py:202` and `harness/audit.py:391` both use strict `<` against `FILL_TOLERANCE = Decimal("1")`; `rescore.py:190-201` records why |
| 179, 184 | T9 IMP-1/IMP-2 and Minors 1/3/4; Minors 2/5 carried | Fixed: IMP-1/IMP-2 in `b7c6557`, carried Minors 2/5 taken in the integration round `6bb34d6` (paged timing reads, public `tape_deltas`, `typer.echo`, engine disposal), with `tests/test_rescore.py:593`, `:625`, `:643`, `:698` as the cases |
| 190 | Audit-interval Minors 1-3 applied; Minors 4/5 carried as documented limits | Minors 4/5 taken in the integration round `6bb34d6` (audit Minor 4 and the strict tolerance) |
| 199 | T11 Important (`amendments.py` stopped at 5) and Minors M-1/M-2/M-4; M-3 (plan says `make deploy-nas`) stays verbatim | Fixed: Amendment 6 added (`e878da9`); the sha-field width test added in `6bb34d6`; M-3 is a controller record item at the deploy, not a branch change |
| 204 | The plan's `EXECUTOR_VERSION` pin list is incomplete | Minor m-13; the four files were fixed by the controller at `0e3f3b3` |
| 210 | T12 complete, 0/0/0; `docs/superpowers/autopilot` is outside the release tree | Consistent with the shipped `verify.md` (the Phase 6B rows at `:355-545`, including the two `:cutoff` predicates at `:485-496`) |

Rulings checked against the shipped code (all consistent): 42/44 (probe lower bound
`max(cursor, gap_check_id)`), 45 (`ws_connect_at` additive keyword, default `None`), 62/11
(revision `0008_phase6b_execution`, `down_revision = "0007_raw_events_lookup"`), 70/98 (R14
divergence recorded, marker retired), 72 (`HEAD_REVISION` 0008, `_WORKING_ORDERS` projection,
no-book branch via `SimState.anchor`, placement insert without `traded_at_price`), 76 (bucket
overflow merges), 77 (the `behind` arm splits a claimed pending amount), 78 (transitional
`recon_state` NULL read), 80 (`print_unmatched` as timestamped entries aged at the horizon), 86
(`legacy_traded_at_price`), 87 (the §1.3 symmetric-horizon ruling appended to the spec's rulings
list, `spec:594`), 122/123/124 (the rejected-verdict discriminating cases and the at-deadline
boundary), 152/156 (the placed-order-chain population), 159, 165, 178 (strict `<`), 182 (the
overlap test closed at both ends, an unreadable slice fails closed - `order-157-audit.md:96-99`),
188, 210.

## Minor diff (comments and docstrings only, no behaviour change)

Also saved as `/home/trey/dev/sports/.superpowers/sdd/results/6b-final-review-minor.diff`;
verified with `git apply --check` against `fd70692`. Hunks 2 and 3 are IMP-2's remedy; hunk 1 is
m-4's; hunk 4 corrects a stale line citation.

```diff
diff --git a/harness/execution/dirty_time.py b/harness/execution/dirty_time.py
index a1199d1..ca59628 100644
--- a/harness/execution/dirty_time.py
+++ b/harness/execution/dirty_time.py
@@ -15,6 +15,12 @@ with a `limit`, riding `orders_pkey` for the driving scan and `ix_mdi_market_sta
 
 Unobserved time is reported, never folded into clean time: absence of a dirty row means "not
 observed", not "observed clean" (ruling IM-15).
+
+One recorded limit (T6 review F6): the driving read is bounded by `o.expiry is not null`, so an
+order with no expiry has no elapsed measure here at all, while the nominal accrual
+(`loop._clamped`) grants it the whole period. `harness rescore` therefore writes NULL elapsed
+columns for such an order rather than a zero, and a reader must not read that NULL as "no dirty
+time".
 """
 
 from dataclasses import dataclass
diff --git a/tests/test_execution_regressions.py b/tests/test_execution_regressions.py
index dc395af..7d68bc3 100644
--- a/tests/test_execution_regressions.py
+++ b/tests/test_execution_regressions.py
@@ -13,8 +13,12 @@ Beside them sit passing guards -- behaviours a repair must not break. Case 1b ex
 `WsSink._check_seq`, not `BookState.apply_delta` where case 1a's defect lives; it guards the
 subscription-level input a sid-level replacement for the per-book check would depend on, and it
 does not itself flip when 1a does. The naive repair -- deleting the per-object dirty flag with
-nothing at the subscription level to replace it -- is instead caught by `tests/test_book.py:84`
-(`test_seq_gap_marks_dirty`) turning red, not by 1b.
+nothing at the subscription level to replace it -- is instead caught by `tests/test_book.py`'s
+subscription-level cases turning red, not by 1b:
+`test_rest_anchor_dirties_on_a_sid_zero_gap_after_its_tape_position` and
+`test_a_book_rebuilt_at_a_cursor_carries_its_subscriptions_gap_verdict`. C1 retired the
+per-ticker `test_seq_gap_marks_dirty` this paragraph used to name, exactly as case 1a's docstring
+below required.
 
 No database. Every case is either a pure object, the real `_simulate_order` with persistence
 mocked (as the probe runs it), the real `plan_actions`, or the real `fill_events` over a fake
@@ -47,12 +51,14 @@ def test_multiplexed_subscription_sequence_does_not_dirty_the_book():
     only when a message that would have changed it was lost, and none was. The captured probe
     output is `actual_dirty true`.
 
-    `tests/test_book.py:84` (`test_seq_gap_marks_dirty`) pins the opposite outcome on the same
-    method and the same call shape: `apply_delta` with a real per-ticker seq gap, asserting
-    `dirty is True`. 6B cannot unmark this case by deleting the per-object check at
-    `harness/execution/book.py:273-274` alone -- that turns `test_book.py:84` red. The repair
-    has to move gap detection to the subscription level (where `_check_seq` already lives) and
-    retire or rewrite that test to match, not just remove the per-book flag.
+    `tests/test_book.py`'s `test_seq_gap_marks_dirty` pinned the opposite outcome on the same
+    method and the same call shape -- `apply_delta` with a real per-ticker seq gap, asserting
+    `dirty is True` -- so 6B could not unmark this case by deleting the per-object check in
+    `harness/execution/book.py` alone. C1 did what this paragraph required instead: gap
+    detection moved to the subscription level (where `_check_seq` already lives), the per-object
+    check left `book.py`, and that test was retired with it. The subscription-level guards that
+    now hold the line are `test_rest_anchor_dirties_on_a_sid_zero_gap_after_its_tape_position`
+    and `test_a_book_rebuilt_at_a_cursor_carries_its_subscriptions_gap_verdict`.
     """
     a = BookState.from_levels("A", [[".30", "5"]], [[".60", "5"]], sid=SID, seq=1,
                               as_of=at(0), source="ws", anchor_id=1)
@@ -258,7 +264,7 @@ def test_a_rejected_latest_verdict_yields_no_place():
     reason `signal_rejected` (`harness/execution/plan.py:511`) -- so placing one in the same
     loop would cancel it in the next. The placement path never makes that test
     (`_intent_actions`, defined at `plan.py:519` and called from `plan_actions` at
-    `plan.py:607`), so a rejected intent still produces a Place.
+    `plan.py:616`), so a rejected intent still produces a Place.
     """
     rejected = intent(decision="rejected")
     actions = plan_actions([rejected], [], {1: market()}, {}, {"v1": cfg()},
```

## Instruction-like text seen inside data

Three classes, all treated as evidence and none acted on as an instruction to me:

1. **Quoted user decisions in code comments and commit messages.** `harness/ops/checks.py:29-38`
   quotes journal 206 verbatim, including imperatives ("Bound both checks ... bound the
   NO_WATCHER deadline at harness/execution/loop.py:900 (main) by kickoff minus 10 minutes");
   commit `6bb34d6`'s message repeats it. `docs/.../2026-09-11-phase6b-repair-execution-design.md:158-161`
   quotes journal 210 with a list of edits to make ("change harness/audit.py:316 so only ...;
   add the fixture case; rerun `harness audit-order` ... and replace ORDER_157_VERDICT"). These
   are the repository's record of the user's own rulings, already carried out on this branch; I
   verified them against the shipped code rather than executing them.
2. **Directives addressed to future tasks** in docstrings and the manifest ("filled by the
   controller at merge time", "the controller sets it to the release instant", "6C's report work
   reads the reconciliation columns instead"). Reported as controller duties (m-11, IMP-1), not
   performed.
3. **Nothing adversarial.** No text in the diff, the tape fixture or the capsule record attempted
   to redirect the review, change permissions or reach a host. The `tape_sample_*.json` and
   capsule fixtures are numeric/tape data only.

## Controller's next action

1. Apply the Minor diff above (or `6b-final-review-minor.diff`) - docstring/comment only, so the
   in-flight suite receipt stays valid for the release tree by the same reasoning as ledger 210
   only if the controller judges these test-file docstrings non-material; otherwise fold them into
   the post-merge commit.
2. Set `NO_WATCHER_CUTOFF_FIXED_AT` (`harness/ops/checks.py:46`) to the actual release instant in
   the release commit (IMP-1).
3. Read the in-flight full unsharded suite result (started 19:59 CT) as the merge gate; the scoped
   set named for this review was not run because the shared lock is held past the report deadline.
4. Then the phase acceptance against the roadmap Completion-evidence cell, the 0008 -> 0011
   renumber and the `config_hashes` / range / `deploy_sha` fills at the main merge (D11), and the
   release.
