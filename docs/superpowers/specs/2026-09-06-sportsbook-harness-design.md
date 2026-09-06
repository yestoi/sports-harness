# Sportsbook Harness — Design Spec

**Date:** 2026-09-06
**Status:** Approved in brainstorming; awaiting spec review
**Scope:** 2026 NFL and college football (NCAAF) season

## 1. Purpose

A self-hosted Python system that:

1. Finds and places **+EV straight bets** on CFTC-regulated exchanges (Kalshi, Novig) using sharp-sportsbook fair value, maker-side limit orders, and disciplined sizing.
2. Builds **fun LSU/Saints-flavored parlay cards** for the user to place by hand on DraftKings.
3. Uses an **AI research layer** (Claude API) to veto stale bets, write weekly briefs, and author parlay rationale. AI does not price games.
4. Measures itself by **closing line value (CLV)**, runs in **paper mode first**, and only goes live after passing an explicit gate.

## 2. Context and constraints

- **User:** Louisiana resident, LSU and Saints fan, existing DraftKings account.
- **Execution venues:** Kalshi (public REST/WebSocket API, demo environment) and Novig (CFTC DCM since 2026-08-04, OAuth REST API, commission-free). DraftKings has no API and is manual-only.
- **Legal note:** Louisiana Gaming Control Board's December 2025 advisory calls sports event contracts illegal sports wagering under state law. Kalshi and Novig serve Louisiana under CFTC authority. Enforcement so far targets licensed operators, not residents. The system must tolerate a venue disappearing mid-season (see 9.4).
- **Bankroll:** $2,000–$5,000 across Kalshi and Novig. Separate small "fun budget" for parlays.
- **Hosting:** Docker Compose on the user's home NAS. Assumed x86_64; images built multi-arch if it turns out to be ARM.
- **Budget:** Data and AI spend "whatever it takes within reason" — enough for 2-minute odds polling, historical odds for backtesting, and per-bet LLM research.
- **Delivery:** Local dashboard only. No SMS/email/Slack.
- **Stack:** Python 3.12, Postgres, FastAPI + Jinja templates, APScheduler, SQLAlchemy, httpx, pytest.

## 3. Strategy (the "why" behind the code)

Grounded in the literature and production bots reviewed on 2026-09-06:

| Principle | Source | Design consequence |
|---|---|---|
| NFL/CFB closing lines are near-efficient; nobody beats them by forecasting | Winkelmann et al. 2024; CFB efficiency studies | We do not build a game-prediction model. Fair value is imported from sharp books. |
| Prediction markets lag sportsbooks by minutes on news; cross-venue disparities persist | Ng, Peng, Tao, Zhou (SSRN 2025); arb guides | Edge = sharp fair value minus venue price. Poll fast on game days. |
| Kalshi taker fee ≈ 0.07·P·(1−P) per contract peaks ~1.75¢ at 50¢ — roughly the whole edge | Kalshi fee schedule | Post maker limit orders; watcher keeps them fresh. Fees modeled per venue per side. |
| Fractional Kelly rounds to zero on micro-edges at small bankrolls | mlb-kalshi-bot lessons | Quarter-Kelly with a flat $10 floor and hard caps. |
| CLV > +1% over ~1,000 bets ⇒ winner regardless of W/L | OddsPapi EV/CLV methodology | CLV is the primary KPI and the paper→live gate. |
| Favorite-longshot bias persists in CFB moneylines; fee curve punishes longshots | Sciencedirect 2016; fee math | Trade only contracts priced 20¢–80¢. |
| Own ML/LLM predictors underperform the market; current information beats reasoning | mlb-kalshi-bot; WC2026 LLM benchmark (arXiv 2607.17765) | LLM is a veto/research layer fed live news, not a pricer. |
| Live in-game momentum/mean-reversion is mostly order-book noise | botforkalshi strategy guide | Pre-game only. No live trading in v1. |

## 4. Architecture

Modular monolith: one Python package `harness/` in one container, plus Postgres. Modules communicate via Python interfaces and the database; each is independently testable.

```
harness/
  config/        Pydantic settings, YAML config, per-venue mode (paper|live)
  feeds/         The Odds API client, ESPN schedule/scores client
  venues/        VenueAdapter protocol; kalshi/, novig/, paper/ implementations
  matching/      canonical Game/Market model, team alias table, venue-ticker → canonical matcher
  pricing/       devig (power, proportional), consensus fair value, fee models
  strategy/      edge scan, filters, sizing (Kelly), routing, arb detector
  risk/          risk gate, caps, kill switch, drawdown stop
  execution/     order state machine, placer, watcher, paper fill simulator
  settlement/    fills/settlement sync, scores, P&L, CLV snapshots
  research/      Claude client, veto agent, weekly brief, prompt templates
  parlay/        parlay builder (smart + lottery), payout/vig math
  dashboard/     FastAPI app, Jinja templates, read-only views + kill switch + config
  scheduler/     APScheduler job definitions and cadences
  backtest/      replay historical odds snapshots through strategy
  db/            SQLAlchemy models, Alembic migrations
```

### 4.1 Scheduled loops

| Loop | Cadence | Does |
|---|---|---|
| Odds | Every 2 min when any game kicks off within 3 h; every 15 min otherwise | Pull sportsbook lines + venue order books; write snapshots |
| Strategy | Immediately after each odds loop | Fair value → edge scan → filters → veto → sizing → intents |
| Execution | Every 60–120 s | Risk gate → place/amend/cancel; paper-fill simulation |
| Closing-line snapshot | T−5 min per game | Capture sharp fair value for CLV |
| Settlement | Nightly 03:00 CT + hourly during game windows | Fills, settlements, scores, P&L, CLV |
| Weekly brief | Mon 09:00 CT (review), Thu 09:00 CT (preview) | Claude-authored notes |
| Parlay cards | Fri 12:00 CT (CFB), Sat 20:00 CT (NFL) | Two cards each |
| Backup | Nightly | `pg_dump` to NAS volume |

## 5. Data layer

### 5.1 Sportsbook odds — The Odds API
- Sports: `americanfootball_nfl`, `americanfootball_ncaaf`.
- Markets: `h2h`, `spreads`, `totals`.
- Books: sharp set (configurable; default Pinnacle, Circa if available, BetOnline, LowVig) and soft set (DraftKings, FanDuel) for parlay pricing and arb reference.
- Historical endpoint used by the backtest harness.
- Every response stored as an `odds_snapshot` row per book/market/outcome with timestamp.

### 5.2 Venue adapters
Protocol (all async):

```
list_markets(sport, window) -> [VenueMarket]
get_orderbook(market_id) -> OrderBook (bids/asks with size)
place_limit(market_id, side, price_cents, contracts, post_only=True) -> VenueOrder
amend(order_id, price_cents, contracts) -> VenueOrder
cancel(order_id) -> None
get_orders(status) -> [VenueOrder]
get_fills(since) -> [Fill]
get_positions() -> [Position]
get_balance() -> Money
fee(side, role, price_cents, contracts) -> Money      # role = maker|taker
```

- **Kalshi:** RSA-signed REST; WebSocket for order-book deltas on subscribed tickers; demo environment for integration tests. Fee formula and maker/taker treatment loaded from config and verified against the live fee schedule at implementation time.
- **Novig:** OAuth2 client credentials; REST. Fees default 0; verified at implementation.
- **Paper:** wraps a real adapter's read methods; write methods record orders locally and simulate fills (see 8.3).

### 5.3 Market matching
- Canonical model: `Game(sport, home, away, kickoff_utc)`, `Market(game, type ∈ {moneyline, spread, total}, line, side)`.
- Team alias table (seeded for all NFL + FBS teams, extended when unmatched names appear).
- Venue ticker parser per venue + fuzzy fallback on team names and kickoff within ±1 h.
- Match confidence score; below threshold ⇒ `unmatched` row, never traded, surfaced on dashboard.

### 5.4 Schedules and scores — ESPN public scoreboard
Kickoff times (drive polling cadence), final scores (settlement cross-check). Venue settlement is the P&L source of truth.

### 5.5 Database (Postgres)
Core tables: `games`, `venue_markets`, `odds_snapshots`, `fair_values`, `signals` (one row per market per strategy run, with `decision` and `rejection_reason`), `intents`, `orders`, `fills`, `positions`, `closing_lines`, `clv`, `research_notes`, `parlays`, `runs`, `config_history`, `kill_switch`.

## 6. Pricing and strategy

### 6.1 Devig
- Default: **power method** — find k such that Σ pᵢ^k = 1 over a market's outcomes; fair pᵢ = pᵢ^k.
- Fallback: proportional (pᵢ / Σp) if the solver fails.
- Applied per book per market.

### 6.2 Consensus fair value
- Weighted mean in **log-odds** space. Default weights: Pinnacle 0.5, Circa 0.3, remaining sharps share 0.2. Weights renormalize over books present.
- Require ≥ 2 sharp books, else skip the market.
- `disagreement = std(book fair probabilities)`.
- Dynamic threshold: `edge_min = clamp(0.01 + 1.5·disagreement, 0.01, 0.06)`.

### 6.3 Edge and target price
- For each venue contract (YES/NO ≡ outcome): `cost = price + fee(price, role)`.
- `edge = fair_p − cost`.
- Maker target: `price_target = floor_cents(fair_p − edge_min − maker_fee_at(price))`, never above current best ask (if it would cross, use best ask and re-evaluate as taker).
- Candidate if `fair_p − cost_at(price_target) ≥ edge_min`.

### 6.4 Filters (any fail ⇒ rejection with reason)
- Contract price 20–80¢.
- Kickoff > 20 min away.
- Resting depth at or better than target ≥ configured minimum.
- Sharp-line velocity: |Δfair| over last 5 min < 2 pts (news in flight).
- Match confidence ≥ threshold.
- AI veto ≠ `veto` (see 7.1); `reduce` halves stake.

### 6.5 Sizing
- `f* = (fair_p − cost) / (1 − cost)` per-dollar Kelly on a binary contract; stake = `0.25 · f* · bankroll`.
- Caps: 3% bankroll per bet, 5% per game across correlated markets, 15% open exposure per day, max 25 open orders.
- Floor: $10 flat; below floor ⇒ bet $10 if edge ≥ edge_min, else skip.
- Bankroll = sum of live venue balances (live) or config (paper).

### 6.6 Routing and arbitrage
- Compute edge on Kalshi and Novig; place on the better one. If both qualify and combined size ≤ caps, split proportionally to edge.
- **Arb module:** if `ask_yes(A) + ask_no(B) + fees < 1 − arb_min` (default 0.5%), submit both legs as taker simultaneously. One-leg exposure cap; if leg 2 fails, keep leg 1 only if independently +EV, else cancel/unwind at market.

## 7. AI research layer (Claude API)

All calls use strict JSON schemas and are logged with prompt, response, tokens, cost.

### 7.1 Veto agent
- Trigger: per candidate bet after filters, before sizing.
- Inputs: fair-value history across books (last 6 h), venue price history, ESPN injury report for both teams, weather (outdoor stadiums), web news search for team + "injury|out|questionable" in last 24 h, kickoff time.
- Output: `{decision: proceed|reduce|veto, confidence, reason}`.
- Purpose: detect stale sharp lines or price gaps explained by news the feed has not absorbed. Not prediction.
- Cached per (game, market-type) for 30 min.

### 7.2 Weekly briefs
- **Monday review:** P&L, CLV distribution, hit rate by price bucket, top rejection reasons, veto accuracy (did vetoed bets close worse?), anomalies.
- **Thursday preview:** slate, LSU/Saints notes, markets where sharps disagree most.

### 7.3 Parlay rationale
See §8.

## 8. Parlay builder

- Schedule: Friday (CFB, LSU-anchored) and Saturday night (NFL, Saints-anchored).
- Inputs: +EV pool at DraftKings prices (from the soft-book feed), the LSU/Saints games, marquee games on the slate (ranked matchups, prime-time NFL).
- **Smart card:** 3–4 legs. Anchor = an LSU or Saints market (ML/spread/total/prop when available). Other legs from the +EV pool, preferring positive edge at DraftKings prices and different games (uncorrelated).
- **Lottery card:** 6–8 legs, tiny stake, mostly games the user will watch; correlated legs allowed and labeled.
- Output per card: legs, DraftKings American odds per leg, parlay payout, our estimated true probability (product of fair p, correlation-flagged), implied hold, suggested stake from the fun budget, Claude-written rationale in a fan voice.
- User places manually on DraftKings. System records the card; user marks placed/not and result for tracking.

## 9. Execution and risk

### 9.1 Order state machine
`intent → gated → placed → (amended)* → filled | partially_filled | cancelled | rejected → settled`

### 9.2 Watcher
- Reprice when fair value moves ≥ 1¢ (respecting edge_min).
- Cancel if edge < edge_min / 2, or kickoff < 10 min, or veto arrives, or kill switch set.
- Cancel-all at kickoff − 10 min as a hard rule.

### 9.3 Risk gate (every intent)
Config caps (§6.5), kill switch, drawdown stop (pause all trading if bankroll down 20% over trailing 7 days), max open orders, sanity checks (1¢ ≤ price ≤ 99¢, stake ≤ cap, match confidence, venue mode).

### 9.4 Venue outage / geoblock
If a venue returns auth/geo errors twice in a row: mark venue `unavailable`, cancel nothing (can't), alert on dashboard, route all new intents to the other venue. Manual re-enable.

### 9.5 Paper mode
- Per-venue flag. Default paper for both.
- Fill simulation: order fills at our price if any later snapshot shows best ask ≤ our bid before cancel time. Partial fills by observed size. Conservative by design.
- **Go-live gate:** ≥ 100 paper bets, mean CLV ≥ +1% (probability space), zero mismatched-market incidents, and an explicit config change by the user.

## 10. Settlement and CLV
- Closing line = sharp consensus fair value at T−5 min.
- CLV (probability space) = `p_close − p_used`; CLV (ROI space) = `(1/p_used)/(1/p_close) − 1`.
- P&L from venue fills/settlements; ESPN scores as a cross-check that flags disagreements.

## 11. Dashboard (FastAPI + Jinja, read-mostly)
Pages: Overview (bankroll by venue, P&L, CLV chart, mode badges), Signals (every market considered, decision, rejection reason), Orders & Positions, Parlay Cards (with mark-placed/result), Research Notes, Unmatched Markets, Config & Kill Switch (kill switch and per-venue mode are the only writes).

## 12. Backtest harness
Replays The Odds API historical snapshots through `pricing` and `strategy` with a synthetic venue book (venue price = soft-book devigged price + configurable noise) to tune `edge_min`, weights, and filters before Week 1 goes live. Reports CLV and simulated ROI by threshold.

## 13. Testing
- Unit: devig (power/proportional, edge cases), consensus weights, Kelly and caps, fee models, ticker parsing and matching, order state machine, paper fill simulator.
- Adapter tests with recorded HTTP fixtures; Kalshi integration tests against the demo environment (opt-in, needs creds).
- End-to-end paper run over one recorded game day.
- TDD throughout; CI runs `pytest` in the container.

## 14. Ops
- `docker-compose.yml`: `app` (scheduler + dashboard in one process), `postgres`, named volume, nightly `pg_dump` sidecar.
- Secrets via `.env` (never committed): Odds API key, Kalshi key id + private key, Novig client id/secret, Anthropic key.
- Structured JSON logs; `/healthz` endpoint; dashboard bound to LAN only.

## 15. Build order (each phase independently useful)
1. **Foundations:** repo, config, DB models, migrations, Docker Compose, dashboard skeleton.
2. **Feeds + matching:** Odds API client, ESPN client, canonical model, alias table, snapshots.
3. **Pricing + strategy (paper, no venues):** devig, consensus, edge scan, filters, sizing, signals table, dashboard Signals page. Backtest harness.
4. **Kalshi adapter + paper execution:** order book ingest, matcher, paper fills, watcher, CLV snapshots, settlement.
5. **Novig adapter + routing + arb.**
6. **AI layer:** veto agent, weekly briefs.
7. **Parlay builder + dashboard cards.**
8. **Live gate + hardening:** go-live checklist, outage handling, backups.

## 16. Open items (non-blocking)
- NAS architecture (x86_64 vs ARM) — affects image build only.
- Confirm Circa is available on The Odds API tier chosen; otherwise sharp set = Pinnacle + BetOnline + LowVig.
- Verify current Kalshi maker-fee treatment for NFL/NCAAF series and Novig fee schedule at adapter implementation time; both are config values.
- Kalshi and Novig accounts, API credentials, and funding are the user's to set up.
