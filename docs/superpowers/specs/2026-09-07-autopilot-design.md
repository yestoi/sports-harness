# Autopilot — Design

Date: 2026-09-07. Status: approved in chat (authorizations chosen by the user at 00:45 CT).

## 1. Purpose

Replace the human "go" signals that drove phases 0–2 with a loop that runs the same
process unattended: execute the next committed phase plan, review it, merge it, deploy
it to the NAS, verify the live system (data over ssh and pixels through Chrome), fix
what verification finds, journal every decision, and stop only at gates the user has
not pre-authorized.

## 2. What the transcripts showed (phases 0–2, 2026-09-06/07)

- 13.5 hours, 29 human-typed prompts, 99 agent dispatches (66 sonnet, 18 haiku,
  9 opus), 35 follow-up messages to running agents, 21 questions to the user of which
  16 were design questions during brainstorming.
- The user's prompts were go signals, status checks, environment facts (NTP, secrets),
  and four real decisions (legal posture, scope cuts, Odds API tier, demo vs production
  key). During execution the controller stopped for the user only to choose an
  execution style, to merge a finished branch, to decide whether to fix newly found
  issues now, and to approve a hotfix. Every other conflict was a ledger ruling.
- Per phase: brainstorm → design addendum → written plan → subagent-driven development
  on a `phaseN-*` branch (implementer → task reviewer → fix rounds to the same
  implementer → scoped re-review, ledger with rulings) → final whole-branch review on
  opus → one fix wave → re-review → archive ledger and reviews under
  `docs/superpowers/reviews/` → full suite → fast-forward merge → `make deploy-nas` →
  checks over ssh → hotfix branches (`fix-*`) → next phase.
- Verification was ssh-only; the dashboard was never opened in a browser. Both
  post-deploy bugs (zero candidates from the staleness rule; dashboard statement
  timeouts) were found by querying live data and logs.

## 3. Design

### 3.1 Shape

A project skill, `.claude/skills/autopilot/SKILL.md`, plus committed state files under
`docs/superpowers/autopilot/`, run by an interactive Claude Code session that already
has bypass permissions, agent teams, the Claude-in-Chrome bridge, and the same skills
the hand-driven sessions used. Files are the only truth: context may be summarized at
any time, and a restarted session resumes from them (`/autopilot`).

Alternatives considered: a headless wrapper (`claude -p` per iteration under
`caffeinate`) is more crash-proof but unproven with the Chrome extension and loses
teammate context every iteration; a Workflow script is deterministic but the
development loop is judgment-driven (rulings, fix rounds, adjudication). The wrapper is
the documented restart path, not the primary.

### 3.2 State files

| File | Holds |
|---|---|
| `roadmap.md` | phases from spec §15 with status and plan path; each phase's gate; the user's standing authorizations (dated); deferred items the loop may not schedule on its own; user-side TODOs; carried fixes |
| `journal.md` | append-only, one entry per unit of work (preflight, phase, deploy, verify, hotfix, report) with a rulings roll-up |
| `verify.md` | the deploy verification contract: freshness stamp, ssh checks, Chrome walkthrough checklist, time-of-day expectations, verdict rules |
| `evidence/`, `reports/` | walkthrough screenshots; morning reports |

The SDD ledger (`.superpowers/sdd/<plan>/progress.md`) is unchanged and remains the
recovery map while a phase executes; at phase end it is archived under
`docs/superpowers/reviews/` exactly as before.

### 3.3 The loop

Units of work, chosen in priority order each pass: hotfix (carried fixes or a failed
verification), verify (deployed but unverified, or a scheduled wakeup is due), phase
(first roadmap phase with a committed plan and no unmet gate), plan-next (only if
authorized; otherwise the loop reports and stops).

Each pass: orient (read the three files, git state, NAS status) → preflight once per
session → execute the unit → deploy → verify → hotfix up to three rounds → journal →
next pass. A phase runs exactly the transcript pattern through
`superpowers:subagent-driven-development` with the same model allocation (sonnet
implementers and reviewers, haiku for transcription tasks and small re-reviews, opus for
judgment-heavy tasks and every final review). The points where the controller used to
stop for the user are pre-answered by the roadmap's authorizations. Wall-clock waits
(the first pricing run after quiet hours) use scheduled wakeups, never polling.

### 3.4 Verification contract (summary; the full contract is `verify.md`)

1. Freshness first: the dashboard's build stamp must equal the deployed commit; a
   mismatch is a deploy failure, never a walkthrough verdict.
2. Live data over ssh: containers healthy, `/healthz` 200 (or quiet-hours `skipped`),
   runs advancing, executor heartbeat advancing, zero ERROR log lines in the last ten
   minutes, settlement/benchmark/order counts consistent with the time of day, database
   size.
3. Chrome walkthrough through the ssh tunnel, read-only: a walker agent screenshots
   every dashboard section; the controller judges the pixels itself.
4. The kill switch is observed, never exercised (declined by the user on 2026-09-07).
5. Time-of-day expectations are explicit so quiet-hours states are not scored as
   failures.

### 3.5 Build stamp

`make deploy-nas` appends `BUILD_SHA=<short sha>[-dirty]` and `BUILD_TIME=<utc>` to the
env file it pushes; the dashboard header shows the build and `/healthz` returns it.
One test. This makes "did the deploy land" a fact.

### 3.6 Gates (stop, notify, wait)

Anything needing a secret, account, or legal decision from the user; a change to the
spec's scope beyond what the roadmap authorizes; any destructive action on NAS data;
the SDD breaker leaving a load-bearing finding unresolved; a deploy failure inline
debugging cannot resolve; three hotfix rounds without a passing verification; Odds API
credits exhausted (401). Everything else is a recorded ruling.

### 3.7 Authorizations (2026-09-07 00:45 CT)

- Merge to `main` (fast-forward after a pristine full suite) and `make deploy-nas`
  without asking: **yes**.
- Exercise the kill switch during verification: **no** (observe only).
- Draft the phase 4 design and plan after phase 3: **no** — after phase 3 is verified
  the loop reports and stops.
- Continue into credential-free phase 5 items if phase 4 is blocked: **no**.

### 3.8 Resilience

Files as truth; journal and ledger before every long step; `caffeinate`; the Mac is on
AC with sleep disabled; the tunnel and Chrome bridge are re-checked before each
walkthrough; a push notification is sent when the loop stops for any reason.

## 4. Out of scope

Live orders, secrets handling beyond `make deploy-nas`, VPN or location changes, the
legal decision, spec changes, and any NAS data deletion or compaction.
