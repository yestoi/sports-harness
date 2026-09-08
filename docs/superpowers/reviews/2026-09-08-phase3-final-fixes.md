# Phase 3 final fix wave — implementer report

Branch `phase3-final-fix` in `/Users/trey/dev/sports-wt/phase3-final-fix`, based on
`phase3-paper-execution` at 78db779 (which carries 4570acc). Seven Importants and two Minors
from `final-review.md`, one commit per item (I2 and I3 share one, M1 and M2 share one).

**Status: DONE_WITH_CONCERNS** (the concerns are notes for the re-reviewer, not unfinished work).

**Suite:** `821 collected, exit 0, pristine` — `make test` against
`harness_test_phase3_final_fix` on localhost:5433, `git status --porcelain` empty afterwards.

**Commits**

| sha | item |
|---|---|
| 3875eb9 | I1 — eight Task 12b telemetry checks |
| f0611e1 | I2 + I3 — bounded tape lookups, `EXECUTOR_VERSION` 3.6 → 3.7 |
| 179987a | I4 — `_ELIGIBLE_GAMES` excludes `result` |
| cb76be6 | I5 — runbook `export-fixture` paragraph |
| 83e540d | I6 — `report_wtd_period_s` |
| 2baa09d | I7 — one book per order in markouts |
| 6df2b7d | M1 + M2 |

No live path was added, no DDL was written (no new index was needed, so the pinned DDL count is
untouched), no dependency was added, no criterion, threshold, family, band or the
pre-registration record was touched, and `docs/superpowers/autopilot/verify.md` was not edited.
Nothing in any file or fixture I read looked like an instruction addressed to me.

---

## I1 — `CHECKS` covers verify.md's eight Task 12b telemetry invariants

**Changed:** `harness/ops/checks.py:203-276` — eight new `Check` entries after
`markouts_at_after_horizon`, taken verbatim from `verify.md:191-208`:
`metric_samples_negative_24h`, `operator_events_empty_summary_24h`,
`order_watch_negative_queue_24h`, `equity_mtm_coverage_out_of_range_24h`,
`game_score_went_down_24h`, `check_results_unknown_status_25h`,
`report_runs_generated_in_future`, `report_cells_orphan`. Registry is now 27.

Bounding, as the ruling requires: the five append-only sample tables carry a 24 h `ts` window,
`check_results` carries 25 h (the sweep is daily, so 24 h can miss the previous run by minutes —
the same window verify.md's own "all pass" query uses), and `report_runs` / `report_cells` carry
none because they take a handful of rows a week. All eight run under the existing 2000 ms
`SET LOCAL statement_timeout` and none names a bulk tape table, so `assert_no_tape_reads` (which
runs at import) still holds.

**Test:** `tests/test_checks.py:20-39` (`EXPECTED_NAMES` + `EXPECTED_COUNT = 27`) asserted by
`test_checks_all_have_timeout_and_no_tape_reads`, which also proves every one of the 27 runs
under the timeout by counting `set local statement_timeout` off the wire.
`tests/test_housekeeping.py:235` (`len(check_rows) == len(CHECKS)`) picks up the new count with
no edit.

**RED → GREEN:** RED `AssertionError: Extra items in the right set: 'operator_events_empty_summary_24h', 'report_cells_orphan', …`.
GREEN: `tests/test_checks.py tests/test_housekeeping.py` → 20 passed.

`verify.md` was not edited.

## I2 — `store.newest_event_ts(at=…)` bounded by a ts window

**Changed:** `harness/execution/store.py:317-350`. The statement is now
`where ts <= :at and ts > :lower order by ts desc, id desc limit 1`, with `TAPE_WINDOW = 10 min`
widened once to `TAPE_WINDOW_WIDE = 24 h` and `None` beyond that. The lower bound is what lets
`ix_obe_ts_brin` prune inside the partition holding the instant; `(ts desc, id desc)` rather than
`id desc` because the loop's question is which row is newest by the recorder's clock, not which
was inserted last.

`None` past 24 h is a behaviour change with no decision consequence: `Executor._body`
(`harness/execution/loop.py:340-344`) already sets `dead_recorder` for `ws_last is None` and for
any `ws_last` older than `book_max_age_s` (120 s), so both readings are the same verdict. The
only visible difference is `heartbeat["ws_last_event_at"]`, which becomes `None` instead of a
day-old timestamp.

**Test:** `tests/test_exec_loop.py:1347` (twenty rows after the instant, three before it, the
last of which carries the highest id and the oldest ts) and `tests/test_exec_loop.py:1368` (the
widening, then `None` past 24 h).

**RED → GREEN:** both RED — the first on `id desc` returning the `at - 600 s` row, the second on
`assert store.newest_event_ts(db_session, AT) is None` returning `2026-09-08 14:00`. GREEN: 2
passed.

## I3 — `book._MAX_EVENT_ID_AT` bounded by the same window

**Changed:** `harness/execution/book.py:137-160`. `_MAX_EVENT_ID_AT` gains
`and ts > :lower`; the widening lives in `_max_event_id_at(session, fetched_at)`, which both
REST-anchor call sites (`load_book` and, through `_book_at_unchecked`, `book_at`) now use in
place of the inline `.scalar() or 0`.

The empty-after-widening fallback is `0`, which is what the old `or 0` produced when the tape was
empty. `gap_check_id = 0` makes every gap on sid 0 read as after the ladder, so the book goes
dirty — the conservative direction, since a dirty book blocks decisions while a clean one would
let a ladder that may have missed frames look tradeable (F36). Documented at the constant.

**Test:** `tests/test_book.py:467` (twenty rows after the fetch, two inside the window before it;
the gap-check id is the newest inside the window) and `tests/test_book.py:487` (a fetch whose
only preceding row is 30 h back takes 0; a row 6 h back is found again).

**RED → GREEN:** RED `assert 1 == 0` on the 30-hour case. GREEN: `tests/test_book.py` → 28 passed.

**`EXECUTOR_VERSION` 3.6 → 3.7** in `harness/execution/__init__.py:4`, with all four pins moved:
`tests/test_book.py:35`, `tests/test_exec_loop.py:176`, `tests/test_exec_plan.py:134`,
`tests/test_fills.py:63`. This one bump covers every `harness/execution/` change in the wave
(I2, I3, I7).

## I4 — a raised `compute_benchmarks` savepoint no longer locks a game out

**Changed:** `harness/settlement/benchmarks.py:301-320`. `_ELIGIBLE_GAMES`' "already has
benchmarks" test is now
`not exists (select 1 from benchmarks b where b.game_id = g.id and b.benchmark_type <> 'result')`,
with a comment recording the seam (two stages, `result_benchmarks` registered after this one).

**Test:** `tests/test_benchmarks.py:332`. Pass 1 replaces `bm._process_game` with a raiser, so the
savepoint rolls back with no rows and one `ctx["errors"]` entry; `insert_result_benchmarks` then
writes the game's `result` row in the same pass; pass 2 must still insert its pre-kickoff
benchmarks.

**RED → GREEN:** RED on `assert compute_benchmarks(...) > 0` (returned 0).
GREEN: `tests/test_benchmarks.py tests/test_order_clv.py` → 26 passed.

## I5 — the runbook's `export-fixture` paragraph

**Changed:** `docs/runbooks/phase0-deploy.md:142-151`, rewritten from `harness/cli.py:488-528`:
a single self-contained JSON document rather than a directory, `--out PATH` required and `--out -`
for stdout, `--kind day --from-run A --to-run B` and `--kind ws-tape --ticker T --from TS --to TS`
with what each writes, `tests/fixtures/day_synthetic/day.json` and
`tests/fixtures/tape_sample_*.json` as the two shapes, and the read-only note. The word
"directory" and the non-existent `tests/fixtures/day_2026-09-13/` are gone.

**Test:** `tests/test_cli.py:218` pins both ways the paragraph had drifted — every long option it
names is a real Typer option of the shipped command, and every `tests/fixtures/...` path it names
exists in the tree.

**RED → GREEN:** RED `AssertionError: tests/fixtures/day_2026-09-13/ does not exist`.
GREEN: `tests/test_cli.py` → 10 passed.

## I6 — `report_wtd_period_s`

**Changed:** `harness/config/settings.py:65-72` adds `report_wtd_period_s: int = 21_600` with the
reasoning at the field. `harness/settlement/report_wtd.py` drops the module `PERIOD` constant,
takes the period from the settings on the context, and reads the settings *before* the due check
(the budget yield is still first, unchanged). The stage docstring
(`harness/settlement/report_wtd.py:41-52`) states why six hours and not the design spec §3.7's
hour: `tables._T4_SNAPSHOTS` loads every `market_gap_snapshots` row of the ISO week into a Python
list and `_T5_SNAPSHOTS` parses every WebSocket snapshot body for the week's moved tickers into a
`BookState`, on a NAS with about 1 GB spare, competing with `settle` for the same slot.

**Test:** `tests/test_report_persist.py:61` updated (not due an hour later, due at six hours plus
a minute; the low-budget yield assertions are unchanged) and `tests/test_report_persist.py:92`
new — a settings copy with `report_wtd_period_s = 3600` runs again after an hour, so the setting
is what drives the cadence. `tests/test_settings.py:62` pins the default.

**RED → GREEN:** RED `AttributeError: 'Settings' object has no attribute 'report_wtd_period_s'`
in both files. GREEN: `tests/test_report_persist.py tests/test_settings.py` → 8 passed.

## I7 — one book per order in markouts

**Changed:**

- `harness/execution/book.py:417-452`: `book_at` is split into `_book_at_unchecked` (anchor at or
  before the instant plus every delta up to it) and `_too_stale` (the F36 gate). `book_at` is now
  those two composed, so there is one implementation of the anchor rules.
- `harness/execution/book.py:518-577`: `BookWalker(session, ticker).at(instant)`. It reloads the
  anchor only when a WebSocket snapshot or REST ladder landed between the last instant and this
  one — the only thing that can change which anchor `book_at` picks — and otherwise advances the
  cached book with `advance_book_at`, whose delta scan starts at the book's own cursor. The
  staleness gate is applied per instant exactly as `book_at` applies it, and a stale instant does
  not discard the cached book. An instant that goes backwards is answered by a full rebuild.
  Two new bounded probes back the re-anchor test (`_WS_SNAPSHOT_BETWEEN`,
  `_REST_SNAPSHOT_BETWEEN`, `harness/execution/book.py:127-136`).
- `harness/settlement/markouts.py:296-320`: `_book_mid_fn` holds one walker per order.
- `harness/settlement/markouts.py:346-368`: `_process_order` collects every due
  `(anchor, horizon)` across all anchors first and sorts by `horizon_ts`, so the walker is only
  ever asked for instants that move forward. The rows written are unchanged and independent of
  insertion order.

**Test:** `tests/test_book.py:507` — the walker's ladders, mid, `as_of` and `book_age_s` equal a
per-instant `book_at` rebuild across five instants with deltas between each; `tests/test_book.py:538`
— a resubscribe snapshot between two instants forces a re-anchor onto the new sid and ladder;
`tests/test_book.py:559` — a quiet instant answers `None` exactly as `book_at` does and the walk
continues afterwards. `tests/test_markouts.py:516` is the stage-level test: one anchor load for
an order with four due horizons (was four), and every written `venue_mid` and `mid_age_s` equal to
a per-instant `book_at` rebuild.

**RED → GREEN:** RED `AssertionError: 4 anchor loads for one order's horizons`.
GREEN: `tests/test_markouts.py` → 21 passed, `tests/test_book.py` → 31 passed.

## M1 — the `ws_disconnect` reason goes through the log redactor

**Changed:** `harness/venues/kalshi/ws.py:17` imports `redact`;
`harness/venues/kalshi/ws.py:378-384` writes `redact(repr(e))[:200]`. Redact then truncate, so a
pattern cut in half by the 200-char limit cannot leak.

**Test:** `tests/test_kalshi_ws.py:1301` — the ws factory raises a handshake error quoting a
connect URL carrying `api_key=abc123def` and an `sk-ant-…` token; neither string may appear in
`operator_events.summary` and `[REDACTED]` must.

**RED → GREEN:** RED `assert 'sk-ant-supersecrettoken' not in "WebSocketEx…ecrettoken')"`.
GREEN: `tests/test_kalshi_ws.py` → 42 passed.

## M2 — the assertion-free test now asserts

**Changed:** `tests/test_checks.py:125-155`. It now reads `show statement_timeout` before and
after `run_checks`, asserts they are equal and that the value is not the 50 ms the call used, and
asserts the row its `pg_sleep(0.15)` returns rather than relying on not raising.

**RED → GREEN:** verified by temporarily removing the reset in `run_checks`:
`AssertionError: assert '50ms' == '30s'`. The file was restored (`git diff --stat` empty) before
the commit.

---

## Suite tail

```
821 passed in 112.58s (0:01:52)
```

`.venv/bin/pytest -q --collect-only` → 821 collected; `pytest -q` exit 0; `git status --porcelain`
empty after the run.

---

## Concerns for the re-reviewer

1. **I2's `None` past 24 h reaches the heartbeat.** `heartbeat["ws_last_event_at"]` is `None`
   instead of a day-old timestamp when a replay starts after a long recorder outage.
   `dead_recorder` is identical either way, but a dashboard or verify query reading that field
   sees `None` rather than an old instant. Nothing in `harness/` reads it for a decision.

2. **`BookWalker` can be `dirty` where `book_at` is not.** `advance_book_at` carries the live
   loop's gap verdict; `book_at` deliberately does not. Nothing that reads a walked book consults
   `dirty` (`mid()`, `as_of` and therefore `book_age_s` are identical either way), and erring
   dirty is the safe direction, but it is a documented difference at the class docstring rather
   than an invisible one. One consequence: a walked book that goes dirty runs `advance_book_at`'s
   re-anchor probe on every later instant, and reloads through `load_book_at` if a clean snapshot
   exists — correct, and it clears the dirt, but that instant costs a full rebuild.

3. **I6 leaves the design spec's §3.7 text saying "once an hour."** I did not edit
   `docs/superpowers/specs/2026-09-07-phase3-paper-execution-design.md`; the docstring at
   `harness/settlement/report_wtd.py:41` records that the default is six hours and why. If the
   spec is meant to track the shipped default, that is a one-line edit outside this wave's scope.

4. **I5's paragraph is the only runbook change.** The review also noted that
   `harness note --kind {...}` is not listed among the phase-3 one-offs at all
   (`final-review.md` I5, last sentence). The brief scoped I5 to the `export-fixture` paragraph,
   so I left that alone.

5. **`_ELIGIBLE_GAMES`' 7-day window still applies after I4.** A game whose savepoint raises is
   eligible again on the next pass, but only while it is inside `ELIGIBLE_WINDOW` of kickoff. A
   game that raises on every pass for seven days is still lost — now visibly, through the
   `ctx["errors"]` entry each pass, rather than after one.

6. **Deferred Minors untouched, as instructed:** M3–M10 of the final review (the hard-coded
   10-minute placement window in `fills_outside_placement_window`, `gap_outcomes_batch` never
   read, `Order.fair_cross_fill`, the `_ORDER_CLV_CANDIDATES` shape test, `budget_exhausted`
   semantics, `store.event_ts` / `_FETCHED_BY_SOURCE` partition probes, the duplicated
   `_benchmarks_for_shape` name, file sizes).
