# Autopilot checkpoint

Updated 2026-09-15 07:17 CT (12:17Z) by controller session sports-0b (tmux `sports-autopilot`, flock held; PID 1894063) in `/home/trey/dev/sports` on Omarchy. Last journal entry: 227. Paper-only. Runtime build: **0d04804** (fix 69/70 full release 02:05-02:07 CT, journal 222). Main: 0d04804 + docs (deploy trigger diff empty). origin/main pushed at this checkpoint (U7 ahead of a LUKS reboot).

## Right now: the 6E cold-start stop is done; the host reboot is the user's (journal 226-227)

- The production stack was stopped cleanly 07:11:22-07:16:23 CT (app-backup Exited 137 after the stop timeout, a 6E acceptance note). **Nothing is running on /srv/sports-harness until the reboot or the 07:50 CT backstop.**
- Expected: the user reboots at the console, unlocks LUKS, `sports-harness.service` brings the stack up, the user relaunches per Kickoff. This controller session dies with the reboot; that is expected, not a missed unit.
- New session's preflight must record the cold-start evidence for 6E: boot time (`uptime -s`), container start order/times (`docker ps`, journalctl for sports-harness.service), first recorder run at the new boot and boot-to-first-healthy-tick, app-backup state, RSS after the cold cache, the tape gap from 07:11 CT. Write it to evidence/2026-09-15-coldstart-<HHMM>.txt and journal it; roadmap 6E row gains "cold-start observed <date>" only when the loop judges the evidence.
- Backstop: reminder file ~/.cache/sports-harness/reminders/2026091502.txt (written at arm time), timer sports-reminder-2026091502 at 07:49 CT, session wakeup 07:50 CT. If uptime has not reset by then and this session is alive: `/srv/sports-harness/sports-compose up -d --no-build --pull never`, journal the missed slot, continue with batch A. If a new session finds the stack down and no reboot happened: same command, journal, continue.

## Order of work after the cold start (journal 224 item 17; unchanged)

1. Hotfix batch A (one release, app-only unless the diff says otherwise): item 2 cutoff constant 2026-09-15T05:23:44Z with the checks.py:40 and verify.md:514 comments; item 3 `_parse_run_range` open-ended form and the pre-boundary test; item 4 `gone` from open observation rows (sonnet impl, opus review); item 8 spec amendment 0.19 (`book_unreadable`, loop-owned); item 13 Amendment 6's run 17016 sentence; item 15 runbook note on the restart unit; item 14 (vocabulary in corrections.py:156 and the audit document) and item 16's remaining journal reads ride it.
2. Hotfix batch B (one release): item 9's fix 71 narrowing (application_name per service, drain by listed pid and stopped-service names, partition children as bulk, healer lock_timeout, dump-in-progress refusal); opus impl, sonnet review.
3. Row 72 batch (own release, full recipe: revision 0013 `orders.nw_executor_version`): opus impl, opus review; Amendment 6's sub-population sentence; verify.md Layer 2b's two narrowed queries read 0 / 0 (ids not in evidence/2026-09-15-row72-ids.txt).
4. Verify after each release: journal 219's deferred rows (fix 64's check row, the two cutoff-bounded checks and `intents_without_order_or_skip`, the 01:00-08:00 open-interval rule, c1066b5-era orphan intents), item 12's exec-health windows on 0d04804 (02:30-06:30 CT vs ca30ed1's 23:38-00:23 CT and Monday's quiet hours), item 16's tape-gap read for 23:34-23:37 CT Sep 14, durable re-reads of rows 49 and 68, the 6B by-cause row.
5. Operate: daily 09:00 CT line; Tue 09:30 CT futures snapshot check (the job runs at 09:00 CT: confirm it ran after the reboot); the storage retention proposal (item 11, decision by 2026-09-22), no execution. 6D acceptance rows and 4.6 T18b/T19 at the first NFL window (next kickoff Thu 2026-09-17 18:30 CT). The user does the LAN files (roadmap TODO line 567).

## Active units

- **operate (6E cold-start)**: stop done 07:16 CT (journal 227); evidence pending in the new session.
- **hotfix batch A**: not started; ledger to create at .superpowers/sdd/hotfix-2026-09-15/progress.md (append; the fix 71 and fix 69/70 history is there). Worker smoke (MCP inventory, sandbox/DB negative checks, screenshot delivery) must run before the first dispatch of the new session.
- **phase 6D**: on main (ffbecd5), released b69b880 (journal 221); ledger .superpowers/sdd/2026-09-13-phase6d-sustained-evaluation/progress.md, dispatches 30. Remaining: verify.md 6D block acceptance rows at the first game window, §4 read-backs at real ticks, exec-health baseline retaken on 0d04804 (item 12), ledger re-archive and final review (docs/superpowers/reviews/2026-09-13-phase6d-* is stale by 9 ledger lines), then roadmap `done`; policy comparison is the user's adoption decision; worktree phase6d-merge-main removable after acceptance.
- **phase 4.6**: on main, released c1066b5. Remaining: T18b (watched NFL-window queries), T19 (verification rows), walkthrough items; the user's three LAN files.
- **6C**: planned/partial; closure names both funnel units as delivered by 6D; counterfactual fills separate by `fills.id > 1878` (spec 0.17).
- **6B**: done (journal 216); remaining on main: batch A items, the row 72 batch, verify §3 rows judged at the first game window.

## Pending results / subprocesses

- Suites: none. Agents: none. Wakeups: 07:50 CT backstop (session-only) plus the durable timer above; nothing else armed.
- Latest receipts (~/.cache/sports-harness/test-state/): fix-20260915-dirty-intervals 0d04804 (exit 0, pristine, release tree = deployed tree); phase6d-merge-main ffbecd5 (exit 0); main e17d0f5 (exit 0).
- Fixture grant UPDATE(indisvalid) ON: harness_test_main (+ shards), phase6d_merge_main, phase6d_merge_review. Fix-64/fix-71/fix-69-70 grants revoked or their databases gone.
- Worktrees: phase6d-merge-main (ffbecd5) plus the older recovery/fix45/fix48/fix52/worker-smoke set under ../sports-wt (preserve until the recovery handoff is accepted).
- Day counters (CT) Sep 15: dispatches 4, failed deploys 1 (row 72 verify FAIL; release stands), implementers running 0.
- Peer sessions sports-f6 and herdr-autopilot-f6 (idle, unlocked): not controllers.

## Rulings landed (journal 199-212, 224, 226)

User-side closed: NAS key, age key off-host, Anthropic account limits, Kalshi read scope, runs 14485/14486 stay, gate criterion 8 stays, R4 window rule, cutoff constant = 05:23:44Z (journal 224 item 2), row 64 authorized. Journal 224: row 72 option (b) with spec 0.17; RFQ0 stays; fix 71 ratified standing; walkthrough items 14/18 exempt from the twice-running clause; storage retention decision by 2026-09-22; 6B exec-health unattributable on c1066b5; Amendment 6 range (1, 17016) stays; order 157 vocabulary split (spec 0.18); host restart unit a plain oneshot; U7 governs pushes. Journal 226: the 6E cold-start timing is the loop's; taken 2026-09-15 07:11 CT.

## Evidence receipts

- Preflight: evidence/2026-09-15-preflight-0709.txt (PASS, paper posture intact, 0d04804).
- Releases: /srv/sports-harness/releases/20260915T070539Z-0d04804 (full, healthy); 20260915T062147Z-b69b880; 20260915T060556Z-e17d0f5 (app); 20260915T052344Z-c1066b5 (full); 20260915T043416Z-f28cf4d (failed-old-apps-restored). Boundary: evidence/2026-09-15-release-boundary-6b.txt.
- Verify (journal 219): evidence/2026-09-15-verify-*.txt and the walkthrough captures; fix 69/70: evidence/2026-09-15-verify-fix6970-0208.txt; row 72: evidence/2026-09-15-row72-ids.txt; 6D: evidence/2026-09-15-predeploy-baseline-6d.txt, -verify-6d-*.txt. Review of record: https://claude.ai/artifact/3VT5WfN4fWPXXkSNCGXAE2 (journal 223).

## Standing constraints (unchanged)

Paper-only; no live posture; no non-GET venue code; invariant 5 (no DROP/RENAME/TRUNCATE/DELETE/compaction/retention by code or hand; the loop proposes, the user executes); no new outbound hosts; git pushes only as U7 directs (main and the current phase branch after every phase and every Monday; never pull, rebase onto the remote, open PRs or push task worktree branches; creating a remote stays gate 8); secrets never printed; workers sandboxed without production access; ceilings and model rules per the skill.
