# Adversarial Review of the Sportsbook Harness Spec

**Date:** 2026-09-06
**Reviewed:** `2026-09-06-sportsbook-harness-design.md` (v1)
**Reviewers:** six independent agents with distinct lenses: quant/microstructure, venue practitioner, experiment design, novel-edge strategist, risk/security/legal, architecture/scope.
**Lead verification:** Louisiana Act 182 enrolled text read directly; Ninth Circuit *KalshiEX v. Assad* confirmed via Justia and two law-firm summaries; Kalshi football series fee type, strike structure, price format, and close times confirmed against the live public API.

## 1. Overall verdict

The spec is buildable, but as written it would (a) start recording data about a week too late, (b) measure the wrong thing at the go-live gate, and (c) run a market-making business at 2-minute latency without modeling the cost of being a market maker. Three reviewers independently concluded the paper-fill simulator would pass the go-live gate on fiction.

The strategy premise needs one correction. Kalshi single-game sports markets inside 48 hours of close are close to calibrated (Le 2026, arXiv 2602.19520: slope 0.90–1.10, calibration error 0.008). The favorite-longshot bias exists on Kalshi, but in sports it lives in long-horizon markets (futures, win-total ladders), not in game-day moneylines. The unconditional gap between sharp fair value and venue price is real but small on NFL (1–2c spreads), and it is not the edge a maker receives after adverse selection and the 0.0175·P·(1−P) maker fee.

The quant reviewer's verdict, which the lead endorses: the valuable use of the next three weeks is measuring two quantities that decide whether there is a business here at all, fill-conditional markout by side and price bucket, and feed staleness. If markouts come back negative, stop, and the data will have cost only API fees.

## 2. Conflicts between reviewers, and how they are resolved

| Conflict | Resolution |
|---|---|
| Architecture says drop WebSocket for simplicity; quant, experiment, and venue say the trade tape is required for honest fills. | Kalshi's public REST `/markets/trades` endpoint returns every print with timestamp and taker side, paginated. Phase 0 polls it via REST every 2 minutes, which captures the full tape with no WebSocket. An append-only WebSocket recorder (`trade`, `orderbook_delta`) is added in phase 1 for sub-minute book state; it records only and never drives execution. |
| Edge strategist wants Novig for zero-fee pre-game and RFQs; venue reviewer found credentials are by request only and reads return 401 without a token. | Request Novig credentials on day 0. Defer the Novig adapter to phase 5. Until then, The Odds API `novig` bookmaker key serves as a read-only Novig price feed for the cross-venue disparity signal. |
| Edge strategist proposes quoting combo RFQs (selling parlays); spec has the user buying parlays on DraftKings. | User decision (see section 6). Recommended: keep the fun DraftKings card, and add a passive RFQ listener plus pricer in paper mode as a measured hypothesis. |
| Risk says veto agent is bounded and fine; architecture says it sits in the critical path unmeasured. | Veto runs in shadow mode: decision logged on the signal, never changes the intent in v1. A `no_veto` variant is the control. |
| Quant and risk both want the arb module hardened; architecture wants it cut. | Cut for the 2026 season. Cross-venue comparison remains a logged signal. |

## 3. Consolidated changes by spec section

### §2 Context and constraints
- Replace the legal note with dated facts: LGCB December 2025 advisory; Ninth Circuit ruling 2026-08-28 that sports event contracts are likely not swaps and the CEA likely does not preempt Nevada law, splitting with the Third Circuit (April 2026), Supreme Court review expected; Kalshi geofences NV, WA, MI under orders; Louisiana Act 182 effective 2026-08-01 defines "client" as anyone physically in Louisiana making gambling-by-computer wagers, keeps the $500 / six-month misdemeanor tier for clients, makes each wager a separate violation, mandates forfeiture of profits, and gives the AG and LGCB cease-and-desist, account-freezing, and platform-blocking powers; the commodities-exchange exemption (R.S. 14:90.3(M)) remains and its applicability is the disputed question; no reported prosecution of an individual prediction-market user found.
- Add: Novig credentials are by request; submit day 0.
- Add: Odds API tier is the 5M-credit plan ($119/mo); Circa is not on The Odds API; BetOnline and LowVig share a pricing feed and count as one vote.
- Add a line recording the user's decision on live trading from Louisiana.

### §3 Strategy table
- Correct the favorite-longshot row: single-game Kalshi sports markets are near-calibrated within 48h; the bias is in horizons beyond one week. Single-game trading keeps the 20–80c band; a separate futures hypothesis targets the high-price side of ladders and futures.
- Add a row: makers on Kalshi are net positive only because retail overbuys YES on favorites (Bürgi, Deng, Whelan 2026); one-sided informed flow predicts maker losses. Design consequence: measure fill-conditional markout before trusting any edge number.
- Add a row: LLM panels lose to Kalshi NFL spreads 72% of the time on 10c+ disagreements (OddsShopper), and all six frontier models lost 16–31% in Prediction Arena. LLM is veto/research only, in shadow mode until measured.

### §4 Architecture
- Sync code, one process for the scheduler and a second for the dashboard from one image. No async in v1.
- Delete `arb detector`. Add `recorder/` (raw responses, trades, WebSocket events) and `variants/`.
- New §4.2 Job semantics: `max_instances=1`, `coalesce=True`, `misfire_grace_time=60s`; overrunning ticks skip the next, never queue; 10s HTTP timeout; 100s tick budget; append-only raw tables; idempotency keys on every normalized table; `client_order_id = intent_id`.
- Delete the T−5 closing-line job; closing lines are computed at settlement from recorded snapshots.
- Cadence: sportsbook 2 min per sport from 3h before that sport's first kickoff until its last kickoff, 5 min all day Saturday and Sunday, 15 min otherwise, none 01:00–08:00 CT. Burst mode at 15–20s from T−100 to T−60 min for NFL (inactives window) and on any venue mid move ≥ 2c. Kalshi trades and top-of-book every tick; full ladder every 5 min inside T−3h.

### §5 Data layer
- §5.1: add `alternate_spreads` and `alternate_totals` via the per-event endpoint; use `bookmakers=` not `regions=`; store each bookmaker's `last_update` and the fetch time on every row; log credit cost per tick on `runs`.
- §5.2: prices are `Decimal` probabilities, not cents; contracts `Decimal`; outcome canonical YES/NO; each adapter owns wire encoding (Kalshi: dollar strings, bid/ask on the YES leg; Novig: tick-table probabilities, `outcomeId`, qty where 100 = one contract). Fee model per series read from `GET /series/{ticker}` at startup, maker 0.0175, taker 0.07, ceiling to the cent per order. Kalshi order book is two bid ladders; asks derived. Paper venue implements the write half by inserting `orders` rows; fill simulation is a separate pure function over recorded prints.
- §5.3: canonical market is `Market(game, type, threshold ending in .5, side)` plus `push_rule`; Kalshi normalized from `floor_strike` and `yes_sub_title` and event title, never from the ticker; book integer lines feed pricing only and match nothing; match confidence 1.0 only when both teams alias-resolve and ESPN kickoff is within 1h. Alias table covers all FBS teams from day one. Fixture of 50 real Kalshi tickers/titles.
- §5.4: in paper mode ESPN final scores settle positions; in live mode venue settlement is truth.
- §5.5: replace the table list with the architecture reviewer's full column and index list, plus `market_gap_snapshots`, `gap_outcomes`, `benchmarks`, `venue_trades`, `orderbook_events`, `strategy_variants`, `rfqs`/`rfq_quotes` (if adopted), `news_events`, and an append-only `ledger`. Positions and CLV are views.

### §6 Pricing and strategy
- New §6.0 Units: probabilities as `Decimal` in [0,1], 4 dp; fees in dollars per contract; stake in dollars; `contracts = floor(stake / price)`, reject if < 1; velocity and disagreement in probability points.
- §6.2: sharp set = Pinnacle 0.65, BOL family 0.35; require Pinnacle present or mark `no_sharp` (logged, not traded); `edge_min = clamp(0.02 + 1.5·disagreement, 0.02, 0.06)`; reject fair values older than 90s; exact half-point threshold must be quoted by both sharp groups, no interpolation for trading (interpolated fair stored with `fair_method=interpolated` for the gap dataset only).
- §6.3: one-pass target price: `p0 = fair − edge_min − AS`, `price_target = floor_cents(p0 − fee(p0))`, accept if `fair − cost(price_target) ≥ edge_min`. `AS` is the trailing measured markout by sport, price bucket, and side, seeded at 0.01 until 50 fills. Remove the taker fallback. Add derived fair values for threshold contracts from a margin-distribution model, logged with `fair_source=derived`, not traded in v1.
- §6.4: replace the depth filter with 24h traded volume ≥ minimum AND spread ≤ 8c NFL / 20c NCAAF. Add: do not post when large-lot or block taker imbalance in the last 5 minutes points against our side. In paper mode all filters are labels on the signal row, never a reason to skip logging.
- §6.5: `p_cond = fair − AS` feeds Kelly; same-side moneyline and spread on one game are one position; paper bankroll is fixed for the whole experiment and paper P&L never feeds sizing; open exposure counts filled positions plus resting orders at order price.
- §6.6: arb removed. Routing rule kept with a `venue` column. Ladder monotonicity check (P(over N.5) non-increasing in N) logged as an intra-venue consistency signal.
- New §6.7 Strategy variants: frozen YAML config hashed to `variant_id`; `run_strategy(snapshot_bundle, variant, now)` is pure; one primary plus at most five pre-registered secondaries run each tick; all others by replay; weeks 1–2 explore, week 3 confirms; Benjamini-Hochberg at 10%; empirical-Bayes shrinkage; cells with n < 30 greyed.

### §7 AI research layer
- Veto agent in shadow mode; may only lower size when promoted; retrieved pages passed as quoted untrusted data; source URLs logged; veto-rate spike trips a dashboard alert. Cache invalidated on injury-status change, not only on the timer.
- Weekly review is a code-generated report with eight tables carrying n and cluster-robust CIs (funnel, CLV per variant per benchmark, adverse selection, mispricing map, convergence lag, fill realism, veto accuracy, data quality). The LLM writes at most five bullets that cite table cells. Thursday preview dropped from v1.

### §8 Parlays
- Smart card: legs from different games; anchor is an LSU/Saints moneyline, spread, or total; props only if fetched via the event-odds endpoint at build time. Lottery card: correlated legs allowed with the label "DraftKings will quote lower than this; enter the slip and compare"; record the actual DK payout on mark-placed. Delivered as a CLI that prints the card; scheduled job dropped from v1.
- Optional (user decision): passive RFQ listener on Kalshi (and Novig when credentials arrive) that logs every combo RFQ, computes an independence-product quote from leg fair values with a configurable per-leg margin, declines same-game combos, and grades every quote at settlement. Paper only.

### §9 Execution and risk
- Every live order carries venue-side expiry: Kalshi GTC with `expiration_time = min(kickoff − 10 min, now + 3 × watcher period)`, `cancel_order_on_pause=true`, `self_trade_prevention_type=maker`, all orders in one order group whose Trigger is the kill switch. Novig `tif=PO` with matching `ttl`.
- Startup reconciliation before any job: pull open orders, fills, positions; cancel anything unknown or past deadline; rebuild positions.
- Persisted intent UUID before send; never resend blind on timeout, 5xx, or 429; partial unique index enforcing at most one non-terminal order per (venue, market, side); execution loop is the only venue writer and reconciles current state to target.
- Pre-send invariant independent of the encoder: `price × contracts ≤ per-bet cap` and `contracts ≤ cap`. Post-send echo check: decoded venue order must equal the intent or cancel and freeze the market.
- Per-venue message budget (60 order messages/min); three consecutive rejects cancel the order and freeze the market 15 min; budget breach trips the kill switch. WebSocket seq gap or 30s without ping: stop repricing, resubscribe, rebuild from REST.
- Live requires all of: `LIVE_TRADING=1` in the container environment at start, the config flag, a stored passing gate report, and a canary tier for the first two weeks or 30 fills ($25 per bet, $200 daily open exposure, Kalshi only). Paper mode never loads private keys. Dashboard: kill switch ON is unauthenticated; kill switch OFF and any config write require a token; per-venue mode is not a dashboard write.
- Drawdown stop measures equity (cash + mark-to-market + resting collateral, net of deposits), not balance.
- Venue-withdrawal runbook: on any Louisiana-specific notice, stop opening positions, let orders expire, withdraw. Keep venue balances at the 7-day minimum. No VPN or location spoofing, ever.
- Clock: all internal times tz-aware UTC; CT only in APScheduler triggers with `timezone="America/Chicago"` and in rendering; container `TZ=UTC` with tzdata; skew check against venue `Date` header trips the kill switch above 30s. DST ends 2026-11-01.
- Go-live gate (replaces v1): ≥ 150 paper fills confirmed by trade prints across ≥ 40 games and both sports; ≥ 30% of fills in NFL or marquee NCAAF; lower bound of the 90% cluster-robust CI on mean net-of-fee CLV vs Pinnacle T−5 > 0; mean 30-minute markout net of maker fee > 0 with t > 2; adverse drift (fair at fill − fair at place) > −1.0 pt; CLV of filled minus unfilled not significantly negative; median feed staleness < 90s; zero mismatched markets; explicit config change. ROI, P&L, and hit rate reported with CIs and never gate inputs.
- New §9.6 Data-gathering mode (weeks 1–3): the product is a dataset. Strategy is a pure function of recorded data; nothing is filtered out of the record; CLV attaches to snapshots not bets; variants are pre-registered; benchmarks are plural; veto is measured not trusted; fill simulation is queue-aware and audited weekly against the tape; success at week 3 is a mispricing map with at least three cells whose 90% CI excludes zero after shrinkage, a measured convergence lag per venue, and an adverse-selection estimate for maker fills.

### §10 Settlement and CLV
- `benchmarks` table with types: pinnacle_t5, consensus_t5, consensus_t60, consensus_t180, opening_first_seen, kalshi_mid_t5, kalshi_last_trade_pre_kick, novig_mid_t5, result. Close = last snapshot before kickoff whose source timestamp is within 10 min; else flagged stale.
- Per order: fair per book with timestamps at place, every amend and cancel with fair at that moment, queue ahead at placement; per fill: markouts at +1, +5, +30, +120 min and close; per unfilled order: the same markouts from placement.
- Append-only `ledger` for tax: per fill, settlement, position open/close pairs (FIFO cost basis), deposits/withdrawals, DK parlay tickets; per-year CSV export; 7-year retention.

### §11–14 Dashboard, replay, testing, ops
- v1 dashboard: one page with health, last 200 signals with rejection reason, open orders and fills, unmatched markets, kill switch. Bound to 127.0.0.1 behind the NAS reverse proxy with auth, or basic auth on the LAN bind.
- §12 becomes `harness replay --from --to --variant` over recorded data. No synthetic books, no historical Odds API endpoint in v1 (10× credit cost).
- §13: every source has `fetch_*() -> dict` (I/O) and `parse_*(dict) -> [Model]` (pure); fixtures exported from `raw_responses`; `run_strategy` and `simulate_fills` take `now`; test Postgres via `DATABASE_URL_TEST`; end-to-end is a replay over one exported game day; Kalshi demo checks are a manual smoke script; adapter round-trip encode/decode tests for YES and NO.
- §14: secrets as 0600 file mounts, never in the DB; encrypted backups before they touch a synced volume; log redaction of Authorization, signatures, and auth request bodies; `restart: unless-stopped`; Postgres healthcheck dependency; skip Alembic until phase 1 schema stabilizes.

### §15 Build order (revised; calendar days from Sept 7)
0. **Days 1–2, Recorder.** Deployed before the first Week 1 kickoff (Sept 9). Odds API featured + alternates, Kalshi bulk markets + trades + near-kickoff ladders, ESPN scoreboard, all stored verbatim in `raw_responses`. Never thrown away.
1. **Days 3–4, Normalize and match.** Parsers, aliases for all FBS + NFL, games keyed on Odds API event id, thresholds from `floor_strike`, `harness reprocess`. WebSocket append-only recorder for `trade` and `orderbook_delta`.
2. **Days 5–7, Pricing and signals.** Devig, consensus, edge, filters-as-labels, sizing, variants, `market_gap_snapshots`, `harness replay`, one-page dashboard. By Week 2 kickoff the system says what it would bet and why.
3. **Days 8–10, Paper execution.** Intents, orders, queue-model fills from the tape, watcher, ESPN settlement, benchmarks, markouts, CLV views.
4. **Days 11–14, Kalshi authenticated adapter** with demo smoke script, order groups, expiry, reconciliation, message budget. Still paper. Weekly report tables.
5. **Day 15 onward, as the season allows.** Veto agent in shadow; RFQ listener (if adopted); futures ladder weekly snapshots; parlay CLI; Novig adapter when credentials arrive; overview chart. Go-live gate review after the data-gathering criteria are met, realistically mid-October, and only after the Louisiana decision.

## 4. Hypotheses the 3-week dataset must be able to test

| # | Hypothesis | Measurement | Reviewer |
|---|---|---|---|
| H1 | Maker fills on football are adversely selected; markout is negative net of fee. | Fair at place/fill/+30m; filled vs unfilled CLV. | quant, experiment |
| H2 | Venue mid is systematically off sharp fair by sport × market type × price bucket × time-to-kickoff × popularity. | `market_gap_snapshots` + `gap_outcomes` mispricing map. | experiment |
| H3 | Retail taker flow (small lots, Robinhood) pushes prices toward favorites and popular teams; large/block flow predicts the next move. | `venue_trades` signed imbalance by size bucket vs next-30m mid and T−5 close. | edge |
| H4 | Spread and total threshold boards are mispriced relative to the moneyline board on the same game. | Derived fair from margin model vs venue mid per rung; ladder monotonicity violations. | edge, venue |
| H5 | Combo RFQs pay ~3% per leg over the independence product. | `rfqs` and `rfq_quotes` graded at settlement (if adopted). | edge |
| H6 | The venue lags sharp books by a measurable window on inactives and injury news. | `news_events` timestamp triplets; burst-mode snapshots; depth at the stale price. | edge, quant |
| H7 | Long-horizon futures and win-total ladders are compressed toward 50c. | Weekly ladder snapshots vs Monte Carlo fair from game lines; week-over-week drift. | edge |
| H8 | Kalshi vs Novig lead/lag is measurable and one venue leads. | Cross-correlation of mid changes (Novig via Odds API feed until credentials). | edge |
| H9 | The LLM veto improves CLV of proceeded vs vetoed candidates. | Shadow decisions vs outcomes; `no_veto` variant. | experiment, risk |

## 5. Rejected or deferred suggestions

- Group-of-5 CFB rating-composite benchmark (edge #7): deferred; requires a 2025 backtest against closing lines first.
- Weather sub-signal (edge #8): NWS snapshots recorded, no trading logic.
- Own ML/LLM pricer: rejected, consistent with the evidence.
- Live in-game trading: out of scope for the season.

## 6. Decisions pending from the user

1. **Live trading from Louisiana.** Paper only this season, proceed to live after the gate with the wind-down runbook and 7-day-minimum venue balances, or defer the decision to the gate review.
2. **Combo RFQ quoting.** Add the passive RFQ listener and pricer as a paper-mode hypothesis, or keep parlays as DraftKings fun cards only.
3. **Scope cuts.** Confirm cutting from v1: Novig live adapter (deferred to credentials), arb module, historical-Odds-API backtest, scheduled LLM briefs, five of seven dashboard pages, async.

## Sources cited by reviewers

Bürgi, Deng, Whelan, "Makers and Takers" (karlwhelan.com/Papers/Kalshi.pdf); Le, arXiv 2602.19520; arXiv 2607.14430 (combo pricing); Angelini and De Angelis, arXiv 2606.07811; Ng, Peng, Tao, Zhou, SSRN 5331995; Gómez-Cram et al., SSRN 6617059; arXiv 2607.17765 (World Cup LLM benchmark); arXiv 2604.07355 (Prediction Arena); Oddpool RFQ census; docs.kalshi.com (create-order-v2, rfqs, websockets); Kalshi DCM Rulebook v1.29; docs.novig.com (authentication, fees, ticks, place-order, rest-api); Novig eligibility page; Ninth Circuit *KalshiEX v. Assad* No. 25-7516 (2026-08-28); Louisiana HB 883 / Act 182 enrolled text; The Odds API bookmaker and pricing pages; OddsShopper AI-vs-Kalshi series; Sportico and Bloomberg combo-loss reports.
