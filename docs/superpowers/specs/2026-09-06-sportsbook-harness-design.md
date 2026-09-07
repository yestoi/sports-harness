# Sportsbook Harness — Design Spec (v2)

**Date:** 2026-09-06 (v2, post adversarial review; v1 superseded)
**Status:** Approved in brainstorming; incorporates the six-reviewer adversarial review recorded in `2026-09-06-adversarial-review.md`
**Scope:** 2026 NFL and college football (NCAAF) season
**User decisions (2026-09-06):** live trading deferred to the go-live gate review; combo RFQ listener added as a paper hypothesis; v1 scope cuts accepted (no Novig live adapter until credentials, no arb module, no historical-odds backtest, no scheduled LLM briefs, one dashboard page, sync code).

## 1. Purpose

A self-hosted Python system whose product for the first three weeks is a **dataset**, and afterwards, if the data supports it, a **paper-validated straight-bet strategy** on CFTC-regulated exchanges.

1. **Record** sharp sportsbook lines, Kalshi order books and trade prints, and schedules, from the first NFL Week 1 kickoff onward, in a form that any later strategy can be replayed against exactly.
2. **Paper trade** a sharp-fair-value maker strategy with an honest, tape-based fill model, measured by closing line value (CLV) and fill-conditional markout, not by win/loss.
3. **Test nine pre-registered hypotheses** about where prediction-market prices are exploitably off, including retail flow, cross-board consistency, combo RFQ margins, and news latency.
4. **Build fun LSU/Saints parlay cards** for manual placement on DraftKings.
5. Use the Claude API as a **shadow-mode veto and report annotator**, never as a pricer.
6. Go live only after an explicit gate, a separate legal decision by the user, and a canary period.

## 2. Context and constraints

- **User:** Louisiana resident, LSU and Saints fan, DraftKings account. Bankroll for straight bets $2,000–$5,000; separate fun budget for parlays.
- **Venues:** Kalshi (public REST/WebSocket, demo environment, self-serve keys). Novig (CFTC DCM since 2026-08-04, available in Louisiana, OAuth REST, credentials issued by request only, markets endpoint returns 401 without a token). DraftKings is manual-only.
- **Legal landscape (as of 2026-09-06, not legal advice):**
  - Louisiana Gaming Control Board advisory, December 2025: sports event contracts are sports wagering under state law.
  - Louisiana Act 182 of 2026 (HB 883), effective 2026-08-01: R.S. 14:90.3 defines "client" as anyone physically located in Louisiana using a computer to make gambling-by-computer wagers; clients face up to $500 / six months; each wager is a separate violation; forfeiture of profits is mandatory; the AG and LGCB gained cease-and-desist, account-freezing, and access-removal powers (R.S. 27:19.1–19.2). Subsection M exempts transactions on a commodities exchange; whether a CFTC sports contract qualifies is the disputed question. Subsection P exempts only licensed Louisiana sportsbooks (DraftKings qualifies).
  - Ninth Circuit, *KalshiEX, LLC v. Assad*, 2026-08-28, 3–0: sports event contracts are **not** swaps under the CEA and federal law does **not** preempt state gambling regulation; dissolution of the preliminary injunction affirmed. Confirmed circuit split with the Third Circuit (*KalshiEX v. Flaherty*, April 2026). New Jersey's certiorari deadline was 2026-09-03; whether a petition was filed is unconfirmed as of 2026-09-07. Kalshi geofences NV, WA, MI under orders. The Fifth Circuit (Louisiana) has not ruled. No reported prosecution of an individual prediction-market user in Louisiana.
  - **User decision:** live trading is deferred to the go-live gate review (realistically mid-October). Paper trading and data collection place no wagers. No VPN, VPS relocation, or location spoofing under any circumstances; the statute's element is the person's physical location, and misrepresenting location to the venue is a separate, worse problem.
- **Hosting:** Docker Compose on the user's home NAS (architecture to confirm; multi-arch images if ARM).
- **Budget:** The Odds API 5M-credit tier ($119/mo). Claude API for shadow veto and report annotation. No other paid data in v1.
- **Stack:** Python 3.12, sync code, Postgres 16, SQLAlchemy 2, httpx, APScheduler, FastAPI + Jinja, pytest, Docker Compose.
- **Day-0 actions for the user:** create Kalshi account and API key; request Novig API credentials; subscribe to The Odds API 5M tier; create an Anthropic API key.

## 3. Strategy premises and their evidence

| Premise | Evidence | Design consequence |
|---|---|---|
| NFL/CFB sportsbook closing lines are near-efficient; nobody beats them by forecasting. | Winkelmann et al. 2024; CFB efficiency studies. | No game-prediction model. Fair value is imported from sharp books. |
| Kalshi single-game sports markets inside 48h are close to calibrated (slope 0.90–1.10). The favorite-longshot bias on Kalshi sports lives in horizons beyond one week. | Le 2026, arXiv 2602.19520; Bürgi, Deng, Whelan 2026. | The unconditional gap on game-day markets is small. Single-game trading keeps the 20–80c band. A separate futures/ladder hypothesis (H7) targets long horizons. |
| Makers on Kalshi are net positive only because retail overbuys YES on favorites; one-sided informed flow predicts maker losses. Prediction markets lag sportsbooks by minutes on news. | Bürgi, Deng, Whelan 2026; Ng, Peng, Tao, Zhou SSRN 5331995. | The strategy is a slow market maker. Its cost, adverse selection, must be measured (fill-conditional markout) before any edge number is trusted. |
| Kalshi football series charge makers 0.0175·P·(1−P) per contract and takers 0.07·P·(1−P), each rounded up to the cent per order. | Verified 2026-09-06 via `GET /series/{KXNFLGAME,KXNFLSPREAD,KXNCAAFGAME}`: `quadratic_with_maker_fees`, multiplier 1. | Fees modeled per series from the API. Maker fee is up to 44% of a 1-point edge, so the edge floor is 2 points. No taker path in v1. |
| Retail loses ~15c per dollar on Kalshi combos; cross-game combos price ~3% per leg above the independence product; any member may answer combo RFQs. | Sportico/Bloomberg 2026; arXiv 2607.14430; docs.kalshi.com/getting_started/rfqs; Oddpool census. | H5: passive RFQ listener and pricer in paper mode. |
| CLV > +1 pt over hundreds of bets is a mathematical winner regardless of W/L; ROI is unmeasurable at n=100. | OddsPapi methodology; experiment reviewer's power analysis. | CLV and markout with cluster-robust CIs are the KPIs. ROI, P&L, hit rate are reported with CIs and never gate anything. |
| LLM panels lose to Kalshi NFL spreads 72% of the time on 10c+ disagreements; frontier models lost 16–31% in Prediction Arena; current information beats reasoning. | OddsShopper series; arXiv 2604.07355; arXiv 2607.17765. | LLM is shadow veto and report annotator only, measured against a no-veto control. |
| Live in-game momentum is mostly order-book noise; news drift in thin markets is not exploitable after the spread. | botforkalshi guide; Angelini & De Angelis arXiv 2606.07811. | Pre-game only. No live trading. |

## 4. Architecture

Modular monolith, one image, two processes (`harness run` scheduler, `harness serve` dashboard), plus Postgres.

```
harness/
  config/        Pydantic settings; YAML strategy variants; secrets from 0600 file mounts
  recorder/      tick(): fetch every source, store verbatim in raw_responses; WS append-only recorder
  feeds/         The Odds API and ESPN clients: fetch_*() -> dict (I/O), parse_*(dict) -> [Model] (pure)
  venues/        VenueAdapter protocol; kalshi/ (public + authenticated), paper/, novig/ (phase 5)
  matching/      canonical Game/Market model, team aliases (all FBS + NFL), Kalshi and Novig matchers
  pricing/       devig (power, proportional), consensus, fee models per series, derived-fair margin model
  strategy/      run_strategy(snapshot_bundle, variant, now) -> [Signal]; filters as labels; sizing
  variants/      variant registry (frozen YAML -> variant_id), pre-registration
  risk/          risk gate, caps, kill switch, drawdown stop on equity, message budget
  execution/     intents, order state machine, placer (single venue writer), watcher, reconciliation
  paper/         simulate_fills(order, prints, books) pure queue model
  settlement/    ESPN scores, venue settlements, benchmarks, markouts, CLV views, ledger, weekly report tables
  research/      Claude client (JSON schema), shadow veto, report annotator, RFQ pricer
  parlay/        parlay CLI (smart + lottery cards), DK payout/vig math
  dashboard/     FastAPI + Jinja, one page in v1
  replay/        harness replay --from --to --variant over recorded data
  db/            SQLAlchemy models; create_all + numbered .sql until phase 1 stabilizes, then Alembic
```

### 4.1 Scheduled loops

| Loop | Cadence | Does |
|---|---|---|
| Recorder tick | Per sport: every 2 min from 3h before that sport's first kickoff of the day until its last kickoff; 5 min all day Sat/Sun; 15 min otherwise; none 01:00–08:00 CT. Burst 15–20s from T−100 to T−60 min for NFL games (inactives window) and for 10 min after any venue mid move ≥ 2 pts. Quiet hours are suppressed while a game of that sport is in progress (kickoff to kickoff + 4 h). | Odds API featured + alternates, Kalshi bulk markets, Kalshi trades since last cursor, Kalshi full ladders for games inside T−3h, ESPN scoreboard. Everything to `raw_responses`. Then normalize. |
| WS recorder | Continuous, reconnecting | Kalshi `trade` and `orderbook_delta` for all matched football tickers, appended to `orderbook_events`/`venue_trades`. Records only; never drives execution. |
| Strategy | After each tick | Fair values → gap snapshots → signals per registered variant (filters as labels) → intents for the primary variant |
| Execution | Every 60–120s | Reconcile resting orders to target: place/amend/cancel (paper: insert orders rows). Watcher rules §9.2 |
| Paper fills | After each tick and WS batch | `simulate_fills` over new prints for every open paper order |
| Settlement | Nightly 03:00 CT and hourly during game windows | ESPN scores, venue settlements (live), benchmarks, markouts, CLV, ledger |
| Weekly report | Mon 09:00 CT | Code-generated tables (§7.2); Claude annotates |
| Futures snapshot | Weekly Tue 09:00 CT | Every Kalshi football futures and ladder contract, for H7 |
| RFQ listener | Continuous when Kalshi RFQ feed available | Log RFQs, compute paper quotes (§8.2) |
| Backup | Nightly | Encrypted `pg_dump` |

### 4.2 Job semantics

- APScheduler `BackgroundScheduler`, all jobs `max_instances=1`, `coalesce=True`, `misfire_grace_time=60`. An overrunning tick causes the next to be skipped and logged, never queued.
- Every HTTP call has a 10s timeout; a tick has a 100s budget after which remaining ladder fetches are skipped and counted on `runs`.
- `raw_responses` is append-only. Every normalized table is unique on `(raw_id, natural key)`. Signals are unique on `(run_id, variant_id, venue_market_id, side)`. `client_order_id = intent_id`.
- All internal times are tz-aware UTC. `America/Chicago` appears only in APScheduler trigger timezones and dashboard rendering. Container sets `TZ=UTC` with tzdata. DST ends 2026-11-01.
- Clock check each tick: local time vs venue `Date` header; skew > 30s trips the kill switch.
- Startup reconciliation runs before any job (§9.1).
- Run status: `ok` | `degraded` (secondary-source non-2xx only) | `error` (exception or primary-source non-2xx) | `skipped`. Health is 503 only on stale or error.

## 5. Data layer

### 5.1 Sportsbook odds — The Odds API
- Sports `americanfootball_nfl`, `americanfootball_ncaaf`. Featured markets `h2h,spreads,totals` via `/sports/{sport}/odds`; `alternate_spreads,alternate_totals` via `/sports/{sport}/events/{id}/odds` for every event that has a Kalshi rung in the 20–80c band (15 min cadence, 2 min inside T−3h). `bookmakers=` parameter, never `regions=`.
- Sharp set: `pinnacle` (weight 0.65), BOL family = `betonlineag` + `lowvig` collapsed to one vote (weight 0.35). Circa is not available. Soft set: `draftkings`, `fanduel`. Cross-venue feed: `novig` bookmaker key as a read-only Novig price feed until credentials arrive; `kalshi` key recorded for comparison with our own capture.
- Every row stores the bookmaker's `last_update` and our `fetched_at`. Credit cost per tick logged on `runs`. Monthly budget alarm at 80% of tier.
- No historical endpoint in v1.

### 5.2 Venue adapters
Protocol (sync):

```
list_markets(sport, window) -> [VenueMarket]
get_orderbook(market_id) -> OrderBook          # two bid ladders (yes, no); asks derived
get_trades(market_id, since_cursor) -> [Print]  # price, count, taker_side, ts, trade_id
place_limit(Order) -> VenueOrder                 # Order(prob: Decimal, contracts: Decimal, outcome: YES|NO, expiry, post_only)
amend(order_id, prob, contracts) -> VenueOrder
cancel(order_id) -> None
cancel_group(group_id) -> None
get_orders(status) / get_fills(since) / get_positions() / get_balance()
fee(role, prob, contracts, in_game=False) -> Decimal   # dollars, per order, ceiling to cent
ticks() -> TickTable
```

- **Kalshi:** prices and counts are fixed-point strings; side is bid/ask on the YES leg (buy NO = ask on YES); `self_trade_prevention_type=maker`; `time_in_force=good_till_canceled` with `expiration_time`; `cancel_order_on_pause=true`; `order_group_id` on every order; `post_only=true`. Fee model read at startup from `GET /series/{ticker}` and cached; unit test asserts maker 0.0175 and taker 0.07 on `KXNFL*` and `KXNCAAF*`. Public endpoints (markets, orderbook, trades) need no key. Demo environment for lifecycle smoke tests only; demo prices are not evidence.
- **Novig (phase 5):** OAuth2 client credentials; prices are decimal probabilities to 3 dp snapped to a non-uniform tick table; `outcomeId` not side; qty in minimal units where 100 = one $1 contract for `CASH` (hardcoded; `COIN` is play money usable for live-API paper tests); pre-game straights are fee-free both sides, in-game taker 0.03·P·(1−P), RFQ taker coefficient 0.10; `tif=PO` with `ttl` ms; place response means "queued", reconcile via get-orders and the private WS channel; wash fills cancel both own orders.
- **Paper:** implements the write half by inserting `orders` rows with `mode=paper`; read methods delegate to the Kalshi public adapter; balance is config. Never loads private keys.
- Pre-send invariant independent of encoders: `prob × contracts ≤ per_bet_cap_dollars` and `contracts ≤ contract_cap`. Post-send echo check: the venue's returned order decoded back must equal the intent, else cancel immediately and freeze that market.

### 5.3 Canonical model and matching
- `Game(sport, home_team_id, away_team_id, kickoff_utc, odds_api_event_id [canonical identity], espn_event_id, status)`.
- `Market(game_id, type ∈ {moneyline, spread, total, team_total}, threshold: Decimal ending in .5 or null for moneyline, side, push_rule ∈ {none, push_on_integer})`. Book integer lines carry `push_on_integer`, feed pricing only, and match no Kalshi contract.
- **Kalshi matching:** game from `event.title` split on " vs " plus event date; side from `yes_sub_title`; threshold from `floor_strike`; ticker parsing is a fallback that can only lower confidence. Confidence 1.0 only when both team names alias-resolve and ESPN kickoff is within 1h. Fixture of 50 real tickers/titles from the live API.
- **Novig matching:** `homeTeam`/`awayTeam`/`scheduledStart` + `market.type` + `strike`.
- `team_aliases(source, raw_name, team_id)` seeded for all NFL and FBS teams from the ESPN team list on day 1; unmatched names surface on the dashboard and are added by hand. Anything below the confidence threshold is `unmatched`, never traded.

### 5.4 Schedules and scores
ESPN public scoreboard for kickoff times (re-read each tick; changed kickoffs get a `kickoff_changed` note) and final scores. Paper mode: ESPN scores settle positions. Live mode: venue settlement is the source of truth, ESPN is a cross-check that flags disagreements.

### 5.5 Database
Postgres, all timestamps `timestamptz` UTC. Tables (key columns and indexes):

- `raw_responses(id, source, endpoint, params_json, fetched_at, http_status, body jsonb)`; index `(source, fetched_at)`; BRIN on `fetched_at`.
- `runs(id, started_at, finished_at, status, error, config_hash, n_requests, credits_used, replay bool)`.
- `teams(id, sport, canonical_name, popularity_tier)`; `team_aliases(source, raw_name, team_id)` unique `(source, raw_name)`.
- `games(...)` as §5.3, unique `odds_api_event_id`.
- `odds_snapshots(id, raw_id, run_id, book, game_id, market_type, outcome, threshold, price_decimal, book_last_update, fetched_at)`; unique `(raw_id, book, market_type, outcome, threshold)`; index `(game_id, market_type, fetched_at)`.
- `venue_markets(id, venue, ticker unique, game_id, market_type, threshold, side, close_time, series_ticker, match_confidence, match_status ∈ {matched, unmatched, manual}, fee_type, fee_multiplier)`.
- `venue_quotes(id, raw_id, run_id, venue_market_id, yes_bid, yes_ask, no_bid, no_ask, bid_size, ask_size, volume, open_interest, fetched_at)`; index `(venue_market_id, fetched_at)`.
- `orderbook_snapshots(id, raw_id, venue_market_id, fetched_at, yes_bids jsonb, no_bids jsonb)` full ladder, cap 20 levels/side; index `(venue_market_id, fetched_at)`.
- `orderbook_events(venue, market_id, ts, seq, side, price, size_delta, raw)` WS deltas, append-only.
- `venue_trades(venue, market_id, trade_id unique, ts, price, count, taker_side, is_block)`; index `(market_id, ts)`.
- `fair_values(run_id, game_id, market_type, outcome, threshold, fair_p, n_books, disagreement, method, fair_source ∈ {direct, interpolated, derived}, newest_book_ts, staleness_s)`; PK `(run_id, game_id, market_type, outcome, threshold)`.
- `market_gap_snapshots(id, run_id, venue_market_id, fair_id, book_snapshot_id, venue_mid, best_bid, best_ask, bid_size, ask_size, fair_p, n_books, disagreement, gap_mid, gap_maker_target_net, ttk_minutes, dow, hour_ct, price_bucket, venue_volume_24h, open_interest, soft_minus_sharp, home_popularity_tier, away_popularity_tier, home_rank, away_rank)`; index `(venue_market_id, run_id)`.
- `gap_outcomes(gap_snapshot_id, benchmark_type, clv_mid, clv_maker_target)`.
- `benchmarks(game_id, market_type, outcome, threshold, benchmark_type ∈ {pinnacle_t5, consensus_t5, consensus_t60, consensus_t180, opening_first_seen, kalshi_mid_t5, kalshi_last_trade_pre_kick, novig_mid_t5, result}, p, ts, source_ts, stale bool)`.
- `strategy_variants(variant_id, config_json, registered_at, tier ∈ {primary, secondary, replay})`; `config_history(config_hash, config_json, first_seen)`.
- `signals(id, run_id, variant_id, venue_market_id, side, fair_p, fair_source, venue_best_bid, venue_best_ask, price_target, fee_at_target, as_estimate, edge, edge_min, stake, contracts, decision, labels jsonb [every filter result], rejection_reason, veto_decision, veto_reason, config_hash, created_at)`; unique `(run_id, variant_id, venue_market_id, side)`.
- `intents(id uuid, signal_id, venue, target_prob, target_contracts, created_at)`.
- `orders(id, intent_id, variant_id, venue, mode ∈ {paper, live}, client_order_id unique, venue_order_id, order_group_id, ticker, outcome, prob, contracts, status, placed_at, expiry, cancelled_at, cancel_reason, fair_p_at_place, fair_books_json, venue_bid_at_place, venue_ask_at_place, queue_ahead_at_place, edge_at_place, config_hash)`; partial unique index on `(venue, ticker, outcome)` where status is non-terminal.
- `order_events(order_id, ts, kind ∈ {place, amend, cancel, reject, expire}, prob, contracts, fair_p_at_event, reason)`.
- `fills(id, order_id, prob, contracts, fee, filled_at, simulated bool, fill_method ∈ {queue_model, snapshot_cross, live}, source_trade_id, source_snapshot_id, taker_side)`.
- `markouts(order_id, fill_id nullable, horizon ∈ {1m, 5m, 30m, 120m, close}, fair_p, venue_mid)` for filled and unfilled orders alike.
- `settlements(game_id, home_score, away_score, source, settled_at)`; `venue_settlements(venue, ticker, result, payout, settled_at)`.
- `ledger(...)` append-only: per fill, settlement, position open/close pair (FIFO basis and proceeds), deposit/withdrawal, DK parlay ticket; per-year CSV export; retained 7 years.
- `research_notes(id, kind, subject_id, prompt, response_json, tokens, cost, source_urls, created_at)`.
- `rfqs(id, venue, rfq_id, ts, legs_json, size, requester_side)`; `rfq_quotes(rfq_id, our_fair, our_quote, margin, would_accept bool, market_best_quote, graded_p_close, graded_result)`.
- `news_events(id, game_id, kind, source, event_ts, first_sharp_move_ts, first_venue_move_ts, depth_at_stale_price)`.
- `parlays(id, card_type, legs_json, dk_odds_json, dk_payout_est, true_p_est, hold_est, stake, placed bool, actual_dk_payout, result)`.
- `kill_switch(id, active, reason, set_at)`; `venue_status(venue, available bool, reason, since)`.
- `positions` and `clv` are SQL views over `orders`, `fills`, `benchmarks`.

Volume estimate: NCAAF Saturdays at 2 min produce ~4k normalized rows per tick; three weeks is on the order of 50–75M rows. Indexes above are mandatory; `raw_responses` is partitioned by week.

## 6. Pricing and strategy

### 6.0 Units
Probabilities are `Decimal` in [0,1] at 4 dp everywhere inside the system; venue formats convert at the adapter boundary. Fees are dollars per order, `ceil_to_cent(rate × contracts × p × (1−p))`, and per-contract fee is that divided by contracts. Stake is dollars; `contracts = floor(stake / p)`; `< 1` contract rejects with `below_min_contracts`. "Points" means probability points (0.01).

### 6.1 Devig
Power method per book per market (find k with Σ pᵢᵏ = 1); proportional fallback if the solver fails. Applied to featured and alternate lines.

### 6.2 Consensus fair value
- Weighted mean in log-odds space: Pinnacle 0.65, BOL family 0.35. Pinnacle absent ⇒ `no_sharp` (recorded in the gap dataset, never traded).
- Exact half-point threshold must be quoted by both sharp groups for `fair_source=direct`. Interpolated values (`fair_source=interpolated`) are stored for the gap dataset only.
- `disagreement` = population std of book fair probabilities. `edge_min = clamp(0.02 + 1.5·disagreement, 0.02, 0.06)`.
- `staleness_s` = now − newest book `last_update`; fair values older than 180 s are labelled `stale` and are not tradeable (amended 2026-09-07 from 90 s: featured lines are polled at a ≥120 s cadence, so 90 s would label every row stale).

### 6.3 Target price
- `AS` = trailing measured 30-minute markout on filled orders by (sport, price bucket, side), seeded at 0.01 until 50 fills exist in the bucket.
- One pass: `p0 = fair − edge_min − AS`; `price_target = floor_cents(p0 − fee_per_contract(p0, maker))`; accept if `fair − price_target − fee_per_contract(price_target, maker) ≥ edge_min` and `price_target < best_ask`. No taker path in v1.
- Derived fair values for threshold contracts from a margin-distribution model (normal, sd 13.5 NFL / 17 CFB, key-number mass adjustment at 3 and 7) fitted to the sharp spread, total, and moneyline; stored as `fair_source=derived`; not traded in v1 (H4 measurement only).

### 6.4 Filters (labels in paper mode; every signal is logged with every label)
- Price band 20–80c. Kickoff > 20 min. 24h traded volume ≥ minimum AND spread ≤ 8c NFL / 20c NCAAF. Sharp velocity |Δfair| < 2 pts over 5 min. Match confidence 1.0. Not `no_sharp`, not stale. Large-lot or block taker imbalance in the last 5 min not against our side. Shadow veto recorded, not applied.
- Scope for the primary variant: NFL plus FBS games with a ranked team or two power-conference teams. Everything else is recorded and evaluated by replay.

### 6.5 Sizing
- `p_cond = fair − AS`; `f* = (p_cond − cost) / (1 − cost)`; stake = `0.25 · f* · bankroll`.
- Caps: 3% of bankroll per bet, 5% per game (same-side moneyline and spread on one game are one position; only the higher net edge is placed; totals count separately), 15% open exposure per day, 25 open orders. Floor $10.
- Paper bankroll is a fixed config value for the whole experiment; paper P&L never feeds sizing. Open exposure = filled positions plus resting orders at order price. Live bankroll = venue equity (§9.3).

### 6.6 Routing and consistency signals
Signals and orders carry `venue`; the routing rule (place on the venue with higher net edge) activates when a second venue adapter exists. Arbitrage submission is out of scope for the season. Logged consistency signals: ladder monotonicity (P(over N.5) non-increasing in N), moneyline-vs-spread board divergence > 3 pts, Kalshi-vs-Novig-feed mid gap.

### 6.7 Strategy variants
A variant is a frozen YAML config hashed to `variant_id`. `run_strategy(snapshot_bundle, variant, now)` is pure. Each tick runs the primary plus at most five pre-registered secondaries (registered in `strategy_variants` before Week 1 kickoff); every other variant is evaluated by `harness replay`. Reporting rules: weeks 1–2 explore, week 3 confirms anything selected in week 2; Benjamini–Hochberg at 10% across cells; empirical-Bayes shrinkage of cell means toward the grand mean; cells with n < 30 shown greyed. A `no_veto` secondary is mandatory.

## 7. AI research layer (Claude API, strict JSON)

### 7.1 Shadow veto
- Runs per candidate of the primary variant. Inputs: fair history across books (6h), venue price history, ESPN injury report, weather for outdoor stadiums (NWS), web news search (24h), kickoff time. Retrieved pages are passed as quoted, untrusted data with a fixed instruction; source URLs logged. Output `{decision: proceed|reduce|veto, confidence, reason}`.
- Shadow mode in v1: decision is recorded on the signal and does not change the intent. Promotion to enforcing requires measured shadow accuracy over ≥ 100 vetoed signals and, when enforcing, may only lower size. Cache 30 min, invalidated by any injury-status change. A veto-rate spike trips a dashboard alert.

### 7.2 Weekly report (Monday)
Generated by `settlement/report.py` as fixed tables, each cell carrying n and a 90% cluster-robust (by game) CI:
1. Funnel by sport/venue: markets scanned, signals, candidates, orders, fills, fill rate.
2. Mean net-of-fee CLV per registered variant vs each benchmark; gate status.
3. Adverse selection: fair at fill − fair at place; filled vs cancelled/unfilled CLV.
4. Mispricing map: venue mid − sharp fair by sport × market type × price bucket × TTK bucket × venue.
5. Convergence: median minutes from a sharp move ≥ 2 pts to venue mid covering ≥ 50% of it.
6. Fill realism: share of paper fills confirmed by a print at or through our price.
7. Veto: CLV of vetoed vs proceeded candidates.
8. Data quality: feed latency distribution, snapshot gaps, unmatched markets, credit spend.
9. Flow: signed taker imbalance by size bucket vs next-30m mid and T−5 close (H3).
10. RFQ: quotes logged, margin distribution, graded P&L (H5).
Claude writes at most five bullets that may only cite table cells. No Thursday preview in v1.

## 8. Parlays

### 8.1 DraftKings fun cards (CLI: `harness parlay --sport cfb|nfl`)
- **Smart card:** 3–4 legs from different games; anchor is an LSU or Saints moneyline, spread, or total (props only if fetched via the event-odds endpoint at build time); other legs from the +EV pool at DraftKings prices. Payout = product of leg decimal odds.
- **Lottery card:** 6–8 legs, tiny stake, games the user will watch; correlated legs allowed and labeled "DraftKings will quote lower than this; enter the slip and compare." Record the actual DK payout on mark-placed and compute hold from it.
- Output: legs, DK American odds, estimated payout, our true-probability estimate, implied hold, stake from the fun budget, Claude-written rationale in a fan voice. Recorded in `parlays`. Run by hand Friday (CFB) and Saturday evening (NFL).

### 8.2 Combo RFQ listener (paper, H5)
Subscribe to the Kalshi RFQ feed (and Novig when credentials arrive). For each cross-game RFQ whose legs all have `direct` sharp fair values with disagreement below threshold: `fair = Π leg fair`; quote `yes_bid = fair − margin·legs`, `no_bid = 1 − fair − margin·legs` (default 3c per leg, configurable); collateral cap per RFQ; decline any RFQ with two legs from one game. Log every RFQ and quote to `rfqs`/`rfq_quotes`; grade each quote at settlement against the product of closing leg fair values. Optionally act as requester on synthetic combos (never accept) to log the best market quote. No live quoting in v1.

## 9. Execution and risk

### 9.1 Order lifecycle and startup
`intent → gated → placed → (amended)* → filled | partially_filled | cancelled | expired | rejected → settled`.
Startup reconciliation before any job: pull open orders, fills, positions from each live venue; cancel any resting order unknown to the DB or past its deadline; rebuild positions; only then start loops. Persisted intent UUID before send; never resend blind on timeout, 5xx, or 429; reconcile by `client_order_id` first. The execution loop is the only venue writer and reconciles current state to the strategy's target per market.

### 9.2 Watcher and expiry
- Every live order: Kalshi GTC with `expiration_time = min(kickoff − 10 min, now + 3 × watcher_period)`, renewed each cycle so a dead process leaves orders that self-expire within one cycle; `cancel_order_on_pause=true`; all orders join one order group whose Trigger is the kill switch. Novig: `tif=PO`, `ttl` with the same bound.
- Reprice when fair moves ≥ 1 pt; cancel if edge < edge_min/2, kickoff < 10 min, kill switch, or venue mid moves ≥ 2 pts against the order (from WS, without waiting for the sportsbook feed). Cancel-all at kickoff − 10 min is the fast path; expiry is the guarantee.
- Message budget 60 order messages/min per venue; three consecutive rejects on one order cancel it and freeze that market 15 min; budget breach trips the kill switch. WS seq gap or 30s without ping: stop repricing, resubscribe, rebuild from REST before acting.

### 9.3 Risk gate
Config caps (§6.5); kill switch; drawdown stop on **equity** (cash + mark-to-market positions + resting collateral, net of deposits) down 20% over 7 days; max open orders; sanity checks (0.01 ≤ p ≤ 0.99, notional ≤ cap, confidence 1.0, venue mode); pre-send and echo invariants (§5.2).

### 9.4 Venue outage, geoblock, and withdrawal runbook
Two consecutive auth/geo errors mark a venue `unavailable`, alert on the dashboard, route nothing new to it; manual re-enable. On any Louisiana-specific notice or order affecting either venue: stop opening positions, let resting orders expire, withdraw balances. Venue balances are kept at the minimum needed for the next 7 days; the rest stays in the bank. Never a VPN or location spoofing.

### 9.5 Modes and the go-live gate
- Per-venue mode, default paper. Paper mode never loads private keys.
- **Paper fills:** `simulate_fills(order, prints_after_place, books)` fills a resting order from trade prints at or through its price on the opposite taker side, after first consuming `queue_ahead_at_place`; partial fills accumulate; `fill_method=queue_model`. The optimistic `snapshot_cross` fill is computed in parallel for comparison. When sharp fair moves through our price between two feed snapshots, assume filled before cancel (worst case).
- **Go-live gate (all required):** ≥ 150 paper fills confirmed by prints across ≥ 40 games and both sports; ≥ 30% of fills in NFL or marquee NCAAF (spread ≤ 4c); lower bound of the 90% cluster-robust CI on mean net-of-fee CLV vs `pinnacle_t5` > 0; mean 30-minute markout net of maker fee > 0 with t > 2; adverse drift (fair at fill − fair at place) > −1.0 pt; CLV of filled minus unfilled not significantly negative; mean CLV ≥ 0 under every benchmark; median feed staleness < 90s; zero mismatched-market incidents; a stored passing gate report; the user's separate, documented legal decision; `LIVE_TRADING=1` in the container environment at start plus the config flag.
- **Canary tier** for the first two weeks or 30 live fills: $25 per bet, $200 daily open exposure, Kalshi only. Live adapter classes refuse to construct unless every condition holds.

### 9.6 Data-gathering mode (weeks 1–3)
The product is a dataset. Strategy is a pure function of recorded data; nothing is filtered out of the record (filters, veto, caps, drawdown stop are labels in paper mode; a `constrained` secondary variant applies them for sizing realism); CLV attaches to snapshots, not bets; variants are pre-registered; benchmarks are plural; veto is measured, not trusted; fill simulation is queue-aware and audited weekly against the tape. **Week-3 success** = a mispricing map with at least three cells whose 90% CI excludes zero after shrinkage, a measured convergence lag per venue, and an adverse-selection estimate for maker fills. The go-live decision is a separate, later step.

### 9.7 Pre-registered hypotheses

| # | Hypothesis | Measurement |
|---|---|---|
| H1 | Maker fills on football are adversely selected; markout is negative net of fee. | `markouts` for filled vs unfilled orders. |
| H2 | Venue mid is systematically off sharp fair by sport × market type × price bucket × TTK × popularity. | `market_gap_snapshots` + `gap_outcomes`. |
| H3 | Small-lot retail flow pushes prices toward favorites and popular teams; large/block flow predicts the next move. | `venue_trades` imbalance by size bucket vs next-30m mid and T−5 close. |
| H4 | Spread and total threshold boards are mispriced relative to the moneyline board. | Derived fair vs venue mid per rung; ladder monotonicity violations. |
| H5 | Combo RFQs pay ~3% per leg over the independence product. | `rfq_quotes` graded at settlement. |
| H6 | The venue lags sharp books by a measurable window on inactives and injury news. | `news_events` timestamp triplets; burst-mode snapshots; depth at the stale price. |
| H7 | Long-horizon futures and win-total ladders are compressed toward 50c. | Weekly ladder snapshots vs Monte Carlo fair from game lines; week-over-week drift. |
| H8 | Kalshi vs Novig lead/lag is measurable. | Cross-correlation of mid changes (Novig via Odds API feed until credentials). |
| H9 | The shadow veto improves CLV of proceeded vs vetoed candidates. | Shadow decisions vs outcomes; `no_veto` variant. |

## 10. Settlement, benchmarks, CLV, ledger
- Closing line computed at settlement from recorded snapshots: last snapshot before kickoff whose source timestamp is within 10 min, else `stale=true`. Benchmarks per §5.5.
- CLV (probability) = `p_benchmark − p_used`; CLV (ROI) = `(1/p_used)/(1/p_benchmark) − 1`; net-of-fee variants subtract the per-contract fee.
- Markouts at +1, +5, +30, +120 min and close for every order, filled or not, from placement time.
- Ledger per §5.5; per-tax-year CSV export; monthly reconciliation to venue statements.

## 11. Dashboard (v1: one page)
Health (last tick, errors, credit spend, WS status), last 200 signals with labels and rejection reason, open orders and fills, unmatched venue markets, kill switch. Kill switch ON is unauthenticated; kill switch OFF and any config write require a token; per-venue mode is never a dashboard write. Bound to 127.0.0.1 behind the NAS reverse proxy with auth, or basic auth on the LAN bind. Overview chart, parlay, and research pages after paper execution ships.

## 12. Replay
`harness replay --from --to --variant` runs the identical `run_strategy()` over recorded snapshots, prints, and books, writing signals and paper orders tagged `replay=true`. No synthetic books, no historical Odds API endpoint.

## 13. Testing
- Every source: `fetch_*() -> dict` (I/O, untested) and `parse_*(dict) -> [Model]` (pure, unit-tested). Fixtures exported from `raw_responses` via `harness export-fixture --raw-id`.
- Unit: devig, consensus, fee models (assert maker 0.0175 / taker 0.07 with rounding), units and target price, Kelly and caps, matching on 50 real Kalshi tickers, order state machine, `simulate_fills`, adapter encode/decode round trips for YES and NO.
- `run_strategy` and `simulate_fills` take `now` explicitly. DB tests use `DATABASE_URL_TEST` with `create_all` and truncate.
- End-to-end: `harness replay` over one exported game day, asserting signal counts and zero unmatched-market orders.
- Kalshi demo: manual smoke script for auth, place/amend/cancel/expiry, order groups, fill and settlement parsing.
- TDD throughout; `docker compose run app pytest` locally; no hosted CI in v1.

## 14. Ops
- `docker-compose.yml`: `app-run` (scheduler), `app-serve` (dashboard), `postgres` with healthcheck, named volume, nightly encrypted `pg_dump` (age or gpg) before it touches any synced volume. `restart: unless-stopped`.
- Secrets as 0600 file mounts (Kalshi key id + RSA private key, Novig client id/secret, Odds API key, Anthropic key); never in the DB or `config_history`. Log redaction filter drops Authorization, signature headers, and auth request bodies. Structured JSON logs; `/healthz`.
- Nightly ledger and gate-report backups retained 7 years.

## 15. Build order (calendar days from 2026-09-07; one developer with an AI assistant)
0. **Days 1–2, Recorder.** Deployed on the NAS before the first NFL Week 1 kickoff (Sept 9). `git init` done; pyproject; Compose; settings; `raw_responses` and `runs`; `tick()` with Odds API featured + alternates, Kalshi bulk markets, trades, near-kickoff ladders, ESPN; job semantics; JSON logs; `/healthz`. Never thrown away.
1. **Days 3–4, Normalize and match.** Parsers, aliases for all FBS + NFL, games keyed on Odds API event id, thresholds from `floor_strike`, `harness reprocess`, WS append-only recorder. Check Week 1 match rate; fix aliases.
2. **Days 5–7, Pricing and signals.** Devig, consensus, fee models, edge, filters-as-labels, sizing, variants registry, `market_gap_snapshots`, `harness replay`, one-page dashboard. Pre-register primary + secondaries before Week 2 kickoff.
3. **Days 8–10, Paper execution.** Intents, orders, queue-model fills, watcher, ESPN settlement, benchmarks, markouts, CLV views, weekly report tables. Real paper trading from Week 3.
4. **Days 11–14, Kalshi authenticated adapter.** Demo smoke script, order groups, expiry, reconciliation, message budget, echo checks, drawdown on equity, ledger, encrypted backups, Alembic baseline. Still paper.
5. **Day 15 onward, as the season allows.** Shadow veto; RFQ listener; futures weekly snapshots; parlay CLI; NWS forecast snapshots; Novig adapter when credentials arrive; overview chart. Gate review after §9.6 criteria are met, realistically mid-October, together with the legal decision.

## 16. Open items
- NAS architecture (x86_64 vs ARM).
- Novig credential approval timing.
- Confirm Kalshi RFQ feed access for a retail API key (needed for §8.2).
- Kalshi demo environment fidelity for order groups and expiry.
