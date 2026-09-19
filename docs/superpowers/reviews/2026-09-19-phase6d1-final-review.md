# 6D.1 final whole-branch review (opus)

Branch `phase6d1-execution-viability`, base `60dd46a`, head `949676b`. Read from the main checkout
with the sports-worker shell tool only; no worktree, no test run, no file changed but this report.

Verdicts are at the bottom. Counts: **Critical 0, Important 2, Minor 5.**

## Critical

None. Every §0 invariant I could check mechanically holds at 949676b:

- **Isolation (§0.4, I12).** `git grep 'harness.experiments'` over `harness/` outside the package
  returns exactly two live mentions: `harness/cli.py:22`'s `from
  harness.experiments.execution_viability.cli import exp_app` (plan choice 5, ruling D2) and
  `harness/research/worker.py:63`'s `PASS_MODULES` string (ruling C1). Everything else is prose in
  a docstring using the slash spelling. `tests/test_exp_isolation.py::test_no_production_module_imports_the_experiment_package`
  allows exactly those two and asserts the CLI one appears once. No experiment module names a
  gateway, transport or `harness.execution.loop`. No raw `insert into` / `update ... set` /
  `delete from` text exists anywhere under `harness/experiments/`; every write goes through
  `ExperimentWriter`, whose `insert_guard` refuses a non-`exp_` table **and** a `Table` object that
  is not the writer's own, before the empty-row shortcut. `source.reader` sets the three §4.3 GUCs
  and then runs `assert_least_privilege` (the server's `has_table_privilege`), and
  `ExperimentWriter.open` orders secret -> privilege -> destination (ruling I1).
- **Additivity (§0.5, gate 3).** `migrations/versions/0015_phase6d1_exec_viability.py` creates
  eleven tables, four plain indexes and one `create or replace view`; no DROP/RENAME/TRUNCATE/
  DELETE/ALTER/backfill; `downgrade()` is `pass`; revision id is 28 chars; `down_revision` is
  `0014_orders_intent_index` and `harness/db/migrate.py::HEAD_REVISION` moves with it.
  `harness/db/models.py` declares the same eleven tables and the same four indexes
  (`ix_exp_order_run_arm`, `ix_exp_fill_trade`, `ix_exp_mismatch_run`, `ix_exp_limitation_run`),
  and `schema.py::_INDEX_DDL` carries the identical four `create index if not exists` statements.
  `EXECUTOR_VERSION` is still `"4.5"` (`harness/execution/__init__.py:12`) and `PRICING_VERSION`
  `"2.3"` (`harness/pricing/__init__.py:7`). `git diff --stat 60dd46a..949676b` over
  `harness/variants/`, `pyproject.toml`, `constraints.txt`, `harness/recorder/cadence.py` and
  `Makefile` is empty. The only `plan.py` edit is one branch guarded by
  `policy.cadence_allowance is not None`, and `BASELINE` leaves it `None`.
- **Dormancy (§0.8, §0.14a-c).** `Settings.veto_pacing_profile = None` and
  `Settings.exp_observer_enabled = False` (`harness/config/settings.py:220-240`).
  `pacing.load_profile(None) is None`, `reserved_floor(None, ...) == Decimal("0")` and
  `near_kickoff(None, ...) is False`, so `reserve_spend`'s new block is arithmetically inert; the
  caps are `Decimal` on both sides, so no float/Decimal type error waits at activation.
  `veto.claim_statement(None)` returns the unmodified `_OLDEST_BUCKET` object itself, and
  `claim_bucket` binds `:now` only for the profile statement. Nothing in the branch sets either
  setting. `arms.ARMS` holds A and B only; arm C has no arm row and no tape stepper.
- **Seams.** `adapter.run_window` commits the writer **per chunk** and `run_chunk` writes the
  checkpoint last, so a later failure never loses a completed chunk; `storage.resume` checks the
  rebuilt manifest hash before it reads the state row; `cli.run_cmd` rebuilds the manifest for the
  current code/settings and refuses on mismatch before any step. `cli._record_run_outcomes`
  implements D44's skip-not-update idempotency by reading `(exp_order_id, horizon)` pairs first and
  batches each horizon group under `exp_batch_rows`, so `writer.insert`'s own bound cannot be hit.
  Both `exp report` and `exp decide` render arm rows through `report.arm_table(..., exploratory=True)`
  (cli.py:1185), so every exploratory cell carries `exp_label`; `report.sum_rows` raises
  `PortfolioSumRefused` across two identities. `EXP_LABEL` is printed first, after argument
  validation and before any number, in all nine `exp` commands (cli.py:47, 153, 203, 278, 323, 493,
  673, 847, 1117 against the nine `@exp_app.command` declarations). Arm C rows are identified by
  `source = 'exp_observer'` with no `arm_id` (D27), and `exp decide` filters on it.
- **Verification rows (T9).** Every table, column and index named in the new verify.md block exists
  at 949676b (`exp_veto_coverage`'s shipped shape including `sport` and the `left join games`;
  `ix_veto_decisions_decided`; `ix_metric_samples_name_ts`; the four `ix_exp_*`). No pre-existing
  expectation is loosened - the block states in three places that the 6D line and every earlier
  threshold, window and cadence stand word for word, and it explicitly declines to claim the
  file-tree re-hash that does not exist yet (M19).
- **Tests.** Across the 24 new/changed test files there is no `xfail`, no `time.sleep`, no bare
  `print`, no outbound call and no read under `secrets/`. The only `skip` in new code is
  `tests/test_exp_isolation.py:74`, the ruled role-creation case, whose fail-closed companion runs
  unconditionally. `tests/test_alembic.py:69`'s skip is pre-existing.

## Important

**I-1. Two of §1.8(e)'s three validity windows can never fire in production.**
`harness/research/features.py:72` declares `VALIDITY_WINDOWS = {espn_status: 900 s, weather: 3600 s,
fair_move: 21600 s}`, and `harness/research/veto.py:459-468` measures the age between the trigger
signal's `created_at` and the deciding signal's `created_at`. But the trigger context lives only
inside **one claimed `veto_queue` bucket**: `veto_pass` resets `trigger_features`/`trigger_as_of` at
the top of every pass, `claim_bucket` claims a single `(game_id, market_type, bucket_start)` bucket,
and a bucket is `Settings.veto_bucket_minutes = 30` minutes wide
(`harness/config/settings.py:184`, `harness/execution/store.py:269`). The maximum age the pure
function is ever asked about in production is therefore < 1800 s, so `weather` (3600 s) and
`fair_move` (21600 s) are unreachable and only `espn_status` can ever expire. The spec requires "an
explicit validity **window** per key" (§1.8(e)); two thirds of that is delivered as declared
constants and dead labels. The new tests exercise `invalidated` directly with hand-chosen
timestamps, so none of them would notice. Fix is a choice for the user/loop - either bound the
windows by the reachable cache lifetime and say so, or carry the trigger context across buckets -
but the branch should not be read as having shipped per-key expiry for `weather` and `fair_move`.

**I-2. The T6b change is live, shifts the decided population from the release instant, and no
boundary is recorded.** Unlike T6's claim order and reservation (both behind
`veto_pacing_profile`), `invalidated(..., cached_at=..., as_of=...)` is called unconditionally
(`harness/research/veto.py:464`). Within a 30-minute bucket an `espn_status` expiry now forces a
second paired call where the first decision used to be inherited from cache, and
`trigger_as_of` then resets - so a bucket can cost up to twice what it cost before. The $25/day cap
is untouched and binds before 09:00 CT every day (§0's live facts), so the effect is not more spend
but **fewer buckets decided per day and a different time-to-kickoff mix**, from the deploy instant.
§1.8(f) and §0.8 require the changed population and the boundary instant to be printed beside any
pre/post veto comparison; verify.md's new row 4 records a boundary only for the pacing-profile
amendment ("a `daily_reserved` row with a `None` profile is a FAIL"), `decision.py`'s veto section
cites the profile preflight, and nothing anywhere names the release instant of the validity-window
change as a population boundary for H9. Cheapest fix: one journaled boundary instant at the release
carrying this diff, plus a sentence in the decision report's veto section and in verify.md row 4.

## Minor

**M-a. `exp run`'s "orders" summary line reports one chunk, not the run.** `cli.py` prints
`chunks[-1].orders`, which is `len(order_ids)` for the **last** chunk only (closed orders are popped
from `runner.orders` at every chunk boundary, by design, ruling D23/I3). A window that ran twelve
chunks prints the last chunk's handful. `exp report` gives the real numbers, so this is a
presentation defect in the operator line, not in the data.

**M-b. `_ARM_ORDERS`' correlated fill lookup rides no index.** `cli.py:_ARM_ORDERS` computes each
order's entry instant with `(select min(f.filled_at) from exp_fill f where f.run_id = ... and
f.arm_id = ... and f.exp_order_id = o.id)`. `ix_exp_fill_trade` leads on `(run_id, arm_id,
source_trade_id)`, so the subquery re-walks the arm's whole fill range once per order - ~41,000
times on a three-day run - under the source session's 25 s `statement_timeout`. The chunk work is
already committed, so the worst case is the outcome step failing and being re-run, but a grouped
join (`left join (select exp_order_id, min(filled_at) ... group by 1)`) costs nothing and removes
the cliff. Related to M18's timeout concern but a different statement.

**M-c. `docs/runbooks/experiments.md` §3 mislabels the pre-grant error.** It says "Before the grant
the expected result is a non-zero exit with `IsolationError: the experiment secret ... is absent or
empty`". That is the pre-**secret** error. With the secret placed and the role not yet created, the
failure is a connection error for `role "harness_exp" does not exist` (which verify.md row 2 gets
right: "every name errors with `role "harness_exp" does not exist`"). One sentence.

**M-d. `EXP_LABEL` as printed by a command carries no run/arm/manifest.** `__init__.py:14` sets
`EXP_LABEL = COUNTERFACTUAL_LABEL`, and §1.9(e) defines `EXP_LABEL = COUNTERFACTUAL_LABEL + " run=…
arm=… manifest=…"`. The identified form exists as `exp_label(run_id, arm_id, manifest_hash)` and is
used on every exploratory table cell, which is what §1.9(e) is for; the §0.6 first-line print is the
bare label. Defensible, and the tests pin it, but the two names do not mean what the addendum says
they mean - worth one line in the journal's spec-drift list rather than a code change.

**M-e. `veto.py`'s `_SIGNALS` `left join games` is unconditional.** The join and the extra
`kickoff_utc` column are on the live path whatever `veto_pacing_profile` says (ruling I3 authorised
the additive field). It is a left join on `games`' primary key, one row per queue row, with the
`order by s.created_at, s.id` unchanged, so no row, order or existing column moves; noted only
because the plan's "bit-identical live path" bullet reads as if T6 touched nothing while dormant.

## Carry-forward confirmations (M1-M22)

I re-read every item against 949676b and found **none worse than described**. The five whose
disposition is "no code change" are true as stated:

- **M10** (re-explain the chunk query on real data): a controller/user duty after release. Every
  statement in `capture.py`, `cli.py` and `outcomes.py` that I read carries a comment naming the
  index it rides or stating that the table is small; `_ORDER_INSTANTS`/`_VERSION_SPAN` carry the
  `variant_id` predicate `ix_orders_key_placed` needs (D14), `_EVENT_INSTANTS` is bounded by the
  order ids (I5), `_SUBSCRIPTION_SIDS` and `_COUNT_SQL` are window-bounded on `ix_obe_ticker_ts`.
  Confirmed.
- **M12** (readers join on the surrogate `exp_order.id`): `_REPORT_OUTCOMES`,
  `_REPORT_OUTCOME_VALUES`, `_REPORT_GAME_SHARE`, `_ARM_ORDERS` and `adapter.run_chunk`'s fill
  write all use `exp_order.id`; `arm_order_id` appears only as the upsert key. No change needed.
- **M13** (unknown close -> permanent censor): still true; `outcomes.MISSING_REASONS` is the three
  §1.9 spellings and `_row` censors a null `close_at`. The report names it for the user; a fourth
  reason is the user's.
- **M15** (observer reads on the worker's `harness` session, writes only as `harness_exp`): true as
  described, and stated in `observer.py`'s module docstring.
- **M18** (`exp decide`'s all-time window): the decide queries I read are bounded by `run_id`;
  verify.md row 5 and the T9 row bound the observer reads by run. Confirmed; see M-b for a
  different statement with the same shape of risk.
- **M19** (no command re-hashes the capture tree) and **M20** (no `exp_observation` index) are both
  still open exactly as recorded, and verify.md row 1 and row 5 say so in the text rather than
  claiming the behaviour. Good.
- **M21**'s residual (a censored `exp_outcome` row is never re-observed when its horizon matures) is
  confirmed present: `_RECORDED_OUTCOMES` skips any `(exp_order_id, horizon)` pair that exists,
  censored or not.

## Spec defects (for the user)

1. **§2's `exp_limitation` invariant has a clause that cannot fire.** `scope` is declared
   `nullable=False` in both the model and migration 0015, so `or scope is null` is a schema guard,
   not a live predicate. Already on the record; verify.md's row 1 comment says so. No code change.
2. **§3 row 2's privilege array names `positions`, which is a view** (`schema.py`'s
   `_POSITIONS_VIEW`), while `exp isolation-check` names `ledger` (D1). T9 carries both; the
   addendum text is the erratum.
3. **§1.9(e)'s `EXP_LABEL` and the code's `EXP_LABEL` are different strings** (see M-d): the code
   splits the addendum's one name into `EXP_LABEL` (the §0.6 banner) and `exp_label(...)` (the §1.9e
   cell label). Both requirements are met; the naming is drift.
4. **§1.8(e) is unimplementable as written for two of its three keys** given
   `veto_bucket_minutes = 30` (finding I-1). Either the windows or the cache lifetime has to move,
   and which one is a design decision above this review.
5. **§0.8's amendment record has no counterpart for the §1.8(e) change** (finding I-2). §0.8 binds
   the *pacing profile's* activation to a dated user decision and a recorded boundary; §1.8(e) makes
   a live sampling change with neither. If the user intends §0.8's boundary discipline to cover any
   change to which signals get a fresh call, §0.8 should say so and the 6D.1 release instant should
   be journaled as one.
6. Previously recorded and re-confirmed: D29's narrower corpus claim, D33's re-serialised
   observation body against §2's "raw response", and T3's `exp_order` column drift (`arm_order_id`,
   surrogate `id`) against §2's declared shape.

## Passes made

1. Spec addendum rev 3: §0 in full, §1.1-§1.2, §1.8-§1.10, §2, §3, §4 in full, §9 headings
   (skimmed §5-§7); plan `## Global Constraints`, `## What this plan does not do`, `## Rulings`;
   ledger rulings D1-D46 (`grep -n 'Ruling D'`); `final-review-carryforwards.md` M1-M22.
2. Whole-branch invariant sweeps: production-import grep, EXECUTOR/PRICING version grep, untouched
   file `git diff --stat`, raw-SQL-write grep, `EXP_LABEL` placement grep against the command list.
3. Package pass A: `__init__.py`, `source.py`, `storage.py`, `manifest.py` (full text).
4. Package pass B: `arms.py`, `liquidity.py`, `episodes.py`, `report.py`, `outcomes.py` (full text).
5. Package pass C: `adapter.py` (interface scan plus `run_chunk`/`run_window` in full), `capture.py`
   (docstring, constants, every statement and its bound), `cli.py` (command list, the three
   label-first bodies, `_record_run_outcomes` and the report/decide statement block).
6. Production-edit pass: `harness/execution/policy.py`, `plan.py`, `harness/config/settings.py`,
   `harness/db/schema.py`, `migrate.py`, `models.py` (exp declarations), `docker-compose.yml`,
   `harness/cli.py`, `harness/research/worker.py`, `veto.py`, `spend.py`, `pacing.py` (full),
   `features.py`; migration 0015 in full.
7. Docs/ops pass: the whole new verify.md block and window table; `docs/runbooks/experiments.md`.
8. Tests pass: hygiene sweep (skip/xfail/sleep/print) across all 24 new/changed test files;
   `tests/test_exp_isolation.py` and the `tests/test_veto_features.py` diff in full.

**Not covered in depth, and left for the controller if it wants another pass:** `baseline.py`,
`bookhealth.py`, `forecast.py`, `veto_profile.py` and `decision.py` were scanned by interface and
docstring but not line-read; `observer.py` was read by grep of its guards and statements, not in
full; the bodies of `cli.py`'s `book-health`, `veto-profile`, `capture` and `observe` commands; the
test bodies other than isolation and features; the `tests/test_alembic.py` and `tests/test_compose.py`
diffs (the catalogue-diff test is the evidence that models and migration agree, and the controller
ran the suite on every candidate). Each of those had a task review and at least one scoped
re-review, and nothing I did read suggested a systematic problem in them.

## Verdicts

**Spec compliance of the branch: compliant, with two spec defects the code cannot resolve on its
own.** Every §0 invariant and every roadmap gate I could check holds - isolation, additivity,
dormancy, unchanged caps/cadences/hosts/dependencies, no registered surface touched, no adoption and
no activation. The two Important findings are both §1.8(e): the spec's per-key validity window is
delivered for one of three keys because the cache lifetime the spec did not name is 30 minutes
(I-1), and the change ships live without the boundary record §1.8(f)/§0.8 would want beside it
(I-2). Neither breaches §0; both need a ruling, not a quiet fix.

**Branch quality: high.** 56 files and ~12,800 insertions with a legible module boundary, a
privilege boundary that does not depend on its own assertions, statements that carry their bounds
and index names, checkpoints that commit per chunk, and a verification block that documents what is
*not* implemented (M19, M20, the quiet window) instead of claiming it. The defects I found are one
mis-scoped constant, one unrecorded measurement boundary, one misreported counter, one unindexed
correlated subquery and one runbook sentence - none of them structural.
