# Phase 1 match-report runbook

Run after each game weekend on the deployed instance:

```bash
docker compose run --rm app-run match-report
```

For every `unresolved: <name>` or `side team unresolved: <name>` reason, look up the ESPN team and add `<name>: <espn_id>` under the sport's `kalshi_name` in `harness/matching/aliases_manual.yaml`, then:

```bash
docker compose run --rm app-run seed-teams
docker compose run --rm app-run reprocess --family kalshi_markets --from-raw-id 0
docker compose run --rm app-run match-report
```

`ambiguous: N games` needs a look at the games (doubleheader or reschedule); do not add aliases for those. `no game for pair` on events dated more than a week out is expected: ESPN's scoreboard and The Odds API only cover the current week, and those markets match once the week arrives.

Acceptance: NFL `matched` >= 98%; NCAAF `matched + fuzzy` >= 95% with `fuzzy` <= 5%, measured on events dated within the next 7 days.

## Baseline (2026-09-06, live test run during Task 7, all listed events including future weeks)

| sport | venue markets | matched | fuzzy | unmatched |
|---|---|---|---|---|
| nfl | 772 | 84.2% | 0.3% | 15.5% |
| ncaaf | 3235 | 86.8% | 0.0% | 13.2% |

Unmatched was dominated by `no game for pair` on future-week events. Aliases added that day: Appalachian St. (2026), Louisiana-Monroe (2433), Miami (FL) (2390). The next pass is due after the Week 1 NFL games (Sept 13–14).
