# Phase 6D.1 final-review fixes

Final review: `2026-09-19-phase6d1-final-review.md` (opus, branch head 949676b, every plan task merged). Verdict: compliant with two §1.8(e) spec defects, quality high; 0 Critical, 2 Important, 5 Minor. One fix wave (opus, rulings D47-D52 in the ledger) as de38563 plus the controller's verify.md row 1 edit 5008178; scoped re-review (sonnet) clean; full suite on 5008178: 4,533 passed, 1 skipped (the ruled role-creation skip), pristine.

## Fix-wave report (implementer)

# 6D.1 final fix wave - implementer report (task `6d1-final-fixes`)

Worktree `/home/trey/dev/sports-wt/phase6d1-final-fixes` (branch `phase6d1-final-fixes`, base
`949676b`). Nothing committed and nothing staged: the working tree carries the changes for the
controller. Test database `harness_test_phase6d1_final_fixes` through `make test` only. No
`docs/superpowers/` file, no migration, no model, no dependency, no setting and no activation was
touched; `harness/research/spend.py`, `harness/execution/plan.py`, `harness/db/models.py` and
`migrations/` carry **no diff** (`git diff --stat -- ...` is empty for all four).

## Status per item

| Item | Status | Where |
|---|---|---|
| **I-2 (D47)** expiry path dormant with the rest of §1.8 | **FIXED** | `harness/research/veto.py`, `tests/test_veto_pacing.py` |
| **I-1 (D48)** `VALIDITY_WINDOWS` comment, no behaviour change | **FIXED** | `harness/research/features.py`, `tests/test_veto_features.py` |
| **M-a** `exp run`'s "orders" line is the run's count | **FIXED** | `harness/experiments/execution_viability/cli.py`, `tests/test_exp_cli.py` |
| **M-b** `_ARM_ORDERS` grouped left join | **FIXED** | `cli.py` (G2 tests unchanged and green) |
| **M-c** runbook §3 names the real pre-grant error | **FIXED** | `docs/runbooks/experiments.md` |
| M-d, M-e | NO CHANGE (journal only, per the brief) | - |
| **M1** writer interface docstring | **FIXED** | `storage.py` |
| **M2** `check_destination -> str \| None` | **FIXED** | `storage.py` |
| **M3** test of `isolation_check`'s printed read-back | **FIXED** (test only) | `tests/test_exp_cli.py` |
| **M4** three GUCs hoisted into connect options | **FIXED** (the engine supports it with no new dependency) | `harness/db/engine.py`, `source.py`, `tests/test_engine.py` |
| **M5** `HealthRow` evidence built before construction | **FIXED** | `cli.py` |
| **M6** stale T5 docstring | **FIXED** | `bookhealth.py` |
| **M7** the item's sport at the pacing call site | **FIXED** | `veto.py`, `tests/test_veto_pacing.py` |
| **M8** partial NDJSON marked on a guard refusal | **FIXED** | `capture.py`, `tests/test_exp_capture.py` |
| **M9** adapter fixture tidy | **FIXED** | `tests/test_exp_adapter.py` |
| **M10** | NO CHANGE (post-release duty); confirmed the chunk query names its index | see Confirmations |
| **M11** freezer and `rebuild_manifest` derive the same fields | **FIXED** (test) | `tests/test_exp_state.py` |
| **M12** every reader joins on `exp_order.id` | NO CHANGE - confirmed | see Confirmations |
| **M13** | NO CHANGE (the fourth missing reason is the user's) | see Confirmations |
| **M14** `Episode` identity | NO CHANGE to the shape; docstring added (no consumer needs an ordinal) | `episodes.py` |
| **M15** observer docstring | NO CHANGE - already stated in `observer.py`'s module docstring (confirmed, §4.2 paragraph) | - |
| **M16** freshness difference documented at the pure function | **FIXED** | `observer.py::_fair_p` |
| **M17** `ExperimentWriter.rollback` used in the observer's failure path | **FIXED** | `storage.py`, `observer.py`, `tests/test_exp_state.py`, `tests/test_exp_observer.py` |
| **M18** `exp decide` bounded by `run_id` | NO CHANGE, but **not fully confirmed** - see Concerns 4 | - |
| **M19** `exp report` re-hashes the capture tree | **FIXED** | `cli.py`, `tests/test_exp_cli.py` |
| **M20** | NO CHANGE (DDL, the user's) | - |
| **M21** censored rows re-recorded when the horizon matures | **FIXED**, by `ExperimentWriter.update` (**not** `delete`) - see Deviation 1 | `storage.py`, `outcomes.py`, `cli.py`, `tests/test_exp_cli.py` |
| **M22** 900 s cadence as a named constant | **FIXED** (pure rename, no behaviour change) | `harness/recorder/tick.py`, `features.py`, `tests/test_veto_features.py` |

## What each fix does

**I-2 (D47).** `veto_pass` now passes `cached_at`/`as_of` to `features.invalidated` only when a
pacing profile is loaded for the pass (`if fired is None and profile is not None:`, where
`profile = pacing.load_profile(settings.veto_pacing_profile)` - the same condition T6's claim
order and reservation use). With the setting at its default `None` the call is
`invalidated(trigger_features, numeric)`, byte for byte the pre-T6b call; `expired` stays `None`,
so no `{"expired": ...}` delta and no expiry-forced call can exist. §1.8 now activates as one
thing against one boundary instant. The pass docstring says so.

**I-1 (D48).** The three keys and their derived windows are unchanged. A comment at
`VALIDITY_WINDOWS` states that the trigger context never outlives one 30-minute
`veto_bucket_minutes` bucket, so only `espn_status` (900 s) can elapse in production and
`weather`/`fair_move` are §1.8(e)'s declared bounds, reachable only if the bucket grows.

**M-a.** `print(f"  orders {len(arm_orders)}")` - the run and arm's own `exp_order` rows, which
`_ARM_ORDERS` has already read - instead of `chunks[-1].orders`.

**M-b.** `_ARM_ORDERS` is now `left join (select exp_order_id, min(filled_at) ... where run_id = :r
and arm_id = :a group by exp_order_id)`. Same result set (null for an unfilled order), one pass
over the arm's fills instead of one per order.

**M4.** `make_engine` gained an additive keyword-only `session_gucs: dict[str, str] | None`, which
is appended to libpq's `options` startup string (`psycopg` needs nothing new: the factory already
passes `-c statement_timeout=...` that way). The default is `None`, so every existing caller's
`connect_args` is byte-identical - `tests/test_engine.py`'s two existing assertions still hold
unchanged. `source.source_engine` passes `SOURCE_SESSION_GUCS = {"lock_timeout": "1s",
"default_transaction_read_only": "on"}`, so a connection `pool_pre_ping` replaces mid-run carries
the read-only GUC, and `RESET ALL` at pool check-in restores to it rather than dropping it.
`reader()` still issues the same session `SET`s (an injected test engine has only those).

**M8.** `capture_slice` wraps each stream's write: on any exception the (already closed) file is
renamed to `<stream>.ndjson.partial` (`PARTIAL_SUFFIX`) and the refusal propagates unchanged; a
completed stream removes a stale marker. Nothing can now read a truncated prefix as the stream.

**M19.** `exp report` reads the run's `manifest.capture_hashes`, re-hashes
`<exp_dir>/<run_id>/<stream>.ndjson` (1 MiB blocks) and prints
`capture files re-hashed: N streams, M mismatch(es) (§2)` before any arm number, after `EXP_LABEL`.
A stream that no longer matches - **including one whose file is absent** - is written as a
`capture_hash_mismatch` `exp_limitation` row through `ExperimentWriter`, whose scope is
`{"stream", "path"}` and whose detail carries both digests. The writer is opened **only** when
there is a row to write, so a report on an intact tree stays read-only, and a second report on a
tampered tree writes no duplicate row (the existing rows' streams are read first). `run_dir` is
deliberately not used: a report may not create the tree.

**For verify.md row 1 (the controller's edit).** Exact command and expected output:

```
docker compose exec app-research harness exp report --run-id <run_id>
```
line 1 is `EXP_LABEL`; then, before the tables:
`capture files re-hashed: 13 streams, 0 mismatches (§2)` for an intact capture tree.
With one file tampered with or missing, the line reads
`capture files re-hashed: 13 streams, 1 mismatch (§2)` followed by an indented
`  capture_hash_mismatch   <stream>`, and
`select kind, scope->>'stream' from exp_limitation where run_id = '<run_id>' and kind = 'capture_hash_mismatch'`
returns exactly one row per affected stream (a re-run of the command adds none).

**M21.** `_RECORDED_OUTCOMES` now reads `censored` with the pair. A censored pair whose horizon
`outcomes.matured_horizons` says has arrived at `until` is recomputed and **updated in place** on
its natural key `(run_id, arm_id, exp_order_id, horizon)` through the new
`ExperimentWriter.update`; `observed_at` moves to the invocation that actually observed it. A
horizon that is still in the future, and a `close` horizon on a market the tape kept no close
for, are untouched. `outcomes.record_outcomes`/`rerecord_outcomes` share one `_build_rows`, so the
first write and the re-observation cannot drift.

**M22.** `harness/recorder/tick.py::ESPN_SCOREBOARD_S = 900` replaces the literal at the two ESPN
scoreboard call sites only (`_espn` and `_espn_rollover`'s dated fetch); the Kalshi events cadence
keeps its own 900. `tests/test_veto_features.py` now pins `tick.ESPN_SCOREBOARD_S == ESPN_POLL_S ==
900` instead of matching tick.py's source text.

## Deviations

1. **M21 uses `ExperimentWriter.update`, not `delete`.** §4.7's grant
   (`docs/runbooks/experiments.md` §2) is `GRANT INSERT, UPDATE ON exp_run, ..., exp_outcome, ...`
   with `REVOKE ... DELETE ...` above it: a `delete` path would fail closed in production and would
   need a grant change that is the user's. The brief's parenthetical ("or a natural-key path
   without DDL") covers this; the class docstring and `update`'s docstring both state the reason.
   `update` refuses an empty key and runs `insert_guard` first, so the production-table and
   foreign-`Table` refusals are exactly `insert`'s.
2. **M14 is a docstring, not an ordinal.** No consumer needs identity: `episodes_for` is called
   per `(run, arm, variant, market, side)` key, the only readers are `report.render`'s rule lines
   and the tests, and §2 declares no episode table.
3. **M4 touches a production module** (`harness/db/engine.py`), additively and with the default
   path unchanged. The alternative - a second `create_engine` call inside the experiment package -
   would have duplicated fix 76's single factory.

## RED -> GREEN evidence

All runs are `timeout 1500 make test TEST_ARGS='<paths> -q'` from the worktree root.

- **RED (I-2, M7)**: with the two `veto.py` hunks reverted in place,
  `tests/test_veto_pacing.py::test_the_expiry_path_is_dormant_while_no_profile_is_named` and
  `::test_the_pacing_reservation_is_asked_about_the_items_own_sport` -> **2 failed**
  (`assert (2 == 1)`; `assert [None, None, None, None] == ['nfl', 'nfl', 'ncaaf', 'ncaaf']`).
  **GREEN** after restoring the fix: `tests/test_veto_pacing.py` -> **25 passed**.
- **RED (M-a, M21, M19)**: with `cli.py` restored to `git show HEAD:...` (the base file),
  `tests/test_exp_cli.py` -> **6 failed, 14 passed** -
  `test_the_summary_line_names_the_counts`, `test_the_orders_line_counts_the_runs_orders_not_the_last_chunks`,
  `test_a_censored_horizon_is_re_recorded_when_it_matures`,
  `test_report_re_hashes_the_capture_files_and_says_they_match`,
  `test_report_writes_capture_hash_mismatch_for_a_tampered_file`,
  `test_report_reports_an_absent_capture_file_as_a_mismatch`.
  **GREEN** after restoring: `tests/test_exp_cli.py` -> **20 passed**.
  (`test_isolation_check_prints_one_read_back_line_per_privilege_it_names` (M3) and
  `test_a_censored_horizon_that_has_not_matured_is_left_alone` pass on the base too: M3 is pure
  new coverage and the second is the half of M21 that must not change.)
- **M8**: `tests/test_exp_capture.py` -> **16 passed**, including
  `test_a_refusal_inside_a_stream_leaves_a_marked_partial_file_and_no_complete_one`
  (asserts `books.ndjson` absent, `books.ndjson.partial` holds the written prefix, `CaptureRefused`
  still raised, and a later completed capture leaves no marker).
- **M17**: `tests/test_exp_observer.py` -> **22 passed**, including
  `test_the_failure_row_is_recorded_even_after_a_raise_left_the_transaction_aborted` (an aborted
  transaction: without `writer.rollback()` the insert raises `InFailedSqlTransaction` inside the
  bare `except` and no row lands).
- **M11, M17, M21 mechanism**: `tests/test_exp_state.py` -> **18 passed**, including
  `test_the_freezer_and_the_resume_rebuild_derive_the_same_manifest_fields`,
  `test_the_writer_updates_one_row_on_its_natural_key_and_refuses_an_unkeyed_update`,
  `test_the_writers_rollback_discards_its_own_work_and_keeps_the_session_usable`.
- **M4**: `tests/test_engine.py tests/test_exp_isolation.py` -> **18 passed, 1 skipped** (the
  pre-existing ruled role-creation skip). The two new cases assert the options string and the
  experiment source engine's three settings.
- **I-1, M22**: `tests/test_veto_features.py` cases
  `test_only_the_window_shorter_than_a_bucket_can_fire_under_todays_bucket` and
  `test_the_windows_are_the_cadences_the_harness_already_runs_on` (now pinned to
  `tick.ESPN_SCOREBOARD_S`).

### Regression sets (both required by the brief)

- `tests/test_exp_isolation.py tests/test_exp_cli.py tests/test_exp_report.py tests/test_exp_outcomes.py tests/test_exp_adapter.py tests/test_exp_state.py tests/test_exp_manifest.py tests/test_exp_observer.py tests/test_exp_bookhealth.py -q`
  -> **125 passed, 1 skipped in 108.41s**.
- `tests/test_veto_pacing.py tests/test_veto_features.py tests/test_veto_worker.py tests/test_annotator.py tests/test_exec_plan.py tests/test_tick.py -q`
  -> **291 passed in 238.61s** (this is where `tests/test_exec_plan.py`'s corpus `config_hash` case
  and `tests/test_tick.py`'s cadence cases run: no `config_hash` moved).
- Extra sweep over everything else I touched or could reach:
  `tests/test_exp_capture.py tests/test_exp_episodes.py tests/test_veto_queue.py tests/test_engine.py tests/test_cli.py tests/test_exp_decision.py tests/test_exp_forecast.py tests/test_exp_arms.py tests/test_exp_baseline.py tests/test_exp_liquidity.py tests/test_veto_prompt.py -q`
  -> **158 passed in 60.05s**.

The unfiltered full suite was **not** run (the controller runs it).

## Confirmations (the NO CHANGE items)

- **M10.** `_ARM_ORDERS`' comment still names `ix_exp_order_run_arm (run_id, arm_id, placed_at)`,
  and the new grouped join is bounded by the same `run_id`/`arm_id`. Re-explaining on real data
  stays the post-release duty §3's row names.
- **M12.** `_REPORT_OUTCOMES`, `_REPORT_OUTCOME_VALUES`, `_REPORT_GAME_SHARE`, `_DECIDE_SIGN`,
  `adapter.run_chunk`'s fill write and the new `_ARM_ORDERS` join all use `exp_order.id`;
  `arm_order_id` appears only as the upsert key. No change needed.
- **M13.** `outcomes.MISSING_REASONS` is still the three §1.9 spellings and `_row` censors a null
  `close_at`; an unknown close is therefore still a permanent censor. A fourth reason is the
  user's (vocabulary + DDL). Named here for the user, as ruled.
- **M15.** `observer.py`'s module docstring already states the §4.2 split (reads on the worker's
  `harness` session, writes only as `harness_exp`); nothing added.
- **M18.** Partially confirmed - see Concern 4.

## Concerns

1. **`exp report` now re-hashes the whole capture tree on every invocation.** That is what M19
   asks for, but on a real run the NDJSON tree is hundreds of MB to a few GB, so the command gains
   seconds-to-minutes of pure I/O and the report is no longer instant. There is no `--no-verify`
   flag (adding one would let the invariant be skipped). If the controller wants the cost bounded,
   the natural place is a flag defaulting to on, which is a decision above this dispatch.
2. **`exp report` can now open a writer.** Only when a mismatch exists, but that means a report on
   a tampered tree needs §4.7's grant and fails closed without it, where it used to print. This is
   the intended reading of "writes `capture_hash_mismatch` rows through the writer"; flagging it
   because it changes the command's capability class in one branch.
3. **`harness exp isolation-check` prints `EXP_LABEL` last, not first.** The final review's §0.6
   check cites `cli.py:47`, which is precisely this trailing print - the other eight commands
   print it first. §0.6 as written only says every command *prints* the label, so this is not a
   spec violation, and I did not change the order (it is outside this brief's items and would
   change a shipped output). The new M3 test asserts the read-back's eight lines plus the label
   without pinning the label's position. Worth a ruling.
4. **M18 is not fully true as stated.** Every `exp_*` read in `exp decide` is bounded by
   `run_id`. The three §1.10 statements - `_DECIDE_OBSERVED_FILLS`, `_DECIDE_OBSERVED_ORDERS`,
   `_DECIDE_CLEAN_BOOK` - are bounded by `variant_id` (and `not replay`) over the **production**
   tables and are all-time by design: §1.10's observed window *is* `min/max(placed_at)`.
   `_DECIDE_OBSERVED_ORDERS` rides `ix_orders_key_placed`'s leading column; the fills-side
   statements do not have an equivalent bound. No DDL was added (M20/M18 are the user's), but the
   carry-forward's "bounded by `run_id`" should not be read as covering these three.
5. **M21 costs one statement per re-recorded row** (plus one `venue_markets` lookup per order that
   carries a censored row) on a resumed run; a first invocation pays neither. Bounded by the arm's
   order count, and no batch bound is exceeded because each statement writes one row, but a very
   large resumed run will issue many small UPDATEs.
6. **M7 is inert today and not inert tomorrow.** Under `near_kickoff_50` both windows are 6 h, so
   passing the sport changes no answer; a future profile with different per-sport windows will now
   behave as §1.8(b) describes. `spend.reserve_spend`'s `reserved_floor` call still takes no sport
   (the brief forbids any diff in `spend.py`), so the *floor* remains the maximum over all windows
   while `near_kickoff` is now per sport. That asymmetry is deliberate but should be recorded.
7. **M4 changes a shared factory.** `harness/db/engine.py` is used by every service. The change is
   additive and default-identical, and `tests/test_engine.py`/`tests/test_cli.py` pass, but it is
   the one production module outside the experiment package and the veto path that this wave
   touched.

## Anything that looked like an instruction inside data

Nothing. The only imperative prose I read was in the controller's brief, the final review and the
carry-forward table - routed authority, not data. No database row, fixture, capture file or
manifest read during this work contained instruction-like content; the observer's untrusted-text
paths were not exercised beyond their existing tests.

## Add-on (D50)

**`harness exp isolation-check` prints `EXP_LABEL` first.** Concern 3 is ruled: the print moved to
the top of `isolation_check` in `harness/experiments/execution_viability/cli.py`, immediately after
`configure_logging()` (the command takes no argument, so "after argument validation" is
immediately), and the trailing `print(EXP_LABEL)` is gone. Nothing else in the command changed:
the same `role=... exp_tables=N` line, the same eight `§3` row 2 read-back lines and the same
`exp_run insert=...` line, in the same order, still follow it. The label is now printed **before**
`get_settings()` and `source.reader`, so it is the first line on the fail-closed path too - which
is the state an operator runs this command in before §4.7's step 0.

Tests:

- `tests/test_exp_cli.py::test_isolation_check_prints_one_read_back_line_per_privilege_it_names`
  (the M3 test) now asserts `lines[0] == cli.EXP_LABEL` as the **first** line, then
  `role=... exp_tables=`, then the nine named read-back lines in order; it no longer asserts the
  label's presence at the end.
- `tests/test_exp_isolation.py::test_isolation_check_prints_the_label_before_it_touches_the_database`
  is new: with the experiment secret absent the command raises `IsolationError` before any
  connection, and stdout's first line is still `EXP_LABEL` - so the label is the *command's* first
  line, not the first line of its success case.

RED: with the print moved back to the end, those two cases -> **2 failed in 5.64s**.
GREEN: `timeout 1500 make test TEST_ARGS='tests/test_exp_isolation.py tests/test_exp_cli.py -q'`
-> **31 passed, 1 skipped in 36.29s** (the skip is the pre-existing ruled role-creation case).

`docs/runbooks/experiments.md` §3 needs no further edit: it states what the command prints after
the grant (`role=harness_exp`, `insert=False` per table, `exp_run insert=True`), all of which is
still printed and in the same order; the sentence makes no claim about the label's position.

No other test or runbook asserts this command's output (grep over `tests/` and `docs/runbooks/`).

D51 and D52 are recorded as ruled: no change made for concerns 1, 2, 4, 5, 6 or 7.

## Scoped re-review

# Scoped re-review: 6D.1 final fix wave (rereview-6d1-final-fixes)

Diff reviewed: `.superpowers/sdd/2026-09-18-phase6d1-execution-viability/review-949676b..5008178.diff`
(de38563 worker diff + 5008178 controller verify.md edit). All reads via `git show <sha>:<path>`
from the main checkout; no worktree, no test run, no file changed but this report.

## Verdicts

**I-1 (D48).** ADDRESSED. `harness/research/features.py` `VALIDITY_WINDOWS` block gains a comment
stating only `espn_status` (900s) is reachable under the 30-minute `veto_bucket_minutes` bucket;
the three keys and their derived windows are byte-unchanged (`espn_status=900s`,
`weather=3600s`, `fair_move=21600s`). New test
`tests/test_veto_features.py::test_only_the_window_shorter_than_a_bucket_can_fire_under_todays_bucket`
asserts the reachable set is `{"espn_status"}` against `Settings.veto_bucket_minutes`.

**I-2 (D47).** ADDRESSED. `harness/research/veto.py` (`veto_pass`, ~line 390 of the module):
`if fired is None and profile is not None: expired = invalidated(trigger_features, numeric,
cached_at=trigger_as_of, as_of=item.created_at)`. With `veto_pacing_profile` at its default
`None`, `profile is None` and the call is exactly `invalidated(trigger_features, numeric)` -
byte-for-byte the pre-T6b call (confirmed: `grep -n "invalidated("` over `harness/research/`
finds exactly two call sites in `veto.py`, the bare one and this gated one - no second path
reaches `invalidated` with timestamps while dormant). Dormancy test
`test_the_expiry_path_is_dormant_while_no_profile_is_named` asserts one reservation, no
`{"expired":...}` delta, `from_cache=True` on the second signal. Case (e)
(`test_an_expired_cache_forces_a_call_and_records_the_window_it_left`) now sets
`veto_pacing_profile = "near_kickoff_50"` before running.

**M-a.** ADDRESSED. `cli.py` `run_cmd`'s orders line is now `print(f"  orders {len(arm_orders)}")`
(the run+arm's own `exp_order` rows already read by `_ARM_ORDERS`), replacing
`chunks[-1].orders`. Test `test_the_orders_line_counts_the_runs_orders_not_the_last_chunks`
asserts 2 across two synthetic chunks reporting 1 each.

**M-b.** ADDRESSED. `_ARM_ORDERS` in `cli.py` is now `... left join (select exp_order_id,
min(filled_at) ... from exp_fill where run_id = :r and arm_id = :a group by exp_order_id) f
on f.exp_order_id = o.id ...` - one pass over the arm's fills instead of one correlated
subquery per order, identical result shape (`filled_at` null for an unfilled order).

**M-c.** ADDRESSED. `docs/runbooks/experiments.md` §3 now distinguishes "before the secret is
placed" (`IsolationError: ... absent or empty`) from "secret placed, role not yet created"
(`role "harness_exp" does not exist"`), matching verify.md row 2's wording.

**D50 add-on (isolation-check label first).** ADDRESSED. `isolation_check()` in `cli.py` now
prints `EXP_LABEL` immediately after `configure_logging()` (line 44), before `get_settings()`
and `source.reader`; the trailing `print(EXP_LABEL)` is removed. Grep of all nine
`@exp_app.command` bodies at 5008178 confirms `print(EXP_LABEL)` is the first statement of the
command body in every case, including the fail-closed path (new test
`tests/test_exp_isolation.py::test_isolation_check_prints_the_label_before_it_touches_the_database`,
secret absent, `IsolationError` raised, label already printed).

**M1.** ADDRESSED. `ExperimentWriter`'s class docstring in `storage.py` now documents
`open/close`, `table`, `insert/upsert/upsert_returning`, `commit`, `rollback` and `update` as the
writer's full interface.

**M2.** ADDRESSED. `check_destination(...) -> str | None` in `storage.py`; docstring explains the
`None`-database case.

**M3.** ADDRESSED (test only). `tests/test_exp_cli.py::test_isolation_check_prints_one_read_back_line_per_privilege_it_names`
asserts `EXP_LABEL` first, then `role=... exp_tables=`, then the nine named read-back lines in
order.

**M4.** ADDRESSED. `harness/db/engine.py::make_engine` gains additive keyword-only
`session_gucs: dict[str, str] | None = None`; default path verified byte-identical
(`options = ["-c statement_timeout={ms}"]` then joined with `" "`, same as before when
`session_gucs` is `None`). `harness/experiments/execution_viability/source.py` defines
`SOURCE_SESSION_GUCS = {"lock_timeout": "1s", "default_transaction_read_only": "on"}` and passes
it to `make_engine` in `source_engine`; `reader()` still issues the same session `SET`s
afterwards (belt-and-suspenders for an injected test engine). New tests
`tests/test_engine.py::test_named_session_gucs_ride_the_connections_own_startup_options` and
`::test_the_experiment_source_engine_carries_its_three_settings_on_the_connection` assert the
built `options` string.

**M5.** ADDRESSED. `book_health` in `cli.py` now builds the verified-snapshot evidence via
`dataclasses.replace(row, evidence={**row.evidence, "snapshot_at_interval_end": ...})` before
appending to `rows`, instead of mutating `row.evidence[...]` on an already-constructed
`frozen=True` `HealthRow`.

**M6.** ADDRESSED. `bookhealth.py::persist`'s docstring now states `exp_book_health` exists
(migration 0015), replacing the stale "the migration is still to come" text.

**M7.** ADDRESSED. `veto.py`: `QueuedSignal.sport: str | None = None` added from the same
additive `left join games` (`_SIGNALS` now selects `g.sport`); `claim_bucket` and `_call_pair`
pass `queued.sport`/`sport=queued.sport` into `pacing.near_kickoff`. New test
`test_the_pacing_reservation_is_asked_about_the_items_own_sport` confirms
`asked == ["nfl", "nfl", "ncaaf", "ncaaf"]`. Note (not a defect in the diff): `harness/research/pacing.py`
is untouched by this wave and its `near_kickoff` docstring still says "`QueuedSignal` carries no
sport" (line ~215), which this fix makes stale prose in a file outside the diff - harmless, no
behaviour affected, flagged under New breakage below since it is a side effect of this change.

**M8.** ADDRESSED. `capture.py::capture_slice` wraps each stream's write in `try/except
BaseException`: on any exception the file (already closed by the `with`) is renamed to
`<stream>.ndjson.partial` (`PARTIAL_SUFFIX`) and the exception is re-raised; a completed stream
removes a stale `.partial` marker. Test
`test_a_refusal_inside_a_stream_leaves_a_marked_partial_file_and_no_complete_one` confirms the
`.ndjson` is absent, the `.partial` holds the written prefix, `CaptureRefused` still raises, and a
later successful capture leaves no marker.

**M9.** ADDRESSED. `tests/test_exp_adapter.py::_order` fixture gained a `kickoff` parameter; the
overnight-transition test now passes its own kickoff directly instead of rebuilding the adopted
`OpenOrderView` via `__class__(**__dict__)`.

**M10.** NO CHANGE (confirmed). `_ARM_ORDERS`'s comment still names `ix_exp_order_run_arm
(run_id, arm_id, placed_at)`; the new grouped join is bounded by the same `run_id`/`arm_id`.
Post-release re-explain duty stands, as ruled.

**M11.** ADDRESSED (test only). New
`tests/test_exp_state.py::test_the_freezer_and_the_resume_rebuild_derive_the_same_manifest_fields`
asserts `storage._MANIFEST_DERIVED` fields and `baseline_settings` agree between the frozen
`exp_run.manifest` and `storage.rebuild_manifest(...)`, and that `rebuilt.freeze() ==
stored_hash`.

**M12.** NO CHANGE (confirmed). `_ARM_ORDERS`'s new join, `_REPORT_OUTCOMES` etc. all key on
`exp_order.id`; `arm_order_id` remains the upsert key only. No diff needed and none made.

**M13.** NO CHANGE (confirmed). `outcomes.py`'s `MISSING_REASONS`/`_row` are untouched by this
diff (only `_build_rows`/`record_outcomes`/`rerecord_outcomes` were added); an unknown close is
still a permanent censor. Fourth reason is the user's, as ruled.

**M14.** ADDRESSED (docstring, no shape change - matches the ruled disposition).
`episodes.py::Episode`'s docstring now states deliberately no identity; no ordinal added, since
`episodes_for` is called per `(run, arm, variant, market, side)` key and no consumer persists an
`Episode`.

**M15.** NO CHANGE (confirmed). `observer.py`'s module docstring already stated the read/write
split; this diff makes no further change to it.

**M16.** ADDRESSED. `observer.py::_fair_p`'s docstring documents the three ways arm C's
freshness rule differs from production's (drops over-age lines instead of labelling them, ages
from `now` rather than pricing time, keeps a `None`-stamped line unconditionally), and the
resulting selection-bias caveat for a C-vs-A/B comparison.

**M17.** ADDRESSED. `storage.py` gains `ExperimentWriter.rollback()` (discards uncommitted work,
keeps the session open, distinct from `close()`); `observer.py::_record_read_failure` now calls
`writer.rollback()` before its insert. New tests
`tests/test_exp_observer.py::test_the_failure_row_is_recorded_even_after_a_raise_left_the_transaction_aborted`
and `tests/test_exp_state.py::test_the_writers_rollback_discards_its_own_work_and_keeps_the_session_usable`
confirm the aborted-transaction case is recorded and that rollback never discards a previously
committed write.

**M18.** NO CHANGE (confirmed), per D52's restatement. Verified directly:
`_DECIDE_OBSERVED_FILLS`, `_DECIDE_OBSERVED_ORDERS` and `_DECIDE_CLEAN_BOOK` in `cli.py` (~lines
1128-1165) are bounded by `variant_id` (and `not replay`) over `fills`/`orders`, with no `run_id`
predicate - all-time by design, exactly as D52 states (§1.10's observed window is
`min/max(placed_at)`). No DDL added; not a defect requiring a code change.

**M19.** ADDRESSED. `cli.py` adds `_capture_hash_rows`/`_verify_capture_tree`, called from
`report_cmd` inside the same read session, before any arm number is read from the query results
(the print happens after the `with source.reader(...)` block closes but before any table is
rendered). Re-hashes each manifest-recorded stream's file in 1 MiB blocks; a mismatch (including
an absent file) is written as an `exp_limitation` row of kind `capture_hash_mismatch` through
`ExperimentWriter`, opened only when there is a row to write (an intact tree stays read-only,
asserted by `monkeypatch.setattr(cli.storage.ExperimentWriter, "open", lambda *a, **k:
pytest.fail(...))` in `test_report_re_hashes_the_capture_files_and_says_they_match`). A stream
already recorded as mismatched (`_REPORT_CAPTURE_LIMITATIONS`) is skipped on a re-run, so no
duplicate row (`test_report_writes_capture_hash_mismatch_for_a_tampered_file`'s second
`report_cmd` call). Tests also cover an absent file. verify.md row 1's edit (5008178) states the
exact command (`docker compose exec app-research harness exp report --run-id <run_id>`), print
order (`EXP_LABEL` then "capture files re-hashed: <n> streams, ..." before the tables) and the
`select kind, scope->>'stream' from exp_limitation where ... kind = 'capture_hash_mismatch'`
query - all matching the implementer's report's stated command/output verbatim (the report's
concrete "13 streams" example vs. verify.md's generic "<n>" is a genericisation, not a
discrepancy).

**M20.** NO CHANGE (confirmed). No DDL anywhere in the diff (`migrations/`, `harness/db/models.py`
carry no diff); `exp_observation` remains unindexed, as ruled (the user's/6D.2's).

**M21.** ADDRESSED, via `ExperimentWriter.update` (not `delete`, a ruled deviation matching D51).
`storage.py::ExperimentWriter.update(table, *, key, values)` runs `insert_guard` (refuses a
non-writer `Table` and a non-`exp_` table) then refuses an empty `key`, then issues a plain `UPDATE
... WHERE <key columns>` (never an `INSERT`, so it cannot create a duplicate row - the
worst case is `rowcount == 0`). `outcomes.py` adds `matured_horizons` (asks whether a horizon has
actually arrived, using the same `_matures_at` the rows are built with - a `close` horizon with no
close stamp never matures) and `rerecord_outcomes` (shares `_build_rows` with `record_outcomes` so
first-write and re-observation cannot drift, then calls `writer.update` on the natural key
`(run_id, arm_id, exp_order_id, horizon)`). `cli.py::_record_run_outcomes` reads `censored` with
`_RECORDED_OUTCOMES`, computes `matured_horizons` only for each order's already-censored horizons,
and calls `rerecord_outcomes` only for the horizons that matured - a horizon still in the future,
or a `close` horizon the tape kept no close for, is left untouched. Tests
`test_a_censored_horizon_is_re_recorded_when_it_matures` (re-observed, no duplicate row, `close`
stays censored) and `test_a_censored_horizon_that_has_not_matured_is_left_alone` (byte-identical
on a second invocation whose window still ends early) both pass by inspection of the diff.

**M22.** ADDRESSED, confirmed a pure rename. `harness/recorder/tick.py` adds
`ESPN_SCOREBOARD_S = 900` and both ESPN call sites (`_espn`, `_espn_rollover`) now use it in
place of the literal `900`; grepping every remaining `900` in `tick.py` at 5008178 shows the
Kalshi events cadence (`_kalshi_events`, line ~905) still uses its own literal `900`, exactly as
the new constant's comment says it should ("Not the Kalshi event cadence ... moves
independently"). `harness/research/features.py` now pins `ESPN_POLL_S = 900` to the exported
name in its comment; `tests/test_veto_features.py::test_the_windows_are_the_cadences_the_harness_already_runs_on`
asserts `tick.ESPN_SCOREBOARD_S == ESPN_POLL_S == 900` instead of matching tick.py's source text.

## Invariants (re-verified independently)

`git diff --stat 949676b..5008178` is empty for `harness/research/spend.py`,
`harness/execution/plan.py`, `harness/db/models.py`, `migrations/`, `harness/config/settings.py`,
`pyproject.toml`, `constraints.txt`, `harness/variants/`, `Makefile`. `EXECUTOR_VERSION` is still
`"4.5"`, `PRICING_VERSION` still `"2.3"`. `docs/superpowers/autopilot/` is touched only by the
controller's own commit 5008178 (verify.md), not by the worker's de38563 - confirmed via `git show
--stat` on each commit separately. `git diff --name-only 949676b..5008178` lists exactly the 21
worker files plus verify.md; nothing else moved.

## New breakage

None found that changes behaviour or fails an invariant. One cosmetic, out-of-diff side effect:
`harness/research/pacing.py::near_kickoff`'s docstring (untouched by this wave) still reads
"`sport` is optional because `QueuedSignal` carries no sport" - now stale, since `veto.py`'s
`QueuedSignal` gained a `sport` field in this diff. No functional effect (the parameter was
already optional and correctly used); worth a one-line docstring fix whenever `pacing.py` is next
touched, not a defect in this wave.

All findings addressed: yes
