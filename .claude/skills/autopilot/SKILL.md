---
name: autopilot
description: Use when asked to run, resume, or report on the overnight autopilot for the sportsbook harness. Executes the next roadmap unit end to end (phase plan via subagent-driven development, final review, merge, NAS deploy, live verification through ssh and Chrome), journals every decision, and stops only at the gates listed in docs/superpowers/autopilot/roadmap.md.
---

# Autopilot — sportsbook harness

The hand-driven pattern of phases 0–2 with the human's go signals replaced by standing
authorizations in `roadmap.md`. Durable state lives in committed files. Context may be
summarized at any moment; the files are the only truth, so read them first, every time.

## State files — read all of them before doing anything

| File | Holds |
|---|---|
| `docs/superpowers/autopilot/roadmap.md` | phases and status, gates, standing authorizations, deferred items, carried fixes |
| `docs/superpowers/autopilot/journal.md` | append-only log; the last two entries say where you are |
| `docs/superpowers/autopilot/verify.md` | the deploy verification contract |
| `.superpowers/sdd/<plan-basename>/progress.md` | the SDD ledger while a phase executes (git-ignored recovery map) |

Also run: `git status --short`, `git log --oneline -15`, `git branch --show-current`,
`make status-nas`. Trust the journal, the ledger, and `git log` over memory.

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
is reused, not duplicated. Announce the plan of the day in one short message, then do
not wait for a reply.

## Preflight (once per session; journal the result as a `preflight` entry)

1. Mac awake: `pmset -g | grep -E "^\s*sleep"` shows sleep prevented, else run
   `caffeinate -dims &`. The terminal and Chrome stay open.
2. Test DB: `docker ps` lists `harness-pg-test` on 5433; `pgrep -f pytest` prints nothing.
3. NAS: `make status-nas` shows four containers (five once `app-exec` exists) and a `[200]`;
   `ssh -o BatchMode=yes trey@192.168.12.228 true` succeeds without a prompt.
4. Tunnel: `ssh -N -L 8180:127.0.0.1:8180 trey@192.168.12.228` running in the background
   (start it with `run_in_background`); `curl -s -o /dev/null -w '%{http_code}' http://localhost:8180/` is 200.
5. Chrome bridge: load the Claude-in-Chrome tools (one ToolSearch call), `tabs_context_mcp`
   answers, create a tab, load `http://localhost:8180/`, take one screenshot, close the tab.
6. `secrets/` holds the four files (never read their contents); `.env.nas` exists.
7. Working tree clean on `main`.

Local plumbing failures (tunnel, Docker, a stale tab group) get fixed inline. Anything
else is a gate.

## Orient → choose the unit

Pick the first that applies:

1. **hotfix** — `roadmap.md` Carried fixes is non-empty, or the last journal entry ends in `FAIL`.
2. **verify** — the last journal entry records a deploy without a verification, a scheduled wakeup is due, or the last `verify` entry lists a deferred item whose judge-after time has passed (fold it into the next verification pass rather than running one just for it, unless nothing else is pending).
3. **phase** — the first roadmap phase with status `planned` (a committed plan) and no unmet gate.
4. **plan-next** — only if the roadmap authorizes drafting the next phase. If not, the loop is finished: write the report, notify, stop.

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
     6, 10, 13).
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
   (copy every `Ruling:` line from the ledger — exhaustive).
10. Continue to the deploy unit.

## Unit: deploy

Run inline in the controller session, never inside an agent (agents cannot surface
failures or prompts). Record `DEPLOY_SHA=$(git rev-parse --short HEAD)` first.

1. `make deploy-nas` with `run_in_background`; wait for the task notification.
2. `make status-nas`: every container `Up`, `app-serve` `(healthy)` within two minutes.
3. Freshness: `/healthz` on the NAS returns `"build": "<DEPLOY_SHA>"` (curl over ssh) and
   the dashboard header shows the same. Mismatch or `-dirty` → deploy failure.
4. On any failure: **REQUIRED SUB-SKILL:** `superpowers:systematic-debugging`, inline;
   unresolved → gate.
5. Journal a `deploy` entry (sha, time, containers, stamp check) and continue to verify.

## Unit: verify

Follow `verify.md` exactly. In brief:

1. ssh checks (the contract lists the commands and the expected values by time of day).
2. Chrome walkthrough: spawn a walker agent (`model: sonnet`) with the walker prompt in
   `verify.md`; it saves screenshots under `docs/superpowers/autopilot/evidence/` and
   returns PASS/FAIL per checklist item with paths.
3. Read the screenshots yourself with the Read tool. The walker's verdict is advisory;
   the pixels and the ssh numbers are the ground truth.
4. Journal a `verify` entry: `PASS n/m` with evidence paths, anomalies, and any item
   deferred to a scheduled wakeup (quiet hours). A FAIL adds a line to Carried fixes and
   the next unit is hotfix. Never silently pass a failed item.
5. If the time-of-day rules defer an item (first pricing run after 08:00 CT), schedule a
   wakeup for the earliest time it can be judged and journal the pending item.

## Unit: hotfix

Branch `fix-<slug>` from `main`. One implementer (`sonnet`; `opus` if it touches the
executor, pricing, or settlement) with a brief that names the failed check, the
evidence, and the covering test to write; one task reviewer; fix rounds and a scoped
re-review as in the SDD skill; full suite; `--ff-only` merge; deploy; re-verify the
failed items only. Three hotfix rounds without a passing verification is a gate.
Remove the item from Carried fixes when it passes.

## Waiting

- Agents: while children run, do local work (ledger, briefs, review packages). When idle,
  wait in bounded stretches (five to ten minutes), then reconcile: `ListAgents`, chase
  finished-but-silent children.
- Wall-clock: `ScheduleWakeup` with the exact delay to the event (for the 08:00 CT
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
- Verification: PASS n/m (evidence: <paths>) | deferred: <items + wakeup time> | not run
- Rulings: <one line each, exhaustive> | none
- Carried forward: <items added to roadmap Carried fixes> | none
- Next: <unit>
```

Entries are appended, never edited. Update `roadmap.md` statuses in the same commit.
Commit state files after every unit: `docs: autopilot journal — <unit> <slug>`.

## Gates — stop, write the report, `PushNotification`, wait

- A secret, account, credential, tier upgrade, or legal decision only the user can make.
- Scope beyond the roadmap: a new phase without authorization to draft it, a spec
  change, or scheduling a deferred item.
- Anything destructive on the NAS: dropping or truncating tables, deleting `pgdata`,
  compaction, changing retention.
- The SDD breaker leaves a load-bearing finding that blocks merge.
- A deploy failure inline debugging cannot resolve.
- Three hotfix rounds without a passing verification, or the same failed item in two
  consecutive verifications.
- Odds API 401 (credits exhausted) or the NAS unreachable for more than 15 minutes.

Everything not listed: decide, journal the ruling, keep moving.

## Report (`docs/superpowers/autopilot/reports/<date>-morning.md`)

Written when the loop stops for any reason, then `PushNotification` with one line.
Sections: what shipped (commits, tests); deploy and verification evidence (links to the
screenshots, the ssh numbers); rulings taken on your behalf (exhaustive, with cost if
wrong); gates and open questions; what the loop would do next; user-side TODOs from the
roadmap. Update the project memory file with the new status.

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
| "The user would want X, better ask" | Check Gates. Not listed → rule, journal, continue. |
| "One failed item, but mostly fine" | Journal FAIL, carry the fix, run the hotfix unit. |
