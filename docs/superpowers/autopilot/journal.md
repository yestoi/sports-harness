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

## 37. decision - stop at a safe point for a fresh session (context) - 2026-09-07 17:59 CT
- Orient: n/a (user instruction in chat)
- Branch / commits: `phase3-paper-execution` (docs only)
- Result: done
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: n/a
- Decision (user, verbatim, 2026-09-07 17:59 CT): "Out context is getting high. Lets find a safe stopping point to start the loop over in a fresh session."
- Rulings: (1) The stopping point is the next task boundary: no new dispatch from now; the loop waits for the two in-flight implementers (Task 3b sonnet, Task 5 opus) to report so their commits are on their branches, then journals `stopped` with the exact resume position and ends the pass; if either has not reported by 18:30 CT the loop stops anyway and the ledger's resume rule 2a (a dirty worktree is the dead implementer's partial work; re-dispatch with "keep what passes its tests, commit") covers it - cost if wrong: one re-dispatched task. (2) The Task 2b quiet-window deploy (01:00-08:00 CT) with `partition-bulk-tables` and the writers stopped is the fresh session's first deploy unit once its Orient reaches it; nothing deploys before the restart.
- Carried forward: none
- Next: stopped (entry 38) once the reports land

## 38. stopped - safe point for a fresh session (user request, entry 37) - 2026-09-07 18:19 CT
- Orient: n/a (entry 37 ruling 1: both in-flight implementers have reported; no review dispatched)
- Branch / commits: `phase3-paper-execution` at ff72562 (ef44385..ff72562: compose fix 9, Tasks 2, 2b, 3, 4, 4b, docs); task branches `phase3-t3b-recorder` 657a5f7 (Task 3b, DONE, 429 tests) and `phase3-t5-plan` 67455a1 (Task 5, DONE, 520 tests) with their worktrees kept; `main` at 659ba67 = the NAS build
- Result: gated: user request (not a gate of the loop's own; the fresh session re-reads every state file)
- Dispatches: this session 40 (day total 63)
- Tests: 462 on the phase branch (Task 4 merge); 429 and 520 on the two task branches
- Review: Task 3b and Task 5 reviews NOT dispatched (entry 37); packages `task-3b-review-ae1e86c..657a5f7.diff` and `task-5-review-070a237..67455a1.diff` are in the SDD workspace
- Deploy: none since 659ba67 (16:26 CT)
- Verification: live at 18:17 CT: `/healthz` ok on 659ba67, credits 4,999,492 (1,218 used today), `/volume1` 3.5 T free (31 %), memory available 1180 MB, database 11 GB, no game in progress yet (FSU-SMU 18:30 CT; window 18:15-22:30 CT)
- Rulings: (1) Carried fixes 9 and 13 are phase-bound (roadmap rows say so): the fresh session's Orient rule 1 does not open a hotfix unit for them; they close with the Task 2b quiet-window deploy's verify. (2) The 19:15 CT game-window observation is dropped; the morning-after full verify (walker) covers the first game under the widened subscription and the gap recovery. (3) The user's objection window on the Task 2b primary-key drop (entry 35 ruling 3) stays open until the fresh session runs the quiet-window deploy; silence is consent per addendum D15.
- Carried forward: none
- Next: fresh session per Kickoff; Orient rule 5 resumes the phase from the ledger: dispatch the Task 3b and Task 5 reviewers (opus), fix rounds, merges, then Task 6 (opus), Tasks 7-9 serial, 10+12 parallel, 11, 13, 14; the Task 2b quiet-window deploy (writers stopped, `partition-bulk-tables`, recovery notes in `task-2b-live-run-recovery.md`) at the first 01:00-08:00 CT boundary; the Task 4b deploy (replay first, Amendment 3, `gate_variant` flip) before 2026-09-16. Report: `docs/superpowers/autopilot/reports/2026-09-07-stopped-1820.md`

## 39. preflight - fresh session (Kickoff) - 2026-09-07 19:01 CT
- Orient: n/a (preflight)
- Branch / commits: `phase3-paper-execution` at de80053 (docs-only above ff72562: the user's U6/U7 roadmap edits, README, canvas sources); `main` 659ba67 = NAS build
- Result: done; paper posture line verbatim: `paper posture intact`
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: preflight evidence `evidence/2026-09-07-preflight-1901.txt`: clock 19:00:48 CDT; git dirty 1 (the evidence file itself); sleep prevented (caffeinate); test DB Up, pytest 0; secrets 7 files 0600, .env.nas present; tunnel 200; NAS containers Up, `app-serve` healthy, `/healthz` ok build 659ba67, credits 4,999,396; `/volume1` 3.5 T free (69 % used); mem available 1068 MB; deployed = main; game window 0|1|0 (FSU-SMU kickoff inside 4 h: window open until about 22:30 CT); last real tick 2558 ok 126 s; heartbeat 2561 skipped 6 s; WS age 0 s
- Tools (R23): ScheduleWakeup, PushNotification, ListAgents, CronCreate/CronList/CronDelete, Monitor, SendMessage and the Chrome set resolved; `tabs_context_mcp` answered (no tab group yet). Test notifications already sent today (11:15 CT), not repeated
- Rulings: (1) Orient rule 1 does not fire: carried fixes 9 and 13 are phase-bound (entry 38 ruling 1). (2) Rule 2: deployed = `main`, no deploy. (3) Rule 3: the last verify (36) postdates the last deploy (34); no wakeup survives a restart; the deferred items (fix 4 gap recovery, 7 null feed_kind rows) fold into the morning-after verify Tue 2026-09-08 after 08:10 CT. (4) Rule 4: the daily watch (30) and the alias pass (11) ran today; nothing due. (5) Rule 5 applies: phase 3 resumes at ledger rule 2a: the Task 3b and Task 5 reviewers are dispatched (opus: recorder/venue paths, execution path). (6) U6: Task 12b enters the wave map after Tasks 6, 7, 10, 11 and 12 and before 13; its brief is written from the plan text at that point. (7) U7: `git push origin main phase3-paper-execution` joins the bundle step after the phase and on Mondays; the loop never pulls.
- Carried forward: none
- Next: phase (Task 3b and Task 5 reviews in flight); the game window blocks deploys until about 22:30 CT; the Task 2b quiet-window deploy at the first boundary after 01:00 CT

## 40. phase - phase 3 progress: Tasks 3b and 5 merged, Task 6 dispatched - 2026-09-07 19:28 CT
- Orient: rule 5 continuation (entry 39 ruling 5)
- Branch / commits: `phase3-paper-execution` 8002270..059cf40 (Task 5 487fd73, c26d394; Task 3b 5687a94, 059cf40)
- Result: in progress (18 tasks with 12b: 1, 2, 2b, 3, 3b, 4, 4b, 5 complete; 6 implementing; 7-14 queued)
- Dispatches: 7 this session (review 2, impl 3, re-review 2); day total 70
- Tests: 540 on the phase branch at 059cf40, pristine
- Review: Task 5 one fix round (a null target now emits `Skip(no_target)` instead of crashing the tick; `rebuild_state` counts each fill once: daily = open orders + fills today, game = open orders + unsettled positions; NO_EDGE shared); Task 3b one fix round (match_key cleared only where game_id is nulled; open-order tickers join the subscription at any horizon; None grid counts as non-linear; ROUND_HALF_UP in compose_match_key; alternates_due defaults mirror settings 120/120/36)
- Deploy: none (game window open)
- Verification: n/a
- Rulings (all in the ledger): (1) Task 5 Important 1 fixed by a skip rule, not by narrowing types. (2) Task 5 Important 2: each stake enters each aggregate once. (3) Task 5 Minors 5 and 6 are Task 6 obligations (cancels applied before places; variant_cfg covers the union of intent and open-order variants with `with_defaults`); Minors 3, 4, 7 deferred. (4) Task 3b Important 2 implemented as the brief's "(any horizon)" union. (5) Task 3b Minors 1, 2, 4, 5, 7 fixed in the round; 3 and 8 deferred. (6) Task 6's Files gain models.py and schema.py for the six additive per-track SimState columns on `orders` (Task 4 ruling). (7) Fresh implementers for both fix rounds at the original tier (the previous session's agents are gone).
- Anomalies: none
- Carried forward: none
- Next: Task 6 review (opus) when it reports; then 7, 8, 9 serial; game window closes about 22:30 CT; Task 2b quiet-window deploy after 01:00 CT

## 41. phase - phase 3 progress: Tasks 6, 7, 8 merged, Task 9 dispatched - 2026-09-07 22:33 CT
- Orient: rule 5 continuation (entry 40)
- Branch / commits: `phase3-paper-execution` 059cf40..0110fcd (Task 6 fe54563, ceb68fd, 30b3b8d; Task 7 5d73017, 5e119f4, 888f604; Task 8 512862b, 8d433dc, 0110fcd; docs)
- Result: in progress (18 tasks: 1, 2, 2b, 3, 3b, 4, 4b, 5, 6, 7, 8 complete; 9 implementing; 10-14 and 12b queued)
- Dispatches: 19 this session (impl 6, review 5, re-review 5, plus 3 fix-round resumes by message); day total 82
- Tests: 628 on the phase branch at 0110fcd, pristine
- Review: Task 6 one fix round (kickoff skip recorded; cursor = what the simulation consumed; within-period gap recovery; nw_done on early returns; once-per-order cross across diverging tracks); Task 7 one fix round (live-only settlement ledger with the status update split out; per-ticker savepoints and a yes/no/tie/void whitelist; degraded status on ctx errors); Task 8 one fix round (Critical: the `result` CLV row could never join; candidacy is now per missing (row, type) pair; per-type target_ts; the drain continues past an unbenchmarkable game; Budget honoured)
- Deploy: none (game window until about 22:30 CT; the quiet-window deploy is next at 01:00 CT)
- Verification: n/a
- Rulings (all in the ledger; the notable ones): (1) Task 6: the intent query drops the kickoff term so `Skip(kickoff)` is recorded; the persisted cursor is the last consumed event id; `dirty_seconds` accepted; restart forfeits one clamp (conservative). (2) Task 7: the brief's "non-replay order" is binding for the ledger; `void` joins the venue whitelist. (3) Task 8: the three `benchmark_type` columns widened to String(32) in the model only (the tables have never existed on the NAS, 0 of 3 at 21:20 CT), never by ALTER TYPE; an implementer's provisional ALTER was reverted before review; novig_devig_t5 is computed from the recorded Odds API novig lines (roadmap phase 5 note). (4) The result-lands-last assumption behind the drain's watermark accepted as structural.
- Anomalies: the Task 8 implementer acted before a ruling reached it (an ALTER COLUMN TYPE, reverted in the next commit; the net diff has none)
- Carried forward: none
- Next: Task 9 review when it reports; then 10 (opus) and 12 (sonnet) in parallel; the Task 2b quiet-window deploy at the first boundary after 01:00 CT (writers stopped, `partition-bulk-tables`, recovery notes)

## 42. phase - phase 3 progress: Tasks 9, 10, 12 merged, Task 11 dispatched - 2026-09-08 00:40 CT
- Orient: rule 5 continuation (entry 41)
- Branch / commits: `phase3-paper-execution` 0110fcd..c317e5e (Task 9 f9210ca, 1be170d; Task 10 3f14623.., c403636; Task 12 96f0e88.., c317e5e; docs)
- Result: in progress (18 tasks: 1-10, 12 complete (12 of 18); 11 implementing; 12b, 13, 14 queued)
- Dispatches: 31 this session (impl 8, review 8, re-review 8, plus fix-round resumes by message); day total 2026-09-07: 94; 2026-09-08 so far: 3
- Tests: 708 on the phase branch at c317e5e, pristine
- Review: Task 9 one fix round (close horizon; no-lookahead quotes; any fair source; as_measured off the stage module; deterministic ties; taker fee on cross_fill); Task 10 one fix round (Critical: a homogeneous stratum counted cells toward §9.6; feed_kind stratum; gap_mid as the family panel; fair_changed adverse drift; CRITERIA_TEXT completed); Task 12 one fix round (24 h windows; replay filters; fee-drift tests; banners; partition roll-up)
- Deploy: none (the quiet-window deploy is next, at the 01:00 CT boundary: `main` to ae1e86c only, ledger ruling 23:36 CT)
- Verification: n/a
- Rulings (all in the ledger; the notable ones): (1) the families A and B, the shrinkage and the §9.6 count run on the gap_mid quantity §9.7 names, over every feed, with feed_kind as a stratum and the maker-net panel beside them; the user ratifies or switches before the Mon 2026-09-21 freeze (phase report: Decisions you may want to reverse). (2) A τ² = 0 stratum or a zero-width posterior interval never counts as significant. (3) Task 10 and Task 12 reviewers chosen outside the path table (opus for the gate's statistics; sonnet for a one-line registry append). (4) `Markout.anchor` widened in the model only (no ALTER; no production table). (5) The quiet-window deploy carries `main` only to ae1e86c (the U3 scope); app-exec rides the phase deploy after the final review.
- Anomalies: the controller's Task 10 merge first ran from a worktree cwd (no-op); redone from the main checkout (the state.md lesson stands)
- Carried forward: none
- Next: Task 11 review when it reports; the quiet-window deploy at the next boundary after 01:00 CT; then 12b (sonnet), 13 (opus), 14 (sonnet)

## 43. deploy - main ae1e86c (Task 2b quiet-window deploy, U3 partition migration) - 2026-09-08 01:30 CT
- Orient: plan Task 2b step 5 (controller, pre-authorized by U3) at the first boundary after 01:00 CT; ledger ruling 2026-09-07 23:36 CT: `main` fast-forwarded only to ae1e86c (compose fix 9, Task 2 with fix 13, Task 3, Task 4b code, Task 2b), never to the branch head, so `app-exec` waits for the phase deploy after the final review
- Branch / commits: `main` 659ba67..ae1e86c (13 commits: 59a6737 compose fix 9; Task 2 b3f834f; Task 4b dcfc7cb; Task 3 0a2c313; Task 2b ae1e86c; docs)
- Result: done (deploy); the migration done; the verify that follows is entry 44
- Dispatches: 0
- Tests: 418 passed on `main` at ae1e86c, pristine (`harness_test_main`)
- Review: n/a
- Deploy: ae1e86c at 01:29:10-01:30:14 CT via `make deploy-nas` with `app-ws` and `app-run` stopped first at 01:28:56 CT (fix 13 ships in this build; the writers-stopped procedure ends here); one refused attempt at 01:29:02 CT (an untracked evidence log dirtied the tree; logs now go to the scratchpad first); log `evidence/2026-09-08-deploy-0129-ae1e86c.log`; containers Up, `app-serve` healthy at 01:31 CT; stamp `"build":"ae1e86c"` verified 01:30:45 CT; Postgres restarted with fix 9 (`shared_buffers` 512MB, `effective_cache_size` 1536MB, `max_wal_size` 4GB); writers stopped again 01:31 CT for `partition-bulk-tables` (log `evidence/2026-09-08-partition-bulk-tables-0131.log`): orderbook_events partitioned in 2107 s (three legacy indexes and the new primary key built concurrently, constraint validated), venue_trades in 19 s; writers restarted 02:07:29 CT; about 66 min of quiet-hour tape not recorded (01:29-02:07 CT, journaled cost of the U3 migration); gap rows 0; the id sequence (29868588) equals the legacy max id; partitions legacy + y2026w37 + y2026w38 on both tables; both legacy check constraints validated; new rows landing in the w37 partitions from 02:08 CT
- Verification: entry 44
- Rulings: (1) The game-window rule read on the evidence for game 114 (ledger 01:14 CT; carried fix 14): absent from ESPN's live scoreboard, no tape on its 50 matched markets for 40 min, kickoff nearly 6 h earlier: the window was closed. (2) No forced tick: quiet hours (rows deferred to the 08:10 CT verify). (3) Carried fixes 9 and 13 close with this deploy: `show shared_buffers` reads the tuned value and `init-db` completed with the autocommit-per-statement `create_schema` (writers were stopped, so the deadlock path was not exercised; the unit test covers it).
- Carried forward: 14 (ESPN midnight-Eastern rollover leaves a game in_progress forever; ships on the phase branch after Task 12b, before Task 13)
- Next: verify (entry 44)

## 44. verify - deploy ae1e86c - 2026-09-08 02:36 CT
- Orient: rule 3 - no verify entry since the deploy (43)
- Branch / commits: n/a
- Result: **FAIL** (one row: dashboard page time), otherwise PASS
- Dispatches: 0 (no walker: quiet hours, the day's first verify with the walker is the morning-after duty after 08:10 CT)
- Tests: n/a
- Review: n/a
- Deploy: none (entry 43)
- Verification: Layer 1 PASS (stamp ae1e86c, status ok). Layer 2 (evidence: `evidence/2026-09-08-deploy-verify-0212-layer2.txt`): containers Up and `app-serve` healthy; heartbeat rows inside 2 min; cadence run 07:08Z after the restart; ERROR lines 0 on every service; gap rows 0; WS age 0.1 s; Postgres health ok (no dead-tuple table over 20 %); WAL 1.6 GB; disk 3.5 T free (31 %); memory available 1321 MB; degraded sections 0 at 02:13 CT (four `funnel`/`data_quality` OperationalError warnings 02:16-02:20 CT, none in the ten minutes to 02:33 CT); credits 4,998,958; DB size 20 GB; `runs.build_sha` NULL (the column is written from Task 12b: not a FAIL; note). Layer 2b (evidence: `evidence/2026-09-08-deploy-verify-0210-layer2b.txt`): 9/9 invariants zero; the three bulk tables listed in `pg_partitioned_table`; duplicate trades 0; duplicate ids 0. Layer 3: **FAIL**: `/api/summary` 92 s at 02:20 CT and 86 s at 02:33 CT against the 10 s bound; `verify-summary` cannot fetch the page inside its 30 s curl (no evidence file); the transient re-check at 02:33 CT (ten minutes after the first FAIL) still fails: not transient. Cause (measured 02:36 CT): the funnel's 24 h counts scan the pricing tables (fair_values 7.5 s, market_gap_snapshots 13.5 s, signals 25.7 s per variant) on a cache flushed by the Postgres restart and the 30-minute index build; the partitioned tables answer in about a second (count 5 m 1.3 s, last event by id 0.2 s), so the migration is not the cause and this is not a failed deploy for the day's ceiling.
- Anomalies: (1) `runs.build_sha` NULL on every row (dormant column until Task 12b). (2) Game 114 stays `in_progress` until fix 14 lands.
- Rulings: (1) Carried fix 15 (roadmap) widened: every funnel count that scans a pricing table comes from `runs.notes` (the funnel's `raw_responses` and `venue_markets` counts stay); it ships on the phase branch with fix 14 after Task 12b merges (app.py is in flight there), before Task 13, per the hotfix rule that carried fixes touching in-flight files ride the branch. (2) The verify FAIL's hotfix unit is therefore the phase-branch fix batch, not a `fix-` branch from `main`; re-verify of the page-time row happens after the phase deploy - cost if wrong: the dashboard answers in about 90 s until the phase deploy (days, not weeks).
- Carried forward: 15 (dashboard funnel scans; widened)
- Next: phase (Task 12b fix round in flight); the fix batch (14, 15) after 12b merges; Task 13; Task 14; the morning-after verify after 08:10 CT

## 45. phase - phase 3: all 18 tasks merged, final review dispatched - 2026-09-08 04:05 CT
- Orient: rule 5 continuation (entry 42)
- Branch / commits: `phase3-paper-execution` c317e5e..4570acc (Task 11 a0e3643; Task 12b a677a24; fix batch 14+15 131c092; Task 13 9706d42; Task 14 4570acc; docs)
- Result: in progress (18/18 tasks complete plus the fix batch; final whole-branch review in flight)
- Dispatches: 52 this session (impl 12 incl. the batch, review 13, re-review 12, plus fix-round resumes by message); 2026-09-08 so far: 24
- Tests: 809 on the phase branch at 4570acc, pristine
- Review: Task 11 one fix round (unique gate rows; the difference-in-means cluster-robust SE; NULL prices dropped; hash over definitions only); Task 12b one fix round (Critical: telemetry writers could roll back a step; savepoints; one mtm_coverage convention; 19 checks); fix batch one round (dated body kept out of the latest-body fallback; uncapped 24 h notes); Task 13 one fix round (replay's book is the live book bounded at the instant; advanced not rebuilt; a failing step exits 1; the R14 fixture covers deltas and a REST anchor); Task 14 clean
- Deploy: none since ae1e86c (entry 43)
- Verification: n/a (entry 44 FAIL on page time stands until the phase deploy re-verifies with fix 15)
- Rulings (the notable ones; all in the ledger): (1) CHECKS covers every Layer 2b statement in verify.md (19), not the brief's eight examples. (2) The criteria hash stays over the sorted definition strings (my threshold-fold ruling withdrawn). (3) The dated ESPN body never feeds today's latest-body fallback. (4) Replay's book follows the live rules bounded at the instant; a replay step that fails exits 1. (5) The quiet-window deploy's page-time FAIL is closed by fix 15 at the phase deploy, not by a `fix-` branch from `main`. (6) Task 14 merged last per plan order after its clean review.
- Audit 3a (verbatim outputs): variants diff empty; pyproject diff empty; hosts: `harness/venues/kalshi/ws.py:22:FALLBACK_URL = "wss://external-api-ws.kalshi.com/"` (on main since 7b27916, phase 1); DDL grep: 14 prose/test/helper lines (`truncate_ms`, docstrings, test names), no statement
- Anomalies: ledger timestamps ran up to 20 min ahead of the clock during the night (entries 42-44 carry the clock time; the ledger's own stamps are approximate)
- Carried forward: none (14 and 15 closed on the branch)
- Next: final review verdict -> fix wave if needed -> archive, merge to main, phase deploy (adds app-exec; quiet hours: no forced tick), verify, phase report, bundle and push (U7); then Task 4b step 5 (replay, `variants register`, Amendment 3 with the gap_mid note, `gate_variant` flip) before 2026-09-16

## 46. phase done - phase 3 paper execution merged to main - 2026-09-08 04:55 CT
- Orient: rule 5 - final review clean after one fix wave (entry 45's next step)
- Branch / commits: `main` ae1e86c..94d8b44 (the phase branch `phase3-paper-execution`, 18 tasks plus fix batch 14+15 and the final fix wave; 74 code and docs commits)
- Result: done
- Dispatches: phase total 57 across two sessions (this session 55: impl 14 incl. the batch and the fix wave, review 15, re-review 14, plus fix-round resumes by message); 2026-09-08 so far: 29
- Tests: 821 passed on the branch at 6df2b7d, pristine (`main` suite runs next, before the deploy)
- Review: final review (opus) no Critical, seven Importants fixed in one wave (27 checks; bounded tape-head and max-id lookups; `result` excluded from benchmark eligibility; runbook export-fixture; report_wtd every six hours; one book per order in markouts) plus two Minors; re-review clean; 19 deferred Minors triaged as can-wait (archived review), phase 6's list
- Deploy: next (entry 47): the phase deploy adds `app-exec`; `partition-bulk-tables` a no-op; quiet hours: no forced tick
- Verification: next
- Rulings: exhaustive list = the 94 `Ruling:` lines of the archived ledger `docs/superpowers/reviews/2026-09-08-phase3-sdd-ledger.md` (this session's are journaled in entries 39-45). The ones a reader should know: (1) families A and B, the shrinkage and the §9.6 count run on the gap_mid quantity §9.7 names, over every feed, feed_kind as a stratum, the maker-net panel beside them (user ratifies or switches before the Mon 2026-09-21 freeze); (2) a τ²=0 stratum or a zero-width interval never counts as significant; (3) the criteria hash is over the sorted definition strings; (4) the three benchmark_type columns and Markout.anchor widened in the model only, never by ALTER (no production table existed); (5) novig_devig_t5 from the recorded Odds API novig lines; (6) `void` in the venue result whitelist; (7) the executor records `Skip(kickoff)` (the intent query drops the kickoff term); (8) the persisted cursor is what the simulation consumed; (9) telemetry writers under savepoints, never a decision, never in replay; (10) replay's book is the live book bounded at the instant, advanced not rebuilt, a failing step exits 1; (11) CHECKS mirrors verify.md's 27 Layer 2b statements; (12) report_wtd every six hours (the U6 spec text says hourly; listed for the user); (13) the quiet-window deploy carried main only to ae1e86c; (14) sharp_two_sided was registered by that deploy's `variants register` step (Makefile:39) before the plan's replay: Amendment 3 records the true order; the replay runs on the phase build after this deploy; `GATE_VARIANT=sharp_two_sided` added to deploy/nas.env in this commit (U5, Task 4b step 5, controller action); (15) carried fixes 14 and 15 shipped on the branch (game 114's stale status; the funnel from run notes) and re-verify at this deploy
- Audit (on 6df2b7d): variants 0 lines; pyproject 0; hosts 0 beyond `harness/venues/kalshi/ws.py` FALLBACK_URL (phase 1, on main since 7b27916); DDL grep 1 line, a test docstring stating that no ALTER was used
- Anomalies: none new
- Carried forward: none (phase 6's deferred list lives in the archived final review and ledger)
- Next: deploy (entry 47) from `main` after `make test` on main; verify (entry 48); the phase report; bundle + `git push origin main phase3-paper-execution` (U7); Task 4b step 5's replay and Amendment 3; the morning-after verify with the walker after 08:10 CT

## 47. deploy - main 21f8ca4 (phase 3 deploy: app-exec, settlement, report, gate, telemetry) - 2026-09-08 04:52 CT
- Orient: rule 2 after the merge (entry 46): `main` 21f8ca4 ahead of the NAS build ae1e86c in code; preconditions held (main, clean tree; game window 0|0|0 with game 114 excluded by the entry 43 ruling; suite on main 821 passed pristine)
- Branch / commits: `main` ae1e86c..21f8ca4
- Result: done
- Dispatches: 0
- Tests: 821 passed on `main` at 21f8ca4, pristine (`harness_test_main`)
- Review: n/a
- Deploy: 21f8ca4 at 04:50:56-04:51:57 CT via `make deploy-nas` (the diff touches models.py and docker-compose.yml; `app-ws` restarted, a few seconds of quiet-hour tape), exit 0, no seed-teams warning, log `evidence/2026-09-08-deploy-0452-21f8ca4.log`; containers Up, `app-exec` present and healthy by 04:57 CT, `app-serve` healthy; stamp `"build":"21f8ca4"` at 04:52 CT; first executor heartbeat at 04:52:13 CT (loop 1, 610 ms, version 3.7, no error); snapshots 128 in the first 3 min; gap rows 0; no forced tick (quiet hours); the first settlement pass run by hand at 04:52-04:55 CT per the final review's deploy note 5 (97 games and 100 markets settled, 100 venue rows, 0 mismatches, `job_runs` 1 `degraded`: see entry 48)
- Verification: entry 48
- Rulings: (1) `GATE_VARIANT=sharp_two_sided` in `deploy/nas.env` (U5, Task 4b step 5) rode this deploy. (2) The Amendment 3 replay (`replay --from-run 2321 --to-run 3477 --variant sharp_two_sided`) launched on the NAS at 05:00 CT after the deploy; its counts go into the amendment when it finishes (a following session appends it if this one stops first).
- Carried forward: none in this entry
- Next: verify (entry 48)

## 48. verify - deploy 21f8ca4 - 2026-09-08 04:58 CT
- Orient: rule 3 - no verify entry since the deploy (47)
- Branch / commits: n/a
- Result: **FAIL** (three rows), otherwise PASS
- Dispatches: 0 (no walker: quiet hours; the day's first walker verify is the morning-after duty after 08:10 CT; the FAIL rows are deterministic)
- Tests: n/a
- Review: n/a
- Deploy: none (entry 47)
- Verification: Layer 1 PASS (stamp 21f8ca4). Layer 2 (evidence: `evidence/2026-09-08-deploy-verify-0458-layer2.txt`): containers Up, `app-exec` and `app-serve` healthy; cadence runs ok with 0 errors and `build_sha = 21f8ca4`; heartbeat rows inside 2 min; ERROR lines 0 on all four services in the 10 min window; gap rows 0; WS age 3 s; Postgres health ok; WAL 1.6 GB; DB 20 GB; disk 31 % free; memory 1359 MB; degraded sections 0; credits 4,998,958; `exec_heartbeat` age 14 s, loops 20, p95 47 ms, no error PASS; orders 0 and intents 0 (no exec-variant candidates in quiet hours; not a FAIL by the time-of-day table); settlements 97 and venue rows 100 PASS; game 114 `final` 24-27 (carried fix 14 healed it through the dated fetch `{"dates": "20260907"}` at 09:52:29Z) PASS; `metric_samples` fresh for every exec.*, recorder.*, ws.*, host.*, db.*, match.* name PASS; `check_results` 25 pass and 2 **skip** (`duplicate_trades`, `fair_values_negative_staleness`: statement timeouts) against the row's "all pass": **FAIL** (carried fix 16); `/api/summary` 200 in 43 s against the 10 s bound: **FAIL**, the second time running for this row (entry 44): a ceiling (carried fix 17); `verify-summary` cannot fetch the page. Layer 2b (evidence: `evidence/2026-09-08-deploy-verify-0458-layer2b.txt`): 14/14 zero PASS. `settle` job 1 `degraded`: `compute_benchmarks` failed for games 17, 18 and 114 with `StringDataRightTruncation` on `benchmarks.benchmark_type varchar(16)` (`kalshi_last_trade_pre_kick` is 26 chars): **FAIL** on the settlement row, cause below.
- Anomalies: (1) The production columns `benchmarks.benchmark_type`, `gap_outcomes.benchmark_type`, `order_clv.benchmark_type` are `varchar(16)` and `markouts.anchor` `varchar(8)`: the 01:30 CT deploy (ae1e86c, Task 2's models) created the four tables at those widths, and `create_all` never widens an existing column, so the Task 8 and Task 9 width rulings ("the tables have never existed in production, no ALTER needed") were true at 21:20 CT on 2026-09-07 and false after 01:30 CT; my error: I did not re-check after the quiet-window deploy. `benchmarks` holds 100 partial rows (games whose types all fit); the other three tables are empty. (2) `runs.build_sha` is NULL on heartbeat (`skipped`) rows and set on real ticks: acceptable, noted for the verify row's wording.
- Rulings: (1) The width fix is an `ALTER TABLE ... ALTER COLUMN ... TYPE` on production (metadata-only on empty or 100-row tables; the `clv` view must be dropped and recreated around the `order_clv` change): gate 3, the user executes; the loop proposes the exact statements (entry 49). (2) Until then the settlement job stays `degraded` every hour on the benchmarks stage only (savepoint per game; settle, venue_result, CLV drain and markouts unaffected), the executor and recorder run normally. (3) Carried fix 16 is phase work (a check change), 17 a hotfix from `main` once the gate clears.
- Carried forward: 16 (two checks exceed the 2 s timeout; verify.md row), 17 (page time: the candidates section scans signals)
- Next: gate (entry 49)

## 49. gate - production column widths need an ALTER TYPE; the page-time row failed twice running - 2026-09-08 05:05 CT
- Orient: gates 3 and 12 (entry 48)
- Branch / commits: `main` 21f8ca4 plus this docs commit
- Result: gated: (a) an `ALTER COLUMN ... TYPE` on the production database (gate 3: the user executes); (b) the same verify item (page time) failing twice running (a ceiling, gate 12)
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: entry 48
- Question (a): the four columns below must be widened before benchmarks, CLV and markouts can record the pre-registered names. Options: (1) the user runs the five statements below on the NAS (metadata-only, seconds; `benchmarks` has 100 rows, the others 0); (2) the loop ships a migration CLI that does the same (still an ALTER TYPE in code: gate 3 either way); (3) shorten the stored names (edits registered names: gate 9, and R1 forbids). Recommendation: option 1, now, in this order, then `docker compose run --rm app-run init-db` to recreate the `clv` view, then `docker compose run --rm -T app-run settle` once so the benchmarks stage catches up:
  ```
  ssh trey@192.168.12.228 'cd /volume1/docker/sports-harness && docker compose exec -T postgres psql -U harness -d harness' <<'SQL'
  begin;
  drop view if exists clv;
  alter table benchmarks alter column benchmark_type type varchar(32);
  alter table gap_outcomes alter column benchmark_type type varchar(32);
  alter table order_clv alter column benchmark_type type varchar(32);
  alter table markouts alter column anchor type varchar(10);
  commit;
  SQL
  ssh trey@192.168.12.228 'cd /volume1/docker/sports-harness && docker compose run --rm app-run init-db && docker compose run --rm -T app-run settle'
  ```
  (`init-db` recreates `clv` with `CREATE OR REPLACE VIEW`; the DDL count pin covers only statements `create_schema` issues, so nothing else changes.) Question (b): the page-time row: carried fix 17 (a hotfix from `main`) is the loop's proposed remedy; the ceiling asks the user to say "continue" so the loop may run that hotfix and re-verify. Recommendation: after (a), answer "continue" and the loop runs fix 17, re-verifies the page-time and check rows, and resumes the calendar (the morning-after verify with the walker after 08:10 CT; Task 4b step 5's Amendment 3 with the replay counts).
- Rulings: (1) While gated, the executor, recorder, settlement (degraded on one stage) and dashboard keep running: nothing stops data. (2) The Amendment 3 replay result (background) is recorded by the next session if it finishes after this one stops.
- Carried forward: none beyond entry 48
- Next: stopped (report `docs/superpowers/autopilot/reports/2026-09-08-stopped-0505.md`), both notifications, the repo bundle and `git push origin main` (U7)

## 50. gate (addendum) - the Amendment 3 replay demoted the live sharp_two_sided row - 2026-09-08 05:20 CT
- Orient: the entry 49 gate stands; this adds a third item for the user
- Branch / commits: `main` (docs)
- Result: gated (a third question)
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: n/a
- Anomaly: the plan's Task 4b step 5 command (`replay ... --variant sharp_two_sided --file harness/variants/sharp_two_sided.yaml`) registers the file's config with `tier: replay` as a new id (e82fcd0a1e99) and, by `register_variants`' documented rule for a name whose config changed, renamed the live secondary row 5632da729fa7 to `sharp_two_sided#5632da729fa7` and set it inactive at 09:58:22Z. The replay itself succeeded (Amendment 3 appended to the pre-registration record with its counts). A plan defect (the command should name the registered variant without `--file`), recorded; my error was running the plan's text without reading the replay's registration path first.
- Question: restore the live row. Options: (1) the user runs two UPDATEs (below); (2) the loop runs them (a hand edit to a registered row: gate 9's spirit, so no); (3) leave it: the NO-side variant is never scored live and the gate row stays on the primary. Recommendation: option 1, now:
  ```
  ssh trey@192.168.12.228 'cd /volume1/docker/sports-harness && docker compose exec -T postgres psql -U harness -d harness' <<'SQL'
  update strategy_variants set name = 'sharp_two_sided#e82fcd0a1e99' where variant_id = 'e82fcd0a1e99';
  update strategy_variants set name = 'sharp_two_sided', active = true where variant_id = '5632da729fa7';
  select variant_id, name, tier, active from strategy_variants order by registered_at;
  SQL
  ```
  (The replay row keeps its signals; the live row's id, config and registration time are untouched.)
- Rulings: (1) The 1,277,190 replay signals stay (tagged, excluded from reports; the amendment's evidence). (2) The next plan-next amends the plan text for any later replay step: never `--file` for a registered variant.
- Carried forward: none
- Next: stopped (the report is updated in this commit); the operate duties continue (morning-after verify after 08:10 CT)

## 51. decision - the user clears the gate and asks for a clean stop - 2026-09-08 07:12 CT
- Orient: n/a (user instruction in chat)
- Decision (user, verbatim, 2026-09-08 07:12 CT): "Run the four ALTER COLUMN TYPE statements on the NAS. The two UPDATE statements, and anything else needed so I may start the loop over in a fresh session."
- Rulings: (1) The user's explicit instruction is the authorization for the ALTERs and the UPDATEs (gate 3 and the entry 50 row restore): the loop runs them now, in the order the stopped report gives, and verifies each. (2) The instruction to prepare a fresh session is read as the answer to the ceiling question (entry 49 b): the fresh session's Orient rule 1 opens the hotfix unit for carried fix 17 first; if the user meant otherwise, the roadmap's Carried fixes row is the place to say so. (3) This session ends at a clean boundary after the verification below, with `state.md` carrying the resume steps.
- Next: the ALTERs, `init-db`, the UPDATEs, one `settle` pass, verification, `stopped` (entry 52)

## 52. stopped - gate cleared by the user; clean boundary for a fresh session - 2026-09-08 07:36 CT
- Orient: n/a (entry 51)
- Branch / commits: `main` (docs only since 21f8ca4; NAS build 21f8ca4)
- Result: done (the user's SQL executed and verified); stopped at the user's request
- Dispatches: 0 (session total 57)
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: the four columns now `varchar(32)`/`varchar(32)`/`varchar(32)`/`varchar(10)` (information_schema at 07:34 CT); `clv` view recreated by `init-db` at 07:34 CT (pg_views count 1); `sharp_two_sided` (5632da729fa7) active under its own name, the replay row renamed `sharp_two_sided#e82fcd0a1e99`; settle job 4 at 07:35 CT `ok`: benchmarks 264 rows across nine types (incl. `kalshi_last_trade_pre_kick` 148, `opening_first_seen` 15, `result` 150), gap_outcomes 1,095, order_clv 0 (no orders yet), markouts 0
- Rulings: (1) The entry 49 gates (a) and (b) are cleared by entry 51's decision; carried fix 17 (page time) is the fresh session's first unit (Orient rule 1), carried fix 16 stays phase work. (2) No wakeup survives this session (stopped).
- Carried forward: none new (16, 17 open)
- Next: fresh session per Kickoff. Orient: rule 1 hotfix 17 (`fix-2026-09-08-dashboard` from `main`, sonnet, reviewer sonnet; deploy with `make deploy-nas-app` since app-exec exists and the diff is app-only; re-verify the page-time row); then the morning-after verify with the walker (the calendar day's first walker verify; the 08:10 CT pricing run is the first to score `sharp_two_sided` live: check `signals` for 5632da729fa7 and `venue_markets.price_ranges` populating); the daily 09:00 CT line; then phase 4 plan-next (its first task carries fix 16 and the plan-text correction: never `--file` for a registered variant). Report: `docs/superpowers/autopilot/reports/2026-09-08-stopped-0736.md`

## 53. decision - the family panel: gap_mid, families A and B on the featured feed - 2026-09-08 07:43 CT
- Orient: n/a (user instruction in chat)
- Decision (user, verbatim, 2026-09-08 07:43 CT): "I'll do the recommendation." (the recommendation of 07:40 CT: ratify `gap_mid` as the family panel and restrict families A and B, the shrinkage and the §9.6 count to the featured feed, the addendum's "the headline H2 claim is from `feed_kind = featured`"; the maker-net and all-feed cells stay as display columns)
- Rulings: (1) The ratification is a dated note in the pre-registration record's Amendment 3 (appended now). (2) The feed restriction is a code change in `harness/report/tables.py` (`_table4`: the family's CI reads the `feed featured` bucket instead of the all-feed panel) shipped as a reviewed hotfix `fix-2026-09-08-report-panel` from `main` (sonnet implementer, sonnet reviewer), deployed with `make deploy-nas-app` (app-only diff; app-exec exists) outside a game window, and verified by rendering `report --week 37` on the NAS; the cell definitions, BH q, the grid and every threshold are untouched (R1) - cost if wrong: a week-38 selection on the featured stratum only, which is the addendum's own wording
- Next: hotfix unit (entry 54)

## 54. stopped - clean boundary before a fresh session (user request) - 2026-09-08 08:06 CT
- Orient: n/a (user instruction in chat: "I'm ready to start a new session ... mark state files appropriately")
- Branch / commits: `main` at de5514a plus this docs commit; NAS build 21f8ca4
- Result: stopped at the user's request
- Dispatches: 1 since entry 52 (the panel hotfix implementer, sonnet, dispatched 07:44 CT; idle at 08:04 CT with `harness/report/tables.py` and `tests/test_report.py` modified, uncommitted, no report: the dead implementer's partial work stays in the worktree per resume rule 2a)
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: not run (no walker verify today; the daily line not written today)
- Rulings: (1) Neither hotfix is done: 17 (page time) has no branch; the panel hotfix (decision 53) has uncommitted work on `../sports-wt/fix-2026-09-08-report-panel` (branch `fix-2026-09-08-report-panel` from `main` de5514a). (2) The next session runs both hotfixes in parallel (disjoint areas: dashboard vs report) and ships them in one deploy (`make deploy-nas-app`; the diff is app-only), outside a game window, with a forced tick after 08:00 CT; then re-verifies the page-time row and renders `report --week 37 --out -` to confirm table 4's header reads featured-only. (3) Then the morning-after walker verify (the calendar day's first walker: check `signals` for 5632da729fa7 since the 08:10 CT run and `venue_markets.price_ranges` populating), the daily 09:00 CT line, then the repo bundle and `git push origin main` (U7).
- Carried forward: none new (16 phase work; 17 open)
- Next: fresh session per Kickoff; `state.md` carries the order

## 55. preflight - fresh session - 2026-09-08 08:08 CT
- Orient: n/a (preflight)
- Branch / commits: `main` c39cf0f (docs only ahead of NAS 21f8ca4)
- Result: done
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: `paper posture intact` (verbatim); evidence `docs/superpowers/autopilot/evidence/2026-09-08-preflight-0806.txt`: NAS 5/5 Up (app-exec, app-serve, postgres healthy), `/healthz` build 21f8ca4, credits 4,998,946 of 5,000,000, `/volume1` 69 % used (3.5 T free), memory available 1214 MB; game window 0|0|0; last real tick 3927 ok 101 s ago, heartbeat 3930 skipped 11 s, WS event age 0 s; tunnel 200; caffeinate running; secrets 600; test DB up, pytest processes 0
- Tools (R23): ScheduleWakeup, PushNotification, ListAgents, CronCreate, CronList, CronDelete, Monitor, SendMessage and the Chrome set resolved; `tabs_context_mcp` answered (no tab group yet). Test notifications (R3, first session of the day): osascript sent ok; PushNotification returned "not sent - terminal active" (channel reachable, suppressed as redundant)
- Rulings: (1) The untracked preflight evidence file is a state-file edit: committed here. (2) Worktree `../sports-wt/fix-2026-09-08-report-panel` maps to state.md (the dead implementer's uncommitted work is kept and re-dispatched per resume rule 2a); a new worktree `../sports-wt/fix-2026-09-08-dashboard` from `main` for carried fix 17. (3) The panel branch is based on de5514a and `main` moved by docs commits only: the controller rebases it onto `main` before the ff merge.
- Carried forward: none
- Next: hotfix (two batches in parallel: 17 dashboard, decision-53 report panel), one shared deploy

## 56. hotfix - 17 dashboard page time + decision-53 report panel (one wave) - 2026-09-08 09:20 CT
- Orient: rule 1 - roadmap Carried fixes row 17 open; journal 53 ruling 2 (panel hotfix); state.md resume order 1-2
- Branch / commits: `fix-2026-09-08-report-panel` de5514a..1c878b6 (rebased, merged as 8638ea0); `fix-2026-09-08-dashboard` c39cf0f..8a5e1b6 (rebased, merged as dc08c1c..33a0e3a); `main` 7c5ccd1..33a0e3a
- Result: done
- Dispatches: 6 (impl sonnet x2 / review sonnet x2 / re-review opus x1 + haiku x1)
- Tests: panel branch pristine (exit 0, all dots); dashboard branch 825 passed pristine; `main` 33a0e3a 826 dots pristine
- Review: panel clean (0 rounds); dashboard 2 fix rounds (round 1 Important: the `venue_trades` 24 h count had no index on `source`, replaced by a per-run `kalshi_trades_normalized` note summed over the same 24 h notes window; round 2 Important: pre-deploy rows without the key fed the numerator only, now skipped), re-reviews clean
- Deploy: 33a0e3a at 09:17 CT via `make deploy-nas-app` (app-ws untouched, gap rows n/a), stamp verified (`/healthz` build 33a0e3a at 09:19 CT), app-serve healthy at 09:19 CT, forced tick run 4067 ok n=98 credits=6 at 09:20 CT
- Verification: entry 57 (the morning-after verify with the walker folds in the page-time and panel rows); `report --week 37` rendered on the NAS at 09:20 CT (evidence `evidence/2026-09-08-report-w37-panel-0920.md`, 677 lines): table 4's header reads "Families A/B, the `posterior` shrinkage and the §9.6 count run on `gap_mid` restricted to the `feed featured` rows" PASS
- Rulings: (1) The per-side candidate split is dropped from the dashboard's candidates section (served from run notes, which carry no side) with a note; the deterministic verify and the walker read `funnel.signals_by_variant`, untouched - cost if wrong: an operator loses a per-side glance, the weekly report keeps sides. (2) Round 1 widened into `harness/normalize/kalshi.py` and `harness/recorder/tick.py` (an additive `kalshi_trades_normalized` run-note; the brief's suggested key counted pages, the implementer corrected it): accepted as within the finding (bounding a 24 h read), re-review tier `opus` by the path rule over the size rule. (3) Round 2 diff 59 lines: re-review `haiku`. (4) The panel branch's docs-only rebase onto `main` carries its suite result; `make test` on `main` after the merge is the deciding run. (5) The week-37 render goes to `evidence/`, not `docs/reports/2026-w37.md`, which the Monday 2026-09-14 duty writes. (6) The re-reviewer's two observations are ledgered Minors, no action: `_recent_run_notes`'s `limit` parameter is now unused in production; a `harness normalize` reprocess re-counts sideless prints in the numerator but not the denominator (pre-existing asymmetry, the watermark keeps it out of normal operation). (7) `no_taker_side_share_24h` reads only post-deploy runs for its first 24 h (until 2026-09-09 09:17 CT): a deferred verify note, not a FAIL.
- Anomalies: (1) `budget_exhausted` on the pricing loop: 83 of 272 pricing runs since 2026-09-07 00:00Z (30 %) ran out of `price_budget_s` after scoring one to five variants, all on the large alternates-fed ticks (~3,700 gaps; 148 s at 13:00Z), and the tail dropped rotates by run id by design (`harness/strategy/pipeline.py`); the roadmap's phase 6 item 4 threshold (10 % of game-day ticks) is met on the pricing side: an input to plan-next, not a hotfix (a budget or cadence change is phase work). (2) `sharp_two_sided` (5632da729fa7) had 0 live signals as of 08:16 CT: the 13:00Z run exhausted its budget after `no_velocity`; judged again in entry 57 after the forced tick. (3) The haiku re-reviewer reported "640 tests" against the implementer's 825: a subset count; the controller's `make test` on `main` decides.
- Carried forward: none (row 17 removed once entry 57's page-time row passes)
- Next: verify (morning-after, walker runs: day's first verify and a dashboard diff)

## 57. verify - morning-after (game day 2026-09-07) + hotfix deploy 33a0e3a - 2026-09-08 09:55 CT
- Orient: rule 3 - no `verify` entry since the deploy in entry 56; the calendar's morning-after duty (walker runs: day's first verify, and the diff touched `harness/dashboard/`)
- Branch / commits: `main` 33a0e3a (NAS build 33a0e3a)
- Result: FAIL (three items, all carried)
- Dispatches: 1 (walker sonnet)
- Tests: n/a
- Review: n/a
- Deploy: none (entry 56)
- Verification: PASS 60/63 (evidence: `evidence/2026-09-08-verify-0926-layer2.txt`, `-layer2-mac.txt`, `-layer2b.txt`, `-summary.txt`, `2026-09-08-verify-0939-summary.txt`, screenshots `2026-09-08-verify-0926-NN-*.jpg|png`). Layer 1 stamp 33a0e3a PASS. Layer 2: containers 5/5 Up, app-serve and app-exec healthy; runs ok on the 15-min daytime cadence (real ticks 13:00, 13:15, 13:30, 13:45, 14:01, 14:16Z plus U1 alternates fetches), heartbeat 28 s, no errors; ERROR lines 0 on every service; tape continuity gap rows 0 today (10,153,098 events); WS event age 1.2 s; Postgres dead tuples under rule, WAL 1 GB; disk 31 % free (3.5 T); memory available 1396 MB; degraded sections 0; credits 4,998,844 numeric and decreasing on real ticks only; DB 21 GB (1 % of the 2 TB ceiling); build stamp since deploy 33a0e3a only: all PASS. Phase 3 rows: exec heartbeat age 14 s, no error, p95 994 ms (page 4770 ms, both under 7500); 1 open order (ws book), 98 cancelled, 0 open without tape; intents 509, fills 0, settlements 98, venue_settlements 300, benchmarks 414, gap_outcomes 1,845, markouts 290; skip reasons a breakdown (fair_stale 508, book_dirty 501, no_book 98); settle job ok at 08:54 CT (one `degraded` row at 07:51 CT: `gap_outcomes_drain` hit "cached plan must not change result type" right after the 07:34 CT ALTER TYPE, the next pass ok: transient); order_clv 0 (no fills: n/a); metric samples all exec/recorder/ws names fresh; order watch 12 rows/h; equity snapshots per exec variant; game score events 103; check_results 25 pass + 2 skip (carried fix 16, phase work, noted not re-failed); report_runs as expected; the week-37 render PASS (entry 56); `harness gate` rows n/a (Monday duty). Layer 2b: 25 of 27 invariants 0; `build_sha_drift` 519 = the pre-deploy runs inside 24 h (expected after a deploy; `check_results.build_sha_drift` pass); `metric_samples.value < 0` = 2 rows (`exec.ws_event_age_s` -0.006 and -7.0 s: the exchange event clock ahead of the NAS clock) **FAIL -> carried fix 18**, `metric_samples` under audit until it clears; book coverage 99/99 orders with a ws book; stale benchmarks pinnacle_t5 0/11, consensus_t5 1/12 (8 %); CLV sanity and tape conservation n/a (no fills). Layer 3 (09:28 CT): 8/9, **page_time FAIL** (`/` 0.5 s, `/api/summary` 21.6 s): the first request after the app-serve restart; re-measured at 09:32 CT (4 min later, not the rule's 10: the mechanism was already clear) `/api/summary` 0.3 s and the deterministic re-run 0.4 s / 0.4 s; no cache exists in the app, so the warm number is real -> **carried fix 19** (cold first request: the `_websocket` 1 h `venue_trades` count has no index on `(source, ts)`). Walker 16 items: 14 PASS, 2 FAIL; controller re-score: item 10 FAIL confirmed (heartbeat age renders "-1": a fractional negative age formatted "%.0f", folded into fix 19), item 12 re-scored PASS (P&L and exposure tables render headers; 0 fills and 0 positions exist), item 4 PASS (seven active variants with counts plus a zeroed `sharp_two_sided#e82fcd0a1e99` replay row). Deferred: days-to-ceiling "not yet measurable" (housekeeping has one day of growth data; judge after 2026-09-09 09:52 CT); `no_taker_side_share_24h` reads keyed rows only until 2026-09-09 09:17 CT.
- Rulings: (1) Row 17 is closed: its covering rows pass (warm page 0.4 s; `_funnel`, `_candidates`, `_data_quality` issue no statement against the four tables); the cold first request is a distinct cause carried as 19, not silently passed and not the same item a third time. (2) `build_sha_drift` 519 after a deploy is by construction; not an anomaly. (3) Walker item 12: empty P&L/exposure tables with 0 fills is data, not a defect; a zero row per exec variant is a phase 4.5 display note. (4) The replay-tier row `e82fcd0a1e99` is `active = true` and appears on the dashboard with 0/0: a hand UPDATE on a registered row is the user's call (entry 50): listed in the report's Needs you as optional. (5) Odds credits 142 used by 09:26 CT: below the U1 band floor (2,000/day) because only one game sits inside the 36 h alternates window; explained, judged again in the Monday duty.
- Anomalies: (1) Pricing budget: every daytime pricing run today (7 of 7 since 08:00 CT, 83 of 272 since yesterday) exhausts `price_budget_s = 20` after 1-4 of 7 variants over 4,541 gaps (about 6 s per variant; ticks take 50-148 s against `tick_budget_s = 100`); coverage since 08:00 CT: no_velocity 5, nfl_only 4, constrained/sharp_direct/sharp_plus_derived 3, sharp_two_sided 1, wide_band 1 of 7 runs. The rotation is by design (spreads missingness), so the primary and the gate variant are scored on under half the ticks: a throughput design item for phase 4 plan-next's first tasks (options: raise the budget within the tick, always score the gate variant and the primary first, phase 6 item 4's normalizer; every option is a dated amendment) - not a hotfix. (2) `sharp_two_sided` first live scoring: run 4035 at 09:01 CT, 194 candidates / 8,888 rejected in 24 h (Amendment 3's "first live scoring" date is 2026-09-08 09:01 CT). (3) `report --week 37` writes a `report_runs` row with `provisional = false` (by design, noted).
- Carried forward: 18 (executor `exec.ws_event_age_s` negative), 19 (dashboard cold first request; heartbeat age "-1"); 17 removed
- Next: hotfix (18 executor path, opus; 19 dashboard, sonnet; dispatched 09:44 CT), then operate (daily line, entry 58; bundle + push)

## 58. operate - daily 09:00 CT line - 2026-09-08 09:58 CT
- Orient: rule 4 - calendar "Daily 09:00" (due; written at the verify boundary)
- Branch / commits: n/a
- Result: done
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none
- Verification: `/volume1` 69 % used, 3.5 T free (31 %; gate below 25 %); `free -m` available 1396 MB (R17); database 21 GB (`pg_database_size`), housekeeping 19.7 GB = 1 % of the 2 TB ceiling, growth "not yet measurable" (one day of data; days-to-ceiling deferred to 2026-09-09); Odds credits 4,998,844 remaining, 142 used today by 09:26 CT (under the U1 band floor: one game inside the alternates window; ruling 5 of entry 57); Anthropic spend n/a before phase 5; executor heartbeat age 14 s, loop 1094, no error; ERROR messages none on any service (10 min); kill switch observed inactive; `secrets/backup_age_key` does not exist (no nag)
- Rulings: none
- Carried forward: none
- Next: hotfix batches 18 and 19 in flight; repo bundle + `git push origin main` (U7) now

## 59. hotfix - 18 executor metric + 19 dashboard cold request (one wave) - 2026-09-08 10:05 CT
- Orient: rule 1 - roadmap Carried fixes rows 18 and 19 (entry 57)
- Branch / commits: `fix-2026-09-08-exec-metric` 9925958..fdedde5 (rebased, merged as b25bd53..af58a29); `fix-2026-09-08-dashboard-cold` 9925958..80080bf (rebased, merged as ..1911a4f); `main` 5afaf50..1911a4f
- Result: done
- Dispatches: 4 (impl opus + sonnet / review opus + sonnet)
- Tests: fix 18 branch 829 passed pristine; fix 19 branch 833 passed pristine; `main` 1911a4f all dots pristine
- Review: fix 18 clean (one Minor fixed by the reviewer: tests relocated beside the metric-batch test, fdedde5); fix 19 clean after the addendum round (the implementer's first report omitted the heartbeat clamp it had in fact committed as 80080bf: a reporting gap, not a code one)
- Deploy: 1911a4f at 10:02 CT via `make deploy-nas-app` (app-ws untouched, gap rows n/a), stamp verified (`/healthz` build 1911a4f at 10:04 CT), app-serve and app-exec healthy by 10:06 CT, forced tick run 4156 ok n=50 credits=6 at 10:07 CT
- Verification: entry 60
- Rulings: (1) Fix 19 widened from the `venue_trades` scan to the heartbeat-age clamp and the green badge (walker item 10) as one dashboard batch. (2) `ws_trades_1h` is now an approximation from the sink's per-minute samples (documented in the key's docstring); its meaning "WebSocket prints in the last hour" is unchanged and no verify row reads the number. (3) The implementer's inventory found two more unindexed reads (`venue_quotes` in the unmatched-markets section, `odds_snapshots` staleness in data quality) with no small-table proxy: carried to plan-next as a phase 4.5 snapshot-layer input, not a hotfix (the cold measurement below passes). (4) The Makefile's `-q` on top of pyproject's `addopts = "-q"` hides pytest's count line (noted by two agents): a one-character Makefile change for the phase 4 plan's ops task, not a hotfix.
- Carried forward: none
- Next: verify (entry 60)

## 60. verify - re-verify rows named by fixes 18 and 19 (deploy 1911a4f) - 2026-09-08 10:12 CT
- Orient: rule 3 - no `verify` entry since the deploy in entry 59; scoped to the rows the findings name (skill Unit: hotfix)
- Branch / commits: `main` 1911a4f (NAS build 1911a4f)
- Result: done (PASS)
- Dispatches: 1 (walker sonnet, scoped to items 1, 8, 10)
- Tests: n/a
- Review: n/a
- Deploy: none (entry 59)
- Verification: PASS 6/6 (evidence: `evidence/2026-09-08-verify-1005-summary.txt`, screenshots `2026-09-08-reverify-1008-08-websocket.jpg`, `-10-executor.jpg`). Layer 3 at 10:06 CT, the first `/api/summary` request after the restart (the cold measurement fix 19 targets): 9/9 PASS, `/` 0.4 s, `/api/summary` 0.6 s (21.6 s cold before the fix); `ws_trades_1h` 1544 numeric. Fix 18: every `exec.ws_event_age_s` sample since the deploy is >= 0 (min 0.0067 over 5 samples) and `exec.ws_event_ahead_s` is written beside it (5 samples, max 0.0); the 24 h invariant still returns 3 rows, all stamped before the restart (14:09Z, 14:27Z, 15:01:47Z; the deploy restarted app-exec at 15:03Z): deferred, judge after 2026-09-09 09:30 CT when they age out. Walker (scoped): item 1 build 1911a4f PASS; item 8 last event 2 s old, orderbook events 11,641 in 5 min, ws trades 1,565 PASS; item 10 heartbeat age 12 s with a green `ok` badge, last WS event 14 s PASS; controller re-score of item 10 from the screenshot: PASS (age 12, badge ok, no negative values anywhere in the Executor block). Executor heartbeat 7.5 s, no error; ERROR-bearing runs in 10 min 0; build stamp since the restart 1911a4f (plus the old process's last runs at 33a0e3a, expected).
- Rulings: (1) Rows 18 and 19 close: their code rows pass; the aged-out invariant is a deferred item, not an open fix. (2) Observation: open paper orders rose from 1 to 143 between 09:28 and 10:09 CT after the daytime pricing runs (capacity `exec_max_open_orders` = 150): expected on a weekday with games inside 8 days; the `exec_capacity` skip reason will appear once the cap binds; noted for the daily line, not a FAIL.
- Anomalies: (1) The forced tick 4156 again exhausted the pricing budget (2 of 7 variants: `sharp_two_sided`, `wide_band`): the phase 4 Task 1 item (Amendment 4), unchanged.
- Carried forward: none (16 stays phase work)
- Next: plan-next (phase 4; the plan writer is running), then Unit: phase; bundle + push now (U7)

## 61. plan-next - phase 4 (Kalshi authenticated adapter still paper, risk gate, backups, Alembic) - 2026-09-08 11:06 CT
- Orient: rule 6 - phase 4 status `not planned`, no unmet gate; started 09:58 CT after entry 58 (inputs: v2 spec §5.2, §9.1-9.4, §14, §15.4; roadmap pre-loaded decisions 1-11; phase 3 addendum; journal 56-57 anomalies; Kalshi Trade API and Alembic docs through ctx7; live NAS facts: no `age` or `pg_dump` on the Mac or NAS host, `pg_dump 16.15` in the postgres container)
- Branch / commits: `main` c180f0f (addendum rev 1) .. this commit (addendum rev 2 + erratum, plan rev 2 + residuals); design `docs/superpowers/specs/2026-09-08-phase4-kalshi-authed-design.md`, plan `docs/superpowers/plans/2026-09-08-phase4-kalshi-authed.md` (17 tasks, 11 waves, 4,703 lines)
- Result: done
- Dispatches: 4 (design reviewers opus x2 / plan writer opus / plan reviewer opus, two rounds) - plan-next ceiling 6
- Tests: n/a
- Review: design review 11 Critical + 25 Important across both lenses, every one ruled on (addendum "Rulings" section, all accepted and applied); plan review round 1: 6 Critical, 11 Important, 11 Minor, all applied by the writer (revision 2); round 2: 0 Critical, 2 Important (N1 `is_file()` credential gate, N2 test clock attribute), 3 Minor, applied by the controller as rulings; the reviewer's CCTV count (68) corrected by the writer's measurement (69: 15 success / 18 payload / 32 header / 3 no match / 1 HMAC), confirmed by the reviewer
- Deploy: none
- Verification: n/a (the plan's Task 16 extends verify.md); audit 3a baseline on `main`: variants diff empty; pyproject additions empty; hosts: only the pre-existing `wss://external-api-ws.kalshi.com/` fallback (phase 1, explained); DDL grep empty
- Rulings (decisions taken on the user's behalf, addendum §11): D1 age v1 format implemented with `cryptography` (no `age` binary anywhere; CCTV vectors as fixtures); D2 dump sidecar on the postgres image (pg_dump with native zstd); D3 `signals` stays in the nightly dump per decision 8 (100-150 GB of nightlies by week 3, reversible by amendment); D4 gateway interface with `PaperGateway` (paper behaviour unchanged, golden replay); D5 `venue_requests` records authenticated calls only, made non-vacuous by the recorder's hourly limits read; D6 drawdown stop is an annotation label outside every decision rule (`ANNOTATION_LABELS`), paper keeps placing, the badge is phase 4.5; D7 `alembic` added to pyproject plus two appended pins (gate 7 forbids regenerating, not appending: listed for the user under "Decisions you may want to reverse"); D8 pricing order (gate variant by id, then primary, then rotating secondaries) and `price_budget_s` 20 -> 45 as Amendment 4 with the protocol's four elements, table 2 and family C named, a coverage line in table 1, deployed mid-phase by the controller; D9 demo hosts as pinned by the roadmap; D10 the restore drill in two halves (NAS restore into a throwaway container with an anonymous volume, Mac decrypt of the same build's file; plaintext kept until the Mac half proves decryption); D11 `venue_status` keyed (venue, env), outages counted for prod only. Further rulings: app-run gains the production key files read-only for the GET-only limits read (the read-scoped key TODO makes the fence physical); the demo smoke runs only as a controller `docker compose run` with explicit one-run mounts; `Settings.mode` and `live_trading` bound to HARNESS_MODE and LIVE_TRADING (implementing invariant 3, not changing it); Task 14 (unattended NAS shell scripts) is opus; Task 1 deploys with `make deploy-nas-app` (its diff is app-only), the phase deploy with `make deploy-nas`.
- Carried forward: none (row 16 is the plan's Task 2)
- Next: phase (Unit: phase; branch `phase4-kalshi-authed` from `main`; wave 1 = Tasks 1, 2, 3, 12 under the 3-implementer ceiling)

## 62. phase start - phase 4 Kalshi authenticated adapter (still paper), risk gate, backups, Alembic - 2026-09-08 11:07 CT
- Orient: rule 5 - phase 4 status `planned` (entry 61), no unmet gate
- Branch / commits: `phase4-kalshi-authed` from `main` 185b182; plan `docs/superpowers/plans/2026-09-08-phase4-kalshi-authed.md` (17 tasks); SDD workspace `.superpowers/sdd/2026-09-08-phase4-kalshi-authed/` (ledger, briefs, reports)
- Result: running
- Dispatches: 0 at start (wave 1: Tasks 1 opus, 2 sonnet, 12 opus now; 3 sonnet when a slot frees; ceiling 3 concurrent)
- Tests: n/a
- Review: n/a
- Deploy: Task 1 mid-phase (controller, from `main`, `make deploy-nas-app`); the phase deploy after Task 16 (`make deploy-nas`: Dockerfile and compose change)
- Verification: n/a
- Rulings: the ledger's standing rulings (deploy from main; trailers; parallel disjoint tasks); pre-flight scan clean (12 shared-file rows, all serialized by the wave map)
- Wave map: 1={1,2,3,12} 2={4} 3={5,13} 4={6,14} 5={6b,7} 6={8} 7={9} 8={10} 9={11} 10={15} 11={16}
- Carried forward: none
- Next: phase (wave 1 running)

## 63. deploy - phase 4 Task 1 (pricing order and budget, Amendment 4) mid-phase - 2026-09-08 11:44 CT
- Orient: the plan's Task 1 "Controller: deploy this task now" (R15: from `main`); Orient rule 2 also held (`main` ahead of the NAS in code)
- Branch / commits: `phase4-t1-pricing` 185b182..ceae0f2 (rebased, merged as 2937a7a..a193fd0 into `phase4-kalshi-authed`, ff into `main`); NAS 1911a4f -> a193fd0
- Result: done
- Dispatches: 0 (the task's 3 seats are in the phase's count)
- Tests: task branch 853 passed pristine; `main` a193fd0 all dots, exit 0
- Review: task review clean after fix round 1 (the pricing budget capped to the cadence in force)
- Deploy: a193fd0 at 11:44 CT via `make deploy-nas-app` (app-ws untouched, gap rows n/a), game window 0|0|0 at 11:43 CT, stamp verified (`/healthz` build a193fd0 at 11:46 CT), app-serve and app-exec healthy, forced tick run 4344 ok n=166 credits=6 at 11:49 CT
- Verification: entry 64
- Rulings: (1) The amendment's deploy line says `make deploy-nas-app` (the plan wrote `make deploy-nas`; the diff touched no build or socket file). (2) Amendment 4 placeholders filled: sha a193fd0, 11:44 CT, pre-fix range runs 344 (the first budget-exhausted pricing run of 2026-09-07) to 4327 (the last pricing run before the deploy); 4344 is the first post-fix run.
- Carried forward: none
- Next: verify (entry 64), then the phase continues (wave 1: Tasks 2, 3, 12 in flight)

## 64. verify - Task 1 rows after deploy a193fd0 - 2026-09-08 11:57 CT
- Orient: rule 3 - no verify since the deploy in entry 63; scoped to the rows the task names (the full morning-after verify ran in entry 57)
- Branch / commits: `main` a193fd0
- Result: done (PASS)
- Dispatches: 0
- Tests: n/a
- Review: n/a
- Deploy: none (entry 63)
- Verification: PASS 6/6 (evidence: the run-note query in this entry; `evidence/2026-09-08-report-w37-coverage-1150.md`). Forced tick run 4344 (81 s at the 900 s daytime cadence): `pricing.order` = [sharp_two_sided, sharp_direct, wide_band, constrained, nfl_only, no_velocity, sharp_plus_derived] (the gate variant 5632da729fa7 first, the primary second, secondaries rotated) PASS; `variants_run` equals the order, all seven scored, `budget_exhausted` false PASS; `budget_s` 45, `budget_capped` false (fetch and normalize left the whole budget) PASS; `variant_ms` 2.4-2.6 s per YES-only variant and 4.9 s for the two-sided one, 19.5 s in total PASS; `gate_variant_id` 5632da729fa7, `gate_variant_missing` false PASS; build stamp since the restart a193fd0 (plus the old process's last runs) PASS; table 1 renders the `tick_coverage` column on the NAS PASS. Deferred: the coverage line's values are judged in the Monday duty (the column exists; the week-37 window straddles the deploy).
- Rulings: (1) The pricing-budget anomaly of entries 57 and 60 is closed by this deploy; the phase 6 normalizer item keeps its measurement note. (2) The reviewer's watch item (a busy game-day tick may still overrun with the 20 s floor) is a verify.md row for Task 16: `recorder.tick_ms` and `budget_capped` on game-window ticks.
- Anomalies: none
- Carried forward: none
- Next: phase (wave 1 continues)

## 65. phase done - phase 4 Kalshi authenticated adapter (still paper), risk gate, backups, Alembic - 2026-09-08 21:13 CT
- Orient: Unit: phase step 8 (final review clean after one fix wave; audit clean; archive committed)
- Branch / commits: `phase4-kalshi-authed` 185b182..65e8a10 (58 commits incl. the archive d5ec5dc), ff-merged into `main` at d5ec5dc; archive `docs/superpowers/reviews/2026-09-08-phase4-{sdd-ledger,final-review,final-fixes}.md`
- Result: done
- Dispatches: 54 this unit (17 implementers, 17 task reviews, 17 scoped re-reviews incl. Task 5's wrapper commit, 1 final review, 1 fix wave, 1 fix-wave re-review; the ceiling is 80)
- Tests: 1729 passed pristine on the branch (fix-wave head = merged head); `make test` on `main` follows in this unit before the deploy
- Review: 17 task reviews (11 needed one fix round, Task 6 two); final whole-branch review: 1 Critical (the first deploy would abort at the backup precheck: fixed), 3 Important (table 11 added; the drill's Mac half documented with the tunnel; KALSHI_ENV=prod in nas.env), 3 Minor (fixed); all 28 deferred minors triaged ship-as-is (two ▲ noted for a follow-up); conformance §12: 1-12 pass after the wave
- Deploy: pending (this evening: backup-keygen on the Mac first, then `make deploy-nas` outside the game window; the Wed 19:20 CT kickoff is the next window)
- Verification: entry 67 after the deploy
- Rulings (exhaustive roll-up from the ledger):
  - Standing rulings (autopilot skill): Ruling: deploy steps run from main by the controller (R15); Task 1's "Controller: deploy this task now" = ff-merge the phase branch into main, make deploy-nas-app (its diff is app-only), verify, continue — cost if wrong: one extra app restart. Ruling: commit trailers use this session's values (Co-Authored-By Claude Fable 5.1; Claude-Session session_01NS7krCnaLCV6QawWTyEjHZ) — cost if wrong: none. Ruling: parallel dispatch of tasks with disjoint Files: lines in separate worktrees/databases; same-file tasks serial in plan order — cost if wrong: a rebase conflict the implementer resolves.
  - Ruling: scan clean, no plan text changed — cost if wrong: a conflict surfaces in a task review.
  - Task 12: Ruling: CCTV in-scope count is 68 (14 success / 18 payload / 32 header / 3 no match / 1 HMAC), not the plan's 69: hybrid_and_x25519 supplies only a hybrid identity and needs the ML-KEM stanza (implementer's three-way verification) — the plan's assertion and Task 13's acceptance line are amended by the controller — cost if wrong: one vector less in the known-answer suite.
  - Task 1: Ruling: task reviewer opus (the table names sonnet for harness/strategy/, but this task carries a pre-registration amendment and the measurement order) — cost if wrong: one opus review seat.
  - Task 1: Ruling: pricing budget capped per tick to what remains of the cadence in force (max(20, min(price_budget_s, cadence - elapsed - 10))), recorded as pricing.budget_s/budget_capped; Amendment 4 and the addendum sentence corrected; Task 1 Files gains harness/recorder/tick.py (no other current task touches it) — cost if wrong: on a busy game-day tick pricing gets the 20 s floor (today's behaviour), never less.
  - Task 12: reported DONE_WITH_CONCERNS 11:38 CT (38ac364; 952 passed). Concern 1 ruled (68). Ruling: concern 2 (last-chunk finality decided by trying a full block interior-first then final, per the corpus's payload-failure vectors) is accepted as the format's rule; the addendum's generic wording stands — cost if wrong: none, the vectors pin it. Concern 3 is D1's known cost. Concern 4 (header_chunk_count validates payload size) is carried into Task 13's dispatch.
  - Task 12: Ruling: task reviewer opus (cryptographic format) — cost if wrong: one opus seat.
  - Task 2: Ruling: sql_for falls back to the static parent-table statement when the partition is absent (to_regclass) — cost if wrong: the slow path on a fresh database only
  - Task 2: stall 13:24 CT: impl-t2 idle since 12:08 CT with uncommitted fix-round edits and no report (it waited on a background make test that never woke it); report-now sent, 10-min timer armed; Ruling: every later implementer brief says to run the suite in the foreground, never in the background — cost if wrong: none
  - Task 5: NEEDS_CONTEXT 13:54 CT: the log-redaction filter redacts KALSHI-ACCESS-* values in plain form but NOT in dict-repr form (the design reviewer's claim was wrong; measured by the implementer). Ruling: no filter edit (invariant 4, a gate); the dict-repr assertion is replaced by a caplog test that the transport logs no header value at any level and that venue_requests carries no header/body; the plain-form test stays; the gap goes to the phase report's Needs you as an optional user-side filter extension — cost if wrong: a future module that logs a header dict would leak a signature into the JSON log (no module does today).
  - Task 5: Ruling: module-level test key and a local db_session_factory fixture in the task's test file (no shared conftest edit mid-wave) — cost if wrong: a small fixture duplication for the final review to fold
  - Task 13: Ruling: task reviewer opus (the only file-deleting rule in the phase, encryption pipeline) — cost if wrong: one opus seat
  - Task 13: Ruling: release when a drill row with decrypt_ok has build_sha equal to the unit's encrypt row build (stable across deploys) — cost if wrong: plaintexts linger until a drill of that build runs
  - Task 13: Ruling: a failed-structure ciphertext is renamed .bad-<stamp>, never deleted (invariant 5) — cost if wrong: a stray file per failure
  - Task 13: Ruling: keygen writes repo-relative defaults (secrets/backup_age_key, deploy/backup_age.pub), recipient first, identity 0600 via os.open — cost if wrong: none
  - Task 5: Ruling: read client wrapped in a GET/HEAD-only facade; docstring corrected; harness/feeds/http.py untouched — cost if wrong: a thin wrapper
  - Task 5: Ruling: transport errors wrapped in VenueTransportError without the request object (the raw httpx exception carries signed headers); https required before signing — cost if wrong: callers catch a new type (Tasks 6-8 are told)
  - Task 6: Ruling: KalshiApiError(status, method, path, code) raised on any non-2xx (code only, escaped, 40 chars); decoders always populate outcome_side and book_side via canonical_side; dec rejects non-finite — cost if wrong: callers (6b, 9, 10) catch one more type
  - Task 14: Ruling: deploy sequencing: the controller runs backup-keygen before the first phase deploy so deploy/backup_age.pub exists when the compose bind source is evaluated (else Docker creates a directory that a later tar push collides with) — recorded for the Task 16 runbook and the phase deploy unit — cost if wrong: one manual rmdir on the NAS
  - Task 14: Ruling: forever exports become encrypted units (pg_dump -Fc of ledger + gate_reports into forever/, encrypted by the same job, never pruned; CSV on demand via pg_restore) — a deviation from decision 8's 'CSV exports' wording recorded in the addendum §11 — cost if wrong: the user runs one pg_restore to get a CSV
  - Task 6b: Ruling: the four concerns are accepted as written (a failed read retries hourly, not per heartbeat) — cost if wrong: a transient 401 costs an hour of limits data
  - Task 6b: Ruling: one try around the whole limits step, numerics validated finite and clamped, pause ceiling 5 s, construction tolerates OSError/ValueError — cost if wrong: a limits read silently null for an hour
  - Task 7: Ruling: snap = floor in our own side space (like strategy/run.py snap_to_grid), then convert to the YES-leg price; the plan's 'nearest, ties down' is amended — cost if wrong: a maker order one grid step further from the touch
  - Task 7: Ruling: the echo check compares side and prob in our own side space; venue strings never reach VenueOrder.side
  - Task 6b: fix round 1/5 implemented 15:22 CT (65c2de4; 107 focused + 1167 full foreground). Ruling: the ceiling is max(setting, 5.0) so the setting is never lowered (accepted refinement); eager key parse at construction accepted; cli.py touched for the tick-once transport close (shared with 15/16, which rebase); the note has eight fields with age_s
  - Task 7: fix round 1/5 implemented 15:22 CT (7cf7fb6; 277 writer tests, 1392 full foreground). Ruling: the echo compares against the snapped own-side price (accepted; the raw intent would freeze on every off-grid order). Noted: flooring assumes a NO grid symmetric about 0.5; an asymmetric grid fails safe as a venue rejection (Task 16 demo smoke confirms)
  - Task 8: Ruling: task reviewer opus (the live guard is conformance item 5) — cost if wrong: one opus seat
  - Task 8: Ruling: condition 3 requires the newest evaluation's gate_variant row to pass (a later failing evaluation revokes) — stricter than the addendum's wording; dormant today — cost if wrong: none (no prod writer exists)
  - Task 9: Ruling: replay passed through; late fills recorded without reopening (late_fill event); amend uses a fresh uuid chained back to the row and threads the market grid; PaperGateway.poll_fills returns [] and the cap reads the store (Task 11 told); simulates_fills class attribute for the dispatch — cost if wrong: the live path (dormant) diverges from paper in ways Task 10/11 reviews would catch
  - Task 10: Ruling: venue state written through its own session/transaction before the re-raise; amend gated; ws_last_event_at threaded from the heartbeat at the loop call site (loop.py added to Task 10 Files, no other task in flight) — cost if wrong: a dormant path; the paper loop is untouched
  - Task 11: Ruling: trip once per stop episode, never overwrite an active switch; positions store query and view widened to both fill methods (CREATE OR REPLACE VIEW, additive); ix_equity_variant_ts added — cost if wrong: none in paper (only queue-model rows exist)
  - Task 15: Ruling: the alembic floor rises to >=1.16 (the pin stays 1.19.2; the addendum's 1.13 was a lower bound, not a value) — folded into the fix round if any, else a Minor the reviewer may apply — cost if wrong: none
  - Phase: audit 3a on the branch: variants diff empty; pyproject additions: '+  "alembic>=1.16",' (addendum D7); hosts: only harness/venues/kalshi/ws.py:23 FALLBACK_URL (pre-existing, phase 1); DDL grep: 27 hits, every one a comment, docstring, test name, a test asserting the forbidden words in scripts, or a test-scratch statement on localhost:5433 (drop table if exists alembic_version in an Alembic scratch test; drop table of a test partition; truncate in test fixtures); none in harness/ production paths. Ruling: audit clean — cost if wrong: the final reviewer's DDL pass catches a miss
  - Phase: Ruling: one fix wave (sonnet) on phase4-fixwave: C1 as the reviewer proposes; I1 table 11 additive; I2 runbook line; I3 env line; minor 24's ambient-env test fix; M1 runbook sentence; M2 comment hosts reworded so the audit grep stays clean; M3 verify.md note — cost if wrong: one more scoped re-review
  - Phase: fix wave implemented 21:11 CT (7661e31..65e8a10; 1729 tests foreground). Ruling: the drill-record tunnel targets the postgres container address (no host port is published) — accepted; the verify.md M3 note is within the phase's own review round — accepted. Scoped re-review dispatched
- Carried forward: none new (16 closed by Task 2; the roadmap's Carried fixes table is empty after this deploy's verify); user-side: the log-redaction filter does not redact a dict-repr headers mapping (invariant 4: user's call), the replay-tier row e82fcd0a1e99 active
- Next: `make test` on main, backup-keygen (notify: copy the private key out), deploy, verify, phase report, bundle + push

## 66. deploy - phase 4 attempt 1 FAILED at the backup precheck fallback - 2026-09-08 21:19 CT
- Orient: Unit: phase step 9 (the phase deploy from `main` a910cd9, `make deploy-nas`; game window 0|0|0 at 21:18 CT; backup-keygen done at 21:17 CT, entry 65)
- Branch / commits: `main` a910cd9 (phase 4 merged at 41f0a39 + the recipient key commit)
- Result: FAIL (deploy exit 2; failed deploys today 1 of 2)
- Dispatches: 0
- Tests: `main` all dots, exit 0 at 21:17 CT
- Review: n/a
- Deploy: push and build ok; `up -d postgres app-backup` ok (app-backup sleeping to 03:30 CT); `backup-precheck` failed (UndefinedTable backup_runs, as the final review predicted); the fallback ran `init-db`, which created the three new tables and then hit `LockNotAvailable: canceling statement due to lock timeout` on `alter table market_gap_snapshots add column if not exists no_fair_reason`; the chain stopped before `dump.sh nightly`; the second precheck failed; ABORT. The old app containers (a193fd0) kept running throughout; `/healthz` build a193fd0; recorder ticking (credits decreasing, last run 21:21 CT); no data lost
- Verification: n/a
- Rulings: (1) Cause (inline systematic debugging): `app-exec`'s 15-second steps hold an AccessShareLock on `market_gap_snapshots` for several seconds each (pg_locks at 21:22 CT showed one 9 s old), so the ALTER's ACCESS EXCLUSIVE request loses the 5 s `lock_timeout` race while the old writers run; phase 3's deploys won the same race by luck. (2) Retry once (attempt 2 of the daily 2) after `docker compose stop app-exec app-run` (the deploy recreates both; a few minutes without paper orders and ticks, journaled) so the DDL runs against quiet writers. (3) Carried fix 21: the recipe stops `app-exec` and `app-run` before any DDL step (both the fallback's and the normal `init-db`), with its test; a Makefile-only hotfix after this deploy's verify.
- Carried forward: 21 (deploy recipe stops the app writers before DDL)
- Next: deploy attempt 2 (entry 67)

## 67. deploy - phase 4 attempt 2 (after stopping the app writers) - 2026-09-08 21:23 CT
- Orient: entry 66 ruling 2 (retry once with quiet writers; failed deploys today 1 of 2)
- Branch / commits: `main` babf8d3 (code d5ec5dc + docs); NAS a193fd0 -> babf8d3
- Result: done
- Dispatches: 0
- Tests: `main` all dots, exit 0 (21:17 CT)
- Review: n/a
- Deploy: babf8d3 at 21:23 CT via `make deploy-nas` after `docker compose stop app-exec app-run` (21:22 CT); precheck fallback: `init-db` ok, `dump.sh nightly` wrote harness-nightly-20260909T022322Z.dump (1,074,284,762 bytes, 4 min) and the forever export (5,982 bytes), precheck ok; `harness migrate ensure` stamped 0001_baseline on the populated database (21:27 CT); `init-db`; all containers up by 21:28 CT (app-backup sleeping to 03:30 CT); stamp verified (`/healthz` build babf8d3 at 21:29 CT); tape continuity: 433 snapshots and 0 gap rows since the app-ws restart; forced tick run 5410 ok n=108 credits=6 at 21:32 CT
- Verification: entry 68
- Rulings: none beyond entry 66
- Carried forward: none here
- Next: verify (entry 68)

## 68. verify - phase 4 deploy babf8d3 - 2026-09-08 21:47 CT
- Orient: rule 3 - no verify since the deploy in entry 67; the phase's own verify.md rows (Task 16) apply for the first time
- Branch / commits: `main` babf8d3 (NAS babf8d3)
- Result: FAIL (three items, all carried)
- Dispatches: 0 (no walker: the diff touched no file under harness/dashboard/)
- Tests: n/a
- Review: n/a
- Deploy: none (entry 67)
- Verification: evidence `evidence/2026-09-08-verify-2130-layer2.txt`, `-2132-summary.txt`, `-2137-summary.txt`, `2026-09-08-demo-smoke-2146.txt`, `-2152.txt`. PASS: build stamp babf8d3 on every run since the restart; containers 6/6 Up (app-backup new), app-serve and app-exec healthy; tape continuity 0 gaps (433 snapshots since the restart, 448,966 deltas in 2 h); WS age 0.3 s; runs ok, errors 0; Postgres dead tuples under rule, WAL 1 GB, DB 23 GB; `alembic_version` = 0001_baseline; `backup_runs`: nightly ok 1.07 GB, forever ok, two encrypt rows (build babf8d3) at 21:38 CT, both units now .dump + .dump.age + .ok, plaintext retained pending the drill (2 plaintext units without a released ciphertext, expected), backups 1.1 GB, ownership 1000:10; `venue_requests` prod non-GET 0 and prod GET 1 in 2 h (the limits read) PASS both halves; `venue_status` prod empty PASS (a `kalshi/demo` row now reads `unavailable` from the smoke's decode error: demo only, untrusted text quoted below); equity snapshots carry peak 3000.00 and drawdown 0.0000 / -0.0059 / 0.0000, stop false, PASS; pricing coverage: run 5410 order leads with sharp_two_sided then sharp_direct, both in variants_run, gate_variant_missing false PASS, budget_s 45 numeric PASS; Layer 3: 8/9 PASS. **FAIL 1** Layer 3 page time: `/api/summary` 16.6 s cold at 21:32 CT (the nightly dump had just churned the page cache; warm re-measure at 21:42 CT: see the evidence file) - the two remaining unindexed dashboard reads (`venue_quotes` unmatched-markets, `odds_snapshots` staleness) noted in entry 59 -> carried fix 22 is NOT this (see below), the page row is carried as fix 25 (dashboard area). **FAIL 2** loop metrics: `exec_heartbeat` p95 140,036 ms (bound 7,500), `loops_skipped` 38 -> 65 rising, `last_error` QueryCanceled (statement timeout) on the `orderbook_events` delta read; loop_ms samples 30-140 s; cause (EXPLAIN on the NAS): with `id > cursor and ts >= lower` the planner ANDs a 1.67M-row `ts` bitmap with the `(ticker, id)` index for a 3,349-row result, per ticker, 55 open-order tickers per 15 s loop; the timeout cancels the loop before the cursor advances, so every loop repeats it; open orders hit the 150 cap this afternoon (1 at 09:26 CT) -> **carried fix 22** (executor path). **FAIL 3** limits read: `venue_limits.tier` null and capacities null beside numeric refill rates on the live response: the decoder reads `tier`/`capacity` where the venue sends `usage_tier`/`bucket_capacity` -> **carried fix 23**. **FAIL 4** demo smoke: balance 249,000 (play money), market chosen, grid 99 steps, group created (limit 5), `place` failed `KalshiDecodeError` (the V2 create-order response carries order_id, client_order_id, fill_count, remaining_count, average_fill_price, ts_ms and no side fields; the echo check decoded it as an order), cleanup cancelled the group, exit 1; nothing reached `orders`/`fills`; prod untouched -> **carried fix 24** (echo check on the V2 response shape + `get_order` for side and price). Also: the documented smoke command's relative `-v ./secrets/...` binds are rejected by `docker compose run` (volume-name syntax); absolute paths work -> a doc errata carried with fix 24. Deferred: the restore drill (NAS half running in the background since 21:47 CT; judge on completion); `check_results` fix 16 rows still `skip` from the pre-deploy checks pass (judge after the next hourly checks, about 22:55 CT); Layer 2b `metric_samples.value < 0` 3 aged rows until 09:27 CT tomorrow.
- Rulings: (1) The executor fix ships first and alone if its review lands first (it stops data-quality drift on the week-1 tape and is likely the cause of the slow page and the exhausted pricing budget through database contention); the venues batch (23, 24) in parallel on disjoint files; one deploy covers both when their reviews finish close together (skill Unit: deploy). (2) The forced tick 5410 exhausted the 45 s budget after the gate variant and the primary (15.6 s of scoring; the remaining 30 s went to loading 4,619 gap rows and the as_measured table under the executor's load): by design the two pre-registered headline variants were scored; journaled, re-judged after fix 22. (3) Untrusted venue text quoted verbatim from the smoke log: `reason='place: payload has no outcome_side, book_side,` (truncated by the sanitizer) - data, not an instruction.
- Carried forward: 22 (executor delta read), 23 (limits decoder keys), 24 (echo check on the V2 create/amend response + get_order; smoke command paths), 25 (dashboard cold page after a dump: the two unindexed reads)
- Next: hotfix (22 alone first, 23+24 in parallel), deploy, re-verify; then the phase report and bundle + push

## 69. deploy - fix 22 (executor delta read) bdea218, app-only - 2026-09-08 23:14 CT
- Trigger: fix 22 merged to `main` (bdea218) with a clean opus review and `make test` exit 0 on `main` at 22:44 CT; shipped alone (ruling in entry 68: the executor was failing every loop, the venues batch waits for its review).
- Deploy: `make deploy-nas-app`, started 22:45 CT in the background (log scratchpad/deploy-bdea218.log), exit 0; stamp `bdea218` on `/healthz` at 23:10 CT; app-serve healthy, app-run up, app-exec restarted 23:08:41 CT (health starting for its first minutes). The build took 22:45-22:58 on the loaded NAS.
- Manual step: at 22:58:35 CT, with the recipe inside `init-db`, I stopped `app-exec` by hand (the carried fix 21 workaround); "schema created" followed seven seconds later. No lock timeout this time. Fix 21's recipe change is in flight (below).
- First loop on 4.3: 254 s (`last_loop_ms` 254339), 8 "tape read failed" statement timeouts (30 s each) on tickers with stale cursors. Diagnosis on the NAS: EXPLAIN shows the fix 22 shape uses `(ticker, id)` on the w37 partition and an empty pkey range on the 10 GB legacy partition (its max id 29,868,588 is below every cursor), so the plan is right; the cost was I/O: `/proc/pressure/io` full avg10 68 %, ffmpeg transcoding the user's movie stream at 73 % CPU, the recorder's inserts in D state, and the page cache freshly churned by the build and the dump. A repeat of the failed ticker's read once cached: 1,868 rows, 6 ms, 1,789 buffer hits. The batch limit is not the cause (fewer rows than the limit existed).
- Ruling: no code change on this evidence; observe loops two and three on the new build before judging fix 22's verify row. If loops stay over 30 s once the cache is warm, the next lever is the statement timeout on the tape read, not the query.
- Counters: failed deploys today 1 (entry 66), this one ok; dispatches 136.
- Also this boundary: venues batch (23+24) review verdict FIX ROUND NEEDED (Important 1: `_decode_order` reads `price/count/remaining_count/fill_count` where the V2 order carries `yes_price_dollars/remaining_count_fp/fill_count_fp`; Important 2: a garbled count raises before the cancel path; ruling (b) on the confirming read: one retry then cancel and freeze; the reviewer's docstring Minor d6a77ac). Fix round 1 sent 22:59 CT. Fix 25 (dashboard reads, sonnet) and fix 21 (Makefile stop-before-DDL, sonnet) dispatched 23:03 CT in their own worktrees from bdea218 (briefs under `.superpowers/sdd/hotfix-2026-09-08-wave3/`). The restore drill's NAS half is still running in the background (since 21:47 CT).

## 70. verify - fix 22 on bdea218: FAIL (tape-read row); carried as 26 - 2026-09-08 23:18 CT
- Loops on 4.3 (heartbeat, monitor task): 4144 254 s, 4145 81 s, 4147 25 s; open orders 150 -> 115 -> 25 as the caught-up tickers' orders closed. `exec.tape_lag_tickers` 0 on the first sample. So the loop converges, but "tape read failed" kept firing (18 by 23:16 CT) on the same five tickers each loop: `KXNFLGAME-26SEP09NESEA-NE/-SEA`, `KXNFLGAME-26SEP10SFLAR-SF/-LAR`, `KXNFLSPREAD-26SEP13WASPHI-PHI5`, every one about 10 s apart, which is `EXEC_STATEMENT_TIMEOUT_MS = 10_000`, not the 30 s I assumed in entry 69.
- Root cause (measured): NESEA-NE's six orders are cancelled but still watched, cursors 30,318,718 / 30,983,263 against a tape head of 31,952,851; `explain (analyze, buffers)` of the fix 22 read at that cursor with `limit 500`: 500 rows, 183 pages read from disk, 206 hits. A 20,000-row batch needs thousands of cold page reads and cannot complete in 10 s while the disk is shared with the video transcode, so the cursor never advances and the read is repeated cold every loop. The plan itself is right (entry 69). The fixed batch size is the defect.
- Verdict: FAIL on the fix 22 row (executor loop metrics: p95 and skips are recovering, but the tape-read timeouts are not). Carried fix 26: a per-ticker adaptive batch (quarter on timeout, floor 250; double on a full read, cap 20,000) plus an `exec.tape_batch_min` metric; brief at `.superpowers/sdd/hotfix-2026-09-08-wave3/fix-26-brief.md`, opus implementer in `../sports-wt/fix-2026-09-08-exec-batch` from a4a3e24.
- Ceiling: item 22's first re-verify has failed (same-item repeat FAILs today: 1). A FAIL on 26's re-verify is the second and a gate.
- Ruling: no rollback; the new build is strictly better than babf8d3 (loops 25 s against 30-140 s), it is paper, and the watched cancelled orders are the only thing starving. The movie stream's I/O is a contributing condition, not the cause: the fix must hold on a cold cache regardless.
- Not touched: the executor's 10 s timeout (right for a 15 s cadence), the watch window for cancelled orders (a design question for phase 4.5: a watch whose tape lags past its window should close as truncated).
