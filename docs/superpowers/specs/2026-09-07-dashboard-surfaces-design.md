# Dashboard Surfaces and Telemetry. Design Spec

Date: 2026-09-07. Extends the v2 spec (`2026-09-06-sportsbook-harness-design.md` §4.1, §12) and the phase 3
addendum (`2026-09-07-phase3-paper-execution-design.md` §4, §6). Source brief: the dashboard design brief of
2026-09-07 (its §5 "three jobs" is the design problem; its §6 hard constraints are honoured here except
where §0 says otherwise). User decisions in this spec: U6 (roadmap Decisions table).

## 0. Amendments to earlier specs

1. **"One page, no JS" is lifted for new surfaces only.** The existing page at `/` and the `/api/summary`
   contract (nine checked keys, section names) are frozen: the redesign adds surfaces under `/ui/` and adds
   keys, never reshapes. The legacy page is linked from the new nav as "Legacy panel".
2. **Phase 5(g) "Overview page: dashboard page 2, server-rendered SVG" is absorbed** into the Study surface
   (§2.3) and removed from phase 5. Equity, CLV by week and fills are drawn there from stored data.
3. **Compute once, render in the browser.** No page view runs a query. A snapshot job writes pre-aggregated
   JSON documents on a fixed cadence; the dashboard serves them by primary key. Constraint 2 of the brief
   (every query bounded) becomes structural: the only code that can reach Postgres from the dashboard is
   the snapshot builders, and they carry their own statement timeout below the WebSocket sink's.
4. **New telemetry tables (§3).** Nine additive tables, eight in phase 3 and one in phase 4.5. Five of them cannot be backfilled, so their
   writers land in phase 3 as Task 12b, before week 1 accumulates.
5. **The weekly report persists its cells (§3.7).** `harness report` writes every cell it renders to
   `report_cells` in the same run that writes the markdown. The dashboard reads cells; it never recomputes
   a table (brief constraint 6). A provisional week-to-date evaluation is written hourly by the settlement
   job from the same code path and labelled `provisional`.

## 1. Jobs, tempos, surfaces

One person, three jobs, four surfaces. The mapping is one job per surface plus a fourth for the single
October read.

| Surface | Job (brief §5) | Tempo | Primary device | Refresh |
|---|---|---|---|---|
| **Pulse** | A: is it alive and honest | daily, 30 s | phone | 30 s |
| **Floor** | B: what is it doing right now | game day, watched | desktop second monitor; phone on Saturdays | 15 s in a game window, 60 s otherwise |
| **Study** | C: is the strategy any good | Monday, an hour | desktop | 10 min; week-to-date cells hourly |
| **Gate** | C, decisive | once in October, and every Monday as a rehearsal | desktop | on each `gate_reports` insert |
| **Ticket** | the fun parlays (real money, $50/week, placed by hand) | Friday and Saturday evenings, then during games | phone | 15 s in a game window, 60 s otherwise |

Navigation: one fixed header on every surface with five tabs (Ticket last, styled warm so it reads as the fun one), the Pulse status word (§2.1), a `PAPER`
badge, the build sha, and the age of the oldest snapshot on the current surface. The header never scrolls
away. On a phone the tabs sit at the bottom.

### 1.1 Honesty rules that apply to every surface

- Every monetary or contract figure sits under the word **paper** in the same visual unit (badge, column
  header, chart title). No surface has a view where the word is absent. The one exception is Ticket (§2.5), where every figure is real fun money and
  the badge says so; paper and fun money never appear on the same surface.
- No estimate is drawn without `n_obs`, `n_clusters` and its interval. Below 10 game clusters the mark is
  grey and labelled; below 30 it carries a flag. This is the report's own rule and the UI reads the flags
  from the stored cell rather than re-deriving them.
- Stored definitions are shown beside stored results (gate criteria, benchmark families, variant ids).
  The UI never rounds a stored number differently from the report: cells are rendered from the stored
  string form when one exists.
- Thresholds used to colour anything are imported from the code that enforces them (`STALE_AFTER_S`,
  `CREDITS_LOW_FRACTION`, the executor's 60 s and 120 s, `db_budget_gb`, the 25 % disk gate), never
  restated in the front end. The snapshot carries the threshold beside the value.
- No secret, token, PEM material or raw venue free text is ever placed in a snapshot. Snapshot builders
  pass through the same sanitizer as `/kill`'s reason (F50) for any string that originated outside the
  harness.
- Nothing on any surface acts. The `/kill` and `/unkill` forms stay on the legacy page only.

### 1.2 Learnable by anyone (user decision, 2026-09-07 evening)

The surfaces must make sense to someone who has never seen the system, without losing any insight the
operator needs. Audience: the operator plus anyone they show it to, so the plain layer is always on and
the technical names stay visible in small type for cross-reference with the reports.

- **Sentence first, evidence second.** Every section opens with one to three plain-English sentences
  written by fixed templates in the snapshot builder (§4), never in the browser. The chart or table under
  the sentence is its evidence. Templates are pure functions over the payload with unit tests; the
  sentence carries the same numbers the evidence shows, never a different rounding.
- **Two-level labels.** The primary label is a plain question or phrase; the technical name sits beside
  or beneath it in small muted type (`Better than the closing price? · CLV vs pinnacle_t5`). Kalshi tickers
  are rendered as `Alabama to win`, `Wisconsin +7`, `Over 55`, with the ticker string secondary.
- **Uncertainty in words.** Sample sizes are translated by one function shared by every template,
  bound to the report's own flags: below 10 game clusters "too few games to say anything" and no estimate
  is stated in the sentence; 10 to 29 "enough to notice, not enough to trust"; 30 or more "enough to
  take seriously". The interval is called the "honest range" and always stated beside an estimate.
  Clusters are explained once per surface as "games, not bets: bets in the same game rise and fall
  together".
- **Tap definitions.** Every remaining technical term carries a definition of two lines (what it is,
  why it matters here) from a static `glossary.json` in the front end, opened by tap or hover on the
  term. One entry per row of the vocabulary table below; the reviewer checks that no term on a surface
  lacks one.
- **How it works.** A static page under `/ui/#how`, linked from the header on every surface: the
  pipeline as a diagram of eight steps (record, match, price, decide, paper order, fill from the
  recorded tape, settle, judge) each with one sentence; the three jobs and which surface answers each;
  what paper means; the honesty rules in plain words; the full glossary.
- **Reason codes in plain words.** Skip, cancel and rejection reasons are shown as phrases with the code
  secondary, from the vocabulary table. A reason not in the table is shown as its code and logged as a
  gap for the next plan.

**Vocabulary (plain phrase, then the technical name it stands for; the glossary entry expands each):**

| Plain | Technical |
|---|---|
| simulated, no money at risk | paper |
| the sharp books' fair price; age of the fair price | fair value; `staleness_s` |
| better than the closing price? | CLV (closing line value) |
| Pinnacle's price 5 min before kickoff; the consensus closing price; Kalshi's last trade before kickoff; the final result | `pinnacle_t5`; `consensus_close`; `kalshi_last_trade_pre_kick`; `result` |
| did the price move our way after the fill? | markout |
| wanted to bet | intent |
| passed on | skip |
| the price we wanted was already gone; too close to kickoff; the order book could not be trusted; our fair price was too old; already at the open-order limit; hit a bankroll cap; could not tell which game this market was; not enough edge | `post_only_reject`; `kickoff`; `book_dirty`; `fair_stale`; `exec_capacity`; `cap_*`; `unmatched`; `edge_below_min` |
| the market moved against us; moved our order to a new price; the edge shrank; the signal went away; the stop button was on; kickoff arrived | cancel reasons `venue_move`; `reprice`; `edge_decay`; `signal_rejected`; `kill_switch`; expiry |
| orders ahead of ours at this price | `queue_ahead_at_place`, `queue_remaining` |
| filled from the recorded order book; would have filled without our watcher; the market crossed our price | `queue_model`; `no_watcher`; `snapshot_cross` |
| a real trade printed at our price | `has_print` |
| games, not bets | `n_clusters` |
| honest range | cluster-robust 90 % interval |
| strategy variant; the one being judged; the pre-registered original | variant; gate variant; primary |
| the stop button: no new orders, cancel everything | kill switch |
| the live list of everyone's orders on Kalshi, recorded | order book, tape |
| this page's data, computed N s ago | dashboard snapshot |
| a week still being counted | `provisional` |
| the twelve tests for going live | gate criteria |

## 2. Surfaces

Each surface lists: the job, the layout in reading order, every metric with its source table, the rule
that colours it, and what must never appear.

### 2.1 Pulse

**Job.** Answer "is it fine?" in one glance, and when it is not, name the rule that fired.

**Layout.** (1) One status word, full width: `FINE`, `WATCH`, or `BROKEN`, with the list of fired rules
under it, newest first, each naming the rule, the value and the threshold. (2) A 24 h **tape continuity
strip**: one horizontal bar per source (`ws events`, `odds`, `kalshi rest`, `espn`) coloured by activity
per 5 min bucket, gaps drawn as breaks. (3) A **vitals row** of stat tiles, each with a 24 h sparkline.
(4) **Storage arc**: DB size against the ceiling with days-to-ceiling. (5) The **invariant wall**: every
check in the latest sweep as a small tile, pass or fail, with the check name; tap for value and SQL name.
(6) **Recent operator events**, last ten.

**Status rules.** `BROKEN` when any of: recorder last run older than `STALE_AFTER_S` (20 min); executor
heartbeat older than 120 s; WS last event older than 120 s; any `gap` row in the last 2 h; kill switch
active; free space on `/volume1` below 25 %; DB size above 80 % of `db_budget_gb`; any `check_results`
row `fail` in the latest sweep; settlement job `error` in the last 24 h; any snapshot older than three
times its cadence. `WATCH` when any of: heartbeat older than 60 s; WS last event older than 60 s; DB above
60 % of ceiling; credits below 40 % of the monthly budget (`BROKEN` below `CREDITS_LOW_FRACTION`, 20 %);
`budget_exhausted` on the two newest settle runs; `book_dirty_markets > 0` while a game is in progress;
any snapshot older than twice its cadence. Otherwise `FINE`. Every rule has a name and the fired list
shows it. This is the whole list; a red that is not one of these is a bug.

**Metrics and sources.**

| Metric | Source |
|---|---|
| recorder last run age, last status | `runs` (newest by id), `compute_health` |
| executor heartbeat age, loop ms, p95, loops skipped, dirty markets, WS event age | `exec_heartbeat` (now), `metric_samples` (24 h sparkline) |
| WS events per minute, trades per minute, reconnects, gaps | `metric_samples` (`ws.*`), written by the WS recorder; never a count on `orderbook_events` |
| tape continuity strip | `metric_samples` (`ws.events_per_min`, `recorder.fetched{source}`) |
| credits remaining, budget, daily burn | `runs.odds_remaining`, `metric_samples` (`recorder.credits_remaining`) |
| DB size, six largest tables, growth per day, days to ceiling | `job_runs.notes` (housekeeping), `metric_samples` (`db.*`) |
| free disk, available memory | `metric_samples` (`host.disk_free_gb`, `host.mem_available_mb`) |
| kill switch state and history | `kill_switch`, `operator_events` (`kill_on`, `kill_off`) |
| invariant wall | `check_results` (latest sweep) |
| settlement job status, budget exhausted | `job_runs` |
| operator events | `operator_events` |
| snapshot ages | `dashboard_snapshots.generated_at` |

**Never shown here.** Any P&L, CLV or strategy figure. Pulse is about the machine, not the edge. No
table wider than a phone. No button.

### 2.2 Floor

**Job.** Show what the executor is doing during a game window, well enough that "running but stupid" is
visible, and engagingly enough to leave open.

**Layout.** (1) **Game board**: one card per game with a matched venue market and kickoff within the
next 24 h or status `in_progress`. Card: teams, kickoff countdown or live score with period and clock,
number of matched markets, our open paper orders and open positions on the game as small variant-coloured
markers. Sorted: in progress first, then by kickoff. (2) **Funnel**, trailing 6 h: a left-to-right flow
from raw ticks to gap snapshots to candidates (by variant) to intents to orders to fills, with the leaks
drawn as labelled branches leaving the flow: signals rejected by reason, intents skipped by reason,
orders cancelled by reason. Widths are counts; every branch has its count. (3) **Open paper orders**:
one row or card per open order with a **queue bar** (`queue_remaining` against `queue_ahead_at_place`,
filled portion grows as prints consume the queue), age, our price against best bid and ask, current fair
and edge, book source, dirty minutes, variant. Sorted by queue remaining ascending. The bar's history is
a sparkline from `order_watch_samples`. (4) **Fills stream**: the last 50 fills today, newest first, each
with game, side, price, contracts, method, `has_print`, `through`, tape source. (5) **Exposure lanes**:
per exec variant, open stake, open positions, fills today, daily cap use for `apply_caps` variants, and
paper cash and marked-to-market equity from the newest `equity_snapshots` row. (6) **Executor vitals
strip**: loop ms, WS events per minute, dirty markets, skips per minute as 2 h sparklines.

**Metrics and sources.**

| Metric | Source |
|---|---|
| games, kickoff, status | `games`, `teams` |
| live score, period, clock | `game_score_events` (newest per game), labelled with its ESPN age |
| matched markets per game | `venue_markets` (`game_id`, `match_status`) |
| funnel counts | `runs.notes` (ticks), `market_gap_snapshots`, `signals` (by `decision`, `rejection_reason`, `variant_id`), `intents`, `order_events` (`kind = skipped`, `reason`; `kind = cancel`, `reason`), `orders`, `fills`; all `created_at >= now - 6 h` |
| open orders | `orders` (status open or partially filled), `order_watch_samples` |
| current fair and edge per open order | `fair_values` newest per market (bounded by open orders) |
| fills | `fills` joined to `orders`, `games`, `teams`; `LIMIT 50` |
| exposure, cap use | `positions` view, `orders`, `fills` since 00:00 CT, variant config |
| paper cash, MTM equity | `equity_snapshots` newest per variant |
| vitals | `metric_samples` (`exec.*`, `ws.events_per_min`) |

**Never shown here.** CLV, markouts or any figure that judges the strategy. Floor shows activity, not
quality, so that a good afternoon of fills is never mistaken for edge. No cumulative P&L number larger
than the per-variant equity tile. No control.

### 2.3 Study

**Job.** Make the weekly read rigorous and make it hard to fool yourself.

**Layout.** A week selector (ISO weeks from 37; the current week marked `provisional`). (1) **Variant
ledger**, t1: one row per variant, each cell drawn as an interval mark with `n_clusters` beside it.
(2) **CLV small multiples**: one panel per benchmark family, mean CLV per variant with cluster-robust
intervals; the `result` and `opening_first_seen` panels carry the stored "reported, never gated" note.
(3) **Paired contrasts forest plot**, t2: each secondary against the primary, per benchmark, interval
and point, zero line drawn. (4) **Fill and markout panel**, t3: the three fill sets with 1 m and 5 m
markouts; the feed-kind and staleness split. (5) **Strata heatmap**, t4: the 72-cell grid, shrunk
estimates, cells grey below 10 clusters, flagged below 30, the sign line from `header`. (6) **Survival**,
t5: featured-only intervals with `km_median`. (7) **Validity panel**, t6, and **data quality**, t8, as
compact tables. (8) **Equity curves**: per variant, realized cash and cash plus mark-to-market as two
lines, `operator_events` drawn as annotated ticks (deploys, config changes, amendments, kill toggles).
(9) **Declined candidates**: rejected signals and skipped intents by reason over the week, and their
counterfactual CLV from `gap_outcomes` at the variant's own `price_target` (table t12, §3.7). (10) The
week's markdown report, rendered, collapsed by default, with the "model-written, unverified" block (phase
5e) visibly fenced when present.

**Metrics and sources.**

| Metric | Source |
|---|---|
| every table cell | `report_cells` (newest `report_runs` row for the week); the UI never computes a mean |
| equity curves | `equity_snapshots`; annotations from `operator_events` |
| declined candidates | `report_cells` table `t12` |
| markdown | `docs/reports/2026-wNN.md` is not on the NAS; the snapshot carries `report_runs.markdown_sha256` and the UI shows the stored markdown from `report_runs.markdown` |

**Never shown here.** A mean without its interval and cluster count. A dollar figure extrapolated beyond
the observed weeks. Any cell recomputed client-side. Anything for the current week without the
`provisional` label.

### 2.4 Gate

**Job.** Present the go-live gate as evidence, in the shape it will be read in October.

**Layout.** (1) One word: `NOT PASSING` or `PASSING`, for the gate variant, with the evaluation date and
`criteria_hash`. (2) The **twelve criteria** as rows: name, stored definition text, stored threshold,
measured value, `n`, status in {pass, fail, insufficient}, and a small history of that criterion across
every stored evaluation. (3) **Gate variant beside the primary**: the pre-registered YES-only primary's
row rendered in the same twelve-row shape, labelled as reported, not gated. (4) The standing text: paper
only; live trading is a separate legal decision; the loop never decides.

**Metrics and sources.** Everything from `gate_reports` (`criteria_json`, `criteria_hash`, `passed`,
`gate_variant`, `evaluated_at`) and `strategy_variants` for names. Nothing else.

**Never shown here.** A projection. A recomputed criterion. Any variant under a name other than its
registered one. A button.

### 2.5 Ticket (user decision, 2026-09-07 evening)

**Job.** Make watching the week's fun parlays a joy, and keep the fun money visibly separate from the
research. This is the one surface that shows **real money**: the owner's $50-a-week fun budget (roadmap
phase 5c, v2 spec §8.1), placed by hand at DraftKings. Phone-first: it is opened at a bar on a Saturday.

**Concept: the ticket.** A physical betting slip on the dark ground: warm paper, perforated edges, ink
type. Legs run down the slip as a chain of lamps: grey while pending, gold and pulsing while alive, green
with a HIT stamp, red and torn when missed. The potential payout sits at the top in the largest type on
any surface and dims as legs fall. A diagonal CASHED or BUSTED stamp ends the card. Motion is allowed
here and nowhere else in this quantity: a lamp lighting, a stamp landing, a short burst when a leg hits.

**Layout.** (1) **Live tickets**: the smart card first, lottery cards after. Each: kind, stake, potential
payout, legs remaining; then the legs, each with its lamp, a plain description ("LSU to win", "Alabama by
more than 7", "Over 55 points"), the DraftKings odds, the live score with **what still needs to happen**
("Georgia leads by 3, needs 4 or more"), and **"sharps say NN %"**: the sharp books' live probability for
that leg, updating during the game, with a small history bar. The card footer states the chance every leg
hits per the sharp books beside what DraftKings pays as if, and the hold between them. (2) **Season
strip**: every past ticket as a thumbnail with its stamp; staked, returned, net against the budget; best
hit; the current streak. (3) **Between cards**: when nothing is live, when the next card is built (Friday
for college, Saturday evening for the NFL), the anchor rule (an LSU or Saints leg), and the budget left
this week.

**Sentences** (§1.2 templates, in a fan's voice, still deterministic): "One leg from glory." "Alabama
needs to win by 8 or more; they lead by 10 with six minutes left." "Ouch. Georgia let it slip." The
"needs to happen" text is a pure function of the leg's market, line and the current score, never a guess.

**Metrics and sources.**

| Metric | Source |
|---|---|
| cards, legs, stake, estimated payout, true-probability estimate, hold, rationale | `parlay_cards`, `parlay_legs` (§3.9) |
| placed or not, actual DraftKings odds, payout and stake | `parlay_placements` |
| live score, period, clock; what still needs to happen | `game_score_events`, `games`, `needs(leg, score)` |
| leg status (pending, alive, hit, miss), card status | `parlay_legs.status`, `parlay_cards.status` (settlement stage `parlay_grade`) |
| sharps say NN % per leg, with history | `parlay_leg_probs` (written by the recorder tick from the sharp consensus while a card is live) |
| season strip, staked, returned, net, best hit, streak | `parlay_ledger`, `parlay_cards` |

**Never shown here.** Any paper number, any research variant, any CLV. No DraftKings account state.
No "place" button: placement is by hand and is confirmed through the CLI (`harness parlay placed`). The
header badge on this surface reads `FUN MONEY · $50/WEEK · PLACED BY HAND` in place of `PAPER`, and the
How-it-works page says in one paragraph that this page and the research never mix. The lottery card's
correlation note from §8.1 ("DraftKings will quote lower than this") is shown as written.

## 3. Telemetry tables (additive; every timestamp `timestamptz`)

Volumes assume a game day. None of these tables is ever scanned by the tape writers or read by a page
view. Retention: kept (invariant 5: nothing non-additive).

### 3.1 `metric_samples`

`id bigserial PK`, `ts`, `source String(12)` in {recorder, ws, exec, settle, housekeeping, serve},
`name String(48)`, `labels JSONB default {}`, `value Numeric(18,6)`. Index `(name, ts desc)`.

Writers and names:

- **executor**, once per `metric_sample_s = 60` (every fourth loop, first loop after start): `exec.loop_ms`,
  `exec.p95_loop_ms`, `exec.loops_skipped`, `exec.open_orders`, `exec.dirty_markets`,
  `exec.ws_event_age_s`, `exec.intents_considered`, `exec.placed`, `exec.cancelled{reason}`,
  `exec.skipped{reason}`, `exec.filled_contracts`, counters being the totals since the previous sample.
- **recorder tick**, once per run: `recorder.tick_ms`, `recorder.fetched{source}`, `recorder.errors`,
  `recorder.credits_remaining`, `recorder.trade_gaps`, `pricing.fair_values{feed_kind}`,
  `pricing.staleness_median_s{feed_kind}`, `pricing.candidates{variant}`, `pricing.rejected{variant,reason}`.
- **WS recorder**, once per 60 s from its own clock: `ws.events_per_min`, `ws.trades_per_min`,
  `ws.subscribed_tickers`, `ws.reconnects`, `ws.gaps`, `ws.sink_lag_s` (newest event `ts` against now).
- **housekeeping**, daily: `db.size_gb`, `db.table_gb{table}` (six largest), `db.growth_gb_per_day`,
  `host.disk_free_gb` (`os.statvfs` on the Postgres data mount, bind-mounted read-only into `app-run`),
  `host.mem_available_mb` (`/proc/meminfo`), `match.rate{sport}`, `match.unmatched{sport}`.
- **serve** (phase 4.5), per snapshot: `serve.snapshot_ms{name}`.

Bound: about 80k rows and under 15 MB per game day at the worst case.

### 3.2 `operator_events`

`id bigserial PK`, `ts`, `kind String(24)`, `summary String(200)`, `ref JSONB default {}`.
Index `(ts desc)`. `summary` passes the F50 sanitizer before insert.

Kinds and writers: `kill_on`, `kill_off` (the dashboard routes, alongside the existing single-row
update); `deploy` (recorder and executor at startup when `build_sha` differs from the newest `runs` row or
the heartbeat's `executor_version`); `config_change` (executor at startup when `config_hash` differs from
the newest open order's); `ws_connect`, `ws_disconnect` (WS recorder, with the reason code);
`settle_error`, `budget_exhausted` (settler); `report_written`, `gate_evaluated` (the CLIs, with
`criteria_hash`); `check_failed` (housekeeping, one per failing check); `note` (a new CLI `harness note
--kind amendment|alias_pass|verify_pass|verify_fail|drill "text"` for the operator and the autopilot).

### 3.3 `order_watch_samples`

`order_id bigint`, `ts`, `queue_remaining Numeric(14,2)`, `nw_queue_remaining Numeric(14,2)`,
`best_bid Numeric(6,4)`, `best_ask Numeric(6,4)`, `fair_p Numeric(6,4)`, `book_dirty bool`,
`terminal String(12) null` (set on the last sample: filled, cancelled, expired). PK `(order_id, ts)`.

Written by the executor for every open non-replay order once per `watch_sample_s = 60` (in-memory
last-sample clock per order; a restart simply samples on the next loop), plus one terminal sample when
the order leaves the open set. Replay writes none. Bound: 150 open orders gives at most 216k rows per
day; typical is a tenth of that.

### 3.4 `equity_snapshots`

`ts`, `variant_id String(12)`, `cash Numeric(12,2)` (variant `bankroll` plus the sum of non-replay
`ledger.cash_delta`), `open_stake Numeric(12,2)`, `mtm_open Numeric(12,2) null` (open positions valued at
`side_p(venue mid)` from the live book; null when no book), `mtm_coverage Numeric(5,4)` (share of open
contracts that had a clean book), `n_open_positions int`, `n_open_orders int`. PK `(ts, variant_id)`.

Written by the executor every `equity_sample_s = 300` for each exec variant, and by the settler after its
`settle` stage. Bound: under 1k rows per day.

### 3.5 `game_score_events`

`id bigserial PK`, `game_id int`, `ts`, `status String(12)`, `period smallint null`, `clock String(8)
null`, `home_score smallint null`, `away_score smallint null`, `raw_id bigint null` (the
`raw_responses` row). Index `(game_id, ts desc)`.

Written by `link_espn_scoreboard` when any of (status, period, clock, scores) changes for a game. ESPN is
polled every tick, so the clock resolution is the tick interval and the UI labels it with its age.
Bound: a few hundred rows per game.

### 3.6 `check_results`

`id bigserial PK`, `job_run_id bigint`, `ts`, `check_name String(48)`, `status String(8)` in {pass,
fail, skip}, `value Numeric(18,6) null`, `threshold String(48) null`, `detail String(200) null`.
Index `(ts desc)`.

A registry `harness/ops/checks.py` holds the Layer 2b invariants from `verify.md` as named, bounded SQL:
each runs under the settle job's statement timeout, none touches `orderbook_events`, and any check over
`venue_trades` is bounded to the current weekly partition. The housekeeping stage runs the registry
daily and writes one row per check; a `fail` also writes an `operator_events(kind = check_failed)` row.

### 3.7 `report_runs` and `report_cells`

`report_runs`: `id bigserial PK`, `year smallint`, `week smallint`, `generated_at`, `provisional bool`,
`build_sha String(40)`, `criteria_hash String(64)`, `config_hashes JSONB`, `markdown text`,
`markdown_sha256 String(64)`. Index `(year, week, generated_at desc)`.

`report_cells`: `report_run_id bigint`, `table_key String(4)`, `row_key String(64)`, `col_key
String(48)`, `estimate Numeric(14,6) null`, `n_obs int null`, `n_clusters int null`, `lo Numeric(14,6)
null`, `hi Numeric(14,6) null`, `text String(64) null` (the rendered string, the form the UI shows),
`flags JSONB` (`greyed`, `flagged`, `not_collected`). PK `(report_run_id, table_key, row_key, col_key)`.

`harness report --week N` writes one `report_runs` row and every cell of every table it renders, in the
same transaction as the markdown output, `provisional = false`. The settlement job gains a stage
`report_wtd` that runs `weekly_tables` for the current ISO week once per hour under the shared budget and
writes a `provisional = true` run. Re-running a week appends a new run; the UI reads the newest
non-provisional run for closed weeks. `Table` gains `row_key(row)` (first column) and the columns list
already names `col_key`.

**Table t12 (new, additive; not a gate input):** declined candidates by `(variant, reason)` for the week:
count, share, and for rejected signals the counterfactual `clv_target_p_net` at the variant's own
`price_target` from `gap_outcomes` under `pinnacle_t5` and `kalshi_last_trade_pre_kick`, as
`(estimate, n_obs, n_clusters, lo, hi)`; for skipped intents the same from the intent's gap snapshot.
Lands with phase 4.5, not Task 12b.

### 3.8 `dashboard_snapshots` (phase 4.5)

`name String(32) PK`, `generated_at`, `elapsed_ms int`, `payload JSONB`, `error String(80) null`.

### 3.9 Parlay tables (phase 4.5 creates them and the Ticket surface; phase 5c writes them)

- `parlay_cards`: `id serial PK`, `year smallint`, `week smallint`, `sport String(5)`, `kind String(8)` in
  {smart, lottery}, `built_at`, `stake Numeric(8,2)`, `dk_payout_est Numeric(10,2)`, `true_prob_est
  Numeric(8,6)`, `hold_est Numeric(6,4)`, `rationale String(600)` (F50-sanitized; a model wrote it only
  when the Anthropic key exists, and the row says which), `anchor_leg_id int null`, `status String(8)` in
  {proposed, placed, alive, cashed, busted, void}, `correlated bool` (lottery cards). Index `(year, week)`.
- `parlay_legs`: `id serial PK`, `card_id int`, `seq smallint`, `game_id int`, `market_type String(6)` in
  {ml, spread, total}, `side_team_id int null`, `side String(5) null` in {over, under}, `threshold
  Numeric(5,1) null`, `dk_american int`, `dk_decimal Numeric(8,4)`, `plain_text String(80)`,
  `odds_snapshot_id bigint null` (the DraftKings row the price came from), `status String(8)` in
  {pending, alive, hit, miss, void}, `graded_at null`. Index `(card_id, seq)`.
- `parlay_placements`: `card_id int PK`, `placed_at`, `stake_actual Numeric(8,2)`, `dk_payout_actual
  Numeric(10,2)`, `dk_odds_actual int null`, `note String(200)`. Written by `harness parlay placed --card
  ID --stake --payout [--odds] [--note]`; a card without a row is shown as "not placed" and never enters
  the ledger.
- `parlay_ledger`: `id serial PK`, `ts`, `card_id int`, `kind String(6)` in {stake, return, void},
  `amount Numeric(10,2)`, `year smallint`, `week smallint`. Stake on placement, return on `cashed`, the
  stake back on `void`.
- `parlay_leg_probs`: `leg_id int`, `ts`, `sharp_p Numeric(6,4)`, `book_p Numeric(6,4) null` (the
  DraftKings live implied probability when present in the feed). PK `(leg_id, ts)`. Written once per
  recorder tick for every leg of a card whose status is placed or alive and whose game is inside the
  in-progress window, from the same consensus the pricing path uses for that market. Bound: a few
  hundred rows per leg per game.
- Settlement stage `parlay_grade` (phase 5c, registered after `settle`): grades a leg from
  `settlements` with the same `resolve_market` rules as Kalshi contracts (a push on a whole-number line
  is `void`), moves a card to `cashed` when every leg is `hit`, to `busted` on the first `miss`, writes
  the ledger, and appends `operator_events(kind = parlay_settled)`.
- `needs(leg, score) -> str` in `harness/parlay/needs.py`: a pure function tested on every market type
  and both sides ("needs 4 or more", "needs 12 more points", "any win does it", "already done").

## 4. Snapshot layer (phase 4.5)

- A `SnapshotJob` runs inside `app-serve` on APScheduler (`max_instances = 1`, `coalesce = True`), one
  job per snapshot name with its own cadence: `pulse` 30 s, `floor` 15 s inside a game window (the R4
  definition) and 60 s outside, `study:<year>-<week>` 10 min for the current week and once on demand
  for closed weeks (cached until a newer `report_runs` row exists), `gate` 60 s (cheap; changes only when
  a row is inserted). `ticket` 15 s inside a game window when a card is placed or alive, 60 s otherwise. Each builder runs on its own session from an engine with
  `SNAPSHOT_STATEMENT_TIMEOUT_MS = 2000`, strictly below the sink's timeout, and writes its row with
  `elapsed_ms`; an exception writes `error` and leaves the previous `payload` in place. Builders reuse
  `_section` semantics: one failing section of a snapshot does not empty the others.
- No builder reads `orderbook_events`, `venue_trades` or `raw_responses`. Everything that used to need
  the tape comes from `metric_samples`.
- `GET /api/snap/{name}` returns the payload with `generated_at`, `elapsed_ms`, `cadence_s` and an ETag;
  `GET /api/snap` lists names and ages. Unknown name is 404. The front end polls with `If-None-Match`.
- `GET /ui/` and `/ui/*` serve static files from `harness/dashboard/static/`. Root `/` is unchanged.
- Every payload carries `sentences`: a mapping from section id to the list of plain-English sentences of §1.2, built by `harness/dashboard/sentences.py` (pure functions, one per section, sharing `confidence_phrase(n_clusters)` and `reason_phrase(code)`), and `readings` for per-row plain text where a surface shows one (Gate criteria, Study declined reasons). The front end renders sentences verbatim and never composes its own.
- The build sha and `now` go into every payload; the UI shows staleness relative to its own clock and
  flags a snapshot older than twice its cadence (`WATCH`) or three times (`BROKEN`), which is also how
  the UI detects a dead `app-serve` job.

## 5. Front end (phase 4.5)

- Static, no build step: `index.html`, one CSS file, ES modules, and one vendored chart library for time
  series (uPlot, pinned, 45 KB). Everything that is not a time series (funnel, forest plot, heatmap,
  queue bars, tape strip, storage arc) is hand-drawn SVG from the payload. No CDN, no font download: the
  NAS is viewed over a tunnel and the page must work with no internet. Asset budget: 300 KB uncompressed.
- Responsive from 360 px to 2560 px. Pulse and Floor are laid out phone-first; Study and Gate are
  desktop-first and remain readable on a phone by stacking. Tables scroll inside their own container.
- Light and dark, following the OS, with a manual toggle in the header stored in `localStorage` (user
  decision 2026-09-07 late evening: the owner reads dark; family members will read light). The two are
  one token set with two values each, never two stylesheets:

  | Token | Dark | Light |
  |---|---|---|
  | page ground | `#0b0e13` | `#f4f3ef` |
  | header, tile | `#0f131a` | `#fbfaf7` |
  | card | `#131820` | `#ffffff` |
  | raised (active tab, dim badge) | `#1a212b` | `#ebe9e3` |
  | border | `#1f2733` | `#e2dfd6` |
  | row rule | `#171d26` | `#ecebe6` |
  | track, gridline | `#232b36` | `#dedbd2` |
  | ink | `#eef2f6` | `#17191d` |
  | secondary ink | `#aab4c0` | `#4a525c` |
  | muted ink | `#7c8794` | `#6b7480` |
  | accent | `#35c9d9` | `#0f8e9c` |
  | good, warning, bad | `#2bc257`, `#fab219`, `#e05252` | `#168a3a`, `#b7780f`, `#c23b3b` |
  | fun accent (Ticket) | `#f5a524` | `#8a5a08` (the warning amber is too faint as text on cream) |
  | variants, seven slots in fixed order | `#3987e5 #d95926 #199e70 #c98500 #d55181 #008300 #9085e9` | `#2a78d6 #eb6834 #1baf7a #eda100 #e87ba4 #008300 #4a3aa7` |
  | Ticket slip paper, ink, muted | `#2b2517`, `#f1e6cf`, `#b9ad95` (night paper) | `#f4ead6`, `#1b1a17`, `#6b6255` (cream) |
  | Ticket HIT, MISS | `#4fc46f`, `#ef5a6e` | `#1f8a3b`, `#c8102e` |

  Both variant palettes pass the dataviz validator on their surfaces (adjacent-pair CVD and normal-vision
  floors). On white, three light slots sit under 3:1 contrast, so the relief rule applies: every series
  is always named beside its mark, never identified by colour alone (already the case everywhere). The
  Ticket slip is dark warm paper in dark mode, so it reads as a ticket without glaring, and cream in
  light mode; status colours on the slip are stepped for the paper they sit on.
- Visual direction is decided: "mission control with game-day energy", fixed by the design canvas
  (https://claude.ai/code/artifact/78f3e81e-bae8-4d69-aa30-2b6697e7dd3b; artboard sources under
  `docs/superpowers/design/dashboard/`), approved 2026-09-07 and refinable after week 1 data exists;
  the front end is implemented from the canvas; this spec fixes the
  surfaces, metrics and rules, not the typography or palette. Two fixed points: the `PAPER` badge is the
  same colour on every surface, and the interval mark with its cluster count is a single reusable
  component used everywhere an estimate appears.
- The front end ships `glossary.json` and the How-it-works page (§1.2); a term with a glossary entry is rendered with a dotted underline and opens its definition on tap or hover. The header on every surface links to How it works.
- Reduced motion is honoured. Animation is limited to the queue bars, the fills stream and status
  transitions.

## 6. Performance budget

| Item | Budget |
|---|---|
| Snapshot builders, total CPU | under 2 s per minute on the NAS; `elapsed_ms` per snapshot is itself a Pulse metric |
| Any single snapshot query | statement timeout 2000 ms; the builder marks `error` and keeps the last good payload |
| Telemetry writers | executor: one batched insert per sample interval, inside the loop's existing transaction; recorder: inside the run's transaction; WS: on the sink's commit cadence |
| `app-serve` memory | unchanged class of process; the snapshot job adds one thread and no cache beyond the table |
| Page | first paint under 1 s on the LAN; polling per §4; no page view touches Postgres |
| Legacy page | unchanged, still about 0.6 s, still checked by the verify pass |

## 7. Roadmap placement (U6)

- **Phase 3 Task 12b, "Telemetry for the dashboard".** Tables §3.1 to §3.7 (without t12), their writers,
  `harness note`, the checks registry, `report_runs`/`report_cells` writes in `harness report`, the
  `report_wtd` stage, and `verify.md` rows that assert each table is receiving rows. Runs after Task 12
  and before Task 13 (both touch `loop.py`; 12b depends on Tasks 6, 7, 10 and 12 having merged). No
  dashboard change in 12b beyond the two `operator_events` writes in the kill routes.
- **Phase 4.5, "Dashboard surfaces".** `dashboard_snapshots`, the snapshot job, `/api/snap`, `/ui/`, the
  four surfaces, table t12, the design canvas implemented, and a `verify.md` extension that loads `/ui/`
  and each snapshot and checks ages. Planned by the autopilot after phase 4 from this spec and the canvas.
  Absorbs phase 5(g).
- **Ticket** ships in phase 4.5 with the parlay tables (§3.9) empty; it shows the between-cards state until
  phase 5c writes the first card. Phase 5c gains the writers named in §3.9.
- Phase 4 item 6's drawdown alert and item 7's `venue_requests` table get a Pulse rule and a Floor tile
  respectively when phase 4.5 is planned; they need no change to phase 4.

## 8. Testing

- Every telemetry writer has a DB test that the row appears with the expected labels and that replay
  writes none (`order_watch_samples`, `equity_snapshots`).
- `game_score_events` test: two scoreboard bodies differing only in `displayClock` produce two rows; an
  identical body produces none.
- `report_cells` test: every rendered cell of a fixture week round-trips through the table with the same
  `text`; a second run appends a second `report_runs` row and leaves the first untouched.
- Checks registry test: every registered check has a statement timeout and none references the tape
  tables (a static assertion over the SQL strings).
- Phase 4.5: `needs(leg, score)` is tested on every market type, both sides, a push and a finished game;
  the Ticket snapshot builder is tested with no cards, a placed card before kickoff, a live card with one
  missed leg (status `busted`, payout dimmed), and a cashed card; the ledger never contains a card without a
  placement row.
- Phase 4.5: every sentence template has a unit test on a fixture payload, including the three `confidence_phrase` bands and the rule that a greyed cell yields a sentence with no estimate; a test that every technical term appearing in the surfaces' label tables has a `glossary.json` entry; a test that every skip, cancel and rejection code the executor and strategy can emit has a `reason_phrase`.
- Phase 4.5: each snapshot builder has a test with a seeded database asserting bounded queries (the
  test engine's statement timeout at 2000 ms and an assertion that no SQL text names the three bulk
  tables), a payload schema test, an ETag test, and a Chrome pass in `verify.md` for `/ui/` at 390 px and
  1440 px.

## 9. Out of scope

Retention or aggregation of `metric_samples` (tiny; revisit in operator mode). Push notifications from
the UI (the autopilot's channel is R3). Authentication beyond the existing `127.0.0.1` binding and the
tunnel. Any write path other than the frozen kill pair.

## 10. Decisions taken on the user's behalf

1. Four surfaces rather than three: Gate is split from Study because the October read must not be
   surrounded by exploratory charts. Cost if wrong: one extra tab.
2. Snapshots live in `app-serve`, not `app-run`: the container that serves is the one that pays, and the
   recorder's tick budget is untouched. Cost if wrong: `app-serve` gains a scheduler thread; reversible by
   moving the job.
3. `order_watch_samples` at 60 s, not the 15 s loop, to bound rows at 216k per day worst case. Cost if
   wrong: the queue sparkline is coarser than the loop.
4. The current week's Study cells come from an hourly provisional report run rather than from client-side
   arithmetic, to keep one computation path (brief constraint 6). Cost: one more settle stage under the
   shared 600 s budget; it yields when the budget is spent.
5. Live scores are recorded as change events from the existing ESPN poll rather than adding a faster
   scoreboard cadence. Cost: clock resolution equals the tick interval, labelled as such.
6. Vendored uPlot rather than hand-drawn time series or a framework. Cost: one pinned 45 KB file.
8. The Ticket surface breaks the calm of the other four on purpose, and keeps real money on its own page
   with its own badge so that fun and research never share a screen. Cost if wrong: one more tab.
7. Sentences are written server-side by templates, not by a model and not in the browser, so the plain layer
   is deterministic, testable and cannot drift from the evidence. Cost: templates must be extended when a
   new section or reason code appears; the tests in §8 catch a missing one.
