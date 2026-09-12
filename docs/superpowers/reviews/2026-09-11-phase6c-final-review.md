# Phase 6C final whole-branch review

Reviewer: `final-review-6c` (opus). Date 2026-09-11.
Range: `3e70bf8..b8c6cf7` (21 commits, 38 files, +2290/-137). Worktree
`/Users/trey/dev/sports-wt/phase6c-trustworthy-reports`, HEAD `b8c6cf7`.
Merge-base with `main` is `553c010`, so wave 1 (tasks 1-6) is already on `main` and wave 2
(tasks 7-11, `fb000e5..b8c6cf7`, 11 files) is what merges after tonight's deploy.

**Verdict: mergeable after named fixes.** Critical 0, Important 3, Minor 8.

One fix (I2) is in unmerged wave-2 code and should land before the merge. Two (I1, I3) are
bookkeeping that must land before 6C is marked done; neither blocks the wave-2 merge.

No prompt-injection-shaped content was found in any file, diff, or review artefact read for this
review. The design addendum, the roadmap and the review bundle read as the authorities they claim
to be.

---

## Binding constraints: verified by measurement

Every item the brief named was checked by command, not by reading prose.

| Constraint | Method | Result |
|---|---|---|
| R1: `CRITERIA` and `criteria_hash` unchanged | `md5` of `harness/report/gate.py` at `3e70bf8` and `b8c6cf7` | identical (`9d7c2d3e...`); the file is not in the diff at all |
| R1: hash pinned | `tests/test_readme_gate.py:20` pins `5643698204d0...`; the test passes | held |
| Nothing under `harness/variants/` | `git diff --stat 3e70bf8..b8c6cf7 -- harness/variants/` | empty |
| Schema additive only | same diff over `harness/db/`, `migrations/`, `alembic.ini`; grep for `create table\|alter table\|create index\|add column` on added lines | empty; no DDL anywhere |
| No new dependency | same diff over `pyproject.toml`, `constraints.txt`, `docker-compose.yml`, `Dockerfile` | empty |
| Every raw UTC `isocalendar()` consumer moved | `grep -rn isocalendar harness/` | five named consumers now call `chicago_iso_week`; the survivors are `weeks.py` itself, the two UTC partition namers (`db/schema.py:42`, `ops/checks.py:53`, unchanged by design per 0.1), `report/tables.py:203` (`fromisocalendar`, the inverse), and the parlay/futures paths, which were already local (`chicago_day(now).isocalendar()`, `local.isocalendar()`) |
| Amendment 5 carries the protocol's fields with placeholders | field-by-field against Amendment 4 in the same record | all seven fields present; deploy line reads `` `<sha>` at `<YYYY-MM-DD HH:MM CT (HH:MMZ)>` `` with an explicit "the controller fills the sha and the time at the deploy; until then the amendment is not yet in force" |
| README §7 matches `CRITERIA` | `tests/test_readme_gate.py`, run | 4 tests pass; both directions covered, plus the duties split |
| t13 reads only bounded sources | read every `text()` in `_table13` and `_t13_coverage` | `fills` on `ix_fills_filled_at` with `orders` by primary key; `metric_samples` on `ix_metric_samples_name_ts`; `runs.notes` through `recent_runs_pricing`' cap-then-filter; no `runs.started_at` predicate, no anti-join, no tape table |
| Annotator backlog bounded | `harness/research/annotate.py:148,152,159` | `BACKLOG_DAYS = 28`, `BACKLOG_LIMIT = 8`, both bound in SQL, not in Python |
| Every new SQL bounded and on an existing index | enumerated all new `text()` blocks; cross-checked `harness/db/schema.py:147,160,179,183` | see below |

**Indexes.** `ix_fills_filled_at`, `ix_orders_status`, `ix_metric_samples_name_ts` and
`ix_report_runs_week` are declared in `harness/db/schema.py`, not `models.py`, which is why a
grep of `models.py` alone appears to show none. Every new statement's claimed index exists.

Three new statements are bounded but not index-served, all on small tables and all documented in
their own comments: `_T13_NEWEST_FINAL` (`report_runs`, `order by generated_at desc limit 1`, no
index on `generated_at`), `_PENDING` (`annotate.py:159`, whose `year desc, week desc` ordering
cannot ride `ix_report_runs_week`'s ascending leading columns), and `_SNAPSHOT_AGES`
(`dashboard_snapshots`, about six rows). `_ELIGIBILITY_SIGNALS` and `_ELIGIBILITY_ORDERS` are
week-windowed scans of `signals` and `orders`, which is the same read `_T1_SIGNALS` and
`_CONFIG_HASHES` already perform on every render; the comment at `weekly.py:335-341` states this
rather than implying an index. None of these is a scan of `orderbook_events`, `venue_trades` or
unindexed `fair_values`, so the Global Constraints hold.

**Tests run.** `tests/test_snap_floor.py`, `tests/test_amendments.py`, `tests/test_weeks.py`,
`tests/test_readme_gate.py` (67 passed) and `tests/test_report.py` (60 passed), against
`harness_test_final6c` on localhost:5433. No failures, no warnings, no tracebacks. The full suite
is the controller's to run.

---

## What I checked that the per-task reviews could not see

**The exact-contract join is correct, and I verified it end to end rather than trusting the
comment.** `_FAIR_FOR_ORDERS` (`floor.py:331-337`) now compares three identity pairs with
`is not distinct from`. The question a per-task review cannot settle is whether the two tables
share a vocabulary: if `fair_values.outcome_side` and `venue_markets.side` used different domains,
every order would silently lose its fair. They agree, by construction on both sides:

- `harness/normalize/kalshi.py:112` writes `venue_markets.side = (None if side_kind == "team" else "over")`, so the column is `NULL` for moneyline and spread and `"over"` for totals, and `side_team_id` is `NULL` for totals.
- `harness/pricing/fair.py:104-122` (`_shapes_for_game`) builds the fair row's shape from those same venue-market columns: `("moneyline", side_team_id, None, None)`, `("spread", side_team_id, None, threshold)`, `("total", None, "over", threshold)`.

So the load-bearing predicates are `side_team_id` (which separates the two sides of a moneyline)
and `threshold` (which separates spread and total lines) — exactly the two defects 0.10 names.
`outcome_side` is effectively constant per market type today and costs nothing. The `coalesce(...)
= coalesce(...)` form of the same join already exists at `settlement/rfq_grade.py:79` and
`settlement/benchmarks.py:409,423`, so this is a new instance of an established pattern, written
in the stricter NULL-correct form.

**The side-space half of 0.10 needs no change and is guarded.** `_live_edge` already routes the
fair through `side_p(fair_p, side)` and Floor selects no `venue_mid_at_place`; three tests pin it
(`test_a_no_orders_live_edge_uses_one_minus_the_fair_and_the_book_is_not_converted_twice`,
`test_floor_selects_no_placement_mid_to_convert`, `test_a_null_keyed_fair_matches_only_a_null_keyed_market`).

**Both production callers of `weekly_tables` pass `now` explicitly** (`cli.py:492`,
`settlement/report_wtd.py:71`), so the Global Constraints' single clock-dependent exemption never
reaches production. `cli.py` additionally now computes one `now` for `weekly_tables`, `build_meta`
and `persist_report`, which is the right fix: t13's "this run generated at" row, the `- Generated:`
line and `report_runs.generated_at` are now the same instant by construction.

**`RENDER_ORDER` is derived from `TABLE_KEYS`** (`tables.py:64`), so a future table cannot be added
to one and forgotten in the other. `IDENTITY_COLUMNS` gains `t13: "item"` and the existing
set-equality test covers it.

**Replay consistency across the new fill queries is safe.** `harness/execution/loop.py:922` writes
`fills.replay` from the same `self.replay` that stamps the order, so the three funnel fill queries
cannot disagree even though only two of them filter both sides. This downgrades task 10's deferred
minor to informational.

**Wave 1 / wave 2 seam.** Wave 2 is a linear continuation on the same branch, not a merge, so the
three files both waves touch (`floor.py`, `tables.py`, `weekly.py`) carry no reconciliation risk.
I re-read the wave-2 diff of each against its wave-1 state and found no lost hunk.

---

## Important

### I1. The README's `fill_events` line states half the coded 80 % condition

`README.md:140-141` reads "at least **80 %** of them from a live order-book feed rather than
periodic snapshots." The coded criterion (`harness/report/gate.py`, `fill_events` description) is
"of which >= 80 % have `book_source = 'ws'` **and** `dirty_minutes = 0`". The clean-book half is
absent, and the sentence as written tells the reader the 80 % is about feed source alone.

This matters more than a wording slip. The milestone's acceptance is "README and the coded gate
`CRITERIA` reconciled with the pre-registration record without adding or removing a criterion,
**every requirement in its proper place**", and `dirty_minutes` is the half that is actually
binding in the current run. A reader of §7 today would not learn that.

The reconciliation test cannot catch it: `test_readme_section_7_names_no_criterion_the_code_does_not_have`
asserts every backticked underscore-bearing token in §7 is a `Criterion.name`, which forbids
writing `` `dirty_minutes` `` in §7 at all. So the fix has to be in plain words.

**Fix.** Amend the bullet at `README.md:140-141` to carry both halves, with no new backticks:

```
- `fill_events`: at least **150 pretend fills** confirmed by real trades, across at least **40 games and
  both sports**, at least **80 %** of them from a live order-book feed rather than periodic snapshots
  **and with a clean book for the whole time the order rested**.
```

This is wave-1 code, already on `main` at `3a61bc1`. It is a follow-on commit, not a wave-2 merge
blocker, but it must land before 6C is marked done.

### I2. Table 1 labels its rate's unit and not its count's

`harness/report/tables.py:396-398` now says "`fill_rate` is the actual fill rate (orders with a
`queue_model` fill / placements)". Good. But the neighbouring `fills` column
(`_T1_COLUMNS`, `tables.py:373`) is `count(f.id)` filtered to `queue_model` and non-replay
(`_T1_ORDERS`, `tables.py:320-333`) — that is a count of fill **rows**, while `fill_rate`'s
numerator is `filled_orders`, a count of **orders**. The two sit side by side and neither the
column nor the header says they are different units.

A reader does the obvious thing: divides `fills` by `orders` and gets a number that is not
`fill_rate`. Addendum 0.11 requires that "Floor's `_funnel` **and table 1** label each count with
its unit and never add unlike things", and this is precisely the roadmap's "a candidate row count
and a decision-event count are not a conversion funnel" failure mode, inside the milestone named
for trustworthy reports. Floor's funnel now does this correctly through `FUNNEL_UNITS`; table 1
was given only the `fill_rate` rename.

**Fix.** Extend the same header string at `tables.py:396-398`, one clause:

```python
              "`fill_rate` is the actual fill rate (orders with a `queue_model` fill / "
              "placements); its numerator is orders, while the `fills` column beside it counts "
              "`queue_model` fill *rows*, of which one order can carry several. The column key "
              "is unchanged because it is a stored `report_cells.col_key` the Study surface "
              "reads. "
```

This is wave-2 code (`ab73606`), unmerged. Land it before the merge.

### I3. Two of the six funnel units the roadmap names are deferred, and the deferral is recorded only inside 6C

The roadmap's 6C acceptance paragraph names six units verbatim: "unique candidate opportunities,
distinct intent episodes, placements, skips, actual filled orders, and counterfactual outcomes",
followed by "A candidate row count and a decision-event count are not a conversion funnel."

The branch delivers four of the six as named. The other two ship as labelled event counts that say
in the payload what they are not: `candidate_signals` is labelled "candidate signal rows, not
distinct opportunities" and `intent_verdicts` "intent verdicts (placed + skipped), not distinct
episodes" (`floor.py:110-117`).

I judge the implementation right and the bookkeeping incomplete. The distinct counts genuinely
need the `signals`/`intents` queries fix 31 removed as unbounded scans; rebuilding them here would
undo a fix. The design review reached the same conclusion (ruling I6) and the addendum recorded it
as D11 with its cost stated. That is a legitimate scope decision, honestly labelled on the surface.

What is missing is that the deferral lives only in the 6C addendum and a code comment. The
roadmap's 6D paragraph does not name distinct opportunities or intent episodes anywhere, so
nothing outside this milestone's own documents is holding the obligation. The roadmap's own rule
is that each milestone's acceptance is its acceptance paragraphs verbatim, and moving an item out
of one is the user's call.

**Fix,** both documentation, before 6C is marked done:

1. Add the two deferred units to 6D's acceptance in `docs/superpowers/autopilot/roadmap.md` so the obligation is carried where 6D will be judged.
2. Name the deferral in the phase report's Needs-you, beside the one-sided-direction decision that 0.8 already routes there, so the user sees both 6C scope decisions in one place.

---

## Minor

1. **`amendments.py:51` — Amendment 5's `deploy_sha` is `None` and will stay `None`.** The controller fills the sha into the pre-registration record at the deploy; nothing updates the code constant. The field feeds no output (`_eligibility` emits `excluded_runs`, `recorded`, `what`) and is read only by `tests/test_amendments.py:56`, so this is drift in dead data. Either fill it at the deploy alongside the record, or drop the field.

2. **`weekly.py:292` labels a non-cell posterior "insufficient (< 10 clusters)".** `_has_floor` returns `False` for any `t4` row whose posterior is `PLACEHOLDER` or `NOT_COLLECTED`, not only for a greyed cell, so a selected cell with no estimate at all is counted under the cluster-floor label. The confirmed count is unaffected. Splitting "insufficient" from "no estimate" would make the denominators exact.

3. **`weekly.py:275-280` — the printed `selected` count is a list length, the match set is a set.** If `select_cells` ever emitted a duplicate cell key, `selected` would exceed `evaluated + insufficient + missing`. Deduplicate `selected` against `wanted_cells`, or assert the invariant.

4. **`weekly.py:328-334` — the eligibility order count silently drops orders with no gap snapshot.** The join through `market_gap_snapshots` is the only way to reach a run id from an order, and the comment says so honestly, but the rendered line reads as a complete count. Naming the omitted population in the printed line would close it.

5. **`pulse.py:250` casts arbitrary payload JSON with `::float`.** `(payload->>'cell_age_s')::float` raises on any non-numeric value, which would fail the whole snapshot-ages read — the health surface. Only Study writes the key today and writes a number. A `nullif(..., '')` plus a guarded cast, or reading it as text and converting in Python, would make the ages panel unable to break on a future surface's payload.

6. **Task 2 M2 (carried): a single-quoted `text("...")` evades the structural SQL test.** No statement added by this branch is written that way, so nothing is currently unguarded. Worth closing when the bounds test is next touched.

7. **Task 2 M4 (carried): the week's fills range is scanned three times per render** (`_T13_ORDER_FILLS`, `_T13_FILL_ROWS`, `_T13_CUMULATIVE`). Three index-range scans at the six-hourly provisional cadence. Acceptable; noted for 6D's cost work.

8. **Task 9 M (carried): `tests/test_snap_bounds.py`'s table-name extraction reads the `FROM` of `IS NOT DISTINCT FROM`.** The forbidden-table assertion at `test_snap_bounds.py:241-248` is a substring check over the five tape-table names and stays sound; the cosmetic mismatch is in the neighbouring extraction. Task 4's deferred minor (the older-than-window test passing for an unrelated reason) and task 10's replay-filter note are both test hygiene and are covered above.

---

## Acceptance against the milestone, item by item

| Roadmap 6C requirement | Delivered |
|---|---|
| Chicago week selection across Ticket, WTD, Study scheduler/readers, Pulse | Yes — five consumers moved; `tests/test_weeks.py` pins Sunday evening, midnight rollover, the DST fall-back hour on both instants, the ISO year trailing the calendar year, and the UTC disagreement |
| UTC tape partitioning unchanged | Yes — `db/schema.py:42` and `ops/checks.py:53` untouched, stated in 0.1 and in Amendment 5 |
| Dated amendment for the measurement key | Yes — Amendment 5, all protocol fields, placeholders the controller fills |
| Diagnostic: actual filled orders and distinct games, actual/counterfactual separated | Yes — t13 rows 2-7 |
| Diagnostic: operational coverage and skipped work | Yes, within the honest limit ruling C1 set: the coverage rows are `runs.notes`' own reading and the table says so twice |
| Diagnostic: order 157 audit status | Yes — `audits.py` register, two t13 rows |
| Dashboard freshness vs report-cell age | Yes — Study's two labelled times, Pulse's "cells from" column, the sentence layer no longer says "fresh" |
| README and `CRITERIA` reconciled, duties named | Mostly — see I1 |
| Ten-game cluster floor, direction stored, definition of confirmation | Yes — the floor is a restoration (`is_grey`'s own docstring already said §9.6 excludes greyed cells), the direction is stored and printed but applied nowhere, the note prints all five denominators |
| Amendment-specific eligibility with excluded and missing shown | Yes — one line per amendment including the ones that exclude nothing |
| Floor exact-contract join and side-space comparisons | Yes — verified against both writers, not just the comment |
| Funnel units explicit | Four of six as named; two labelled and deferred — see I3 |
| 14-day exposure: complete aggregate or explicit limitation | Yes, the second branch, which the roadmap permits |
| Annotation: fix 41, bounded backlog, backoff, idempotence, prior-week test | Yes — 28 days, limit 8, bounds in SQL, one call per sweep |

`docs/superpowers/autopilot/verify.md` gains seven wave-1 rows with time-of-day expectations and a
deferral rule, six wave-2 rows, a deterministic stand-in for the walker while the Chrome bridge is
down, and a new Sunday-evening line in the time-of-day table. The rows are specific enough to fail
on: (ii) calls a padded or UTC-week snapshot name a FAIL rather than cosmetic, and the wave-2
confirmation row checks the deployed build for `DIRECTION_NOTE` so a one-sided reading cannot ship
as a condition unnoticed.
