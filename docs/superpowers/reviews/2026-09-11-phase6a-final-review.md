# Phase 6A final whole-branch review

Branch `phase6a-preserve-and-define`, head **ac4a556**, 23 commits, base **main e67b19d** (merge-base
equals main's tip: fast-forward possible). Reviewer: opus, whole-branch, against the design addendum
revision 2 (`docs/superpowers/specs/2026-09-11-phase6a-preserve-and-define-design.md`), not task by task.

Worktree `/Users/trey/dev/sports-wt/phase6a-preserve-and-define`. Tests run on a separate database
(`scripts/testdb.py probe_final6a`), never the controller's.

## Verdict

**Mergeable after two cheap documentation fixes.** No Critical. Both Important findings are one-line
docs edits that do not touch code and do not need a re-dispatched implementer task; the controller can
take them at merge. Every code-level invariant the milestone rests on is verified below by measurement,
not by reading: `criteria_hash` unchanged, no DDL, no dependency, no variant edit, nothing sets the
eligibility settings, every new statement bounded, exactly six strict xfails with two passing guards,
and the tar stream path exercised end to end.

## Measurements taken during this review

| Check | Result |
|---|---|
| `criteria_hash()` in the worktree | `5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5` — equals the expected pin |
| Hash pinned independently | `tests/test_gate_eligibility.py:26` and `tests/test_readme_gate.py:19`, same literal |
| `harness/variants/`, `pyproject.toml`, `constraints.txt`, `harness/db/schema.py`, `harness/db/models.py`, `alembic/` | zero diff against main |
| DDL / `truncate` / `delete from` in added lines | none |
| `gate_eligible_from_*` set anywhere outside `settings.py` and the CLI read | none (no compose file, no test, no default) |
| Strict xfails in the whole suite | 6, all in `tests/test_execution_regressions.py`, all `raises=AssertionError` |
| `tests/test_execution_regressions.py tests/test_corrections.py tests/test_gate_eligibility.py tests/test_snap_gate.py` | 44 passed, 6 xfailed, no XPASS, no warning |
| `tests/test_capsule.py tests/test_cli.py tests/test_gate.py tests/test_readme_gate.py` | 59 passed, no warning |
| `harness manifest` with no database in the environment | prints `manifest_version 1`, `measurement_version 4.4`, one correction `C0` |
| C0 tuples | 7 variant ids × 12 hex, 6 config hashes × 64 hex — matches the record and verify.md row |
| `--out -` tar path (probe script) | 3 members in order, `manifest.json` last, every `sha256` matches, mode 0644, datetimes stringified, `truncated: ["fills"]` and exit signal correct |
| Marker comments in the nine gate constants | every one on its own line; the tail of each `where` clause survives interpolation, including `_VARIANT_ORDERS` embedded inside `_SETTLEMENT_COVERAGE` |
| One-sided activation (`Eligibility(5, None)`) | `explain` succeeds on `_MISMATCHED` and `_STALENESS`; surplus binds tolerated by `text()` |
| Commit trailers | all 23 commits carry the ledger's two lines |

## Spec coverage

Every addendum section is delivered.

- **0.1 capsule** — `harness/capsule.py`, `harness capsule`. Every selector present: the order chain by
  primary key, the tape through `export_ws_tape`, `venue_trades`, the window's gap rows, the tickers'
  `venue_markets` → `market_gap_snapshots` → `fair_values`, the window's orders/fills/events/ledger,
  `metric_samples` by the twelve-name explicit list, `operator_events`, `exec_heartbeat` and
  `config_history` in full. `CAPSULE_ROW_CAP = 150_000`, `CAPSULE_STATEMENT_TIMEOUT_MS = 60_000`,
  cap reaches every read, truncation marks the file and exits 2, a missing order exits 1.
- **0.2 periods** — five selection queries plus order 157 in `docs/runbooks/capsule.md:44-84`;
  `--period-note` records the query and its result in the manifest; `unverifiable_slices` carries both
  reasons, `gap` (with `sid` and `ts`) and `no anchor`.
- **0.3 correction manifest** — `harness/corrections.py` with `MANIFEST_VERSION`, `measurement_version()`
  read off `harness.execution` at call time, all fourteen `Correction` fields, C0 filled, the mirrored
  record and its heading-parity test, `harness manifest`.
- **0.4 dormant eligibility** — two `None` settings, the pure `eligible_sql`, the `Eligibility` keyword on
  `evaluate_gate`/`evaluate_all`/`render_gate`, `criteria_json["eligibility"]` written only when active,
  the line in `render_gate`. One clause unmet: see Important 1.
- **0.5 regressions** — six strict xfails (1a, 2, 3, 4, 5, 6) and two passing guards (1b, criterion 1).
  Case 1a's docstring names the `tests/test_book.py:84` conflict and explains why the naive repair is
  caught there rather than by 1b (`tests/test_execution_regressions.py:52-57`).
- **0.6 fix 37** — merged and exercised by deploy `bd220b8` (roadmap row 37, journal 123). Precondition met.
- **0.7 identity check** — `--main-sha`, `--healthz-build`, `--worktrees` on the command;
  `identity.build_mismatch` compares against both the container's `build_sha` and the reported
  `/healthz` build; runbook §2 journals all three first.
- **§1.1-1.5 and §3** — delivered; all five verify rows present, plus the invariant in Layer 2b.
  No walkthrough items, as §3 requires.

## Cross-task seams

Checked, all consistent.

- **Capsule (T2) ↔ runbook (T5) ↔ manifest keys.** Every option the runbook quotes exists on the final
  `capsule_cmd` after T3 and T4 also edited `harness/cli.py`. Exit codes 0/1/2 match the code: the exit-1
  paths all raise before anything is written, so the runbook's "nothing was written" is accurate. The
  manifest keys the runbook tells the controller to read (`truncated`, `identity.build_mismatch`,
  `unverifiable_slices`, `counts`, per-file `sha256`) all exist.
- **Logging does not corrupt the tar.** `configure_logging` writes to stderr (`harness/logging_setup.py:36`),
  so `--out -` piped to a file stays a clean archive. Verified by reading the archive back.
- **C0 record ↔ filled tuples.** The record's "seven variant ids" and "six config hashes" match the code
  and the verify.md row exactly.
- **Verify rows (T6) cite what shipped.** The xfail count row says six; six is the whole suite's xfail
  population. The manifest row's expected values match the command's actual output. The invariant query
  matches the condition `evaluate_all` writes under.
- **T1 ↔ T4.** `test_criterion_one_is_satisfiable_by_a_realistic_population` calls `fill_events` with four
  positional arguments; T4's added fifth parameter defaults to `None`, so the guard still exercises the
  dormant path.
- **Three commands coexist.** `gate`, `capsule` and `manifest` all register; `harness --help` lists them.

## R1 and the gates

Clean. `CRITERIA`, the thresholds, families, grid and cut-offs are untouched; `criteria_hash()` computes
the expected value; the markers are SQL comments that change no result. The dormant call path returns the
module `text()` object itself (`gate.py:267-275`), so a default-off evaluation is object-identical, not
merely equivalent. Both eligibility settings default `None` and nothing in the repository, in a compose
file, or in a test sets them. No DDL, no new dependency, nothing under `harness/variants/`, no venue
client opened by either new command.

## Bounded-query safety

Every new statement carries a bound and `limit :cap`, and the 60 s timeout reaches all of them through
`make_engine(..., CAPSULE_STATEMENT_TIMEOUT_MS)` (`harness/db/engine.py:11-14`). Eighteen capsule
statements audited: primary-key or id-list reads for the order chain; `ts`/`created_at`/`placed_at`/
`filled_at` ranges for the window reads; the ticker-plus-window tape reads through the executor's own
indexes; `metric_samples` by an explicit name list, never a LIKE prefix. The gap read is the single
tape read without a ticker, which is exactly the exception the addendum permits, and it rides the `ts`
BRIN. `exec_heartbeat` and `config_history` are the two full reads the addendum names, both still capped.
The gate's eligibility predicates add only `o.id >=` and `s.run_id >=` comparisons on already-bounded
statements.

## Findings

### Critical

None.

### Important

**I1. Addendum §0.4's roadmap clause is not delivered.** §0.4 says switching the settings on is a dated
user decision and that "the roadmap's User-side TODO gains the line". `docs/superpowers/autopilot/roadmap.md:469-487`
carries no such line, and the plan never assigns it to a task (`grep "User-side" ` over the plan returns
nothing). Without it the only record that the switch needs the user is inside the spec and two code
comments, which is precisely the gap the clause exists to close. By the loop's own convention
(`roadmap.md:86`) the controller owns that section, so this is a merge-time edit, not a re-dispatch.

*Fix:* add to the User-side TODOs, before the merge commit:
`- 2026-09-11 (6A, R1): gate measurement boundary. Setting GATE_ELIGIBLE_FROM_ORDER_ID / GATE_ELIGIBLE_FROM_RUN_ID changes which rows every gate criterion sees. The mechanism ships dormant and the loop never sets it; say the word and the date, and the loop records the decision.`

**I2. The runbook's unpack line loses five of the six capsules.** `docs/runbooks/capsule.md:111-112` says
"Unpack on the Mac with `tar -xf`". The archive carries no directory prefix — members are
`orders.jsonl.gz`, `manifest.json` and so on at the root (confirmed by building one). Six capsules
unpacked that way into one directory overwrite each other silently, and the operator would only notice
when the counts looked wrong. `docs/superpowers/autopilot/verify.md:338` and runbook §6 both assume one
directory per capsule (`capsule/*/manifest.json`), so the layout the verification checks is not the
layout the runbook produces.

*Fix:* replace the unpack sentence with an explicit destination, e.g.
`mkdir -p capsule/order-157 && tar -xf /tmp/capsule-order-157.tar -C capsule/order-157`, and say that
each capsule gets its own directory named for its selector.

### Minor

**M1. A documented deviation from §0.1 that is not ledgered.** The addendum asks the order capsule to
carry "the `config_history` row of its `config_hash`". `harness/capsule.py:111-117` deliberately keys that
read by `variant_id` instead, because `config_history.config_hash` holds the 12-hex strategy id while
`orders.config_hash` is the executor's 64-hex sha256, and binding the latter would match nothing for
every capsule ever taken. The reasoning is right and the comment is excellent; the deviation belongs in
the journal as a ruling so a reader of the addendum is not left believing the literal text shipped.

**M2. `--out -` has no automated test, and the manual check predates the rewrite.** Every command in the
runbook uses the tar form; `tests/test_capsule.py` covers only the directory path, and the T2 reviewer's
hand-check of the tar came before commit `08943aa`, which rewrote exactly that streaming logic (review I3).
I exercised it during this review and it is correct: three members in write order, `manifest.json` last,
every `sha256` matching, mode 0644, datetimes stringified. No merge blocker; add the test in 6B.

**M3. One-sided activation silently mixes epochs.** With only `gate_eligible_from_order_id` set,
`Eligibility.params()` supplies a `None` run bound and `eligible_sql` leaves `_STALENESS` unmarked
(`gate.py:280-283`, `gate.py:616-625`), so eleven criteria measure the new period and criterion 8 measures
the whole history. Verified to compile and run. `render_gate` prints `from_run_id:None`, so it is visible
rather than hidden. State it in the correction record before the switch is ever flipped. Matches the
ledger's deferred T4 M2.

**M4. The runbook's exit-code table hard-codes the cap.** `docs/runbooks/capsule.md:120` says "hit the
150,000-row cap"; `--cap` overrides it, and the manifest's `row_cap` is the number actually in force.
Word it as "hit the row cap (`row_cap` in the manifest)".

**M5. Two over-long lines.** `harness/corrections.py:78` is about 250 characters and
`tests/test_execution_regressions.py:239` about 140, against the repo's roughly 95-character convention.
No linter is configured, so this is cosmetic only.

**M6. Both selectors together resolve silently.** `--order` with `--period` takes the order branch
without a word (`harness/cli.py`, the `if order is not None` chain). Harmless, but the runbook's exit-code
table would read better with a line saying the order selector wins.

**M7. One read transaction spans the whole capsule.** The session is never committed between statements,
which is good — every slice sees one consistent snapshot — but it pins autovacuum's xmin horizon for the
capsule's full runtime. Immaterial in a 04:30-08:00 CT quiet window; worth knowing if an extraction ever
runs long.

## Triage of the ledger's deferred minors

| Ledger item | Ruling |
|---|---|
| T2: streaming-order spy covers the directory path only | stays ledgered; folded into M2, and the tar path is verified correct in this review |
| T2: the `--period` error test asserts the exit code, not the message | stays ledgered; the exit code is what the runbook documents |
| T2 M2: in-memory manifest vs written one on a datetime gap entry | stays ledgered; no consumer reads `ts` off the return value, and the written form stringifies correctly (verified) |
| T2 M3: the "small table" claim unmeasured | stays ledgered; the claim is about `ledger`, whose row count is bounded by the fill population, and both reads carry `limit :cap` |
| T2 M4: `merge_slices`' substring test for statement inclusion | stays ledgered; no two capsule statements are substrings of one another |
| T4 M2: a `None` bound when one setting is unset | stays ledgered, but promoted in wording — see M3 above; it must be stated before the switch, not before the merge |
| T4 M3: the explain test's bind filter substring | stays ledgered |

None of the deferred minors must be fixed before merge.

## What the per-task reviews did not reach

The two Important findings are both seam-level and outside any single task's diff: I1 lives in a file no
task edits, and I2 is the T5 runbook read against the T2 archive layout and the T6 verification glob
together. The per-task reviews were correct within their scopes.
