# Hotfix 52 preparation report

Commit: `8a3b25bb578393c40be3e7e5fa1244eb40ebe8af`
Branch: `recovery/fix52`
Worktree: `/Users/trey/dev/sports-wt/recovery-fix52`
Base: `2a1061ca1d12537335b05adbd11db63faa851cce`
Authorship: OpenAI Codex, recorded explicitly in the commit author and message.

## Changes

- `harness/recorder/tick.py`: weather derives the shortest active sport interval directly from `interval_for`; when all sports are quiet the result is None and the source skips before constructing its client. The 900-second fallback in `cadence_in_force` remains unchanged for pricing. Registered cadences, source budget, 25-second remaining-budget guard, settings, and pricing behavior are unchanged.
- `harness/weather/snapshots.py`: successful per-game forecast refreshes upsert the existing `source_state` table. No migration/schema addition. Snapshot changes remain append-only and existing `WeatherSnapshot.fetched_at` values are never touched.
- `tests/test_recorder_weather.py`, `tests/test_tick.py`: regression coverage described below. Existing NWS test clients now use the fixture clock, and multi-pass clients advance explicitly rather than manually aging evidence rows.

## Marker and freshness contract for controller-owned verify.md update

`source_state.key` (String(64), primary key) is `nws_hourly:<decimal game id>`, e.g. `nws_hourly:123`.
`source_state.last_fetched_at` (timezone-aware timestamp, non-null) is the successful hourly HTTP result's `fetched_at`, taken by the existing NWS client clock on response completion. It is not tick-start time and not a venue-provided timestamp. There is no status column: existence/update of this key means a schema-valid HTTP 200 response contained at least one period in the game's kickoff -1h/+4h window and any changed periods flushed successfully. An unchanged usable response advances the same marker. HTTP failures, failed point resolution, schema refusal, timeout/exception, exhausted/skipped work, and valid bodies with no relevant forecast periods do not advance it. Empty relevant windows now carry `no forecast periods` in the existing skipped notes.

The effective current freshness timestamp used by `games_due` is PostgreSQL `greatest(s.last_fetched_at, (select max(w.fetched_at) from weather_snapshots w where w.game_id = g.id))`, joined by `s.key = 'nws_hourly:' || g.id::text`. PostgreSQL greatest ignores a null operand; both null means never fetched. This preserves legacy snapshot fallback and honors more recent snapshots even when a mark exists. Due-ness clears until this timestamp is at least REFETCH_AFTER (one hour) old. Ordering is oldest effective successful fetch first, with never-fetched games first.

Use that effective timestamp for the same existing 2-hour operational freshness criterion. The controller owns the weather row in `docs/superpowers/autopilot/verify.md`; this commit deliberately does not edit it. Retain the 72-hour horizon, outdoor/skipped explanation rules, cadence defer rules, and timestamp-integrity checks. The marker is mutable scheduling/operational state only: never substitute it for snapshot fetched_at in historical as-of evidence readers. Do not backfill marks from arbitrary raw HTTP success rows, because schema/window usability matters.

The existing research `_NEWEST_WEATHER` reader remains unchanged: `weather_snapshots.fetched_at <= :as_of`. Repeated unchanged refreshes cannot erase old evidence or invalidate old signal reads; later changed rows are still excluded before their fetch time. Current-state markers cannot establish past refresh history after being updated; raw responses retain the underlying attempts if that history must be audited.

## Regression coverage added or strengthened (not executed here)

- Real Chicago quiet-hour boundaries: 00:59:59, 01:00, 03:00, 07:59:59, and 08:00; normal and forced tick paths; no weather client constructed in quiet hours; pricing fallback unchanged.
- Cadence guard cases None, 20, 30, 120, 300, 900; existing remaining-budget coverage preserved.
- Changed and unchanged usable refreshes clear due-ness; unchanged repeat at +30s causes no network fetch; due boundary is exactly one hour.
- Unchanged refresh leaves every original snapshot fetched_at unchanged; actual research as-of query still sees earlier evidence and sees nothing before its first fetch.
- Changed forecast adds rows while an earlier as-of query remains on old evidence.
- HTTP 500, schema rejection, no-window forecast, timeout, and failed points resolution preserve/omit success markers appropriately and remain due.
- Success-marker ordering, legacy fallback tests, and unrelated-source key isolation.

## Validation and integration

Passed: Python AST syntax parse of all four changed files; `git diff --check`.
Not run: pytest, database tests, full suite, deployed/runtime behavior, raw NWS volume checks. No claim of test success or operational acceptance. Controller must run `make test` in this branch's worktree on Omarchy against localhost:5433 and inspect failures; focused relevant files are `tests/test_recorder_weather.py`, `tests/test_weather_snapshots.py`, and weather tests in `tests/test_tick.py`.

Inspected `fix-48-pricing-stage-order`'s committed tick diff against 2a1061c: it adds telemetry in `_recorder_samples`, with no overlap in `_weather`. Pending uncommitted controller/agent work may differ; preserve their changes when integrating. This commit touches only the five-line weather gate in shared tick.py and weather-related tests in shared test_tick.py.

Remaining operational evidence: controller update to verify row, Omarchy suite execution, daytime stable-forecast freshness check, hourly NWS raw-response counts, next quiet-window zero-NWS confirmation. Failed requests intentionally remain due as before; this change does not introduce new error-backoff policy or guarantee a traffic ceiling during an outage. Source-state grows one small row per successfully forecast game, using the existing mechanism; no new retention policy introduced.

No instruction-like content was observed inside fixture/provider data examined during this work. Existing repository documentation contains historical NAS/Claude/approval instructions; those were read as project context and not used to override the assigned no-NAS and controller-owned validation constraints. No ssh, scp, docker, deploy/status target, network fetch, or database command was executed.
