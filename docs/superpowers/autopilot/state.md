# Autopilot checkpoint

Updated 2026-09-13 09:12 CT (14:12Z) by a user-directed review/setup session (session_01S4HobsJyxi384dB3eDEhEG) in `/home/trey/dev/sports` on Omarchy; not a controller. The controller (sports-09) STOPPED at the user's request at 07:16 CT (journal 161) after the release, its verification and Task 4. Worktrees `/home/trey/dev/sports-wt`; production `/srv/sports-harness`. Last journal: 162 (PRs #1/#2 merged, phase 4.6 added). Stopped report `reports/2026-09-13-stopped.md`. No Claude controller is running; `~/.cache/sports-harness/controller.lock` is a stale zero-byte file from sports-09 (no tmux server); the session helper replaces it.

## Active units (all idle)

- **phase 6B** (plan `docs/superpowers/plans/2026-09-11-phase6b-repair-execution.md`, ledger `.superpowers/sdd/2026-09-11-phase6b-repair-execution/progress.md`): branch `phase6b-repair-execution` head bac35ab (T10, T1, T2, T3, T4 accepted). **Next legal action: dispatch T5** from `task-5-omarchy-brief.md` (create `../sports-wt/phase6b-t5-expiry-rejected` from the phase head, DB `harness_test_phase6b_t5_expiry_rejected` + `_a/_b/_s` with the indisvalid grant; model sonnet). Remaining after T5: T6-T9, T11, T12, final review; xfail ledger: 3 in `tests/test_execution_regressions.py` (cases 4, 5, 6) plus the R14 marker in `tests/test_replay_execute.py` (owner Task 7 / 6D). Whole-branch list in the ledger. The phase branch is based on ebf0953-era main; main has moved by docs-only commits (rebase is clean by construction: no shared files).
- **phase 4.6** (new, journal 162): `not planned`, roadmap row after 4.5, pre-loaded decisions subsection "Phase 4.6", decision U9. Brief: `docs/superpowers/design/ui-revision-2026-09-12/` (`fable/DESIGN-SPEC.md`, `fable/FEASIBILITY.md`, `AFTER-FABLE.md`). Parallel track: plan-next when the ceiling allows; 6B, the 6C duties and hotfixes keep priority. Two `opus` design reviewers. No addendum or plan exists yet.
- **hotfix ledger** `.superpowers/sdd/hotfix-2026-09-12-omarchy/progress.md`: fix 53 merged 6f0b5b9 and deployed (row reopened on walk item 19); fix 49 merged ebf0953 and deployed (6 h row after 12:44 CT). Carried, not started: 54 (Gate reading units), 55 (legacy signals label), 53 round 2 (disclosure rendering), 51 → 6D.
- **deploy**: runtime = ebf0953 (full release 06:42-06:44 CT, receipt `/srv/sports-harness/releases/20260913T114210Z-ebf0953/`); main 5d13cdc is ahead by docs-only commits (journal/state/roadmap, PR #2 b10fa8e, PR #1 d9702a2..5d13cdc): the deploy trigger diff excluding docs, markdown and `.claude` is empty. NFL block from 10:20 CT Sun through Monday night; next deploy window Tuesday.
- **verify**: journal 160 FAIL (walk items 18 data / 19 fix 53 rendering; five invariants under audit unchanged); all release-specific rows PASS. Deferred with judge-after: fix 52 daytime freshness at the 09:05 CT pass; fix 49 6 h/500 MiB after 12:44 CT; fix 50 21:05 CT; 6C (i)/(iii) Sunday 19:00-23:59 CT; fix 45 24 h Mon 06:44 CT; 6C (v)/(vi) and wave-2 eligibility lines after Monday's report; 6A capsules after the extraction; walk item 9 after the first real tick.
- **6C**: planned/partial; Sun 19:00 CT discriminator and Mon 09:00 CT diagnostic report still due (no controller running: see Deadlines).
- **6D plan-next**: not started; inputs pre-read (ROADMAP.md §6D, backlog dispositions); carries 46, 48, 51, the 55 coverage half, the normalizer denominator.
- **Qwen adoption package** (PR #2, merged b10fa8e): a review record only. No route, budget, skill amendment or paid call is authorized; D1–D6 and the dated budget decision await the user (journal 162, PR #2 review comment).

## Pending results / subprocesses

- None. No agents in flight (the two review forks of journal 162 are consumed). No suite running; no deploy running.
- Wakeups: none survive the stopped session (session-only crons 762f2c56 Sun 09:05, 0d81be85 Sun 19:07, bfcb0b14 Mon 08:57 died at the stop). User-systemd reminders Sun 18:29:59 and Mon 07:29:59 CT still fire and launch nothing (Sun 02:49:59 and 09:29:59 have passed or are passing).
- Fixture grant UPDATE(indisvalid) ON: harness_test_main (+_a,_b,_s) only. All branch databases revoked. (Revoke main's before a non-controller use; re-grant before the next main suite.)
- Preserved: recovery branches/worktrees from the restart (fix-45-raw-events-index, fix-48-pricing-stage-order, recovery/*, restart-worker-smoke), three migration stashes. Local branch `review/qwen-autopilot-adoption` deleted after its merge; `origin/design/ui-revision-2026-09-12` and `origin/review/qwen-autopilot-adoption` remain on the remote.

## Evidence receipts

- Runtime ebf0953 all five app services (image sha256:c8ec17eb…), schema 0007; receipt healthy 11:42:10-11:44:01Z; first natural tick 13951 06:43:57 CT; health at 08:59 CT `status: ok`, credits 4,902,925 of 5,000,000.
- Main suite receipts: 9a85a92 run 2 3,240/6 exit 0; ebf0953 3,244/6 exit 0 (`.superpowers/sdd/omarchy-loop-2026-09-12/main-full-*.log`, `-receipt.json`). Branch: fix 53 b362960 3,240/6; fix 49 ebf0953 3,244/6; T3 13f891b 3,272/5; T4 bac35ab 3,276/4 (all exit 0, clean). No receipt exists for 5d13cdc (docs-only; none needed until a code change lands).
- Verify ebf0953: `evidence/2026-09-13-verify-ebf0953-0653-{layer2,layer2b,summary}.txt`, `2026-09-13-verify-0653-*` (24 PNG, browser.json, walker report).

## Counters and gates

- CT day Sep 12: 51 dispatches; CT day Sep 13: 11 (rev-6b-t3, rerev-6b-t3-1/2, impl-6b-t4, rev-fix49, rerev-fix49-1/3, rev-6b-t4, walker, two PR code-review forks). Failed deployment acceptance: 1 (fix 48 row, carried from Sep 12). No rate-limit events. No open gates.
- Fix rounds: fix 53 2; fix 49 3; T3 2; T4 0.

## Deadlines and open acceptance

- NFL block from 10:20 CT Sun through Monday night; next deploy window Tuesday. 6C Sun 19:00 CT and Mon 09:00 CT (diagnostic report duty; operate.md). Fix 49 original acceptance: 20-tick synthetic <5 % holds in the suite; production 6 h/500 MiB judged from `recorder.rss_mb` after 12:44 CT. 6E cold-start (user LUKS) and two corrected-workload windows open. 6F awaits the user's amendment. Live evidence for 48/6D: markets normalizer backlog and zero current-run venue_quotes unchanged (journal 154).
- 4.6 user-side items: ufw rule for LAN port 8443 done (user, 2026-09-13; recorded in the 4.6 decisions item 4); a paid stat provider only if the ESPN summary measurement falls short (gate 7).

## Risks and lessons

- The release script requires the full-suite receipt under `harness_test_main` at the exact HEAD; an identical tree under another branch name is refused. Run `make test` on main last, after all docs commits, then deploy.
- Suite-slot contention: three implementers plus controller suites serialize on one host slot; a 30-minute full suite blocks every targeted run. Sequence full suites deliberately; kill a superseded suite by SIGKILL on the pytest process group (SIGTERM/INT/HUP do not stop it mid-run).
- Worker namespace masks (.env.example, .env.nas.example, secrets/.gitkeep) never applied; a reviewer running inside the sandbox sees them as working-tree changes (journal 162). Alembic scratch tests cannot run in the worker sandbox (socket URL); the controller's TCP suite covers them.
- Fix 49 root cause: `.scalars().all()` on a 500-row jsonb batch; the normalizer backlog remains a separate open defect (6D).
