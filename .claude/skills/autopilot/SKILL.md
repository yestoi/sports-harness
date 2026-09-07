---
name: autopilot
description: Use when asked to run, resume, or report on the autopilot for the sportsbook harness. Plans and executes roadmap phases end to end (autonomous brainstorm and plan when needed, subagent-driven development, final review, merge, NAS deploy, live verification through ssh and Chrome), runs the season's operator duties, journals every decision, and stops only at the gates listed in docs/superpowers/autopilot/roadmap.md.
---

# Autopilot — sportsbook harness

The hand-driven pattern of phases 0–2 with the human's go signals replaced by standing
authorizations and pre-loaded decisions in `roadmap.md`. Durable state lives in committed
files. Context may be summarized at any moment; the files are the only truth, so read
them first, every time.

## State files — read all of them before doing anything

| File | Holds |
|---|---|
| `docs/superpowers/autopilot/roadmap.md` | phases and status, gates, standing authorizations, secrets, pre-loaded decisions per phase, operator calendar, carried fixes |
| `docs/superpowers/autopilot/journal.md` | append-only log; the last two entries say where you are |
| `docs/superpowers/autopilot/verify.md` | the deploy verification contract |
| `.superpowers/sdd/<plan-basename>/progress.md` | the SDD ledger while a phase executes (git-ignored recovery map) |

Also run: `git status --short`, `git log --oneline -15`, `git branch --show-current`,
`make status-nas`, and note the current time in America/Chicago. Trust the journal, the
ledger, and `git log` over memory.

## Kickoff (a fresh session, the way the user starts it)

```
cd ~/dev/sports && claude --dangerously-skip-permissions
/effort            # max
/autopilot
```

Then read the state files, run preflight, choose the unit, go. Phase 3's SDD workspace
(`.superpowers/sdd/2026-09-07-phase3-paper-execution/`) already holds the ledger with
the pre-flight scan, its rulings, and the 14 task briefs; the SDD skill resumes from
that ledger. A tunnel left running by an earlier session (`pgrep -f "ssh -N -L 8180"`)
is reused, not duplicated. Secrets listed in the roadmap are provisioned by the user
when convenient; never wait for them. Announce the plan of the day in one short message,
then do not wait for a reply.

## Preflight (once per session; journal the result as a `preflight` entry)

1. Mac awake: `pmset -g | grep -E "^\s*sleep"` shows sleep prevented, else run
   `caffeinate -dims &`. The terminal and Chrome stay open.
2. Test DB: `docker ps` lists `harness-pg-test` on 5433; `pgrep -f pytest` prints nothing.
3. NAS: `make status-nas` shows every container `Up` and a `[200]`;
   `ssh -o BatchMode=yes trey@192.168.12.228 true` succeeds without a prompt.
4. Tunnel: `ssh -N -L 8180:127.0.0.1:8180 trey@192.168.12.228` running in the background
   (start it with `run_in_background`); `curl -s -o /dev/null -w '%{http_code}' http://localhost:8180/` is 200.
5. Chrome bridge: load the Claude-in-Chrome tools (one ToolSearch call), `tabs_context_mcp`
   answers, create a tab, load `http://localhost:8180/`, take one screenshot, close the tab.
6. `secrets/` holds at least the four original files (never read their contents);
   `.env.nas` exists. Note which optional secrets from the roadmap are present.
7. Working tree clean on `main`.

Local plumbing failures (tunnel, Docker, a stale tab group) get fixed inline. Anything
else is a gate.

## Orient → choose the unit

Pick the first that applies:

1. **hotfix** — `roadmap.md` Carried fixes is non-empty, or the last journal entry ends in `FAIL`.
2. **verify** — the last journal entry records a deploy without a verification, a scheduled wakeup is due, or the last `verify` entry lists a deferred item whose judge-after time has passed (fold it into the next verification pass rather than running one just for it, unless nothing else is pending).
3. **operate** — a duty in the roadmap's operator calendar is due (Monday report, alias pass, post-game verification, daily watch). Duties run at unit boundaries, never mid-task.
4. **phase** — the first roadmap phase with status `planned` (a committed plan) and no unmet gate.
5. **plan-next** — the first roadmap phase with status `not planned` and no unmet gate: brainstorm and plan it autonomously (below), then run it as a phase.
6. Nothing due and nothing left to plan → operator mode: journal, `ScheduleWakeup` for the next calendar event, end the pass.

## Unit: plan-next (autonomous brainstorm → design addendum → plan)

Inputs, read before writing anything: the spec sections the roadmap names for the phase;
the roadmap's pre-loaded decisions for it; every deferred item and review minor that
touches its area (`docs/superpowers/reviews/`, the journal); the last two journal entries'
anomalies; live facts from the NAS (query what the design depends on, as the phase 3
brainstorm did); current documentation for every external API involved (`ctx7`: Kalshi,
api.weather.gov, Anthropic).

1. **REQUIRED SUB-SKILL:** `superpowers:brainstorming`, in autonomous mode: no
   `AskUserQuestion`. Every question the skill would put to the user is answered from
   the pre-loaded decisions or by your own judgment, and each answer is written into the
   addendum's final section, **Decisions taken on the user's behalf** (decision; source:
   pre-loaded | model; rationale; cost if wrong; how to reverse). Write the addendum to
   `docs/superpowers/specs/<date>-phaseN-<slug>-design.md` in the shape of the phase 3
   addendum: §0 amendments to v2, components, data, testing, ops, out of scope.
2. **Design review.** Dispatch one reviewer (`model: opus`) with the adversarial lens the
   v2 spec review used (venue practitioner, experiment design, risk and security,
   architecture): Critical and Important findings, each naming the spec section it
   contradicts. Rule on every finding in a "Rulings" list at the bottom of the addendum
   (`Ruling: <decision> — <why> — <cost if wrong>`); amend the addendum; one round only.
3. Spec self-review: placeholders, contradictions, scope, ambiguity. Fix inline.
4. **REQUIRED SUB-SKILL:** `superpowers:writing-plans` →
   `docs/superpowers/plans/<date>-phaseN-<slug>.md`. The plan's last task extends
   `docs/superpowers/autopilot/verify.md` with the phase's ssh checks and walkthrough
   items and, when it adds a secret, the Makefile's deploy push for it. Plan self-review;
   commit the addendum and the plan; roadmap status → `planned`; journal a `plan-next`
   entry listing every decision taken.
5. Continue directly into Unit: phase.

Gates inside plan-next: a decision that changes bankroll, legal or live posture, spends
real money, or needs an account action beyond dropping a secret file into `secrets/`.

## Unit: phase

Branch: `phaseN-<slug>` from `main` (implement in place, as phases 0–2 did; no worktree).

1. Journal a `phase start` entry (plan path, base commit, expected task count).
2. **REQUIRED SUB-SKILL:** `superpowers:subagent-driven-development` on the committed
   plan. Its four stop conditions map to the roadmap: a merge is pre-authorized; an
   irreversible or destructive operation, a security-sensitive action, or a plan so
   broken that every path is a guess is a gate (see Gates). Everything else is a ruling
   in the ledger: `Ruling: <decision> — <why> — <cost if wrong>`.
3. Model allocation (the phase 0–2 pattern, and the skill's own guidance):
   - implementer: `sonnet`; `haiku` when the brief contains the complete code
     (transcription); `opus` for judgment-heavy, multi-file tasks (for phase 3: Tasks 5,
     6, 10, 13; for later phases, the ones the plan-next entry names).
   - task reviewer: `sonnet`; `opus` for the tasks above.
   - scoped re-review: `haiku` for a small fix diff, `sonnet` otherwise.
   - fix rounds 1–3 resume the same implementer (`SendMessage`); rounds 4–5 a fresh
     implementer one tier up.
   - final whole-branch review: `opus`; fix wave: `sonnet` (`opus` if a finding is
     architectural); the re-review of the wave: `sonnet`.
   Always pass `model` explicitly. Never run two implementers at once (shared test DB).
4. Commit trailers on every commit use **this** session's values from the harness
   instructions (`Co-Authored-By` + `Claude-Session`); a plan that hard-codes an older
   session id is stale on that point — ledger the ruling once, at phase start.
5. Full suite before every merge and deploy: `pgrep -f pytest` empty, then
   `DATABASE_URL_TEST=postgresql+psycopg://harness:harness@localhost:5433/harness_test .venv/bin/pytest -q`,
   pristine output (no warnings, no tracebacks).
6. A plan step that says "controller: deploy this task now" (phase 3 Task 1) is honoured
   mid-phase: run the deploy unit from the branch, verify with `verify.md` §Task-specific,
   journal it. Restarting `app-ws` costs a few seconds of WebSocket events; note it.
7. When the final review is clean (or residuals are parked with rulings): archive the
   ledger, the final review, and the fix report under `docs/superpowers/reviews/` as
   `<date>-phaseN-{sdd-ledger,final-review,final-fixes}.md`; commit `docs: archive phase N ...`.
8. **REQUIRED SUB-SKILL:** `superpowers:finishing-a-development-branch`. Its question is
   pre-answered by the roadmap: merge locally with `git merge --ff-only`, delete the
   branch, no PR. Then delete the SDD workspace.
9. Journal `phase done` with the commit range, test count, and the rulings roll-up
   (copy every `Ruling:` line from the ledger — exhaustive); roadmap status → `done`.
10. Continue to the deploy unit, then verify, then write the phase report (below).

## Unit: deploy

Run inline in the controller session, never inside an agent (agents cannot surface
failures or prompts). Record `DEPLOY_SHA=$(git rev-parse --short HEAD)` first.

1. `make deploy-nas` with `run_in_background`; wait for the task notification.
2. `make status-nas`: every container `Up`, `app-serve` `(healthy)` within three minutes.
3. Freshness: `/healthz` on the NAS returns `"build": "<DEPLOY_SHA>"` (curl over ssh) and
   the dashboard header shows the same. Mismatch or `-dirty` → deploy failure.
4. On any failure: **REQUIRED SUB-SKILL:** `superpowers:systematic-debugging`, inline;
   unresolved → gate.
5. Journal a `deploy` entry (sha, time, containers, stamp check) and continue to verify.

## Unit: verify

Follow `verify.md` exactly. In brief:

1. ssh checks (the contract lists the commands and the expected values by time of day).
2. Chrome walkthrough: spawn a walker agent (`model: sonnet`) with the walker prompt in
   `verify.md`; it returns PASS/FAIL per checklist item with screenshot paths; copy the
   screenshots into `docs/superpowers/autopilot/evidence/` with the contract's names.
3. Read the screenshots yourself with the Read tool. The walker's verdict is advisory;
   the pixels and the ssh numbers are the ground truth.
4. Journal a `verify` entry: `PASS n/m` with evidence paths, anomalies, and any item
   deferred by the time-of-day rules with its judge-after time. A FAIL adds a line to
   Carried fixes and the next unit is hotfix. Never silently pass a failed item.
5. When the roadmap's optional secrets exist, run the checks that depend on them (the
   demo smoke, the veto dry run) as part of this unit.

## Unit: hotfix

Branch `fix-<slug>` from `main`. One implementer (`sonnet`; `opus` if it touches the
executor, pricing, settlement, or the venue adapter) with a brief that names the failed
check, the evidence, and the covering test to write; one task reviewer; fix rounds and a
scoped re-review as in the SDD skill; full suite; `--ff-only` merge; deploy; re-verify the
failed items only. Three hotfix rounds without a passing verification is a gate. Remove
the item from Carried fixes when it passes.

## Unit: operate (season operator duties, from the roadmap calendar)

- **Monday 09:00 CT:** run `harness report --week N` and `harness gate` on the NAS
  (`docker compose run --rm app-run ...`), copy the report into `docs/reports/`, commit,
  one-line `PushNotification` with the headline numbers.
- **Alias pass** (Monday 09:30 CT, and the morning after a Thursday or Friday game): run
  `harness match-report` on the NAS; an implementer adds aliases for the top unmatched
  names to `harness/matching/aliases_manual.yaml` on `fix-aliases-<date>` with a test per
  alias; reviewer; merge; deploy; confirm the match rate rose in the next report.
- **Post-game verification** (the morning after every game day): the verify unit in
  full, then the hotfix loop.
- **Daily watch** (09:00 CT): credits remaining, database size vs budget, executor
  heartbeat, error lines, kill-switch state → one journal line; anomalies → carried fixes.
- **Tuesday 09:30 CT** (once phase 5a ships): confirm the futures snapshot job ran.

Between duties: `ScheduleWakeup` for the next calendar event, `reason` naming it. Duties
never pre-empt a running task; they run at unit boundaries.

## Waiting

- Agents: while children run, do local work (ledger, briefs, review packages). When idle,
  wait in bounded stretches (five to ten minutes), then reconcile: `ListAgents`, chase
  finished-but-silent children.
- Wall-clock: `ScheduleWakeup` with the exact delay to the event (for the first weekday
  pricing run: wake at 08:10 CT), `reason` naming the event. Never poll.
- Background commands (`deploy`, long waits): `run_in_background`, then act on the
  notification.

## Journal entry format

```
## <N>. <unit> — <slug> — <YYYY-MM-DD HH:MM CT>
- Branch / commits: <branch> <base7>..<head7> | n/a
- Result: done | FAIL | gated: <why>
- Tests: <count> passed, pristine | n/a
- Review: clean | <n> fix rounds | parked: <items>
- Deploy: <sha> at <HH:MM CT>, stamp verified | none
- Verification: PASS n/m (evidence: <paths>) | deferred: <items + judge-after time> | not run
- Rulings: <one line each, exhaustive> | none
- Carried forward: <items added to roadmap Carried fixes> | none
- Next: <unit>
```

Entries are appended, never edited. Update `roadmap.md` statuses in the same commit.
Commit state files after every unit: `docs: autopilot journal — <unit> <slug>`.

## Gates — stop, write the report, `PushNotification`, wait

- A credential, account, tier upgrade, or legal decision only the user can make, other
  than a secret file the roadmap already lists (those are conditional, never blocking).
- Scope beyond the roadmap: work not in the phase table, the deferred list, or the
  operator calendar; a spec change outside an addendum's §0 amendments; anything Novig;
  live trading; bankroll changes; real money.
- Anything destructive on the NAS: dropping or truncating tables, deleting `pgdata`,
  compaction, changing retention.
- The SDD breaker leaves a load-bearing finding that blocks merge.
- A deploy failure inline debugging cannot resolve.
- Three hotfix rounds without a passing verification, or the same failed item in two
  consecutive verifications.
- Odds API 401 (credits exhausted) or the NAS unreachable for more than 15 minutes.

Everything not listed: decide, journal the ruling, keep moving.

## Reports (`docs/superpowers/autopilot/reports/<date>-<slug>.md`)

Written after every phase (`phaseN`), after every Monday report (`week-NN`), and whenever
the loop stops (`stopped`), each followed by a one-line `PushNotification`. Sections: what
shipped (commits, tests); deploy and verification evidence (screenshot links, the ssh
numbers); decisions and rulings taken on your behalf (exhaustive, with cost if wrong);
gates and open questions; what the loop does next; user-side TODOs from the roadmap.
Update the project memory file with the new status.

## Red flags — stop, you are about to break the loop

| Thought | Reality |
|---|---|
| "I remember where we are" | Read the journal and the ledger. |
| "The walker said PASS" | Read the PNGs and the ssh numbers. |
| "Quiet hours, nothing to verify" | Verify what quiet hours allow; schedule the wakeup for the rest. |
| "I'll deploy from inside an agent" | Deploy inline only. |
| "The fix is tiny, skip the re-review" | Every fix round ends with a scoped re-review. |
| "I'll patch this myself" | Dispatch; controller fixes skip review. |
| "The suite was green an hour ago" | `pgrep -f pytest` empty, full suite, before every merge and deploy. |
| "The user would want X, better ask" | Check Gates and the pre-loaded decisions. Not listed → rule, journal, continue. |
| "One failed item, but mostly fine" | Journal FAIL, carry the fix, run the hotfix unit. |
| "This secret is missing, I'll wait" | Build it conditional on the file; the roadmap says never block on a listed secret. |
| "A quick brainstorm question won't hurt" | plan-next takes no questions; decide, record it under Decisions taken, move on. |
