# Deploy verification contract

Runs after every deploy. Three layers, in order: freshness, live data over ssh, pixels
through Chrome. The controller judges; walker prose is advisory.

## Preconditions

- The tunnel is up: `ssh -N -L 8180:127.0.0.1:8180 trey@192.168.12.228` (background);
  `curl -s -o /dev/null -w '%{http_code}' http://localhost:8180/` returns 200.
- `DEPLOY_SHA` recorded at deploy time.
- The dashboard token is never read into the conversation and never typed into a
  browser field. The kill switch is observed only (roadmap authorization).

## Layer 1 — freshness (abort the verification on failure; it is a deploy failure)

```
ssh trey@192.168.12.228 'curl -s http://127.0.0.1:8180/healthz'
```
`build` equals `DEPLOY_SHA` and does not end in `-dirty`. The dashboard header shows
the same string (checked again in Layer 3, item 1).

## Layer 2 — live data over ssh

Run the SQL through stdin (dollar quoting is expanded by the remote shell otherwise):

```
ssh -o BatchMode=yes trey@192.168.12.228 'cd /volume1/docker/sports-harness && docker compose exec -T postgres psql -U harness -d harness -At -F " | "' <<'SQL'
\echo -- containers are checked with make status-nas; this block is data
select id, started_at, status, credits_used, odds_remaining from runs order by id desc limit 3;
select count(*) from runs where started_at > now() - interval '20 minutes';
select decision, count(*) from signals where replay=false and created_at > now() - interval '3 hours' group by 1;
select pg_size_pretty(pg_database_size('harness'));
SQL
```

Plus:
```
make status-nas
ssh trey@192.168.12.228 'cd /volume1/docker/sports-harness && for s in app-run app-serve app-ws app-exec; do printf "%s ERROR lines (10m): " $s; docker compose logs --no-log-prefix --since 10m $s 2>/dev/null | grep -c "\"levelname\": \"ERROR\""; done'
```

| Check | Expected |
|---|---|
| Containers | all `Up`; `app-serve` `(healthy)`; `app-exec` present after phase 3 |
| `/healthz` | 200 with `status: ok`; during quiet hours `last_status` is `skipped`, still 200 |
| Runs | a row inside the last 20 minutes (heartbeat writes `skipped` runs every 30 s) |
| Credits | `odds_remaining` numeric and decreasing only on real ticks |
| ERROR lines | 0 for every service in the last 10 minutes |
| Signals | see time-of-day table |
| DB size | below 800 GB (dashboard red line); note the number in the journal |

### Phase 3 additions (after the executor ships)

```
select last_loop_at, loops, open_orders, last_error, now() - last_loop_at as age from exec_heartbeat;
select status, count(*) from orders where replay=false group by 1;
select count(*) from intents; select count(*) from fills where replay=false;
select count(*) from settlements; select count(*) from venue_settlements; select count(*) from benchmarks; select count(*) from gap_outcomes; select count(*) from markouts;
select count(*) from order_events where kind='skipped';
```

| Check | Expected |
|---|---|
| `exec_heartbeat.age` | under 60 s, `last_error` null |
| Settlements | > 0 (97 games were final on 2026-09-07 00:40 CT); rerunning `harness settle` inserts 0 more |
| Benchmarks | rows for finals that had pre-kickoff snapshots (games kicked off after 2026-09-06 19:30 CT); zero rows is a FAIL only if such games exist |
| Gap outcomes | > 0 once benchmarks exist |
| Orders / intents | see time-of-day table; every candidate of an exec variant newer than 1 h has an intent or a `skipped` event |
| `harness report --week 37` (via `docker compose run --rm app-run`) | writes `docs/reports/2026-w37.md` with tables 1–6 and 8 populated, 7/9/10 "not collected" |
| `harness gate` | stores a `gate_reports` row with `passed = false` |

## Time-of-day expectations (America/Chicago)

| Window | Recorder | Pricing / signals | Executor |
|---|---|---|---|
| 01:00–08:00, no game in progress | `skipped` runs every 30 s; no fetches | none new | heartbeat advances; open orders unchanged; expiry renewals only if orders exist |
| Weekday 08:00–01:00 | real tick every 15 min | candidates > 0 for the primary while games are within 8 days (Week 1 NFL, Week 2 CFB) | intents for candidates < 1 h old; orders placed or `skipped` with a reason |
| Sat/Sun daytime | every 5 min; 2 min inside game windows | as above, more rows | as above |
| Game in progress | 2 min; WS up to 4M events/h | as above | cancels at kickoff − 10 min |

An item that cannot be judged in the current window is **deferred**, not failed: journal
it with the wakeup time (first weekday pricing run: 08:10 CT).

## Layer 3 — Chrome walkthrough (read-only)

Walker prompt (Agent tool, `model: sonnet`, substitute `<...>`):

```
You are the deployment walker for <unit slug>. Read-only: never submit a form, never type
into an input, never click Kill or Unkill.
Load the Claude-in-Chrome tools first (one ToolSearch call: tabs_context_mcp,
tabs_create_mcp, navigate, computer, read_page, get_page_text, tabs_close_mcp). Create a
NEW tab; never reuse existing tabs. Open http://localhost:8180/ .
Save every screenshot under /Users/trey/dev/sports/docs/superpowers/autopilot/evidence/
as <date>-<unit>-<nn>-<slug>.png (use save_to_disk and record the returned path).
Checklist:
<numbered checklist below>
Per item: scroll the section into view, screenshot, record PASS or FAIL with one sentence
of what you saw. FAIL anything that needs squinting: an "unavailable" or error string in
a section, an empty table where the contract expects rows, a stale time, broken layout.
Close your tab when done. Your final message is machine-read. Return exactly:
## Verdict: PASSED | FAILED | FRESHNESS-FAILED
## Items
- <#> <PASS|FAIL> — <one sentence> — evidence: <path>
## Anomalies
```

Checklist (current dashboard; items 10–14 apply once phase 3 is deployed):

1. Header shows `build <DEPLOY_SHA>`; otherwise return FRESHNESS-FAILED and stop.
2. Health: status badge `ok`; `Credits remaining` numeric; kill switch badge `off`.
3. Funnel: raw rows by source lists `odds`, `kalshi`, `espn` with non-zero counts (24 h).
4. Funnel: signals per active variant lists six variants with candidate and rejected counts.
5. Match report: `nfl` and `ncaaf` blocks with a matched percentage.
6. Signals table renders rows for the primary variant (time, contract, fair, decision).
7. Unmatched markets table renders (rows optional).
8. WebSocket: `Last event` within 60 minutes; counts are numbers.
9. Data quality: staleness medians per book listed.
10. Executor block in Health: heartbeat age under 60 s, rendered green.
11. Open paper orders and today's fills tables render (rows per the time-of-day table).
12. Paper P&L per exec variant and open exposure render with numbers.
13. Candidates since the staleness fix: a count per variant.
14. Database size vs budget: a number in GB, not red.
15. No section shows `unavailable:` or an exception name; the page loaded in under 10 s.

## Verdict rules

- The controller reads every screenshot with the Read tool and re-scores each item.
- Any FAIL → `Carried fixes` in `roadmap.md` and the next unit is hotfix.
- Deferred items carry a wakeup time; they are scored when it fires.
- Anomalies off the checklist go to the journal; if they would fail a criterion in the
  spec, they become carried fixes.
