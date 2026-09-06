# SDD ledger — plan: docs/superpowers/plans/2026-09-06-phase1-normalize-match.md
Spec: docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md (v2). Branch: phase1-normalize-match (from main @ 9552e18). Test DB: DATABASE_URL_TEST=postgresql+psycopg://harness:harness@localhost:5433/harness_test (container harness-pg-test). Real secrets exist in secrets/ (Odds API key; Kalshi production key id + PEM, unfunded). Compose stack running on the Mac (ports 8080, pgdata) — do not run docker compose in tasks.

Ruling: implement on branch in place (same rationale as phase 0). Cost if wrong: none to main.

## Pre-flight scan
| Pair / task | Produces vs consumes | Finding |
|---|---|---|
| T1 fetch_events_all ↔ T7 runner family kalshi_events (endpoint "/events") | endpoint string "/events", params {series_ticker} | consistent |
| T2 Team(sport,id) composite PK ↔ T3 teams.py (index_elements ["sport","id"]), T4 espn.py `session.get(Team,(sport,h))`, T5 kalshi.py `session.get(Team,(sport,tid))` | composite key everywhere | consistent after 2026-09-06 patch |
| T2 TeamAlias(sport,source,raw_name) ↔ T3 _upsert_alias index_elements | match | consistent |
| T3 resolve_team(session, sport, raw_name, sources) -> (id, src) ↔ T4, T5, T6 | same signature; `kalshi_uuid` raw lookup branch | consistent |
| T3 test count `== 6` NFL teams ↔ T3 fixture keep-set (6 names) | Giants, Rams, Saints, Chiefs, Seahawks, Patriots | consistent |
| T3 fuzzy id 2029 (Arkansas-Pine Bluff) ↔ verified ESPN id | 2029 | consistent |
| T4 find_game_by_pair(session, sport, a, b, date, tolerance) ↔ T5 match_event, T6 | same | consistent |
| T4 test expects Rams home 14 / Giants away 19 ↔ ESPN ids | verified | consistent |
| T5 MarketClass/EventMatch ↔ T6 upsert_venue_markets | field names match | consistent |
| T5 side_team_id_for prefix rule ↔ T6 test expecting NYG moneyline matched via "New York G" | prefix rule ≥6 chars unique within game | consistent after patch |
| T6 upsert_venue_markets(session, sport, markets, events_by_ticker, raw_id, fetched_at) ↔ T7 runner `_handle` | same | consistent |
| T6 test EVENTS "Denver vs Kansas City" → Broncos absent from NFL fixture | expects `unresolved: Denver` | consistent (Broncos not in keep-set) |
| T7 normalize_new(session, batch, ctx) ↔ tick integration | same | consistent |
| T7 NORMALIZED_TABLES truncate includes games; team_aliases kept | intended | note: truncating games orphans venue_markets.game_id → they are re-matched on reprocess because upsert_venue_markets re-tries non-matched rows; but rows with status matched keep stale game_id. Ruling: reprocess with truncate must also reset venue_markets (it is in NORMALIZED_TABLES) — yes, `venue_markets` is listed. OK |
| T9 sign_request ↔ T10 WsRecorder._headers | same | consistent; WS URL verified live |
| T10 `subscribed` ack sid inside msg ↔ ws.py | patched to read msg.msg.sid | consistent |
| T10 deps websocket-client, cryptography | installed in venv already for the probe; pyproject must list them | task adds |
| Global: sync only; UTC; raw never modified | T7 truncate never touches raw_responses | consistent |
Task 1: dispatched (base 9552e18, implementer impl-p1-task-1, model sonnet)
Task 1: reported DONE (commit 81f15fe); reviewer dispatched
Task 1: complete (commits 9552e18..81f15fe, review clean). Minor (deferred): stale comment on commit-count test. Fact: NFL total title "Will there be over 63.5 points scored?"; NCAAF "Over 73.5 points scored"; totals carry no custom_strike.
Task 2: dispatched (base 81f15fe, implementer impl-p1-task-2, model sonnet)
Task 2: reported DONE (commit 1a07121); reviewer dispatched
Task 2: complete (commits 81f15fe..1a07121, review clean)
Task 3: dispatched (base 1a07121, implementer impl-p1-task-3, model sonnet)
Task 3: plan defect — normalize_name turned apostrophes into spaces ('St. John''s' → 'st john s'). Ruling: delete apostrophes before the non-alnum strip; adds Hawai'i assertion. Cost if wrong: none (apostrophes carry no identity).
Task 3: reported DONE (commit 7f093d7); reviewer dispatched
Task 3: complete (commits 1a07121..7f093d7, review clean; apostrophe ruling applied). Note: manual aliases win regardless of `sources` subset — intended.
Task 4: dispatched (base 7f093d7, implementer impl-p1-task-4, model sonnet)
Task 4: reported DONE (commit b377e5c); reviewer dispatched. Minor (deferred): unused imports in tests/test_games.py from the brief.
Task 4: review Important (plan-mandated): unknown ESPN status fell back to 'scheduled' instead of raw lowercased. Ruling: fix now; cost if wrong: none.
Task 4: fix round 1/5 (1 addressed, 0 open — raw status fallback; commits b377e5c..e7b2658)
Task 4: complete (commits 7f093d7..e7b2658, review clean after 1 fix round)
Task 5: dispatched (base e7b2658, implementer impl-p1-task-5, model sonnet)
Task 5: reported DONE (commit 2331636); reviewer dispatched
Task 5: complete (commits e7b2658..2331636, review clean). Minor (deferred): no test for the exact+fuzzy mixed path in match_event (logic hand-traced correct). Fact: NCAAF spread titles lack the trailing "?"; regex tolerates it.
Task 6: dispatched (base 2331636, implementer impl-p1-task-6, model sonnet)
Task 6: DONE_WITH_CONCERNS (commit 839648e). Plan defect: `.rowcount` is -1 for INSERT ... ON CONFLICT DO NOTHING under psycopg3. Ruling: accept implementer's `.returning(pk)` + len(fetchall()) counting; carry the same pattern into Task 7/10 briefs. Cost if wrong: none. Reviewer dispatched.
Task 6: complete (commits 2331636..839648e, review clean). Minor (deferred): MarketsResult.updated never incremented; fuzzy row with a later missing-title pass keeps its stored status while the tally counts it unmatched.
Task 7: dispatched (base 31c8434, implementer impl-p1-task-7, model sonnet)
Task 7: live run revealed plan defect — Kalshi spread/total event titles carry ': Spread' / ': Total' suffixes so parse_event_title fails on them (57+53+52+46+46 unmatched). Ruling: strip a trailing ':<label>' suffix in parse_event_title (Task 5 file) within Task 7, add test + manual aliases (Appalachian St. → App State 2026, Louisiana-Monroe → UL Monroe), reprocess, re-report. 'no game for pair' (340) is expected for future-week games. Cost if wrong: none.
Task 7: reported DONE (commit cf1faa5); reviewer dispatched. Live match rates after suffix fix + 3 aliases: NFL 84.2%, NCAAF 86.8% matched; remainder mostly future-week 'no game for pair'. Note (deferred): pytest's db_session fixture drops the same DB used for CLI real runs on 5433 — use a separate DB for manual runs going forward.
manual-run DB: postgresql+psycopg://harness:harness@localhost:5433/harness_dev
Task 7: complete (commits 31c8434..cf1faa5, review clean). Minor (deferred): normalize_errors lack exception text; unresolved-teams section not per sport.
Task 8: Ruling — operational task requiring game-day data on the deployed instance; runbook written with today's baseline (commit follows); the alias pass itself is deferred to after Week 1 games. Cost if wrong: none.
Task 9: dispatched (base = runbook commit, implementer impl-p1-task-9, model haiku)
Task 9: reported DONE (commit cd01e32); reviewer dispatched
Task 9: complete (commits ce87184..cd01e32, review clean)
Task 10: dispatched (base cd01e32, implementer impl-p1-task-10, model sonnet). Live smoke uses harness_dev DB and real Kalshi key; no docker.
Task 10: reported DONE (commit 7b27916); reviewer dispatched. Smoke: WS URL confirmed, 500 snapshots / 123,595 deltas / 0 gaps / 410 ws trades in ~4 min. Ruling: accept `lookahead_hours` param + `ws_lookahead_hours` setting (default 24). Deferred to final review: select_ws_tickers orders by last_seen_at (brief code) while prose says volume_24h — prefer volume ordering.
Task 10: review Important — WsSink long-lived session has no rollback path; an exception in handle() poisons all later writes. Ruling: fix now (try/except with rollback + error counter in handle and _maybe_commit; test with a flaky execute). Cost if wrong: none. Fix round 1 sent.
Task 10: fix round 1/5 (rollback path + dropped-trade warning amended into fa806c3; suite 90 green, controller-verified); re-review dispatched
Task 10: complete (commits cd01e32..fa806c3, review clean after 1 fix round). Deferred: batched commit + rollback means a commit failure can drop up to 100 messages without a gap record (best-effort durability, documented); select_ws_tickers ordering by last_seen_at vs volume_24h prose.
All 10 tasks complete. Final whole-branch review dispatched (opus) on 9552e18..fa806c3.
Final review (opus): 3 Critical, 7 Important, 12 Minor; verdict merge with fixes. Report: final-review-report.md.
Ruling: single fix wave covers C1 (per-row SAVEPOINT via begin_nested; last_raw_id advances only past committed rows; poison rows recorded and skipped), C2 (reprocess --truncate deletes venue_trades WHERE source='rest' only; ws rows and orderbook_events preserved), C3 (alias key collisions across teams within a sport become an ambiguous sentinel team_id=-1 that resolvers ignore; match-report lists ambiguous aliases), I4 (runner drains up to 5000 rows/family per tick within a 30 s budget), I5 (select_ws_tickers orders by latest venue_quotes.volume_24h desc), I6 (no subscribed acks within 15 s or an error message → reconnect with backoff; _current only advanced when the update is sent), I7 (staleness watchdog: no message for 180 s → reconnect), I8 (upsert_odds_rows returns inserted+dropped counts; dropped recorded in notes.normalized), I10 (NFL city-code prefixes NO/GB/TB/KC/LV/NE/JAX/SF/NY/LA expanded in normalize_name; manual aliases for the ~26 unresolved NFL Kalshi names), plus triage: normalize_errors carry exception text. Deferred to phase 2: I9 (label learned aliases by evidence), remaining minors. Cost if wrong: larger re-review diff; each change reduces silent failure.
Final fix wave: commit ab96cf4, 102 tests (controller-verified). Deviations accepted: JAC/WAS manual aliases; NFL ceiling 88.3% pending I9 (stale learned 'los angeles'/'new york' aliases exist only in harness_dev; fresh deployments learn under the sentinel). Re-review dispatched.
User chose: fix the two WS issues then merge. Ruling: scoped fix — (1) select_ws_tickers scopes latest-quote lookup to in-window markets via LATERAL limit 1 on ix_quotes_market_fetched; (2) backoff resets only after the first data message; (3) WsSink resets _last_seq on reconnect (sink.reset_sequences()) to avoid synthetic gap rows. Cost if wrong: none.
WS fix round: commit 44df914 (106 tests), re-review clean. Merging to main.
