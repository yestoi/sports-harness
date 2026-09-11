# Phase 5 final-review fix wave — report

Worktree `/Users/trey/dev/sports-wt/phase5-final-fixes`, branch `phase5-final-fixes`, cut from the
phase branch at `d24e41d`. Eight commits, one per ruling (Minors grouped into one). All targeted
tests and the full `make test` run pristine (see Verification).

## C1 — `harness/research/veto.py:225-227`, every real `reduce`/`veto` downgraded to `proceed`

**Commit:** `dee4067`

`_grade` resolved the model's `evidence_ids` only against the harness's own post-hoc `s1`-shaped
ids, which the model never sees. Fixed by resolving against the snippet's `url` too:

```python
items = snippets.get("items") or []
known = ({item.get("id") for item in items} | {item.get("url") for item in items}) - {None}
```

`harness/research/veto.py:225-231`. The injection case still fails closed: an injected page's URL
is never present in `snippets`.

**Test:** `tests/test_veto_worker.py::test_the_recorded_live_call_grades_as_reduce_not_proceed`
— runs `tests/fixtures/anthropic_structured_websearch.json` through `parse_response` then
`_grade` and asserts `("reduce", ≈0.62, None)`, matching the reviewer's own recorded-live-call
reproduction. Confirmed pre-fix this test fails with `("proceed", 0.62, "unresolved_evidence")`
and post-fix passes.

## C2 — `harness/report/weekly.py`, the annotator's bullets never render

**Commit:** `fe784a9`

`build_meta` now reads the newest `report_annotations` row for `(year, week)`, joined through
`report_runs` (never a specific `report_run_id`, since `build_meta` runs before `persist_report`
creates this render's own row — Minor 6's ordering point):

```python
_LATEST_ANNOTATION = text("""
    select a.bullets
    from report_annotations a
    join report_runs r on r.id = a.report_run_id
    where r.year = :year and r.week = :week
    order by a.created_at desc
    limit 1
""")
```

`harness/report/weekly.py:236-251`. `meta["annotation"]` is set only when a row exists, so
`render_markdown`'s existing fence logic is untouched.

**Test:** `tests/test_report.py::test_re_rendering_an_annotated_week_shows_the_fence` — seeds an
earlier `report_runs`/`report_annotations` pair for week 38, then invokes `harness report` through
`CliRunner` (not `render_markdown` directly) and asserts the fence and bullet text appear in
stdout, and that the render persisted its own new `report_runs` row rather than reusing the
seeded one.

## I1 — `harness/research/annotate.py`, the ISO-week lock held across the live call

**Commit:** `5b3f3ff`

Added `session.commit()` immediately after `reserve_spend` returns, on both the success path and
the `BudgetRefused` path (which also takes the lock before the caps are checked) — mirroring
`veto.py`'s `_call_pair`/`veto_pass` split exactly. `harness/research/annotate.py:102-118`.

**Tests:** `tests/test_annotator.py::test_the_refused_reservation_does_not_keep_the_week_lock` and
`::test_the_reservation_does_not_keep_the_week_lock_across_the_call` — both assert
`select count(*) from pg_locks where locktype = 'advisory' and pid = pg_backend_pid()` is `0`
after `annotate_pass` returns, on the refusal path and the successful-call path respectively.

## I2 — `harness/execution/store.py:231`, one savepoint per intent

**Commit:** `64f779e`

Split row-building from the write. `_queue_values` (pure, no session) builds one intent's queue
row during the loop; `_write_queue_batch` inserts the whole collected list once, under a single
`session.begin_nested()` after the loop. `harness/execution/store.py:179-244`. Isolation is
unchanged (a queue failure still never costs an intent — the try/except still wraps only the
batched write), and the subtransaction count is now 1 regardless of batch size.

**Tests:** `tests/test_veto_queue.py` —
`test_the_enqueue_never_fails_the_executor` and `test_a_failed_enqueue_leaves_the_intents_committed`
updated to monkeypatch the new `_write_queue_batch` seam (the old `_enqueue_veto` no longer
exists); added `test_the_whole_batch_is_written_under_one_savepoint`, which counts calls to
`db_session.begin_nested` and asserts exactly `1` for a three-candidate batch, plus that all three
rows land in `veto_queue`.

## I3 — `harness/weather/stadiums.py`, YAML re-parsed once per due game

**Commit:** `cd2182b`

`@functools.lru_cache(maxsize=1)` on `load_stadiums`, `load_neutral_sites` and `load_missing` —
the three loaders backing `stadiums.yaml`, `neutral_sites.yaml` and `stadiums_missing.txt`
(`load_roster`, over `roster.txt`, is coverage-test-only and untouched).
`harness/weather/stadiums.py:72-100`. No existing test rewrites these files at run time, so no
`cache_clear()` call was needed; the module docstring now documents the caching and the
`cache_clear()` obligation for any future test that does.

**Test:** none added — `tests/test_stadiums.py`'s existing suite (all read-only against the
shipped files) passed unchanged, which is the expected outcome of a pure caching change.

## I4 — `harness/cli.py:945`, the parlay card's week/year vs. the ledger's Chicago week

**Commit:** `f5babb3`

Added `harness/parlay/build.py`'s `resolve_iso_week(now, week) -> (year, week)`, which anchors to
`chicago_day(now).isocalendar()` exactly as `mark_placed`/`show_cards`/`parlay_grade._settle_card`
already do; the CLI now calls it instead of a raw `now.isocalendar()`. `build_card` gained an
explicit `year` parameter (defaulting to the same Chicago-anchored value) and now stores whatever
`year` it is given rather than deriving `now.year` itself. `harness/cli.py:935-949`,
`harness/parlay/build.py:17-33,85-95,145-146`.

**Test:** `tests/test_parlay_build.py::test_resolve_iso_week_uses_chicago_not_utc` — at Sunday
2026-09-13 20:00 CT (2026-09-14 01:00 UTC), asserts raw UTC `isocalendar()` gives `(2026, 38)`
while `resolve_iso_week` gives `(2026, 37)`, matching the ruling's example. Plus
`::test_resolve_iso_week_honors_an_explicit_week_but_not_an_explicit_year` and
`::test_the_card_s_year_follows_chicago_not_a_passed_explicit_one` (the latter asserts
`build_card(..., year=2031).year == 2031`, proving the parameter is stored rather than derived).

## I5 — `harness/dashboard/static/js/pulse.mjs`, the research section unrendered

**Commit:** `20b7e48`

`_research` (`harness/dashboard/snapshots/pulse.py:738-757`) now puts `research_reading` and
`veto_reading`'s own output into the section (`research.sentence`, `research.veto_sentence`),
making both sentence helpers reachable from something other than their own tests. `pulse.mjs`
renders a ninth "Research spend and the veto" card from those two sentences plus the two counts
neither sentence carries (`rfq_quotes_24h`, `annotations_week`), through `el()`/`statTile()`/
`sentences()` only — no `innerHTML`. Two new `glossary.json` entries
(`research.rfq_quotes_24h`, `research.annotations_week`) back the two stat tiles' technical names.

**Tests:** `tests/test_snap_pulse.py::test_the_research_section_carries_its_own_rendered_sentences`
asserts `section["sentence"]`/`section["veto_sentence"]` match the sentence helpers' own output;
`tests/test_dashboard_surfaces.py::test_the_pulse_module_renders_every_section_of_its_payload`
extended to require `payload.research` alongside the other eight sections (this test also covers
the DOM-safety and glossary-coverage requirements for the whole file, both already green).

## Minors (one commit, `1e2a6ca`)

**Fixed:**
- **Minor 1** (`harness/feeds/nws.py`) — dropped the docstring's inaccurate claim that the module
  wraps a `_ReadOnlyClient` shape; it wraps a bare `httpx.Client` and relies on `NwsClient`
  exposing only `.get()`, which the docstring now says.
- **Minor 7** (`harness/db/schema.py`'s `_VETO_H9_VIEW` vs. `migrations/versions/0004_phase5.py`)
  — added `tests/test_alembic.py::test_the_veto_h9_view_definition_agrees_between_schema_and_migration`,
  asserting the schema constant's stripped text is contained verbatim in the migration file.
- **Minor 10** (`harness/recorder/tick.py`'s `_weather`) — moved `NwsClient(self.s)`'s
  construction inside the method's own `try`, so a construction failure is confined to a warning
  on the weather source instead of escaping to the tick's outer handler.

**Left, with reasons (none mechanical):**
- **Minor 2** — `annotate.py`'s transport-failure-vs-check-failure distinction needs new
  `CallResult`-shape handling to tell the two cases apart.
- **Minor 3** — `render_for_model.py`'s substring number check is a citation-matching behavior
  change that touches the annotator's bullet-drop tests; needs its own review pass.
- **Minor 4** — `weather/snapshots.py`'s double GET after a dead URL is a fetch-flow change.
- **Minor 5** — `pulse.py`'s `week_start` truncation/Chicago-keying and its duplicate `_VETO_RATE`
  call would change `gather()`'s return contract and the `annotations_week` count semantics; out
  of scope for a fix wave that otherwise only added to that function.
- **Minor 6** — informational only (an ordering note); the C2 fix above already accounts for it.
- **Minor 8** — the reviewer states this is conformant as specified; no fix requested.
- **Minor 9** — already parked as M7 at the T14 review; recorded there, not re-opened here.
- **Minor 11** — `parlay/build.py`'s `NoAnchorPriced` dual meaning needs a new exception type or
  an exit-code split; a design call, not a mechanical one.

## Verification

Targeted suite (`tests/test_veto_worker.py tests/test_annotator.py tests/test_report.py
tests/test_veto_queue.py tests/test_weather_snapshots.py tests/test_stadiums.py
tests/test_parlay_build.py tests/test_cli.py tests/test_snap_pulse.py
tests/test_dashboard_surfaces.py tests/test_dashboard_static.py tests/test_alembic.py
tests/test_research_client.py`) plus a ripple check (`tests/test_tick.py
tests/test_recorder_weather.py tests/test_parlay_rationale.py tests/test_parlay_placement.py
tests/test_parlay_grade.py tests/test_store.py tests/test_schema.py`): all green, no
FAILED/ERROR, no warnings summary.

Full `make test` (`DATABASE_URL_TEST` against `harness_test_phase5_final_fixes`, run in the
foreground/background per the loop rule, ended 2026-09-10 20:54 CT): reached `[100%]`, exit code
0, no FAILED/ERROR lines, no warnings summary — pristine.

## Concerns

None outstanding. The `build_card`/`resolve_iso_week` default-fallback design (a caller that
passes no `year` still gets the Chicago-anchored value) means the fix is safe even for any future
caller that forgets the new parameter, at the cost of the Chicago-anchoring logic existing in two
places (`resolve_iso_week` and `build_card`'s own fallback) rather than one; flagging this as a
judgment call rather than a defect, since `build_card`'s own fallback is the same one-line
`chicago_day(now).isocalendar().year` call, not a re-implementation.
