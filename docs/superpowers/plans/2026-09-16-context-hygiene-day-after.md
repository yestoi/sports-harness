# Context hygiene: the day-after measurement (user-directed follow-up)

Start a fresh Claude session in `/home/trey/dev/sports` and paste: "Execute
docs/superpowers/plans/2026-09-16-context-hygiene-day-after.md. This is a user-directed review
session: do not run /autopilot, preflight or any operate duty." The controller may keep running;
everything here is read-only except one journal entry and, on your ruling, the launcher line.

Authority: spec `docs/superpowers/specs/2026-09-15-context-hygiene-design.md` section 3.5 (the
measurement), section 7 item 10 (acceptance), and the migration `repair` entry (journal 247).
The loop never runs `usage.py` and never changes the launcher; both are yours.

## When

After the controller's first full CT day at `--autocompact 300k`. The launcher changed in commit
ec01ada (merged to main b60a603, 2026-09-15). If the controller was relaunched the evening of
2026-09-15, the first full day is Wed 2026-09-16 and this runs Thu 2026-09-17 morning; otherwise the
first CT day the controller ran from its morning start through its evening duties.

## Steps

1. Confirm the controller ran at 300k all day: `grep -n 'autocompact' scripts/autopilot-session.sh`
   reads `300k`, and the first journal entry of that day (a `preflight`) is dated that day.
2. Run the measurement (read-only; it prints numbers and session ids, never message text):

   ```
   python3 .claude/skills/autopilot/scripts/usage.py --since 2026-09-16
   ```

   Keep the whole output. The rows that matter are the `<date> day:` lines for the first full day
   and the per-session rows above them (sessions with at least 20 assistant turns; controller
   sessions have hundreds or thousands).
3. Decide whether the day is conclusive (spec 3.5). It is **inconclusive**, not a pass or a fail, if
   any of these hold for that CT day:
   - fewer than 10 dispatches: sum the `- Dispatches:` lines of that day's journal entries, or read
     the day counter in `state.md` `Counters and deadlines`;
   - no `verify` entry that day: `grep -nE '^## [0-9]+\. verify' docs/superpowers/autopilot/journal.md | grep <date>`;
   - the day was dominated by a gate or a rate-limit pause (a `gate` entry, or a `paused` entry,
     covering most of the working hours).
4. If conclusive, judge the day row against the criteria, pooled over that day's sessions with at
   least 20 turns (the `day:` line already pools them):
   - average context per turn **at most 200,000** (`avg context/turn`);
   - **at most 6.0 compactions per 1,000 turns** (the parenthesis after `compactions`).
   Baseline for comparison (spec section 1): 263k per turn, 2.35 per 1,000 turns at 500k.
   Also note the context at which compaction actually fired (the `k` values in each session's
   `compactions (...)` column; at 500k it fired near 465k, so expect roughly 270-290k).
5. Record one journal entry, as the review session, in the recording grammar (recording.md), then run
   `python3 .claude/skills/autopilot/scripts/context.py check` and commit with the journal alone
   (`docs: autopilot journal - decision usage.py day-after measurement`). Shape:

   ```
   ## <N>. decision - usage.py day-after measurement at 300k - <YYYY-MM-DD HH:MM> CT

   > <the user's ruling, verbatim>

   - Measurement: <date> day: turns N, compactions N (x.x per 1,000), avg context/turn N, cache-read N, output N, journal entries N; compaction fired at <values>k; sessions <ids and turns>
   - Verdict: pass | miss (which criterion) | inconclusive (which condition)
   - Applied: <300k stays | launcher set to 350k | launcher reverted to 500k>
   - Result: recorded
   - Next: <the loop continues unchanged | relaunch after the launcher edit>
   ```

   Paste the `usage.py` table into `docs/superpowers/autopilot/evidence/<date>-usage-300k.txt` and
   cite it from the entry rather than copying rows into the body (3,000-character cap).
6. Your ruling:
   - **Pass**: 300k stays. Nothing else changes.
   - **Miss** on either criterion: choose **350k** or **revert to 500k**. Edit
     `scripts/autopilot-session.sh` (the `--autocompact` value on the `controller_cmd` line and the
     comment above it) and the Kickoff comment in `.claude/skills/autopilot/SKILL.md` (gate 10 text,
     user-directed, so this session may edit it on your word), commit
     (`chore(autopilot): launcher compacts at <value> (day-after ruling, journal N)`), and relaunch the
     controller at a unit boundary: stop it at your console, then
     `scripts/autopilot-session.sh start-herdr` and `/effort` high.
   - **Inconclusive**: 300k stays; rerun this file after the next full day.
7. Whatever the verdict, also read the day's `NEEDS USER` lines if any (grep the day's entries for
   `Anomalies:`): an `authority` or `operator` bootstrap part over 27,000 characters is yours to trim
   (move older decision quotes to the journal, prune the calendar or TODOs).

## Not in scope

No change to the spec, the hooks, the worker sandbox, gates, invariants, ceilings or model
allocations; no pushes (U7 governs); no worktree, database or evidence deletions.
