# Phase 3: Paper Execution, Settlement, Benchmarks, CLV — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the primary variant's candidate signals into paper orders watched every 15 s, fill them from the recorded WebSocket tape with a queue model, settle them from ESPN scores, and measure everything (benchmarks, CLV on every gap snapshot, markouts, weekly report tables, a stored gate report), while fixing the staleness definition that left Week 1 with zero candidates.

**Architecture:** A new `app-exec` process (`harness exec`, 15 s loop) is the only writer of intents, orders, order events, fills, and its heartbeat; it rebuilds a live book per open order from `orderbook_snapshots` + `orderbook_events`, and all of its decisions and the fill simulation are pure functions over recorded rows with an explicit `now`, so `harness replay --execute` reproduces them. A settlement job in the recorder tick (hourly) resolves games from stored ESPN bodies, computes benchmarks once per game at kickoff + 5 min, attaches CLV to every gap snapshot, and computes markouts for every order. Report and gate commands are pure SQL + statistics over those tables.

**Tech Stack:** Python 3.12 sync, SQLAlchemy 2 + psycopg 3, Postgres 16, APScheduler (existing), Typer CLI, FastAPI/Jinja2 dashboard, pytest + respx. `statistics` stdlib for CIs; no new third-party dependencies except none (BH and shrinkage are ~40 lines each).

**Spec:** `docs/superpowers/specs/2026-09-07-phase3-paper-execution-design.md` (addendum; amendments in its §0) over `docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md` §5.5, §6.5, §7.2, §9.1–9.6, §10–§13.

## Global Constraints

- No live orders anywhere; the executor has no network client and never loads secrets; `mode` is always `paper` in phase 3.
- Nothing is filtered out of the record: every candidate signal of an exec variant becomes an intent; every intent yields an order or an `order_events` row with `kind = skipped` and a reason; every order gets markouts whether filled or not.
- All decision functions are pure with explicit `now` (`plan_actions`, `simulate_fills`, `resolve_market`, `benchmark_at`, `markout_at`) and unit-tested without a database.
- Units: probabilities as `Decimal` with 4 places; contracts as `int`; money as `Decimal` cents-exact; fees via `harness.pricing.fees` (`KALSHI_FOOTBALL`, maker `0.0175·p·(1−p)` per contract, ceil to cent per fill).
- Time: everything `timestamptz` UTC; local day boundaries use `Settings.tz_local` (America/Chicago).
- Idempotency: every insert has a unique key and uses `ON CONFLICT DO NOTHING … RETURNING id` (psycopg3 `rowcount` is −1 on conflict inserts).
- Schema changes via `Base.metadata.create_all` plus idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS` and `CREATE OR REPLACE VIEW` in `create_schema`; the NAS already has every phase 0–2 table.
- Tests: `DATABASE_URL_TEST=postgresql+psycopg://harness:harness@localhost:5433/harness_test`, venv `.venv/`, `pgrep -f pytest` must print nothing before any run (shared DB), no docker in tests, output pristine (no warnings/tracebacks).
- Secrets never logged, rendered, or committed. The dashboard stays one page, no JS, every query bounded.
- Commit trailers on every commit:
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01Wp1gV1T1tYrgYDP3EJALci`.

## Verified data facts (from the NAS, 2026-09-07)

- Odds API featured cadence per sport: 120 s on game days / in-progress, 300 s weekends, longer weekdays; alternates per event: 900 s, 120 s within 3 h of kickoff, only within 36 h of kickoff. Tick duration between odds fetch and pricing ≈ 20–95 s.
- All direct-fair gap rows in a live run had staleness 910–932 s (alternates cadence). 32 of 44 direct rows passed every label except `not_stale`.
- `orderbook_events` peaks near 4M rows/hour during live games; BRIN indexes on `orderbook_events.ts` and `venue_trades.ts` exist; a 1-hour count is still ~23 s cold, so executor/tape queries must be per ticker and by `id`/`ts` ranges.
- WS rows: `orderbook_events(ticker, ts, sid, seq, kind ∈ {snapshot, delta, gap}, side ∈ {yes, no} | None, price Decimal | None, delta Decimal | None, raw)`; a `snapshot` row's `raw` carries `yes_dollars`/`no_dollars` lists of `[price, size]`; `delta` rows carry `delta` contracts (negative = removed) at `price` on `side`. `venue_trades(venue, trade_id, ticker, ts, yes_price, count, taker_side ∈ {yes, no}, is_block, source ∈ {ws, rest})`. REST `orderbook_snapshots(yes_bids, no_bids)` same `[price, size]` shape, 20 levels/side.
- Kalshi books hold bids only per side: a NO bid at price q is a YES ask at 1 − q. Our paper orders are YES bids (side `yes`) at `price_target`.
- Existing interfaces: `GapRow`, `StrategyState(open_orders, daily_exposure, game_exposure, positions)`, `SignalRow`, `run_strategy(rows, variant, now, state=None, fee_model=...)`, `Variant`, `active_variants`, `_load_gap_rows(session, run_id)`, `pricing_clock_for_run(run, tick_budget_s)`, `compute_health(session_factory, now) -> (body, code)`, `create_dashboard(session_factory, settings, clock)`, `Recorder.maybe_tick`, `build_scheduler(recorder, heartbeat_s)`, `Settings`, `KillSwitch(id, active, reason, set_at)`, `Game(kickoff_utc, status, home_score, away_score)`, `VenueMarket(ticker, market_type ∈ {moneyline, spread, total}, threshold, side_team_id, match_status)`, `ESPN status map` in `harness/normalize/espn.py` (`STATUS_FINAL → final`).

---

### Task 1: Staleness amendment (feed kind, feed lag, stale allowance)

**Files:** Modify `harness/db/models.py` (`FairValue`, `MarketGapSnapshot`: `feed_kind: str | None` (String(9)), `feed_lag_s: int | None`, `stale_allowance_s: int | None`), `harness/db/schema.py` (three `ALTER TABLE … ADD COLUMN IF NOT EXISTS` per table), `harness/pricing/lines.py` (`Line` already has `key.market_type` and `fetched_at`; add `def feed_kind(line: Line) -> str` returning `"alternate"` when `key.market_type` starts with `alternate_` else `"featured"`), `harness/pricing/fair.py` (direct and derived paths), `harness/pricing/gaps.py` (copy the three columns from the fair value), `harness/strategy/run.py` (`GapRow.stale_allowance_s: int | None`, `GapRow.feed_kind: str | None`; `not_stale` label), `harness/strategy/pipeline.py` (`_load_gap_rows` populates the two fields), `docs/superpowers/reviews/2026-09-07-phase2-preregistration.md` (Amendment 2). Test: `tests/test_fair.py`, `tests/test_gaps.py`, `tests/test_strategy.py`, `tests/test_schema.py`.

**Interfaces:**
- Consumes: `direct_fair(pairs, now) -> (Consensus, [BookFair])`; the `pairs` dict values are `(Line, Line)` per book.
- Produces: `stale_allowance_s(feed: str, ttk_minutes: float | None, tick_budget_s: int = 100) -> int` in `harness/pricing/fair.py`: `120 + tick_budget_s` (= 220) for `featured`; for `alternate`: 220 when `ttk_minutes is not None and ttk_minutes <= 180`, else `900 + tick_budget_s` (= 1000). `feed_kind` for a fair value = the kind of the newest group-member line among the pairs (the line whose `last_update` equals `consensus.newest_ts`; tie → `alternate` if any such line is alternate). `feed_lag_s = int((now − that line.fetched_at).total_seconds())`. Derived fair values inherit the main-line consensus's three values.
- `not_stale` in `run_strategy`: `row.staleness_s is not None and row.staleness_s <= max(cfg["stale_s"], row.stale_allowance_s or 0)`.

- [ ] **Step 1: Failing tests.** `test_stale_allowance_table` (four cases above). `test_direct_fair_records_feed_kind_and_lag` (seed one game with a featured spread at −3.5 and an alternate spread at −4.5 fetched 800 s earlier; Kalshi rungs 3.5 and 4.5; assert the 3.5 fair is `featured` with lag ≈ 0 and allowance 220, the 4.5 fair is `alternate` with lag ≈ 800, allowance 1000 when kickoff is 10 h away and 220 when 2 h away). `test_gap_copies_feed_columns`. `test_not_stale_uses_allowance` (staleness 910, `stale_s` 180, allowance 1000 → label True; allowance 220 → False; allowance None → 180 rule). `test_create_schema_adds_feed_columns` (drop, rerun, present).
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement.** Keep `staleness_s` semantics unchanged.
- [ ] **Step 4: Run the suite; commit** `fix: staleness allowance by feed cadence (feed_kind, feed_lag_s)`.
- [ ] **Step 5 (controller):** deploy this task to the NAS right after its review and confirm candidates > 0 on the next pricing run; append Amendment 2 to the pre-registration record with the deploy time.

---

### Task 2: Phase 3 schema (models, views, settings)

**Files:** Modify `harness/db/models.py`, `harness/db/schema.py`, `harness/config/settings.py` (`exec_period_s: int = 15`, `exec_variants: list[str] = ["sharp_direct", "constrained"]`, `settle_period_s: int = 3600`, `db_budget_gb: int = 1000`, `exec_expiry_s: int = 45`, `exec_cancel_venue_move_pts: Decimal("0.02")`, `exec_reprice_fair_move_pts: Decimal("0.01")`, `exec_kickoff_cutoff_min: int = 10`). Test: `tests/test_schema.py`.

**Models (columns):**
- `Intent(id UUID pk, signal_id bigint unique, variant_id str, venue str, venue_market_id int, side str, target_prob Numeric(6,4), target_contracts int, created_at, replay bool default False)`.
- `Order(id bigint pk, intent_id UUID, variant_id, venue, mode str default "paper", client_order_id str unique, ticker str, side str, prob Numeric(6,4), contracts int, status str ∈ {open, filled, partially_filled, cancelled, expired, settled}, placed_at, expiry, cancelled_at, cancel_reason, fair_p_at_place, fair_books_json JSONB, venue_bid_at_place, venue_ask_at_place, queue_ahead_at_place int, edge_at_place, edge_min_at_place, config_hash str, feed_kind str, gap_snapshot_id bigint, game_id int, kickoff_utc, worst_case_fill bool default False, filled_contracts int default 0, replay bool default False)`. Partial unique index `uq_open_order` on `(venue, ticker, side, variant_id)` where `status in ('open','partially_filled') and replay = false` (create in `create_schema`).
- `OrderEvent(id pk, order_id, ts, kind ∈ {place, cancel, expire, fill, skipped, cap_gate}, prob, contracts, fair_p_at_event, reason str)`.
- `Fill(id pk, order_id, prob, contracts, fee Numeric(10,4), filled_at, simulated bool, fill_method ∈ {queue_model, snapshot_cross}, source_trade_id str | None, source_event_id bigint | None, taker_side str | None, confirmed bool | None, replay bool)`; unique `(order_id, fill_method, source_trade_id, source_event_id)` via coalesce functional index.
- `Markout(order_id, horizon ∈ {1m, 5m, 30m, 120m, close}, fair_p, venue_mid, at_ts, source ∈ {quote, ws_book, none})`, PK `(order_id, horizon)`.
- `Settlement(game_id pk, home_score, away_score, source, settled_at)`; `VenueSettlement(venue, ticker, result ∈ {yes, no}, source ∈ {derived, venue}, settled_at)`, PK `(venue, ticker, source)`.
- `Benchmark(id pk, game_id, market_type, outcome_team_id, outcome_side, threshold, benchmark_type, p, target_ts, source_ts, stale bool)`; unique on `(game_id, market_type, coalesce(outcome_team_id,-1), coalesce(outcome_side,''), coalesce(threshold,0), benchmark_type)` (functional index in `create_schema`).
- `GapOutcome(gap_snapshot_id, benchmark_type, clv_mid_p, clv_target_p, clv_target_roi_net)`, PK `(gap_snapshot_id, benchmark_type)`.
- `Ledger(id pk, ts, variant_id, kind ∈ {fill, settlement}, order_id, fill_id, ticker, contracts, price, fee, cash_delta, replay)`.
- `ExecHeartbeat(id pk default 1, last_loop_at, loops int, open_orders int, last_error str | None)`.
- `GateReport(id pk, evaluated_at, criteria_json JSONB, passed bool)`.
- Views in `create_schema` (`CREATE OR REPLACE VIEW`): `positions` (per `variant_id, ticker, side`: `sum(contracts)` from fills of non-replay orders minus settled, `avg_price`), `clv` (per order: join `signals.gap_snapshot_id → gap_outcomes` pivoted to `clv_pinnacle_t5, clv_consensus_t5, clv_result` net of fee).

- [ ] **Step 1: Failing tests:** every table exists after `create_schema`, the partial unique index rejects a second open order on the same key but allows one when the first is `cancelled`, the benchmark functional unique index dedupes NULL-bearing keys, views select without error on empty tables, `create_schema` is idempotent (run twice).
- [ ] **Step 2–4:** implement, run, commit `feat: phase 3 schema (orders, fills, benchmarks, settlements, views)`.

---

### Task 3: Live book state from snapshots and deltas

**Files:** Create `harness/execution/__init__.py`, `harness/execution/book.py`; Test `tests/test_book.py`.

**Interfaces (produce):**
- `@dataclass BookState(ticker, yes_bids: dict[Decimal, int], no_bids: dict[Decimal, int], sid: int, seq: int, as_of: datetime, dirty: bool = False)`.
- `BookState.from_snapshot(ticker, yes_levels: list[list], no_levels: list[list], sid, seq, as_of)`; `apply_delta(side, price, delta, seq, ts) -> None` (seq must equal `self.seq + 1`, else `dirty = True`; sizes floored at 0 and pruned at 0); `best_yes_bid()`, `best_yes_ask()` (= `1 − best_no_bid`), `mid()`, `resting_yes_at(price) -> int`, `size_removed_at(price)` helper not needed — the fill model reads deltas directly.
- `load_book(session, ticker, now) -> BookState | None`: newest `orderbook_events` row with `kind = snapshot` for the ticker (or newest REST `orderbook_snapshots` row when no WS snapshot exists, seq = 0, `dirty = False`), then apply every `delta` row with `id` greater than the snapshot's `id` and `ts <= now`, in `(sid, seq)` order; a `gap` row sets `dirty`. `advance_book(session, book, upto_now) -> BookState` applies only rows newer than the ones already applied (track `last_event_id`).

- [ ] **Step 1: Failing tests (pure):** snapshot → best bid/ask/mid; delta adds and removes; removal below zero clamps; seq gap → dirty; `resting_yes_at`. DB test: `load_book` from the `kalshi_orderbook.json` fixture plus three inserted deltas; `advance_book` applies only new rows.
- [ ] **Step 2–4:** implement, run, commit `feat: live book state from WS snapshots and deltas`.

---

### Task 4: Queue-model fill simulation (pure)

**Files:** Create `harness/execution/fills.py`; Test `tests/test_fills.py`.

**Interfaces (produce):**
- `@dataclass(frozen=True) PaperOrder(order_id, ticker, side: str = "yes", prob: Decimal, contracts: int, placed_at: datetime, queue_ahead_at_place: int, filled_contracts: int = 0)`.
- `@dataclass(frozen=True) TapePrint(trade_id, ts, yes_price, count: int, taker_side)`; `@dataclass(frozen=True) TapeDelta(event_id, ts, side, price, delta: int)`.
- `@dataclass FillResult(fills: list[SimFill], cross_fill: SimFill | None, queue_remaining: int, remaining_contracts: int)`; `SimFill(prob, contracts, fee, filled_at, fill_method, source_trade_id, source_event_id, taker_side)`.
- `simulate_fills(order: PaperOrder, prints: list[TapePrint], deltas: list[TapeDelta], now: datetime, fee_model=KALSHI_FOOTBALL) -> FillResult`. Rules (YES bid at `prob`): merge prints and deltas by `ts` (prints after deltas on equal `ts`); a print with `taker_side = "no"` and `yes_price <= prob` (a seller hitting bids at or through our price) consumes `queue_ahead` first, then fills `min(count − consumed, remaining)`; a delta on side `yes` at `price == prob` with negative `delta` reduces `queue_ahead` by `|delta|` (floor 0) — deltas caused by our own simulated fill do not exist in the tape, so no double counting; cross fill = first event where the best NO bid implies a YES ask `<= prob` (a delta on side `no` at price `>= 1 − prob` with positive delta) or a print through our price: fill remaining at `prob`, `fill_method = snapshot_cross`. Fee per fill: `ceil_to_cent(fee_for_order(model, "maker", prob, contracts))`. Events after `now` are ignored.
- `confirmed(fill: SimFill, prints, window_s=60) -> bool`: a print with `yes_price <= prob` and `taker_side = "no"` exists in `[placed_at, filled_at + window]`.

- [ ] **Step 1: Failing tests:** queue consumed before fill; partial then complete; cancel-ahead delta shortens queue; opposite-side prints ignored; print through price fills; cross fill recorded separately and not in `fills`; events after `now` ignored; fee rounding matches `fees` module; `confirmed` true/false cases; deterministic given identical inputs.
- [ ] **Step 2–4:** implement, run, commit `feat: queue-model paper fill simulation`.

---

### Task 5: Executor decisions (pure) and exposure rebuild

**Files:** Create `harness/execution/plan.py`; Test `tests/test_exec_plan.py`.

**Interfaces (produce):**
- `@dataclass(frozen=True) OpenOrderView(order_id, variant_id, ticker, venue_market_id, side, prob, contracts, filled_contracts, placed_at, expiry, fair_p_at_place, venue_mid_at_place, edge_min_at_place, kickoff_utc, game_id, stake)`.
- `@dataclass(frozen=True) IntentView(intent_id, signal_id, variant_id, venue_market_id, ticker, side, target_prob, target_contracts, edge, edge_min, fair_p, game_id, kickoff_utc, stake, latest_decision: str)`.
- `@dataclass(frozen=True) MarketNow(venue_market_id, fair_p: Decimal | None, fair_ts, book_mid: Decimal | None, book_dirty: bool, matched: bool)`.
- Actions: `Place(intent_id, prob, contracts, expiry)`, `Cancel(order_id, reason)`, `Renew(order_id, expiry)`, `Skip(intent_id, reason)`, `Expire(order_id)`.
- `plan_actions(intents: list[IntentView], open_orders: list[OpenOrderView], markets: dict[int, MarketNow], state_by_variant: dict[str, StrategyState], variant_cfg: dict[str, dict], kill_active: bool, now: datetime, s: ExecSettings) -> list[Action]` implementing spec addendum §1: order of checks per open order — `expired` (now > expiry) → `Expire`; kill → `Cancel(kill_switch)`; kickoff − now < cutoff → `Cancel(kickoff)`; not matched → `Cancel(unmatched)`; book dirty → `Renew` only (no decision); venue mid moved ≥ 2 pts against a YES bid, i.e. `book_mid <= venue_mid_at_place − 0.02` (the market fell toward our bid, so a fill would be adversely selected) → `Cancel(venue_move)`; fair edge (`fair_p − prob − fee_per_contract − as`) `< edge_min/2` → `Cancel(edge_decay)`; newest intent for the key rejected → `Cancel(signal_rejected)`; newest intent target differs by ≥ 1 pt → `Cancel(reprice)` + `Place`; else `Renew`. Per intent without an open order: kill → `Skip(kill_switch)`; kickoff cutoff → `Skip(kickoff)`; dirty book → `Skip(book_dirty)`; cap gate for variants with `apply_caps` → `Skip(cap_<label>)`; else `Place(prob = target_prob, contracts = target_contracts, expiry = min(kickoff − cutoff, now + expiry_s))`.
- `rebuild_state(open_orders: list[OpenOrderView], positions: list[PositionView], fills_today: list[FillView], variant_id) -> StrategyState` (spec §0.2).

- [ ] **Step 1: Failing tests:** one test per branch above with fixed `now`; determinism; a reprice yields exactly Cancel then Place; dirty book yields Renew only; cap gate applies only when `apply_caps`; `rebuild_state` sums stakes and daily fills since local midnight.
- [ ] **Step 2–4:** implement, run, commit `feat: pure executor decisions and exposure rebuild`.

---

### Task 6: Executor loop, persistence, CLI, Compose

**Files:** Create `harness/execution/loop.py`, `harness/execution/store.py`; Modify `harness/cli.py` (`exec`, `exec-once`), `harness/scheduler.py` (`build_executor(settings)`), `docker-compose.yml` (`app-exec`), `docs/runbooks/phase0-deploy.md` (executor section). Test: `tests/test_exec_loop.py` (DB).

**Interfaces:**
- `Executor(settings, session_factory, clock=..., monotonic=...)` with `step() -> ExecStats(intents_new, placed, cancelled, renewed, expired, fills, skipped, errors)`:
  1. Load `kill_switch`; load candidate signals of `exec_variants` without intents (`replay = false`, `created_at >= now − 1 h`, joined to `venue_markets`/`games`) → insert `intents` (`ON CONFLICT (signal_id) DO NOTHING`).
  2. Load open orders (`status in (open, partially_filled)`, `replay = false`); for each ticker load/advance `BookState`; latest `fair_values` per market shape (via `market_gap_snapshots` newest row for the market); build `MarketNow`.
  3. For each open order run `simulate_fills` over prints/deltas with `id` beyond the order's `last_tape_event_id` / `last_trade_ts` (persist both on the order as `tape_cursor_event_id`, `tape_cursor_trade_ts` — add to `Order` in this task), insert `fills` + `order_events(kind = fill)` + `ledger`, update `filled_contracts`/`status`.
  4. `plan_actions` → apply: `Place` inserts `Order` (`client_order_id = f"paper-{intent_id}"`, capture `fair_books_json` from the latest `fair_values.model_json`/consensus books, `queue_ahead_at_place = book.resting_yes_at(prob)`, bid/ask/mid at place) + `order_events(place)`; `Cancel` sets status/reason/`cancelled_at` + event; `Renew` updates `expiry`; `Expire` sets `expired` + event; `Skip` writes `order_events(kind = skipped, reason)` against a synthetic `order_id = NULL` — make `OrderEvent.order_id` nullable and add `intent_id` to it.
  5. Update `exec_heartbeat`; commit once per step; on exception rollback, record `last_error`, continue next loop.
- CLI `harness exec` runs `Executor.step` on an APScheduler interval `exec_period_s` with the same SIGTERM handling as `run`; `harness exec-once` runs one step and prints `ExecStats`.
- Compose: `app-exec` with `command: ["exec"]`, `env_file: .env`, no secret mounts, `restart: unless-stopped`, `depends_on: postgres: condition: service_healthy`, same `user:` line.

- [ ] **Step 1: Failing DB tests:** seed via `tests/test_pipeline._seed` + a registered `tiny` primary and `price_and_signal`, then inject a candidate signal: `step()` creates an intent and an open order with `queue_ahead_at_place` from an inserted WS snapshot; a second `step()` with a print at the price fills it and writes a fill, an event, and a ledger row; kill switch active → cancel with reason; expiry in the past → expired; a second candidate at a different price → cancel(reprice) + new order; heartbeat updated; exception in one order does not abort the step (record `last_error`).
- [ ] **Step 2–4:** implement, run, commit `feat: paper executor loop and app-exec service`.

---

### Task 7: Settlement from ESPN scores, positions, ledger

**Files:** Create `harness/settlement/__init__.py`, `harness/settlement/settle.py`; Modify `harness/recorder/tick.py` (hourly hook after pricing: `if settle due → run_settlement(session, now)`; due via `source_state` key `settlement`), `harness/cli.py` (`settle`). Test: `tests/test_settle.py`.

**Interfaces (produce):**
- `resolve_market(market_type, threshold, side_team_id, home_team_id, away_team_id, home_score, away_score) -> str` (`yes`/`no`): moneyline → `yes` if the side team won (ties: `no`); spread for team T at k.5 → `yes` if `(T score − opp score) > k.5`; total over k.5 → `yes` if `home + away > k.5`.
- `run_settlement(session, now) -> SettleCounts(games, markets, orders, mismatches)`: for games with `status = final` and scores present and no `settlements` row → insert; for each matched venue market of the game → `venue_settlements(source = derived)`; if the newest Kalshi market summary for the ticker carries a `result` (`raw` body field `result ∈ {yes, no}`) → `venue_settlements(source = venue)` and a `settlement_mismatch` warning in `ctx` when they differ; every fill of a non-replay order on that ticker → `ledger(kind = settlement, cash_delta = contracts × (1 if result == side else 0))` once per fill (unique on `(fill_id, kind)`), order `status = settled`.
- Tick notes gain `settlement: {games, markets, orders, mismatches}`.

- [ ] **Step 1: Failing tests:** `resolve_market` table (six cases incl. ties and negative spreads); settlement idempotent on rerun; mismatch recorded; ledger cash delta per fill; unsettled games untouched.
- [ ] **Step 2–4:** implement, run, commit `feat: settlement from ESPN finals, venue settlement check, ledger`.

---

### Task 8: Benchmarks and CLV on gap snapshots

**Files:** Create `harness/settlement/benchmarks.py`; Modify `harness/settlement/settle.py` (call after settlement: `compute_benchmarks(session, now)`), `harness/cli.py` (`benchmarks --game-id`). Test: `tests/test_benchmarks.py`.

**Interfaces (produce):**
- `benchmark_at(kind, target_ts, snapshots: list[Snap]) -> tuple[Decimal | None, datetime | None, bool]` pure: last snapshot with `ts <= target_ts` (`stale = source_ts < target_ts − 10 min`), `opening_first_seen` = first ever.
- `compute_benchmarks(session, now) -> int`: for every game with `kickoff_utc + 5 min <= now` and any matched venue market and no benchmark rows yet: per shape from `fair_values` history (sharp consensus rows: `fair_source = direct`) → `consensus_t5/t60/t180`, `opening_first_seen`; `pinnacle_t5` from `odds_snapshots` (book `pinnacle`, devigged with the two-way pair via `harness.pricing.devig`); `kalshi_mid_t5` and `kalshi_last_trade_pre_kick` from `venue_quotes`/`venue_trades` of the matched market; `novig_mid_t5` from `odds_snapshots` book `novig`; `result` after settlement (0/1) inserted by `run_settlement`. Insert with the functional unique index.
- `compute_gap_outcomes(session, game_id) -> int`: for each gap snapshot of the game's markets with `fair_p` and each benchmark of its shape: `clv_mid_p = p_bench − venue_mid`, `clv_target_p = p_bench − target` where target = the primary's `price_target` from `signals` (fallback `best_bid`), `clv_target_roi_net = (1/p_used)/(1/p_bench) − 1 − fee_per_contract(maker, p_used)/p_used`. PK dedupes.

- [ ] **Step 1: Failing tests:** `benchmark_at` picks the right snapshot and stale flag; `compute_benchmarks` runs once per game (rerun inserts 0); `pinnacle_t5` devig equals the module's; gap outcomes computed for every gap with a fair and skipped for `no_fair_reason` rows; formulas on fixed numbers.
- [ ] **Step 2–4:** implement, run, commit `feat: benchmarks at kickoff and CLV on every gap snapshot`.

---

### Task 9: Markouts for every order

**Files:** Create `harness/settlement/markouts.py`; Modify `harness/settlement/settle.py` (call `compute_markouts(session, now)` each settlement run), Test: `tests/test_markouts.py`.

**Interfaces (produce):**
- `HORIZONS = {"1m": 60, "5m": 300, "30m": 1800, "120m": 7200}` plus `close` = `kickoff_utc − 5 min`.
- `markout_at(horizon_ts, fairs: list[(ts, p)], quotes: list[(ts, mid)], book_mid_fn) -> (fair_p, venue_mid, source)`: last fair at or before; last quote at or before if within 60 s else `book_mid_fn(horizon_ts)` (from `load_book` at that time), else `none`.
- `compute_markouts(session, now) -> int`: for orders (filled or not, non-replay) lacking a horizon row whose horizon time `<= now`: insert `markouts`; `adverse_drift` is a view column: fair at first fill − `fair_p_at_place`.

- [ ] **Step 1: Failing tests:** horizon selection; quote-vs-book source choice; unfilled orders get markouts; idempotent.
- [ ] **Step 2–4:** implement, run, commit `feat: markouts at 1/5/30/120 min and close for every order`.

---

### Task 10: Report statistics and `harness report --week`

**Files:** Create `harness/report/__init__.py`, `harness/report/stats.py`, `harness/report/tables.py`, `harness/report/weekly.py`; Modify `harness/cli.py` (`report --week N [--out docs/reports/]`). Test: `tests/test_report_stats.py`, `tests/test_report.py`.

**Interfaces (produce):**
- `cluster_ci(values: list[Decimal], clusters: list[int], level=0.90) -> (mean, lo, hi, n, n_clusters)`: cluster-robust SE by cluster (game) via the standard sandwich estimator on cluster means weighted by size; `t` from `statistics.NormalDist` at 0.95 (normal approx; note in the doc).
- `bh_reject(pvalues: list[Decimal], q=0.10) -> list[bool]`; `eb_shrink(cell_means, cell_ns, cell_vars) -> list[Decimal]` (method of moments: `τ² = max(0, var(means) − mean(var/n))`, shrink `m_i` toward the grand mean by `n_i/(n_i + σ²/τ²)`).
- `weekly_tables(session, week_start, week_end, settings) -> dict[str, Table]` for tables 1, 2, 3, 4, 5, 6, 8 per spec addendum §4, each `Table(title, columns, rows)` with `(estimate, n, lo, hi)` cells and `n < 30` flagged; tables 7, 9, 10 as `Table(title, [], [], note="not collected")`.
- `render_markdown(tables) -> str`; CLI writes `docs/reports/2026-wNN.md` (week N = ISO week; `week_start` Monday 00:00 America/Chicago).

- [ ] **Step 1: Failing tests:** `cluster_ci` on a fixed vector equals a hand-computed value; `bh_reject` on a textbook vector; `eb_shrink` pulls small-n cells further; `weekly_tables` on the seeded DB returns all ten keys with the three placeholders; rendering has no empty cells.
- [ ] **Step 2–4:** implement, run, commit `feat: weekly report tables with cluster-robust CIs, BH, shrinkage`.

---

### Task 11: Gate report

**Files:** Create `harness/report/gate.py`; Modify `harness/cli.py` (`gate`). Test: `tests/test_gate.py`.

**Interfaces (produce):** `evaluate_gate(session, now) -> GateResult(criteria: dict[str, Criterion(value, threshold, passed)], passed: bool)` implementing spec §9.5 measurable criteria: fills confirmed ≥ 150 across ≥ 40 games and both sports; ≥ 30% in NFL or marquee NCAAF (spread ≤ 4c at place); CLV vs `pinnacle_t5` net-of-fee lower CI bound > 0; mean 30 m markout net of maker fee > 0 with t > 2; adverse drift > −0.010; filled-minus-unfilled CLV not significantly negative; mean CLV ≥ 0 under every benchmark; median feed lag < 90 s; zero settlement mismatches; the two manual items (`legal_decision`, `live_trading_env`) always `False` in phase 3. Stores `gate_reports`. CLI prints the table and exit code 0 either way.

- [ ] **Step 1: Failing tests:** empty DB → all fail, stored; a seeded passing fixture for the numeric criteria passes those and still fails overall on the manual items.
- [ ] **Step 2–4:** implement, run, commit `feat: stored go-live gate report`.

---

### Task 12: Dashboard additions and housekeeping

**Files:** Modify `harness/dashboard/app.py`, `harness/dashboard/templates/index.html`, `harness/recorder/tick.py` (daily `housekeeping` at 09:00 UTC via `source_state` key), Create `harness/ops/housekeeping.py`. Test: `tests/test_dashboard.py`, `tests/test_housekeeping.py`.

**Sections added (all bounded):** executor heartbeat in Health (red > 60 s); open paper orders (`LIMIT 100`) and today's fills (`LIMIT 200`); paper P&L per exec variant (from `ledger`, last 7 days) and open exposure from `positions`; candidates since the staleness fixes (count of `decision = candidate`, last 24 h, per variant); database size vs `db_budget_gb` (`pg_database_size`, red at 80%); settlement mismatches (last 7 days). `housekeeping()` records `pg_total_relation_size` for the six largest tables into the run notes.

- [ ] **Step 1: Failing tests:** each new section renders and appears in `/api/summary`; heartbeat colour logic; db size number present; housekeeping note shape.
- [ ] **Step 2–4:** implement, run, commit `feat: dashboard executor, P&L, and database budget sections`.

---

### Task 13: Replay `--execute` and end-to-end fixture day

**Files:** Modify `harness/replay.py` (`replay(..., execute: bool = False)`), `harness/cli.py`; Create `tests/fixtures/day_2026-09-13/` exported via `harness export-fixture` (add this CLI: dumps `raw_responses` for a `run_id` range to JSON) — if no recorded game day exists yet when the task runs, synthesize a 30-minute fixture from existing fixtures (odds, Kalshi markets, one WS snapshot, ten deltas, five prints) and mark it synthetic. Test: `tests/test_replay_execute.py`.

**Interfaces:** with `execute`, after signals are written for each run, the replay drives `Executor.step` in "replay mode" (`replay = True` on intents/orders/fills, tape read up to that run's `pricing_clock_for_run`, clock = that time), so orders and fills are reconstructed from stored rows only; unique keys include `replay`, so live rows are untouched. `ReplayCounts` gains `orders, fills`.

- [ ] **Step 1: Failing tests:** replay with `--execute` over the seeded pipeline run produces exactly the orders/fills the live executor produced for the same rows (compare counts and prices), tagged `replay = True`; rerun inserts 0; end-to-end fixture day asserts signal, order, fill, gap_outcome counts and zero orders on unmatched markets.
- [ ] **Step 2–4:** implement, run, commit `feat: replay --execute reconstructs paper orders and fills`.

---

### Task 14: Runbook, deploy, and pre-registration amendment 2

**Files:** Modify `docs/runbooks/phase0-deploy.md` (phase 3 upgrade: `init-db` adds tables/views/columns; new `app-exec`; `harness settle`, `report`, `gate`, `exec-once`; what "candidates since fixes" means), `docs/superpowers/reviews/2026-09-07-phase2-preregistration.md` (Amendment 2 with the Task 1 deploy time, if not already written in Task 1), `Makefile` (no change expected; verify `up -d` starts `app-exec`).

- [ ] **Step 1:** edits; **Step 2:** `make deploy-nas`; verify `make status-nas` shows four app containers healthy, `exec_heartbeat` advancing, the dashboard's executor block green; commit `docs: phase 3 runbook and deploy notes`.

---

## Self-review

**Spec coverage:** addendum §0.1 → T1; §0.2 → T5/T6; §0.3 → T5/T6; §0.4 → T10; §0.5 → T8; §0.6 → T12; §1 → T3/T5/T6; §2 → T4/T6; §3 → T7/T8/T9; §4 → T10/T11/T12/T13; §5 → T2 (+ columns in T1/T6); §6 → T6/T12/T14; §7 → every task's Step 1; §8 out of scope honoured (no network client, no NO-side orders).

**Placeholder scan:** interfaces and test intents are explicit in every task; formulas given where they are not a direct composition of earlier modules (fills, benchmarks, CLV, statistics). T13's fixture has a stated fallback rather than a TBD.

**Type consistency:** `StrategyState` (existing) consumed by T5/T6; `BookState` (T3) by T4 (via `resting_yes_at`) and T6/T9; `PaperOrder`/`FillResult` (T4) by T6/T13; `plan_actions` actions (T5) by T6/T13; `pricing_clock_for_run` (existing) by T13; `GapRow.stale_allowance_s`/`feed_kind` (T1) by pipeline and replay loaders; `Order.tape_cursor_*` added in T6 and read by T13.
