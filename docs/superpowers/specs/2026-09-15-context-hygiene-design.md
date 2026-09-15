# Context hygiene for the autopilot loop (design)

Date: 2026-09-15. Author: the review session with the user, from the audit of the week 2026-09-08 to 2026-09-15.
Status: draft for adversarial review, then the implementation plan. Executed by a fresh user-directed session
while the controller lock is free; never by the loop (the loop may not edit its skill, scripts or authority).

## 1. Problem

The loop's context design (files are truth, tail-only journal, routed references, one-kilobyte worker
reports) survived a hard crash and eleven compactions. Three things drifted in one week:

| Fact | Sep 8 | Sep 15 |
|---|---|---|
| `roadmap.md` | 40 KB | 174 KB, of which Carried fixes is 91 KB (64 rows, about 15 open) |
| `journal.md` | 178 KB | 699 KB, 245 entries; the last 40 have `- Result:` in 3 and `- Verification:` in 2 |
| `state.md` | 5.6 KB | 11 KB with two history sections (Evidence receipts, Rulings landed) |
| `context.py bootstrap` output | about 35 KB (estimated) | 157 KB |
| Evidence PNGs in git | | 88 files, 13 over 1 MB, 63 MB added this week |

1. The harness persists any Bash result over about 30,000 characters to a file and shows a 2 KB preview
   (observed: smallest persisted result 30.5 KB; no inline Bash result above 30 KB). The bootstrap has been
   truncated since the roadmap passed that size. The controller's workaround (`| sed -n '1,120p'`) delivers
   roadmap authority only; state, the journal tail, fix rows, calendar and TODOs arrive through separate reads.
   The skill's tests check structure, not size, so nothing flagged it.
2. Recording discipline eroded. Journal headings doubled to 150 characters and carry the numbers that
   recording.md sends to evidence files; fix rows average 1.3 KB of narrative; ledger lines run 400 to 1,300
   characters; state.md duplicates the journal. The Orient rules that grep the last entry for `FAIL` or for a
   `verify` after the last `deploy` depend on the template, so this is a correctness risk, not only cost.
3. Per-turn cost is set by the compaction threshold. With `--autocompact 500k` the window fills to about
   465k tokens before each compaction; the average turn carries about 260k tokens. Baseline (transcript usage
   records, sessions on Omarchy; context per turn = input + cache-read + cache-creation tokens of that turn):

| Session (start CT) | Turns | Compactions (context at each) | Cache-read tokens | Output tokens | Avg context/turn |
|---|---|---|---|---|---|
| Sep 14 02:28 (aebc28da) | 1,939 | 5 (467k, 460k, 466k, 465k, 466k) | 507.5M | 2.91M | 262k |
| Sep 14 15:32 (180a0657) | 1,406 | 3 (465k, 462k, 465k) | 389.3M | 2.41M | 277k |
| Sep 15 07:44 (72d7f42b) | 910 | 2 (461k, 463k) | 221.7M | 1.62M | 244k |

Of the controller's tool-result intake, about a third is re-reading its own canonical documents and up to
another third is ledgers, briefs and reports. Worker final reports average 1 KB (working as designed).

## 2. Goals and non-goals

Goals: (a) every wake reads the whole bootstrap, in parts that fit the harness limit, with a test that fails
when the real files outgrow it; (b) the recording rules become executable checks that the loop runs before
every docs commit; (c) the current-state files stop accumulating history; (d) per-turn context drops and the
change is measured against the baseline above; (e) evidence images stop inflating the repository while staying
inside the Monday git bundle; (f) instruction text that no longer applies stops loading.

Non-goals (user rulings 2026-09-15): no change to verify.md (its cadence tagging is a follow-up spec); no git
history rewriting; no change to gates, invariants, ceilings, the Decisions table or the model allocations; no
change to hooks or the worker sandbox; no edit to the v2 spec.

## 3. Components

All new and changed scripts live in `.claude/skills/autopilot/scripts/`. A new file under `scripts/` would
appear in the deploy trigger's diff (`git diff --stat "$DEPLOYED"..main -- . ':!docs' ':!*.md' ':!.claude'
':!scripts/autopilot-session.sh'`) and make the loop redeploy; `.claude/` is excluded. The one change under
`scripts/` is to `autopilot-session.sh`, which the trigger already excludes.

### 3.1 Bootstrap in three parts (`context.py bootstrap [checkpoint|authority|operator]`)

- Budget: each part's rendered output, `Source:` lines included, is at most 27,000 characters (10 percent
  under the observed 30,000 limit). A part over budget still prints in full but begins with one line
  `BUDGET EXCEEDED: <part> <n> chars > 27000; largest sections: <title> <n>, ...` so the loop sees it in the
  harness preview and reads the persisted file by the `Source:` ranges.
- `checkpoint` (the default when no part is named): `state.md` verbatim, the last two complete journal
  entries, the `Open` section of `fixes.md`, then the footer `Next: run context.py bootstrap authority, then
  context.py bootstrap operator; then the selected procedure, applicable Pre-loaded decisions and active
  ledgers.` Degrade rule: when the part would exceed the budget with two journal entries, it prints only the
  last entry and the line `journal entry N-1 omitted for budget: run context.py journal
  docs/superpowers/autopilot/journal.md --count 2`. Missing state prints the existing `STATE MISSING` line.
- `authority`: the roadmap preamble (text before the first level-2 heading) and every level-2 section of
  `roadmap.md` except the four operator sections below and the children of `Pre-loaded decisions` (its
  preamble prints, as today). Required headings: `Phases (spec §15)`, `Decisions (2026-09-07)`, `Standing
  authorizations (user, 2026-09-07)`, `Files and sections the loop may edit`, `Invariants the loop never
  changes (hard-forbidden; always a gate, never a ruling)`, `Pre-loaded decisions`. A missing required heading
  raises, as today. New level-2 sections are included automatically, as today; nothing is filtered by phase
  status or date. Measured today this part is about 23.6 KB.
- `operator`: `Current host and restart setup (user-directed, 2026-09-12)`, `Secrets (provision when
  convenient; the loop never blocks on them)`, `Operator calendar (America/Chicago)` and `User-side TODOs`,
  all required. Measured today, before 3.7's pruning, about 26 KB; after it about 15 KB.
- `bootstrap` with no argument prints `checkpoint`. The skill's step 1 names all three parts; the footer
  repeats the order so a session that forgets the skill text still runs them.
- Tests: the existing fixture tests are updated to the three parts; a new live test measures each part
  against the real repository files (skipped when they are absent) and asserts the budget.

### 3.2 Fix rows: `docs/superpowers/autopilot/fixes.md`

- Three level-2 sections, `Open`, `Watch`, `Closed`, each a table with today's columns:
  `| # | Finding | Files | Change | Covering test | Deploy |`. A short preamble states the rules below.
  Numbering continues from the roadmap's last number.
- `Open` holds actionable hotfix rows only; Orient rule 1 selects hotfix from `Open` and nowhere else.
  `Watch` holds rows assigned to a phase, owned by the user, recorded as observations, or ruled follow-ups;
  hotfix, phase and plan-next briefs read `Watch` when they touch its area. `Closed` holds done rows.
- An `Open` or `Watch` row is at most 600 characters. The Finding cell states the symptom in one clause and
  points to where the numbers live (`journal N`, `evidence/<file>`, a ledger path). Numbers, time series and
  query output never go in a row.
- Moves: a verify that reads PASS on the covering row, or the user's ruling, moves a row from `Open` or
  `Watch` to `Closed` verbatim in the same commit as the journal entry that closes it, with the closing pointer
  in the Deploy cell; a ruling that assigns or defers a row moves it `Open` to `Watch`; a ruling that makes a
  `Watch` row actionable moves it to `Open`. Rows are never deleted. `Closed` rows have no size cap.
- Migration (one time, in the implementation session): every row of the roadmap's Carried fixes table is
  copied to `fixes.md`. `Closed` when its Deploy cell, `state.md`, or the journal entry it cites says closed,
  CLOSED, done or superseded; `Open` when `state.md`'s "Carried fixes open and actionable" list or the Deploy
  cell says actionable or in flight; everything else, ambiguous rows included, goes to `Watch`. The
  classification table (row number, section, one-line reason) goes in the migration's `repair` journal entry
  (3.7) with the ambiguous rows under `Needs you`. Open and Watch rows over 600 characters are condensed to
  the cap; the condensed row's Finding cell cites the journal entry that carried the fix and
  `roadmap.md@<pre-migration sha>` so the original text stays one command away. Row count in `fixes.md`
  (Open + Watch + Closed) equals the roadmap's row count.
- `roadmap.md` replaces the Carried fixes section with a two-line pointer to `fixes.md`. The user's
  edit-rights sentence in `Files and sections the loop may edit` names `fixes.md` (`Open`, `Watch`, `Closed`)
  as loop-editable and states that rows move and are never deleted. The skill files that say "roadmap
  Carried fixes" (SKILL.md Orient rule 1 and Files the loop may edit; references/hotfix.md; references/verify.md
  step 5; references/recording.md `Carried forward`) say `fixes.md Open` (or `Watch` where a row is deferred).

### 3.3 Executable recording rules (`context.py check`, `context.py append`)

`context.py check` reads the repository and prints one line per violation, `<file>: <rule>: <measured> vs
<limit>`, exit 1 when any; exit 0 and `check: ok` otherwise. Rules:

| Target | Rule |
|---|---|
| bootstrap parts | each rendered part at most 27,000 characters |
| `state.md` | at most 8,000 characters; level-2 headings drawn only from the fixed set in 3.4, exact titles, no suffix; the required ones present |
| `journal.md` last entry (or `--entry N`) | heading matches `## N. <unit> - <slug> - <YYYY-MM-DD> <HH:MM>[-<HH:MM>] CT` and is at most 120 characters; `- Result:` and `- Next:` lines present; `- Orient:` present when the unit is one of preflight, hotfix, deploy, verify, operate, phase, plan-next, idle, repair; `- Verification:` present when the unit is verify or deploy; body at most 3,000 characters unless the unit is decision or gate (they quote the user verbatim); unknown units need only Result and Next |
| `fixes.md` | `Open`, `Watch` and `Closed` present; every `Open` and `Watch` row at most 600 characters |

`context.py append` refuses a ledger line longer than 400 characters with the message `ledger line <n> chars
> 400: write the detail to the report or brief and reference its path`, exit 1, nothing written.

Rule text: `recording.md` gains "Run `python3 .claude/skills/autopilot/scripts/context.py check` before every
docs commit; fix a violation by moving detail to evidence, the journal body, or `fixes.md`, never by dropping
an unresolved fact." `preflight.md` runs `check` once per session and journals a failure as the first repair.
The journal format block in `recording.md` gains the heading and body limits and the unit list; the
`<unit> - <slug>` separator is a plain hyphen.

Tests: fixture-based tests for each rule (passing and failing cases), the append refusal, and a live test
that runs `check` against the real repository so the migration cannot land failing.

### 3.4 `state.md` schema

Level-2 headings, in this order, exact titles: `Resume first` (optional; a handoff like the 17:27 CT stopping
point), `Right now`, `Order of work`, `Active units`, `Pending results`, `Counters and deadlines`,
`Constraints`. The header line above them keeps the update time, session, checkout, last journal entry,
runtime build and main/origin as today. Content per section is recovery.md's existing field list: `Pending
results` carries agents, suites, wakeups and the latest receipts as pointers; `Counters and deadlines` carries
the CT-day counters, failure counts, wakeup IDs, judge-after times and next duties. Receipts and rulings are
pointers (`journal N`, ledger line time), never pasted. Size at most 8,000 characters.

Migration: the implementer rewrites the current file into the schema, carrying every unresolved fact. Before
the Evidence receipts and Rulings landed sections are dropped, each evidence path and release stamp they name
is grepped in `journal.md` and the active ledgers; any that appear nowhere else are listed in the migration's
`repair` journal entry (3.7), so nothing is lost. `recovery.md`'s checkpoint section names the fixed headings
and the cap.

### 3.5 Compaction at 300k, measured (`autopilot-session.sh`, `usage.py`)

- `controller_cmd` passes `--autocompact 300k`. The comment line says 300k and cites this spec.
- New `.claude/skills/autopilot/scripts/usage.py [--since YYYY-MM-DD] [--project DIR]` reads the Claude Code
  transcripts for this project (`~/.claude/projects/-home-trey-dev-sports/*.jsonl` by default) and prints one
  row per session with at least 20 assistant turns: start time (CT), session id prefix, assistant turns,
  compactions with the context size at each (the context of the last assistant turn before the compact
  summary), cache-read tokens, cache-creation tokens, uncached input tokens, output tokens, average context
  per turn (as defined in section 1), and the model. Read-only; it never prints message text.
- Baseline: the three rows in section 1. Success after the first full controller day at 300k: average
  context per turn at most 200k and at most 6 compactions per 1,000 assistant turns (baseline 2.6). If either
  misses, the user chooses between 350k and reverting to 500k; the loop does not change the launcher.
- The user or a review session runs `usage.py`; the loop does not (it would read its own transcripts).

### 3.6 Evidence images (`evidence_image.py`)

- `evidence_image.py SRC DEST`: ImageMagick (`magick`, present on Omarchy) converts SRC to a JPEG at DEST,
  metadata stripped, starting at quality 85; while the result exceeds 400 KB it steps quality to 70 and then
  multiplies the width by 0.75 repeatedly until under the cap or below 50 percent of the original width; over
  800 KB at the floor it exits 1 and leaves no DEST. It prints `DEST <bytes> q<quality> <width>x<height>`. It
  refuses to overwrite DEST.
- `references/verify.md` step 3 replaces `cp -n` with the tool; canonical evidence names keep the contract's
  `<date>-<unit>-<HHMM>-<nn>-<slug>.jpg` form (verify.md line 887 already says `.jpg`). The reviewer keeps
  reading the uncompressed originals in `.superpowers/sdd/screenshots/`.
- Existing PNGs in `evidence/` stay. No history rewrite.
- Tests: a generated image round-trips under the cap; a DEST that exists is refused; skipped when `magick` is
  absent.

### 3.7 Dead instruction text

User-owned text, edited in the implementation session under the user's direction (the loop never does this):

- `SKILL.md` U8 scheduling exception: the dated 6C deadline (Sun 2026-09-13) has passed; the paragraph shrinks
  to the still-live rule that ready 6x milestones may be planned in parallel with separate plans, branches and
  ledgers, controller git serial, ceilings unchanged, and 6C `planned` until full acceptance.
- `SKILL.md` kickoff block and `docs/runbooks/claude-omarchy-restart.md`: "select `/effort` high explicitly;
  the saved default may be xhigh, which the skill measured at three to four times the tokens for no gain."
- `roadmap.md` Secrets: the production Kalshi key row keeps one clause, `done, read-scoped, journal 202`.
- `roadmap.md` Operator calendar: the four past-dated rows (the drill before 2026-09-12; Sun 2026-09-13; Mon
  2026-09-14; Tue 2026-09-15) are removed; rows whose Duty is an NAS `ssh` command are rewritten to the Omarchy
  equivalent only where a Makefile target or runbook command exists, otherwise left as they are with the note
  `(NAS command; see linux-controller.md)`.
- `roadmap.md` User-side TODOs: the fourteen `[x]` items are deleted; each cites its journal entry.
- The Decisions table, Standing authorizations, Invariants, Phases and Pre-loaded decisions are not touched.

The migration is recorded as one `repair` journal entry in the template, listing: files restructured, the row
classification table, receipts not found elsewhere (3.4), the launcher change, and the new commands.

## 4. Data flow after the change

Wake: `bootstrap` (checkpoint) then `bootstrap authority` then `bootstrap operator`, three tool calls of at
most 27,000 characters each, then the routed procedure and phase decisions as today. Unit end: journal entry
in the template, `state.md` rewritten in the schema, fix rows moved on close, `check`, one docs commit.
Verify walkthrough: captures to `.superpowers/sdd/screenshots/`, reviewer reads originals, controller runs
`evidence_image.py` per file into `evidence/`. Compaction: the hooks are unchanged; the resumed context runs
the three bootstrap parts and recovery.md as today.

## 5. Error handling

- A bootstrap part over budget prints the `BUDGET EXCEEDED` line first; the loop reads the persisted file by
  the `Source:` ranges, journals the overage in the next entry and shrinks what it owns (state, Open and Watch
  rows, TODOs). Overage inside user-owned sections is reported in the next `stopped` or weekly report's
  "Needs you", not edited by the loop.
- `check` failing mid-unit costs one turn: the message names the remedy. Under time pressure the loop still
  commits after fixing; it never bypasses `check` by deleting an unresolved fact.
- `append` refusing a long line loses nothing: the loop writes the detail to the report or brief and appends
  the short line.
- `evidence_image.py` failing on one capture: the item is recorded as pending visual evidence with the source
  path, per the existing rule; never an invented pass.
- If compaction at 300k produces a reconcile mistake (a duplicate entry, a duplicate dispatch), Orient rule 0
  and the ceilings apply as today; the day-after measurement records it and the user decides the threshold.

## 6. Sequencing and safety

1. Preconditions: `scripts/autopilot-session.sh status` shows the controller lock free and no
   `sports-autopilot` tmux or herdr session; the fix 78 part 2 worktree and its uncommitted edits are left
   alone (state.md Resume first covers them).
2. Branch `context-hygiene-2026-09-15` from main. Order: scripts and tests (3.1, 3.3, 3.5 tool, 3.6) with
   the fixture tests green; then the document migration (3.2, 3.4, 3.7) and the skill text; then the live tests
   and `check` green; then the launcher change (3.5).
3. Fast-forward merge to main; docs and skill in as few commits as keep each reviewable (scripts, migration,
   text). No push (U7 governs pushes).
4. The user relaunches the controller with the launcher; its first preflight runs `check`.
5. After the first full controller day: `usage.py` against the baseline; the user rules on the threshold.

## 7. Acceptance

1. `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests` passes with the new tests.
2. `python3 .claude/skills/autopilot/scripts/context.py check` exits 0 on main after the merge.
3. Each bootstrap part measured on main is under 27,000 characters (the live test).
4. `state.md` is at most 8,000 characters, has only the fixed headings, and its Resume first section carries
   the 17:27 CT stopping-point content.
5. `fixes.md` exists with `Open`, `Watch` and `Closed`; their rows total the roadmap's 64; the roadmap has no
   Carried fixes table; `grep -rn 'Carried fixes' .claude/skills/autopilot/SKILL.md
   .claude/skills/autopilot/references` returns nothing.
6. `grep -n 'autocompact 300k' scripts/autopilot-session.sh` matches.
7. The deploy trigger diff between the deployed build and main shows no non-docs, non-`.claude` file other
   than `scripts/autopilot-session.sh`.
8. The `repair` journal entry exists and passes `check --entry N`.
9. Day-after: `usage.py` rows for the first 300k session recorded in the journal by the user or a review
   session, judged against 3.5.

## 8. Rejected alternatives

- Raising `BASH_MAX_OUTPUT_LENGTH` for the controller: the CLI has the knob, but a larger result per call
  grows the context the change is meant to shrink; parts that fit are also explicit about what was read.
- Filtering fix rows by a Status column inside the roadmap: keeps 90 KB of history in the authority file and
  puts the filter logic where a heading rename breaks it silently.
- Keeping screenshots out of git or in LFS: both leave the Monday bundle without the visual evidence.
- Enforcing `check` as a git pre-commit hook: a failing commit mid-unit leaves the loop in an unrecorded state;
  the documented command plus the live test is enough for now.
- Splitting verify.md by cadence: deferred by ruling until the measurement shows what a verify pass costs.

## 9. Risks

- The authority part has about 3 KB of headroom; a long new user decision could push it over. The
  `BUDGET EXCEEDED` line and the live test make that visible the day it happens; the remedy is the user's
  (move older decision quotes to the journal), not the loop's.
- Three bootstrap calls per wake instead of one: they run in one turn as parallel tool calls; the footer
  carries the order.
- JPEG artifacts: only the archived copy is compressed; the reviewer reads originals.
- The check's unit list may reject a legitimate new unit name: unknown units require only Result and Next.
- The day-after measurement depends on a full controller day at 300k; a short or interrupted day is reported
  as inconclusive, not as a pass.
