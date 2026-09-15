# Context hygiene for the autopilot loop (design)

Date: 2026-09-15. Author: the review session with the user, from the audit of the week 2026-09-08 to 2026-09-15.
Revision 3 after two adversarial reviews (design logic; loop operations) and a re-check. Status: for the user's
approval, then the implementation plan. Executed by a fresh user-directed session while the controller lock is free; never by the
loop (the loop may not edit its skill, scripts, launcher or authority).

## 1. Problem

The loop's context design (files are truth, tail-only journal, routed references, one-kilobyte worker
reports) survived a hard crash and eleven compactions. Three things drifted in one week:

| Fact | Sep 8 | Sep 15 |
|---|---|---|
| `roadmap.md` | 40 KB | 174 KB, of which Carried fixes is 91 KB (63 rows, 58 over 600 characters, median 1,329) |
| `journal.md` | 178 KB | 699 KB, 245 entries; no entry since 236 has `- Result:`; 17 of the last 40 bodies exceed 3,000 characters; 55 of 245 headings exceed 120 |
| `state.md` | 5.6 KB | 13.4 KB with two history sections (Evidence receipts, Rulings landed) |
| `context.py bootstrap` output | about 35 KB (estimated) | 159.6 KB |
| Evidence images in git | | 85 PNG (average 620 KB, 13 over 1 MB, largest 6.9 MB) and 59 JPEG (average 98 KB); 63 MB added this week |

1. The harness persists any Bash result over about 30,000 characters to a file and shows a 2 KB preview
   (observed: smallest persisted result 30.5 KB; no inline Bash result above 30 KB). The bootstrap has been
   truncated since the roadmap passed that size. The controller's workaround (`| sed -n '1,120p'`) delivers
   roadmap authority only; state, the journal tail, fix rows, calendar and TODOs arrive through separate reads.
   The skill's tests check structure, not size, so nothing flagged it.
2. Recording discipline eroded. Journal headings carry the numbers that recording.md sends to evidence
   files; fix rows average 1.4 KB of narrative; ledger lines run 400 to 1,300 characters; state.md duplicates
   the journal. The Orient rules that grep the last entry for `FAIL` or for a `verify` after the last `deploy`
   depend on the template, so this is a correctness risk, not only cost.
3. Per-turn cost is set by the compaction threshold. With `--autocompact 500k` the window fills to about
   465k tokens before each compaction; the average turn carries about 260k tokens. Baseline (transcript usage
   records, sessions on Omarchy; context per turn = input + cache-read + cache-creation tokens of that turn):

| Session (start CT) | Turns | Compactions (context at each) | Cache-read tokens | Output tokens | Avg context/turn |
|---|---|---|---|---|---|
| Sep 14 02:28 (aebc28da) | 1,939 | 5 (467k, 460k, 466k, 465k, 466k) | 507.5M | 2.91M | 262k |
| Sep 14 15:32 (180a0657) | 1,406 | 3 (465k, 462k, 465k) | 389.3M | 2.41M | 277k |
| Sep 15 07:44 (72d7f42b) | 910 | 2 (461k, 463k) | 221.7M | 1.62M | 244k |

Pooled: 10 compactions in 4,255 turns, 2.35 per 1,000 turns; 263k average context per turn. Of the
controller's tool-result intake, about a third is re-reading its own canonical documents and up to another
third is ledgers, briefs and reports. Worker final reports average 1 KB (working as designed).

## 2. Goals and non-goals

Goals: (a) every wake reads the whole bootstrap, in parts that fit the harness limit, with a test that fails
when the real files outgrow it; (b) the recording rules become executable checks that the loop runs before
every docs commit, and a check the loop cannot satisfy never blocks it; (c) the current-state files stop
accumulating history; (d) per-turn context drops and the change is measured against the baseline above;
(e) evidence images stop inflating the repository while staying inside the Monday git bundle; (f) instruction
text that no longer applies stops loading.

Non-goals (user rulings 2026-09-15): no change to `verify.md` beyond the one-word edit the user authorised in
3.6 (its cadence tagging is a follow-up spec); no git history rewriting; no change to any gate's trigger or
outcome, to invariants, ceilings, the Decisions table or the model allocations; no change to hooks
(`.claude/settings.json`, the hook scripts) or the worker sandbox; no edit to the v2 spec; no renumbering of
fix rows.

## 3. Components

All new and changed scripts live in `.claude/skills/autopilot/scripts/`. A new file under `scripts/` would
appear in the deploy trigger's diff (SKILL.md Orient rule 2: `git diff --stat "$DEPLOYED"..main -- . ':!docs'
':!*.md' ':!.claude' ':!scripts/autopilot-session.sh'`) and make the loop redeploy; `.claude/` is excluded.
The one change under `scripts/` is to `autopilot-session.sh`, which the trigger already excludes. Note that
`scripts/release_tree.py` excludes only `docs/superpowers/autopilot/`, so `.claude/`, the launcher, root
`CLAUDE.md`, the runbook and this spec are all inside the release tree: after the merge main's release tree
differs from the deployed tree without any deployable code change, the deploy trigger still reads empty, and
no pre-existing full-suite receipt matches main. The next deploy needs a receipt for the post-merge tree; fix
78 part 2's full suite after its rebase onto the merged main supplies it in the normal flow (deploy.md step
2). The migrated state says so (3.4).

### 3.1 Bootstrap in three parts (`context.py bootstrap [checkpoint|authority|operator]`)

- Budget: each part's rendered output, `Source:` lines included and measured after the degrade rules below,
  is at most 27,000 characters (10 percent under the observed 30,000 limit). A part over budget still prints
  in full but begins with one line `BUDGET EXCEEDED: <part> <n> chars > 27000; largest sections: <title> <n>,
  ...` so the loop sees it in the harness preview and reads the persisted file by the `Source:` ranges.
- `checkpoint` (the default when no part is named): `state.md` verbatim, the last two complete journal
  entries, the first ten rows of the `Open` section of `fixes.md` (more rows print the line `N more Open
  rows: run context.py section docs/superpowers/autopilot/fixes.md Open`), then the footer `Next: run
  context.py bootstrap authority, then context.py bootstrap operator; then the selected procedure, applicable
  Pre-loaded decisions and active ledgers.` Degrade rules, applied in order until the part fits: (1) print
  only the last journal entry plus `journal entry N-1 omitted for budget: run context.py journal
  docs/superpowers/autopilot/journal.md --count 2`; (2) if the last entry alone still overflows, print its
  heading and its non-quoted lines plus `quoted block omitted for budget: run context.py journal
  docs/superpowers/autopilot/journal.md --count 1` (the user's words stay in the journal; only the bootstrap
  elides them). Worst case under the caps of 3.3 and 3.4 after both rules: about 13,000 of state and
  overhead, 6,150 of entry and 6,000 of rows, under 27,000. An overage that remains after both rules is the
  `BUDGET EXCEEDED` line and, in `check`, `NEEDS USER` when the last entry is a decision or gate entry,
  otherwise `BLOCK`. Missing `state.md` prints the existing `STATE MISSING` line. Missing `fixes.md` or
  a missing `Open` heading prints `FIXES MISSING: reconstruct fixes.md from roadmap.md history and the
  journal before Orient; do not select idle` (and `check` reports a BLOCK, 3.3).
- `authority`: the roadmap preamble (text before the first level-2 heading) and every level-2 section of
  `roadmap.md` except the four operator sections below and the children of `Pre-loaded decisions` (its
  preamble prints, as today). Required headings: `Phases (spec §15)`, `Decisions (2026-09-07)`, `Standing
  authorizations (user, 2026-09-07)`, `Files and sections the loop may edit`, `Invariants the loop never
  changes (hard-forbidden; always a gate, never a ruling)`, `Carried fixes` (the pointer section of 3.2, kept
  because the unchanged `verify.md` names it), `Pre-loaded decisions`. A missing required heading raises, as
  today. New level-2 sections are included automatically, as today; nothing is filtered by phase status or
  date. Measured today: 22.5 KB plus `Source:` lines and the pointer section, about 23.2 KB; headroom about
  3.8 KB.
- `operator`: `Current host and restart setup (user-directed, 2026-09-12)`, `Secrets (provision when
  convenient; the loop never blocks on them)`, `Operator calendar (America/Chicago)` and `User-side TODOs`,
  all required. Measured today 26.0 KB; after 3.7's pruning about 17 KB.
- Tests: the existing fixture tests (`test_bootstrap_preserves_authority_and_live_checkpoints_verbatim`,
  `test_missing_state_does_not_reset_counts_and_new_authority_is_included`) are rewritten for the three parts
  and their fixture gains a `fixes.md`; a new live test measures each part against the real repository files
  (skipped when they are absent) and asserts the budget.
- Text that describes one bootstrap output is updated: SKILL.md step 1 (lines 24-26) and its Kickoff comment
  (line 62, "compact at 500k"), `docs/runbooks/claude-omarchy-restart.md` line 34, and root `CLAUDE.md` line
  12 ("run ... bootstrap" becomes "run ... bootstrap, all three parts"). The hooks' text is unchanged (non-goal);
  it still says "run its scripts/context.py bootstrap", which prints the checkpoint part with the footer.
- The skill's tests are not part of `make test` (`pyproject.toml` testpaths is `tests`). `preflight.md`
  therefore runs `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests` once per session, after
  `check`; a failure is a `BLOCK`-class repair before any dispatch. The live tests assert only what the loop
  owns (no `BLOCK` from `check`; the checkpoint part under budget); an `authority` or `operator` overage
  surfaces as a `NEEDS USER` line, never as a test failure.

### 3.2 Fix rows: `docs/superpowers/autopilot/fixes.md`

- Three level-2 sections, `Open`, `Watch`, `Closed`, each a table with today's six columns:
  `| # | Finding | Files | Change | Covering test | Deploy |`. A preamble states the rules below and carries
  one line `Baseline numbers (2026-09-15): 16, 20, ..., 80` listing every row number present at migration
  (the duplicate 56 listed twice). New rows continue from the highest number.
- `Open` holds actionable hotfix rows only; Orient rule 1 selects hotfix rows from `Open` and nowhere else
  (its second trigger, the last entry ending in FAIL, stands; its second sentence, that rows assigned to
  phase work, user actions or already closed do not keep selecting hotfix, becomes "such rows live in `Watch`
  or `Closed`"). `Watch` holds rows assigned to a phase, owned by the user, recorded as observations, or ruled
  follow-ups; hotfix, phase and plan-next briefs read it with `context.py section
  docs/superpowers/autopilot/fixes.md Watch` when they touch its area. `Closed` holds done rows.
- An `Open` or `Watch` row is at most 600 characters and has exactly six cells. The Finding cell states the
  symptom in one clause and points to where the numbers live (`journal N`, `evidence/<file>`, a ledger path).
  Numbers, time series and query output never go in a row.
- Moves: a verify that reads PASS on the covering row, or the user's ruling, moves a row to `Closed`
  unchanged except that the Deploy cell gains `closed: journal N`, in the same commit as the closing journal
  entry. A transient (references/hotfix.md lines 5 and 23, which today say "remove") moves the row to `Closed`
  with `transient (journal N)`; a passed item (line 23) likewise with `PASS (journal N)`. A user ruling that
  assigns or defers a row moves it from `Open` to `Watch`; a ruling that makes a `Watch` row actionable moves it
  to `Open`. The loop itself moves an `Open` row to `Watch` in exactly one case: references/hotfix.md's scope
  rule (lines 15-17) makes the fix phase work (a new table, a dependency); the Deploy cell gains `watch: phase
  work (journal N)` and the next plan-next brief names the row. Otherwise a row added by a verify FAIL or an
  integrity anomaly leaves `Open` only to `Closed` on PASS or by the user's ruling. Every move happens in the
  same commit as the journal entry that records it. Rows are never deleted; `check` proves it against the
  baseline line. `Closed` rows have no size cap.
- Migration (one time, in the implementation session). Facts: the roadmap table has 63 rows numbered 16 to 80
  with 17, 18 and 19 absent and 56 used twice; rows 78, 79 and 80 have four cells (Files, Change, Covering
  test and Deploy folded into Finding) and row 71 has seven. Steps: (1) copy every row; the second 56 keeps
  its number with the # cell reading `56 (dup)`; rows 78-80 and 71 are normalised to six cells with the
  original text preserved across the cells and `none stated` where a cell has nothing. (2) Classify,
  case-insensitively, with `Closed` taking precedence over `Open`: `Closed` when the Deploy cell, `state.md`,
  or the most recent journal entry the row cites says closed, done, superseded, transient, or records a deploy
  followed by a verify PASS on the covering row; `Open` when `state.md`'s "Carried fixes open and actionable"
  list or the Deploy cell says actionable or in flight; everything else, ambiguous rows included, goes to
  `Watch`. (3) Write the classification table (row number, section, one-line reason, original length) and the
  receipts grep of 3.4 to `docs/superpowers/autopilot/reports/2026-09-15-context-hygiene-migration.md`; the
  user rules on the table before the fast-forward merge; ambiguous rows are listed there under `Needs you`.
  (4) Condense `Open` and `Watch` rows over 600 characters to the cap; the condensed Finding cell cites the
  journal entry that carried the fix and `roadmap.md@<pre-migration sha>:<line>` so the original text stays
  one command away. An independent read-only reviewer (a subagent in the hook sandbox) compares every
  condensed row with its original and reports any fact that the condensed row plus its pointers no longer
  reach. (5) `Open` plus `Watch` plus `Closed` equals 63.
- `roadmap.md` keeps the level-2 heading `Carried fixes` as a pointer section with no table rows: "Rows live
  in `fixes.md` (`Open`, `Watch`, `Closed`). A verify FAIL adds a row to `fixes.md` `Open`. This section holds
  no rows." The unchanged `verify.md` lines 765 and 897 and `roadmap.md` line 232 resolve through it. The
  pointer text is the user's; the edit-rights sentence in `Files and sections the loop may edit` (roadmap line
  119) and SKILL.md's "Files the loop may edit" (line 180) replace `Carried fixes` with `fixes.md`, state that
  rows move and are never deleted, and add `scripts/autopilot-session.sh` and the skill's scripts to the
  never-edited list (3.5). The rows leave the roadmap in the same commit that creates `fixes.md`. The skill files that say "roadmap Carried fixes" (SKILL.md Orient rule 1 and Files
  the loop may edit; references/hotfix.md lines 5 and 23; references/verify.md step 5; references/recording.md
  `Carried forward`) say `fixes.md Open` (or `Watch` where a row is deferred).

### 3.3 Executable recording rules (`context.py check`, `context.py append`)

`context.py check [--entry N]` reads the repository and prints one line per finding in two classes:

- `BLOCK <file>: <rule>: <measured> vs <limit>`: something the loop owns and must fix before the commit.
- `NEEDS USER <file>: <rule>: <measured> vs <limit>`: something only the user may edit.

Exit 1 when any `BLOCK`; exit 0 with `check: ok` or with only `NEEDS USER` lines; exit 2 only when
`roadmap.md` or `journal.md` is unreadable or has an ambiguous required heading, with the existing "read the
canonical file directly" message. An absent `fixes.md`, an absent `fixes.md` or `state.md` section, or an
absent `state.md` is a `BLOCK`, not a reader failure.

| Target | Rule | Class |
|---|---|---|
| bootstrap `checkpoint` | at most 27,000 characters after the degrade rules | BLOCK; NEEDS USER when the overage remains with a decision or gate entry last |
| bootstrap `authority`, `operator` | at most 27,000 characters | NEEDS USER |
| `state.md` | fixed level-2 headings only, exact titles, no suffix (3.4); required ones present; the file minus the `Resume first` section (header line included) at most 8,000 characters; the `Resume first` section at most 4,000 | BLOCK |
| `fixes.md` | `Open`, `Watch`, `Closed` present; every `Open` and `Watch` row at most 600 characters; every row in every section has six cells; # cells parse as `\d+( \(dup\))?` and the multiset of parsed numbers over the three tables contains the baseline line's multiset (56 twice) | BLOCK |
| `roadmap.md` `Carried fixes` | the section has no table rows | BLOCK |
| `journal.md` last entry, or `--entry N` (the entry whose heading starts `## N.`) | heading grammar and limits below | BLOCK |

Journal entry rules (checked on the last entry only; older entries are never checked, and an entry may be
edited until the commit that lands it, after which recording.md's append-only rule applies):

- Heading: `## N. <unit> - <slug> - <YYYY-MM-DD> <HH:MM>[-<HH:MM>] CT`, the text after `## ` at most 120
  characters. `<unit>` is one token matching `[a-z][a-z0-9-]*`; qualifiers go in the slug (`verify - re-read:
  ...`, `phase - start: ...`, `paused - rate limit: ...`); the timestamp follows the last ` - `; nothing
  follows ` CT`. Result values (`done | FAIL | transient | gated: | paused: rate limit | ceiling`) are
  unchanged; `paused: rate limit` stays a Result value and gate 14's text is untouched.
- Required lines: `- Result:` and `- Next:` in every entry (a decision entry writes `- Result: recorded`);
  `- Orient:` when the unit is one of preflight, hotfix, deploy, verify, operate, phase, plan-next, idle,
  repair; `- Verification:` when the unit is verify or deploy. Unknown units need only Result and Next.
- Body (characters after the heading line, trailing whitespace stripped): at most 6,000 for verify, deploy and
  repair (their contracts require one line per item); at most 3,000 for every other unit; in decision and gate
  entries the quoted block (lines starting `>`) is exempt and the rest is at most 3,000. Three of the last
  eight verify bodies exceed 6,000 (entries 219: 13,355; 231: 11,037; 234: 6,917): per-item numbers go to the
  `-layer2.txt` and `-summary.txt` evidence files as recording.md already says, and the entry keeps one
  verdict clause per item.

`context.py append` refuses a ledger line longer than 400 characters with the message `ledger line <n> chars
> 400: write the detail to the report or brief and reference its path`, exit 1, nothing written.

Rule text. `recording.md` gains: "Run `python3 .claude/skills/autopilot/scripts/context.py check` before every
docs commit. A `BLOCK` is fixed before the commit by moving detail to evidence, the journal body, a report or
`fixes.md`, never by dropping an unresolved fact. A `NEEDS USER` line is copied once into the entry's
`Anomalies:` line and into the next report's Needs you; it is never a repair entry and never blocks. An entry
may be edited until the commit that lands it; from that commit on it is appended to, never edited." The
journal format block gains the heading grammar, the limits, the unit list and one decision-entry example.
`preflight.md` runs `check` and the skill's tests once per session: a `BLOCK` is fixed inside the preflight's
own commit; a `NEEDS USER` line is reported as above.

Tests: fixture-based tests for each rule (passing and failing cases), the two classes and three exit codes,
the append refusal, and a live test that runs `check` against the real repository and asserts no `BLOCK` and
the checkpoint part under budget, so the migration cannot land failing.

### 3.4 `state.md` schema

Level-2 headings, in this order, exact titles: `Resume first` (optional), `Right now`, `Order of work`,
`Active units`, `Pending results`, `Counters and deadlines`, `Constraints`. The header line above them keeps
the update time, session, checkout, last journal entry, runtime build and main/origin as today. recovery.md's
field list maps onto the headings: active units, plans, ledgers, branches and next legal action under
`Active units`; agents, suites, wakeups and the receipts of every stage (code SHA, test, review, merge,
deploy, verify) as one pointer line per stage under `Pending results`; CT-day counters, failure counts,
wakeup IDs, judge-after times and next duties under `Counters and deadlines`; standing constraints and the
applicable unresolved lessons and risks with their evidence pointers under `Constraints`. Receipts and
rulings are pointers (`journal N`, ledger line time, evidence path), never pasted. The file minus the
`Resume first` section, header line included, is at most 8,000 characters; `Resume first` is at most 4,000
and is removed at the first checkpoint after the resumed session consumes it, every fact it carried moving
to its section or the resume journal entry in the same commit.

Migration: the implementer writes a fact inventory (every sentence of the current `state.md` mapped to its
destination: a schema section, a `fixes.md` row, the journal entry that already records it, or the migration
report) into the migration report of 3.2, then rewrites the file. The current live sections measure 10.4 KB
including the 2.4 KB stopping point (17:34 CT, commit 9fcff02), so the rewrite must reach 8,000 plus 4,000
by pointers, not by dropping facts; the independent reviewer of 3.2 checks the rewrite against the inventory.
Before the Evidence receipts and Rulings landed sections are dropped, each evidence path and release stamp
they name is grepped in `journal.md` and the active ledgers; any that appear nowhere else are listed in the
migration report and cited from the `repair` entry (3.7). The migrated `Right now` states that main's
release tree differs from the deployed tree by non-deployable files only (section 3) and that the next
deploy needs a post-merge suite receipt. The migrated `Resume first` records the user's ruling of 2026-09-15:
the stop at 17:34 CT suspends the hotfix batch wall-clock, which resumes at relaunch with 35 minutes consumed. `recovery.md`'s
checkpoint section names the fixed headings, the mapping and the caps.

### 3.5 Compaction at 300k, measured (`autopilot-session.sh`, `usage.py`)

- `controller_cmd` passes `--autocompact 300k`; the comment line says 300k and cites this spec; SKILL.md's
  Kickoff comment (line 62) says 300k.
- `scripts/autopilot-session.sh` is added to the never-edited list in `Files and sections the loop may edit`
  (roadmap line 124, which today names only the skill and the v2 spec) and to SKILL.md's "Files the loop may
  edit" (line 181, which already forbids the skill's references, scripts and tests); gate 10's list (line
  203) gains the launcher. No gate's trigger or outcome changes.
- New `.claude/skills/autopilot/scripts/usage.py [--since YYYY-MM-DD] [--project DIR]` reads the Claude Code
  transcripts for this project (`~/.claude/projects/-home-trey-dev-sports/*.jsonl` by default) and prints one
  row per session with at least 20 assistant turns: start time (CT), session id prefix, assistant turns,
  compactions with the context size at each (the context of the last assistant turn before the compact
  summary), cache-read tokens, cache-creation tokens, uncached input tokens, output tokens, average context
  per turn (section 1's definition), and the model; then one pooled row per CT day (turn-weighted), plus
  cache-read tokens per journal entry written that day as a work-normalised figure. Read-only; it never prints
  message text.
- Baseline: section 1 (pooled 263k per turn, 2.35 compactions per 1,000 turns). Success after the first full
  controller day at 300k, pooled over that CT day's sessions with at least 20 turns: average context per turn
  at most 200k and at most 6 compactions per 1,000 turns. A day with fewer than 10 dispatches or no verify
  entry, or one dominated by a gate or a rate-limit pause, is reported as inconclusive, not as a pass. The
  report also states the context at which compaction actually fired (the 500k setting fired near 465k). If
  either criterion misses, the user chooses between 350k and reverting to 500k; the loop does not change the
  launcher. The bootstrap and state shrink land the same day, so the comparison is against the whole change,
  not the threshold alone.
- The user or a review session runs `usage.py`; the loop does not (it would read its own transcripts).

### 3.6 Evidence images (`evidence_image.py`)

- `evidence_image.py SRC DEST`: ImageMagick (`magick`, present on Omarchy) converts SRC to a JPEG at DEST,
  metadata stripped, starting at quality 85; while the result exceeds 400 KB it steps quality to 70 and then
  multiplies the width by 0.75 repeatedly, stopping before a step that would go below 50 percent of the
  original width; at the floor a result of 400 to 800 KB is kept with `over cap` printed, exit 0; over 800 KB
  it exits 1 and leaves no DEST. An existing DEST is kept untouched with `DEST exists, kept` printed, exit 0
  (the contract's `cp -n` semantics). A conversion failure exits 1. Success prints `DEST <bytes> q<quality>
  <width>x<height>`.
- `references/verify.md` step 3 says the tool implements the contract's copy step; canonical evidence names
  keep `<date>-<unit>-<HHMM>-<nn>-<slug>.jpg` (verify.md line 887; line 793 already says screenshots are
  JPEG). The controller re-scores FAIL items from the originals in `.superpowers/sdd/screenshots/` and cites
  the `evidence/` path. The walker reviewer keeps reading the originals.
- `verify.md` line 886 says "copies ... with `cp -n`". The user authorised (2026-09-15, this session) the
  one-line edit under gate 10: the line says the controller archives each returned path with
  `evidence_image.py`, which keeps `cp -n`'s no-overwrite behaviour. No other line of `verify.md` changes;
  the `repair` entry quotes the ruling.
- Existing PNGs in `evidence/` stay. No history rewrite.
- Tests: a generated image round-trips under the cap; an existing DEST is kept with exit 0; skipped when
  `magick` is absent.

### 3.7 Dead instruction text

User-owned text, edited in the implementation session under the user's direction (the loop never does this):

- `SKILL.md` U8 scheduling exception: the dated 6C deadline (Sun 2026-09-13) has passed; the paragraph shrinks
  to the still-live rule that ready 6x milestones may be planned in parallel with separate plans, branches and
  ledgers, controller git serial, ceilings unchanged, and 6C `planned` until full acceptance. The same
  deadline text in SKILL.md Orient rule 0 (lines 82-83, the 6C deadline-slice sentence), `recovery.md` line 35
  ("Recheck U8's 6C deadline at every task boundary"), `phase.md` line 63 and the roadmap calendar row "At
  resume, and each unit/task boundary until the Sunday/Monday duties" (line 556) is removed.
- `SKILL.md` kickoff block and `docs/runbooks/claude-omarchy-restart.md`: "select `/effort` high explicitly;
  the saved default may be xhigh, which the skill measured at three to four times the tokens for no gain."
- `roadmap.md` Secrets: the production Kalshi key row keeps one clause, `done, read-scoped, journal 202`.
- `roadmap.md` Operator calendar: the four past-dated rows (the drill before 2026-09-12, journaled as entries
  18, 29 and 133; Sun 2026-09-13; Mon 2026-09-14; Tue 2026-09-15) are removed; rows whose Duty names the NAS in any
  form (an `ssh` command, a `/volume1` path, or the words "on the NAS") are rewritten to the Omarchy
  equivalent only where a Makefile target or runbook command exists, otherwise left as they are with the note
  `(NAS command; see linux-controller.md)`.
- `roadmap.md` User-side TODOs: the twelve `- [x]` items are deleted; each cites its journal entry.
- The Decisions table, Standing authorizations, Invariants, Phases and Pre-loaded decisions are not touched.

The migration is recorded as one `repair` journal entry in the template (body at most 6,000; `- Orient: none
(user-directed session)`): files restructured, the classification counts (Open n, Watch n, Closed n) with the ambiguous row numbers under
`Needs you`, the count of receipts not found elsewhere, the launcher change, the new commands, and the path of
the migration report that carries the tables.

## 4. Data flow after the change

Wake: `bootstrap` (checkpoint) then `bootstrap authority` then `bootstrap operator`, three tool calls of at
most 27,000 characters each, then the routed procedure and phase decisions as today. Unit end: journal entry
in the template, `state.md` rewritten in the schema, fix rows moved on close, `check`, one docs commit.
Verify walkthrough: captures to `.superpowers/sdd/screenshots/`, reviewer reads originals, controller runs
`evidence_image.py` per file into `evidence/`. Compaction: the hooks are unchanged; the resumed context runs
the three bootstrap parts and recovery.md as today.

## 5. Error handling

- A bootstrap part over budget prints the `BUDGET EXCEEDED` line first; the loop reads the persisted file by
  the `Source:` ranges. A `checkpoint` overage is loop-owned: shrink state, Open rows or the journal entry.
  An `authority` or `operator` overage is `NEEDS USER`: one `Anomalies:` line and the next report's Needs you.
- A `BLOCK` from `check` costs one turn: the message names the remedy. The loop never bypasses a `BLOCK` by
  deleting an unresolved fact, and never treats `NEEDS USER` as a reason to edit user-owned text.
- `append` refusing a long line loses nothing: the loop writes the detail to the report or brief and appends
  the short line.
- `evidence_image.py` failing on one capture: the item is recorded as pending visual evidence with the source
  path, per the existing rule; never an invented pass. A kept existing DEST is not a failure.
- If compaction at 300k produces a reconcile mistake (a duplicate entry, a duplicate dispatch), Orient rule 0
  and the ceilings apply as today; the day-after measurement records it and the user decides the threshold.

## 6. Sequencing and safety

1. Preconditions: `scripts/autopilot-session.sh status` shows the controller lock free and no
   `sports-autopilot` tmux or herdr session; no other Claude session has this checkout as its working
   directory; `git status --short` shows only `.claude/settings.local.json` untracked. The fix 78 part 2
   worktree (`../sports-wt/fix-2026-09-15-executor-batch-2`, branch at e0c9888, review pending), its test
   database, the reminders and everything under `.superpowers/sdd/` and `~/.cache/sports-harness/` are left
   alone: never `git worktree prune`, `make worktree-rm`, `git branch -D`, `git clean`, `make testdb-prune`, a
   fixture-grant revoke, or `git add -A`.
2. Branch `context-hygiene-2026-09-15` from main. Order: scripts and tests (3.1, 3.3, 3.5 tool, 3.6) with
   the fixture tests green; then the migration report and `fixes.md` (3.2), the state rewrite (3.4), the
   skill and roadmap text (3.2, 3.7); then the live tests and `check` green; then the launcher and never-edit
   lists (3.5). The independent read-only reviewer of 3.2 and 3.4 runs before the merge.
3. Before the fast-forward merge the user rules on the classification table and approves the diff to the
   user-owned roadmap sections and the skill. Then `--ff-only` to main; commits: scripts and tests; migration
   (fixes.md, state, report, repair entry); skill and roadmap text; launcher. No push (U7 governs pushes).
   Verification that nothing else moved: `git diff <pre-migration sha>..HEAD --stat --
   docs/superpowers/autopilot/verify.md .claude/settings.json` shows exactly one line changed in verify.md
   (the 3.6 edit) and nothing in settings.json; `git diff --numstat <pre-migration sha>..HEAD --
   docs/superpowers/autopilot/journal.md` shows 0 in the deletions column (append-only); the deploy trigger diff is
   empty; `git diff --stat <pre-migration sha>..HEAD -- scripts` shows only `autopilot-session.sh`.
4. `git checkout main` with a clean tree before the user relaunches the controller with the launcher; its
   first preflight runs `check`.
5. After the first full controller day: `usage.py` against the baseline; the user rules on the threshold.

## 7. Acceptance

1. `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests` passes with the new tests.
2. `python3 .claude/skills/autopilot/scripts/context.py check` exits 0 on main after the merge with no
   `BLOCK` line.
3. `check` on main prints neither `BLOCK` nor `NEEDS USER`: every bootstrap part is under 27,000 characters
   at merge time (the live test holds the checkpoint part to it permanently).
4. `state.md` has only the fixed headings, the file minus `Resume first` is at most 8,000 characters, `Resume
   first` at most 4,000, and `Resume first` carries every fact of the 17:34 CT stopping point (commit 9fcff02)
   per the fact inventory plus the batch-clock ruling.
5. `fixes.md` exists with `Open`, `Watch` and `Closed`; their rows total 63; every row has six cells; the
   baseline line lists 63 numbers; the roadmap's `Carried fixes` section has no table rows; `grep -rn
   'Carried fixes' .claude/skills/autopilot/SKILL.md .claude/skills/autopilot/references` returns nothing.
6. `grep -n 'autocompact 300k' scripts/autopilot-session.sh .claude/skills/autopilot/SKILL.md` matches in
   both; `grep -n '500k'` in both returns nothing.
7. The deploy trigger diff between the deployed build and main is empty; `git diff --stat <pre-migration
   sha>..HEAD -- scripts` shows only `autopilot-session.sh`.
8. The `repair` journal entry exists and `check --entry N` reports no `BLOCK`.
9. The migration report exists with the classification table, the fact inventory and the receipts grep, and
   the user's ruling on the ambiguous rows is recorded in the journal.
10. Day-after: `usage.py` rows for the first 300k day recorded in the journal by the user or a review
    session, judged against 3.5.

## 8. Rejected alternatives

- Raising `BASH_MAX_OUTPUT_LENGTH` for the controller: the CLI has the knob, but a larger result per call
  grows the context the change is meant to shrink; parts that fit are also explicit about what was read.
- Filtering fix rows by a Status column inside the roadmap: keeps 90 KB of history in the authority file and
  puts the filter logic where a heading rename breaks it silently.
- Renumbering the duplicate row 56: journal entries and evidence cite the numbers.
- Keeping screenshots out of git or in LFS: both leave the Monday bundle without the visual evidence.
- Enforcing `check` as a git pre-commit hook: a failing commit mid-unit leaves the loop in an unrecorded state;
  the documented command plus the live test is enough for now.
- A single body cap for every journal unit: the verify and deploy contracts require per-item lines that do
  not fit 3,000 characters.
- Splitting verify.md by cadence: deferred by ruling until the measurement shows what a verify pass costs.

## 9. Risks

- The authority part has about 4 KB of headroom; a long new user decision could push it over. The
  `NEEDS USER` line makes that visible the day it happens; the remedy is the user's (move older decision
  quotes to the journal), not the loop's.
- The classification may still leave 40 or more rows in `Watch`; they are not printed at wake, so the cost is
  the one-time condensation and the user's ruling on the table.
- Three bootstrap calls per wake instead of one: they run in one turn as parallel tool calls; the footer
  carries the order.
- JPEG artifacts: only the archived copy is compressed; the reviewer and the controller's re-score use
  originals.
- The check's unit list may reject a legitimate new unit name: unknown units require only Result and Next.
- The day-after measurement depends on a full controller day at 300k; a short, quiet or interrupted day is
  reported as inconclusive, not as a pass.
- The hotfix batch wall-clock (3 h, started 16:59 CT Sep 15) was interrupted by the user's stop at 17:34 CT;
  the user ruled that the stop suspends it (3.4), so the first wake does not journal `ceiling`.
