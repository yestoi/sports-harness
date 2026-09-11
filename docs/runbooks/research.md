# Research runbook

## What `app-research` is

One container on the harness image, no ports, mounting `secrets/anthropic_api_key` and nothing
else (`docker-compose.yml`, ruling A-M2: this is the one process in the stack that talks to a
paid model API, so it is also the one process with no venue credential in it — a compromise here
can spend money against the U4 caps and can reach no exchange at all). It runs two passes on a
30-second loop: the shadow veto and the weekly report annotator. It is dormant without the key
and off when `RESEARCH_WORKER_ENABLED=0` in the NAS `.env` (`deploy/nas.env`), and it reports
which of those it is in its log on every sweep: `research worker started, poll=<n>s passes=<n>`
followed by sweeps, or `research: dormant, no key`.

Dormant with the key file present on the NAS is a **FAIL**, not a normal state: check the bind
mount first. Compose materialises a missing bind source as an empty directory, and the worker's
`Path.is_file()` switch is what catches a directory standing in for the key.

## The two switches, and which one to use

`RESEARCH_WORKER_ENABLED=0` stops both passes and leaves the container up: use it to stop
spending without redeploying. Removing the key file is the harder off switch and also stops the
parlay rationale, which lives in the CLI (`harness/parlay/rationale.py`), not in this container —
`build_card` calls it directly through the same key check. Neither switch stops the RFQ listener,
which is `RFQ_LISTENER_ENABLED` in the same file and lives in `app-ws`, not here (see below).

Both switches are on their own line in `deploy/nas.env`, with no inline comment: `docker run
--env-file` does not strip a trailing comment, so a comment on the value's line would deploy as
part of the value. The rollback for either is to set it back to `1` and restart the affected
container — `docker compose restart app-research` or `docker compose restart app-ws`.

## The weekly report annotator

Fix 36 (journal 109): the annotator renders the week's final report from its stored
`report_cells` rows, never by recomputing the tables, so its per-sweep cost is one bounded query
regardless of how heavy the report was to build in the first place. A pass that fails (an
exception, or a captured model-call error) backs that report off an hour, escalating to a day
after three failed attempts in a row, so a report that keeps failing is not retried every sweep.
The backoff lives in `job_state` under `annotate:<report_run_id>`.

## The structured-output schemas

Fix 39 (journal 110): the three schemas sent through `output_config` (`harness/research/annotate.py`'s
`OUTPUT_SCHEMA`, `harness/research/prompt.py`'s `OUTPUT_SCHEMA`, `harness/parlay/rationale.py`'s
`_SCHEMA`) use only `type`, `properties`, `required`, `additionalProperties`, `items`, `enum`,
`description` and `anyOf` -- the structured-output subset the API actually accepts. `minimum`,
`maximum`, `maxLength` and `maxItems` are not in it; the SDK's own `parse` helper silently strips
them, but the raw dict `ResearchClient.call` sends through `output_config` reaches the API
unstripped, and the first live call to use a schema with one of them comes back HTTP 400. Every
length and range limit the schemas used to state is instead a sentence in the property's
`description` and is enforced after the response parses:

- the annotator keeps at most `BULLETS_MAX` bullets and truncates each to `BULLET_MAX` characters
  (`sanitize_model_text`, `harness/research/annotate.py`);
- the veto's `_sanitized` (`harness/research/veto.py`) clamps `confidence` to `[0, 1]` and
  truncates `reason` to `VETO_REASON_MAX` and `evidence_ids` to `VETO_EVIDENCE_IDS_MAX`, all
  before `_grade` reads the output;
- the parlay rationale truncates `text` to 600 characters (`sanitize_model_text(text,
  RATIONALE_MAX)`, `harness/parlay/rationale.py`).

`tests/test_research_client.py` walks all three schemas recursively and asserts no key falls
outside the supported set, so a future schema edit that reintroduces one of these keywords fails
in CI rather than at the next live call.

On an `anthropic.APIStatusError` (any 4xx/5xx), `ResearchClient.call` logs the status code and
the API's own `message` field at WARNING beside the exception's class name, and returns it as
`CallResult.error_detail` -- `write_notes` stores it under `research_notes.output.error_detail`.
`output.error` stays the class name; `error_detail` is what makes a rejected request
diagnosable from the row without a live repro:

```sql
select model, output->>'error' as error, output->>'error_detail' as detail
from research_notes
where output ? 'error_detail'
order by created_at desc
limit 20;
```

## Reading the spend

```sql
select day, kind, model, calls, usd, usd_reserved
from research_spend
where day >= current_date - 7
order by day desc, kind, model;
```

The caps are totals across `veto`, `annotate`, `parlay` and `study` (`harness/research/spend.py`,
`KINDS`), per America/Chicago day and per ISO week — `veto_daily_usd_cap` = $25,
`veto_weekly_usd_cap` = $150. `usd_reserved` is a live reservation taken by `reserve_spend`
before a call and released after it; it should be zero when nothing is in flight. A non-zero
reservation with no call running means a release did not happen. The reservation clears at the
next day boundary either way, but journal it as a FAIL per the Phase 5 verify block.

## What "dormant" means and when it lifts

The worker is dormant when today's spend plus reservations plus the next pair's worst case would
cross a cap. It lifts at the next America/Chicago midnight, or on Monday for the weekly cap.
Every signal that arrives while dormant decides `veto_skipped_budget` with a null `call_id`, so
H9's denominator (table t7 in the weekly report) keeps the gap visible rather than hiding it.
Pulse's `research_budget` rule reads WATCH — never BROKEN — while this is true.

## Re-fitting the worst case

After the first full live day, run the "Worst-case re-fit" row in `docs/superpowers/autopilot/verify.md`'s
Phase 5 block and journal the six numbers. The constants — `WORST_CASE_INPUT_TOKENS = 30_000`,
`WORST_CASE_OUTPUT_TOKENS = 4_096`, `WORST_CASE_SEARCHES = 3` — live in
`harness/research/spend.py`. Changing them is a code change with a test, not a config edit; the
weekly report's t7 table and the Pulse tile both read the module directly, so a change there is
the one place that moves them everywhere.

## When the veto rate spikes

Pulse's `veto_rate` WATCH fires above 25 % (`VETO_RATE_WATCH`, `harness/health.py`) of *decided*
signals in 24 hours (`proceed | reduce | veto` — `veto_skipped_budget` and `veto_error` are
excluded, since counting them would make a dormant or broken day read as a calm one). Read this
first:

```sql
select decision, count(*)
from veto_decisions
where decided_at > now() - interval '24 hours'
group by 1;
```

A spike made of `veto_error` is a machine problem: read `research_notes.output->>'error'`. A
spike made of `veto` is the model's judgement and is exactly what H9 (table t7) is measuring. The
veto is post-hoc and shadow-only (addendum 0.1): it never changes what the executor already
placed.

## The injection posture

Retrieved pages and the one free-text feature go into the prompt after a fixed instruction that
names them untrusted; the prompt defaults to `proceed`. A `reduce` or `veto` whose evidence ids
resolve to no retrieved snippet is downgraded to `proceed` with `reason_code =
'unresolved_evidence'` (`harness/research/veto.py`).

```sql
select count(*) from veto_decisions where reason_code = 'unresolved_evidence';
```

This is the count of times that fired. It is not an alarm on its own; a sustained rise is worth
reading the `research_notes.snippets` URLs for.

## The RFQ listener

It lives in `app-ws` (`harness/venues/kalshi/rfq_socket.py`), on its own WebSocket connection,
independent of the market-tape socket the same container also runs.

```sql
select status, reason, since from venue_status where venue = 'kalshi_rfq';
```

is its state. `unavailable` is the one-hour idle (`IDLE_S = 3600`) after a refused subscribe or a
frame gap, and `reason` is the venue's own text, capped at 120 characters — quote it in the
journal, never act on it. The market tape is on a different socket and is unaffected; confirm
that with `select max(ts) from orderbook_events`. To turn the listener off:
`RFQ_LISTENER_ENABLED=0` in `deploy/nas.env`, then `docker compose restart app-ws`.

### Fix 35: cheap quotes and the quote rate limit

The 03:15-03:45 CT incident (journal 109): the venue replays the whole open RFQ set on every
subscribe, and reconnected ten times in thirty minutes — 4,902 frames, almost all combos on
non-football series the harness never prices. Every leg of every one used to run the fair-value
lookup (`_LEG` in `harness/venues/kalshi/rfq_quote.py`) before deciding there was nothing to
find; the executor's own loop went from single-digit seconds to 235-336 s under the same window's
`pg_dump`, and eleven quote computations hit the 30 s statement timeout. The controller set
`RFQ_LISTENER_ENABLED=0` at 03:44 CT (see "To turn the listener off" above).

Three changes came out of it (round 1 corrected the first cut of the second and third, below):

- **Cheap declines.** `single_leg`, `same_game`, and `no_fair` for a leg whose `series_ticker`
  is not one of the six `harness.venues.kalshi.public.FOOTBALL_SERIES` names (or whose
  `market_ticker` is not in `venue_markets` at all) are now decided from `venue_markets` alone — `compute_quote` never touches `fair_values`
  for a combo that cannot possibly have a fair. Only a combo whose every leg is a priced football
  market reaches the fair-value lookup, and that lookup runs against `ix_fair_leg_lookup` (fix
  35's migration `0005_rfq_lookup`), a covering index on that lookup's own five-column shape
  instead of the wider `ix_fair_game_type_created`. The index's three nullable columns are
  `coalesce(...)`-wrapped and both `_LEG` and `_CLOSING_LEG` compare them the same way (round 1: the original
  `is not distinct from` comparison is NULL-safe equality Postgres cannot use an index for at
  all, so the first cut of the index was never actually chosen by the planner).
- **The quote rate is limited, not the connection's frame count.** `RFQ_QUOTE_RATE_MAX = 500`
  calls to `compute_quote` in any trailing `RFQ_QUOTE_RATE_WINDOW_S` (60 s), a sliding window
  (`harness/venues/kalshi/rfq_socket.py`). A dedupe hit (an `rfq_quotes` row already present for
  the id) or an `rfq_deleted` frame never counts against it — only a frame the listener is about
  to actually compute a quote for does — so a reconnect's replay of an already-quoted set costs
  nothing, and a long-lived connection keeps quoting new RFQs indefinitely rather than exhausting
  a lifetime budget the way a per-connection counter would (round 1: the first cut counted every
  `rfq_created` frame, dedupe hits included, which meant a reconnect's replay of a set the
  listener had *already* quoted spent the whole budget on frames doing no work and silently
  starved every genuinely new RFQ that arrived afterward on that connection). A frame the rate
  turns away is still stored as an arrival — H5's denominator never loses one to this — and
  counted in `quotes_skipped_rate`; the listener logs one WARNING when the limit first engages
  and one INFO when it releases, never per frame (fix 38: each direction is also bounded to at
  most one such log per 30 s — see below).
- **Replayed/quoted/skipped counts are one summary per burst, not one log line per frame.** The
  "already quoted" skip used to log an INFO line per replayed frame at burst volume (roughly 490
  per reconnect in the incident); it is now DEBUG, and the listener instead logs one INFO summary
  (`replayed=<n> quoted=<n> skipped_rate=<n>`) once `RFQ_BURST_SILENCE_S` (5 s) has passed since
  the last frame it processed.

Re-enabling the listener after this fix is the same switch as turning it off, in reverse:
`RFQ_LISTENER_ENABLED=1` in `deploy/nas.env`, then `docker compose restart app-ws`.

### Fix 38: the boundary filter, the summary, the rate-log hysteresis and retention

The 05:26-05:33 CT incident (journal 110), with fix 35 already live: the `communications`
channel is not a sporadic combo trickle, it is **every** RFQ create and delete on the exchange —
11,000-14,000 frames a minute sustained on one connection (one `ws_connect` in ten minutes, so
not a replay), three quarters `rfq_deleted`, almost all on `KXMVECROSSCATEGORY-SHARD1-...`
combos with no football leg. `store_rfq` wrote every one — 74,608 `rfqs` rows (128 MB) in seven
minutes, about 17 million rows and 30 GB a day, `app-ws` at 64 % CPU beside the market tape it
shares a process with. The controller switched the listener off at 05:33 CT
(`RFQ_LISTENER_ENABLED=0`, same switch as above).

- **Filter before storing.** `handle_frame` (`harness/venues/kalshi/rfq.py`) now decides, before
  `store_rfq` ever runs: an `rfq_created` frame is stored only when at least one leg's
  `event_ticker` (or, for a single-market RFQ with no `mve_selected_legs`, the RFQ's own
  top-level ticker) resolves to a series in `FOOTBALL_SERIES` — a string check
  (`event_ticker.split("-")[0]`, the same derivation `harness/venues/kalshi/public.py` uses for
  `MarketSummary.series_ticker`), never a `venue_markets` lookup, because there is no session at
  this point and a per-leg DB probe on every one of 11,000-14,000 frames a minute is exactly the
  cost fix 35 already removed from the quote path. This is deliberately weaker than fix 35's own
  `no_fair` decline (`_is_football` in `rfq_quote.py`, "every leg is football, DB-resolved"): a
  combo with even one football leg still stores and reaches the normal quote/decline path; only
  a combo (or single market) touching *no* football series at all is dropped. An `rfq_deleted`
  frame is applied only when its id is already a stored row; otherwise it is counted and dropped
  too — before fix 38 a delete for an unseen id wrote its own row (F71: "the socket is the only
  record"), which is exactly the incident's other three quarters. Either drop is counted, never
  logged per frame.
- **Four new counters, one summary.** The listener counts `frames_seen`, `frames_stored`,
  `dropped_nonfootball` and `dropped_unknown_delete` alongside fix 35's `replayed`, `quoted` and
  `quotes_skipped_rate`, in the same one-line INFO summary
  (`replayed=<n> quoted=<n> skipped_rate=<n> frames_seen=<n> frames_stored=<n>
  dropped_nonfootball=<n> dropped_unknown_delete=<n>`). The flood that started this fix never
  goes quiet, so the summary no longer waits only on `RFQ_BURST_SILENCE_S` (5 s) of silence — it
  also flushes after `RFQ_SUMMARY_PERIOD_S` (60 s) of continuous flow, whichever comes first, so
  it logs at least once a minute while frames are arriving instead of staying dark until the
  connection eventually goes quiet.
- **The rate-limit log no longer flaps.** The incident also showed the quote rate limiter's
  engage/release log pair firing 100 ms apart at the window boundary — a real state transition
  every time, so the old transition-only guard logged every one of them. Each direction
  (`rfq listener: quote rate limit engaged`/`released`) now also carries its own cooldown,
  `RFQ_RATE_LOG_COOLDOWN_S` (30 s): a transition still has to happen to log at all, but a burst
  of transitions inside 30 s logs at most once per direction. The limit itself —
  `RFQ_QUOTE_RATE_MAX` per `RFQ_QUOTE_RATE_WINDOW_S`, and which frames it turns away — is
  unchanged.
- **Retention.** Housekeeping (`harness/ops/housekeeping.py`, `_prune_rfqs`) deletes `rfqs` rows
  older than `RFQ_RETENTION_DAYS` (21 days — round 1, review I2: 7 days was too short against the
  weekly report's own reach into `rfqs`, `_T10_ARRIVALS`'s H5 denominator; 21 covers a Monday
  render or a re-render of the prior ISO week with margin) with no `rfq_quotes` row, in one
  bounded batch of `RFQ_PRUNE_BATCH` (5,000) per daily run, and records `db.rfqs_pruned`. A
  quoted row is never in scope (F71: the row is the record of what would have been answered).
  This is how the flood's own 74,608 rows leave the table — no one-time cleanup, just ordinary
  attrition once each row passes the retention window, the same way it prunes anything written
  after this fix too. Round 1 (review I1): the prune runs in `housekeeping_stage` in its own
  savepoint, separate from the metrics batch's — a lock wait, statement timeout or deadlock in
  the prune logs one WARNING naming the exception's class and simply omits `db.rfqs_pruned` from
  that day's batch, never rolling back `db.size_gb` and the rest; a later, unrelated metrics
  failure likewise can never undo a prune that already succeeded.

Re-enabling the listener after fix 38, same as fix 35: `RFQ_LISTENER_ENABLED=1` in
`deploy/nas.env`, then `docker compose restart app-ws`.

## What is never done here

No quote is sent. `POST /communications/quotes` is refused by the transport before signing and
before any I/O (`harness/venues/kalshi/http.py:176` raises `PaperModeViolation`); the RFQ
listener module contains no `POST`, no `quotes` path and no `.send(` — it receives parsed frames
only, never the socket object. The veto never changes an intent; it is judged after the fact from
features frozen as of the signal (addendum 0.1). The parlay CLI writes only the harness's own
tables; the user places the slip by hand at DraftKings. Nothing in this container, or in the RFQ
listener, can reach an exchange.
