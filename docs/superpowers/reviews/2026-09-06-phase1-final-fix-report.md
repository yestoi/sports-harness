# Phase 1 final-review fix wave — report

Branch `phase1-normalize-match`. One commit implementing the rulings C1–C3, I4–I8 and I10 from
`final-review-report.md`. TDD throughout: a failing test first for every behavioural change.

Full suite: **102 passed** (was 92 before this wave).

---

## C1 — per-row savepoint in the normalize runner

**Changed** `harness/normalize/runner.py`. `normalize_new` was rewritten around a new
`_drain_batch` helper (`runner.py:97-130`) and a `_watermark` helper (`runner.py:88-94`).

- Each row's `_handle` now runs inside `with session.begin_nested():` (`runner.py:111`), so a
  database error rolls back only that row's savepoint. The rest of the batch commits.
- On exception the runner logs `log.exception` and appends
  `{family: {"raw_id": r.id, "error": repr(e)[:300]}}` to `ctx["normalize_errors"]`
  (`runner.py:115-116`). This also closes triage item T7 ("`normalize_errors` carry exception text").
- The watermark still advances past a poison row (`runner.py:118`), so a deterministic error is
  skipped once rather than retried forever.
- The watermark is written from a plain local (`last_id`) after the row loop rather than mutated
  inside the loop, because a savepoint rollback would otherwise revert an in-loop `UPDATE
  normalize_state` and desynchronise the ORM object from the row.
- If the family commit itself fails, the runner rolls back, records
  `{family: {"commit_error": repr(e)[:300]}}`, resets the watermark to the last successfully
  committed row and stops draining that family (`runner.py:120-129`).

**Test** `tests/test_runner.py::test_poison_row_is_isolated_by_savepoint_and_watermark_advances`.
A raw `kalshi_trades` row whose `trade_id` is 80 characters (column is `String(64)`) followed by
a valid row.

RED:
```
E   psycopg.errors.InFailedSqlTransaction: current transaction is aborted, commands ignored until end of transaction block
E   [SQL: UPDATE normalize_state SET last_raw_id=%(last_raw_id)s::BIGINT WHERE normalize_state.family = %(normalize_state_family)s::VARCHAR]
```
GREEN: the valid trade is inserted, `normalize_errors` names the poison raw id with its exception
text, and `normalize_state.last_raw_id` equals the valid row's id.

## C2 — `reprocess --truncate` no longer destroys WebSocket trades

**Changed** `harness/normalize/runner.py:17-19` and `:155-158`. `venue_trades` was removed from
`NORMALIZED_TABLES` (with a comment saying why) and replaced by
`delete from venue_trades where source = 'rest'`. The rest of the truncate list is unchanged and
`orderbook_events` remains untouched.

**Test** `tests/test_runner.py::test_reprocess_truncate_keeps_websocket_trades`. Seeds one
`source="ws"` and one `source="rest"` trade and runs `reprocess(truncate=True)` on an empty raw
table.

RED: `assert set() == {'ws-1'}`. GREEN: only `ws-1` survives.
`test_reprocess_truncate_rebuilds_same_counts` still passes, so the REST rebuild is unaffected.

## C3 — colliding ESPN alias keys become ambiguous instead of last-write-wins

**Changed** `harness/matching/teams.py`.

- New sentinel `AMBIGUOUS_TEAM_ID = -1` (`teams.py:18`) with a comment explaining the D-II/D-III
  namesake problem.
- `_upsert_alias` (`teams.py:21-29`) now uses
  `on_conflict_do_update(set_={"team_id": case((keep, excluded.team_id), else_=AMBIGUOUS_TEAM_ID)})`
  where `keep` is `(team_aliases.team_id == excluded.team_id) | (~team_aliases.source LIKE 'espn_%')`.
  So an ESPN source whose key is already claimed by a different team parks the key on the
  sentinel; manual and learned sources keep plain overwrite semantics. A key already on the
  sentinel never flips back, because the existing `-1` never equals an incoming real team id.
- `resolve_team` (`teams.py:60-64`) and `resolve_fuzzy` (`teams.py:76-77`) filter out
  `team_id == AMBIGUOUS_TEAM_ID`.
- `harness/cli.py:146-150`: `match-report` prints
  `ambiguous aliases: N (first 10: [...])`.

**Tests** in `tests/test_teams.py`:
- `test_colliding_espn_alias_key_becomes_ambiguous_not_last_write_wins` — two teams sharing
  location "Troy" in one sport; `resolve_team("Troy")` returns `(None, "")`,
  `resolve_team("Troy Trojans")` resolves to 2653, and a manual alias for "Troy" then wins.
- `test_ambiguous_sentinel_does_not_flip_back_on_reseed` — seeds twice, asserts the row is still
  `-1`.
- `test_non_espn_sources_still_overwrite` — a learned `kalshi_name` key is overwritten, not
  poisoned.

RED: `ImportError: cannot import name 'AMBIGUOUS_TEAM_ID'`. GREEN: all three pass.

Live effect in `harness_dev`: `KXNCAAFGAME-26SEP12CHARMISS` and `KXNCAAFSPREAD-26SEP12CHARMISS`
(17 markets) went from `unmatched / no game for pair` to `matched / pair+date exact`, and
`ncaaf espn_location 'charlotte'` is now the sentinel with `espn_short 'charlotte' -> 2429` doing
the work. `troy` likewise now resolves to 2653 Troy Trojans via `espn_short`/`espn_abbr` rather
than to 3237 Troy Vikings. Eleven ambiguous keys exist in live data:

```
ncaaf espn_abbr osu, espn_abbr rsvt, espn_abbr_name "rsvt lakers",
ncaaf espn_display "roosevelt lakers", espn_location charlotte, espn_location roosevelt,
ncaaf espn_location troy, espn_short roosevelt, espn_slug "roosevelt lakers"
nfl   espn_location "los angeles", espn_location "new york"
```

## I4 — the runner drains a family instead of one batch per tick

**Changed** `harness/normalize/runner.py:133-151`. `normalize_new` now takes
`time_budget_s: float = 30.0` and loops `_drain_batch` per family until a batch returns fewer
than `batch` rows, the family's commit fails, or the monotonic budget is exceeded (logged at
warning). Each batch commits. `harness/recorder/tick.py:308` passes `time_budget_s=30`.

**Test** `tests/test_runner.py::test_normalize_drains_multiple_batches_in_one_call` — 1,200
trivially parseable `kalshi_events` raw rows with `batch=500`.

RED: `assert 500 == 1200`. GREEN: 1,200.

## I5 — WebSocket ticker selection ordered by latest quote volume

**Changed** `harness/venues/kalshi/ws.py:44-58`. `select_ws_tickers` now builds a subquery of the
max `fetched_at` per `venue_market_id`, joins it back to `venue_quotes` to take that row's
`volume_24h` (aggregated with `max` so a tie on `fetched_at` cannot duplicate a market), left
joins it to `venue_markets`, and orders by `volume_24h desc nulls last, last_seen_at desc`.

**Test** `tests/test_kalshi_ws.py::test_select_ws_tickers_orders_by_latest_quote_volume` — three
eligible markets, one with the highest latest volume but a lower earlier volume, one with a huge
*earlier* volume and a small latest one, one with no quotes at all. Asserts the order is
`["K-BUSY", "K-QUIET", "K-NONE"]` and that `cap=1` returns `["K-BUSY"]`.

RED: `ImportError: cannot import name 'is_stale'` (collection error on the shared module).
GREEN: passes, and the earlier-volume decoy confirms the "latest quote" part of the ordering.

## I6 — a rejected or unacknowledged subscription reconnects

**Changed** `harness/venues/kalshi/ws.py`.

- New pure helper `should_reconnect(acks_received, elapsed_s, error_msg)` (`ws.py:27-31`):
  true if an error frame arrived, or if no `subscribed` ack has arrived within
  `SUBSCRIBE_ACK_TIMEOUT_S = 15`.
- The recv loop was extracted into `_recv_loop` (`ws.py:107-140`). An `{"type": "error", ...}`
  frame is logged at warning with its text (`ws.py:129-131`) instead of falling silently into
  `WsSink.handle`; both conditions raise `_Reconnect`, which the existing
  `except Exception` backoff path in `run_forever` handles (`ws.py:161-164`).
- `_resubscribe` (`ws.py:88-102`) now advances `self._current` only when at least one
  `update_subscription` was actually sent, or when the diff was empty. With `_sids` empty nothing
  is sent and `_current` is left alone, so the next replan still computes a real diff.
- `run_forever` resets both `_sids` and `_current` on every reconnect (`ws.py:151`).

**Test** `tests/test_kalshi_ws.py::test_should_reconnect_on_missing_ack_or_error_frame` covers the
four cases (no ack past the window, no ack inside the window, acked and long-running, error frame).

## I7 — staleness watchdog on the recv loop

**Changed** `harness/venues/kalshi/ws.py`. New Settings field
`ws_stale_s: int = 180` (`harness/config/settings.py:21`). `RECV_TIMEOUT_S = 30.0` is now the
value passed to `ws_factory(..., timeout=...)` (`ws.py:77`), so the timeout accounting and the
socket agree. Consecutive `WebSocketTimeoutException`s are counted and reset on any received
frame (`ws.py:112-120`); `is_stale(consecutive_timeouts, recv_timeout_s, stale_s)` (`ws.py:34-36`)
decides when the silence forces a reconnect.

**Test** `tests/test_kalshi_ws.py::test_is_stale_counts_consecutive_recv_timeouts` — six timeouts
at 30 s is stale against a 180 s budget, five is not, zero is not.

## I8 — dropped odds rows are counted

**Changed** `harness/normalize/odds.py:13-17` adds
`OddsUpsertResult(inserted, dropped_unknown_game, dropped_unresolved_team)`; `upsert_odds_rows`
(`odds.py:57-82`) returns it and counts both `continue` paths.
`harness/normalize/runner.py:68-71` accumulates them into
`ctx["odds_dropped"] = {"unknown_game": N, "unresolved_team": N}`, and
`harness/recorder/tick.py:331` writes `notes["odds_dropped"]`.

**Tests** `tests/test_normalize_odds.py`: the existing assertions became `.inserted == 6` /
`.inserted == 0`, and a new `test_upsert_counts_dropped_rows_by_reason` feeds two rows with an
unknown event id and two with an unresolvable team name and asserts `(0, 2, 2)`.

RED: `AttributeError: 'int' object has no attribute 'inserted'`. GREEN: both pass.

## I10 — Kalshi's abbreviated NFL event titles

**Changed**:
- `harness/matching/teams.py:42-43` seeds a new alias source `espn_abbr_name` with the normalized
  `f"{abbreviation} {name}"`, e.g. `no saints`, `gb packers`, `pit steelers`.
- `espn_abbr_name` added to `PRIORITY` right after `espn_display` (`teams.py:12-13`) and to
  `KALSHI_SOURCES` (`harness/matching/kalshi.py:13`).
- `harness/matching/aliases_manual.yaml` gains four `nfl: kalshi_name:` entries:
  `"New York J": 20` (Jets, id confirmed from the seeded `teams` table in `harness_dev`),
  `"New York G": 19`, plus `"JAC Jaguars": 30` and `"WAS Commanders": 28`.

**Note on the two extra aliases.** The ruling named only "New York J" and "New York G". The live
before-run showed `unresolved: WAS Commanders` (which the review itself lists under I10) and
`unresolved: JAC Jaguars` still failing after the `espn_abbr_name` pass, because Kalshi
abbreviates Jacksonville `JAC` and Washington `WAS` while ESPN uses `JAX` and `WSH`. These are the
same `<ABBR> <Nickname>` convention I10 is about, so I added them; without them the finding would
have been left half-implemented. Flagged here rather than left silent.

**Tests** in `tests/test_teams.py`:
- `test_abbr_name_alias_resolves_kalshi_abbreviated_title` — after seeding the NFL fixture (which
  includes the Saints), `resolve_team(session, "nfl", "NO Saints")` returns `(18, "espn_abbr_name")`.
- `test_shipped_manual_aliases_cover_kalshi_nfl_title_conventions` — loads the shipped
  `aliases_manual.yaml` and asserts all four NFL manual entries resolve.

RED on the second test: `assert (None, '') == (30, 'manual:kalshi_name')`. GREEN after the yaml edit.

---

## Live match-report, `harness_dev`

Commands run with
`DATABASE_URL=postgresql+psycopg://harness:harness@localhost:5433/harness_dev` and
`ODDS_API_KEY_FILE=$PWD/secrets/odds_api_key`:
`harness seed-teams`, then `harness reprocess --family kalshi_markets --from-raw-id 0`, then
`harness match-report`.

| sport | venue markets | matched before | matched after |
|---|---|---|---|
| nfl | 772 | 648 (83.9%) + 2 fuzzy | 682 (88.3%), 0 fuzzy |
| ncaaf | 3235 | 2807 (86.8%) | 2843 (87.9%) |

Every `unresolved: <name>` reason in the NFL section is gone. All 90 remaining unmatched NFL
markets are `no game for pair` and belong to just four event tickers:

```
KXNFLSPREAD-26SEP13ARILAC   "Arizona vs Los Angeles: Spread"
KXNFLTOTAL-26SEP13ARILAC    "Arizona vs Los Angeles: Total Points"
KXNFLSPREAD-26SEP13DALNYG   "Dallas vs New York: Spread"
KXNFLTOTAL-26SEP13DALNYG    "Dallas vs New York: Total Points"
```

**NFL did not reach 90%, and cannot from these rulings alone.** Those four titles carry a bare
city that names two teams each. `espn_location 'los angeles'` and `'new york'` are now correctly
on the ambiguous sentinel, but stale *learned* `kalshi_name` aliases (`'los angeles' -> 14` Rams,
`'new york' -> 20` Jets) survive, are not an `espn_` source, and still resolve — to the wrong
team, hence `no game for pair`. That is finding **I9**, which was explicitly not in this wave's
scope. Fixing it needs either a conflict guard on learned aliases or use of the ticker
abbreviation (`ARILAC`, `DALNYG`) to disambiguate. The ceiling without I9 is 682/772 = 88.3%,
which is what this wave reached. There is no regression: the same four event tickers were
unmatched before the wave (92 `no game for pair`, of which 90 were these).

The NCAAF Troy markets (`26SEP19TROYMIZZ`, `26SEP12ALSTTROY`) are still `no game for pair`, but
this is now a genuine future-week miss rather than the C3 resolution bug: `troy` resolves
correctly to 2653 Troy Trojans, and `games` holds exactly one Troy fixture (2026-09-05), so no
Sep 12 or Sep 19 Troy game exists to match against. This is the case the review's recommendation
4 asks `match-report` to separate; that recommendation was not part of this wave.

## Full suite tail

```
........................................................................ [ 70%]
..............................                                           [100%]
102 passed in 10.83s
```

Run with `DATABASE_URL_TEST=postgresql+psycopg://harness:harness@localhost:5433/harness_test`
against real Postgres, on a pristine tree, with no other pytest process running.
