# Autopilot journal — sportsbook harness

Append-only; newest entry last; format defined in `.claude/skills/autopilot/SKILL.md`.
Times are America/Chicago.

## 1. preflight — session start — 2026-09-07 06:55 CT
- Branch / commits: `main` at `b251741` (autopilot skill, roadmap, verify contract, design spec); working branch `fix-build-stamp` created from it
- Result: done
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: walkthrough path validated end to end — tunnel up (`ssh -N -L 8180:127.0.0.1:8180`, pid 44774), `/healthz` 200 through the tunnel, `http://localhost:8180/` rendered in Chrome (Harness Dashboard, run #1268, status ok, last run status skipped, kill switch off; funnel and variant tables populated). NAS: four containers up, `app-serve` healthy, zero ERROR lines in the last 30 min on `app-run` and `app-serve`, database 9.4 GB, 19M orderbook events, 97 final games, 42 primary candidates in the six hours before quiet hours (staleness hotfix confirmed working). Local: `harness-pg-test` up on 5433, no pytest running, Mac on AC with sleep disabled, `secrets/` complete, `.env.nas` present. Clocks: Mac, NAS, and Kalshi's server agree (11:53Z).
- Rulings: (1) The design was presented at 00:45 CT; the user's authorizations and this preflight landed at 06:55 CT (run ids 502→1270 on the NAS confirm six and a half hours of wall time), so the timeline shifts by six hours: phase 3 executes during the day, and Task 1's early deploy is judged on live 15-minute ticks instead of a deferred 08:10 wakeup — cost if wrong: none, the contract's time-of-day table still applies. (2) The build stamp ships first as its own `fix-build-stamp` unit (implementer → reviewer → merge → deploy → baseline verification) so the first phase 3 deploy already has a freshness check — cost if wrong: one extra container restart during quiet hours.
- Carried forward: none
- Next: hotfix-style unit `build-stamp`
