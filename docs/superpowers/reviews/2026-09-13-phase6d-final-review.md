# Phase 6D final whole-branch review (review ID `final-6d`)

- Reviewer: opus, final whole-branch reviewer, 2026-09-14 20:5x-21:2x CT.
- Worktree `/home/trey/dev/sports-wt/phase6d-final-review` at `1442e26` (clean apart from this review's Minor diff).
- Package `/home/trey/dev/sports/.superpowers/sdd/2026-09-13-phase6d-sustained-evaluation/review-final-664fdcf..1442e26.diff`
  (6,940 lines, 16 commits, 52 files changed, +5,529 / -126).
- Test database `harness_test_phase6d_final_review`; two scoped `make test` runs (never a full suite).

## Verdict

**MERGEABLE** (0 Critical, 1 Important, 1 Minor diff of documentation annotations).

The single Important is a documentation defect in `docs/superpowers/autopilot/verify.md`, which is not writable in
this sandbox; the exact replacement text is given below for the controller to apply. It does not touch code,
schema or behaviour and does not block the merge; it does block a correct first 6D verification if applied after
the deploy rather than before.

Counts: Critical 0 / Important 1 / Minor 1 (one unified diff, documentation annotations only, no behaviour change).

## R1 and invariant checks (all pass)

| Check | Result |
|---|---|
| `PYTHONPATH=. .venv/bin/python -c 'from harness.report.gate import criteria_hash; print(criteria_hash())'` | `5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5` (exact match) |
| `harness/variants/` | `git diff --stat 664fdcf..1442e26 -- harness/variants/` is empty |
| `harness/report/gate.py`, `harness/config/settings.py`, `pyproject.toml`, `constraints.txt` | empty diff (no gate criterion, no new dependency, no eligibility setting) |
| Cadence | no `*CADENCE*` constant added, removed or changed; the three diff hits are prose/comment references to the existing `DEFAULT_CADENCE_S = 900` |
| New outbound host / non-GET venue call | none. Every new HTTP line in the diff is a `respx.get(...)` mock inside tests (`tests/test_coverage_samples.py`, `tests/test_coverage_denominator.py`); `harness/venues/kalshi/rfq_socket.py`'s change is a read-side yield guard and a store-rate cap, no frame sent |
| Migration `0009_phase6d_sustained_eval` additive-only | `upgrade()` is four `op.create_table(..., if_not_exists=True)`/`op.create_index(..., if_not_exists=True)` families plus `concurrent_index("ix_intents_created", "intents", ["created_at"])`; `downgrade()` is `pass`. No DROP/RENAME/TRUNCATE/DELETE/ALTER TYPE/backfill anywhere in the file (`migrations/versions/0009_phase6d_sustained_eval.py:57-134`). Renumbering at the main merge is D9's and is documented in the module docstring |
| Worktree clean | `git status --porcelain` empty before the Minor diff; only the addendum is modified now |

## Tests

Scoped runs only, per the brief. `tests/test_coverage.py` does not exist on this branch; substituted the branch's
actual coverage files, `tests/test_coverage_samples.py` and `tests/test_coverage_denominator.py` (stated as the
brief permits).

| Run | Scope | Result |
|---|---|---|
| 1 | `test_coverage_samples.py test_coverage_denominator.py test_funnel_episodes.py test_report_t14.py test_snap_floor.py test_alembic.py test_snap_bounds.py test_tick.py test_policy_compare.py` | **388 passed** in 168.40 s, exit 0, no warning, no xpass |
| 2 | `test_checks.py test_exclusion_classes.py test_latency_decomposition.py test_rfq_listener.py test_rfq_refusal.py test_exec_loop.py test_pipeline_stage_order.py test_report_t7_t10.py test_schema.py test_render_for_model.py` | **358 passed** in 277.32 s, exit 0, no warning, no xpass |

Total **746 passed / 0 failed** across the two scoped sets. Both receipts record
`head/head_after = 1442e264…`, `dirty_before/dirty_after` empty, `release_tree af9804e8…`. The whole-branch
pristine evidence remains the per-task sharded suites in the ledger, the newest being
`t8-full-57d55e1.log` (3,388-class runs, six shards 0); the controller's own unsharded suite at the phase head is
still the merge gate.

## Acceptance (roadmap §6D, verbatim: "Primary/gate completion measured end to end; no silent omissions; current and proposed policies compared on repaired replay"; roadmap.md Pre-loaded decision 4)

| Acceptance item | Verdict | Evidence (file:line) |
|---|---|---|
| Primary/gate completion measured **end to end** — scheduled set recorded by the writer that decided it | **Met** | `harness/ops/coverage.py:153` `record()` (one multi-row insert, validated before the database is touched), `:227` `evaluation_scheduled_rows`, `:237` `evaluation_completion_rows`; the cell is fixed at enumeration and reused at completion (`:209` `evaluation_cells`). Tests: `tests/test_coverage_samples.py:51` (twelve units written *before* the work), `:72` (the same twelve accounted at completion), `:308` (espn `source_state` snapshotted before the fetch, so a fetched family is `scheduled`, never `not_due`) |
| …and **published per variant** for the gate variant and the primary | **Met** | t14 `harness/report/tables.py:2082` (`Table 14 (t14): coverage contract and denominators`), registered after t13 at `:60`/`:66`. Tests: `tests/test_report_t14.py:55` (registration/order), `:67` (completed over scheduled per variant), `:102` (`total_runs`, `non_skipped_runs`, `priced_runs`, and the sentence naming `priced_runs` as the share's denominator), `:131` (says so when the run cap bound rather than printing a share it cannot compute). Verify row 1 and row 6 in `docs/superpowers/autopilot/verify.md:363,368` |
| **No silent omissions** — every scheduled cell closed or explained | **Met** | Reconciliation query transcribed verbatim into `verify.md:364`; proven both directions: `tests/test_coverage_samples.py:107` (no rows when every cell is closed) and `:123` (a pricing block that raised leaves its scheduled rows **unclosed**, i.e. the query finds the real omission). Durability: `:357`, `:431` (scheduled rows survive a rollback of the work that followed, in both the pipeline and the recorder) |
| …caps and failures are visible, never silent | **Met** | `coverage.py:175-186` writes a `truncated` row plus a `coverage.truncated` metric when `COVERAGE_ROW_CAP = 3072` binds (`tests/test_coverage_samples.py:189`); `harness/ops/episodes.py:53-86` does the same with `EPISODE_UPSERT_CAP = 2048` (`tests/test_funnel_episodes.py:107`). The class map is exhaustive by test (`tests/test_coverage_samples.py:182`, `tests/test_exclusion_classes.py:85`) and an unclassified outcome is refused before any write (`coverage.py:139` `_validate`, `tests/test_coverage_samples.py:159`, `:169`, `:243`) |
| …missingness quantified and classified | **Met** | `harness/ops/exclusions.py` (four exclusion classes, six coverage classes); `tests/test_exclusion_classes.py:22`, `:36`, `:52`, `:64`, `:85`. Verify row 3 (`verify.md:365`) journals the outcome histogram beside each class |
| **Current and proposed policies compared on repaired replay** — the comparison *harness* | **Met (harness); the run is pending 6B, named as pending, not unmet** | `harness/execution/policy.py:372` `compare()` over the tape with `rest_to_expiry`, stale allowance, near-kickoff and fillability branches; `PolicyResult` at `:237`; `BASELINE_RECORD` lazily built at `:217`; `COUNTERFACTUAL_LABEL` at `:39` on every row and on the rendered table (`:548`); `NOT_EXERCISED`/`NOT_EXERCISED_NOTE` at `:86`/`:89` disclose the two alternatives this harness cannot move; `ORDER_SCAN_CAP = 20_000` at `:278` with a log when it binds (`:532`). CLI `harness policy-compare` at `harness/cli.py` (`--from-run --to-run --variant --policies --out -`). Tests: `tests/test_policy_compare.py:86` (the baseline changes nothing about the live path), `:222` (every alternative registered, none adopted), `:233` (every output labelled counterfactual), `:275` (the comparison never writes a row), `:340`, `:415`, `:435`, `:447`, `:477`, `:497`. The live path stays bit-identical: `harness/execution/plan.py` takes `policy: HoldingPolicy = BASELINE` defaulted arguments only |
| Pre-loaded decision 4 bullet 2 (fix 48's closure, budget isolation) | **Met** | `harness/strategy/pipeline.py` stage units / `remaining_ms` / `cause`; direct-only variants complete without re-scoring and record `rescore_suppressed`; `tests/test_pipeline_stage_order.py` (D12's amended parity contract, 16 cases green), `tests/test_coverage_samples.py:395` (a suppressed direct-only variant still closes its coverage units) |
| Pre-loaded decision 4 bullet 3 (missing-stage denominator, normalizer evidence) | **Met** | `harness/ops/coverage.py:272` `eligible_runs`, `:287` `exhaustion_share`, `COVERAGE_RUN_CAP = 25_000` at `:75`; `harness/normalize/runner.py` backlog samples; `docs/superpowers/plans/2026-09-13-normalizer-split-proposal.md`. Tests: `tests/test_coverage_denominator.py:35`, `:47`, `:59`, `:84` (cap-then-filter, no predicate on `runs.started_at`, ruling I5), `:93`, `:111`, `:148`, `:171` (prints its plan), `:188` |
| 6C's two deferred funnel units delivered as episodes | **Met** | `harness/ops/episodes.py:40` `upsert` (one read, one upsert, cap-bounded, reading on `ended_at`); Floor keys at `harness/dashboard/snapshots/floor.py:632`, `:634`, `:657`. Tests: `tests/test_funnel_episodes.py:26`, `:48` (six continuous hours are **one** episode — the T8 fix round's red-then-green case), `:76`, `:85`, `:118`, `:131` (§2 invariants return zero), `:143`, `:172`, `:190` (a replay executor writes none); `tests/test_snap_floor.py:1068`, `:1105`, `:1137` (the old funnel keys stay for one release) |
| Carried fix 46 (RFQ listener) | **Met** | `harness/venues/kalshi/rfq_socket.py` `RFQ_STORE_RATE_MAX = 60`, `_yielding()` with a polled heartbeat read and a no-heartbeat-is-not-a-yield rule; `rfq.stored_rows` / `rfq.yielded` metrics. Tests: `tests/test_rfq_listener.py` (89 cases green in run 2) |
| Carried fix 51 (checks bounded) | **Met** | `harness/ops/checks.py` `trades_partition()` / `current_trades_partition()`, `DUPLICATE_TRADES_WINDOW_H = 25` over the weekly partitions the window touches, with the early-week previous-partition case; `ix_intents_created` in the revision. Tests: `tests/test_checks.py` (new cases green, incl. the `EXPLAIN` read-back case) |
| Latency decomposition (decision 4 bullet 1) | **Met** | `harness/recorder/tick.py:281` `tape_covered_frac`, `:336` `recorder.phase_ms`, `harness/execution/loop.py:403` `exec.signal_to_order_ms` (p50 per variant), `fair_age_s` bounded to a 10,000-entry deque. Tests: `tests/test_latency_decomposition.py:20`, `:38`, `:77`, `:95`, `:115` (no new read touches a tape table), `:128` |
| The comparison **run** and any policy adoption | **Pending, not unmet** | Outside the plan by its own Global Constraints and addendum M6: designed and tested on fixtures, never run on the live tape; adoption is the user's dated decision (§0.15a). Blocked on 6B's merge (repaired replay). `tests/test_policy_compare.py:222` asserts none is adopted |

## Disposition of the inputs index

Every carried item, documented limit and ruling in `final-review-inputs.md` was read in the ledger
(`progress.md`) at the line numbers given. Dispositions:

| Item (ledger line) | Disposition |
|---|---|
| Rulings 6, 7 (deploy from main; commit trailers; workers never commit) | Procedure; honoured — this review returns a diff and commits nothing |
| Ruling 8 / 31 (migration id and number) | **Confirmed on the branch**: the file is `0009_phase6d_sustained_eval` (27 chars) on `0008_positions_open_fill`, with the 33-char truncation reason in the module docstring. Renumbering at the main merge is the controller's (D9). One documentation consequence is the Important below |
| Ruling 9 (worker can run `test_alembic.py` scratch cases) | Confirmed: 78 `test_alembic.py` cases green in run 1 |
| Ruling 10 (Qwen not activated; comparison never run live) | Confirmed: no Qwen path in the diff; `policy.py`'s docstrings and `tests/test_policy_compare.py:275` hold the never-writes rule |
| Ruling 32 (verify.md's `duplicate_trades` wording is T10's) | Confirmed: `verify.md:167` and `:203` carry the amended wording, and the 6D block row 8 at `verify.md:370` |
| Ruling 33 (two out-of-list T1 edits) | Confirmed behaviour-neutral: `tests/test_schema.py`'s pinned concurrent-index name set now includes `ix_intents_created`; the revision docstring's "re-numbers" wording does not trip `FORBIDDEN` |
| Note 34 (`TEST_ARGS` splits on spaces) | Honoured: both runs select by node id, no `-k` |
| Ruling 41 (`ROUND_HALF_UP` exemption) | Confirmed by exact name in `tests/test_exclusion_classes.py:22`; a real unclassified reason still fails |
| Rulings 48, 49, 50 (cardinality 2,352 vs the plan's 2,688; the espn state snapshot; fetch-phase counters) | Confirmed: `tests/test_coverage_samples.py:283` asserts the real bound and the cap 3,072 is unchanged; `:308` is the snapshot regression case |
| Carried 51 (`http_error` per family; a raising ticker still counted completed; case-count wording) | Documented limits, unchanged and visible in `runs.notes`; not silent (the reconciliation query and the outcome histogram both see them) |
| Rulings 56, 58 (T9 Critical + I1-I4) | Confirmed fixed on the branch: `clean_resting_seconds` sums each order once (`tests/test_policy_compare.py:415`, `:435`), `rest_to_expiry` below the kill switch (`:447`), inert rows labelled `NOT_EXERCISED` (`:477`), the tape read once per instant (`:497`), `ORDER_SCAN_CAP` on `ix_orders_key_placed` with no new index on `orders` |
| Rulings 57, 59 (T4 I1/I2) | Confirmed: the caller's flush sits **outside** `begin_nested` with the reason in the code (`harness/ops/coverage.py:186-194`), and `tests/test_coverage_samples.py:253` pins the failure direction |
| Rulings 65, 73 (load-shaped suite failures, no fix round) | Accepted; both re-runs green in the ledger |
| Rulings 68, 69 (T5: suppressed rows record `no_fair`/`completed`; cause literal `"budget"`; `variant_ms` = scored pass only; two measurement boundaries) | Confirmed in `pipeline.py` and carried into t14's note (`tests/test_report_t14.py:201`) and verify row 5 (`verify.md:367`) |
| Ruling 76 / carried 80 (T6 fixture, `ctx.get('now')` guard, I4 carried to T10) | Confirmed: the hourly guard is a module statement; I4 is discharged by verify row 12's exemption clause and by this review's addendum annotation |
| Carried 85, 88, 92 (T7 deviations, the missing-stage denominator mutation case) | Confirmed: `tests/test_coverage_denominator.py:59` is the additive case the reviewer asked for (the share is not over `non_skipped_runs`) |
| Carried 97, 98, 99 (T8 Important 1) | Confirmed fixed: the upsert reads on `ended_at` with one plain `ended_at` index per table in both catalogues, and `tests/test_funnel_episodes.py:48` is the red-then-green six-hour case |
| Documented limit: `episodes.truncated` carries source `"recorder"` even for `kind="intent"` (an executor write) | **Stands as a documented limit.** `harness/ops/episodes.py:86`. It is a label on a bounded-cap metric, not an accounting error: the value (keys dropped) is correct and the table is identified by the metric's own labels. No change asked |
| Documented limit: `coverage.record` swallows a failure of its own two statements and returns 0 | **Stands.** It logs `log.exception("coverage.record failed …")` (`coverage.py:204`) and the caller's own pending failure is deliberately *not* swallowed (`:186-194`, `tests/test_coverage_samples.py:214`, `:253`). A swallowed coverage failure shows up as an unclosed scheduled cell in verify row 2, so it is not silent |
| Standing note: addendum §3 row 7 / row 12 text defects | **Confirmed and discharged.** The verify.md clarification at `1442e26` is correct against the code: Floor emits a single shared `episode_gap_rule_s` (`harness/dashboard/snapshots/floor.py:657`) and its funnel window is 6 h (`floor.py:88` `FUNNEL_WINDOW = WINDOW_6H`); `exec.signal_to_order_ms` is written only on a placement (`harness/execution/loop.py:403`, `:1132`); `ws.tape_covered_frac` is written for the previous whole hour with a NULL-minute-is-clean rule (`harness/recorder/tick.py:281-331`). The addendum annotation is this review's Minor diff |
| Ruling 104 / D9 (renumber at the main merge) | Open by design; the controller renumbers to `0012` after 6B's `0011`, rewriting `down_revision` and `HEAD_REVISION` only |

## Important 1 — `verify.md`'s 6D `alembic_version` row names a revision id that cannot exist

`docs/superpowers/autopilot/verify.md:380` reads:

```
| `alembic_version` (6D) | reads `0009_phase6d_sustained_evaluation` — or whatever number the controller assigned at merge, which the deploy journal line states (D10). |
```

The revision on this branch is `0009_phase6d_sustained_eval` (27 characters). The long form is 33 characters and
cannot be stamped at all: `alembic_version.version_num` is `String(32)` and widening it would be a non-additive
ALTER, which is exactly why T1 shortened the id (ruling, ledger line 31, and the revision's own docstring at
`migrations/versions/0009_phase6d_sustained_eval.py:20-30`). As written, the first 6D verification compares the
stamp against a string that can never appear and would read as a FAIL.

`docs/superpowers/autopilot/` is not writable in this sandbox, so the controller applies this. Suggested
replacement, keeping D10's renumbering escape:

```
| `alembic_version` (6D) | reads `0009_phase6d_sustained_eval` — the id is deliberately 27 characters, not `..._sustained_evaluation` (33), because `alembic_version.version_num` is `String(32)` and cannot be widened additively (T1 ruling; the revision's own docstring). Whatever number the controller assigns at merge keeps the `_eval` stem, and the deploy journal line states it (D10). |
```

Severity Important rather than Critical: no code, schema, accounting or acceptance item is affected, and the row
is self-limiting ("or whatever number the controller assigned at merge"). It should be applied before the 6D
deploy, not necessarily before the merge.

## Minor diff (one unified diff, documentation annotations only, no behaviour change)

Written into the worktree and saved at
`/home/trey/dev/sports/.superpowers/sdd/results/6d-final-review-minor.diff` (41 lines, one file:
`docs/superpowers/specs/2026-09-13-phase6d-sustained-evaluation-design.md`). Three annotations, all bracketed and
dated so the original text is preserved:

1. **§3 row 7** — "their `gap_rule_s`" is one *shared* `episode_gap_rule_s` key for both tables
   (`floor.py:657`), and Floor's funnel counts run over its 6 h `FUNNEL_WINDOW` (`floor.py:88`), so the row's 24 h
   query is a standalone integrity read, not the number on the page (Task 10 review I1, clarified in verify.md at
   `1442e26`; this review confirms the clarification against the code).
2. **§3 row 12** — the three freshness exemptions: `exec.signal_to_order_ms` (present-since-the-last-placement,
   labels `{"variant", "q": "p50"}`, Task 6 review I4), `ws.tape_covered_frac` (the hour after the hour it
   measures; a NULL `ws.gaps` minute counts as clean, Task 6 M4/M5), `coverage.truncated` expected absent, and
   `rfq.*` deferred while the listener is off (Task 10 review I2).
3. **§8 D10** — a footnote under the decisions table recording that the revision as built is
   `0009_phase6d_sustained_eval`, with the `String(32)` reason, and that any merge-time renumbering keeps the
   `_eval` stem. This is the addendum-side counterpart of Important 1.

The footnote is placed **after** the D1-D12 table rather than inside it so the markdown table is not broken.

## Instruction-like text seen in data

None that asked me to act. The diff, the ledger and the addendum contain imperative prose addressed to workers
("Read the addendum section your task names before writing a line of code", "stop and report it", deploy recipes
with `make deploy-omarchy` and `sports-compose` command lines, and `.env` flag instructions). All of it is
documentation of the controller's and the user's procedures, quoted inside `verify.md`, the plan and the addendum;
I executed none of it. No production, NAS, ssh, scp, docker or deploy command was run. The only commands executed
were `git` reads, `sed`/`grep`/`cat` reads, two scoped `make test` runs, the `criteria_hash` one-liner and the
annotation script.

## Controller's next action

1. Commit this review's Minor diff (the addendum annotation) —
   `/home/trey/dev/sports/.superpowers/sdd/results/6d-final-review-minor.diff`, one file, 16 insertions.
2. Apply Important 1 to `docs/superpowers/autopilot/verify.md:380` (controller-owned file) with the replacement
   text above, before the 6D deploy.
3. Run the unsharded whole-branch suite at the phase head and merge `phase6d-sustained-evaluation` into main,
   renumbering `0009_phase6d_sustained_eval` per D9 (`0012` after 6B's `0011`; rewrite `down_revision` and
   `harness/db/migrate.py`'s `HEAD_REVISION`, and nothing else) and updating the `alembic_version` verify row to
   the assigned number, keeping the `_eval` stem.
4. Carry forward as named-pending, not unmet: the policy **comparison run** and any adoption (blocked on 6B's
   merge for the repaired replay; the user's dated decision), and the ops read-backs of §4 that only the first
   post-deploy game window and the first Saturday slate can answer.
