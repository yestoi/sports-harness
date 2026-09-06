# Phase 1 final review — `phase1-normalize-match` (9552e18..fa806c3)

Reviewer: senior code review, whole-branch pass. Read-only. Evidence from the working tree,
the plan/spec, and read-only queries against `harness_dev` on `harness-pg-test`.

---

## Strengths

- **The rebuild story actually holds.** Normalizers are pure functions over one raw body,
  every write is an idempotent upsert, and `normalize_state` is a per-family watermark. The
  `RETURNING`-based counting works around psycopg3's `rowcount = -1` consistently in all five
  insert paths (`odds.py:74`, `kalshi.py:110/122/133`, `ws_sink.py:81`). Reprocess-idempotency
  is covered by a real test against real Postgres.
- **Game identity and cross-source dedupe are right.** `find_game_by_pair` with a ±1 day
  window is the correct answer to the UTC-boundary problem, and the data confirms it: 372 of
  3457 matched markets have a ticker date one day off the kickoff date, zero are off by more,
  and there are **no duplicate games for the same pair within two days**. The 48 duplicate
  pairs in `games` are all legitimate NFL divisional rematches weeks apart.
- **Match integrity invariants are clean in live data.** Zero `venue_markets` whose
  `side_team_id` is not one of its game's two teams; zero `odds_snapshots` whose
  `outcome_team_id` is outside its game; zero matched moneyline/spread markets with a null
  side; zero orphan quotes.
- **The prefix rule is safer than it looks.** The two-hit veto in `side_team_id_for`
  (`kalshi.py:119-123`) makes the rule game-scoped, so the dangerous NCAAF families all
  resolve correctly or abstain: "louisiana" against Louisiana / Louisiana Tech gives two hits
  and returns `None`; "north carolina" against UNC / NC A&T likewise. I could not construct a
  single-wrong-hit case for a real FBS pair, and the live data has none.
- **Sport-scoped keys, apostrophe stripping, the `: Spread` / `: Total` suffix strip with its
  "colon must follow the vs" guard, the raw ESPN status fallback, the sink rollback path, and
  the `ws_lookahead_hours` setting are all implemented as ruled.**
- **Secrets and logging are clean.** The Odds API key is redacted in `FetchResult.url` and is
  never written into `raw_responses.params`; the RSA private key is read from a file and only
  ever used to produce headers; compose mounts all three secrets read-only; `.env` and
  `secrets/*` are gitignored.
- **Tests are honest.** Real Postgres, mocked HTTP via respx, fixed clocks, no live network
  and no dependence on today's date. The test DB (`harness_test`) is now distinct from the
  manual-run DB (`harness_dev`), which closes the Task 7 note.
- The WebSocket sink's `_last_seq` is keyed by `sid`, of which there are exactly two, so it
  is bounded. The smoke run's 0 sequence gaps over 123k deltas is a real result.

---

## Issues

### Critical

**C1. A single database-level error silently discards a whole normalize batch and rewinds the
watermark — permanently, with a healthy status.**
`harness/normalize/runner.py:94-102`. Each row is processed inside `try/except`, but there is
no savepoint. Once any statement raises a database error, the Postgres transaction is aborted;
every later row in that family also fails, and the `session.commit()` at line 102 does not
raise — it silently issues a ROLLBACK. I verified this against the test Postgres:

```
inserted 1
row failed 2 DataError
row failed 3 InternalError
commit OK
rows now: 0
```

Consequences, all silent:
- Every successfully normalized row in that batch is discarded.
- `state.last_raw_id` is rolled back, so the next tick reprocesses the same batch and hits the
  same poison row. For a deterministic error this repeats forever and that family never
  advances again.
- `counts[family]` still reports the rows that "succeeded", so `runs.notes.normalized` shows
  nonzero progress.
- `normalize_errors` is written to run notes but does not feed `ctx["errors"]` or
  `ctx["warnings"]` (`tick.py:307-321`), so run status stays `ok` and `/healthz` returns 200.
- If the poisoned family is `espn` (first in `FAMILIES`), the rollback also discards the raw
  responses that `maybe_tick` flushed but had not yet checkpointed — up to 50 raw rows of
  primary data lost per tick.

Fix: wrap each `_handle` call in `session.begin_nested()` and roll back only that savepoint on
error; escalate a nonempty `normalize_errors` into `ctx["errors"]` (or at minimum
`ctx["warnings"]`) so status and `/healthz` reflect it; and include `repr(exc)` in the note so
a poison row is diagnosable.

**C2. `reprocess --truncate` permanently destroys WebSocket trades.**
`harness/normalize/runner.py:16` and `:109`. `NORMALIZED_TABLES` includes `venue_trades`, but
that table holds both REST rows (`source='rest'`, rebuildable) and WebSocket rows
(`source='ws'`, `raw_id IS NULL`), which exist nowhere in `raw_responses`. Truncating is
irreversible. `orderbook_events` was correctly left out of the list; `venue_trades` is the same
kind of table and was not. The existing test passes only because the fixture has no WS trades.

Fix: replace the blanket truncate of `venue_trades` with `delete from venue_trades where
source = 'rest'`, or exclude it from `NORMALIZED_TABLES` and delete REST rows by `raw_id >=
from_raw_id`. Everything else in the list is correct and complete for a rebuild.

**C3. ESPN alias seeding silently overwrites colliding alias keys, making real FBS teams
unmatchable — and the failure is indistinguishable from a benign one.**
`harness/matching/teams.py:20`. `_upsert_alias` uses `on_conflict_do_update(set_={"team_id":
team_id})`, so when two teams normalize to the same alias key the last one seeded wins. ESPN's
`groups=80` returns 792 NCAAF teams including D-II/D-III programs, so namesakes exist.
Measured in `harness_dev`:

| alias key | source | resolves to | should be |
|---|---|---|---|
| `troy` | espn_location | 3237 Troy Vikings | 2653 Troy Trojans |
| `charlotte` | espn_location | 3253 Charlotte Saints | 2429 Charlotte 49ers |
| `osu` | espn_abbr | 3161 Ohio State Newark Titans | 194 Ohio State Buckeyes |

`espn_location` outranks `espn_short` in `PRIORITY`, so the wrong row wins. The live effect is
concrete: game 422 is Ole Miss vs **Charlotte 49ers** on 2026-09-12, and every
`KXNCAAFGAME/SPREAD-26SEP12CHARMISS-*` market is `unmatched` with reason `no game for pair`.
Same for `26SEP19CHARAPP`, `26SEP19TROYMIZZ`, `26SEP12ALSTTROY`. This never self-heals:
`match_event` only learns a `kalshi_name` alias after a successful pair lookup, which is
exactly what is failing. And because the reason string is `no game for pair` — the same string
produced by legitimate future-week games — it is invisible in `match-report`.

The NFL variant is a latent wrong-answer rather than a miss: `espn_location 'new york'` maps
only to the Jets (20) and `'los angeles'` only to the Rams (14), so the Giants and Chargers are
unreachable by location and a Kalshi title reading "New York" would resolve confidently to the
Jets. Five location collisions and two abbreviation collisions exist today.

Fix: make seeding collision-aware. During `seed_teams_from_espn`, detect keys claimed by two
different `team_id`s and drop the key entirely (or keep it only for the higher-tier team)
rather than letting the last write win, so resolution falls through to a more specific source
or a manual alias. Add the dropped-ambiguous keys to `match-report`. Add manual
`kalshi_name`/`odds_api` aliases for Troy and Charlotte now.

### Important

**I4. The runner can fall behind and only recovers in quiet hours.**
`harness/normalize/runner.py:82`, called once per tick from `tick.py:299`. `batch=500` per
family per tick. Measured in `harness_dev`: one run already stored **554** `/markets/trades`
raw rows. Any tick that produces more than 500 rows in one family leaves a permanent deficit
that only drains on a tick producing fewer than 500 — i.e. overnight. On a CFB Saturday the
`kalshi_trades` family will lag raw for the whole slate, which is precisely the window phase 2
cares about. `ladder_cap_per_tick = 400` keeps `kalshi_orderbook` under the limit, but with
only 100 rows of headroom.
Fix: drain each family in a loop until a batch returns fewer than `batch` rows, bounded by a
wall-clock budget so a tick cannot overrun; log when the budget stops a drain.

**I5. `select_ws_tickers` picks an arbitrary, unstable 500 markets.**
`harness/venues/kalshi/ws.py:31` orders by `VenueMarket.last_seen_at.desc()`. All markets are
refreshed in the same tick, so `last_seen_at` is a near-total tie: **8 distinct values across
4007 rows**. Postgres therefore returns an arbitrary subset for the `[:500]` slice, and the
subset can change between five-minute replans, churning `add_markets`/`delete_markets` for
hundreds of tickers with no real change. Measured: **2314** matched markets fall inside a single
Saturday 24-hour window against a cap of 500, so 78% are dropped by a tie-break. The plan's
prose (line 1872) specifies "ordered by `volume_24h` of the latest quote desc"; the plan's own
code block (line 2035) contradicts it, and the implementation followed the code. The plan is
wrong here, and the prose is right.
Fix: order by the latest quote's `volume_24h` desc, with `last_seen_at` only as a tiebreak.

**I6. A rejected WebSocket subscription disables the recorder permanently and silently.**
`harness/venues/kalshi/ws.py:62-68`. `_resubscribe` skips sending when `self._sids` is empty
(`:65`) but still sets `self._current = list(wanted)` (`:68`). So if the initial subscribe never
produces a `subscribed` ack, every later replan computes an empty diff, sends nothing, and the
process runs forever recording zero messages while looking alive. The cold-start path in
compose reaches this directly: `app-ws` depends only on `postgres`, so on a fresh deploy
`venue_markets` is empty, `select_ws_tickers` returns `[]`, and the subscribe carries an empty
`market_tickers` list. `restart: on-failure` does not help because the process never exits.
Non-`subscribed` control frames (including `{"type": "error", ...}`) fall through to
`WsSink.handle`, which returns `None` without logging.
Fix: log every unrecognised control message at warning level; do not advance `self._current`
when nothing was sent; treat an empty ticker list or an empty `_sids` after the subscribe
window as a reconnect condition with backoff.

**I7. No staleness watchdog on the WebSocket loop.**
`harness/venues/kalshi/ws.py:87-89`. A `WebSocketTimeoutException` just `continue`s. A
half-open socket or a server that keeps the TCP connection alive while sending nothing is never
detected, and the loop spins for the rest of the three weeks. Fix: record the timestamp of the
last data message and force a reconnect after, say, two minutes of silence while subscriptions
are non-empty.

**I8. Dropped odds rows are not counted anywhere.**
`harness/normalize/odds.py:65` (no game for the event id) and `:74` (team name unresolved) both
`continue` silently, and `upsert_odds_rows`'s return value is discarded by the runner
(`runner.py:69`). Phase 2 has no way to tell a book that quoted nothing from a book whose rows
were dropped. `GamesResult.unresolved` covers only featured-endpoint team names, and only the
name, not a count. Fix: return per-reason drop counts, aggregate them into `ctx`, write them to
run notes, and print them in `match-report`. Reprocess is the remedy once an alias is added, so
the counts are actionable.

**I9. Alias learning writes global aliases from game-scoped and fuzzy evidence, unlabelled.**
`harness/matching/kalshi.py:109/126/127` and `harness/matching/games.py:26/31`.
`side_team_id_for` resolves a side name against only the two teams in one game, then persists
the answer as a sport-wide `kalshi_name` (and `kalshi_uuid`) alias. Live examples:
`nfl kalshi_name 'new york' -> 20 (Jets)` and `'los angeles' -> 14 (Rams)`. Both are genuinely
ambiguous names that happened to be unambiguous inside one game. Today they are harmless
because `key in names` still fires for the right team in a Giants or Chargers game, but a
Kalshi title reading "New York" would now resolve to the Jets with confidence 1.00, and if the
Jets played the same opponent within a day the market would be matched to the wrong game and
frozen by `upsert_venue_markets`'s never-downgrade rule. Separately, `_resolve_odds_name`
persists fuzzy hits (ratio ≥ 0.90, runner-up < 0.85) under the plain `odds_api` source, so a
wrong fuzzy hit is indistinguishable from an exact one, is logged only at INFO, and survives
`reprocess --truncate`. I found no wrong fuzzy alias in the current data — the runner-up guard
is doing its job — but there is no way to notice one.
Fix: never write an alias whose key already maps to a different `team_id` (log the conflict
instead); label learned-fuzzy aliases with a distinct source (`odds_api_fuzzy`) or a provenance
column; list them in `match-report`.

**I10. Kalshi's abbreviated NFL event titles are systematically unresolved.**
Live `harness_dev` unmatched reasons include `unresolved: NO Saints`, `LV Raiders`,
`PIT Steelers`, `GB Packers`, `DET Lions`, `MIN Vikings`, `SEA Seahawks`, `WAS Commanders`,
`CAR Panthers`, `CLE Browns`, `CIN Bengals`, `IND Colts`, `PHI Eagles`, `New York J` — roughly
26 markets and a recognisable convention (`<ABBR> <Nickname>`). This is most of the gap between
the reported 84% NFL match rate and full coverage. It is the Task 8 alias pass, but it is a
known pattern, not an unknown, and three weeks of NFL data depend on it.
Fix: add the manual aliases (or a rule that strips a leading two-or-three-letter abbreviation
when the remainder matches a team nickname) before deploying.

### Minor

- `harness/normalize/espn.py:60-62` and `harness/matching/games.py:70-72` both write
  `kickoff_utc`, and `upsert_games_from_odds` also rewrites `home_team_id`/`away_team_id`. When
  ESPN and The Odds API disagree — routinely, on neutral-site games — the row flips on every
  tick and inflates `GamesResult.updated`. Matching is unaffected (the pair is unordered), but
  it is needless write churn. Pick one source of truth per column.
- `MarketsResult.updated` is never incremented (`normalize/kalshi.py:23`), and a fuzzy row that
  later hits a missing-title pass keeps its stored status while the tally counts it unmatched.
  `match-report` reads the table, not the tally, so this is cosmetic.
- `normalize_errors` entries carry only `{family: raw_id}` with no exception text.
- `harness/venues/kalshi/ws.py:48-56`: the fallback URL is only tried on
  `WebSocketBadStatusException`; a DNS or timeout failure on the primary skips the fallback and
  goes straight to backoff.
- `_EVENTS` (`runner.py:17`) is process-global and never evicted. Practically bounded at a few
  thousand event dicts (a few MB) for a season, and tests clear it via an autouse fixture, so
  this is acceptable — but it deserves the comment it does not have.
- `resolve_fuzzy` (`teams.py:73`) rescans every `espn_display`/`espn_location` alias and runs
  `SequenceMatcher` per candidate on every call, with no cache in the `upsert_games_from_odds`
  path, so a permanently unresolvable name pays the full scan on every raw row.
- `reprocess --truncate` deliberately keeps `teams` and `team_aliases`, so a bad learned alias
  survives a rebuild. That matches the ruling, but it should be stated in the runbook alongside
  the manual `delete from team_aliases where source in (...)` recipe.
- `match-report`'s unresolved-Odds-API-names section is not broken out per sport.
- `tests/test_kalshi_ws.py` covers only `WsSink` and `diff_subscriptions`. There is no test for
  `WsRecorder._connect`, `_subscribe`, `_resubscribe`, or the empty-ticker path — which is where
  I6 lives.
- `harness seed-teams` (`cli.py:78`) fetches with bare `urllib.request.urlopen` instead of the
  project's `HttpClient`, so it gets no retry and no param redaction.
- Stale comment on the commit-count test; unused imports in `tests/test_games.py`.

---

## Deferred-minor triage

| Item | Verdict |
|---|---|
| T1: stale comment on the commit-count test | defer to phase 2 |
| T4: unused imports in `tests/test_games.py` | defer to phase 2 |
| T5: no test for the exact+fuzzy mixed path in `match_event` | defer to phase 2 |
| T6: `MarketsResult.updated` never incremented; fuzzy tally mismatch | defer to phase 2 (report reads the table, not the tally) |
| T7: `normalize_errors` lack exception text | **fix before merge** — required to diagnose C1 |
| T7: unresolved-teams section not per sport | defer to phase 2 |
| T7: separate DB for manual runs | closed — `harness_dev` exists and `conftest` only touches `harness_test` |
| T10: batched commit can drop up to 100 WS messages on commit failure, no gap record | defer to phase 2 — documented best-effort durability, and `WsSink.errors` counts it |
| T10: `select_ws_tickers` orders by `last_seen_at` rather than `volume_24h` | **fix before merge** — see I5, it is not cosmetic |

---

## Recommendations

1. Fix C1, C2, C3 and I4, I5, I6 before merging. They are the ones that bite unattended: a
   silent normalizer stop, an irreversible truncate, silently unmatchable teams, a backlog that
   only drains overnight, an arbitrary WebSocket subscription set, and a WebSocket recorder that
   can run for three weeks recording nothing.
2. Add three cheap operational guards while you are in there: fold `normalize_errors` into run
   status so `/healthz` turns red; add a `depends_on`-equivalent for `app-ws` (either a compose
   healthcheck on a populated `venue_markets`, or the reconnect-on-empty behaviour from I6); and
   have `match-report` print dropped-odds counts and ambiguous-alias keys.
3. Do the I10 NFL alias pass before Week 2 rather than after. The convention is already visible
   in the data.
4. Add a `match-report` line that separates `no game for pair` where **neither** team has any
   game in the window (a real future-week miss) from the case where a game exists for one of the
   resolved teams (a resolution bug like C3). That single line would have surfaced Troy and
   Charlotte on day one.
5. Note for phase 2: `upsert_venue_markets` never downgrading a `matched` row is the right
   default given the data (no duplicate games within two days, no ticker date off by more than
   one), but it should be paired with a periodic audit that re-runs `match_event` for matched
   rows and reports disagreements, rather than a silent freeze.

---

## Assessment

**Ready to merge? With fixes.**

The architecture is sound and the live results are real: pure normalizers, a genuine rebuild
path, correct ±1 day pair matching verified against 3457 matched markets, and clean referential
invariants in production data. The team-identity model, the suffix strip, and the two-hit veto
on the prefix rule are all good calls, and the WebSocket smoke result stands.

But three of the four failure modes this branch is calibrated against are present. The
normalizer can stop permanently while reporting success (C1). An operator rebuild destroys
WebSocket trades that no raw row can recreate (C2). Real FBS teams are silently unmatchable
because a D-III namesake won an alias key, and the resulting reason string is the same one
legitimate misses produce (C3). None of these are visible from `/healthz` or `match-report`
today, which is exactly what makes them unsuitable for three weeks unattended.

Fix C1-C3 and I4-I6, re-run the suite and one live tick, and this is ready. The remaining
Importants and all Minors can follow into phase 2.
