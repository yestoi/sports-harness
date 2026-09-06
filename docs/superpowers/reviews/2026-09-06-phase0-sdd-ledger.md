# SDD ledger — plan: docs/superpowers/plans/2026-09-06-phase0-recorder.md
Spec: docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md (v2). Branch: phase0-recorder (from main @ 663cfc1).

Ruling: implement on branch `phase0-recorder` in place, no separate worktree — fresh single-developer repo with only docs on main; a worktree directory would complicate the Docker Compose deploy path and pgdata volume. Cost if wrong: none to main (branch isolation), minor cleanup.

## Pre-flight scan
| Pair / task | Produces vs consumes | Finding |
|---|---|---|
| T1 Settings ↔ T10 Recorder, T11 build_recorder | tz_local, ladder_cap_per_tick, tick_budget_s, odds_api_base_url, odds_api_key(), odds_api_bookmakers, kalshi_base_url, espn_base_url, http_timeout_s, kalshi_sleep_s, heartbeat_s | all defined in T1 |
| T1 conftest ↔ T3 schema | db_session imports make_engine/create_schema/drop_schema lazily | ok; DB tests skip until DATABASE_URL_TEST |
| T3 models ↔ T8 store | RawResponse/Run/TradeWatermark/SourceState columns vs store kwargs | match |
| T4 FetchResult ↔ T5–T10 | status, headers, body, fetched_at, url, elapsed_s | match (T8 test constructs all six) |
| T5 OddsApiClient ↔ T10/T11 | (http, base_url, api_key, bookmakers); parse_event_ids_and_times -> [(id, dt)] | match; T9 alternates_due consumes same tuple |
| T6 EspnClient ↔ T10 | fetch_scoreboard(sport); T10 duplicates _PATH as _ESPN_PATH | minor duplication, deferred |
| T7 KalshiPublic/MarketSummary ↔ T9/T10/T11 | ctor (http, base_url, sleep_s, sleep); MarketSummary field order | match (T9 _ms helper order verified) |
| T9 select_trade_tickers ↔ T10 | (now, markets, watermarks: dict[str, (dt, Decimal)]) | match in code; T9 interface prose omits `now` first — code wins |
| T10 tests internal | first-tick credits 3*2+2 with alt called once; failure isolation expects 6 kalshi rows; budget=0 → skipped_trades | consistent after plan patch 663cfc1 |
| T11 health ↔ T8 finish_run | finished_at kwarg | present |
| T12 Dockerfile/compose ↔ T11 CLI | entrypoint `harness`, commands run/serve/init-db/tick-once | match |
| T1 deps | pytest-httpx listed but respx used | minor, deferred |
| Global constraints | UTC internals, CT only in cadence; secrets from file; bookmakers= param; job flags | no contradictions found |

Ruling: T10 keeps its own `_ESPN_PATH` map rather than importing the private `_PATH` from espn.py — avoids exporting a private; reviewer may flag duplication as minor. Cost if wrong: trivial refactor.
Task 1: dispatched (base fa4220d, implementer impl-task-1, model sonnet)
Task 1: complete (commits fa4220d..ea727e1, review clean; trailers verified by controller)
Task 2: dispatched (base ea727e1, implementer impl-task-2, model haiku)
Task 2: review found Important (plan-mandated): RedactionFilter ignores exc_info/exc_text so exception messages containing secrets leak. Ruling: fix now (redact formatted traceback into exc_text, clear exc_info) — spec §14 requires secrets never reach logs; cost if wrong: trivial. Minor (deferred): header-name regex only [A-Z-]; Authorization regex brittle on multi-token values.
Task 2: fix round 1/5 (1 addressed, 0 open — exc_info redaction; commits 459f872..b01ea62)
Task 2: complete (commits ea727e1..b01ea62, review clean after 1 fix round)
Task 3: dispatched (base b01ea62, implementer impl-task-3, model sonnet). Test DB: docker container harness-pg-test, DATABASE_URL_TEST=postgresql+psycopg://harness:harness@localhost:5433/harness_test
Task 2: fix round 2/5 opened (DeprecationWarning from pythonjsonlogger.jsonlogger import surfaced by Task 3's full-suite run; sent to impl-task-2)
Task 3: reported DONE (commit 8fd9321); reviewer review-task-3 dispatched
Task 2: fix round 2/5 (1 addressed, 0 open — deprecated import; commits 8fd9321..2217ec2). Task 2 final: complete.
Task 3: review Important — week_bounds accepts naive datetime (host-local interpretation). Ruling: fix now (raise ValueError on naive) — every later task calls ensure_partitions; cost if wrong: none. Fix round 1 sent to impl-task-3.
Task 3: fix round 1/5 (1 addressed, 0 open — naive datetime guard; commits 2217ec2..9a7d526)
Task 3: complete (commits b01ea62..9a7d526, review clean after 1 fix round)
Task 4: dispatched (base 9a7d526, implementer impl-task-4, model haiku)
Task 4: reported DONE (commit a05b77d); reviewer review-task-4 dispatched
Task 4: complete (commits 9a7d526..a05b77d, review clean). Minor (deferred): HttpClient not closed on FetchError path / no context manager; TimeoutException redundant in except tuple; typing.Callable vs collections.abc.
Task 5: dispatched (base a05b77d, implementer impl-task-5, model haiku)
Task 5: reported DONE (commit 93717bc); reviewer review-task-5 dispatched
Task 5: complete (commits a05b77d..93717bc, review clean). Minor (deferred): naive commence_time would be host-local; _i() TypeError on non-string header.
Task 6: dispatched (base 93717bc, implementer impl-task-6, model haiku)
Task 6: reported DONE (commit ca02d95); reviewer review-task-6 dispatched
Task 6: complete (commits 93717bc..ca02d95, review clean). Minor (deferred): NFL scoreboard test does not assert zero query params.
Task 7: dispatched (base ca02d95, implementer impl-task-7, model haiku)
Task 7: reported DONE (commit 975264c); reviewer review-task-7 dispatched
Task 7: review Important (plan-mandated): `_dec(...) or Decimal("0")` collapses "0.00" to "0" (Decimal zero is falsy). Ruling: fix now with explicit None check — numerically equivalent so no functional risk, but the falsy-Decimal idiom is a trap; cost if wrong: none. Minor (deferred): trailing sleep after terminating page; non-200 page appended (intended).
Task 7: fix round 1/5 (1 addressed, 0 open — Decimal zero scale; commits 975264c..b11ea87)
Task 7: complete (commits ca02d95..b11ea87, review clean after 1 fix round)
Task 8: dispatched (base b11ea87, implementer impl-task-8, model haiku)
Task 8: reported DONE (commit b3d0496); reviewer review-task-8 dispatched
Task 8: review Important (plan-mandated): finish_run default finished_at derives tz from started_at. Ruling: fix now → datetime.now(timezone.utc); cost if wrong: none. Minor (deferred): continuation-line indentation.
Task 8: fix round 1/5 (1 addressed, 0 open — finished_at UTC default; commits b3d0496..5d55c24)
Task 8: complete (commits b11ea87..5d55c24, review clean after 1 fix round)
Task 9: dispatched (base 5d55c24, implementer impl-task-9, model haiku)
Task 9: reported DONE (commit 6dcb552); reviewer review-task-9 dispatched
Task 9: complete (commits 5d55c24..6dcb552, review clean). Minor (deferred): quiet hours short-circuit NFL burst (moot: no NFL kickoffs 01:00–08:00 CT).
Task 10: dispatched (base 6dcb552, implementer impl-task-10, model sonnet)
Task 10: DONE_WITH_CONCERNS, no commit. Plan defect: HttpClient stamps fetched_at with real time while tests inject NOW=2026-09-09; partition for NOW's week does not cover today → CheckViolation on store_raw. Ruling: add `clock` kwarg to HttpClient (default real UTC now) used for fetched_at, and pass the shared test clock into HttpClient in test_tick; production behavior unchanged. Cost if wrong: small API change to Task 4's HttpClient (additive, defaulted). Alternative rejected: freezegun (mixes poorly with an advancing clock dict) and stamping fetched_at from the recorder (changes semantics).
Task 10: reported DONE after ruling (commit e4c8731); reviewer review-task-10 dispatched
Task 10: review approved with 2 Important (plan-mandated). Ruling: fix both now — (1) widen per-source try/except to cover fallback+parse so a DB/parse exception in one sport/series never aborts the tick; (2) Kalshi partial pagination (non-200 page) records an error and does not set source state, so it retries next heartbeat. Cost if wrong: extra retry pressure during a Kalshi outage (bounded: ~14 calls/30s). Minor (deferred): N+1 get_source_state for alternates; _latest_body index partially covering; maybe_tick type hint says Run | None.
Task 10: fix round 1/5 (2 addressed pending re-review — isolation scope, partial pagination; commits e4c8731..651cdf4); rereview-task-10 dispatched
Task 10: fix round 1/5 (2 addressed, 0 open; commits e4c8731..651cdf4). Re-review Low note parked — Ruling: a raised exception inside fetch_markets_all loses all pages before any parse in both pre- and post-fix code (pages are returned only on completion), so no regression; cost if wrong: one tick of summaries for that series.
Task 10: complete (commits 6dcb552..651cdf4, review clean after ruling + 1 fix round)
Task 11: dispatched (base 651cdf4, implementer impl-task-11, model sonnet). Step 5 real-API smoke deferred until secrets/odds_api_key exists.
Task 11: DONE_WITH_CONCERNS (commit d23d130). Smoke found: non-2xx (401 Odds API) leaves run status ok → healthz green with a dead key. Ruling: non-2xx from any source is recorded in notes.errors as "http <status>" so status=error and healthz 503s; cost if wrong: a transient post-retry 429/5xx flips one tick to error (acceptable, self-heals next heartbeat). Also: starlette/anyio TestClient DeprecationWarnings are library-internal → pytest filterwarnings for those modules. Fix round 1 sent to impl-task-11; reviewer review-task-11 dispatched on 651cdf4..d23d130.
Task 11: review approved. Important (carried to Task 12): compose must set stop_grace_period >= 120s on app-run so shutdown(wait=True) can finish a 100s tick. Minor (deferred): healthz seconds_since from started_at (defensible).
Task 11: fix round 1/5 (2 addressed pending re-review — non-2xx errors, warning filters; commits d23d130..3d35df3; controller confirmed suite warning-free); rereview-task-11 dispatched
Task 11: fix round 1/5 (2 addressed, 0 open; commits d23d130..3d35df3)
Task 11: complete (commits 651cdf4..3d35df3, review clean after 1 fix round)
Task 12: dispatched (base 3d35df3, implementer impl-task-12, model sonnet). Carried: stop_grace_period 120s on app-run. Local smoke with fake key expects healthz 503/error (dead key now counts as error). Step 6 NAS deploy is the user's; skipped.
Task 12: reported DONE (commit c6955fd); reviewer review-task-12 dispatched
Task 12: complete (commits 3d35df3..c6955fd, review clean). Minor (deferred): runbook lacks missing-secret-file (Docker creates a dir) warning.
All 12 tasks complete. Final whole-branch review dispatched (final-review, opus) on 663cfc1..c6955fd with triage list.
Final review (opus): 2 Critical, 10 Important, 8 Minor; verdict merge with fixes. Full report: final-review-report.md.
Ruling: single fix wave covers C1 (runbook chown 65534 + missing-file warning), C2 (alternates budget sub-limit 40s + skipped_alternates), I1+I8 (commit per source section and every 50 trade/ladder stores, expunge_all), I2 (watermark upsert on non-2xx with unchanged last_ts), I3 (trades cursor loop, max 5 pages), I4 (status `degraded` for secondary-source non-2xx; `error` for exceptions or primary-source non-2xx; healthz 503 only on stale/error), I5 (remaining only when header present), I6 (DB connect/statement timeouts), I7 (in-memory last-good-body cache), I9 (quiet hours suppressed while a game is in progress — spec §4.1 amendment), I10 (compose healthcheck on app-serve + runbook monitor note). Cost if wrong: larger diff for one re-review; behaviour changes are all in the direction of fewer silent failures.
Final fix wave: commit a310a11 (58 tests, pristine, controller-verified). Ruling: I9 deviation accepted (in-progress game triggers 120s cadence). Re-review dispatched.
Final re-review: all 12 findings ADDRESSED; no Critical breakage. Parked (load-bearing, surfaced to user): fetch_trades 5-page cap returns without signalling an unexhausted cursor, so >5,000 prints per ticker since min_ts (cold start) silently advance the watermark — Ruling: no second fix wave per process; recommended one-line fix is to treat a non-empty final cursor like a non-200 page (route through the I2 branch, watermark keeps prior last_ts). Cost if wrong: a rare cold-start gap on a hyper-active ticker. Minor (deferred): trades/orderbook-only outage stays health-green; alt_floor negative if tick_budget_s<=40; 40s allowance measured from tick start; failed checkpoint commit leaves run 'running'; warnings unbounded in notes; comment nit. Out-of-scope observations recorded for phase 1: Numeric(18,2) watermark, repr(e) in notes unredacted, no startup reconciliation of 'running' rows, unused secret mount on app-serve, `harness run` never calls create_schema.
