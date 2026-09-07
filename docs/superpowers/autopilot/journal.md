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

## 12. hotfix - fix-taker-side (carried fix 1, F5) - 2026-09-07 12:20 CT
- Orient: rule 1 - roadmap Carried fixes item 1 (F5), the first review-batch hotfix (R19)
- Branch / commits: `fix-taker-side` 9524f87..3504829 (5b31f0c canonical side, two additive columns, drop-and-count; 3504829 book side is bid|ask, fixtures re-paired, legacy and upgrade-path tests), fast-forward merged to `main`, branch deleted
- Result: done
- Dispatches: 3 (implementer `opus` plus one fix round by SendMessage; reviewer `opus`; scoped re-review `sonnet`)
- Tests: 277 passed, pristine (controller's full run on 3504829); up from 270
- Review: CHANGES_REQUIRED then APPROVED after 1 fix round. Important (fixed): `taker_book_side`'s domain is `bid|ask` (REST and WebSocket docs), the first round filtered it through the yes/no rule and would have stored NULL for every real print; fixtures had used a value Kalshi never emits. Minors: schema test could not fail on a wrong DDL name (fixed: the test now drops the column and re-runs `create_schema`); no WS legacy-path assertion (fixed); `taker_side_missing` counts occurrences across overlapping trade pages, not distinct prints (ledgered: the contract's invariant is `> 0`); `missing_side` is read by tests only, the WARNING line is the operational signal (as specified)
- Deploy: none in this unit (entry 13 carries this fix and the alias pass)
- Verification: not run in this unit (entry 13: the `taker_side_missing` invariant, trades flowing on both paths, the match rate)
- Rulings: (1) The brief's counter name `missing_side` stands over the phase 3 plan's `dropped_no_side`; the SDD pre-flight scan reconciles the plan line at phase start (R20) - cost if wrong: one stale plan line. (2) Sides are lower-cased before storage and junk is stored as `None`; "verbatim" in the brief yielded to the roadmap's domain rule - cost if wrong: none. (3) The `taker_side_missing` occurrence count is acceptable for the invariant; a distinct-print count is phase work if a report ever needs it - cost if wrong: an overstated drop count in run notes. (4) The tape fixture (197 legacy-only prints) is flagged for phase 3 Task 4's fill-model tests, which need a fixture with the new fields - cost if wrong: Task 4's RED misses the new-field path.
- Carried forward: none (Carried fixes item 1 removed)
- Next: deploy (`main`, carrying fixes 1 and the aliases; fourth deploy of the day), then verify, then hotfix carried fix 2

## 13. deploy - main b6adfc7 (carried fix 1, alias pass) - 2026-09-07 12:18 CT
- Orient: rule 1 continuation (the hotfix unit's deploy step, also carrying the operate unit's merge); rule 2 true (`/healthz` build e87f714, code diff to `main` non-empty)
- Branch / commits: `main` e87f714..b6adfc7 (ac26462 aliases; 5b31f0c, 3504829 taker side; fd7b5f8, 9524f87, b6adfc7 docs)
- Result: done
- Dispatches: 0
- Tests: 277 passed, pristine (entry 12)
- Review: n/a
- Deploy: b6adfc7 at 12:18 CT via `make deploy-nas` (full: `ws_sink.py` and `models.py` changed); preconditions: `main`, clean tree, game window 0/0/0 at 17:17Z (FSU vs SMU at 18:30 CT is the day's only game), fourth deploy of the day (ceiling 6); `init-db` added `venue_trades.taker_outcome_side` and `taker_book_side` (additive, `ADD COLUMN IF NOT EXISTS`); `seed-teams` loaded the manual aliases; containers Up within 10 s, `app-serve` healthy by 12:22 CT; stamp verified on `/healthz` (`"build":"b6adfc7"`); `app-ws` reconnected at 17:18:13Z; gap rows in the 2 h window: 0; 50 snapshots and 2183 deltas in the 5 min after
- Verification (the rows the finding names, 12:22 CT): Layer 1 PASS. Invariant `runs.notes->>'taker_side_missing' > 0` in 24 h = **0** PASS (runs 1883 and 1888 carry the key with value 0); trades since the restart carry the new columns on both paths: REST 41 rows, 41 with `taker_outcome_side` and `taker_book_side` (values `bid`/`ask`); WebSocket 250 rows since the new connection at 17:18:13Z, all with both columns; the 9 WebSocket rows without them (17:18:01-17:18:10Z) are the old container's final flush and predate the new code; WS "taker side missing" warnings 0; ERROR lines 0 on all three services; degraded sections 0; run 1883 at 17:19:13Z `ok` with 4273 gap snapshots priced and 537 candidates in 15 min; credits 98319. Alias confirmation: PASS at 12:33 CT (`harness match-report` after the 17:33Z tick): ncaaf 4126 markets **91.6 % matched** (was 87.8 %; matched 3624 -> 3779, unmatched 502 -> 347); the eight Kalshi names from the alias pass are gone, leaving only `UT Rio Grande Valley` 4 and `Chicago St.` 2 (no ESPN team row) and `no game for pair` 341; nfl unchanged at 76.9 % (the bare-city pair, phase 6 item 5); the report's 7-day `unresolved Odds API names` list still shows the three Odds API names because it aggregates past run notes; judged again at the next odds tick that carries those games
- Rulings: (1) the deterministic rows above are the re-verification of this deploy; the Chrome walkthrough is not repeated (verify.md: only the failed items or the rows the finding names) - cost if wrong: a rendering fault stays unseen until the post-game verify.
- Carried forward: none
- Next: hotfix carried fix 2 (`fix-gap-rows`, implementer dispatched 12:18 CT)

## 14. hotfix - fix-gap-rows (carried fix 2, F6) - 2026-09-07 12:34 CT
- Orient: rule 1 - roadmap Carried fixes item 2 (F6)
- Branch / commits: `fix-gap-rows` b6adfc7..8935e64 (3e439df gap rows keyed to the subscription with `ticker=""` and `{sid, expected, got, exposed_by}` in `raw`, snapshots routed through `_check_seq`; 8935e64 `exposed_by` null for a ticker-less frame, the gap-before-snapshot id order documented), fast-forward merged to `main`, branch deleted
- Result: done
- Dispatches: 3 (implementer `opus` plus one fix round by SendMessage; reviewer `opus`; scoped re-review `haiku`)
- Tests: 281 passed, pristine (controller's full run on 8935e64); up from 277
- Review: APPROVED with 2 Importants and 3 Minors; 1 fix round. Important 1 (fixed, comment only): the gap row must take the lower id than a coinciding snapshot because phase 3's book loader dirties on `gap.sid = anchor.sid and gap.id > anchor.id`. Important 2 (carried as a note, not a fix): `seq` continuity across `update_subscription add_markets` is assumed, not documented by Kalshi; the exposure predates this change and carried fix 4 clears `_last_seq[sid]` on a forced resubscribe. Minor 1 (fixed): `exposed_by` could be `""`, now `None`. Minor 2 (F51, carried fix 3): a gap row is lost if the row after it raises. Minor 3: `raw["sid"]` duplicates the column by design. Re-review APPROVED, no findings
- Deploy: none in this unit (entry 15)
- Verification: not run in this unit (entry 15: gap rows after the restart, sink health)
- Rulings: (1) Test 3 (first snapshot on a fresh sid writes no gap) cannot fail on `main`; the implementer's RED against a deliberately wrong variant, never committed, is accepted as evidence - cost if wrong: none. (2) Important 2 is a note for the phase 3 SDD ledger at phase start (Task 3b keeps the planned `reset_sid` in the F7 recovery), not a hotfix - cost if wrong: a false gap after a forced resubscribe until carried fix 4 lands. (3) The brief's line numbers had drifted after carried fix 1; behaviour facts were right, no brief rewrite - cost if wrong: none.
- Carried forward: none (Carried fixes item 2 removed)
- Next: deploy (`main`, fifth of the day), verify, then hotfix carried fix 3 (F51, `fix-sink-exception-mark`)

## 15. deploy - main 195be51 (carried fix 2) - 2026-09-07 12:43 CT
- Orient: rule 1 continuation (the hotfix unit's deploy step); rule 2 true (`/healthz` build b6adfc7, code diff to `main` non-empty)
- Branch / commits: `main` b6adfc7..195be51 (3e439df, 8935e64 code; 195be51 docs)
- Result: done, on the second attempt
- Dispatches: 0
- Tests: 281 passed, pristine (entry 14)
- Review: n/a
- Deploy: 195be51 at 12:43 CT via `make deploy-nas` (full: `ws_sink.py` changed); preconditions: `main`, clean tree, game window 0/0/0 at 17:35Z, fifth deploy of the day (ceiling 6). The first attempt at 12:36 CT (background) was killed by the Mac's low-memory guard right after the source and `.env` push (the NAS `.env` already read `BUILD_SHA=195be51` while `/healthz` still said b6adfc7); the target is idempotent, so it was re-run in the foreground at 12:37 CT and completed at 12:42 CT. Containers Up, `app-serve` healthy within a minute; stamp verified on `/healthz` (`"build":"195be51"`); `app-ws` reconnected at 17:43:01Z; gap rows in the 2 h window: 0 (the new gap code wrote none on the reconnect, as `reset_sequences` intends); 50 snapshots and 1768 deltas in the first 4 min
- Verification (12:46 CT, the rows the finding names): Layer 1 PASS; tape continuity PASS (0 gap rows); WebSocket flowing PASS; WS trades since the restart 226, all with `taker_book_side`; invariants `taker_side_missing` 0 and `staleness_s < 0` 0 PASS; ERROR lines 0 on all services PASS; degraded sections 0 PASS. No odds tick fell inside the 4 min window (ticks at :33 and :48); the pricing rows were verified on e87f714 and b6adfc7 and this deploy touched only the sink
- Rulings: (1) A deploy killed locally after the push is completed by re-running the same target, never by hand-restarting containers; the stamp on `/healthz` is the only proof of landing - cost if wrong: one extra `init-db`/`seed-teams` pass (both idempotent). (2) Deploys run in the foreground for the rest of this session because the background guard killed one; the 10-minute foreground limit covers a cached build - cost if wrong: a slow build times out and is re-run.
- Anomalies: Mac memory pressure (32 % free) killed a background task; the Mac runs the session, Chrome and the test Postgres container
- Carried forward: none
- Next: hotfix carried fix 3 (`fix-sink-exception-mark`, implementer dispatched 12:43 CT), the fourth and last hotfix unit of the day

## 16. hotfix - fix-sink-exception-mark (carried fix 3, F51) - 2026-09-07 13:15 CT
- Orient: rule 1 - roadmap Carried fixes item 3 (F51); the fourth and last hotfix unit of the day (ceiling 4)
- Branch / commits: `fix-sink-exception-mark` 195be51..68b206f (9fcb5e0 a sink exception writes a `gap` mark with the discarded count in a fresh transaction after the rollback, guarded; aee0013 one mark per subscription in the discarded batch; ca65a55 the mark's own bookkeeping guarded and kept off sid 0, per-subscription seq, sids added only where rows land; 68b206f the guard test drives a real failed flush), fast-forward merged to `main`, branch deleted
- Result: done
- Dispatches: 3 (implementer `opus` plus two fix rounds and one addendum by SendMessage; reviewer `opus`; scoped re-review `sonnet`)
- Tests: 287 passed, pristine (controller's full run on 68b206f); up from 281
- Review: APPROVED with 2 Importants and 6 Minors after the controller's pre-review round; 1 further fix round (round 2). Important 1 (fixed): the sid set's sort ran outside the guard, so a malformed sid would have thrown from inside the except handler and taken the recorder down. Important 2 (fixed): a sid-less failure wrote a sid-0 mark, which phase 3 reserves for REST anchors (one such row would dirty every REST-anchored book). Minors fixed: per-subscription `seq` on mark rows; sids join the pending set only where a row lands (a dropped or deduplicated trade puts nothing at risk); the guard test now reaches a real failed flush (a bigint-overflow `seq`; RED by removing the guard's rollback, restored). Ledgered: a raising `rollback()` in `_maybe_commit` can carry stale sids across a reconnect (narrow, fail-safe); type hints on `_last_seq`/`_pending_sids` no longer cover the string-sid case the test feeds (cosmetic); a literal sid 0 from the venue is assumed impossible file-wide. Re-review APPROVED, no findings
- Deploy: none in this unit (entry 17)
- Verification: not run in this unit (entry 17: the full contract with the walker)
- Rulings: (0) All mark rows of one failure share one flush, so one refused row loses the others' marks; kept (the marks share one cause, the case is rare, per-row transactions are phase 3 Task 3b's call) - cost if wrong: lost marks on a refused row, which the test documents. (1) Fix round 1 ran before the review on the implementer's own note: a mark filed under the failing message's sid alone would leave the other sids in the discarded batch looking clean to phase 3's dirty-book rule (`gap.sid = anchor.sid and gap.id > anchor.id`); one row per pending sid - cost if wrong: a few extra gap rows per sink exception. (2) `_maybe_commit`'s commit-failure path (silent discard when the database itself fails) is phase 3 Task 3b's, not this hotfix's - cost if wrong: a silent hole on a database outage that the heartbeat and tape-continuity rows expose anyway. (3) The guard test's RED by narrowing the inner `except` is accepted as evidence - cost if wrong: none. (4) verify.md's tape-continuity row names "the deploy or the socket" as the causes of a gap row; a sink exception is now a third, told apart by `raw.exposed_by = "sink_exception"`; the controller reads `raw` before naming a cause and the contract text is amended by phase 3's last task, not by a hotfix - cost if wrong: a mis-named cause in one journal line.
- Carried forward: none (Carried fixes item 3 removed)
- Next: deploy (`main`, sixth and last of the day), then the full verify, then the resume drill (R6) at the idle boundary

## 17. deploy+verify - main 8504871 (carried fix 3), full contract - 2026-09-07 13:30 CT
- Orient: rule 1 continuation (the hotfix unit's deploy step), then rule 3 (no verify since the deploy)
- Branch / commits: `main` 195be51..8504871 (9fcb5e0, aee0013, ca65a55, 68b206f code; 8504871 docs)
- Result: done; verification **PASS on every Layer 2 row, 8/9 on the walkthrough** (item 2 is the open carried fix 11; no new carry)
- Dispatches: 1 (walker `sonnet`)
- Tests: 287 passed, pristine (entry 16)
- Review: n/a
- Deploy: 8504871 at 13:19 CT via `make deploy-nas` (full: `ws_sink.py` changed), foreground per entry 15 ruling 2; preconditions: `main`, clean tree, game window 0/0/0 at 18:18Z, sixth deploy of the day (ceiling 6, now reached); containers Up, `app-serve` healthy by 13:22 CT; stamp verified on `/healthz` (`"build":"8504871"`) and in the header; `app-ws` reconnected at 18:19:53Z; gap rows in the 2 h window: 0 (three restarts inside it; `raw.exposed_by` read: no rows at all); 200 snapshots and 62,521 deltas in the window
- Verification: Layer 1 PASS. Layer 2 (13:22-13:26 CT): last 5 real ticks 1937, 1942, 1965, 1970, 1993 all `ok`, 0 `error`, no repeated key PASS; heartbeat 1995 at 18:22:23Z inside 2 min PASS; real tick inside 15 min PASS (1993 at 18:20:23Z); ERROR lines 0 on app-run, app-serve, app-ws (app-exec absent before phase 3) PASS; tape continuity 0 gap rows PASS; WS last event age 0.86 s PASS; Postgres worst dead tuples `trade_watermarks` 176/2046 (8.6 %), `games` 70/453 (15.5 %, never autovacuumed, under the 20 % line), WAL 80 MB PASS; disk 3.5T free of 11T (31 %) PASS; memory available 1829 MB PASS; degraded sections 0 PASS; credits 98,287, numeric, decreasing 8 per odds tick PASS; signals 3 h: 3069 candidates, 139,275 rejected PASS; DB size 9780 MB (up from 9603 MB at 11:20 CT) PASS. Layer 2b: `staleness_s < 0` 0, `taker_side_missing` 0 PASS. Bands: primary candidates 380 over 5 real ticks in 3 h (76 per tick, band 1-500) PASS; credits per day 702 (band 500-3000) PASS; WS gap rows 0 PASS. Time-of-day (weekday 08:00-01:00): real tick every 15 min, candidates > 0 PASS. Trades since the restart carry the book side on both paths (REST 24/24, WS 394/394). Layer 3 (evidence: `docs/superpowers/autopilot/evidence/2026-09-07-verify-8504871-1321-01..05-*.jpg`): item 1 PASS, header `build 8504871 · deployed 2026-09-07T18:18:44Z`; item 2 **FAIL**, status `ok`, kill switch `off`, `Credits remaining` `None` (run #1993 `running`, 68 s since; carried fix 11, already open, no new item); item 3 PASS, odds_api 249, kalshi 21263, espn 142; item 4 PASS, six variants (constrained 93, nfl_only 256, no_velocity 508, sharp_direct 413, sharp_plus_derived 1668, wide_band 651 candidates in 24 h); item 5 PASS, nfl 772 markets 76.9 %, ncaaf 4126 markets 91.6 % (the alias pass confirmed on the page); item 6 PASS, primary rows render, newest 18:05:40Z, the visible tail being the newest tick's no-fair totals markets (WEB @ COLO, SHU @ MASS, WAG @ JMU: FCS opponents without a sharp line, `rejected`/`has_fair`), consistent with the 413 candidates in item 4; item 7 PASS, 50 unmatched rows, top by volume the SCST-FAMU game ESPN's FBS scoreboard does not carry; item 8 PASS, last event 18:21:29Z, 4089 events in 5 min, 3157 WS trades in 1 h; item 9 PASS, seven books, pinnacle median 9.8 s. Walker verdict FAILED on item 2 only; the controller's scores agree item by item. Cross-checks: header sha exact PASS; run id 1993 on the page vs `max(id)` 2002 four minutes later (30-s heartbeats) PASS; candidates per variant: page 413/1668/508/256/651/93 vs SQL 506/1948/601/288/651/93; the page rendered at 18:21:31Z while run 1993 was `running`, and the SQL bounded to the page's instant still includes that run's rows because `created_at` is the tick's pricing clock (18:20Z) while the commit landed after the render; the difference per variant equals run 1993's candidate count exactly (primary 93), so PASS with that reading; DB size and executor cross-checks n/a before phase 3.
- Anomalies: (1) the walker's first navigation bounced to `chrome://newtab/` again (second occurrence today; harmless, retried); (2) the signals table's visible tail is one tick's no-fair rows (a display ordering, not a data fault); (3) the WebSocket `Last event` value still wraps in its stat box (cosmetic, entry 2).
- Rulings: (1) Item 2's FAIL is the open carried fix 11, not a new carry, and the "same failed item twice running" gate applies to a hotfix's re-verification failing twice, not to a carried item awaiting its turn - cost if wrong: one more `None` reading on the page. (2) Carried fix 11 moves to the front of the next day's batch (before item 4) so the item does not fail a third verification - cost if wrong: item 4 lands one unit later. (3) The candidates cross-check is judged at the page's instant with in-flight ticks excluded; the contract's 5 % tolerance assumes a quiescent instant, and phase 3's last task may word it so - cost if wrong: a real 5 % dashboard drift hides behind an in-flight tick once. (4) The daily ceilings are reached (4 hotfix units, 6 deploys); carried fixes 11, 4, 5, 6 run from 00:01 CT on 2026-09-08 (four units), 7, 8, 9 on 2026-09-09 from 00:01 CT (9 inside the 01:00-08:00 quiet window), then phase 3 - cost if wrong: phase 3 starts a day later than an unceilinged loop would. (5) plan-next for a phase is gated by the previous phase's `done` status: the addendum needs the shipped schema and the review findings of the phase before it (phase 4's Alembic baseline covers phase 3's tables), so Orient rule 6 does not fire for phase 4 tonight - cost if wrong: an evening of idle time that could have drafted a design that phase 3 would then invalidate.
- Carried forward: none new (item 11 reordered)
- Next: drill (R6) at this idle boundary, then idle until 00:01 CT (hotfix carried fix 11)

## 18. drill - resume drill before 2026-09-12 (R6) - 2026-09-07 13:38 CT
- Orient: rule 4 - the calendar's "once before 2026-09-12" duty, taken at the first idle unit boundary with no agent running (every dispatched agent is idle; the daily ceilings of 4 hotfix units and 6 deploys are reached; no verify, deploy, operate or phase unit is due before 00:01 CT)
- Branch / commits: `main` at the commit of this entry; tree clean
- Result: done (the drill's second half is judged by the fresh session)
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: n/a
- Rulings: (1) The drill runs now rather than at the 00:01 CT boundary because the user is more likely awake to restart in the afternoon; a restart before 00:01 CT loses nothing (the next work is the 00:01 CT hotfix batch) - cost if wrong: the Tuesday quiet-window batch slips to whenever the restart happens. (2) No wakeup is armed: the pass ends with this commit and the fresh session decides from the clock (skill: after a restart assume no wakeup).
- Carried forward: none
- Next: **drill: expecting idle** (with a wakeup for 00:01 CT, then hotfix carried fix 11) if the restart lands before 00:01 CT on 2026-09-08; **hotfix carried fix 11** if it lands after. Report: `docs/superpowers/autopilot/reports/2026-09-07-drill.md`.

## 19. decision - Odds tier confirmed; NFL bare-city fix pulled forward - 2026-09-07 13:29 CT
- Orient: n/a (user decision in chat, before the drill restart)
- Branch / commits: `main` (docs only)
- Result: done
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: n/a
- Decision (user, verbatim, 13:29 CT): "Odds tier confirmed, and pull the NFL bare-city fix forward"
- Rulings: (1) "Odds tier confirmed" is the U1 confirmation the roadmap's Carried fixes item 10 waits for: the loop flips `odds_monthly_credits` 100000 -> 5000000 and the alternates cadence 900 s -> 120 s inside 36 h of kickoff (featured cadence unchanged; the 80 % alarm and the verify.md credits floor of 1,000,000 follow the tier) as a hotfix unit; the unit's verification includes the next odds tick's `odds_remaining` reading near the new quota - cost if wrong: the flip lands on the old tier and the 80 % alarm fires early, reversible by reverting the settings commit. (2) "pull the NFL bare-city fix forward" moves roadmap phase 6 item 5 (I9 bare-city aliases: `Los Angeles` and `New York` each name two teams; 178 unmatched NFL markets, 23 %) into a hotfix unit before phase 3, added to Carried fixes as item 12; the phase 6 list is the user's text and is left for the user to edit - cost if wrong: one hotfix unit of matching code before phase 3. (3) Daily ceilings stand, so the batch order becomes: Tue 2026-09-08 from 00:01 CT items 10, 12, 11, 4; Wed 2026-09-09 items 5, 6, 7, 8; Thu 2026-09-10 item 9 in the quiet window, then phase 3 (deploy after Thursday's NFL window, i.e. Friday morning at the latest, in time for the Sunday slate) - cost if wrong: phase 3's paper orders start a day later than the previous schedule.
- Carried forward: Carried fixes item 12 (bare-city matching); item 10 unblocked
- Next: the pending drill restart (entry 18 stands): expecting **idle** before 00:01 CT, **hotfix carried fix 10** after

## 20. decision - ceilings raised; proceed in this session - 2026-09-07 13:52 CT
- Orient: n/a (user decision in chat)
- Branch / commits: `main` (skill and docs)
- Result: done
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: n/a
- Decision (user, verbatim, 13:52 CT): "Raise the hotfix cap to 10 and deploys to 15, then proceed". Earlier in the same exchange (13:47 CT): "Ok, I upgraded to the 5M plan. I would like to to get the system fully developed before the first game today. What is left to get done?" and the controller's answer that phase 3 cannot land before tonight's 18:15 CT window and that the ceilings were the binding constraint.
- Rulings: (1) The Ceilings row "Per calendar day" in `.claude/skills/autopilot/SKILL.md` is edited from "4 hotfix units, 6 deploys" to "10 hotfix units, 15 deploys" by the controller at the user's explicit instruction; the loop's ban on editing the skill guards against self-authorisation, and a verbatim user instruction to make one named edit is the user's authority exercised through the controller, recorded here - cost if wrong: none the user did not choose; the reversal is the same one-line edit. (2) "then proceed": work continues in this session; the R6 resume drill (entry 18) is deferred to the next boundary at which no deploy is possible, the 18:15 CT game window, and is re-notified then - cost if wrong: if the user does not restart before bed, the post-game and quiet-window units slip to the morning. (3) Today's remaining plan, in order, subject to the 18:15 CT window: carried fixes 10 (only once the API shows the 5M quota), 12, 11, 8, 4, 5, 6, 7; 9 in the 01:00-08:00 CT quiet window; then phase 3 - cost if wrong: none beyond wall clock.
- Carried forward: none
- Next: hotfix carried fix 10 or 12 (the 13:50 CT tick's `odds_remaining` decides which runs first)

## 21. hotfix - fix-odds-tier (carried fix 10, U1) - 2026-09-07 14:20 CT
- Orient: rule 1 - roadmap Carried fixes item 10, unblocked by entry 19; run 2051 at 18:51:23Z read `odds_remaining` 4,999,992 (the 5M quota live), which the brief's precondition required
- Branch / commits: `fix-odds-tier` d8b04a0..fb1a7f8 (c91018e settings `odds_monthly_credits` 5,000,000 and `odds_alternates_interval_s` 120, `alternates_due` on one configurable interval for every event inside 36 h, `compute_health` with `credits_budget`/`credits_low`, a red `low` badge; fb1a7f8 the four health tests take `env_settings`), fast-forward merged to `main`, branch deleted
- Result: done; the fifth hotfix unit of the day under the raised ceiling (entry 20)
- Dispatches: 3 (implementer `sonnet` plus one fix round by SendMessage; reviewer `sonnet`; scoped re-review `haiku`)
- Tests: 293 passed, pristine (controller's full run on fb1a7f8); up from 287
- Review: APPROVED with 2 Importants and 3 Minors; 1 fix round. Important 1 (fixed): four pre-existing health tests called `create_app` without `env_settings` and depended on the gitignored `.env` after `create_app` began reading `Settings()`; no production caller of `create_app` (the container serves `create_dashboard` with a real settings object). Important 2 (journaled as the operator model): the brief's credit reasoning was wrong: `alternates_due` runs on every 30 s heartbeat outside quiet hours, so each event inside 36 h is fetched about every 120 s around the clock at 2 credits a call; corrected estimate 3,780 credits an hour at a 60-event Saturday peak, about 1.93M a month (39 % of the quota), so the tier holds for the season. Minors ledgered: the test name `test_healthz_keys_unchanged` is stale; `ALTERNATES_BUDGET_S` = 40 caps the burn under load; the brief's header omitted the dashboard files its own item 3 names. Re-review APPROVED, no findings
- Deploy: none in this unit (entry 22)
- Verification: not run in this unit (entry 22)
- Rulings: (1) The implementer's threading of `credits_budget` through `harness/dashboard/app.py` and the extended key assertion in `tests/test_dashboard.py` are accepted: the brief's item 3 names the dashboard as a caller and the suite must stay pristine - cost if wrong: none. (2) verify.md's "Odds credits per day 500-3,000" band was written for the 100k tier; under U1 the loop expects roughly 5,000 a day on a weekday and up to 60,000 on a Saturday, journals the excursion as U1-attributable, and does not treat it as an integrity anomaly; the band's amendment is the user's (flagged in the stopped report) or phase 3's last task - cost if wrong: a key leak hides behind the cadence once; the 20 % floor (1,000,000) still catches a leak within days. (3) verify.md's credits floor is read as 1,000,000 from this deploy on, as the contract's own text says "after the U1 upgrade" - cost if wrong: none.
- Carried forward: none (Carried fixes item 10 removed)
- Next: deploy (`main`, seventh of the day), verify the credits row, then **stopped** at the user's request (entry 23)

## 22. deploy+verify - main 1bd1a26 (carried fix 10, U1) - 2026-09-07 14:20 CT
- Orient: rule 1 continuation (the hotfix unit's deploy step); rule 2 true (`/healthz` build 8504871, code diff to `main` non-empty)
- Branch / commits: `main` 8504871..1bd1a26 (d8b04a0 skill ceilings; c91018e, fb1a7f8 code; 1bd1a26 docs)
- Result: done; verification **PASS** on every row the change names
- Dispatches: 0
- Tests: 293 passed, pristine (entry 21)
- Review: n/a
- Deploy: 1bd1a26 at 14:20 CT via `make deploy-nas` (full: `tick.py` is not on the app-only list, but `deploy-nas-app` does not exist before `app-exec` anyway), foreground; preconditions: `main`, clean tree, game window 0/0/0 at 19:19Z, seventh deploy of the day (ceiling 15); containers Up, `app-serve` healthy; stamp verified on `/healthz` (`"build":"1bd1a26"`, `"credits_budget":5000000`, `"credits_low":false`); `app-ws` reconnected at 19:20:56Z
- Verification (the rows the change names, 14:37 CT): Runs since the restart: 2123 (19:30:26Z), 2128 (19:32:56Z), 2133 (19:35:26Z) all `ok`, 2 credits each (the alternates fetch for the one event inside 36 h, FSU vs SMU, now every 120 s on the heartbeat as U1 specifies), `skipped_alternates` 0, one alternates page per run; 2137 `running` at 19:37:26Z; the featured fetch keeps its 15-minute cadence. Credits row on the new floor (1,000,000): `odds_remaining` 4,999,964 at 19:35Z, numeric, decreasing 2 per alternates run; 36 credits in the last hour; `credits_low` false on `/healthz` PASS. Tape continuity: 0 gap rows in the 2 h window PASS. WS 7862 deltas in the last 10 min PASS. Signals 656 candidates and 24,982 rejected in the last 20 min PASS. ERROR lines 0 on app-run, app-serve, app-ws in the 16 min since the restart PASS; degraded sections 0 PASS; `taker_side_missing` invariant 0 PASS; DB size 9888 MB PASS.
- Rulings: (1) The credits-per-day band excursion that U1's cadence produces is journaled as U1-attributable and is not an integrity anomaly (entry 21 ruling 2) - cost if wrong: a key leak hides behind the cadence once; the 20 % floor still catches it within days.
- Carried forward: none
- Next: stopped (entry 23)

## 23. stopped - user request: skill and loop edits - 2026-09-07 14:40 CT
- Orient: n/a (user instruction in chat at 14:07 CT: "I need to make edits to our skill file and loop setup. Whenever there is a safe stopping point, lets stop."; the safe point chosen was the end of the running unit: fix 10 reviewed, merged, deployed and verified)
- Branch / commits: `main` at the commit of this entry; tree clean; no branch open; every agent idle
- Result: gated: user request (not a gate of the loop's own; the loop resumes on the next `/autopilot` and re-reads every state file, including whatever the user edits)
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: n/a
- Rulings: (1) The R6 drill entry 18 stands; the restart after the user's edits is the drill's second half if the fresh session orients to the expected unit (hotfix carried fix 12, or idle inside a game window) with no re-dispatch and no duplicate entry; a fresh session in the FSU-SMU window (18:15-22:30 CT) orients to idle with a wakeup for the window's end - cost if wrong: the drill is repeated once. (2) Nothing is dispatched after this entry; the fix-12 brief and the rest are on disk under `.superpowers/sdd/` (git-ignored) and named in the report - cost if wrong: none.
- Carried forward: none (Carried fixes: 12, 11, 4, 5, 6, 7, 8, 9)
- Next: none until `/autopilot` runs again; then hotfix carried fix 12 (outside the game window). Report: `docs/superpowers/autopilot/reports/2026-09-07-stopped.md`

## 24. decision - loop process revised: batches, parallel worktrees, deterministic verify - 2026-09-07 14:45 CT
- Orient: n/a (user decision in chat; the loop stopped itself at the user's request after verifying deploy 1bd1a26, entries 22-23)
- Branch / commits: `main` ef3fa74 (skill, verify contract, roadmap lines, scripts, Makefile, pyproject, `tick-once --force`, state.md; the old session's 5cdd3bf journal commit landed on top of it) plus this journal commit
- Result: done
- Dispatches: 3 (two read-only `sonnet` planners for the skill dry-run, one `sonnet` reviewer for the `--force` diff)
- Tests: 295 passed, pristine (`make test` on `main`, 32 s; `test_forced_tick_fetches_inside_the_interval` RED on the missing flag then GREEN, plus `test_forced_tick_never_overrides_the_quiet_window` from the review)
- Review: `sonnet` reviewer on the `--force` diff: CHANGES_REQUIRED (2 Importants: a forced tick must not override the planner's quiet-window `interval=None`; the un-forced alternates path needed a stated reason), both fixed with a covering test, scoped re-review APPROVED
- Deploy: none (`main` is ahead of the NAS by the `--force` change; it rides the first batch's deploy)
- Verification: `make preflight` complete; `make verify-summary DEPLOY_SHA=1bd1a26` PASS 8/9 (credits_numeric FAIL = carried fix 11, open); a probe worktree imported its own code and passed the suite on `harness_test_probe_wt`
- Decision (user, verbatim, 14:00 CT): "I'm waiting for a stopping point in the current loop run and I will stop it so we can make the edits." then (14:38 CT): "Loop is stopped, tree is clean, apply the edits with the defaults". The review that led here: the 2026-09-07 session transcripts showed the controller computing 38 of 176 minutes, one hotfix unit per 27-45 minutes on a strictly serial chain, a full Chrome walk per deploy, and count ceilings idling the loop.
- Rulings (defaults the user accepted): (1) controller effort `high`; (2) implementers `sonnet` by default, `opus` only where the plan marks; `opus` reviewers on recorder, execution, pricing, settlement and venue code; the reviewer commits Minors itself; re-review only after an Important or Critical; (3) Chrome walk on the day's first verify, a dashboard diff, or a `verify-summary` FAIL; (4) the two roadmap lines (state.md and evidence outputs loop-editable; one test notification per day) applied at the user's instruction; (5) three concurrent implementers, one worktree and one test database each (`make worktree`, `make test`); (6) carried fixes ship in batches by area and share a deploy per wave; (7) ceilings are failure-based (two failed deploys or a repeated FAIL per day) plus the per-dispatch and wall-clock limits; (8) the Layer 2 last-event query reads by id (the `max(ts)` form took 120 s); (9) `tick-once --force` after each deploy outside quiet hours, at one tick of credits. Skill dry-run: same 90-minute scenario, the old skill planned 1 carried fix serially with 1 deploy and 1 walk; the new skill planned all 8 daytime fixes in 5 batches across 4 worktrees with `make verify-summary` after each deploy and 1 walk (on the dashboard diff); the one-deploy-per-wave rule was added after that run.
- Carried forward: none (the eight open items stand; batches listed in `state.md`)
- Next: fresh session per Kickoff (`/effort high`, `/autopilot`): Orient rule 1, the hotfix batches in parallel worktrees, one deploy per wave (the FSU-SMU window 18:15-22:30 CT blocks deploys; batches keep implementing through it)

## 25. preflight - fresh session under the revised loop - 2026-09-07 14:45 CT
- Orient: n/a (Kickoff: `/effort high`, `/autopilot`; state.md entry-24 position confirmed against `git log` and the NAS stamp)
- Branch / commits: `main` cf169ed, tree clean before the preflight evidence file
- Result: done; `make preflight` line: `paper posture intact`
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none (NAS build 1bd1a26; `main` ahead by the `tick-once --force` change and the loop revision, 10 files; rides the first wave's deploy)
- Verification: preflight evidence `docs/superpowers/autopilot/evidence/2026-09-07-preflight-1442.txt`: clock 14:42 CDT, containers Up (`app-serve` healthy), `/healthz` build 1bd1a26, `/volume1` 69 % used (3.5 T free), mem available 1514 MB, tunnel 200, sleep prevented (caffeinate), test DB Up, secrets mode 600, game window 0/0/0, last real tick 2143 ok 81 s, heartbeat 21 s, WS event age 1 s
- Rulings: (1) Tools (R23): `ScheduleWakeup`, `PushNotification`, `ListAgents`, `CronCreate`, `CronList`, `CronDelete`, `Monitor`, `SendMessage` and the Chrome set all resolved; `tabs_context_mcp` answered (no tab group yet; created on first use) - cost if wrong: none. (2) Test notifications (R3) already sent today at 11:15 CT per state.md; not repeated - cost if wrong: none. (3) The preflight's one dirty file is the evidence file itself, committed with this entry - cost if wrong: none. (4) Batching by files, not by the state.md labels: carried fix 7 edits `ws_sink.py` and `ws.py`, the same files as fixes 4 and 5, so the venue-socket batch is 4+5+7 (`fix-2026-09-07-ws`, `opus` implementer and reviewer: venue adapter and recorder socket path) and the recorder batch is fix 6 alone (`fix-2026-09-07-recorder`: `tick.py`, `public.py`); matching 12 (`fix-2026-09-07-matching`), health 11 (`fix-2026-09-07-health`), logging 8 (`fix-2026-09-07-logging`) and compose 9 (`fix-2026-09-07-compose`, merged only for the quiet-window deploy) are single-item batches - cost if wrong: one batch takes longer than two would have. (5) Wave plan against the FSU-SMU window (18:15-22:30 CT): wave 1 = ws, matching, health dispatched now (three implementers, the ceiling); logging and recorder start as slots free; every branch clean by about 17:30 CT merges and deploys once before 18:15 CT; the rest deploy after 22:30 CT; compose 9 deploys inside 01:00-08:00 CT - cost if wrong: a batch misses the pre-game deploy and lands after 22:30 CT.
- Carried forward: none (open: 12, 11, 4, 5, 6, 7, 8, 9)
- Next: hotfix wave 1 (ws, matching, health) in parallel worktrees

## 26. hotfix - wave 1: seven carried fixes in five batches - 2026-09-07 15:40 CT
- Orient: rule 1 - roadmap Carried fixes 12, 11, 4, 5, 6, 7, 8 open (entry 25's batch plan); fix 9 implemented and reviewed but held for the quiet window
- Branch / commits: `main` 9554163..dfca429: `fix-2026-09-07-health` 4dcd2cc (fix 11); `fix-2026-09-07-logging` 53ad9c5 (fix 8); `fix-2026-09-07-ws` 717d25a, ee16d1b, 59c47eb, a737b7f, b31d9a6 (fixes 4, 5, 7); `fix-2026-09-07-matching` 3 commits to e454d09 (fix 12); `fix-2026-09-07-recorder` 7 commits to dfca429 (fix 6); `fix-2026-09-07-compose` b50a18b (fix 9, reviewed APPROVED, unmerged, worktree kept)
- Result: done (deploy pending, entry 27)
- Dispatches: 17 (impl 6: ws opus, matching/health/logging/recorder/compose sonnet; review 6: ws and recorder opus, others sonnet; re-review 3: ws haiku, matching and recorder sonnet; fix rounds by SendMessage 3; report tails 2 not counted)
- Tests: 327 passed on `main` (pristine; up from 295); per branch 298, 301, 305, 303, 300, 302
- Review: health APPROVED after a ruling (1 Important accepted: `last` ordered by id, equivalent on the append-only table); logging clean; ws APPROVED, 1 Minor fixed by the reviewer, 1 latent Minor promoted to a fix round (gap_sids cleared on reconnect), haiku re-review's guard finding dismissed (the sibling call is unguarded); matching APPROVED with 1 Important fixed in a round (unique code split required), re-review clean; recorder CHANGES_REQUIRED (settled pages normalized: last_seen_at refreshed, match_reason clobbered, post-settlement quotes in the gap window), fixed in one round (skip settled rows in `_handle`), 4 Minors by the reviewer, re-review clean; compose clean
- Deploy: none in this entry
- Verification: not run in this entry
- Rulings: (1) batches by files touched, not labels: fix 7 joined the ws batch (entry 25). (2) health's `last` ordering by id accepted (reviewer confirmed equivalence; the brief allowed id ordering for the same reason). (3) ws latent Minor promoted to a fix round instead of parking (a real hole once more channels check sequences). (4) haiku's `is not None` guard finding dismissed: the pre-existing reset_sequences call is unguarded; the guard wording was the controller's. (5) matching Important sent to a fix round: a silent 1.00-confidence wrong match is a tape-integrity risk. (6) matching's `ambiguous_candidates` reads Team fields, not alias rows: accepted, the sentinel destroys the colliding ids; manual sources never hit it. (7) recorder fix placed in `_handle` (row consumed, watermark advances, no JSONB predicate in the family query); the normalized count includes settled no-ops (a processed count by construction). (8) recorder's settled fetch outside the budget guard: ledgered, matches the sibling fetches, worst case self-reports through skipped_* counters. (9) ws deploy risks journaled for verify: one gap re-adds all of `_current` (up to 500 snapshot rows in a burst, one per sid per minute); `delete_markets` briefly empties the sid and an error frame self-heals via reconnect. (10) two review findings on files the briefs did not list (the matching fixture, allowed by the brief's own clause; nothing else) - none out of scope.
- Anomalies: (a) reviewer and implementer running suites on one worktree database at the same time produced deadlocks and drop/create errors twice (health, recorder); the controller now sequences them (no fix round while a review runs on the branch). (b) two controller merges ran inside a worktree cwd and merged nothing until redone from the main checkout; no damage. (c) this venv's pytest 9.1.1 prints no summary line under the repo's `-q` addopts; counts come from dots or `--collect-only`.
- Carried forward: none added; 12, 11, 4, 5, 6, 7, 8 stay open until the wave-1 verify rows pass; 9 waits for the quiet window
- Next: deploy `main` dfca429 via `make deploy-nas` (ws_sink.py, ws.py changed; game window 0/0/0 at 15:25 CT), then verify

## 27. deploy - main e370d45 (hotfix wave 1: carried fixes 12, 11, 4, 5, 6, 7, 8) - 2026-09-07 15:40 CT
- Orient: rule 2 - `/healthz` build 1bd1a26, `git diff --stat 1bd1a26..main -- . ':!docs' ':!*.md'` non-empty (the loop revision, `tick-once --force`, five merged batches)
- Branch / commits: `main` 1bd1a26..e370d45 (code dfca429; e370d45 docs)
- Result: done
- Dispatches: 0
- Tests: 327 passed on `main`, pristine (entry 26)
- Review: n/a
- Deploy: e370d45 at 15:39:37-15:40:41 CT via `make deploy-nas` (full: `ws_sink.py` and `ws.py` changed; `deploy-nas-app` does not exist before `app-exec`), foreground, exit 0, no seed-teams warning (log: `evidence/2026-09-07-deploy-1539-e370d45.log`); preconditions: `main`, clean tree, game window 0/0/0 at 15:39 CT, first deploy of this session (eighth of the day); containers Up, `app-serve` healthy within 2 min; stamp verified on `/healthz` (`"build":"e370d45"`); `app-ws` reconnected at 20:40:47Z, 96 fresh snapshots, gap rows 0; forced tick run 2251 at 15:41 CT: ok, n=115, 6 credits, 0 errors
- Verification: entry 28
- Rulings: (1) The forced tick overlapped the recorder container's own first tick after the restart (run 2250 at 20:41:12Z), so both fetched the hourly settled pages (10 raw rows each) before either had written `source_state`; a controller-timing artefact of running `tick-once --force` inside the first minute after a restart, 12 extra Kalshi requests, no credits - cost if wrong: none; future forced ticks wait for the first heartbeat after a restart.
- Carried forward: none
- Next: verify (entry 28)

## 28. verify - deploy e370d45 (wave 1) - 2026-09-07 15:50 CT
- Orient: rule 3 - no `verify` entry since the `deploy` entry 27
- Branch / commits: n/a
- Result: done; **PASS** on every judged row; two items deferred
- Dispatches: 0 (no walker: `verify-summary` PASS 9/9, no dashboard diff, the day's first verify ran at 11:25 CT)
- Tests: n/a
- Review: n/a
- Deploy: e370d45 (entry 27)
- Verification: PASS (evidence: `evidence/2026-09-07-verify-1542-layer2.txt`, `evidence/2026-09-07-verify-1550-summary.txt`). Layer 1 stamp e370d45. Layer 2: runs ok on cadence with 0 errors, heartbeat 21 s; ERROR lines 0 on every service (10 min); degraded sections 0; tape continuity gap rows 0 since the deploy and 0 in the last 200k events; WS last event age 1 s; Postgres no table over the dead-tuple rule, WAL 192 MB; disk 31 % free (3.5 T; above 30 %, the 25 % gate is 0.7 T away); memory available 1634 MB; credits numeric and decreasing on real ticks only, 4,999,848, 860 used today (inside the 500-3,000 band even under U1 on a weekday); DB size 10,103 MB. Layer 2b invariants: `fair_values.staleness_s < 0` = 0, `taker_side_missing` = 0; gap/snapshot ratio today 0 %. Layer 3 `verify-summary` PASS 9/9 (build, sections, page time 0.6 s, run id, WS age, candidates per variant, kill switch false, credits numeric, data_quality). Rows the fixes name: fix 11 `/healthz` and the summary read 4,999,858 while the newest run was a `skipped` heartbeat PASS (the item-2 FAIL open since 11:25 CT clears); fix 12 `match-report`: NFL 772 markets 100 % matched, 178 rows with reason `pair+date exact (code)` (exactly the former unmatched count), 0 unmatched PASS; fix 7 every snapshot since the restart carries `recorder_offset_ms` (-542 ms on the 20:40Z connect, -844 ms on the 20:46Z connect) PASS; fix 5 the subscription grew from 50 to 96 tickers, equal to the matched markets kicking off inside [-8 h, +72 h] versus 50 inside 24 h PASS; fix 6 settled pages stored (10 raw rows per fetch across the six series, `source_state` keys set, run 2251 `normalize_errors` none) PASS on presence; fix 8 no live row (covering tests, invariant 4 untouched) PASS by test; fix 4 no live gap has occurred, the reconnect path produced fresh snapshots and 0 gap rows PASS on the covering test. Deferred: fix 6 "once per hour" (judge after 16:45 CT: exactly one run fetches settled in the 21:41Z hour); fix 4's live recovery is read from the `app-ws` log when the first `gap` row appears (a standing watch, not a FAIL).
- Anomalies: (1) `app-ws` logged one WARNING at 20:46:18Z, `ws loop error: OperationalError(QueryCanceled: statement timeout); reconnecting in 1s`, reconnected at 20:46:36Z with 96 fresh snapshots and 0 gap rows. The controller's own Layer 2 addendum query was scanning the whole day's `orderbook_events` (19.6 M deltas) at that moment, which starved the sink's insert past its statement timeout. Self-inflicted: verification queries on `orderbook_events` are id-bounded from now on (the `max(id) - N` form), never a day scan; the gap/snapshot band is judged from the id-bounded window. Not carried (no code defect); the 18 s tape hole is inside the WS's own reconnect budget. (2) The forced tick overlapped the container's first tick (entry 27 ruling 1).
- Rulings: (1) Carried fixes 12, 11, 4, 5, 7, 8 removed from the roadmap (rows passed); 6 stays until the once-per-hour check at 16:45 CT; 9 stays until the quiet-window deploy. (2) The credits-per-day band excursion rule of entry 21 stands; today's 860 is inside the band anyway.
- Carried forward: none
- Next: operate (daily line, entry 30), then idle; wakeup 16:45 CT for the fix-6 hourly check; 19:15 CT for a game-window observation (first live game under the widened subscription and the gap recovery); 01:05 CT for the compose deploy (fix 9) and then phase 3 (entry 20 ruling 3: "9 in the quiet window, then phase 3"; R19 puts every carried fix before the phase 3 branch)

## 29. drill - resume drill second half (R6) - 2026-09-07 15:52 CT
- Orient: n/a (judged by this session per entry 23 ruling 1)
- Branch / commits: n/a
- Result: done. This fresh session (Kickoff: `/effort high`, `/autopilot`) read state.md and the journal's last entries, ran `make preflight`, and oriented to **hotfix carried fix 12** in the first wave with no re-dispatch of any earlier agent and no duplicate journal entry (entry 25 is new; entries 22-24 were not re-recorded). That is the expected unit named in entry 23 ruling 1 (fix 12, outside a game window).
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: n/a
- Rulings: (1) The R6 drill is complete (first half entry 18 at 13:38 CT, second half here); the calendar row "Once, before 2026-09-12" is satisfied; the mid-phase drill "once more mid-phase between two tasks" (skill Unit: operate) remains for phase 3 - cost if wrong: one more restart during phase 3.
- Carried forward: none
- Next: entry 30

## 30. operate - daily watch (Monday 2026-09-07, written at the wave-1 boundary) - 2026-09-07 15:52 CT
- Orient: rule 4 - Daily 09:00 CT line not yet journaled today (the morning's entries covered the alias pass, the bundle at 11:23 CT and the weekly-report n/a ruling)
- Branch / commits: n/a
- Result: done
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: `/volume1` 69 % used, 3.5 T free (31 %; gate below 25 %); `free -m` available 1634 MB (R17; 328 MB free, swap 4.3 G used of 10 G, unchanged pattern); database 10,103 MB (about 2.7 GB/day at the current tape rate; the 2 TB ceiling of U3 is years away, the 800 GB verify bound about 290 days; partitioning lands with phase 3 Task 2b); Odds credits 4,999,848 remaining, 860 used today (band 500-3,000; U1 raises Saturdays); Anthropic spend n/a before phase 5; executor heartbeat n/a before phase 3; ERROR messages none in the last 10 min on any service (the one WARNING is entry 28 anomaly 1); kill switch observed inactive; `secrets/backup_age_key` does not exist yet (no nag)
- Rulings: none
- Carried forward: none
- Next: idle; wakeup 16:45 CT (fix-6 hourly check)

## 31. decision - phase 3 starts now, not after the quiet window - 2026-09-07 15:54 CT
- Orient: n/a (user decision in chat)
- Branch / commits: `main` (docs only)
- Result: done
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: n/a
- Decision (user, verbatim, 2026-09-07 15:54 CT): "Start phase 3 now instead of waiting for the quiet window"
- Rulings: (1) The phase 3 branch is created now; R19's "carried fixes before the phase 3 branch" is set aside for fix 9 by this decision (the user's text, the user's call). Fix 9 stays on its reviewed branch and still deploys inside 01:00-08:00 CT as its own hotfix unit; it is never merged into `main` before then, so no mid-phase deploy carries it into a game window. Fix 6's hourly check (16:45 CT) is folded into the phase's next task boundary - cost if wrong: none beyond the ordering the user chose. (2) The compose worktree stays alive through the phase (one of the three implementer slots is not consumed by it; it has no running agent).
- Carried forward: none
- Next: phase 3 (Unit: phase)

## 32. phase start - phase 3 paper execution - 2026-09-07 15:57 CT
- Orient: rule 5 after the user's decision (entry 31) - roadmap phase 3 status `planned`, gate none
- Branch / commits: `phase3-paper-execution` from `main` ef44385; plan `docs/superpowers/plans/2026-09-07-phase3-paper-execution.md` (revision 2), spec addendum `docs/superpowers/specs/2026-09-07-phase3-paper-execution-design.md`; SDD workspace `.superpowers/sdd/2026-09-07-phase3-paper-execution/` (ledger `progress.md`)
- Result: started; 17 tasks (1, 2, 2b, 3, 3b, 4, 4b, 5-14); three mid-phase deploys (after T1; after T2b in the quiet window, U3; after T4b before 2026-09-16)
- Dispatches: 1 so far (T1 implementer, sonnet)
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: n/a
- Wave map: W1 T1; W2 T2; W3 T2b+T3+T4b; W4 T3b, T4, T5; W5 T6, T7, T8, T9; W6 T10+T12, T11, T13, T14 (ledger has the file-sharing table)
- Rulings (pre-flight, exhaustive in the ledger): (1) T1 creates the cadence settings at U1's values (near 120, far 120, window 36, monthly 5,000,000) and keeps `odds_alternates_interval_s` until T3b wires the new names; the plan's pre-flip numbers are stale. (2) Carried fix 9's compose branch merges into the phase branch right before T2b so the Postgres restart rides only the quiet-window deploy. (3) Shipped hotfix names win over the plan's T3b names (map in the ledger); T3b's scope is the unshipped items plus the unconditional subscription priority. (4) T4b's `MAX_SECONDARY = 6` and the new `sharp_two_sided.yaml` are authorized by U2 and the roadmap's phase 3 row (Amendment 3); the six frozen ids are untouched; listed under "Decisions you may want to reverse" in the phase report. (5) T3 is ready after T2 (its dependency on T2b's indexes is performance only; the review checks query shapes). (6) The 16:45 CT fix-6 check and the 19:15 CT game-window observation fold into task boundaries.
- Carried forward: none
- Next: T1 review, then the T1 mid-phase deploy if a window is open (before 18:10 CT or after 22:30 CT)

## 33. deploy - main 358c808 FAILED (Task 1 mid-phase deploy) - 2026-09-07 16:25 CT
- Orient: plan Task 1 step 5 (controller); rule 2 true (`main` fast-forwarded to the phase branch at 358c808; code diff vs e370d45 = Task 1)
- Branch / commits: `main` e370d45..358c808 (Task 1 38fa3d2, 4077bc6; docs)
- Result: **FAIL** (first failed deploy of the day; ceiling 2): `make deploy-nas` exit 2 at 16:24:13 CT in the migrate step; `init-db` raised `OperationalError: (psycopg.errors.DeadlockDetected) deadlock detected` on `create index if not exists ix_obe_ts_brin on orderbook_events using brin (ts)` (log: `evidence/2026-09-07-deploy-1624-358c808.log`). The deploy aborted before `docker compose up -d`; the old containers stayed Up on e370d45, `app-serve` healthy, `/healthz` build e370d45 at 16:25 CT. No data lost.
- Cause (systematic-debugging, inline): `create_schema` (harness/db/schema.py:43) runs every DDL statement inside one `engine.begin()` transaction. `alter table venue_trades add column if not exists ...` (lines 46-47) takes an AccessExclusiveLock on `venue_trades` even when the column exists, held to commit; `create index if not exists ix_obe_ts_brin` (line 61) then takes ShareLock on `orderbook_events` and waits for the WebSocket sink's open batch (RowExclusive); the sink's same batch next inserts into `venue_trades` and waits on init-db: a deadlock, resolved by Postgres against init-db. Latent since the venue_trades ALTER shipped at 12:18 CT; the three deploys since won the race by timing. The deploy recipe runs `init-db` while the old `app-ws` and `app-run` are still writing.
- Dispatches: 0
- Tests: 335 passed on `main`, pristine
- Review: n/a
- Deploy: failed (above)
- Verification: not run
- Rulings: (1) Retry once, now, with `app-ws` and `app-run` stopped over ssh before `make deploy-nas` (the recipe's final `up -d` restarts them): deterministic, costs about two minutes of quiet-hour tape instead of the ten seconds a normal deploy loses; a second failure would hit the daily ceiling - cost if wrong: two minutes of WebSocket tape outside any game. (2) Carried fix 13 (new): `create_schema` must not hold DDL locks across statements that wait on the tape writers: each ALTER/INDEX statement in its own autocommit transaction with a `lock_timeout` and one retry, ALTERs on the tape tables last; since phase 3 Task 2 rewrites `create_schema` (its Files include schema.py and it adds the index loop), the fix ships inside Task 2 with a covering test rather than as a separate hotfix; the roadmap row records it - cost if wrong: one more deadlocked deploy before Task 2 lands (mitigated by ruling 1's stop-first procedure, used for every deploy until then). (3) Every deploy until fix 13 ships stops `app-ws` and `app-run` first (same reason) - cost if wrong: two minutes of tape per deploy.
- Carried forward: Carried fixes item 13 (schema DDL locking; ships in phase 3 Task 2)
- Next: retry the deploy (entry 34)

## 34. deploy+verify - main 659ba67 (Task 1 mid-phase deploy, retry) - 2026-09-07 16:30 CT
- Orient: plan Task 1 step 5 after entry 33's journaled cause and ruling 1
- Branch / commits: `main` 358c808..659ba67 (docs: entry 33, carried fix 13); code = Task 1 (38fa3d2, 4077bc6)
- Result: done; verification **PASS**
- Dispatches: 0
- Tests: 335 passed on `main`, pristine (entry 33)
- Review: n/a
- Deploy: 659ba67 at 16:26:23-16:26:48 CT via `make deploy-nas` with `app-ws` and `app-run` stopped first at 16:25:58 CT (entry 33 ruling 1; about 50 s of quiet-hour tape lost, no game within 4 h), exit 0, no seed-teams warning (log `evidence/2026-09-07-deploy-1626-659ba67.log`); game window 0/0 at 16:25 CT; second deploy attempt of the day, first success after the failure (failed deploys today: 1); containers Up, `app-serve` healthy inside 1 min; stamp verified (`"build":"659ba67"`); `app-ws` reconnected 21:26:50Z with fresh snapshots, gap rows 0; forced tick at 16:28 CT (run 2322: ok, n=120, 6 credits, 0 errors), run after the first heartbeat as entry 27 ruling 1 requires
- Verification: PASS (evidence: `evidence/2026-09-07-verify-1631-summary.txt`, `evidence/2026-09-07-verify-1631-layer2.txt`). Layer 3 `verify-summary` 9/9 (page time `/api/summary` 8.1 s, under the 10 s bound but noted). Layer 2 light: ERROR lines 0 on every service, degraded sections 0, gap rows 0 in the last 200k events, WS age 2 s, heartbeat ok, invariants 0/0. Task 1 rows: candidates > 0 on the first priced run after the deploy (run 2322: 553 candidates across the five active variants, 317 for 49af716f8708) PASS; `fair_values` on run 2322 carry `feed_kind = featured` (2626 rows), `stale_allowance_s = 220`, `feed_lag_s = 38`, `pricing_version = 2.1` PASS; Amendment 2 appended to `docs/superpowers/reviews/2026-09-07-phase2-preregistration.md` (deploy, run range from 2321, tables, criterion 8, re-scoring command, frozen executor settings, cadence settings, the taker-side range 1-1881).
- Anomalies: (1) 7 of 2633 direct `fair_values` rows on run 2322 have `feed_kind` NULL (so allowance NULL and the old 180 s rule applies to them: safe); likely a consensus whose newest timestamp matched no sharp-book line in `_feed_info`; ledgered as a deferred minor for the final review, re-checked at the next verify. (2) The plan's Task 1 text and addendum §6 still list the pre-U1 cadence numbers; Amendment 2 records the values in force.
- Rulings: (1) The deploy-with-writers-stopped procedure stands for every deploy until carried fix 13 ships in Task 2 (entry 33 ruling 3).
- Carried forward: none new (13 open, ships with Task 2; 6 deferred to the 16:45 CT check; 9 merges before Task 2b)
- Next: Task 2 review when its implementer reports; fix-6 hourly check at the next boundary after 16:42 CT

## 35. phase - phase 3 progress: Tasks 2 and 4b merged, 2b and 3 in review - 2026-09-07 17:19 CT
- Orient: rule 5 continuation (entry 32)
- Branch / commits: `phase3-paper-execution` 659ba67..dcfc7cb (compose fix 9 59a6737; Task 2 b3f834f; Task 4b dcfc7cb; docs)
- Result: in progress (17 tasks: 1, 2, 4b complete; 2b, 3 under review; 3b, 4, 5-14 queued)
- Dispatches: 15 so far in the phase (impl 6, review 6, re-review 3)
- Tests: 383 on the phase branch at Task 4b's base, pristine
- Review: Task 2 one fix round (replay filter on order_episodes, init-db batch timeout, five minors); Task 4b clean with one plan-mandated Important parked; Task 3 fix round 1 in re-review; Task 2b in review
- Deploy: none since 659ba67
- Verification: n/a
- Rulings (all in the ledger): (1) Task 2's reviewer on opus (schema underpins every task). (2) order_episodes filters replay rows (spec's non-replay rule over the plan's view text). (3) Task 2b's migration must drop the renamed legacy primary key before ATTACH PARTITION (the plan's rename step was defective: Postgres refuses two primary keys); accepted as part of the U3 migration on the condition that a concurrently built unique index on the legacy key columns precedes the drop; addendum D15 already anticipated the PK change to (id, ts) and asks for the user's objection window before Step 5: the user was notified at 17:20 CT; the live run is the 01:00-08:00 CT quiet window; reversal: do not run `partition-bulk-tables` (the code path is inert until invoked). (4) Task 3's REST-anchored books get a separate gap-check id and a "clean snapshot" reload trigger; a backwards delta timestamp dirties a REST book (the brief's no-seq-check rule stands). (5) Task 4b: MAX_SECONDARY 6 and `sharp_two_sided.yaml` (id 5632da729fa7) authorized by U2; the six frozen ids re-verified from disk; the two-dedupe-key economic position (NO on A equals YES on B) is parked as plan-mandated and goes into Amendment 3's text. (6) A forced tick fetches every family by design; runs 2251 and 2322 are not evidence for fix 6 (entry 27 corrected); judge after 17:45 CT.
- Anomalies: none new
- Carried forward: none (13 ships with Task 2, already merged on the branch: create_schema is autocommit per statement with lock_timeout and a retry; deploys still stop the writers first until it lands on the NAS)
- Next: Task 2b and 3 merges, then Task 3b (sonnet) and Task 4 (opus); fix-6 check at 17:43 CT; game-window observation 19:15 CT; Task 2b deploy plus `partition-bulk-tables` in the quiet window

## 36. verify - carried fix 6 hourly check (deferred from entry 28) - 2026-09-07 17:39 CT
- Orient: rule 3 - a deferred item's judge-after time (17:45 CT, corrected in state.md from the forced-tick semantics) has passed; folded into the phase's task boundary
- Branch / commits: n/a
- Result: done; **PASS**
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: PASS. `source_state` for every `kalshi_settled:<series>` key moved from 21:28:01Z (the forced tick 2322) to 22:28:49Z; exactly one non-forced run, 2417 at 22:28:49Z, stored the settled pages (10 raw rows across the six series) and the eleven real ticks around it (2405-2434) stored none; 0 errors on those runs. The once-per-hour rule holds on the recorder's own cadence.
- Rulings: (1) Carried fix 6 removed from the roadmap. (2) Entry 27 ruling 1's cause is corrected by entry 35 ruling 6: a forced tick fetches every family by design (tick.py line 56); the forced-tick double fetch after a deploy is expected and costs no credits (Kalshi is unmetered).
- Carried forward: none (open: 9 with Task 2b's quiet-window deploy; 13 shipped in Task 2 on the branch, removed when its deploy's verify passes)
- Next: phase 3 continues (Task 4 fix round, Task 3b implementing); game-window observation 19:15 CT; the FSU-SMU window 18:15-22:30 CT blocks deploys
