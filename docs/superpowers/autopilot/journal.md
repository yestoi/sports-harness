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

## 2. build-stamp — deploy verification proven end to end — 2026-09-07 07:22 CT
- Branch / commits: `fix-build-stamp` b251741..376ee98 (`557ae4e` feat: build stamp on the dashboard and /healthz; `376ee98` docs), fast-forward merged to `main`, branch deleted
- Result: done
- Tests: 288 passed, pristine (implementer: RED 4 failed → GREEN; controller re-ran the full suite: exit 0, no failures, no warnings)
- Review: clean — sonnet reviewer approved, no Critical/Important; its ⚠️ (commit trailers) verified by the controller with `git log -1 --format=%B`. Minors deferred to the phase 3 final review: (1) runbook sentence "`/healthz` behaves exactly as before" is now stale; (2) `BUILD_SHA`/`BUILD_TIME` are computed for every make target, not only `deploy-nas`; (3) the `/healthz` route reads `settings.build_sha` directly while `/` and `/api/summary` use the precomputed dict
- Deploy: 376ee98 at 07:14 CT; stamp verified on `/healthz` (NAS direct and through the tunnel) and in the dashboard header (`build 376ee98 · deployed 2026-09-07T12:14:53Z`); four containers up, `app-serve` healthy within three minutes; 0 ERROR lines on every service; WebSocket reconnected (last event 4 s old); database 9.45 GB
- Verification: PASS 9/10 (evidence: `docs/superpowers/autopilot/evidence/2026-09-07-build-stamp-01..06-*.jpg`) — items 1–8 and 10 PASS on the controller's own read of the screenshots; item 9 (feed staleness by book) **deferred**: the section uses a one-hour window of odds fetches and none has run since 00:59 CT (quiet hours), so it is judged at the first verification after a real tick (≥ 08:15 CT); expected then: `pinnacle` and the other books listed with median seconds
- Rulings: (1) item 9 is deferred, not failed — the contract's time-of-day rule applies and the checklist wording lacked the caveat, now amended in verify.md — cost if wrong: a broken data-quality section stays unnoticed until the next verification pass, hours at most. (2) The clipped wide tables (signals, unmatched markets) are the existing `.scroll` overflow-x wrapper working as designed; walker guidance added to verify.md — cost if wrong: none. (3) The three review minors are deferred to the phase 3 final review (Task 12 touches the dashboard, Task 14 the runbook) — cost if wrong: cosmetic.
- Anomalies: the WebSocket "Last event" value wraps inside its stat box at a 1232 px viewport (cosmetic; candidate for Task 12's dashboard pass)
- Carried forward: none (the deferred verification item is recorded above)
- Next: **phase** — phase 3 (`docs/superpowers/plans/2026-09-07-phase3-paper-execution.md`) in a fresh session, per the user; the SDD workspace at `.superpowers/sdd/2026-09-07-phase3-paper-execution/` already holds the pre-flight scan, its rulings, and the 14 briefs

## 3. decisions — second authorization round, pre-loaded phases 4–6 — 2026-09-07 07:45 CT
- Branch / commits: `main` (docs only)
- Result: done
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: n/a
- Rulings: recorded in `roadmap.md` — §Pre-loaded decisions (phases 4, 5, 6), §Secrets, §Operator calendar. User decisions this round: brainstorm, plan, and execute phases 4–6 without waiting = yes; operator mode after the last phase = yes; Novig dropped as a venue (not usable in Louisiana; the Odds API `novig` column stays as a recorded benchmark feed); parlay fun budget $50/week; secrets are provisioned by the user when convenient and the loop never blocks on them (features ship conditional on the file). Skill gained the `plan-next` and `operate` units and the matching orient rules.
- Carried forward: none
- Next: **phase** — phase 3 in a fresh session (`/autopilot`), then `plan-next` for phase 4

## 4. review — six-lens adversarial review of the loop and the phase 3 design — 2026-09-07 09:20 CT
- Branch / commits: `main` (docs only so far)
- Result: done (79 de-duplicated findings; 36 MUST-FIX before the loop starts, 21 FIX in phase 3, 10 for phase 4, 4 for phase 5, 8 notes)
- Tests: n/a
- Review: reports under `.superpowers/reviews/autopilot-review-{quant,venue,experiment,risk,arch,autonomy}.md`; consolidated record with decisions and rulings at `docs/superpowers/reviews/2026-09-07-autopilot-adversarial-review.md` (sections A–F)
- Deploy: none
- Verification: n/a
- Rulings: user decisions U1–U4 (Odds API 5M tier before 2026-09-12 with the 90 s criterion unchanged; NO-side as Task 4b with a new two-sided variant id by amendment 3 before 2026-09-16; partition the bulk tables now as Task 2b with a 2 TB ceiling and archive-and-drop as a gate; veto caps $25/day and $150/week with the Sonnet shadow on every call) and controller rulings R1–R23, all in section F of the record. NAS facts checked: 3.5 TB free of 11 TB; stack directory not exported over NFS or SMB; 7 GB RAM with ~1 GB available; 8 cores.
- Carried forward: the ten recorder hotfix items (record §B(3) + R19) are pre-populated in the roadmap's Carried fixes and run first in the fresh session; the phase 3 SDD workspace made earlier today is deleted (R20) and regenerated by the loop
- Next: apply the review (skill, roadmap, verify contract, Makefile and runbook set, phase 3 addendum and plan revision) in this session, then hand off to a fresh session (`/autopilot`)

## 5. review-applied - loop, roadmap, contract, phase 3 revision - 2026-09-07 10:50 CT
- Orient: n/a (controller session applying the review before the first `/autopilot` run)
- Branch / commits: `main` 9c785a9..HEAD (deploy hardening and paper-posture assertions; roadmap and verification contract; skill; U5; tape fixture; review archive; phase 3 addendum, plan and pre-registration revision)
- Result: done
- Dispatches: 6 reviewers, 1 synthesizer, 4 writers, 1 design re-reviewer (2 fix rounds)
- Tests: 265 passed, pristine (after the ops changes)
- Review: phase 3 addendum, plan and record re-reviewed READY after two fix rounds (`docs/superpowers/reviews/2026-09-07-autopilot-review-reports/rereview-phase3-plan.md`)
- Deploy: none (the recorder hotfixes deploy from the fresh session)
- Verification: n/a
- Rulings: U5 (gate judged on `sharp_two_sided` from amendment 3) recorded in the review record section F and the roadmap; R1-R23 unchanged; the plan writer's D1-D15 in addendum §9
- Carried forward: the ten recorder hotfixes in the roadmap's Carried fixes, the fresh session's first units
- Next: fresh session `/autopilot`: hotfix units 1-9 (10 after the Odds tier confirmation), then phase 3 on `phase3-paper-execution` (16 tasks: 1, 2, 2b, 3, 3b, 4, 4b, 5-14)

## 6. preflight - session start - 2026-09-07 11:15 CT
- Orient: n/a (preflight); state files read: roadmap (Carried fixes 1-10 pre-populated, phase 3 `planned`), journal entries 1-5, verify.md
- Branch / commits: `main` at 9a8084f, tree clean
- Result: done, with two live anomalies found (below)
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none yet; NAS runs 376ee98, `main` is ahead in code (Dockerfile constraints install, Makefile deploy hardening, deploy/nas.env `LIVE_TRADING=0`/`HARNESS_MODE=paper`, tests) so Orient rule 2 fires next
- Verification: preflight checks 1-9: sleep prevented (caffeinate present); `harness-pg-test` Up on 5433, no pytest; NAS four containers Up, `app-serve` healthy, `/healthz` 200 build 376ee98, BatchMode ssh ok; tunnel: an earlier session's tunnel (pid 44774) still holds 8180 and answers 200 (my duplicate failed to bind and is killed); Chrome bridge: `tabs_context_mcp` answers (group 619673834); secrets all `-rw-------`, optional present: `kalshi_demo_key_id`, `kalshi_demo_private_key.pem`, `anthropic_api_key`; absent: `backup_age_key`, `legal_decision`; `.env.nas` exists; tools resolved: ScheduleWakeup, PushNotification, ListAgents, CronCreate, CronList, CronDelete, Monitor, SendMessage (all eight); test notifications: PushNotification "Terminal notification sent. Mobile push requested.", osascript ok. Paper posture output verbatim: `paper posture intact`.
- Daily watch (Mon 09:00 duty, folded in): `/volume1` 3.5T free of 11T (31 % free; above 30 %); `free -m` available 1658 MB, swap 4.5 GB used (NAS also runs Sonarr/Radarr/downloader); load 5.05 on 8 cores, 6 % iowait; database 9603 MB, WAL 80 MB; Odds credits 98359 remaining, 8 per real tick (about 550/day, inside the 500-3000 band); executor n/a (phase 3); ERROR lines app-run: `pricing failed` every real tick since 13:03Z, one `normalize kalshi_trades raw_id=19751 failed`; kill switch not toggled (observed off on the last walkthrough); age-key nag: n/a (file not created yet). Postgres runs container defaults (shared_buffers 128 MB, work_mem 4 MB, max_wal_size 1 GB); carried fix 9 addresses that.
- Anomalies: (A1) **recorder WebSocket down since 15:53Z (10:53 CT)**: `app-ws` logs `Handshake status 401 Unauthorized ... authentication_error NOT_FOUND` on every 60 s reconnect; `orderbook_events` last event 15:53Z; `secrets/kalshi_key_id` and `secrets/kalshi_private_key.pem` on the Mac have mtime 10:54 CT today, consistent with the User-side TODO (new read-scoped key, old key revoked) having been done; the NAS still holds the old key. (A2) **pricing fails on every real tick since the first one after quiet hours (13:03Z, 08:03 CT)**: runs `degraded`, warning `psycopg.OperationalError: number of parameters must be between 0 and 65535`; no `signals` row since 05:59Z; 12 degraded ticks, identical message. (A3) one `venue_trades` insert cancelled by the harness's 30 s statement timeout "while inserting index tuple", while an `app-run` session (pid 38796, opened 12:56Z) has sat `idle in transaction` since 15:53:46Z on `INSERT INTO venue_trades`: a leaked transaction that makes later inserts of the same trade ids wait on the unique index. (A4) my first Layer 2 query batch took over two minutes on the NAS (the `max(ts)` over `orderbook_events`); load is high.
- Rulings: (1) A1 is "recorder down", one of the three named deploy-window exceptions, but no window is open (in_progress 0, kickoffs within -4 h/+15 min 0, NFL 60-100 min 0), so the deploy of `main` runs as an ordinary Orient rule 2 deploy; it pushes the new key files and lands the pending ops hardening - cost if wrong: one container restart outside any game. (2) The Monday 09:00 weekly report duty is n/a: `harness report` and `harness gate` do not exist before phase 3 (`harness/cli.py` has only `match-report`); journaled, not skipped silently. (3) The daily watch line is folded into this entry rather than a separate operate entry - cost if wrong: none. (4) A2 is a reproduced verification failure of the Signals row, judged in the verify unit after the deploy; its hotfix runs before the review's carried fixes because it is a live defect that leaves the dataset without signals - cost if wrong: the review batch slips by one unit.
- Carried forward: none yet (the verify unit after the deploy scores A2 and A3)
- Next: deploy (`main` 9a8084f), then verify
