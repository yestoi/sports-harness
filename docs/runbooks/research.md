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

- **Cheap declines.** `single_leg`, `same_game`, and `no_fair` for a leg whose `event_ticker`
  does not start with `KXNFL`/`KXNCAAF` (or whose `market_ticker` is not in `venue_markets` at
  all) are now decided from `venue_markets` alone — `compute_quote` never touches `fair_values`
  for a combo that cannot possibly have a fair. Only a combo whose every leg is a priced football
  market reaches the fair-value lookup, and that lookup runs against `ix_fair_leg_lookup` (fix
  35's migration `0005_rfq_lookup`), a covering index on the lateral's own five-column shape
  instead of the wider `ix_fair_game_type_created`. The index's three nullable columns are
  `coalesce(...)`-wrapped and the lateral compares them the same way (round 1: the original
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
  and one INFO when it releases, never per frame.
- **Replayed/quoted/skipped counts are one summary per burst, not one log line per frame.** The
  "already quoted" skip used to log an INFO line per replayed frame at burst volume (roughly 490
  per reconnect in the incident); it is now DEBUG, and the listener instead logs one INFO summary
  (`replayed=<n> quoted=<n> skipped_rate=<n>`) once `RFQ_BURST_SILENCE_S` (5 s) has passed since
  the last frame it processed.

Re-enabling the listener after this fix is the same switch as turning it off, in reverse:
`RFQ_LISTENER_ENABLED=1` in `deploy/nas.env`, then `docker compose restart app-ws`.

## What is never done here

No quote is sent. `POST /communications/quotes` is refused by the transport before signing and
before any I/O (`harness/venues/kalshi/http.py:176` raises `PaperModeViolation`); the RFQ
listener module contains no `POST`, no `quotes` path and no `.send(` — it receives parsed frames
only, never the socket object. The veto never changes an intent; it is judged after the fact from
features frozen as of the signal (addendum 0.1). The parlay CLI writes only the harness's own
tables; the user places the slip by hand at DraftKings. Nothing in this container, or in the RFQ
listener, can reach an exchange.
