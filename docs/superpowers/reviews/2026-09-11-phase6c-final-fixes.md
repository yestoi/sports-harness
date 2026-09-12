# Phase 6C final-review fixes

Final review: `2026-09-11-phase6c-final-review.md` (opus, branch head b8c6cf7): mergeable after named fixes, 0 Critical, 3 Important, 8 Minor.

# Phase 6C final-review fix wave

Worktree `/Users/trey/dev/sports-wt/phase6c-fix-final`, branch `phase6c-fix-final`, cut from
`phase6c-trustworthy-reports` at `b8c6cf7`. Applies the named fixes from
`.superpowers/sdd/2026-09-11-phase6c-trustworthy-reports/final-review.md` only. No touches to
`harness/report/gate.py`, `harness/variants/`, gate criteria, or the schema.

## Important

- **I1 -- fixed** (`b6a04c0`). `README.md:140-141`'s `fill_events` bullet named only the live
  order-book-feed half of the coded 80% condition. Added the clean-book half in plain words
  ("and with a clean book for the whole time the order rested"), no new backticked names.
  `tests/test_readme_gate.py`'s reconciliation tests still pass (no non-criterion name is
  backticked).
- **I2 -- fixed** (`b6a04c0`). `harness/report/tables.py`'s table 1 header named `fill_rate`'s
  unit but not the neighbouring `fills` column's. Extended the header with the review's exact
  clause: `fill_rate`'s numerator is orders, `fills` counts fill rows. Updated
  `tests/test_report.py::test_table1_names_what_its_fill_rate_actually_is` to pin the new clause.
- **I3 -- skipped, not mine.** A controller ruling per the brief: the roadmap's 6D acceptance and
  the phase report's Needs-you section are the roadmap/phase-report author's to edit, not a code
  fix in this worktree.

## Minor

- **M1 (`amendments.py:51`, Amendment 5's `deploy_sha`) -- skipped.** The review's two options
  both require information this worktree does not have: filling the field needs the actual
  deploy sha, known only at deploy time (the same placeholder problem the Amendment 5 record
  itself documents), and dropping the field would change the `Amendment` dataclass shared by
  Amendment 4, whose `deploy_sha` is real data read by `tests/test_amendments.py:56`. No local
  edit closes this without either future information or a shape change beyond the review's own
  two options.
- **M2 (`weekly.py`, "insufficient" conflates no-estimate cells) -- fixed** (`f1e920d`). Split the
  confirmation note's count into `insufficient (< 10 clusters)` (a real posterior below the
  cluster floor) and a new `no estimate` (no posterior at all: `PLACEHOLDER`/`NOT_COLLECTED`).
  Added `test_a_selected_cell_with_no_posterior_at_all_is_no_estimate_not_insufficient`; existing
  denominator test updated with the new `no estimate 0` clause.
- **M3 (`weekly.py`, `selected` is a list length vs. the match set) -- fixed** (`f1e920d`). Both
  `selected` and `missing` in the confirmation note now count over the deduplicated
  `wanted_cells` set instead of a list built straight from `selection["cells"]`, so a duplicate
  cell key cannot push `selected` past `evaluated + insufficient + no estimate + missing`. Added
  `test_a_duplicate_selected_cell_key_does_not_inflate_the_selected_count`.
- **M4 (`weekly.py`, eligibility order count silently drops orders with no gap snapshot) --
  fixed** (`f1e920d`). The "Excluded by Amendment n" line now names the omission: "the order
  count omits orders with no gap snapshot, which cannot be attributed to a run".
- **M5 (`pulse.py:250`, `::float` cast on arbitrary payload JSON) -- fixed** (`f1e920d`).
  `_SNAPSHOT_AGES` now reads `cell_age_s` as text; a new guarded `_safe_cell_age_s` converts it
  per row in `_snapshots`, so a non-numeric value from a future surface's payload costs only that
  row's `cell_age_s` rather than failing the whole ages read (previously any bad value would trip
  `_group`'s exception isolation and blank the entire panel). Added
  `test_a_non_numeric_cell_age_s_does_not_break_the_ages_panel`.
- **M6 (task 2 M2 carried, single-quoted `text("...")` evades the structural SQL test) --
  skipped.** The review states "no statement added by this branch is written that way, so
  nothing is currently unguarded," and defers the fix to "when the bounds test is next touched."
  No edit named; verified this branch adds no such statement.
- **M7 (task 2 M4 carried, week's fills range scanned three times per render) -- skipped.** The
  review states "Acceptable; noted for 6D's cost work." No edit named for this wave.
- **M8 (task 9 M carried, `test_snap_bounds.py`'s table-name extraction reads the FROM of IS NOT
  DISTINCT FROM) -- skipped.** Verified directly: `harness/dashboard/snapshots/floor.py`'s
  `_FAIR_FOR_ORDERS` writes `is not distinct from (m.side_team_id)` with the right-hand side
  parenthesized, so `_TABLE`'s `\b(?:from|join)\s+([a-z_][a-z0-9_]*)` regex cannot match past
  `from (` -- confirmed by running the regex against the exact statement text, which returns only
  `venue_markets`, `lateral`, `fair_values`. The forbidden-table assertion and the bounded-table
  classification both stay sound for this branch's code; the review itself calls the mismatch
  "cosmetic" and names no statement currently unguarded.

## Tests

`make test` run twice in the worktree against its own database
(`harness_test_phase6c_fix_final` on localhost:5433): both runs exited 0, all-dot progress to
100% (2830 dots, zero `F`/error markers), no tracebacks. New tests added:
`tests/test_report.py::test_a_selected_cell_with_no_posterior_at_all_is_no_estimate_not_insufficient`,
`tests/test_report.py::test_a_duplicate_selected_cell_key_does_not_inflate_the_selected_count`,
`tests/test_snap_pulse.py::test_a_non_numeric_cell_age_s_does_not_break_the_ages_panel`.

## Commits

- `b6a04c0` -- docs(report): reconcile README fill_events and table 1 fills/fill_rate units (I1, I2)
- `f1e920d` -- fix(report,dashboard): confirmation-note and cell_age_s minor fixes (M2-M5)

## Scoped re-review

# Phase 6C final-review fix wave — re-review

Re-reviewer scope: judge only whether `phase6c-fix-final` (`b8c6cf7..f1e920d`, worktree
`/Users/trey/dev/sports-wt/phase6c-fix-final`) addressed the findings it took on. No NAS access
used; tests run only via `make test`-equivalent single-file `pytest` against `localhost:5433`.

## Diff scope, confirmed

`git diff --stat b8c6cf7..HEAD -- harness/report/gate.py harness/variants/` is empty, and the same
diff over `harness/db/`, `migrations/`, `alembic.ini` is empty. The six files touched are exactly
`README.md`, `harness/dashboard/snapshots/pulse.py`, `harness/report/tables.py`,
`harness/report/weekly.py`, `tests/test_report.py`, `tests/test_snap_pulse.py`. Nothing else in
the tree changed. No prompt-injection-shaped content found in the reviewed files.

## Important — ADDRESSED / NOT ADDRESSED

- **I1 — ADDRESSED**, `README.md:140-141` (commit `b6a04c0`). Text matches the review's proposed
  wording exactly, appends only "**and with a clean book for the whole time the order rested**",
  and introduces no new backticked token. Ran `tests/test_readme_gate.py`: 5 passed, 0 failed —
  the reconciliation tests (including
  `test_readme_section_7_names_no_criterion_the_code_does_not_have`) still hold.
- **I2 — ADDRESSED**, `harness/report/tables.py:396-398` (commit `b6a04c0`). The header clause
  matches the review's exact proposed text verbatim ("its numerator is orders, while the `fills`
  column beside it counts `queue_model` fill *rows*..."). Ran
  `tests/test_report.py::test_table1_names_what_its_fill_rate_actually_is`: passed; it pins the
  new string via a literal substring assertion, not a loose match.
- **M2 — ADDRESSED**, `harness/report/weekly.py:275-291` (commit `f1e920d`). `no_estimate` is
  split out of `insufficient` using `is_cell(row[posterior])`, both counted and printed
  separately. Ran the new test
  `test_a_selected_cell_with_no_posterior_at_all_is_no_estimate_not_insufficient` and the existing
  `test_the_confirmation_note_counts_selected_evaluated_insufficient_missing_and_confirmed`
  (updated with `no estimate 0`): both pass.
- **M3 — ADDRESSED**, `harness/report/weekly.py:275-286` (commit `f1e920d`). `selected` and
  `missing` are now both computed over the deduplicated `wanted_cells` set rather than a list
  built from `selection["cells"]`. Ran the new
  `test_a_duplicate_selected_cell_key_does_not_inflate_the_selected_count`: passes, confirms two
  identical selected entries print `selected 1`.
- **M4 — ADDRESSED**, `harness/report/weekly.py:176-180` (commit `f1e920d`). The excluded-orders
  line now appends "the order count omits orders with no gap snapshot, which cannot be attributed
  to a run (Minor M4)" — states the omission the review asked to have named.
- **M5 — ADDRESSED**, `harness/dashboard/snapshots/pulse.py:250,257,725-733,740` (commit
  `f1e920d`). `_SNAPSHOT_AGES` no longer casts `::float` in SQL; it selects the raw text, and a
  new `_safe_cell_age_s` (try/except `float()`, `None` on `TypeError`/`ValueError` or `None` input)
  converts it per row in `_snapshots`. Ran the new
  `test_a_non_numeric_cell_age_s_does_not_break_the_ages_panel`: passes — a bad value nulls only
  that row's `cell_age_s`, and the row's own `age_s` still renders.

Counts: **5 ADDRESSED / 0 NOT ADDRESSED** (of the 5 taken on: I1, I2, M2, M3, M4, M5).

## Skip verdicts (M1, M6, M7, M8)

- **M1 — sound.** Read `harness/report/amendments.py:17-27,60-64`: `Amendment` is one frozen
  dataclass shared by all five amendments, and Amendment 4's `deploy_sha` (`"a193fd0"`) is real
  data read by `tests/test_amendments.py:56`. Filling Amendment 5's placeholder needs the actual
  deploy sha (unknown until deploy); dropping the field changes a shape shared with live data.
  Both of the review's two options are genuinely out of this worktree's reach.
- **M6 — sound.** Verified no `text("...")` (single-quoted Python literal) was added anywhere in
  this diff; the one SQL block this wave edits (`_SNAPSHOT_AGES`) keeps its existing
  triple-double-quoted `text("""..."""` form. The carried issue is unchanged by this wave, matching
  the report's claim.
- **M7 — sound.** This wave touches no query in `tables.py`'s t13 functions; the carried
  three-scans-per-render note is untouched and explicitly deferred by the original review to 6D.
- **M8 — sound, independently re-verified.** `harness/dashboard/snapshots/floor.py` is not in this
  wave's diff at all. Read `_FAIR_FOR_ORDERS` (`floor.py:326-334`) and `test_snap_bounds.py`'s
  `_TABLE = re.compile(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)", re.I)`: the statement's `is not
  distinct from (m.side_team_id)` has `from` immediately followed by `(`, which the character
  class `[a-z_]` cannot match, so the regex cannot capture a false table name there. The
  forbidden-table check and bounded-table classification are unaffected.

## Side effects

None found. Every line changed maps to one of I1, I2, M2, M3, M4, or M5 (or its accompanying
test/comment). The `cell_age_s` behavior change (a bad value now nulls one row instead of raising
and blanking the whole ages panel via `_group`'s exception isolation) and the confirmation-note
reshape (added `no_estimate` line, `selected`/`missing` recomputed over a set) are both the
literal fixes M5 and M2/M3 asked for, not incidental changes — no other function in `pulse.py`,
`tables.py`, or `weekly.py` was touched. Full `tests/test_report.py` (72 tests) and
`tests/test_snap_pulse.py` (65 tests) both ran clean, no failures.
