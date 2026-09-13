# Autopilot checkpoint

Updated 2026-09-13 09:38 CT (14:38Z) by controller session sports-e7 (session_016HYJedJegPY7Fm782ESjfF) in `/home/trey/dev/sports` on Omarchy (lock held; launched via `start-herdr`). Worktrees `/home/trey/dev/sports-wt`; production `/srv/sports-harness`. Last journal: 164. Preflight done this session (`evidence/2026-09-13-preflight-0918.txt`, paper posture intact).

## Active units

- **phase 6B** (plan `docs/superpowers/plans/2026-09-11-phase6b-repair-execution.md`, ledger `.superpowers/sdd/2026-09-11-phase6b-repair-execution/progress.md`): branch `phase6b-repair-execution` head bac35ab (T10, T1, T2, T3, T4 accepted). **T5 in flight**: implementer `impl-6b-t5` (sonnet, sports-worker) dispatched 09:28 CT on worktree `../sports-wt/phase6b-t5-expiry-rejected` (branch `phase6b-t5-expiry-rejected`, base bac35ab; DB `harness_test_phase6b_t5_expiry_rejected` granted); brief `task-5-omarchy-brief.md`; report expected at the worktree's `.superpowers-report-t5.md`; 90 min timeout (chase at 10:58 CT). Next legal action: consume the report, commit the diff, package `review-bac35ab..<sha>.diff`, opus review. Remaining after T5: T6-T9, T11, T12, final review; xfail ledger: 3 regressions (4, 5, 6) + R14 marker (T7/6D).
- **hotfix batch C** (ledger `.superpowers/sdd/hotfix-2026-09-12-omarchy/progress.md`): fixes 53 round 2, 54, 55 on `fix-20260913-dashboard` (worktree `../sports-wt/fix-20260913-dashboard`, base a4b0b53; DB `harness_test_fix_20260913_dashboard` granted); implementer `impl-fix-dash` (sonnet) dispatched 09:36 CT; brief `fix-53r2-54-55-brief.md`; report expected at the worktree's `.superpowers-report-fix-dash.md`; 90 min timeout (chase at 11:06 CT). Next: commit the diff, sonnet review, controller capture of the Pulse disclosure with the branch static injected, full suite on the branch, ff-merge; deploy app-only in Tuesday's window.
- **phase 4.6** (`not planned`, U9 parallel track): plan-next next, in the gaps while the two implementers run; inputs `docs/superpowers/design/ui-revision-2026-09-12/` (`fable/DESIGN-SPEC.md`, `fable/FEASIBILITY.md`, `AFTER-FABLE.md`), roadmap "Phase 4.6" pre-loaded decisions; two `opus` design reviewers. No addendum or plan yet.
- **6D plan-next**: not started (U8 exception allows it alongside); carries 46, 48, 51, the 55 coverage half, the normalizer denominator.
- **deploy**: runtime = ebf0953 (receipt `/srv/sports-harness/releases/20260913T114210Z-ebf0953/`); main a4b0b53 ahead by docs and the controller session helper only (ruling, journal 164: no deploy). NFL block 10:20 CT Sun through Mon night; next window Tuesday.
- **verify**: journal 160 FAIL stands (walk item 19 -> batch C; item 18 data under audit). Deferred: fix 49 6 h/500 MiB at 12:47 CT (140-266 MiB so far); fix 52 daytime freshness WATCH, re-judge Tuesday outside a game window (quiet-hour half PASS); fix 50 21:08 CT; 6C (i)/(iii) 19:07 CT; fix 45 Mon 06:44 CT; 6C (v)/(vi) and wave-2 eligibility after Monday's report; 6A capsules after the extraction; walk item 9 after the first real tick.
- **6C**: planned/partial; Sun 19:00 CT discriminator (wakeup 6d524910) and Mon 09:00 CT diagnostic report (wakeup 937eca36).
- **Qwen package**: review record only; D1-D6 and the budget decision await the user.

## Pending results / subprocesses

- Agents in flight: `impl-6b-t5` (T5) and `impl-fix-dash` (batch C), both sports-worker sonnet, dispatched 09:28 / 09:36 CT. No suite running; no deploy running.
- Wakeups (session-only crons, verified by CronList at creation): dbbeeb7a Sun 12:47 CT (fix 49 row), 6d524910 Sun 19:07 CT (6C rows), b68cb9d0 Sun 21:08 CT (fix 50), 937eca36 Mon 08:57 CT (Monday duties). User-systemd durable reminders Sun 18:29:59 and Mon 07:29:59 CT.
- Fixture grant UPDATE(indisvalid) ON: harness_test_main (+_a/_b/_s), harness_test_phase6b_t5_expiry_rejected, harness_test_fix_20260913_dashboard. Revoke the branch ones after their last suite.
- Preserved: recovery branches/worktrees from the restart (fix-45-raw-events-index, fix-48-pricing-stage-order, recovery/*, restart-worker-smoke), three migration stashes. A second interactive Claude session (sports-ad) is the user's; not a controller.

## Evidence receipts

- Runtime ebf0953 all five app services, schema 0007; health at 09:17 CT `status: ok`, credits 4,902,671 of 5,000,000.
- Main suite receipts: ebf0953 3,244/6 exit 0 (`.superpowers/sdd/omarchy-loop-2026-09-12/`). Phase: T4 bac35ab 3,276/4 exit 0. No receipt for a4b0b53 (docs and controller helper only; none needed until a code change lands).
- Verify ebf0953: `evidence/2026-09-13-verify-ebf0953-0653-*`, `2026-09-13-verify-0653-*`.

## Counters and gates

- CT day Sep 13: 13 dispatches (11 before this session + impl T5 + impl batch C). Unit counters: phase 6B this session 1; hotfix batch C 1. Failed deployment acceptance: 1 (fix 48 row, carried from Sep 12). No rate-limit events. No open gates.
- Fix rounds: T5 0; batch C 0 (fix 53 cumulative 2).

## Deadlines and open acceptance

- NFL block from 10:20 CT Sun through Monday night; next deploy window Tuesday. 6C Sun 19:00 CT and Mon 09:00 CT. Fix 49 production 6 h/500 MiB judged at 12:47 CT. 6E cold-start (user LUKS) and two corrected-workload windows open. 6F awaits the user's amendment. 48/6D: markets normalizer backlog and zero current-run venue_quotes unchanged (journal 154).
- 4.6 user-side: ufw 8443 done; a paid stat provider only if the ESPN summary measurement falls short (gate 7).

## Risks and lessons

- The release script requires the full-suite receipt under `harness_test_main` at the exact HEAD; run `make test` on main last, after all docs commits, then deploy.
- Suite-slot contention: implementers plus controller suites serialize on one host slot; sequence full suites deliberately; kill a superseded suite by SIGKILL on the pytest process group.
- Worker namespace masks (.env.example, .env.nas.example, secrets/.gitkeep) are not host edits. Alembic scratch tests cannot run in the worker sandbox (socket URL); the controller's TCP suite covers them.
- The normalizer backlog remains a separate open defect (6D).
