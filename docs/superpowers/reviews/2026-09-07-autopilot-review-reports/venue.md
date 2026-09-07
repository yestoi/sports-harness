# Adversarial Review — Kalshi Venue Practitioner Lens

**Date:** 2026-09-07
**Reviewer lens:** every assumption the phase 3–5 plans make about Kalshi's API, data, environments and
rules, checked against Kalshi's current public documentation and against the repo's own recorded fixtures.
**Scope reviewed:** design spec v2 §5.2, §5.5, §8.2, §9.1–9.4, §14; the 2026-09-06 adversarial review;
the phase 3 addendum and plan; the autopilot roadmap; and read-only inspection of
`harness/venues/kalshi/public.py`, `harness/venues/kalshi/ws.py`, `harness/recorder/ws_sink.py`,
`harness/normalize/kalshi.py`, `harness/matching/kalshi.py`, `harness/pricing/fees.py`,
`harness/db/models.py`, `harness/strategy/run.py`, `docs/runbooks/phase0-deploy.md`.
**Method note:** no network calls were made to the Kalshi trading API. Every "live" claim below is either
quoted from Kalshi's published documentation or read from a fixture already recorded in this repo.

---

## 0. Verdict

The venue assumptions the first panel settled are still correct: prices and counts are fixed-point strings,
the book is two bid ladders with asks derived, football single-game markets are on a one-cent grid today,
the maker rate is 0.0175 and the taker rate 0.07 on a quadratic-with-maker-fees series, and the order flags
the spec names (`post_only`, `order_group_id`, `expiration_time`, `cancel_order_on_pause`,
`self_trade_prevention_type=maker`, `time_in_force=good_till_canceled`) all exist with those exact names
and those allowed values. None of the resolved items from 2026-09-06 needs reopening on its merits.

What is wrong is newer than that review, and it is concentrated in exactly the place that decides whether
phase 3 produces a real dataset or a flattering one: **the path from the recorded WebSocket tape to a
simulated queue position.** Six defects on that path each independently push the fill model toward
"we are always at the front of the queue, and we always fill." They are the same failure mode three
reviewers flagged in September — passing the go-live gate on fiction — arriving through a different door.

The single most important one is a field name. The phase 3 plan's "Verified data facts" block says the
WebSocket order book snapshot carries `yes_dollars` / `no_dollars`. It does not; it carries
`yes_dollars_fp` / `no_dollars_fp`. The repo already knows this — the phase 1 plan recorded it from a live
socket and `tests/test_kalshi_ws.py` uses the correct names — but the phase 3 plan restated it wrongly, and
the one test it specifies for `load_book` uses the REST fixture, which legitimately uses the other spelling.
A subagent implementing Task 3 from the plan text will produce a book loader that returns an empty book for
every WebSocket snapshot, `queue_ahead_at_place = 0` on every order, and a green test suite.

Second in importance, and not previously visible: `taker_side` — the field the entire fill model keys on —
is deprecated in Kalshi's current schema, with a removal window that opened on 2026-05-14, four months ago.
Both recorder paths default a missing `taker_side` to `"yes"`, and the fill model only fills on `"no"`. The
day Kalshi drops the field, the harness records a perfectly healthy tape, simulates zero fills, and raises
nothing.

Phase 4 has one design assumption the docs contradict outright: the V2 amend endpoint cannot change
`expiration_time`, so the spec's "renewed each cycle" expiry cannot be implemented as an amend. That is not
merely a phase 4 problem — it means the phase 3 paper model's `Renew` action, which keeps queue priority
across renewals, simulates something the live adapter will not be able to do.

Phase 5's RFQ plan rests on a quote-query guarantee Kalshi withdrew in June.

Everything below is stated as: what the plan says, what the venue says, and the exact change.

---

## 1. Findings

Severity key: **MUST-FIX** = fix before the autonomous loop starts executing phase 3;
**FIX in phase N** = fold into that phase's plan; **NOTE** = record, no action now.

---

### V1 — MUST-FIX — WebSocket snapshot field names are wrong in the phase 3 plan

**File/section:** `docs/superpowers/plans/2026-09-07-phase3-paper-execution.md`, "Verified data facts"
bullet 4; and Task 3 (`harness/execution/book.py`, `BookState.from_snapshot`, `load_book`) and its Step 1
test list.

**Plan says:** "a `snapshot` row's `raw` carries `yes_dollars`/`no_dollars` lists of `[price, size]` …
REST `orderbook_snapshots(yes_bids, no_bids)` same `[price, size]` shape."

**Venue says:** the `orderbook_snapshot` WebSocket message carries `yes_dollars_fp` and `no_dollars_fp`
(https://docs.kalshi.com/websockets/orderbook-updates, documented example quoted verbatim):

```json
{"type":"orderbook_snapshot","sid":2,"seq":2,
 "msg":{"market_ticker":"FED-23DEC-T3.00",
        "yes_dollars_fp":[["0.0800","300.00"],["0.2200","333.00"]],
        "no_dollars_fp":[["0.5400","20.00"],["0.5600","146.00"]]}}
```

The REST order book is a *different* shape: `{"orderbook_fp": {"yes_dollars": [...], "no_dollars": [...]}}`.
Both spellings are already correct elsewhere in this repo — `tests/test_kalshi_ws.py:34` uses
`yes_dollars_fp`; `harness/normalize/kalshi.py:117` and `tests/fixtures/kalshi_orderbook.json` use
`orderbook_fp.yes_dollars`; the phase 1 plan line 35 records the WS spelling as verified live on
2026-09-06. Only the phase 3 plan is wrong.

**Cost if ignored:** `WsSink` stores the snapshot body verbatim in `orderbook_events.raw`, so the data is
fine — but a `load_book` written from the plan text reads `raw["yes_dollars"]`, gets nothing, and returns
an empty ladder. Then `queue_ahead_at_place = book.resting_yes_at(prob)` is 0 for every order, the queue
model degenerates to "always first in line", every resting order fills on the first opposing print, and
tables 3 and 6 of the weekly report (adverse selection, fill realism) plus the go-live gate's
"≥ 150 paper fills confirmed by prints" criterion are all measuring a fiction. Task 3's only DB test uses
`kalshi_orderbook.json`, the REST fixture, so the suite stays green.

**Exact change:**
1. In the "Verified data facts" block, replace the sentence with: "a `snapshot` row's `raw` carries
   `yes_dollars_fp`/`no_dollars_fp` lists of `[price_dollars, count_fp]` (WebSocket shape); the REST
   `orderbook_snapshots(yes_bids, no_bids)` rows come from `orderbook_fp.yes_dollars`/`no_dollars` and use
   the same `[price, size]` element shape but different container keys."
2. In Task 3, give `BookState.from_snapshot` an explicit `source: Literal["ws","rest"]` argument, or have
   `load_book` read `raw.get("yes_dollars_fp") or raw.get("yes_dollars")` with the same for `no`, and
   **raise** if both are absent rather than defaulting to `[]`.
3. Add to Task 3 Step 1: a test that builds a book from a WebSocket-shaped snapshot dict (copy the message
   in `tests/test_kalshi_ws.py:34`) and asserts a non-empty ladder and a correct `resting_yes_at`, in
   addition to the existing REST-fixture test.
4. Add to Task 6 Step 1: assert `queue_ahead_at_place > 0` for an order placed at a price where the seeded
   WebSocket snapshot has resting size. A zero-queue assertion is the tripwire for this whole class of bug.

---

### V2 — MUST-FIX — `taker_side` is deprecated and the recorder silently defaults it to `"yes"`

**File/section:** `harness/recorder/ws_sink.py:74` and `harness/normalize/kalshi.py:130` (both
`taker_side=... or "yes"`); `harness/db/models.py:168`; phase 3 plan Task 4 fill rule; spec §5.2 `get_trades`
signature and §5.5 `venue_trades`.

**Venue says:** on the Trade schema, `taker_side` is "Deprecated. Use `taker_outcome_side` (or
`taker_book_side`) instead. … This field will not be removed before May 14, 2026."
(https://docs.kalshi.com/api-reference/market/get-trades). That date passed four months ago, so removal is
now permitted at any release. The replacement fields are already present in the live message
(https://docs.kalshi.com/websockets/public-trades):

```json
{"type":"trade","sid":11,"seq":2,
 "msg":{"trade_id":"d91bc706-…","market_ticker":"HIGHNY-22DEC23-B53.5",
        "yes_price_dollars":"0.3600","no_price_dollars":"0.6400","count_fp":"136.00",
        "taker_side":"no","taker_outcome_side":"no","taker_book_side":"ask",
        "is_block_trade":false,"ts":1669149841,"ts_ms":1669149841000}}
```

**Semantics, confirmed:** `taker_outcome_side = "no"` ⟺ `taker_book_side = "ask"` ⟺ the taker sold YES ⟺
a **resting YES bid was consumed**. That is exactly the event that fills our order, so the plan's rule
(fill on `taker_side == "no"`) is directionally correct — it is keyed on the wrong, deprecated field.

**Cost if ignored:** `body.get("taker_side") or "yes"` turns a removed field into the string `"yes"` for
every print. `simulate_fills` only fills on `"no"`, so the executor produces zero fills, forever, with no
error, no log line, and a healthy-looking tape. The gate criterion "≥ 150 paper fills confirmed by prints"
simply never advances and the failure looks like "no edge" rather than "broken parser".

**Exact change:**
1. `harness/db/models.py`: add `taker_outcome_side: Mapped[str|None] = mapped_column(String(4))` and
   `taker_book_side: Mapped[str|None] = mapped_column(String(4))` to `VenueTrade`; add both as idempotent
   `ALTER TABLE … ADD COLUMN IF NOT EXISTS` in `harness/db/schema.py`. Do this in phase 3 Task 2, which
   already touches the schema.
2. `harness/recorder/ws_sink.py` and `harness/normalize/kalshi.py`: record all three fields; derive the
   canonical side as `taker_outcome_side or taker_side` and, when **both** are absent, insert NULL and
   increment a `taker_side_missing` counter that lands in `runs.notes` and turns the dashboard's data-quality
   row red. Never default to `"yes"`.
3. Phase 3 plan Task 4: change the fill predicate to read the canonical derived side, and add a test case
   with a print carrying only `taker_outcome_side`/`taker_book_side` (no `taker_side`) that must still fill.
4. Report table 8 (data quality) gains a "prints with no taker side" row.

---

### V3 — MUST-FIX — a sequence gap is per-subscription, but the plan treats it as per-ticker

**File/section:** `harness/recorder/ws_sink.py:55` (`_check_seq`, keyed on `sid` only, writes the gap row
with the ticker of the message that exposed it); phase 3 plan Task 3 (`load_book`, "a `gap` row sets
`dirty`") and Task 5 (`MarketNow.book_dirty`).

**Venue says:** `seq` is "a sequential number that should be checked if you want to guarantee you received
all the messages", and `sid` is "the server-generated subscription identifier used to identify the channel"
(https://docs.kalshi.com/websockets/orderbook-updates). One subscription covers every ticker in
`market_tickers` — this recorder subscribes up to `ws_max_tickers = 500` tickers on a single
`orderbook_delta` sid — so `seq` numbers messages across the whole subscription, not per market. The repo's
own phase 1 note agrees: "`seq` starting at 1 for that sid".

**Cost if ignored:** when a delta is dropped, the recorder attributes the gap to whichever ticker's message
happened to arrive next. Phase 3 then marks that one ticker dirty and keeps trusting the books of the other
499 tickers on the same sid — one of which is the one that actually lost a level. The executor makes place,
cancel and fill decisions against a book it believes is clean. On a game-day sid carrying ~4M events/hour,
this is not a rare corner.

**Exact change:**
1. `harness/recorder/ws_sink.py`: write the gap row with `ticker=""` (sentinel meaning "whole
   subscription") and put `sid`, `expected`, `got` and the ticker that exposed it in `raw`. Keep the
   per-sid `_last_seq` logic as is.
2. Phase 3 plan Task 3, `load_book`: a book is dirty if **any** `gap` row exists with `id` greater than the
   snapshot row's `id` and the same `sid` as the snapshot, regardless of that row's `ticker`. Add the test:
   two tickers on one sid, a gap row written under ticker A, assert ticker B's book also loads `dirty`.
3. Also fix the silent-swallow at `ws_sink.py:83`: `orderbook_snapshot` sets `self._last_seq[sid] = seq`
   unconditionally, which erases a real gap that coincides with a snapshot. Call `_check_seq` first when a
   prior `_last_seq[sid]` exists, then set.

---

### V4 — MUST-FIX — after a gap the book never recovers, because nothing asks for a new snapshot

**File/section:** `harness/venues/kalshi/ws.py` (`_recv_loop` — a gap is logged, never acted on);
phase 3 plan Task 3 `load_book` ("or newest REST `orderbook_snapshots` row **when no WS snapshot exists**").

**Venue says:** the correct client procedure is to treat the snapshot as state, each delta as an ordered
mutation, and on a skipped `seq` to stop acting on the local book and wait for a fresh snapshot. Kalshi's
orderbook-updates page documents `seq` for exactly this purpose but does not publish a recovery recipe, so
the resubscribe-to-force-a-snapshot mechanism below is the standard reading rather than a quoted rule
(recorded as NOTE V19). A new `orderbook_snapshot` for a ticker is emitted only on subscribe — i.e. on
reconnect, or on `update_subscription` with `action: add_markets`.

**Cost if ignored:** the recorder logs the gap and keeps applying deltas to a book it knows is wrong. Phase 3
marks the ticker dirty; `load_book` then looks for the newest WS `snapshot` row, finds the *old* pre-gap one,
replays the gap again, and is dirty again. The REST fallback is gated on "no WS snapshot exists", so it never
triggers. The market stays undecidable until the next full reconnect, which on a healthy socket may be hours
— i.e. exactly through the game we care about. Task 5 turns that into `Renew`-only, so open orders sit
unmanaged and no new orders are placed on that market for the rest of the window.

**Exact change:**
1. `harness/venues/kalshi/ws.py`: on a gap detected for a sid, issue `update_subscription` with
   `action: delete_markets` then `add_markets` for that sid's ticker set (a fresh snapshot per ticker
   follows), and have `WsSink` clear `_last_seq[sid]` before the new snapshots land. Rate-limit this to at
   most once per 60 s per sid so a persistently lossy socket does not thrash; if two recoveries inside
   5 minutes fail to clear, fall through to the existing reconnect path.
2. Phase 3 plan Task 3, `load_book`: choose the newest of (newest clean WS snapshot, newest REST
   `orderbook_snapshots` row for the market) as the base, not "WS if it exists". Full-ladder REST snapshots
   are already fetched every 5 min inside T−3h, which is the window the executor runs in, so this makes the
   dirty state self-healing within one REST cadence even if item 1 is deferred.
3. Add a `book_dirty_markets` count to `exec_heartbeat` and a dashboard row; it should normally be 0.

---

### V5 — MUST-FIX — NFL moneyline ties resolve at $0.50, not $0

**File/section:** `docs/superpowers/specs/2026-09-07-phase3-paper-execution-design.md` §3 ("moneyline:
winner; … ties are impossible at .5"); phase 3 plan Task 7 (`resolve_market`, "moneyline → `yes` if the side
team won (ties: `no`)") and Task 2 (`VenueSettlement.result ∈ {yes, no}`, `Benchmark` `result` as 0/1).

**Venue says:** from the NFL game market recorded in this repo
(`tests/fixtures/kalshi_markets_typed.json`, ticker `KXNFLGAME-26SEP21NYGLAR-NYG`), `rules_secondary`:

> "If the game ends in a tie, the market will resolve to $0.50 for each team."

The "ties are impossible at .5" reasoning is sound for spread and total markets, whose strikes end in .5.
It does not apply to the moneyline board, which has no strike. NFL regular-season ties are rare but real
(overtime can end level); college football has none.

**Cost if ignored:** on a tied game the harness derives `no` for both sides where Kalshi pays $0.50 to each.
Every downstream number for that game is wrong: `venue_settlements`, the `result` benchmark, all CLV against
`result`, the `ledger` cash delta, paper P&L, and the settled position. Because `result` is typed as a
yes/no enum, this cannot be corrected later without a schema migration — which is why it belongs in Task 2
now rather than after the first tie.

**Exact change:**
1. Task 7: `resolve_market(...) -> Decimal` returning the per-contract payout in `{0, Decimal("0.5"), 1}`;
   moneyline returns `0.5` when `home_score == away_score`; spread and total keep returning 0 or 1.
2. Task 2: `VenueSettlement` gains `payout: Mapped[Decimal] = mapped_column(Numeric(3,2))` and `result`
   becomes `String(4)` accepting `yes|no|tie`; `Benchmark.p` for `benchmark_type = "result"` accepts 0.5.
3. Task 7 ledger: `cash_delta = contracts × payout` rather than `contracts × (1 if result == side else 0)`.
4. Task 7 Step 1 test table gains a tie case for moneyline (asserting 0.5) alongside the existing six.

---

### V6 — MUST-FIX — the venue-vs-derived settlement cross-check can never fire

**File/section:** `harness/venues/kalshi/public.py:87` and `:101` (`fetch_markets_all` / `fetch_events_all`
both hardcode `"status": "open"`); phase 3 plan Task 7 ("if the newest Kalshi market summary for the ticker
carries a `result` … → `venue_settlements(source = venue)` and a `settlement_mismatch` warning"); phase 3
plan Task 11 gate criterion "zero settlement mismatches"; spec §5.4 ("Live mode: venue settlement is the
source of truth, ESPN is a cross-check").

**Venue says:** a market's `result` field is empty while the market is open — the recorded open NFL market
has `"result": ""` — and populates only once the market settles. `GET /markets` filters on `status`; a
settled market is no longer `open` and therefore never appears in any page this harness fetches.

**Cost if ignored:** `venue_settlements(source = "venue")` is never written, the `settlement_mismatch`
warning is dead code, and the gate criterion "zero settlement mismatches" passes vacuously — it reports a
clean bill of health for a check that never ran. This also removes the only independent check on the ESPN
score path, and it is the mechanism the spec designates as the source of truth for live mode in phase 4+.

**Exact change:**
1. Add `KalshiPublic.fetch_market(ticker) -> FetchResult` hitting `GET /markets/{ticker}` (no status
   filter), stored to `raw_responses` like every other fetch.
2. Task 7 `run_settlement`: after deriving a venue settlement for a ticker, fetch that single market and
   read `result`, `settlement_timer_seconds` and the settled price fields from the response body; write
   `venue_settlements(source = "venue")` from it. One request per settled market per game day is trivial
   against the rate budget (see V16).
3. If the fetch returns no result yet (settlement timer still running), leave the row absent and retry on
   the next hourly settlement pass rather than writing a derived-only row and calling it verified.
4. Task 11: the gate criterion must read "zero mismatches **and** ≥ 90% of settled markets have a
   `source = venue` row"; a criterion that cannot fail is not a criterion.

---

### V7 — FIX in phase 3 — contract counts are fractional; the plan types them as `int`

**File/section:** phase 3 plan Task 4 (`TapePrint(count: int)`, `TapeDelta(delta: int)`,
`queue_ahead: int`, `FillResult(queue_remaining, remaining_contracts)`); Task 2 (`Order.contracts int`,
`filled_contracts int`, `Intent.target_contracts int`); `harness/strategy/run.py:219`
(`contracts = int(stake / price_target)`).

**Venue says:** counts are fixed-point strings with 2 decimals and "fractional contract values (e.g.,
"2.50") are supported; the minimum granularity is 0.01 contracts"
(https://docs.kalshi.com/getting_started/subpenny_pricing and the V2 order schemas). This is not
theoretical: the NFL market recorded in this repo shows `"open_interest_fp": "1628.07"`.

The database is already correct — `VenueTrade.count` is `Numeric(12,2)` and `OrderbookEvent.delta` is
`Numeric(14,2)`. Only the phase 3 Python layer narrows to `int`.

**Cost if ignored:** `int(Decimal("0.50"))` is `0`. Fractional prints and fractional book deltas stop
consuming `queue_ahead` and stop filling, so the queue drains more slowly than reality on the way in and
our own fills are undercounted on the way out. The bias runs in both directions depending on where the
fractional flow sits, which makes it worse than a constant offset: it is un-auditable from the report.

**Exact change:** in Task 4 and Task 2, type every contract quantity as `Decimal` quantized to
`Decimal("0.01")` — `TapePrint.count`, `TapeDelta.delta`, `queue_ahead`, `queue_remaining`,
`remaining_contracts`, `SimFill.contracts`, `Order.contracts`, `Order.filled_contracts`,
`Intent.target_contracts`, `Fill.contracts`, `Ledger.contracts` — with `Numeric(14,2)` columns. Keep
`contracts = int(stake / price_target)` for our own order sizing (whole contracts are a deliberate
simplification), but state that in the addendum rather than leaving it implied. Add a Task 4 test with a
`"0.50"` print asserting a 0.50 queue consumption.

---

### V8 — FIX in phase 3 — the price grid is not recorded, and `floor_cents` assumes one-cent ticks

**File/section:** `harness/db/models.py:107` (`VenueMarket` has no `price_level_structure` /
`price_ranges`); `harness/normalize/kalshi.py` `upsert_venue_markets` (does not read them);
`harness/strategy/run.py:128,202` (`floor_cents`, `price_target = floor_cents(...)`); phase 3 plan Task 3
(`resting_yes_at(price)` exact-price lookup); spec §5.2 `ticks() -> TickTable` (declared, never built).

**Venue says:** each market carries `price_ranges`, "an array of `{start, end, step}` bands in fixed-point
dollars", which is "the source of truth for valid prices: any price on the grid is valid, and any off-grid
price is rejected", plus a human-readable `price_level_structure` label that clients are told **not** to key
logic off (https://docs.kalshi.com/getting_started/subpenny_pricing). Structures run from `linear_cent`
($0.01) to `center_deci_edge_centi_cent` ($0.0001 below $0.01 and above $0.99, $0.001 between). Rollout is
active: pilot markets moved the week of 2026-07-27 and "expansion to higher-volume markets the week of
August 3, 2026" (https://docs.kalshi.com/changelog).

**Current state is safe.** The NFL game market recorded in this repo has
`"price_level_structure": "linear_cent"` and `"price_ranges": [{"start":"0.0000","end":"1.0000",
"step":"0.0100"}]`. So `floor_cents` is right today.

**Cost if ignored:** the harness has no way to notice when that changes, and the failure is silent and
severe. On a finer grid, resting size sits at prices like 0.4230 while `price_target` is floored to 0.42;
`resting_yes_at(Decimal("0.42"))` then returns ~0, `queue_ahead_at_place` collapses to zero, and the fill
model becomes optimistic in precisely the same way as V1. Separately, a whole-cent order on a sub-cent grid
is a strictly worse quote than the venue permits, so measured maker economics would understate the real
opportunity. Football is a "higher-volume market" and the rollout is ongoing mid-season.

**Exact change:**
1. `VenueMarket` gains `price_level_structure: Mapped[str|None] = mapped_column(String(48))` and
   `price_ranges: Mapped[list|None] = mapped_column(JSONB)`, populated in `upsert_venue_markets` from the
   market body (both fields are already present in the recorded response, so no extra request). Add in
   phase 3 Task 2 with the other `ADD COLUMN IF NOT EXISTS` statements.
2. New pure helper `snap_to_grid(price, price_ranges) -> Decimal` in `harness/strategy/run.py`, flooring to
   the step of the band containing the price; `price_target` uses it, falling back to `floor_cents` when
   `price_ranges` is null. Unit-test both the `linear_cent` band and a two-band tapered structure.
3. Dashboard data-quality row plus a `runs.notes` warning when any **matched** venue market reports a
   structure other than `linear_cent`. This is the tripwire; it costs nothing and it is the difference
   between noticing a mid-season migration in a day and noticing it in the week-3 report.

---

### V9 — FIX in phase 3 — `Renew` keeps queue priority that the live venue cannot preserve

**File/section:** spec §9.2 ("`expiration_time = min(kickoff − 10 min, now + 3 × watcher_period)`, renewed
each cycle so a dead process leaves orders that self-expire within one cycle"); addendum §0.3 and §1
(`Renew` action, "renewed each executor loop (three loops of 15 s)"); phase 3 plan Task 5 (`Renew(order_id,
expiry)` as a distinct action that is not a cancel-and-replace) and Task 6 step 4 ("`Renew` updates
`expiry`"); roadmap phase 4 item 2.

**Venue says:** the V2 amend endpoint `POST /portfolio/events/orders/{order_id}/amend` accepts exactly
`ticker`, `side`, `price`, `count`, `client_order_id`, `updated_client_order_id`, `exchange_index` — it
"amends the price and/or max fillable count" and has no `expiration_time` field
(https://docs.kalshi.com/openapi.yaml). On create, `expiration_time` is an optional int64 Unix-seconds
value used with `time_in_force: good_till_canceled`; Kalshi also now rejects orders whose expiration is in
the past rather than coercing them to IoC, and forbids combining IoC with `expiration_time`
(https://docs.kalshi.com/changelog).

**Cost if ignored:** phase 4 discovers that renewal requires cancel-and-replace, which resets queue position
every 15 s. But phase 3 will already have banked a full dataset in which `Renew` preserved
`queue_ahead_at_place` from the original placement — so the simulated fill rate, the adverse-selection
table and the gate's fill-realism criterion all describe a strategy the live adapter cannot run. This is the
"gate passes on fiction" failure the September panel named, arriving through the expiry design instead of
the fill model.

**Exact change:** pick one and write it into the addendum §0 as an amendment, because it changes the numbers
the gate reads.
- **Preferred:** place with `expiration_time = kickoff − 10 min` once, and drop per-loop renewal entirely.
  The watcher's cancel paths (kill switch, kickoff, venue move, edge decay, unmatched) already cover every
  live reason to leave; `expiration_time` then serves only its real purpose, which is bounding an orphaned
  order left by a dead process. State the orphan window honestly: up to kickoff − 10 min, not one cycle.
  `Renew` becomes a no-op and `queue_ahead` legitimately persists.
- **Alternative:** keep the 45 s expiry, and model renewal as what it will be — `Cancel(reprice)` +
  `Place`, with `queue_ahead_at_place` re-read from the book at each replace. This is honest but throws
  away queue priority every loop and will show near-zero fills.

Either way, phase 4's `KalshiAuthed` must not expose an `amend(expiry=…)` signature, and the roadmap's
phase 4 item 2 should name the V2 amend field list so the round-trip test is written against it.

---

### V10 — FIX in phase 3 — the fee model is hardcoded, never read from the venue, and the multiplier truncates

**File/section:** `harness/pricing/fees.py:19-25`; `harness/db/models.py:107` (`VenueMarket` has no
`fee_type` / `fee_multiplier`, though spec §5.5 lists both); spec §5.2 ("Fee model read at startup from
`GET /series/{ticker}` and cached; unit test asserts maker 0.0175 and taker 0.07"); `tests/test_fees.py:20`.

**Venue says:** `Series.fee_type` is an enum of `quadratic | quadratic_with_maker_fees | flat` and
`Series.fee_multiplier` is a **floating-point double**, both required
(https://docs.kalshi.com/openapi.yaml, GetSeriesResponse). Kalshi additionally exposes
`GET /series/fee_changes` (scheduled future fee changes, with `scheduled_ts`) and `GET /events/fee_changes`
("Event fees are an override layered on top of the parent series' fee structure"). Kalshi has used
promotional fee changes on football this season: combo markets created after 23:59 ET on 2026-08-19
composed entirely of independent NFL components carry no maker fee.

**Two concrete defects.** First, `fee_model_for(fee_type, multiplier)` is never called with real data —
nothing reads the series, and no column exists to store it — so `KALSHI_FOOTBALL` is a hardcoded constant
throughout `gaps.py` and `run.py`. The enum mapping itself is correct (`quadratic` → no maker fee,
`quadratic_with_maker_fees` → 0.0175), but `flat` falls through to the maker-fee branch, which is wrong for
a flat-fee series. Second, `int(multiplier or 1)` truncates: a `fee_multiplier` of 0.5 becomes 0 and every
fee computes to zero. That is latent today and becomes live the moment phase 4 implements the spec's
"read the fee model from `GET /series` and assert".

**Cost if ignored:** every edge, every `price_target`, every net-of-fee CLV and every gate criterion is
computed against an assumed fee rather than the venue's. A scheduled or event-level change to the football
schedule mid-season would be invisible, and the spec's unit test asserting the literals 0.0175/0.07 would
either start failing for the right reason with no explanation, or keep passing while production is wrong.

**Exact change:**
1. `VenueMarket` gains `fee_type: Mapped[str|None] = mapped_column(String(32))` and
   `fee_multiplier: Mapped[Decimal|None] = mapped_column(Numeric(6,4))` (phase 3 Task 2), populated per
   series from a `GET /series/{ticker}` fetch cached per series per day, plus a
   `GET /events/fee_changes?event_ticker=…` pass for matched events.
2. `harness/pricing/fees.py`: `FeeModel.multiplier` becomes `Decimal`; `fee_model_for` takes
   `Decimal(str(multiplier))`, handles `flat` explicitly, and raises on an unknown `fee_type` rather than
   silently returning the football default.
3. Store the fee basis used on each `Fill` (`fee_type`, `fee_multiplier`, `maker_rate`) so historical P&L
   stays reconstructable across a fee change.
4. `tests/test_fees.py`: keep the arithmetic assertions against `KALSHI_FOOTBALL`, and add a separate
   fixture-driven test that a recorded `GET /series/KXNFLGAME` body maps to maker 0.0175. Add a daily
   drift check that alarms on the dashboard when the live series body no longer matches the fixture — that
   is the check the spec actually wanted, and it does not break the build when Kalshi reprices.

---

### V11 — FIX in phase 3 — the WebSocket ticker window drops markets that are still trading

**File/section:** `harness/venues/kalshi/ws.py`, `select_ws_tickers` (`lo = now - timedelta(hours=4)` on
`Game.kickoff_utc`); `harness/config/settings.py:20` (`ws_lookahead_hours: int = 24`).

**Venue says:** football game markets stay open through the game and close early only once the result is
known. The recorded NFL market has `"can_close_early": true` and
`"early_close_condition": "This market will close and expire after a winner is declared."`, with
`expected_expiration_time` (2026-09-22T03:15Z, just after the game) distinct from `close_time` /
`latest_expiration_time` (2026-09-24T00:15Z, two days later). The harness's own operational fact —
`orderbook_events` peaking near 4M rows/hour "during live games" — is direct confirmation that the book is
active in-game.

**Cost if ignored:** a game still trading more than four hours after kickoff (weather delay, long college
overtime, a late window running long) silently leaves the tape. Phase 3 markouts extend to +120 min from
placement, and placements run to kickoff − 10 min, so a horizon at kickoff + 110 min is already close to
the edge; H3 (flow imbalance vs the next move) and H4 (ladder consistency) both want the in-game tape they
would otherwise lose. The failure is invisible because the recorder simply stops subscribing.

**Exact change:** make the lookback a setting, `ws_lookback_hours: int = 8`, and use it in
`select_ws_tickers` in place of the literal `4`. Better, if `expected_expiration_time` is recorded
alongside `close_time` on `VenueMarket` (cheap — it is already in the response body), key the window on
`expected_expiration_time >= now` instead of on kickoff, which is correct by construction. Also record
`close_time` versus `expected_expiration_time` distinctly; anything that treats `close_time` as "the game
is over" is two days wrong.

---

### V12 — FIX in phase 3 — postponed games have a venue settlement rule the design ignores

**File/section:** phase 3 plan Task 7 (`run_settlement` settles only games with `status = final`);
spec §5.4 (`kickoff_changed` note exists, nothing consumes it).

**Venue says:** from the recorded market's `rules_secondary`: "If the game is postponed but begins within
48 hours from its originally scheduled start time, the market will remain open and resolve based on the
official final result. If the game is not started within 48 hours, the market will resolve to a fair price."

**Cost if ignored:** a postponed-and-abandoned game never reaches ESPN `STATUS_FINAL`, so its paper
positions stay unsettled forever, its benchmarks never compute, and the affected orders sit in a
non-terminal state that quietly skews the funnel table and the "orders" counts in every weekly report from
then on. A game postponed and replayed inside 48 hours settles correctly but against a kickoff that moved,
so its `benchmarks` (computed at kickoff + 5 min) point at the wrong instant.

**Exact change:**
1. Task 7: recompute a game's benchmark target times from the **current** `Game.kickoff_utc` and refuse to
   compute benchmarks for any game whose kickoff moved after the first gap snapshot was taken; flag those
   with a `kickoff_moved` column on `benchmarks` instead of silently using the new time.
2. Add a `stale_unsettled` dashboard row and `runs.notes` key: non-replay orders on games whose kickoff is
   more than 72 h in the past with no settlement. Never auto-resolve them; a "fair price" resolution is
   a venue judgement the harness cannot derive, and V6's venue-result fetch is the right source once it exists.

---

### V13 — FIX in phase 4 — target the V2 event-order endpoints, not the legacy ones

**File/section:** `docs/superpowers/autopilot/roadmap.md`, phase 4 pre-loaded decision 2; spec §5.2 Kalshi
adapter bullet and the `place_limit` / `amend` / `cancel` protocol.

**Venue says:** the current path is `POST /portfolio/events/orders`, "using the V2 request/response shape
(single-book `bid`/`ask` side and fixed-point dollar prices)", with `PUT`/`POST …/{order_id}/amend` and
`DELETE …/orders/{order_id}`. "The legacy `/portfolio/orders` endpoint will be deprecated no earlier than
May 6, 2026 — clients should migrate to this path"; the legacy mutation endpoints were deprecated between
2026-06-18 and 2026-06-25 and their rate-limit token costs are now 5× the V2 equivalents
(https://docs.kalshi.com/openapi.yaml, https://docs.kalshi.com/changelog).

The V2 create body, verbatim from the spec: `ticker` (required), `client_order_id`, `side` (required,
`bid|ask` — "for event markets, this refers to the YES leg only: `bid` means buy YES, `ask` means sell
YES"), `count` (required, fixed-point count string), `price` (required, fixed-point dollar string, up to 6
dp), `expiration_time` (optional int64 Unix **seconds**), `time_in_force` (**required**,
`fill_or_kill|good_till_canceled|immediate_or_cancel` — "`GTT` is not a valid API value"), `post_only`,
`self_trade_prevention_type` (**required**, `taker_at_cross|maker`), `cancel_order_on_pause`, `reduce_only`,
`subaccount`, `order_group_id`, `exchange_index`.

**This confirms the spec's flag names and values are all correct**, and confirms §5.2's "buy NO = ask on
YES" and the plan's `best_yes_ask() = 1 − best_no_bid`. Two encoding traps: `time_in_force` and
`self_trade_prevention_type` are *required*, not optional; and `expiration_time` is an integer of seconds
on the request but an RFC3339 `date-time` string on the returned `Order`, so the round-trip test must not
assume symmetry.

**Cost if ignored:** phase 4 builds against a deprecated surface that costs 5× the write budget and can be
withdrawn mid-season, and the encode/decode round-trip test the roadmap promises would validate the wrong
shape.

**Exact change:** roadmap phase 4 item 2 gains: "`KalshiAuthed` targets the V2 event-order endpoints
(`POST /portfolio/events/orders`, `POST /portfolio/events/orders/{order_id}/amend`,
`DELETE /portfolio/events/orders/{order_id}`) with `side ∈ {bid, ask}` on the YES leg, fixed-point dollar
`price` and fixed-point `count` strings, `time_in_force` and `self_trade_prevention_type` always sent,
`expiration_time` as int64 Unix seconds on send and parsed as RFC3339 on read." The pre-send invariant and
post-send echo check in spec §5.2 should compare the decoded V2 response, including `remaining_count` and
`fill_count` as Decimals.

---

### V14 — FIX in phase 4 — demo accounts are a separate signup and start with no funds

**File/section:** `docs/superpowers/autopilot/roadmap.md`, secrets table row for
`secrets/kalshi_demo_key_id` / `secrets/kalshi_demo_private_key.pem`, and "User-side TODOs"; roadmap phase 4
decisions 1 and 4; spec §13 ("Kalshi demo: manual smoke script").

**Venue says:** demo lives at `https://demo.kalshi.co/`; the recommended REST root is
`https://external-api.demo.kalshi.co/trade-api/v2` (alias `https://demo-api.kalshi.co/trade-api/v2`) and the
WebSocket is `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2` (alias
`wss://demo-api.kalshi.co/trade-api/ws/v2`) (https://docs.kalshi.com/getting_started/demo_env). Credentials
are not shared between demo and production — a demo key cannot authenticate against production or vice
versa — and the account must be created separately at demo.kalshi.co with keys generated at
demo.kalshi.co/account/profile. Demo accounts do **not** come with funds preloaded; mock funds are added
using test card numbers (https://help.kalshi.com/en/articles/13823775-creating-and-using-a-demo-account).
Kalshi's explicit caveat: "The price and behavior of markets in the demo environment may not be reflective
of those in real markets."

**Cost if ignored:** the phase 4 demo smoke (`harness kalshi-smoke --env demo`) fails on insufficient
balance the first time it tries to place, and the loop cannot fix it — funding demo is a user action behind
a web form. The roadmap says the loop "never blocks on" secrets, which is right, but this turns into a
verification unit that fails for a reason the journal will report as an adapter bug.

**Exact change:**
1. Roadmap secrets table, demo row: append "create the demo account separately at demo.kalshi.co (demo and
   production credentials are not interchangeable), then add mock funds with a test card at
   demo.kalshi.co before the smoke can place — a demo account has no balance by default."
2. Roadmap "User-side TODOs": add "fund the Kalshi demo account with mock funds (test card) once the demo
   key exists."
3. Roadmap phase 4 decision 1: pin the hosts as
   `kalshi_base_url` demo = `https://external-api.demo.kalshi.co/trade-api/v2`,
   `kalshi_ws_url` demo = `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`.
4. Phase 4 decision 4: the smoke must assert a non-zero balance first and skip with an explicit
   "demo unfunded" journal line rather than failing, so an unfunded demo never looks like a code defect.
5. Keep spec §5.2's "demo prices are not evidence"; Kalshi states the same thing, so it is now a cited
   rule rather than a house convention.

---

### V15 — FIX in phase 4 — record and echo `exchange_index`; Kalshi is sharding the exchange

**File/section:** `harness/db/models.py` `VenueMarket`; `harness/normalize/kalshi.py`
`upsert_venue_markets`; roadmap phase 4 item 2.

**Venue says:** markets, series and orders all carry `exchange_index`, "identifier for an exchange shard,
defaults to 0 if unspecified", and it is an accepted field on the V2 create and amend bodies. Kalshi began
placing new markets on non-zero shards this month: "Starting at 12:00 PM ET on September 10, 2026, new
commodities markets will be created on shard 2 and new basketball markets will be created on shard 3"
(https://docs.kalshi.com/changelog). The recorded NFL market has `"exchange_index": 0`.

**Cost if ignored:** football is on shard 0 today, so nothing breaks now. If a football series is ever
created on another shard, orders sent with the default 0 target the wrong shard and the failure will look
like an unexplained rejection on new markets only.

**Exact change:** add `exchange_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)` to
`VenueMarket`, populate it in `upsert_venue_markets` (the field is already in the recorded body), and pass
it on every phase 4 order rather than omitting it. Add a dashboard data-quality line when a matched market
reports a non-zero shard.

---

### V16 — FIX in phase 4 — read the rate-limit tier rather than assuming it

**File/section:** spec §9.2 ("Message budget 60 order messages/min per venue"); §4.2 job semantics;
`harness/venues/kalshi/public.py` (`_pause` with a fixed `sleep_s`).

**Venue says:** limits are token buckets, per key, and discoverable. Published tiers
(https://docs.kalshi.com/getting_started/rate_limits):

| Tier | Read tokens/s | Write tokens/s | Write bucket |
|---|---|---|---|
| Basic | 200 | 100 | 100 |
| Advanced | 300 | 300 | 600 |
| Expert | 600 | 600 | 1,200 |
| Premier | 1,000 | 1,000 | 2,000 |
| Paragon | 2,000 | 2,000 | 4,000 |
| Prime | 4,000 | 4,000 | 8,000 |
| Prestige | 10,000 | 8,000 | 16,000 |

Most endpoints cost 10 tokens, so Basic is roughly 20 reads/s and 10 writes/s; `GET /account/limits`
returns the effective tier and buckets, and `GET /account/endpoint_costs` is "the authoritative list of
non-default costs currently in effect". A 429 returns `{"error": "too many requests"}` with no
`Retry-After` and no `X-RateLimit-*` headers, and carries no penalty or cooldown — "your next request
succeeds once the balance covers its cost". Basic upgrades to Advanced via an endpoint call; Expert and
above are earned on volume.

**Assessment:** the spec's 60 order messages/min is 1/s against a Basic write budget of ~10/s, so the
message budget is conservative and safe — no change needed there. The gap is that the harness never learns
its tier and treats 429 as a generic error behind a fixed inter-page sleep.

**Exact change:** phase 4 reads `GET /account/limits` at adapter construction, logs the tier and buckets
into `runs.notes` and the dashboard health block, and sizes the recorder's page pause from the read bucket
instead of the static `sleep_s`. Treat 429 as retry-after-backoff rather than an error, since there is no
cooldown; keep it out of the two-consecutive-errors venue-unavailable rule in §9.4, which should count auth
and geo errors only.

---

### V17 — FIX in phase 5 — RFQ quotes are no longer queryable after the fact

**File/section:** spec §8.2 ("Log every RFQ and quote to `rfqs`/`rfq_quotes`; grade each quote at
settlement"); §5.5 `rfqs` / `rfq_quotes`; roadmap phase 5 item (f).

**Venue says:** RFQs are broadcast over REST, FIX and WebSocket, on the `communications` channel, with
message types `rfq_created` and `rfq_deleted` "visible to all subscribers" and `quote_created`,
`quote_accepted`, `quote_executed` visible only to the parties
(https://docs.kalshi.com/getting_started/rfqs). Two changes since the September panel
(https://docs.kalshi.com/changelog): on 2026-06-20, "GET /trade-api/v2/communications/quotes no longer
supports filtering by market_ticker or event_ticker, effective immediately"; and on 2026-06-25, "RFQ quotes
are no longer guaranteed to remain queryable unless they have reached a post-acceptance state."

Kalshi's documentation does not state that reading the RFQ feed requires maker onboarding, and does not
document a 403 for an unapproved key — so the roadmap's "it idles on 403" is an untested assumption about
an undocumented behaviour, not a known contract.

**Cost if ignored:** a listener that captures RFQs and then reconstructs quotes by polling loses most of
them, and H5 ("combo RFQs pay ~3% per leg over the independence product") is graded on a biased sample of
only the quotes that were accepted. That bias runs in exactly the direction that makes the hypothesis look
good.

**Exact change:**
1. Roadmap phase 5 item (f): "the listener subscribes to the `communications` WebSocket channel and
   persists `rfq_created` / `rfq_deleted` on arrival; it must not rely on `GET /communications/quotes` for
   history, which since 2026-06-25 retains only post-acceptance quotes and since 2026-06-20 cannot be
   filtered by market or event."
2. Replace "idles on 403" with "idles on any non-200 from the subscribe or any permission error, recording
   the actual status code and body in `venue_status.reason`" — then the first run tells us what a retail
   key really gets, instead of encoding a guess.
3. `rfqs` gains the arrival timestamp and the raw message; our own paper quotes are graded from our stored
   rows, never re-fetched.

---

### V18 — FIX in phase 5 — NFL-only independent combos carry no maker fee

**File/section:** spec §8.2 (quote formula `yes_bid = fair − margin·legs`, default 3c per leg); H5 grading;
roadmap phase 5 item (f).

**Venue says:** combo markets created after 23:59 ET on 2026-08-19 that are composed entirely of
independent NFL components have no maker fee; a market counts as independent when every component ties to
a different NFL game (Kalshi API update of 2026-08-20).

**Cost if ignored:** the RFQ pricer nets a maker fee that is not charged on the largest and most relevant
class of combos, so our quotes are systematically too wide and H5's measured edge is understated. The error
is one-directional, which makes it a bias rather than noise.

**Exact change:** §8.2's quote formula and the H5 grading subtract a maker fee only when the combo is not
NFL-only-independent; detect that from the combo's component event tickers (all `KXNFL*`, all distinct
events) and record the branch taken on `rfq_quotes` so the grading can be re-run either way.

---

### V19 — NOTE — Kalshi publishes no gap-recovery procedure

`seq` is documented as "a sequential number that should be checked if you want to guarantee you received
all the messages", but the orderbook-updates page prescribes no client behaviour on a skipped number. The
resubscribe-and-wait-for-a-fresh-snapshot mechanism in V4 is the standard reading of a snapshot-plus-delta
feed, not a quoted rule. Implement it, and note in the addendum that it is our convention.

---

### V20 — NOTE — `Order.side` and `Order.action` are deprecated on the same clock as `taker_side`

The `Order` schema marks both "Deprecated. Use `outcome_side` (or `book_side`) instead. … will not be
removed before May 14, 2026", and states that `outcome_side` and `book_side` "will become the canonical way
to determine order direction". Phase 4's decode should read `outcome_side` / `book_side` and treat
`side` / `action` as absent-tolerant, for the same reason as V2. Also note the schema's own clarification
that `outcome_side` describes direction only and does not change the price: an order at price *p* with
`outcome_side = no` is matched by one at the same *p* with `outcome_side = yes`.

---

### V21 — NOTE — price columns are `Numeric(6,4)` against a documented 6-decimal maximum

Kalshi describes dollar amounts as "a fixed-point decimal string with up to 6 decimal places of precision.
This is the maximum supported precision; valid quote intervals for a given market are constrained by that
market's price level structure." Every currently published grid structure is 4 dp or coarser
(`center_deci_edge_centi_cent` bottoms out at $0.0001), so `Numeric(6,4)` on `OrderbookEvent.price`,
`VenueTrade.yes_price` and the probability columns is exact today. It would silently round if Kalshi ever
ships a finer band. Worth a line in the addendum, not a migration.

---

## 2. What the plans get right (checked, no action)

Recorded so the next reviewer does not re-verify these.

- **Book shape.** Kalshi's book is two bid ladders and asks are derived; the V2 order schema states
  outright that `bid` means buy YES and `ask` means sell YES, and that "selling YES is economically
  equivalent to buying NO at `1 - price`". `best_yes_ask() = 1 − best_no_bid` is correct, and the
  one-cent grid on football today is symmetric so `1 − q` always lands on the grid.
- **Fill direction.** A resting YES bid is consumed when the taker sells YES, i.e.
  `taker_outcome_side = "no"` / `taker_book_side = "ask"`. The plan's `yes_price <= prob` predicate is
  correct: prints above our bid hit better-priced bids ahead of us, and only prints at or through our price
  touch our level. (The field it keys on is the problem — see V2.)
- **Order flags.** `post_only`, `order_group_id`, `expiration_time`, `cancel_order_on_pause`,
  `self_trade_prevention_type` (values `taker_at_cross` and `maker`) and `time_in_force` (values
  `fill_or_kill`, `good_till_canceled`, `immediate_or_cancel`) all exist with those exact names and values
  on the V2 create body. `self_trade_prevention_type = maker` is valid and does what §5.2 assumes:
  it cancels the resting maker order and continues matching.
- **Fee arithmetic.** Football series are `quadratic_with_maker_fees`; maker
  `ceil(0.0175 · M · C · P · (1−P))` and taker at 0.07 with a football multiplier of 1 match
  `harness/pricing/fees.py`, and the `fee_model_for` enum mapping is right. The zero-maker-fee change this
  season applies only to NFL-only independent **combo** markets, not to the single-game series this harness
  trades.
- **Price grid today.** The NFL game market recorded in this repo is `linear_cent` with a single
  `{0.0000, 1.0000, 0.0100}` band, so `floor_cents` is currently correct. (The risk is the migration, not
  the present — see V8.)
- **Markets stay open through the game.** `can_close_early: true` with "This market will close and expire
  after a winner is declared", and the harness's own ~4M orderbook events/hour during live games. The
  strategy's cancel-all at kickoff − 10 min is a deliberate choice to sit out in-game trading, not a venue
  constraint.
- **Message budget.** 60 order messages/min is ~1/s against a Basic write budget of ~10/s. Conservative
  and safe.
- **WebSocket auth and subscribe shapes**, the `sid`-inside-`msg` ack, and `update_subscription` with
  `add_markets` / `delete_markets`, as recorded in the phase 1 plan, match the current documentation.

---

## 3. Ordering for the loop

Items V1–V6 change either the schema or a decision function that produces the dataset. Fixing them after
phase 3 ships means either a migration or a week of data that cannot be compared with the weeks after it.
They belong in the phase 3 plan before Task 2 (schema) is executed:

- V5 and V2 add columns → fold into **Task 2**.
- V1, V3, V4 change `book.py` and the recorder → fold into **Task 3**, with V3 and V4 also touching
  `harness/recorder/ws_sink.py` and `harness/venues/kalshi/ws.py`, which are outside the plan's current
  file list and must be added to it.
- V7 changes types across Tasks 2, 4, 5 and 6 → decide once, at the top of the plan.
- V6 adds one public fetch and changes **Task 7** and the **Task 11** gate criterion.
- V8, V10, V11, V12 are additive and can land anywhere in phase 3; V8's dashboard alarm is the cheapest
  high-value item in this report and should not slip.
- V9 is a decision, not code: write the amendment into the addendum §0 before the executor is built,
  because it changes what the gate's fill-realism table means.

---

## 4. Sources

- https://docs.kalshi.com/websockets/orderbook-updates — `orderbook_snapshot` / `orderbook_delta` shapes,
  `yes_dollars_fp` / `no_dollars_fp`, `price_dollars`, `delta_fp`, `sid`, `seq`.
- https://docs.kalshi.com/websockets/public-trades — trade message, `taker_side` /`taker_outcome_side` /
  `taker_book_side`, `count_fp`, `is_block_trade`.
- https://docs.kalshi.com/api-reference/market/get-trades — `taker_side` deprecated, "will not be removed
  before May 14, 2026"; price and count units.
- https://docs.kalshi.com/openapi.yaml — V2 create/amend/cancel order bodies, `Order` schema,
  `GetSeriesResponse` and the `fee_type` enum, `exchange_index`.
- https://docs.kalshi.com/getting_started/subpenny_pricing — fixed-point representation, `price_ranges`,
  `price_level_structure`, 0.01-contract granularity.
- https://docs.kalshi.com/changelog — legacy order endpoint deprecation and 5× token cost, sub-cent
  rollout dates, RFQ quote-durability and quote-filtering changes, exchange sharding, expiration/IoC rules.
- https://docs.kalshi.com/getting_started/rate_limits — tier table, token-bucket semantics, 429 behaviour,
  `GET /account/limits`, `GET /account/endpoint_costs`.
- https://docs.kalshi.com/getting_started/demo_env and
  https://help.kalshi.com/en/articles/13823775-creating-and-using-a-demo-account — demo hosts, separate
  credentials, no preloaded funds, "may not be reflective of those in real markets".
- https://docs.kalshi.com/getting_started/rfqs — `communications` channel, `rfq_created` / `rfq_deleted`
  visibility, quote constraints.
- https://gamingamerica.com/news/1098524/kalshi-king-nfl-prediction-market-parlays-no-maker-fees — the
  2026-08-19 zero-maker-fee change for NFL-only independent combos.
- Local, read-only: `tests/fixtures/kalshi_markets_typed.json` (a real `KXNFLGAME` market carrying
  `price_level_structure`, `price_ranges`, `rules_secondary`, `can_close_early`, `exchange_index`,
  fractional `open_interest_fp`); `tests/fixtures/kalshi_orderbook.json`; `tests/test_kalshi_ws.py:34`;
  `docs/superpowers/plans/2026-09-06-phase1-normalize-match.md:35`.

## 5. Questions only the user can answer

None. Every item above is decidable from the documentation or from data already in this repo. Two items
create user-side chores rather than questions: funding the Kalshi demo account before the phase 4 smoke
(V14), and the V9 expiry choice, which the model can take under the roadmap's standing authorization
provided it is written into the addendum's "Decisions taken on the user's behalf" with the cost if wrong.
