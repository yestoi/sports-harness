# Phase 0 Recorder — Final Whole-Branch Review

Branch `phase0-recorder`, base `663cfc1` → head `c6955fd` (21 commits).
Reviewed: plan `docs/superpowers/plans/2026-09-06-phase0-recorder.md`, spec sections 4/4.1/4.2/5.1/5.2/5.4/5.5/13/14/15, controller rulings in `.superpowers/sdd/2026-09-06-phase0-recorder/final-review-triage.md`, and the full working tree.

Read-only review. No working tree, index, HEAD, or branch was mutated; no Docker commands were run; no tests were re-run (the 47-test suite is reported passing and warning-free).

---

## Strengths

- **The fetch/parse split holds throughout.** Every `parse_*` is pure and total (returns `[]` on garbage), every `fetch_*` returns a `FetchResult`. That is why the malformed-event fixtures and the `_latest_body` fallback both work without special cases.
- **Trades and ladders are coupled to the markets refresh, not the heartbeat.** `harness/recorder/tick.py:208` only enters `_kalshi_trades_and_ladders` when `summaries` is non-empty, and `summaries` is only populated on a tick where the markets list was actually re-fetched (`harness/recorder/tick.py:143-145`). That single line is what keeps Kalshi cost proportional to cadence instead of to the 30 s heartbeat. It is not obvious and it is correct.
- **The API key genuinely cannot leak.** All four paths traced:
  - `raw_responses.params` never contains it — `harness/recorder/tick.py:86` and `:107` store `{"markets": "featured"}` / `{"markets": "alternates"}`.
  - `FetchResult.url` is redacted at `harness/feeds/http.py:70` via `_redacted_url`, and is never persisted anyway.
  - `FetchError` embeds only the base URL (`harness/feeds/http.py:50`), since the key travels in `params`, not the URL string.
  - `configure_logging` pins the `httpx` logger to WARNING (`harness/logging_setup.py:39`), suppressing httpx's own `HTTP Request: GET <full url>` INFO line — the one realistic leak vector. `RedactionFilter` also covers `exc_info` (`harness/logging_setup.py:24-27`).
- **The Monday partition boundary is safe**, contrary to the concern raised. `ensure_partitions` always creates current *and* next ISO week (`harness/db/schema.py:25-36`), so a tick starting at 23:59:59 Sunday UTC can only produce `fetched_at` values inside those two partitions. A multi-week outage is also fine, because the function is keyed on `now`, not on the last partition created. ISO-year rollover naming (2026-W53 → 2027-W01) is also distinct.
- **The `_latest_body` fallback degrades correctly.** Stale ESPN data cannot pin the NFL burst on, because the burst requires a kickoff 60 to 100 minutes in the *future* (`harness/recorder/cadence.py:22-25`); a frozen scoreboard's kickoffs march into the past and the condition goes false on its own. Stale Odds bodies likewise age out via the 36 h filter in `alternates_due`.
- **The controller rulings are sound and consistently applied.** The injectable `HttpClient` clock (`harness/feeds/http.py:28`) is the right call over freezegun. Widened per-source isolation is correct and verified by `tests/test_tick.py:99` and `:117` against real Postgres. Partial-pagination handling (`harness/recorder/tick.py:137-141`) correctly withholds source state so the series retries. `stop_grace_period: 120s` is present on both app services.
- **`week_bounds` rejects naive datetimes** (`harness/db/schema.py:10-11`), closing the host-local-interpretation hole flagged in Task 3.

---

## Issues

### Critical (Must Fix)

#### C1. The container cannot read the API key file, so it will crash-loop on first start

**Where:** `Dockerfile:8` + `docs/runbooks/phase0-deploy.md:5`

`Dockerfile:8` runs `USER nobody` (uid 65534 in `python:3.12-slim`). The runbook instructs `chmod 600 secrets/odds_api_key`, and the file in the tree is `-rw------- trey`. On a Linux NAS bind mount there is no uid remapping, so uid 65534 gets `PermissionError` at `settings.odds_api_key()` (`harness/config/settings.py:25`), which `build_recorder` calls eagerly (`harness/scheduler.py:14`).

**Why it matters:** `harness run` exits non-zero, `restart: unless-stopped` restarts it, forever. `/healthz` returns 503 with no run rows, so it is at least visible, but this blocks the Wed 2026-09-09 19:20 CT deadline. The evidence suggests the Docker path was never smoke-tested end to end (the runbook's own step 5 would have failed).

**Fix:** In the runbook, follow the chmod with `sudo chown 65534:65534 secrets/odds_api_key`, or pin the container to the host uid with `user: "${UID}:${GID}"` in `docker-compose.yml`. Verify with runbook step 5 before deploy day. Fold in the deferred minor about Docker silently creating a *directory* when the secret file is missing — same runbook line.

#### C2. The alternates loop has no budget guard, so a slow Odds API destroys the Kalshi capture during game windows

**Where:** `harness/recorder/tick.py:104-120`, called from `:206`

The alternates loop iterates `alternates_due` with no `budget.ok()` check. `_Budget` only gates trades (`:156`) and ladders (`:181`). `_odds` runs *before* `_kalshi_markets` (`:206` before `:207`).

**Why it matters:** Three effects compound.
1. All events last fetched on the same tick come due on the same later tick (`alternates_due` uses a single per-event interval with no jitter), so the 15-minute batch arrives as a thundering herd rather than spread out.
2. With ~80 NCAAF events in the 36 h window and a 10 s timeout plus one retry (`harness/feeds/http.py:48-52`), a degraded Odds API makes one tick run 20+ minutes. Every Kalshi ladder and trade for that window is skipped, because by then `budget.ok()` is false for everything.
3. A tick exceeding the 120 s `stop_grace_period` gets SIGKILLed, discarding the entire tick (see I1).

This is data loss in exactly the hours the recorder exists to cover.

**Fix:** Pass `budget` into `_odds`, check it inside the alternates loop, and count `skipped_alternates` in `notes`. Give alternates a sub-budget of ~40 s so Kalshi always gets the remainder. Optionally shuffle the due list so the same tail is not always dropped.

---

### Important (Should Fix)

#### I1. The whole tick is one transaction: data loss on kill, and autovacuum blocked for three weeks

**Where:** `harness/recorder/store.py:18-23` (flush only), `harness/recorder/tick.py:201-221` (sole commit is inside `finish_run`)

`store_raw` only flushes; the single commit is `finish_run` (`harness/recorder/store.py:37`). A game-window tick accumulates on the order of 1,000 rows over 100 s in one open transaction.

**Why it matters:** Two costs.
- A SIGKILL, OOM, or connection reset loses the whole tick's raw responses.
- More corrosively: `trade_watermarks` takes ~600 upserts per tick (`harness/recorder/tick.py:173`), and with an ~83% open-transaction duty cycle during game windows, autovacuum's xmin horizon is pinned. Dead tuples accumulate on a 600-row table. `get_watermarks` (`harness/recorder/store.py:52`) is a full table scan run once per tick, so it degrades measurably over exactly the three-week unattended window.

**Fix:** Commit after each source section, and after every N ladder/trade stores. Follow each commit with `session.expunge_all()`.

#### I2. A ticker whose trades call returns non-2xx is retried forever and pins `/healthz` at 503

**Where:** `harness/recorder/tick.py:174-175`

The non-200 branch records the error but skips `upsert_watermark`. `select_trade_tickers` (`harness/recorder/cadence.py:74-77`) therefore re-selects the ticker every tick, appends an error every tick, and the run status is `error` every tick.

**Why it matters:** This is the unbounded case. Budget-skipped tickers (`harness/recorder/tick.py:156-158`) *are* bounded — the backlog drains over two or three ticks since each tick clears a few hundred. Non-2xx is not bounded: it is a permanent per-ticker hot loop plus a permanently red health check.

**Fix:** On failure, upsert the watermark with the current volume and the **unchanged** `last_ts` (or `now - 24h` if none existed). That stops the loop without losing trades, because the next real volume change still re-fetches from the old `last_ts`.

#### I3. Trades are capped at 1,000 with no cursor loop, and the watermark advances past what was dropped

**Where:** `harness/venues/kalshi/public.py:110` (`limit=1000`, no cursor), `harness/recorder/tick.py:172` (`newest = max(stamps)`)

Kalshi returns trades newest-first. If more than 1,000 trades occurred since `min_ts`, the oldest are dropped from the response, and the watermark then advances to the newest — so they are never requested again.

**Why it matters:** Silent, unrecoverable gaps in the raw store that phase 1 depends on. The cold start uses `now - 24h` for every new ticker (`harness/recorder/cadence.py:77`), which is precisely where a 1,000-trade overflow is most likely. Also plausible in the closing minutes of a high-volume NFL game.

**Fix:** Follow the `cursor` in `fetch_trades` the way `fetch_markets_all` already does (`harness/venues/kalshi/public.py:90-103`), capped at a few pages.

#### I4. A single non-2xx flipping the run to `error` and `/healthz` to 503 will cry wolf — recommend changing it

**Where:** `harness/recorder/tick.py:215`, `harness/health.py:26`

**Recommendation (concrete):** Keep recording every non-2xx in `notes.errors`, but introduce a third status, `degraded`, for a run that fetched something and saw only non-2xx responses. Reserve `error` for a run where an exception escaped a source handler, or where nothing was fetched at all. Then change `harness/health.py:26` to return 503 when there has been no `ok` run in the last 20 minutes, rather than when the single most recent run is `error`.

**Why:** A routine 429 or one stale-event 404 then leaves the light green while still leaving a trail in `runs.notes`, and a real outage still trips 503 within one cadence period. As written, one 404 on a future event that The Odds API has pulled pins the health check red for up to 36 hours — and a human glancing at a permanently red light learns nothing. This also subsumes the deferred minor about `seconds_since` being measured from `started_at`.

#### I5. A failing Odds API zeroes the credit reading on the health page

**Where:** `harness/feeds/odds_api.py:17-24`, `harness/recorder/tick.py:90` and `:112`

`parse_credit_headers` returns zeros when headers are absent, and `ctx["remaining"]` is assigned unconditionally. A 401 or post-retry 5xx therefore surfaces as `credits_remaining: 0` on `/healthz`, which reads as an exhausted account.

**Fix:** Only overwrite `ctx["remaining"]` when `x-requests-remaining` was actually present in the response headers.

#### I6. No connect or statement timeout on the database

**Where:** `harness/db/engine.py:6`

Only `pool_pre_ping=True` is set. Every HTTP call is bounded at 10 s but no DB call is bounded at all, so a hung Postgres hangs the tick indefinitely; `max_instances=1` then drops every subsequent heartbeat. Health does eventually go stale-503, so it is not silent, but recovery needs a human.

**Fix:** `create_engine(url, pool_pre_ping=True, future=True, connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000"})`.

#### I7. `_latest_body` re-reads multi-megabyte JSONB four times per tick, every 30 seconds

**Where:** `harness/recorder/tick.py:48-51`, called at `:71` and `:98`

The query has no `fetched_at` bound, so it cannot prune partitions, and it runs whenever a source was not fetched this tick — which is most ticks (2× ESPN + 2× Odds). The NCAAF scoreboard body alone is on the order of a megabyte.

**Fix:** Cache the last good body per `(source, endpoint)` on the `Recorder` instance; fall back to the query only on process start. This also resolves the deferred minor about the index being only partially covering.

#### I8. Peak memory on the cold-start tick is unbounded

**Where:** `harness/recorder/tick.py:161-173` combined with the single-transaction identity map

The first tick requests 24 hours of trades for ~590 tickers, and every response object stays in the session identity map until `finish_run`. Parsed, that can reach several hundred megabytes on a NAS.

**Fix:** Same incremental commits plus `session.expunge_all()` as I1.

#### I9. Quiet hours drop late West Coast games — this is a plan/spec defect, not an implementation one

**Where:** `harness/recorder/cadence.py:18`

`interval_for` returns `None` between 01:00 and 08:00 CT, exactly as the spec requires. But NCAAF Hawaii and late Pac-12 kickoffs run past 01:00 CT, so the closing minutes of games with the widest markets are never recorded.

**Fix:** Suppress quiet hours when any kickoff is in progress, i.e. `kickoff <= now <= kickoff + 4h`. Worth raising against the spec, since the plan inherits the rule verbatim.

#### I10. Nothing watches `/healthz`

**Where:** `docker-compose.yml:17-45`

There is no `healthcheck` on `app-run`, and `restart: unless-stopped` does not react to health anyway. Over three weeks unattended, a stopped recorder is discovered whenever the user next looks.

**Fix:** Add a compose healthcheck curling `/healthz` on `app-serve`, and note in the runbook that an external monitor should poll it.

---

### Minor (Nice to Have)

- **`harness/db/models.py:44`** — `last_volume_fp` is `Numeric(18, 2)`. Any Kalshi volume with more precision rounds on write and then never compares equal in `harness/recorder/cadence.py:78`, producing a permanent per-ticker refetch loop. Widen to `Numeric(18, 6)`.
- **`harness/recorder/tick.py:75`, `:120`, `:148`, `:179`, `:193`, `:213`** — bare `repr(e)` goes into `runs.notes` (durable JSONB) without passing through `redact()`. Belt and braces; no current path leaks, but notes is permanent storage.
- **`harness/recorder/store.py:11-15`** — rows left in `status='running'` after a hard kill are never reconciled. Add a startup `update runs set status='crashed' where status='running'`.
- **`docker-compose.yml:38`** — mounts the key into `app-serve`, which never reads it (`odds_api_key()` is a lazy method). Drop the mount.
- **`harness/recorder/tick.py:60`** — ESPN is polled every 15 minutes during quiet hours (hardcoded 900, no quiet-hours check), so overnight runs report `ok` rather than `skipped`. Harmless; ESPN is free.
- **`harness/db/models.py:44`** — annotated `Mapped[float]` while psycopg returns `Decimal`.
- **`harness/cli.py:34-53`** — `harness run` never calls `create_schema`, so a fresh volume without the runbook's step 4 fails every tick.
- **`harness/recorder/tick.py:199`** — `notes.errors` is unbounded; a total Kalshi outage writes ~600 entries into one JSONB value.

---

## Deferred-minor triage

One line per deferred minor in `final-review-triage.md`:

| Triage line | Decision |
| --- | --- |
| T10 duplicates `_ESPN_PATH` instead of importing private `_PATH` | defer to phase 1 |
| Redaction header-name regex `[A-Z-]` only; Authorization regex brittle on multi-token values | defer to phase 1 — no phase 0 path sends either header (Kalshi public needs no key) |
| `HttpClient` not closed on `FetchError` path / no context manager | defer to phase 1 — the client is process-lifetime by design |
| `httpx.TimeoutException` redundant in the except tuple | defer to phase 1 |
| `typing.Callable` vs `collections.abc.Callable` | defer to phase 1 |
| Naive `commence_time` would be host-local | defer to phase 1 — The Odds API always returns a `Z` suffix |
| `_i()` TypeError on a non-string header | defer to phase 1 — httpx headers are always `str` |
| NFL scoreboard test does not assert zero query params | defer to phase 1 |
| Trailing sleep after the terminating Kalshi page | defer to phase 1 |
| Non-200 page appended to `pages` (intended) | defer to phase 1 — the partial-pagination fix depends on it |
| `finish_run` continuation-line indentation | defer to phase 1 |
| Quiet hours short-circuit the NFL burst (moot) | defer as written — but fix the NCAAF variant, see I9 |
| N+1 `get_source_state` for alternates | defer to phase 1 — ~32 ms per tick |
| `_latest_body` index partially covering | **fix before merge** — folded into I7 |
| `maybe_tick` type hint says `Run \| None` | already resolved — `harness/recorder/tick.py:196` reads `-> Run` |
| healthz `seconds_since` from `started_at` (defensible) | **fix before merge** — folded into the I4 rework |
| Runbook lacks missing-secret-file warning (Docker creates a dir) | **fix before merge** — same runbook line as C1 |

---

## Recommendations

### Credit budget is not a risk

Derived from the code: featured costs 3 credits per call (`markets=h2h,spreads,totals` is 3 markets; 7 bookmakers is one group of 10, so `ceil(7/10) = 1`). Alternates cost 2 per event call (`alternate_spreads,alternate_totals`). Per event across its lifetime: 33 h at the 900 s rate = 264 credits, plus 3 h at the 120 s rate = 180 credits, totalling 444.

| Component | Credits per week |
| --- | --- |
| Featured, both sports, all cadences | ~9,100 |
| Alternates, ~96 events at 444 each | ~42,600 |
| Weekly total | ~52,000 |
| Monthly projection | ~225,000 |

That is under 5% of the 5M tier, with room for the NCAAF event count to be triple the assumption.

**The runaway is bounded.** `alternates_due` (`harness/recorder/cadence.py:46`) skips any event whose `commence` is in the past, so a permanently-404ing event costs at most 36 h × 120 calls/h × 2 = 8,640 credits before it ages out. The runaway's real cost is tick time and a pinned-red health check (I4, C2), not credits.

### Expect chronic budget exhaustion during game windows

400 ladders at ~0.2 s each (50 ms mandated sleep plus RTT) is ~80 s, which with markets pages and trades exceeds the 100 s budget on an NCAAF Saturday. Consequences to accept or address:
- `budget_exhausted` will be true on most game-window runs, so it carries little signal.
- `select_ladders` sorts by volume desc and truncates at the cap, so the tail is deterministically starved — only the top-N-by-volume markets ever get ladders. Document this for phase 1's expectations.
- The 20 s NFL burst is not achievable: a tick takes 60 s or more, so the burst runs at closer to a 60 s cadence. Either accept this explicitly in the plan or lower `ladder_cap_per_tick` from 400 to roughly 150.

### Disk

Rough estimate: 0.5–2 GB/day of JSONB before TOAST compression, so 5–15 GB over three weeks. Add a free-space check to the runbook; if `pgdata` fills, Postgres goes read-only and every tick errors.

### Tests

Solid where they exist. Real Postgres with mocked HTTP is the right shape, and `tests/test_tick.py` covers first tick, cadence skip, source isolation, non-HTTP exception isolation, partial pagination, budget exhaustion, and non-2xx. Gaps that matter for phase 0, all corresponding to issues above and none currently covered:
- No test that a non-2xx trades response leaves a ticker unwatermarked and therefore permanently re-selected (I2).
- No test for budget exhaustion during the alternates loop (C2).
- No test for a `fetched_at` that crosses a partition boundary mid-tick.

---

## Assessment

**Ready to merge?** With fixes

**Reasoning:** The recorder is well structured and the secret handling is genuinely airtight, but the container as built cannot read its own API key, and an unbudgeted alternates loop can starve the entire Kalshi capture during exactly the game windows this exists to record. Fix C1, C2, I2, and I1, and this is ready to run unattended for three weeks.
