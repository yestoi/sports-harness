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

## 7. deploy - main b5ce52b (new Kalshi key, ops hardening) - 2026-09-07 11:13 CT
- Orient: rule 2 - `/healthz` build 376ee98 on the NAS, `git diff --stat 376ee98..main -- . ':!docs' ':!*.md'` non-empty (Dockerfile, Makefile, constraints.txt, deploy/nas.env, tests); plus anomaly A1 (recorder WebSocket down on a revoked key) as the urgency
- Branch / commits: `main` at b5ce52b (376ee98..b5ce52b: review record and deploy hardening 9c785a9, skill/roadmap/contract revisions, phase 3 revision, preflight journal)
- Result: done
- Dispatches: 0
- Tests: n/a (docs and ops commits; 265 passed on `main` per entry 5)
- Review: n/a
- Deploy: b5ce52b at 11:13 CT via `make deploy-nas` (full: the Dockerfile and constraints.txt changed, so `deploy-nas-app` did not apply and did not exist for this tree anyway); preconditions held: `main`, clean tree, game window closed (in_progress 0, kickoff -4 h/+15 min 0, NFL 60-100 min 0; the day's only game is FSU vs SMU at 18:30 CT); four containers Up, `app-serve` healthy within one minute; stamp verified on `/healthz` (`"build":"b5ce52b"`) and in the dashboard header (`build b5ce52b · deployed 2026-09-07T16:12:51Z`); `seed-teams` ok; `app-ws` reconnected at 16:13:51Z with the new key (`ws connected`); gap rows in the 2 h after the restart: 0; 100 fresh snapshots and 92,030 deltas in the 2 h window
- Verification: see entry 8
- Rulings: none beyond entry 6 ruling (1)
- Carried forward: none
- Next: verify

## 8. verify - deploy b5ce52b - 2026-09-07 11:25 CT
- Orient: rule 3 - no `verify` entry since the `deploy` entry 7
- Branch / commits: n/a
- Result: **FAIL** (two Layer 2 rows; walkthrough 8/9)
- Dispatches: 1 (walker `sonnet`)
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: Layer 1 PASS (`build b5ce52b`, no `-dirty`). Layer 2 at 16:15-16:21Z: heartbeat `skipped` row inside 2 min (1764 at 16:15:21Z) PASS; real tick inside the 15 min window PASS (1770 at 16:18:21Z); last 5 real ticks: 0 `error` PASS; **ERROR lines FAIL**: `pricing failed` at 16:19:36Z on run 1770 (not on the upstream list; identical on all 12 real odds ticks since 13:03Z); tape continuity: gap rows 2 h = 0 PASS; WS last event age 0.59 s PASS; Postgres: worst dead-tuple table `games` 70/450 (15.5 %), WAL 80 MB PASS; disk 3.5T free of 11T (31 %) PASS, memory available 1563 MB PASS; degraded sections 0 PASS; credits 98351 numeric, decreasing 8 per real tick PASS; **Signals FAIL**: 0 rows of any decision since 05:59Z on a weekday afternoon with 97 games inside 8 days (expected candidates > 0 for the primary); DB size 9606 MB PASS. Layer 2b: `fair_values.staleness_s < 0` = 0 PASS; the `taker_side_missing` invariant waits for carried fix 1. Bands: candidates per real tick = 0 today (below the 1-500 band because pricing fails, same defect, not a second anomaly); WS gap rows 0 PASS; credits per day about 550 PASS. Layer 3 (evidence: `docs/superpowers/autopilot/evidence/2026-09-07-verify-b5ce52b-1119-01..06-*.jpg`): item 1 PASS, header `build b5ce52b · deployed 2026-09-07T16:12:51Z`; item 2 **FAIL**, status `ok`, kill switch `off`, but `Credits remaining` reads `None` (run #1770 `running`, 39 s since; `harness/health.py:29` reads `odds_remaining` from the newest run row, which is a 30 s heartbeat or a Kalshi-only tick nearly always, so the page shows `None` between real odds ticks: a dashboard defect against the contract's "numeric", not a data defect: the runs table carries 98351); item 3 PASS, odds_api 225, kalshi 19666, espn 126; item 4 PASS, six variants (constrained 51, nfl_only 96, no_velocity 126, sharp_direct 126, sharp_plus_derived 487, wide_band 132 candidates); item 5 PASS, nfl 772 markets 76.9 % matched, ncaaf 4126 markets 87.8 %; item 6 PASS, primary rows render (newest 05:59:41Z, GB @ MIN, MIA @ LV, BUF @ HOU spreads and totals; the age is the Signals FAIL, not a rendering fault); item 7 PASS, about 50 unmatched rows; item 8 PASS, last event 16:19:01Z, 4983 events in 5 min, 1666 WS trades in 1 h; item 9 PASS, seven books, pinnacle median 10.7 s (this also closes entry 2's deferred item 9). Cross-checks: header sha exact; run id 1770 = `max(id)` 1770; candidates per variant exact match to the SQL (49af716f8708 487, 64ba3ef09642 126, c2bc45377328 132, e549e693e117 96, f259ca109084 126, ff363c8ac08d 51); DB size and executor rows n/a before phase 3. Walker verdict FAILED on item 2 only; the controller's scores agree item by item.
- Anomalies: (1) the walker reports `http://localhost:8180/` reverting to `chrome://newtab/` on its first attempts before rendering; the controller's own preflight load rendered first time; watch for recurrence. (2) The WebSocket `Last event` value still wraps in its stat box (cosmetic, entry 2). (3) A3 from entry 6 (leaked idle-in-transaction sink session) cleared with the restart and cannot recur while the socket stays up; it is fixed by construction in the hotfix below rather than waited on. (4) Match report: nfl unresolved bare cities `Los Angeles` 90 and `New York` 88 (ambiguous by design, I9, phase 6 item 5), ncaaf `no game for pair` 367 (FCS-only games ESPN's FBS scoreboard does not carry) and ten FCS or renamed teams; Odds API unresolved `Grambling State Tigers`, `Southern University Jaguars`, `UMass Minutemen` (the alias pass duty, entry 10).
- Rulings: (1) The ERROR-line and Signals FAILs share one root cause (the signals insert overflows psycopg's 65535 bind parameters above 3120 rows; 4218 matched markets) and are one carried fix with A3 (the sink flush), run as a single hotfix unit `fix-tick-failures` - both were scored by this pass and touch disjoint files - cost if wrong: a larger review diff. (2) Item 2 is a real FAIL of the dashboard against the contract, carried as fix 11 (`/healthz` and the Health block carry forward the newest non-null `odds_remaining`), scheduled after the review batch because the data is intact and the runs table is the contract's Layer 2 source - cost if wrong: the page keeps reading `None` for a day. (3) The verify unit is not re-run 10 minutes later for the pricing FAIL: it reproduced on run 1770 after the restart and on 12 ticks before it, which is the reproduction the hotfix rule asks for - cost if wrong: none.
- Carried forward: roadmap Carried fixes 0 (A2 + A3, `fix-tick-failures`, in progress) and 11 (credits display)
- Next: hotfix `fix-tick-failures` (opus implementer dispatched 11:23 CT), then operate (alias pass), then carried fixes 1-3

## 9. hotfix - fix-tick-failures - 2026-09-07 11:45 CT
- Orient: rule 1 - entry 8 ends in FAIL; roadmap Carried fixes item 0 (added by entry 8)
- Branch / commits: `fix-tick-failures` b5ce52b..6eb0b1f (a9dd2b9 chunk the signals insert at 1000 rows; 2601e75 `WsSink.flush()` called from `run_forever`'s inner `finally`; b4a5a3e journal entries 7-8 and evidence; 6eb0b1f flush on every recv timeout), fast-forward merged to `main`, branch deleted
- Result: done
- Dispatches: 3 (implementer `opus` plus one fix round by SendMessage; reviewer `opus`; scoped re-review `haiku`)
- Tests: 269 passed, pristine (controller's own full run on 6eb0b1f, `pgrep -f pytest` empty first); up from 265 on `main`
- Review: APPROVED with 1 Important and 5 Minors; 1 fix round (the Important: a live but silent socket kept the pending batch open until `is_stale` at 180 s against the normalizer's 30 s statement timeout, fixed by a guarded flush in the recv-timeout branch with a covering test; Minors 1-2 folded in: redundant local import, `_FakeSink.flushes` now asserted at the measured 3 and 8); re-review APPROVED, no findings. Ledgered without action: the `sink is None` guard is unreachable in production (harmless, asked for by the brief); the "21 parameters" comment goes stale if columns are added (headroom to 65 columns per row); `close()` duplicates `flush()`'s body
- Deploy: none in this unit (entry 10)
- Verification: not run in this unit (entry 10 re-verifies the ERROR-line and Signals rows)
- Rulings: (1) one hotfix unit covers A2 and A3 - both scored by the same verify pass, disjoint files - cost if wrong: a larger review diff. (2) Implementer deviations accepted: `signals` has no foreign keys so the 3200-row test uses synthetic parent ids; the A3 test pins a large `commit_interval_s` so RED is not wall-clock dependent - cost if wrong: none. (3) The reviewer's Important is fixed in round 1, not parked - three lines, same file, same defect class - cost if wrong: ten minutes. (4) The implementer's measured flush counts (3 and 8) replace the reviewer's estimate (3 and 3) because it proved the assertion discriminates - cost if wrong: a brittle test. (5) Minors 3-5 need no action - cost if wrong: cosmetic.
- Carried forward: none (Carried fixes item 0 removed; items 1-9 and 11 remain, 10 waits on U1)
- Next: deploy (`main` at the journal commit), then verify, then operate (alias pass)

## 10. deploy - main e87f714 (hotfix fix-tick-failures) - 2026-09-07 11:45 CT
- Orient: rule 1 continuation (the hotfix unit's deploy step); rule 2 also true (`/healthz` build b5ce52b, code diff to `main` non-empty)
- Branch / commits: `main` b5ce52b..e87f714 (a9dd2b9, 2601e75, 6eb0b1f code; b4a5a3e, e87f714 docs)
- Result: done
- Dispatches: 0
- Tests: 269 passed, pristine (entry 9)
- Review: n/a
- Deploy: e87f714 at 11:45 CT via `make deploy-nas` (full: the diff touches `ws_sink.py` and `ws.py`; `deploy-nas-app` does not apply before `app-exec` exists); preconditions: `main`, clean tree, game window closed (0/0/0 at 16:20Z; FSU vs SMU kicks off 18:30 CT), third deploy of the day (ceiling 6); image build cached, containers Up within 10 s, `app-serve` healthy by 11:50 CT; stamp verified on `/healthz` (`"build":"e87f714"`); `app-ws` reconnected at 16:45:58Z; gap rows in the 2 h window after the restart: 0; 50 snapshots and 4996 deltas in the 10 min after
- Verification: re-verify of the failed rows at 11:50 CT (16:49Z): **ERROR lines PASS** (0 on app-run, app-serve, app-ws in the 6 min since the restart, covering the 16:48Z real tick); **Signals PASS**: run 1826 at 16:48:27Z `ok`, credits 8, pricing notes carry per-variant counts, 407 candidates and 24,301 rejected in the last 20 min, primary f259ca109084 53 candidates (band 1-500 per real tick); degraded sections 0; one `idle in transaction` session aged 0.9 s (a tick mid-commit, not the leak). Layer 1 PASS. The other rows were scored PASS in entry 8 and nothing in this deploy touches them; the Chrome walkthrough is not repeated for a re-verify of two rows (verify.md: "re-verify only the failed items or the rows the finding names").
- Rulings: (1) The hotfix unit's re-verification is the deterministic Layer 2 pass above, no walker - cost if wrong: a rendering fault in the signals table stays unseen until the next full verify (the post-game verify tomorrow morning at the latest).
- Carried forward: none
- Next: operate (alias pass, in progress), then hotfix carried fix 1 (F5)

## 11. operate - alias pass 2026-09-07 - 2026-09-07 11:55 CT
- Orient: rule 4 - Monday 09:30 CT duty, run at the first unit boundary after the deploy, verify and hotfix units (2 h 25 min overdue at start); Monday 09:00 weekly report n/a before phase 3 (`harness report`/`gate` do not exist; entry 6 ruling 2); Monday 09:45 replay-vs-live n/a before phase 3; Monday repo bundle done at 11:23 CT (`/tmp/sports-2026-09-07.bundle`, 1.5 MB, verified, copied to `/volume1/docker/sports-harness/repo-backup/`)
- Branch / commits: `fix-aliases-2026-09-07` e87f714..fd7b5f8 (ac26462 eleven aliases; fd7b5f8 journal entry 10), fast-forward merged to `main`, branch deleted
- Result: done
- Dispatches: 2 (implementer `sonnet`, reviewer `sonnet`)
- Tests: 270 passed, pristine (controller's full run on fd7b5f8)
- Review: APPROVED, 1 Minor ledgered (`LIU` normalises to a generic three-letter key; manual sources win and the name was unmatched before, so nothing regresses)
- Deploy: none in this unit; the aliases ride the next hotfix deploy of `main` (deploy ceiling 6 per day, 3 used), and the match-rate confirmation runs at that deploy's verification (expected: ncaaf above 87.8 % and the eleven names gone from `harness match-report`)
- Verification: not run (see Deploy)
- Rulings: (1) Duties run at the first unit boundary after a reproduced-failure hotfix; the review-batch hotfixes (roadmap items 1-9) do not pre-empt a due duty because they are planned work, a reproduced FAIL does - cost if wrong: a review hotfix lands an hour later. (2) NFL bare cities `Los Angeles` (90 markets) and `New York` (88) are not alias entries: each names two teams and needs ticker-code disambiguation (phase 6 item 5); reported to the user as a candidate to pull forward since Rams/Chargers and Giants/Jets spread markets stay unmatched through week 1 - cost if wrong: 23 % of NFL markets unmatched for the experiment's first week. (3) `UT Rio Grande Valley` and `Chicago St.` have no ESPN team row; no alias - cost if wrong: six markets. (4) The alias deploy rides the next hotfix deploy - cost if wrong: the match rate is confirmed about an hour later.
- Carried forward: none
- Next: hotfix carried fix 1 (F5, `fix-taker-side`), then deploy (carrying the aliases), then verify
