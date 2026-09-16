# Context Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task, inline in the implementing session. In this repository a project hook confines every subagent to a read-only Bash sandbox of the checkout, so subagents cannot implement tasks here; they serve only as the read-only reviewer of Task 10. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the autopilot loop's per-wake context fit the harness, make its recording rules executable, move fix rows and history out of the current-state files, lower the compaction threshold with a measurement, compress evidence images, and prune dead instruction text, all while the loop is stopped.

**Architecture:** Every script change lives in `.claude/skills/autopilot/scripts/` (excluded from the deploy trigger) with unit tests in `.claude/skills/autopilot/tests/`. `context.py` gains a three-part bootstrap, a `check` command and a ledger-line cap; two new scripts (`usage.py`, `evidence_image.py`) stand alone. The document migration (fixes.md, state.md, roadmap, skill text) follows the scripts and is recorded in one migration report and one `repair` journal entry. The launcher change lands last.

**Tech Stack:** Python 3.12 standard library only (no new dependencies), `unittest` run under the repo's pytest, ImageMagick `magick` on the host, git.

**Spec:** `docs/superpowers/specs/2026-09-15-context-hygiene-design.md` (revision 3, commit 184f48f). The plan argues from the spec; read both. Revision 2 of this plan folds in two adversarial reviews (one executed the code, one dry-ran the migration).

## Global Constraints

- The loop is stopped: `scripts/autopilot-session.sh status` must print `controller lock: free` and list no `sports-autopilot` session before Task 0 ends; no other Claude session may have this checkout as its working directory.
- Never run: `git worktree prune`, `make worktree-rm`, `git branch -D`, `git clean`, `make testdb-prune`, any fixture-grant revoke, `git add -A`, `git push`. Stage files by explicit path only. `.claude/settings.local.json` stays untracked.
- Never touch: `../sports-wt/fix-2026-09-15-executor-batch-2` (branch at e0c9888, review pending), `.superpowers/sdd/`, `~/.cache/sports-harness/`, `.claude/settings.json`, the hook scripts (`recovery_hook.py`, `worker_guard.py`), the v2 spec, the roadmap's Decisions table, Standing authorizations, Invariants, Phases table and Pre-loaded decisions.
- `verify.md` changes on exactly one line (886), authorised by the user 2026-09-15.
- Budgets, verbatim from the spec: bootstrap part 27,000 characters; `state.md` minus `Resume first` 8,000 and `Resume first` 4,000; Open and Watch rows 600; ledger line 400; journal heading text 120; journal body 6,000 for verify, deploy, repair and 3,000 otherwise (quoted `>` lines exempt in decision and gate entries); evidence JPEG 400 KB target, 800 KB hard limit; `--autocompact 300k`.
- Journal entries are append-only once committed; the migration adds exactly one `repair` entry, written last (Task 12) so it never needs editing, and edits nothing above it.
- Numbers that move (the last journal entry, the pre-migration sha, the stopping-point time) are computed at execution time, never copied from this plan: the controller wrote entry 246 at 18:41 CT after the plan was drafted. `<pre-migration sha>` is `/tmp/context-hygiene-pre.sha`, or, after a reboot, the sha recorded in the migration report's header, or `git merge-base main context-hygiene-2026-09-15`.
- Fix rows are never deleted and never renumbered; the duplicate 56 stays as `56 (dup)`.
- Tests run as `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests` from the checkout root. `make test` is not needed (no file under `harness/` or `tests/` changes).
- Commit messages start `docs:` for document-only commits and `chore(autopilot):` for script and test commits; every commit ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Times in commit messages, the report and the journal come from `TZ=America/Chicago date '+%F %H:%M CT'`, never from memory.

---

## File structure

| File | Responsibility |
|---|---|
| `.claude/skills/autopilot/scripts/context.py` (modify) | canonical readers; three-part bootstrap; `check`; `append` with the cap |
| `.claude/skills/autopilot/scripts/usage.py` (create) | transcript usage table per session and per CT day |
| `.claude/skills/autopilot/scripts/evidence_image.py` (create) | screenshot to capped JPEG with `cp -n` semantics |
| `.claude/skills/autopilot/tests/fixture_repo.py` (create) | small canonical-file fixture used by the new tests |
| `.claude/skills/autopilot/tests/test_context_check.py` (create) | bootstrap parts, degrade rules, `check`, `append` cap |
| `.claude/skills/autopilot/tests/test_usage.py` (create) | usage table from a synthetic transcript |
| `.claude/skills/autopilot/tests/test_evidence_image.py` (create) | conversion, cap, keep-existing |
| `.claude/skills/autopilot/tests/test_live_repo.py` (create, Task 12) | `check` and the checkpoint budget against the real files |
| `.claude/skills/autopilot/tests/test_context_recovery.py` (modify) | drop the two single-output bootstrap tests |
| `docs/superpowers/autopilot/fixes.md` (create) | Open / Watch / Closed fix rows with the baseline line |
| `docs/superpowers/autopilot/reports/2026-09-15-context-hygiene-migration.md` (create) | classification table, fact inventory, receipts grep, reviewer findings |
| `docs/superpowers/autopilot/roadmap.md` (modify) | Carried fixes becomes a pointer; edit rights; Secrets row; calendar rows; TODOs |
| `docs/superpowers/autopilot/state.md` (rewrite) | fixed schema |
| `docs/superpowers/autopilot/journal.md` (append) | one `repair` entry |
| `docs/superpowers/autopilot/verify.md` (modify line 886) | archive tool named |
| `.claude/skills/autopilot/SKILL.md` and `references/{recording,preflight,recovery,hotfix,verify,phase,deploy}.md` (modify) | three parts, fixes.md, check rule, schema, dead text, launcher excluded from the deploy trigger |
| `docs/runbooks/claude-omarchy-restart.md`, `CLAUDE.md` (modify) | three parts; effort note |
| `scripts/autopilot-session.sh` (modify) | `--autocompact 300k` |

---

### Task 0: Preconditions and branch

**Files:** none created.

- [ ] **Step 1: Confirm the loop is stopped and the tree is clean**

Run:
```bash
cd /home/trey/dev/sports && scripts/autopilot-session.sh status && git status --short && git branch --show-current && git log --oneline -3
```
Expected: `controller lock: free`, no `sports-autopilot` session in the tmux list, `git status --short` prints at most `?? .claude/settings.local.json`, branch `main`. If the lock is held or a session is listed, stop and tell the user (the user closes the controller; the plan never does); do not continue. Record the header line of `state.md` (`sed -n '3p' docs/superpowers/autopilot/state.md`): its `Updated ... CT` time is the last controller checkpoint, used in Task 7.

- [ ] **Step 2: Record the pre-migration sha and create the branch**

Run:
```bash
cd /home/trey/dev/sports && PRE=$(git rev-parse HEAD) && echo "$PRE" > /tmp/context-hygiene-pre.sha && git checkout -b context-hygiene-2026-09-15 && cat /tmp/context-hygiene-pre.sha
```
Expected: the branch is created; the sha printed is main's head. Every later task that needs `<pre-migration sha>` reads `/tmp/context-hygiene-pre.sha`.

- [ ] **Step 3: Baseline the skill tests**

Run: `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests`
Expected: all pass (49 tests today: 12 in test_context_recovery, 26 in test_worker_guard, 11 in test_worker_tools). If not, stop and report.

---

### Task 1: Three-part bootstrap in `context.py`

**Files:**
- Create: `.claude/skills/autopilot/tests/fixture_repo.py`
- Create: `.claude/skills/autopilot/tests/test_context_check.py` (the `BootstrapPartsTests` class; Task 2 adds more classes to the same file)
- Modify: `.claude/skills/autopilot/scripts/context.py`
- Modify: `.claude/skills/autopilot/tests/test_context_recovery.py:67-95` (delete two tests)

**Interfaces:**
- Produces: `context.bootstrap(root, part="checkpoint") -> str`; `context.checkpoint_raw(root) -> (str, list)`; `context.authority_chunks(root)`, `context.operator_chunks(root)` returning `list[(title, text)]`; `context.assemble(chunks) -> str`; `context.entries(text) -> list[(number, start, end, heading, body)]`; `context.entry(text, number)`; `context.cells(row) -> list[str]`; constants `BUDGET`, `OPEN_ROWS_SHOWN`, `OPERATOR_SECTIONS`, `AUTHORITY_REQUIRED`, `ROADMAP`, `STATE`, `JOURNAL`, `FIXES`, `ROW_RE`. The CLI `context.py bootstrap [checkpoint|authority|operator]`.
- Consumes: the existing `headings`, `section`, `journal_tail`, `read`, `render`.

- [ ] **Step 1: Write the fixture module**

Create `.claude/skills/autopilot/tests/fixture_repo.py`:

```python
"""Small canonical-file fixture for the context reader and check tests."""

from pathlib import Path

ROADMAP = """# Roadmap: fixture

Preamble line. Anything not listed is the model's call.

## Phases (spec §15)

| Phase | Status |
|---|---|
| 1 | done |

## Current host and restart setup (user-directed, 2026-09-12)

Host text.

## Decisions (2026-09-07)

| id | Decision |
|---|---|
| U1 | one |

## Standing authorizations (user, 2026-09-07)

Authorized.

## Files and sections the loop may edit

Edit rights.

## Invariants the loop never changes (hard-forbidden; always a gate, never a ruling)

Invariants.

## Secrets (provision when convenient; the loop never blocks on them)

Secrets table.

## Pre-loaded decisions

Preamble of decisions.

### Phase 4: Kalshi

Phase 4 detail.

## Operator calendar (America/Chicago)

| When | Duty |
|---|---|
| Daily | line |

## User-side TODOs

- todo one

## Carried fixes

Rows live in `fixes.md` (`Open`, `Watch`, `Closed`). This section holds no rows.
"""

STATE = """# Autopilot checkpoint

Updated 2026-09-16 09:00 CT. Last journal entry: 2.

## Right now

Released.

## Order of work

1. verify.

## Active units

- none

## Pending results

- none

## Counters and deadlines

- dispatches 0

## Constraints

Paper-only.
"""

JOURNAL = """# Journal

## 1. preflight - session start - 2026-09-16 08:00 CT
- Orient: rule 3 - clock
- Result: done
- Next: verify, wakeup none

## 2. verify - build abc - 2026-09-16 09:00 CT
- Orient: rule 3 - no verify since deploy
- Result: done
- Verification: PASS 3/3 (evidence: evidence/x.txt)
- Next: idle, wakeup 10:00 CT
"""

FIXES = """# Fix rows

Rules paragraph.

Baseline numbers (2026-09-15): 16, 20, 20

## Open

| # | Finding | Files | Change | Covering test | Deploy |
|---|---|---|---|---|---|
| 16 | symptom (journal 1) | a.py | change | test_a | actionable |

## Watch

| # | Finding | Files | Change | Covering test | Deploy |
|---|---|---|---|---|---|
| 20 | observation (journal 2) | b.py | none | none | phase-assigned |
| 20 (dup) | second twenty | c.py | none | none | user |

## Closed

| # | Finding | Files | Change | Covering test | Deploy |
|---|---|---|---|---|---|
"""


def make_repo(root, roadmap=ROADMAP, state=STATE, journal=JOURNAL, fixes=FIXES):
    """Write the four canonical files under root; None skips a file."""
    target = Path(root) / "docs/superpowers/autopilot"
    target.mkdir(parents=True, exist_ok=True)
    for name, text in (("roadmap.md", roadmap), ("state.md", state),
                       ("journal.md", journal), ("fixes.md", fixes)):
        if text is not None:
            (target / name).write_text(text)
    return Path(root)
```

- [ ] **Step 2: Write the failing bootstrap tests**

Create `.claude/skills/autopilot/tests/test_context_check.py`:

```python
"""Three-part bootstrap, degrade rules, check and the ledger cap. Fixture files only."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

from fixture_repo import FIXES, JOURNAL, STATE, make_repo

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts/context.py"


def module(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


context = module("context")


def run(root, *args):
    return subprocess.run(["python3", str(SCRIPT), "--root", str(root), *args],
                          capture_output=True, text=True)


class BootstrapPartsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = make_repo(self.temp.name)

    def test_checkpoint_has_state_two_entries_open_rows_and_footer(self):
        output = context.bootstrap(self.root, "checkpoint")
        self.assertIn("Updated 2026-09-16 09:00 CT", output)
        self.assertIn("## 1. preflight", output)
        self.assertIn("## 2. verify", output)
        self.assertIn("| 16 | symptom", output)
        self.assertNotIn("| 20 |", output)
        self.assertTrue(output.rstrip().endswith("active ledgers."))
        self.assertEqual(context.bootstrap(self.root), output)

    def test_authority_and_operator_split_every_roadmap_section(self):
        authority = context.bootstrap(self.root, "authority")
        operator = context.bootstrap(self.root, "operator")
        roadmap = context.read(self.root, context.ROADMAP)
        for _, level, title in context.headings(roadmap):
            if level == 2:
                target, other = ((operator, authority) if title in context.OPERATOR_SECTIONS
                                 else (authority, operator))
                self.assertIn(f"## {title}", target)
                self.assertNotIn(f"## {title}", other)
        self.assertIn("Anything not listed is the model's call", authority)
        self.assertIn("Preamble of decisions", authority)
        self.assertNotIn("Phase 4 detail", authority)
        self.assertNotIn("BUDGET EXCEEDED", authority + operator)

    def test_new_authority_section_is_included_and_missing_required_raises(self):
        path = self.root / context.ROADMAP
        path.write_text(path.read_text() + "\n## New dated constraint\n\nPreserve this new rule.\n")
        self.assertIn("Preserve this new rule.", context.bootstrap(self.root, "authority"))
        path.write_text("# Incomplete authority\n")
        with self.assertRaises(ValueError):
            context.bootstrap(self.root, "authority")
        with self.assertRaises(ValueError):
            context.bootstrap(self.root, "operator")

    def test_missing_state_and_fixes_print_reconstruct_lines(self):
        (self.root / context.STATE).unlink()
        (self.root / context.FIXES).unlink()
        output = context.bootstrap(self.root)
        self.assertIn("STATE MISSING", output)
        self.assertIn("FIXES MISSING", output)
        self.assertIn("## 2. verify", output)

    def test_open_rows_are_capped_at_ten_with_a_pointer(self):
        rows = "".join(f"| {n} | symptom {n} | a.py | change | test | actionable |\n" for n in range(100, 112))
        fixes = FIXES.replace("| 16 | symptom (journal 1) | a.py | change | test_a | actionable |\n", rows)
        fixes = fixes.replace("Baseline numbers (2026-09-15): 16, 20, 20", "Baseline numbers (2026-09-15): 20, 20")
        (self.root / context.FIXES).write_text(fixes)
        output = context.bootstrap(self.root)
        self.assertIn("| 109 |", output)
        self.assertNotIn("| 110 |", output)
        self.assertIn("2 more Open rows: run context.py section docs/superpowers/autopilot/fixes.md Open", output)

    def test_checkpoint_degrades_to_one_entry_then_elides_quotes_then_warns(self):
        big = "x" * 30_000
        journal = JOURNAL.replace("- Orient: rule 3 - clock\n", "- Orient: rule 3 - clock\n" + big + "\n")
        path = self.root / context.JOURNAL
        path.write_text(journal)
        output = context.bootstrap(self.root)
        self.assertNotIn(big, output)
        self.assertIn("## 2. verify", output)
        self.assertIn("journal entry 1 omitted for budget: run context.py journal docs/superpowers/autopilot/journal.md --count 2", output)
        self.assertNotIn("BUDGET EXCEEDED", output)
        quote = "\n".join("> " + "q" * 100 for _ in range(300))
        path.write_text(journal + f"\n## 3. decision - ruling - 2026-09-16 10:00 CT\n\n{quote}\n\n- Result: recorded\n- Next: verify\n")
        output = context.bootstrap(self.root)
        self.assertIn("## 3. decision", output)
        self.assertNotIn("qqqq", output)
        self.assertIn("- Result: recorded", output)
        self.assertIn("quoted block omitted for budget: run context.py journal docs/superpowers/autopilot/journal.md --count 1", output)
        self.assertNotIn("BUDGET EXCEEDED", output)
        (self.root / context.STATE).write_text(STATE + "z" * 30_000)
        output = context.bootstrap(self.root)
        self.assertTrue(output.startswith("BUDGET EXCEEDED: checkpoint "), output[:120])

    def test_authority_over_budget_warns_instead_of_raising(self):
        path = self.root / context.ROADMAP
        path.write_text(path.read_text().replace("Invariants.\n", "Invariants.\n" + "i" * 30_000 + "\n"))
        output = context.bootstrap(self.root, "authority")
        self.assertTrue(output.startswith("BUDGET EXCEEDED: authority "), output[:120])
        self.assertIn("largest sections: Invariants the loop never changes", output)

    def test_cli_default_part_and_named_parts(self):
        for args, marker in ((("bootstrap",), "Updated 2026-09-16"), (("bootstrap", "authority"), "## Phases"),
                             (("bootstrap", "operator"), "## User-side TODOs")):
            result = run(self.root, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(marker, result.stdout)
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests/test_context_check.py`
Expected: FAIL (AttributeError: module has no attribute `ROADMAP` / bootstrap takes 1 positional argument).

- [ ] **Step 4: Rewrite `context.py` with the three parts**

Replace the whole file `.claude/skills/autopilot/scripts/context.py` with:

```python
#!/usr/bin/env python3
"""Read canonical Markdown sections without summaries or silent truncation; check recording budgets."""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
AUTOPILOT = Path("docs/superpowers/autopilot")
ROADMAP = AUTOPILOT / "roadmap.md"
STATE = AUTOPILOT / "state.md"
JOURNAL = AUTOPILOT / "journal.md"
FIXES = AUTOPILOT / "fixes.md"

BUDGET = 27_000          # characters per bootstrap part; the harness persists results over about 30,000
OPEN_ROWS_SHOWN = 10
STATE_LIMIT = 8_000      # state.md minus its Resume first section
RESUME_LIMIT = 4_000
ROW_LIMIT = 600          # Open and Watch rows
LEDGER_LIMIT = 400
HEADING_LIMIT = 120      # journal heading text after "## "
BODY_LIMIT = 3_000
BODY_LIMITS = {"verify": 6_000, "deploy": 6_000, "repair": 6_000}
ORIENT_UNITS = {"preflight", "hotfix", "deploy", "verify", "operate", "phase", "plan-next", "idle", "repair"}
VERIFICATION_UNITS = {"verify", "deploy"}
QUOTE_UNITS = {"decision", "gate"}

OPERATOR_SECTIONS = (
    "Current host and restart setup (user-directed, 2026-09-12)",
    "Secrets (provision when convenient; the loop never blocks on them)",
    "Operator calendar (America/Chicago)",
    "User-side TODOs",
)
AUTHORITY_REQUIRED = {
    "Phases (spec §15)", "Decisions (2026-09-07)", "Standing authorizations (user, 2026-09-07)",
    "Files and sections the loop may edit",
    "Invariants the loop never changes (hard-forbidden; always a gate, never a ruling)",
    "Carried fixes", "Pre-loaded decisions",
}
STATE_SECTIONS = ("Resume first", "Right now", "Order of work", "Active units",
                  "Pending results", "Counters and deadlines", "Constraints")
STATE_REQUIRED = STATE_SECTIONS[1:]
FIX_SECTIONS = ("Open", "Watch", "Closed")
HEADING_RE = re.compile(r"^(\d+)\. ([a-z][a-z0-9-]*) - (.+) - "
                        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?:-\d{2}:\d{2})? CT)$")
ROW_RE = re.compile(r"^\|\s*\d+")
NUMBER_RE = re.compile(r"^(\d+)( \(dup\))?$")
BASELINE_RE = re.compile(r"^Baseline numbers \([\d-]+\): (.+)$", re.M)
FOOTER = ("Next: run context.py bootstrap authority, then context.py bootstrap operator; then the selected "
          "procedure, applicable Pre-loaded decisions and active ledgers.\n")
STATE_MISSING = "STATE MISSING: reconstruct from journal and active ledgers; do not reset counters.\n"
FIXES_MISSING = ("FIXES MISSING: reconstruct fixes.md from roadmap.md history and the journal before Orient; "
                 "do not select idle\n")


def headings(text):
    """Return (zero-based line, level, title), ignoring fenced code examples."""
    result = []
    fence = None
    for number, line in enumerate(text.splitlines(keepends=True)):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if marker:
            run, rest = marker.groups()
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not rest.strip():
                fence = None
            continue
        if fence is not None:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            result.append((number, len(match[1]), match[2]))
    return result


def section(text, title):
    entries_ = headings(text)
    matches = [item for item in entries_ if item[2] == title]
    if len(matches) != 1:
        raise ValueError(f"expected one heading {title!r}; found {len(matches)}")
    start, level, _ = matches[0]
    end = next((line for line, depth, _ in entries_ if line > start and depth <= level),
               len(text.splitlines(keepends=True)))
    return start, end, "".join(text.splitlines(keepends=True)[start:end])


def entries(text):
    """Every numbered journal entry as (number, start line, end line, heading text, body)."""
    lines = text.splitlines(keepends=True)
    starts = [(line, title) for line, level, title in headings(text)
              if level == 2 and re.match(r"\d+\.\s", title)]
    result = []
    for index, (start, title) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        body = "".join(lines[start + 1:end]).rstrip()
        result.append((int(title.split(".", 1)[0]), start, end, title, body))
    return result


def entry(text, number):
    matches = [item for item in entries(text) if item[0] == number]
    if len(matches) != 1:
        raise ValueError(f"expected one journal entry {number}; found {len(matches)}")
    return matches[0]


def journal_tail(text, count=2):
    starts = [line for line, level, title in headings(text)
              if level == 2 and re.match(r"\d+\.\s", title)]
    if not starts:
        raise ValueError("no numbered journal entries found")
    lines = text.splitlines(keepends=True)
    start = starts[-count] if len(starts) >= count else starts[0]
    return start, len(lines), "".join(lines[start:])


def cells(row):
    """Table cells of one Markdown row. A literal pipe inside a cell is written as backslash-pipe;
    the cells are returned as written (the backslash is kept, nothing is unescaped)."""
    inner = row.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", inner)]


def read(root, path):
    target = (root / path).resolve()
    target.relative_to(root.resolve())
    return target.read_text()


def render(path, selected):
    start, end, content = selected
    return f"Source: {path}:{start + 1}-{end}\n{content.rstrip()}\n"


def assemble(chunks):
    return "\n".join(text for _, text in chunks)


def budget_warning(part, chunks, output):
    largest = sorted(chunks, key=lambda item: -len(item[1]))[:3]
    summary = ", ".join(f"{title} {len(text)}" for title, text in largest)
    return f"BUDGET EXCEEDED: {part} {len(output)} chars > {BUDGET}; largest sections: {summary}\n\n"


def open_rows_chunk(root):
    try:
        fixes = read(root, FIXES)
        start, end, text = section(fixes, "Open")
    except (OSError, ValueError):
        return FIXES_MISSING
    lines = text.splitlines(keepends=True)
    rows = [index for index, line in enumerate(lines) if ROW_RE.match(line)]
    if len(rows) > OPEN_ROWS_SHOWN:
        lines = lines[:rows[OPEN_ROWS_SHOWN]] + [
            f"{len(rows) - OPEN_ROWS_SHOWN} more Open rows: run context.py section {FIXES} Open\n"]
    return render(FIXES, (start, end, "".join(lines)))


def checkpoint_chunks(root, degrade):
    """degrade 0: two entries; 1: last entry only; 2: last entry without its quoted block."""
    chunks = []
    try:
        state = read(root, STATE)
    except OSError:
        chunks.append(("state.md", STATE_MISSING))
    else:
        chunks.append(("state.md", render(STATE, (0, len(state.splitlines()), state))))
    journal = read(root, JOURNAL)
    items = entries(journal)
    if not items:
        raise ValueError("no numbered journal entries found")
    lines = journal.splitlines(keepends=True)
    if degrade == 0 and len(items) >= 2:
        start = items[-2][1]
        chunks.append(("journal tail", render(JOURNAL, (start, len(lines), "".join(lines[start:])))))
    else:
        number, start, end, _, _ = items[-1]
        block = lines[start:end]
        note = "" if len(items) < 2 else (
            f"journal entry {number - 1} omitted for budget: run context.py journal {JOURNAL} --count 2\n")
        if degrade >= 2:
            block = [line for line in block if not line.startswith(">")]
            note += f"quoted block omitted for budget: run context.py journal {JOURNAL} --count 1\n"
        chunks.append(("journal tail", render(JOURNAL, (start, end, "".join(block))) + note))
    chunks.append(("fixes.md Open", open_rows_chunk(root)))
    chunks.append(("footer", FOOTER))
    return chunks


def checkpoint_raw(root):
    """The checkpoint part after the degrade rules, without the warning line, and its chunks."""
    for degrade in (0, 1, 2):
        chunks = checkpoint_chunks(root, degrade)
        output = assemble(chunks)
        if len(output) <= BUDGET:
            break
    return output, chunks


def authority_chunks(root):
    roadmap = read(root, ROADMAP)
    items = headings(roadmap)
    missing = AUTHORITY_REQUIRED - {title for _, _, title in items}
    if missing:
        raise ValueError(f"roadmap headings changed; read full roadmap: {sorted(missing)}")
    lines = roadmap.splitlines(keepends=True)
    first = next(line for line, level, _ in items if level == 2)
    chunks = [("preamble", render(ROADMAP, (0, first, "".join(lines[:first]))))]
    # Include future top-level authority sections automatically. Never filter by
    # phase status or guess whether a dated decision is still relevant.
    for _, level, title in items:
        if level != 2 or title in OPERATOR_SECTIONS:
            continue
        if title == "Pre-loaded decisions":
            start, end, _ = section(roadmap, title)
            child = next((line for line, depth, _ in items if start < line < end and depth == 3), end)
            chunks.append((title, render(ROADMAP, (start, child, "".join(lines[start:child])))))
        else:
            chunks.append((title, render(ROADMAP, section(roadmap, title))))
    return chunks


def operator_chunks(root):
    roadmap = read(root, ROADMAP)
    present = {title for _, level, title in headings(roadmap) if level == 2}
    missing = set(OPERATOR_SECTIONS) - present
    if missing:
        raise ValueError(f"roadmap headings changed; read full roadmap: {sorted(missing)}")
    return [(title, render(ROADMAP, section(roadmap, title))) for title in OPERATOR_SECTIONS]


def bootstrap(root, part="checkpoint"):
    """Keep authority intact; only phase-specific decisions load on demand."""
    if part == "checkpoint":
        output, chunks = checkpoint_raw(root)
    elif part == "authority":
        chunks = authority_chunks(root)
        output = assemble(chunks)
    elif part == "operator":
        chunks = operator_chunks(root)
        output = assemble(chunks)
    else:
        raise ValueError(f"unknown bootstrap part {part!r}")
    if len(output) > BUDGET:
        output = budget_warning(part, chunks, output) + output
    return output


def append_line(path, text, now=None):
    """Append one ledger line stamped from the clock (America/Chicago), never from memory."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if len(text) > LEDGER_LIMIT:
        raise ValueError(f"ledger line {len(text)} chars > {LEDGER_LIMIT}: write the detail to the "
                         "report or brief and reference its path")
    when = (now or datetime.now(ZoneInfo("America/Chicago"))).astimezone(ZoneInfo("America/Chicago"))
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(f"- {when:%Y-%m-%d %H:%M} CT: {text}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    boot = sub.add_parser("bootstrap")
    boot.add_argument("part", nargs="?", default="checkpoint", choices=("checkpoint", "authority", "operator"))
    for command in ("headings", "section", "journal", "append"):
        child = sub.add_parser(command)
        child.add_argument("path", type=Path)
        if command == "section":
            child.add_argument("title")
        if command == "append":
            child.add_argument("text")
        if command == "journal":
            child.add_argument("--count", type=int, default=2)
    args = parser.parse_args()
    try:
        if args.command == "bootstrap":
            output = bootstrap(args.root, args.part)
        elif args.command == "append":
            try:
                append_line(args.root / args.path, args.text)
            except ValueError as error:
                parser.exit(1, f"{error}\n")
            output = f"appended to {args.path}"
        else:
            text = read(args.root, args.path)
            if args.command == "headings":
                output = "\n".join(f"{args.path}:{line + 1} {'#' * level} {title}"
                                   for line, level, title in headings(text))
            elif args.command == "section":
                output = render(args.path, section(text, args.title))
            else:
                if args.count < 1:
                    raise ValueError("journal count must be positive")
                output = render(args.path, journal_tail(text, args.count))
        print(output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Context reader failed: {error}. Read the canonical file directly.\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Delete the two single-output tests**

In `.claude/skills/autopilot/tests/test_context_recovery.py` delete the methods `test_bootstrap_preserves_authority_and_live_checkpoints_verbatim` (lines 67-78) and `test_missing_state_does_not_reset_counts_and_new_authority_is_included` (lines 80-95) and the `shutil` import if it becomes unused. `ReaderTests` keeps its other three tests.

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests`
Expected: all pass (47 old + 8 new = 55).

- [ ] **Step 7: Commit**

```bash
git add .claude/skills/autopilot/scripts/context.py .claude/skills/autopilot/tests/fixture_repo.py .claude/skills/autopilot/tests/test_context_check.py .claude/skills/autopilot/tests/test_context_recovery.py
git commit -m "chore(autopilot): three-part bootstrap under a 27,000-character budget with degrade rules

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `context.py check` and the ledger cap

**Files:**
- Modify: `.claude/skills/autopilot/scripts/context.py` (add `check`, `check_state`, `check_fixes`, `check_entry`, `unit_of`; extend `main`)
- Modify: `.claude/skills/autopilot/tests/test_context_check.py` (add `CheckTests`, `AppendCapTests`)

**Interfaces:**
- Produces: `context.check(root, entry_number=None) -> list[(cls, message)]` with `cls` in `{"BLOCK", "NEEDS USER"}`; CLI `context.py check [--entry N]` exit 0 (ok or NEEDS USER only), 1 (a BLOCK), 2 (roadmap or journal unreadable or ambiguous). `context.py append` exit 1 with `ledger line <n> chars > 400: ...` on stderr.
- Consumes: Task 1's `checkpoint_raw`, `authority_chunks`, `operator_chunks`, `assemble`, `entries`, `entry`, `cells`, constants.

- [ ] **Step 1: Write the failing check tests**

Append to `.claude/skills/autopilot/tests/test_context_check.py`:

```python
class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = make_repo(self.temp.name)

    def blocks(self):
        return [message for cls, message in context.check(self.root) if cls == "BLOCK"]

    def assertBlock(self, fragment):
        blocks = self.blocks()
        self.assertTrue(any(fragment in message for message in blocks), f"{fragment!r} not in {blocks}")

    def test_fixture_passes_and_cli_says_ok(self):
        self.assertEqual(context.check(self.root), [])
        result = run(self.root, "check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("check: ok", result.stdout)

    def test_state_schema_order_and_sizes(self):
        path = self.root / context.STATE
        path.write_text(STATE.replace("## Constraints", "## Evidence receipts\n\nx\n\n## Constraints"))
        self.assertBlock("state.md: heading not in schema: 'Evidence receipts'")
        path.write_text(STATE.replace("## Active units\n\n- none\n\n", ""))
        self.assertBlock("state.md: required heading: 'Active units' count 0 vs 1")
        path.write_text(STATE.replace("## Right now\n\nReleased.\n\n", "") + "\n## Right now\n\nReleased.\n")
        self.assertBlock("state.md: heading order")
        path.write_text(STATE + "p" * 8_000)
        self.assertBlock("state.md: size without Resume first: ")
        path.write_text(STATE.replace("## Right now", "## Resume first\n\n" + "r" * 4_100 + "\n\n## Right now"))
        self.assertBlock("state.md: Resume first size: ")
        path.write_text(STATE.replace("## Right now", "## Resume first\n\nhandoff\n\n## Right now"))
        self.assertEqual(self.blocks(), [])
        path.unlink()
        self.assertBlock("state.md: missing")

    def test_fixes_sections_cells_cap_numbers_and_baseline(self):
        path = self.root / context.FIXES
        row = "| 16 | symptom (journal 1) | a.py | change | test_a | actionable |"
        path.write_text(FIXES.replace(row, row + " extra |"))
        self.assertBlock("fixes.md: Open row 16: cells: 7 vs 6")
        path.write_text(FIXES.replace("symptom (journal 1)", "s" * 700))
        self.assertBlock("fixes.md: Open row 16: length: ")
        path.write_text(FIXES.replace("| 20 (dup) | second twenty | c.py | none | none | user |\n", ""))
        self.assertBlock("fixes.md: baseline numbers missing: [20]")
        path.write_text(FIXES.replace("| 20 (dup) |", "| 20b |"))
        self.assertBlock("fixes.md: Watch row: number cell: '20b'")
        path.write_text(FIXES.replace("## Watch\n", "## Parked\n"))
        self.assertBlock("fixes.md: section: 'Watch' count 0 vs 1")
        path.write_text(FIXES.replace("Baseline numbers (2026-09-15): 16, 20, 20", "no baseline"))
        self.assertBlock("fixes.md: baseline line: 0 vs 1")
        path.write_text(FIXES + "| 30 | " + "c" * 2_000 + " | a | b | c | closed: journal 2 |\n")
        self.assertEqual(self.blocks(), [])
        path.unlink()
        self.assertBlock("fixes.md: missing")

    def test_roadmap_carried_fixes_must_hold_no_rows(self):
        path = self.root / context.ROADMAP
        path.write_text(path.read_text() + "\n| 99 | a | b | c | d | e |\n")
        self.assertBlock("roadmap.md: Carried fixes rows: 1 vs 0")

    def test_journal_heading_grammar_required_lines_and_body_caps(self):
        path = self.root / context.JOURNAL

        def blocks_for(heading, body):
            path.write_text(JOURNAL + f"\n## 3. {heading}\n{body}\n")
            return self.blocks()

        base = "- Orient: rule 1\n- Result: done\n- Next: idle\n"
        self.assertEqual(blocks_for("hotfix - fix 9 - 2026-09-16 10:00-10:20 CT", base), [])
        self.assertTrue(any("heading grammar" in m for m in blocks_for("hotfix — fix 9 — 2026-09-16 10:00 CT", base)))
        self.assertTrue(any("heading grammar" in m for m in blocks_for("phase start - t1 - 2026-09-16 10:00 CT", base)))
        self.assertTrue(any("heading grammar" in m for m in blocks_for("hotfix - fix 9 - 2026-09-16 10:00 CT (written later)", base)))
        self.assertTrue(any("heading length" in m for m in blocks_for("hotfix - " + "s" * 120 + " - 2026-09-16 10:00 CT", base)))
        self.assertTrue(any("missing line: - Orient:" in m for m in blocks_for("hotfix - fix 9 - 2026-09-16 10:00 CT", "- Result: done\n- Next: idle\n")))
        self.assertTrue(any("missing line: - Verification:" in m for m in blocks_for("verify - abc - 2026-09-16 10:00 CT", base)))
        self.assertTrue(any("missing line: - Result:" in m for m in blocks_for("decision - ruling - 2026-09-16 10:00 CT", "> words\n- Next: idle\n")))
        self.assertEqual(blocks_for("setup - unknown unit - 2026-09-16 10:00 CT", "- Result: done\n- Next: idle\n"), [])
        self.assertTrue(any("body length: 3" in m for m in blocks_for("hotfix - fix 9 - 2026-09-16 10:00 CT", base + "b" * 3_100)))
        verify = base + "- Verification: PASS\n"
        self.assertEqual(blocks_for("verify - abc - 2026-09-16 10:00 CT", verify + "b" * 5_000), [])
        self.assertTrue(any("body length: 6" in m for m in blocks_for("verify - abc - 2026-09-16 10:00 CT", verify + "b" * 6_100)))
        self.assertEqual(blocks_for("decision - ruling - 2026-09-16 10:00 CT", "> " + "q" * 5_000 + "\n- Result: recorded\n- Next: idle\n"), [])
        self.assertTrue(any("body length: 3" in m for m in blocks_for("decision - ruling - 2026-09-16 10:00 CT", "> q\n" + "a" * 3_100 + "\n- Result: recorded\n- Next: idle\n")))

    def test_entry_option_checks_a_named_entry(self):
        self.assertEqual(run(self.root, "check", "--entry", "1").returncode, 0)
        result = run(self.root, "check", "--entry", "9")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Context reader failed", result.stderr)

    def test_classes_and_exit_codes(self):
        path = self.root / context.ROADMAP
        path.write_text(path.read_text().replace("Secrets table.", "Secrets table. " + "s" * 30_000))
        result = run(self.root, "check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("NEEDS USER bootstrap operator: budget: ", result.stdout)
        self.assertNotIn("BLOCK", result.stdout)
        (self.root / context.STATE).write_text(STATE + "p" * 8_000)
        result = run(self.root, "check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("BLOCK state.md: size without Resume first", result.stdout)
        path.write_text("# gone\n")
        result = run(self.root, "check")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Context reader failed", result.stderr)

    def test_checkpoint_overage_class_depends_on_the_last_unit(self):
        (self.root / context.STATE).write_text(STATE + "p" * 30_000)
        findings = context.check(self.root)
        self.assertIn(("BLOCK", f"bootstrap checkpoint: budget: {len(context.checkpoint_raw(self.root)[0])} vs 27000"), findings)
        path = self.root / context.JOURNAL
        path.write_text(JOURNAL + "\n## 3. decision - ruling - 2026-09-16 10:00 CT\n> q\n- Result: recorded\n- Next: idle\n")
        findings = context.check(self.root)
        self.assertTrue(any(cls == "NEEDS USER" and message.startswith("bootstrap checkpoint: budget: ")
                            for cls, message in findings), findings)


class AppendCapTests(unittest.TestCase):
    def test_append_refuses_long_lines_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = Path(root) / "progress.md"
            ledger.write_text("- old\n")
            result = run(root, "append", "progress.md", "x" * 401)
            self.assertEqual(result.returncode, 1)
            self.assertIn("ledger line 401 chars > 400: write the detail to the report or brief and reference its path", result.stderr)
            self.assertEqual(ledger.read_text(), "- old\n")
            result = run(root, "append", "progress.md", "x" * 400)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(ledger.read_text().splitlines()), 2)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests/test_context_check.py -k "CheckTests or AppendCap"`
Expected: `CheckTests` fail with AttributeError (`check` missing); `AppendCapTests` passes already (the cap landed in Task 1) or fails only on the CLI message text.

- [ ] **Step 3: Add the check functions to `context.py`**

Insert after `bootstrap()` and before `append_line()`:

```python
def unit_of(item):
    match = HEADING_RE.match(item[3])
    return match.group(2) if match else ""


def check_state(text):
    findings = []
    titles = [title for _, level, title in headings(text) if level == 2]
    for title in titles:
        if title not in STATE_SECTIONS:
            findings.append(("BLOCK", f"state.md: heading not in schema: {title!r} vs the seven schema headings"))
    for title in STATE_REQUIRED:
        if titles.count(title) != 1:
            findings.append(("BLOCK", f"state.md: required heading: {title!r} count {titles.count(title)} vs 1"))
    order = [STATE_SECTIONS.index(title) for title in titles if title in STATE_SECTIONS]
    if order != sorted(order):
        findings.append(("BLOCK", "state.md: heading order: " + " > ".join(titles) + " vs schema order"))
    resume = ""
    if titles.count("Resume first") == 1:
        resume = section(text, "Resume first")[2]
    rest = len(text) - len(resume)
    if rest > STATE_LIMIT:
        findings.append(("BLOCK", f"state.md: size without Resume first: {rest} vs {STATE_LIMIT}"))
    if len(resume) > RESUME_LIMIT:
        findings.append(("BLOCK", f"state.md: Resume first size: {len(resume)} vs {RESUME_LIMIT}"))
    return findings


def check_fixes(text):
    findings = []
    titles = [title for _, level, title in headings(text) if level == 2]
    for title in FIX_SECTIONS:
        if titles.count(title) != 1:
            findings.append(("BLOCK", f"fixes.md: section: {title!r} count {titles.count(title)} vs 1"))
    if findings:
        return findings
    numbers = Counter()
    for title in FIX_SECTIONS:
        for line in section(text, title)[2].splitlines():
            if not ROW_RE.match(line):
                continue
            parts = cells(line)
            match = NUMBER_RE.match(parts[0])
            if not match:
                findings.append(("BLOCK", f"fixes.md: {title} row: number cell: {parts[0]!r} vs '<n>' or '<n> (dup)'"))
            else:
                numbers[int(match.group(1))] += 1
            if len(parts) != 6:
                findings.append(("BLOCK", f"fixes.md: {title} row {parts[0]}: cells: {len(parts)} vs 6"))
            if title != "Closed" and len(line.rstrip()) > ROW_LIMIT:
                findings.append(("BLOCK", f"fixes.md: {title} row {parts[0]}: length: {len(line.rstrip())} vs {ROW_LIMIT}"))
    baseline = BASELINE_RE.search(text)
    if not baseline:
        findings.append(("BLOCK", "fixes.md: baseline line: 0 vs 1"))
    else:
        wanted = Counter(int(number) for number in re.findall(r"\d+", baseline.group(1)))
        missing = wanted - numbers
        if missing:
            findings.append(("BLOCK", f"fixes.md: baseline numbers missing: {sorted(missing.elements())} vs none"))
    return findings


def check_entry(item):
    number, _, _, title, body = item
    findings = []
    match = HEADING_RE.match(title)
    if not match:
        findings.append(("BLOCK", f"journal.md entry {number}: heading grammar: {title[:60]!r} vs "
                         "'N. <unit> - <slug> - <YYYY-MM-DD> <HH:MM>[-<HH:MM>] CT'"))
    if len(title) > HEADING_LIMIT:
        findings.append(("BLOCK", f"journal.md entry {number}: heading length: {len(title)} vs {HEADING_LIMIT}"))
    unit = match.group(2) if match else ""
    required = ["- Result:", "- Next:"]
    if unit in ORIENT_UNITS:
        required.append("- Orient:")
    if unit in VERIFICATION_UNITS:
        required.append("- Verification:")
    for field in required:
        if not re.search("^" + re.escape(field), body, re.M):
            findings.append(("BLOCK", f"journal.md entry {number}: missing line: {field} vs present"))
    counted = body
    if unit in QUOTE_UNITS:
        counted = "\n".join(line for line in body.splitlines() if not line.startswith(">"))
    limit = BODY_LIMITS.get(unit, BODY_LIMIT)
    if len(counted) > limit:
        findings.append(("BLOCK", f"journal.md entry {number}: body length: {len(counted)} vs {limit}"))
    return findings


def check(root, entry_number=None):
    """Every recording rule; (cls, message) pairs. Raises OSError/ValueError only for roadmap or journal."""
    findings = []
    roadmap = read(root, ROADMAP)
    journal = read(root, JOURNAL)
    items = entries(journal)
    if not items:
        raise ValueError("no numbered journal entries found")
    raw, _ = checkpoint_raw(root)
    if len(raw) > BUDGET:
        cls = "NEEDS USER" if unit_of(items[-1]) in QUOTE_UNITS else "BLOCK"
        findings.append((cls, f"bootstrap checkpoint: budget: {len(raw)} vs {BUDGET}"))
    for part, builder in (("authority", authority_chunks), ("operator", operator_chunks)):
        size = len(assemble(builder(root)))
        if size > BUDGET:
            findings.append(("NEEDS USER", f"bootstrap {part}: budget: {size} vs {BUDGET}"))
    rows = [line for line in section(roadmap, "Carried fixes")[2].splitlines() if ROW_RE.match(line)]
    if rows:
        findings.append(("BLOCK", f"roadmap.md: Carried fixes rows: {len(rows)} vs 0"))
    try:
        findings += check_state(read(root, STATE))
    except OSError:
        findings.append(("BLOCK", "state.md: missing: 0 vs 1"))
    try:
        findings += check_fixes(read(root, FIXES))
    except OSError:
        findings.append(("BLOCK", "fixes.md: missing: 0 vs 1"))
    target = entry(journal, entry_number) if entry_number is not None else items[-1]
    findings += check_entry(target)
    return findings
```

Then in `main()`: after the `boot` parser add

```python
    checker = sub.add_parser("check")
    checker.add_argument("--entry", type=int, default=None)
```

and inside the `try`, before the `elif args.command == "append":` branch, add

```python
        elif args.command == "check":
            findings = check(args.root, args.entry)
            for cls, message in findings:
                print(f"{cls} {message}")
            if not findings:
                print("check: ok")
            return 1 if any(cls == "BLOCK" for cls, _ in findings) else 0
```

and change the final `except` to

```python
    except (OSError, ValueError) as error:
        code = 2 if args.command == "check" else 1
        parser.exit(code, f"Context reader failed: {error}. Read the canonical file directly.\n")
    return 0
```

and the tail of the file to

```python
if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests`
Expected: all pass (64).

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/autopilot/scripts/context.py .claude/skills/autopilot/tests/test_context_check.py
git commit -m "chore(autopilot): context.py check (BLOCK / NEEDS USER) and the 400-character ledger cap

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `usage.py`

**Files:**
- Create: `.claude/skills/autopilot/scripts/usage.py`
- Create: `.claude/skills/autopilot/tests/test_usage.py`

**Interfaces:**
- Produces: `usage.session(path) -> dict | None` (None under 20 assistant turns), `usage.table(sessions, entries_by_day) -> str`, CLI `usage.py [--since YYYY-MM-DD] [--project DIR] [--root DIR]`.

- [ ] **Step 1: Write the failing test**

Create `.claude/skills/autopilot/tests/test_usage.py`:

```python
"""usage.py reads a synthetic transcript; never the real one."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


usage = module("usage")


def transcript(path, turns, compact_after, day="2026-09-14"):
    lines = []
    for index in range(turns):
        if index == compact_after:
            lines.append({"type": "user", "isCompactSummary": True, "message": {"role": "user", "content": "summary"}})
        context = 1_000 * (index + 1)
        lines.append({"type": "assistant", "timestamp": f"{day}T12:{index % 60:02d}:00.000Z",
                      "message": {"model": "claude-fable-5-1", "usage": {
                          "input_tokens": 10, "cache_read_input_tokens": context - 110,
                          "cache_creation_input_tokens": 100, "output_tokens": 50}}})
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


class UsageTests(unittest.TestCase):
    def test_session_counts_turns_compactions_and_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "abcdef12-session.jsonl"
            transcript(path, turns=30, compact_after=20)
            info = usage.session(path)
            self.assertEqual(info["turns"], 30)
            self.assertEqual(info["compactions"], [20_000])
            self.assertEqual(info["totals"]["output_tokens"], 1_500)
            self.assertEqual(info["context_sum"], sum(1_000 * n for n in range(1, 31)))
            self.assertEqual(info["id"], "abcdef12")
            self.assertEqual(info["start"].strftime("%Y-%m-%d %H:%M"), "2026-09-14 07:00")
            transcript(path, turns=5, compact_after=99)
            self.assertIsNone(usage.session(path))

    def test_cli_prints_session_and_day_rows_and_filters_by_date(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            transcript(project / "aaaa1111.jsonl", 30, 20, day="2026-09-14")
            transcript(project / "bbbb2222.jsonl", 25, 99, day="2026-09-15")
            root = Path(directory) / "repo"
            (root / "docs/superpowers/autopilot").mkdir(parents=True)
            (root / "docs/superpowers/autopilot/journal.md").write_text(
                "## 1. verify - a - 2026-09-14 10:00 CT\n- Result: done\n## 2. verify - b - 2026-09-14 11:00 CT\n- Result: done\n")
            result = subprocess.run(["python3", str(SKILL / "scripts/usage.py"), "--project", str(project),
                                     "--root", str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("aaaa1111", result.stdout)
            self.assertIn("bbbb2222", result.stdout)
            self.assertIn("2026-09-14 day", result.stdout)
            self.assertIn("entries 2", result.stdout)
            filtered = subprocess.run(["python3", str(SKILL / "scripts/usage.py"), "--project", str(project),
                                       "--root", str(root), "--since", "2026-09-15"], capture_output=True, text=True)
            self.assertNotIn("aaaa1111", filtered.stdout)
            self.assertIn("bbbb2222", filtered.stdout)
            self.assertNotIn("summary", result.stdout)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests/test_usage.py`
Expected: FAIL (FileNotFoundError for `usage.py`).

- [ ] **Step 3: Write `usage.py`**

Create `.claude/skills/autopilot/scripts/usage.py`:

```python
#!/usr/bin/env python3
"""Token and compaction figures per controller session and per CT day, from this project's
Claude Code transcripts. Read-only; prints numbers and ids, never message text."""

import argparse
import collections
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[4]
PROJECT = Path.home() / ".claude/projects/-home-trey-dev-sports"
CT = ZoneInfo("America/Chicago")
MIN_TURNS = 20
INPUT_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def context_of(usage):
    return sum(usage.get(key, 0) for key in INPUT_KEYS)


def session(path):
    turns = 0
    totals = collections.Counter()
    models = collections.Counter()
    compactions = []
    last = 0
    context_sum = 0
    first = None
    with open(path) as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            kind = item.get("type")
            if kind == "user" and item.get("isCompactSummary"):
                compactions.append(last)
                continue
            if kind != "assistant":
                continue
            message = item.get("message") or {}
            usage = message.get("usage")
            if not usage:
                continue
            first = first or item.get("timestamp")
            turns += 1
            last = context_of(usage)
            context_sum += last
            models[message.get("model", "?")] += 1
            for key in INPUT_KEYS + ("output_tokens",):
                totals[key] += usage.get(key, 0)
    if turns < MIN_TURNS or not first:
        return None
    start = datetime.fromisoformat(first.replace("Z", "+00:00")).astimezone(CT)
    return {"start": start, "id": Path(path).stem[:8], "turns": turns, "compactions": compactions,
            "totals": totals, "context_sum": context_sum, "model": models.most_common(1)[0][0]}


def journal_entries_by_day(root):
    """Entries per CT day from the journal headings. A heading without a parseable date is
    attributed to the previous dated heading's day; the count of such headings is returned
    under the key "unparsed" so the denominator is honest."""
    counts = collections.Counter()
    path = Path(root) / "docs/superpowers/autopilot/journal.md"
    if not path.exists():
        return counts
    current = None
    for line in path.read_text().splitlines():
        if not re.match(r"^## \d+\. ", line):
            continue
        match = re.search(r"(\d{4}-\d{2}-\d{2}) \d{2}:\d", line)
        if match:
            current = match.group(1)
        else:
            counts["unparsed"] += 1
        if current:
            counts[current] += 1
    return counts


def table(sessions, entries_by_day):
    lines = [f"{'start CT':16} {'session':8} {'turns':>5} {'compactions (context at each)':32} "
             f"{'cache-read':>13} {'cache-new':>11} {'uncached':>9} {'output':>10} {'avg ctx':>8} model"]
    days = collections.defaultdict(collections.Counter)
    for info in sessions:
        totals = info["totals"]
        at = ", ".join(f"{value // 1000}k" for value in info["compactions"]) or "-"
        compactions = f"{len(info['compactions'])} ({at})"
        lines.append(f"{info['start']:%Y-%m-%d %H:%M} {info['id']:8} {info['turns']:5d} {compactions:32} "
                     f"{totals['cache_read_input_tokens']:13,d} {totals['cache_creation_input_tokens']:11,d} "
                     f"{totals['input_tokens']:9,d} {totals['output_tokens']:10,d} "
                     f"{info['context_sum'] // info['turns'] // 1000:7d}k {info['model']}")
        day = days[info["start"].strftime("%Y-%m-%d")]
        day["turns"] += info["turns"]
        day["compactions"] += len(info["compactions"])
        day["context_sum"] += info["context_sum"]
        day["cache_read"] += totals["cache_read_input_tokens"]
        day["output"] += totals["output_tokens"]
    lines.append("")
    if entries_by_day.get("unparsed"):
        lines.append(f"unparsed journal headings (attributed to the previous dated entry's day): {entries_by_day['unparsed']}")
    for date in sorted(days):
        day = days[date]
        entries = entries_by_day.get(date, 0)
        per_entry = day["cache_read"] // entries if entries else 0
        lines.append(f"{date} day: turns {day['turns']}, compactions {day['compactions']} "
                     f"({1000 * day['compactions'] / day['turns']:.1f} per 1,000 turns), avg context/turn "
                     f"{day['context_sum'] // day['turns']:,d}, cache-read {day['cache_read']:,d}, "
                     f"output {day['output']:,d}, journal entries {entries}, cache-read per entry {per_entry:,d}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default=None, help="YYYY-MM-DD (CT); sessions starting earlier are dropped")
    parser.add_argument("--project", type=Path, default=PROJECT)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    sessions = [info for info in (session(path) for path in sorted(args.project.glob("*.jsonl"))) if info]
    if args.since:
        sessions = [info for info in sessions if info["start"].strftime("%Y-%m-%d") >= args.since]
    sessions.sort(key=lambda info: info["start"])
    if not sessions:
        parser.exit(1, f"no sessions with at least {MIN_TURNS} assistant turns under {args.project}\n")
    print(table(sessions, journal_entries_by_day(args.root)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests/test_usage.py`
Expected: pass (66 in the whole suite). Then run against the real transcripts: `python3 .claude/skills/autopilot/scripts/usage.py --since 2026-09-14`. The three sessions in spec section 1 (ids aebc28da, 180a0657, 72d7f42b) must appear with 5, 3 and 2 compactions and turn counts 1,939, 1,406 and at least 910 (the third session was still running when the spec was written). Any mismatch, or an exit 1 "no sessions", means the transcript field names differ from the script's assumptions: stop and report before continuing, because the day-after measurement depends on this script.

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/autopilot/scripts/usage.py .claude/skills/autopilot/tests/test_usage.py
git commit -m "chore(autopilot): usage.py, per-session and per-day context and compaction figures

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `evidence_image.py`

**Files:**
- Create: `.claude/skills/autopilot/scripts/evidence_image.py`
- Create: `.claude/skills/autopilot/tests/test_evidence_image.py`

**Interfaces:**
- Produces: `evidence_image.archive(src: Path, dest: Path) -> (message: str, code: int)`; CLI `evidence_image.py SRC DEST` printing the message, exit code as returned. Constants `CAP = 400_000`, `HARD = 800_000`.

- [ ] **Step 1: Write the failing test**

Create `.claude/skills/autopilot/tests/test_evidence_image.py`:

```python
"""evidence_image.py: JPEG under the cap, cp -n semantics. Needs ImageMagick's magick."""

import importlib.util
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


evidence_image = module("evidence_image")


@unittest.skipIf(shutil.which("magick") is None, "ImageMagick magick not installed")
class EvidenceImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root / "capture.png"
        subprocess.run(["magick", "-size", "1440x900", "gradient:blue-white", str(self.src)], check=True)

    def test_converts_under_the_cap_and_reports_size_quality_and_dimensions(self):
        dest = self.root / "2026-09-16-verify-0900-01-gate.jpg"
        message, code = evidence_image.archive(self.src, dest)
        self.assertEqual(code, 0, message)
        self.assertTrue(dest.exists())
        self.assertLessEqual(dest.stat().st_size, evidence_image.CAP)
        self.assertRegex(message, rf"^{re.escape(str(dest))} \d+ q85 1440x900$")
        self.assertEqual(dest.read_bytes()[:3], b"\xff\xd8\xff")

    def test_existing_destination_is_kept_with_exit_zero(self):
        dest = self.root / "kept.jpg"
        dest.write_bytes(b"original")
        message, code = evidence_image.archive(self.src, dest)
        self.assertEqual((message, code), (f"{dest} exists, kept", 0))
        self.assertEqual(dest.read_bytes(), b"original")

    def test_unreadable_source_fails_and_leaves_no_destination(self):
        bad = self.root / "not-an-image.png"
        bad.write_text("nope")
        dest = self.root / "out.jpg"
        message, code = evidence_image.archive(bad, dest)
        self.assertEqual(code, 1)
        self.assertIn("conversion failed", message)
        self.assertFalse(dest.exists())

    def test_cli(self):
        dest = self.root / "cli.jpg"
        result = subprocess.run(["python3", str(SKILL / "scripts/evidence_image.py"), str(self.src), str(dest)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith(str(dest)))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests/test_evidence_image.py`
Expected: FAIL (module file missing).

- [ ] **Step 3: Write `evidence_image.py`**

Create `.claude/skills/autopilot/scripts/evidence_image.py`:

```python
#!/usr/bin/env python3
"""Archive a screenshot into evidence as a JPEG under the size cap, keeping cp -n semantics.

Quality 85, then 70, then the width shrinks by 0.75 per step while staying at or above half the
original width. Under 400 KB: done. 400 to 800 KB at the floor: kept, "over cap". Over 800 KB: refused.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

CAP = 400_000
HARD = 800_000
QUALITIES = (85, 70)
STEP = 0.75
FLOOR = 0.5


def magick(*args):
    return subprocess.run(["magick", *args], capture_output=True, text=True)


def dimensions(path):
    result = magick("identify", "-format", "%w %h", str(path))
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "identify failed")
    width, height = result.stdout.split()
    return int(width), int(height)


def convert(src, dest, quality, scale):
    args = [str(src), "-strip"]
    if scale < 1:
        args += ["-resize", f"{scale * 100:.4f}%"]
    args += ["-quality", str(quality), "jpeg:" + str(dest)]
    result = magick(*args)
    if result.returncode or not dest.exists():
        raise RuntimeError(result.stderr.strip() or "convert failed")
    return dest.stat().st_size


def attempts():
    plan = [(quality, 1.0) for quality in QUALITIES]
    scale = STEP
    while scale >= FLOOR:
        plan.append((QUALITIES[-1], scale))
        scale *= STEP
    return plan


def archive(src, dest):
    src, dest = Path(src), Path(dest)
    if dest.exists():
        return f"{dest} exists, kept", 0
    if shutil.which("magick") is None:
        return "conversion failed: magick not found", 1
    try:
        size = quality = None
        for quality, scale in attempts():
            size = convert(src, dest, quality, scale)
            if size <= CAP:
                break
        if size > HARD:
            dest.unlink()
            return f"{dest} would be {size} bytes > {HARD} at the floor; not written", 1
        width, height = dimensions(dest)
        note = " over cap" if size > CAP else ""
        return f"{dest} {size} q{quality} {width}x{height}{note}", 0
    except (OSError, RuntimeError) as error:
        if dest.exists():
            dest.unlink()
        return f"conversion failed: {error}", 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", type=Path)
    parser.add_argument("dest", type=Path)
    args = parser.parse_args()
    message, code = archive(args.src, args.dest)
    print(message, file=sys.stdout if code == 0 else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests`
Expected: all pass (70; the image tests skip only if `magick` is absent; it is present on Omarchy).

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/autopilot/scripts/evidence_image.py .claude/skills/autopilot/tests/test_evidence_image.py
git commit -m "chore(autopilot): evidence_image.py, capped JPEG archive with cp -n semantics

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Migration report, part 1: fix-row classification and the state fact inventory

**Files:**
- Create: `docs/superpowers/autopilot/reports/2026-09-15-context-hygiene-migration.md`

**Interfaces:**
- Produces: the report with sections `1. Row normalisation`, `2. Classification table`, `3. State fact inventory`, `4. Receipts grep`, `5. Reviewer findings` (5 is filled in Task 11). Task 6 reads section 2; Task 7 reads section 3.

- [ ] **Step 1: Draft the classification with a script**

Run from the checkout root (nothing is written to the repo by this step; output goes to a scratch file):

```bash
python3 - <<'PY' > /tmp/context-hygiene-classify.md
import re, sys
sys.path.insert(0, '.claude/skills/autopilot/scripts')
import context
root = context.ROOT
roadmap = context.read(root, context.ROADMAP)
state = context.read(root, context.STATE)
journal = context.read(root, context.JOURNAL)
start, end, table = context.section(roadmap, 'Carried fixes')
lines = table.splitlines()
rows = [(start + index + 1, line) for index, line in enumerate(lines) if context.ROW_RE.match(line)]
CLOSED = re.compile(r'\b(closed|done|superseded|transient)\b', re.I)
# a deploy sha followed by a *verify* PASS; "review PASS" does not close a row
PASS_AFTER_DEPLOY = re.compile(r'\b[0-9a-f]{7}\b[^|]*\bverif\w*[^|]*\bPASS\b', re.I | re.S)
OPEN = re.compile(r'\b(actionable|in flight)\b', re.I)
# state.md is consulted only in its "Carried fixes open and actionable" bullet and its "Right now" section
def state_scope(text):
    scope = [line for line in text.splitlines() if 'Carried fixes open and actionable' in line]
    try:
        scope.append(context.section(text, 'Right now')[2])
    except ValueError:
        scope += [line for line in text.splitlines() if line.startswith('## Right now')]
    return "\n".join(scope)
state = state_scope(state)
def cited(text):
    return [int(n) for n in re.findall(r'journal(?: entry)?s? (\d+)', text)]
def last_entry_text(row):
    numbers = cited(row)
    if not numbers:
        return ''
    try:
        return context.entry(journal, max(numbers))[4]
    except ValueError:
        return ''
def state_says(number, pattern):
    return re.search(rf'(fix|row)e?s? {number}\b[^.\n]*\b{pattern}\b', state, re.I) is not None
print('| # | line | cells | draft | reason | length |')
print('|---|---|---|---|---|---|')
seen = set()
for line_no, line in rows:
    parts = context.cells(line)
    number = parts[0]
    label = number if number not in seen else f'{number} (dup)'
    seen.add(number)
    deploy = parts[5] if len(parts) == 6 else parts[-1]
    entry_text = last_entry_text(line)
    reasons = []
    if CLOSED.search(deploy) or PASS_AFTER_DEPLOY.search(deploy):
        reasons.append('Deploy cell closes it')
    if state_says(number, '(closed|done)'):
        reasons.append('state.md says closed')
    if entry_text and re.search(rf'\b(fix|row) {number}\b[^.\n]*\b(closed|done|superseded|transient)\b', entry_text, re.I):
        reasons.append(f'journal {max(cited(line))} says closed')
    if reasons:
        draft = 'Closed'
    elif OPEN.search(deploy) or state_says(number, '(actionable|in flight)'):
        draft, reasons = 'Open', ['actionable or in flight']
    else:
        draft, reasons = 'Watch', ['no closing or actionable marker (ambiguous)']
    print(f'| {label} | {line_no} | {len(parts)} | {draft} | {"; ".join(reasons)} | {len(line)} |')
PY
wc -l /tmp/context-hygiene-classify.md && grep -c '| Closed |' /tmp/context-hygiene-classify.md; grep -c '| Open |' /tmp/context-hygiene-classify.md; grep -c '| Watch |' /tmp/context-hygiene-classify.md
```

Expected: 63 table rows and no exception; rows 71, 78, 79, 80 show a cell count other than 6. Record the printed split in the report; it is a draft, not a result (the dry run before these regex fixes drafted 29 Closed, with rows 30, 44, 53 and 64 wrongly closed on "review PASS", a "superseded" inside prose and a "done" about a different fact, and row 51 wrongly Open although state.md lists 50-58 as phase-assigned).

- [ ] **Step 2: Correct the draft by reading each row**

For every row, whatever its draft, read the row's Deploy cell and the most recent journal entry it cites (find the entry's line range with `grep -n '^## N\.' docs/superpowers/autopilot/journal.md` and print it with `sed -n 'START,ENDp'`) and set the section by the spec's rule (3.2 step 2): Closed when the cell, `state.md`, or that entry says closed, done, superseded, transient, or records a deploy followed by a verify PASS on the covering row; Open when `state.md`'s "Carried fixes open and actionable" list or the cell says actionable or in flight; otherwise Watch. Keep the rows that remain ambiguous marked `(ambiguous)` in the reason column; they go to Watch and to the report's `Needs you` list. Rewrite the reason column in your own words for every row you changed.

- [ ] **Step 3: Normalise the four malformed rows on paper**

For rows 71 (seven cells), 78, 79 and 80 (four cells) write the six-cell form in section 1 of the report: the original text redistributed so that Files, Change, Covering test and Deploy hold what the original folded into Finding or split across an extra cell; a cell with nothing gets `none stated`; a literal pipe inside a cell becomes `\|`. Do not shorten anything in this step.

- [ ] **Step 4: Build the state fact inventory**

Run `cat docs/superpowers/autopilot/state.md` and, sentence by sentence (bullets count as sentences), fill a table `| state.md sentence (first 60 chars) | destination | pointer |` where destination is one of: `Resume first`, `Right now`, `Order of work`, `Active units`, `Pending results`, `Counters and deadlines`, `Constraints`, `fixes.md row N`, `journal N (already recorded)`, `report section 4`. A sentence may go to `journal N` only when that entry states the same fact; verify with `grep -n` before writing the pointer. The `Rulings landed` paragraph maps to the journal entries it names (199-212, 224, 226, 229); each named ruling needs its own grep hit.

- [ ] **Step 5: Run the receipts grep**

```bash
python3 - <<'PY' > /tmp/context-hygiene-receipts.md
import re, sys, glob
sys.path.insert(0, '.claude/skills/autopilot/scripts')
import context
state = context.read(context.ROOT, context.STATE)
journal = context.read(context.ROOT, context.JOURNAL)
ledgers = "".join(open(p).read() for p in glob.glob('.superpowers/sdd/*/progress.md'))
def names(text):
    """Evidence file names in text: full paths, brace groups (evidence/<prefix>{a,b}.txt) and
    shorthands (", -deploy-full-1228.txt") in reading order, plus release stamps."""
    found = set()
    last_date = None
    for match in re.finditer(r'evidence/([\w-]+)\{([^}]+)\}\.txt|evidence/([\w-]+)\.txt|(?<=[ ,(])-([\w-]+)\.txt', text):
        if match.group(1):
            for part in match.group(2).split(','):
                found.add(f"{match.group(1)}{part.strip()}.txt")
            last_date = match.group(1)[:10]
        elif match.group(3):
            found.add(f"{match.group(3)}.txt")
            last_date = match.group(3)[:10]
        elif match.group(4) and last_date:
            found.add(f"{last_date}-{match.group(4)}.txt")
    return found | set(re.findall(r'\b\d{8}T\d{6}Z-[0-9a-f]{7}\b', text))
receipts = names(state)
journal_names = names(journal)   # the journal cites the same files in brace form; compare expanded names
ledger_names = names(ledgers)
print('| receipt | in journal | in a ledger |')
print('|---|---|---|')
for item in sorted(receipts):
    in_journal = item in journal_names or item in journal
    in_ledger = item in ledger_names or item in ledgers
    print(f'| {item} | {"yes" if in_journal else "NO"} | {"yes" if in_ledger else "NO"} |')
PY
grep -c '| NO | NO |' /tmp/context-hygiene-receipts.md
```

Expected: a table with no trailing periods in names and every brace-group member expanded (the dry run before these fixes reported six misses of which four were regex artefacts; genuine misses seen: `2026-09-15-t4-explain-1000.txt`, `2026-09-15-predeploy-baseline-6d.txt`). The count of receipts found nowhere else is the number the repair entry reports. Receipts whose evidence file exists on disk but that no entry names are not lost (the file is the record); list them anyway.

- [ ] **Step 6: Write the report**

Create `docs/superpowers/autopilot/reports/2026-09-15-context-hygiene-migration.md` with this skeleton, pasting the tables from steps 1-5:

```markdown
# Context hygiene migration (spec 2026-09-15-context-hygiene-design, revision 3)

Written <date> <HH:MM> CT by the user-directed implementation session on branch `context-hygiene-2026-09-15`,
pre-migration sha <sha from /tmp/context-hygiene-pre.sha> (later tasks fall back to this line if /tmp is gone).
The last controller checkpoint was <state.md header Updated time> CT, last journal entry <N>. The tables here back
the `repair` journal entry.

## 1. Row normalisation

Rows 71, 78, 79, 80 rewritten to six cells (original text preserved, nothing shortened):

| # | original line | six-cell row |
|---|---|---|
| 71 | roadmap.md:<line> | <row> |
| 78 | ... | ... |
| 79 | ... | ... |
| 80 | ... | ... |

## 2. Classification table

Rule: spec 3.2 step 2. Ambiguous rows are marked and listed under Needs you.

| # | line | draft | section | reason | original length |
|---|---|---|---|---|---|
| 16 | 601 | ... | Closed | ... | 1329 |
...

Counts: Open n, Watch n, Closed n (63 rows, 56 twice).

### Needs you

Ambiguous rows (in Watch pending your ruling): <numbers with one line each>.

## 3. State fact inventory

| state.md sentence | destination | pointer |
|---|---|---|
...

## 4. Receipts grep

| receipt | in journal | in a ledger |
|---|---|---|
...

Not found elsewhere: <n> (<list>).

## 5. Reviewer findings

(filled by the independent read-only reviewer in Task 11)
```

- [ ] **Step 7: STOP for the user's ruling**

Show the user sections 1 to 4 (the file path is enough; they read it) and ask them to rule on the classification table and the Needs you list. Record their words verbatim in section 2 under `### Ruling` with the CT time. Do not start Task 6 before the ruling. Apply the ruling to the `section` column.

- [ ] **Step 8: Commit the report**

```bash
git add docs/superpowers/autopilot/reports/2026-09-15-context-hygiene-migration.md
git commit -m "docs: context hygiene migration report (classification, fact inventory, receipts grep)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `fixes.md` and the roadmap pointer section

**Files:**
- Create: `docs/superpowers/autopilot/fixes.md`
- Modify: `docs/superpowers/autopilot/roadmap.md` (the `Carried fixes` section, lines 597 to end)

**Interfaces:**
- Consumes: the ruled classification in the report's section 2; `/tmp/context-hygiene-pre.sha`.
- Produces: `fixes.md` with the baseline line and three sections; `roadmap.md` with a row-free `Carried fixes` pointer section. `context.py check` still reports `BLOCK state.md ...` until Task 7 and a journal BLOCK on the controller's last entry until Task 12; that is expected here.

- [ ] **Step 1: Generate `fixes.md` from the ruled table**

Write the ruled mapping as a file `/tmp/context-hygiene-sections.txt` with one line per row, `<label>\t<section>` (label is `16`, `56`, `56 (dup)` ...), then run:

```bash
python3 - <<'PY'
import re, sys
from pathlib import Path
sys.path.insert(0, '.claude/skills/autopilot/scripts')
import context
root = context.ROOT
sha = Path('/tmp/context-hygiene-pre.sha').read_text().strip()[:7]
mapping = dict(line.rstrip('\n').split('\t') for line in open('/tmp/context-hygiene-sections.txt') if line.strip())
roadmap = context.read(root, context.ROADMAP)
start, end, table = context.section(roadmap, 'Carried fixes')
lines = table.splitlines()
rows = [(start + index + 1, line) for index, line in enumerate(lines) if context.ROW_RE.match(line)]
HEADER = "| # | Finding | Files | Change | Covering test | Deploy |\n|---|---|---|---|---|---|\n"

def journal_pointer(line):
    numbers = [int(n) for n in re.findall(r'journal(?: entry)?s? (\d+)', line)]
    return f"journal {max(numbers)}; " if numbers else ""

def clip(text, limit):
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."

PREFIX = re.compile(r'^[^:|]{0,140}\d{4}-\d{2}-\d{2}[^:|]{0,140}:\s*')   # "verify 2026-09-08 04:58 CT (journal 48): "

def condense(parts, label, line_no, line):
    finding = PREFIX.sub("", parts[1], count=1)
    finding = re.split(r'(?<=[.;:])\s', finding, 1)[0]
    pointer = f" ({journal_pointer(line)}roadmap.md@{sha}:{line_no})"
    row = None
    for width in (220, 160, 120, 80):
        row = (f"| {label} | {clip(finding, width)}{pointer} | {clip(parts[2], 70)} | {clip(parts[3], 70)} | "
               f"{clip(parts[4], 60)} | {clip(parts[5], 90)} |")
        if len(row) <= context.ROW_LIMIT:
            break
    assert len(row) <= context.ROW_LIMIT, (label, len(row))
    return row

sections = {"Open": [], "Watch": [], "Closed": []}
seen = set()
baseline = []
for line_no, line in rows:
    parts = context.cells(line)
    number = parts[0]
    label = number if number not in seen else f"{number} (dup)"
    seen.add(number)
    baseline.append(number)
    if len(parts) != 6:
        raise SystemExit(f"row {label} at roadmap.md:{line_no} has {len(parts)} cells: paste its six-cell form from report section 1 into the roadmap first")
    target = mapping[label]
    parts[0] = label
    if target == "Closed":
        sections[target].append("| " + " | ".join(parts) + " |")
    elif target == "Watch":
        sections[target].append(condense(parts, label, line_no, line))
    else:
        sections[target].append("| " + " | ".join(parts) + " |")   # Open rows are condensed by hand in step 3
text = f"""# Fix rows

Rows the loop carries: `Open` holds actionable hotfix rows (Orient rule 1 selects from here and nowhere else);
`Watch` holds rows assigned to a phase, owned by the user, recorded as observations or ruled follow-ups (hotfix,
phase and plan-next briefs read it with `context.py section docs/superpowers/autopilot/fixes.md Watch`); `Closed`
holds done rows. An `Open` or `Watch` row is at most 600 characters, has six cells, states the symptom in one
clause and points to the numbers (`journal N`, `evidence/<file>`, a ledger path). Rows move between sections in
the same commit as the journal entry that records the move, with the pointer in the Deploy cell (`closed: journal
N`, `transient (journal N)`, `PASS (journal N)`, `watch: phase work (journal N)`); a row added by a verify FAIL
or an integrity anomaly leaves `Open` only to `Closed` on PASS or by the user's ruling. Rows are never deleted or
renumbered; `context.py check` proves it against the line below. New rows continue from the highest number.
Migrated 2026-09-15 from `roadmap.md@{sha}` Carried fixes (report `reports/2026-09-15-context-hygiene-migration.md`).

Baseline numbers (2026-09-15): {", ".join(baseline)}

## Open

{HEADER}{chr(10).join(sections["Open"])}

## Watch

{HEADER}{chr(10).join(sections["Watch"])}

## Closed

{HEADER}{chr(10).join(sections["Closed"])}
"""
(root / context.FIXES).write_text(text)
print("rows", {key: len(value) for key, value in sections.items()}, "baseline", len(baseline))
PY
```

Expected: `rows {'Open': n, 'Watch': n, 'Closed': n}` summing to 63 and `baseline 63`. The script stops on row 71 first (seven cells): paste each malformed row's six-cell form from report section 1 into `roadmap.md` in place of the original line (this is the one edit to the original rows; it changes no words) and rerun. In the dry run with every row in Watch, `condense()` never failed its assertion (longest row 442 characters).

- [ ] **Step 2: Replace the roadmap's Carried fixes section with the pointer**

```bash
python3 - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, '.claude/skills/autopilot/scripts')
import context
sha = Path('/tmp/context-hygiene-pre.sha').read_text().strip()[:7]
path = context.ROOT / context.ROADMAP
text = path.read_text()
start, end, _ = context.section(text, 'Carried fixes')
lines = text.splitlines(keepends=True)
pointer = f"""## Carried fixes

Rows live in `docs/superpowers/autopilot/fixes.md` (`Open`: actionable hotfix rows, Orient rule 1's only source;
`Watch`: phase-assigned, user-owned, observation and follow-up rows; `Closed`: done rows). A verify FAIL or an
integrity anomaly adds a row to `fixes.md` `Open`. Rows move between sections and are never deleted
(`context.py check` proves it against the file's baseline line). This section holds no rows; the pre-migration
table is `roadmap.md@{sha}` (the migration's `repair` journal entry and
`reports/2026-09-15-context-hygiene-migration.md`).
"""
path.write_text("".join(lines[:start]) + pointer + "".join(lines[end:]))
print("replaced lines", start + 1, "to", end)
PY
python3 .claude/skills/autopilot/scripts/context.py check 2>&1 | grep -E 'Carried fixes|fixes.md' || echo "no fixes findings"
```

Expected: `replaced lines 597 to 668`; the check prints no `Carried fixes` and no `fixes.md` BLOCK lines except `Open row ... length` lines for the Open rows not yet condensed.

- [ ] **Step 3: Condense the Open rows by hand, then read every Watch Finding cell**

For each `BLOCK fixes.md: Open row N: length` line, edit that row in `fixes.md`: the Finding cell becomes one clause naming the symptom plus `(journal N; roadmap.md@<sha>:<line>)`, Files stays, Change keeps the instruction an implementer needs, Covering test stays, Deploy keeps its disposition. Re-run `python3 .claude/skills/autopilot/scripts/context.py check | grep 'fixes.md'` until it prints nothing. Then read every `Watch` row's Finding cell: it must name the symptom in one clause before its pointers (the prefix strip handles rows that open with `verify <date> ... (journal N):`; a row whose first clause is still only a date or a sha gets its symptom written by hand within 600 characters). The Task 10 reviewer checks this.

- [ ] **Step 4: Verify the counts and the pointer**

```bash
grep -cE '^\|\s*[0-9]+' docs/superpowers/autopilot/fixes.md; python3 .claude/skills/autopilot/scripts/context.py check | grep 'Carried fixes' || echo 'roadmap rows gone'; python3 .claude/skills/autopilot/scripts/context.py bootstrap authority | grep -A3 '^## Carried fixes'; python3 .claude/skills/autopilot/scripts/context.py bootstrap | grep -c '^| '
```
Expected: 63 rows in `fixes.md`; `roadmap rows gone`; the authority part shows the pointer text and measures about 23,500 characters; the checkpoint part shows the Open table.

- [ ] **Step 5: Commit (rows leave the roadmap and enter fixes.md in one commit)**

```bash
git add docs/superpowers/autopilot/fixes.md docs/superpowers/autopilot/roadmap.md
git commit -m "docs: fix rows move to fixes.md (Open / Watch / Closed); roadmap Carried fixes is a pointer

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `state.md` in the fixed schema

**Files:**
- Rewrite: `docs/superpowers/autopilot/state.md`

**Interfaces:**
- Consumes: report section 3 (fact inventory), the user's rulings of 2026-09-15 18:17 CT (verify.md line 886 edit; the stop suspends the batch clock) and the answer to step 1's question.
- Produces: `state.md` passing `check_state`.

- [ ] **Step 1: Ask the user the batch-clock number**

The hotfix batch wall-clock (3 h) started 16:59 CT on 2026-09-15 when fix 78 part 2 was dispatched. The user ruled at 18:17 CT that their stop suspends the clock, but the controller kept running until its last checkpoint (the `Updated` time in `state.md`'s header, 18:41 CT when this plan was reviewed). Compute `consumed = last checkpoint time - 16:59 CT` and ask the user, in one message: "The batch clock ran from 16:59 CT to the controller's last checkpoint at <time> CT, <consumed>. Resume with that much consumed, or a different figure?" Record the answer verbatim with the CT time in the migration report (section 2, `### Rulings`) and use it in `Resume first`.

- [ ] **Step 2: Write the new file from the inventory**

Rewrite `docs/superpowers/autopilot/state.md` to this shape, filling every section from the inventory (every sentence whose destination is a schema section appears there, as a pointer where the inventory says so):

```markdown
# Autopilot checkpoint

Updated <date> <HH:MM> CT by the user-directed context-hygiene session (branch `context-hygiene-2026-09-15`; no controller running) in `/home/trey/dev/sports` on Omarchy. Last journal entry: <the controller's last entry number; Task 12 raises it by one>. Paper-only. Runtime build: **f8053c6** (journal 243, app-only, fix 78 part 1). Main after the merge: f8053c6 + docs, skill scripts and the launcher (no deployable code ahead of the runtime; the release tree differs from the deployed tree by non-deployable files, so the next deploy needs a post-merge full-suite receipt). origin/main = 35f7180 (U7 pushes after phases and on Mondays).

## Resume first

- <every fact of the controller's "Stopping point" section at `state.md@<pre-migration sha>`, as bullets: the fix 78 part 2 branch head and review state, the consumed wakeups, rows 75/77, the p95 reading, the reminders>
- User ruling 2026-09-15 18:17 CT (the context-hygiene review session): the user's stop suspends the hotfix batch wall-clock (3 h, started 16:59 CT); per the user's answer in step 1 it resumes at relaunch with <consumed> consumed. Do not journal `ceiling` for fix 78 part 2 on that basis.
- The first wake's Orient rule 2 reads the deploy trigger with the launcher excluded (deploy.md step 1, updated); main is not ahead in code, and the next deploy needs a post-merge full-suite receipt (fix 78 part 2's rebased suite supplies it).
- The bootstrap is three parts (`bootstrap`, `bootstrap authority`, `bootstrap operator`); fix rows are in `fixes.md`; run `context.py check` and the skill tests at preflight (preflight.md).

## Right now

<the current "Right now" bullets, receipts as pointers>
- Main's release tree differs from the deployed tree (f8053c6) by non-deployable files only (`.claude/`, the launcher, `CLAUDE.md`, the runbook, the spec); the deploy trigger reads empty; the next deploy needs a post-merge full-suite receipt.

## Order of work

<the current numbered list, unchanged in substance>

## Active units

<the current "Active units" bullets>

## Pending results

<agents, suites, wakeups, latest receipts as one pointer line per stage, fixture grants, worktrees, test databases>

## Counters and deadlines

<day counters (dispatches 23 ... as today), failed deploys, implementers running, next duties with times, judge-after times, reminders>

## Constraints

<the current "Standing constraints" paragraph, plus any unresolved lesson or risk from the inventory>
```

Rules: no `Evidence receipts` or `Rulings landed` heading; the receipts that the inventory maps to `journal N` are not repeated; the batch-clock ruling and the three-part bootstrap note are the only new sentences. Keep the fixed sections under 8,000 characters and `Resume first` under 4,000 by using pointers, never by dropping a fact (spec 3.4).

Feasibility, measured on the file at review time (13,151 characters; header 418, Stopping point 2,428, Right now 2,215, Order of work 1,629, Active units 1,316, Pending results 1,501, Rulings landed 989, Evidence receipts 2,078, Constraints 552): dropping the two history sections leaves 7,656 for the fixed sections, so the release-tree bullet and any new fact must be paid for by turning Right now and Order of work prose into pointers. Facts already in the journal (pointer-able): settle 211 / 69,875, p95 13,850, p95 27.9, wakeup 95df8676, watermark 50028, reminder 2026091509. Facts not in the journal (keep the words): median loop 10.2, dispatches 23, implementers running 0 of 3, ab353cc, the fix-78b database name, the "one statement per column set" deviation, the implementer id; the stopping-point bullets fit `Resume first` (about 2,900 of 4,000).

- [ ] **Step 3: Check**

Run: `python3 .claude/skills/autopilot/scripts/context.py check | grep -E 'state.md|checkpoint' || echo "state ok"`
Expected: `state ok` (no state or checkpoint BLOCK; the journal BLOCK on the controller's last entry remains until Task 12).

- [ ] **Step 4: Diff against the inventory**

Run: `git diff --stat docs/superpowers/autopilot/state.md` and read `git diff docs/superpowers/autopilot/state.md` once, checking that every removed line has a destination in report section 3. Fix any miss.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/autopilot/state.md
git commit -m "docs: state.md in the fixed schema (Resume first carries the controller's stopping point and the batch-clock ruling)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Skill text (SKILL.md and references)

**Files:**
- Modify: `.claude/skills/autopilot/SKILL.md`, `references/recording.md`, `references/preflight.md`, `references/recovery.md`, `references/hotfix.md`, `references/verify.md`, `references/phase.md`, `references/deploy.md`

All edits are exact-string replacements run through one script so a drifted line fails loudly instead of silently. Read each file's current text first; if an `old` string is not found once, stop and reconcile by hand.

- [ ] **Step 1: Apply the replacements**

```bash
python3 - <<'PY'
from pathlib import Path
S = Path('.claude/skills/autopilot')
edits = {}

edits[S / 'SKILL.md'] = [
("""Run `python3 .claude/skills/autopilot/scripts/context.py bootstrap` from the controller checkout.
   It prints canonical roadmap authority, phase status, calendar, carried fixes, current state and the last
   two complete journal entries. It omits phase-specific pre-loaded decisions, which are required in step 3.""",
 """Run `python3 .claude/skills/autopilot/scripts/context.py bootstrap` (the checkpoint part: current state, the
   last two complete journal entries and the open fix rows), then `context.py bootstrap authority` (roadmap authority
   and phase status) and `context.py bootstrap operator` (host setup, secrets, calendar, user-side TODOs) from the
   controller checkout: three tool calls, each under the harness output limit. A first line `BUDGET EXCEEDED` means
   read the persisted result by its `Source:` ranges. The parts omit phase-specific pre-loaded decisions, which are
   required in step 3."""),
("reuse already loaded unchanged instructions. After compaction always reload the bootstrap.",
 "reuse already loaded unchanged instructions. After compaction always reload all three bootstrap parts."),
("scripts/autopilot-session.sh start-herdr   # inside a fresh herdr tab: one controller lock; compact at 500k",
 "scripts/autopilot-session.sh start-herdr   # inside a fresh herdr tab: one controller lock; compact at 300k (spec 2026-09-15-context-hygiene-design)"),
("/effort            # high (xhigh and max spend three to four times the tokens for no measured gain on this loop)",
 "/effort            # high, chosen explicitly: the saved default may be xhigh, which spends three to four times the tokens for no measured gain on this loop"),
("""   For U8's partial milestones, first verify that the ledger records the whole milestone's acceptance. A 6C deadline-slice
   checkpoint is not completion evidence; keep 6C `planned` with its remaining work recorded.""",
 """   For U8's partial milestones, first verify that the ledger records the whole milestone's acceptance; keep 6C `planned`
   with its remaining work recorded until its full acceptance."""),
("""1. **hotfix**: an actionable hotfix remains in `roadmap.md` Carried fixes, or the last journal entry ends in `FAIL`.
   Rows assigned to phase work, user actions, or already closed do not keep selecting hotfix. Batched by area (Unit: hotfix).""",
 """1. **hotfix**: an actionable row remains in `fixes.md` `Open`, or the last journal entry ends in `FAIL`.
   Rows assigned to phase work, user actions, observations or already closed live in `Watch` or `Closed` and never select
   hotfix. Batched by area (Unit: hotfix)."""),
("""**U8 scheduling exception (user-directed setup correction, 2026-09-11).** Check the dated 6C work at every unit and
task boundary; it does not wait until six hours overdue. Initialize 6A first, then plan 6C's urgent week-key and diagnostic
report tasks before starting 6B, while independent 6A/hotfix work continues. If the last feasible delivery window is
already at risk, prioritize that 6C planning immediately. Do not wait for all of 6A or 6B to finish. At resume, use the
current game/job schedule and estimated implementation, review, test, deploy and verification time to record the latest
permitted deployment opportunity before Sun 2026-09-13 19:00 CT and a wakeup/checkpoint before it. If deployment cannot
finish safely, prepare the correct-period diagnostic report and affected-surface labels before the deadline. R4 still holds.
6D instrumentation and 6E inventory/rehearsal preparation may also be planned while another 6x milestone is active, only
where their stated dependencies permit. This exception allows planning ready parallel milestones despite Orient 5/6 and
plan-next step 5. Keep separate plans, branches and ledgers; track each active milestone in state. Controller git/main
operations remain serial, file conflicts and per-branch database limits still apply, and the implementer ceiling is unchanged.
Keep 6C `planned` until its full acceptance is satisfied; completing only the deadline slice does not finish the milestone.""",
 """**U8 parallel planning (user-directed setup correction, 2026-09-11).** Ready 6x milestones may be planned while another
6x milestone is active, where their stated dependencies permit; this allows planning ready parallel milestones despite
Orient 5/6 and plan-next step 5. Keep separate plans, branches and ledgers; track each active milestone in state. Controller
git/main operations remain serial, file conflicts and per-branch database limits still apply, and the implementer ceiling is
unchanged. Keep 6C `planned` until its full acceptance is satisfied; a partial delivery does not finish the milestone."""),
("""`roadmap.md` only in the Status column, Carried fixes and User-side TODOs; rewrites `state.md`; appends to `journal.md`,""",
 """`roadmap.md` only in the Status column and User-side TODOs; moves rows between `fixes.md` `Open`, `Watch` and `Closed` and
never deletes one; rewrites `state.md`; appends to `journal.md`,"""),
("""never edits this skill (including references, scripts and tests), root `CLAUDE.md`, `.claude/settings.json` or
the v2 spec.""",
 """never edits this skill (including references, scripts and tests), `scripts/autopilot-session.sh`, root `CLAUDE.md`,
`.claude/settings.json` or the v2 spec."""),
("10. Any edit to the v2 spec, this skill or its supporting files, root `CLAUDE.md`, `.claude/settings.json`, `verify.md` outside a plan's last task, or the user-owned roadmap sections.",
 "10. Any edit to the v2 spec, this skill or its supporting files, `scripts/autopilot-session.sh`, root `CLAUDE.md`, `.claude/settings.json`, `verify.md` outside a plan's last task, or the user-owned roadmap sections."),
]

edits[S / 'references/recording.md'] = [
("## <N>. <unit> - <slug> - <YYYY-MM-DD HH:MM CT>", "## <N>. <unit> - <slug> - <YYYY-MM-DD HH:MM[-HH:MM] CT>"),
("- Carried forward: <items added to roadmap Carried fixes> | none",
 "- Carried forward: <rows added to fixes.md Open, or moved between Open, Watch and Closed> | none"),
("""Ledger lines are appended with `python3 .claude/skills/autopilot/scripts/context.py append <ledger> '<text>'`,
which stamps the line from the clock; a time is never written from memory or estimated.""",
 """Heading grammar (`context.py check` enforces it on the last entry): `<unit>` is one token matching `[a-z][a-z0-9-]*`
(preflight, hotfix, deploy, verify, operate, phase, plan-next, idle, repair, decision, gate, setup, paused, stopped, drill);
qualifiers go in the slug (`verify - re-read: ...`, `paused - rate limit: ...`); the timestamp follows the last ` - ` and
nothing follows ` CT`; the heading text after `## ` is at most 120 characters. `- Result:` and `- Next:` appear in every
entry (a decision entry writes `- Result: recorded`); `- Orient:` in preflight, hotfix, deploy, verify, operate, phase,
plan-next, idle and repair entries; `- Verification:` in verify and deploy entries. The body is at most 6,000 characters
for verify, deploy and repair entries and 3,000 for every other unit; in decision and gate entries the quoted block (lines
starting `>`) is exempt. Result values are unchanged. Numbers live in the evidence file the entry cites, not in the entry.

Decision entry example:

```
## 246. decision - the user's ruling on packet item 18 - 2026-09-16 09:10 CT

> the user's words, verbatim

- Applied: one line on what changed
- Result: recorded
- Next: hotfix, wakeup none
```

Ledger lines are appended with `python3 .claude/skills/autopilot/scripts/context.py append <ledger> '<text>'`,
which stamps the line from the clock; a time is never written from memory or estimated. A ledger line is at most
400 characters; `append` refuses a longer one, and the detail goes to the report or brief with its path in the line."""),
("""Entries are
appended, never edited, never duplicated (Orient rule 0); `roadmap.md` statuses and `state.md` update in the same commit,
`docs: autopilot journal - <unit> <slug>`, after every unit.""",
 """An entry may be
edited until the commit that lands it; from that commit on, entries are appended, never edited, never duplicated (Orient
rule 0); `roadmap.md` statuses, `fixes.md` moves and `state.md` update in the same commit, `docs: autopilot journal - <unit>
<slug>`, after every unit. Before every docs commit run `python3 .claude/skills/autopilot/scripts/context.py check`: a
`BLOCK` line is fixed before the commit by moving detail to evidence, the journal body, a report or `fixes.md`, never by
dropping an unresolved fact; a `NEEDS USER` line is copied once into the entry's `Anomalies:` line and into the next
report's Needs you, is never a repair entry and never blocks."""),
]

edits[S / 'references/preflight.md'] = [
("""Recompute the next duty and latest feasible 6C delivery opportunity from current
games/jobs.""",
 """Recompute the next duty from current games/jobs."""),
("""Never claim a wakeup exists until its actual scheduler lists it.""",
 """Never claim a wakeup exists until its actual scheduler lists it.

Run `python3 .claude/skills/autopilot/scripts/context.py check` and
`.venv/bin/python -m pytest -q .claude/skills/autopilot/tests` once per session before any
dispatch. A `BLOCK` line or a failing test is fixed inside the preflight's own commit
(loop-owned files only: state, fixes.md, the entry being written); a `NEEDS USER` line goes
into the preflight entry's `Anomalies:` line and the next report's Needs you."""),
]

edits[S / 'references/recovery.md'] = [
("""   recorded cron IDs are hints until verified. Recheck U8's 6C deadline at every
   task boundary. A partial delivery leaves all remaining acceptance work active.""",
 """   recorded cron IDs are hints until verified. A partial delivery leaves all remaining
   acceptance work active."""),
("""Keep the current `state.md` usable by older sessions until this branch is adopted.
On the next authorized controller checkpoint, use concise fields with paths in
place of pasted logs and historical lessons. Do not remove an unresolved fact to
meet a token target. Record:

- Updated time (CT with UTC), controller session and checkout, last journal entry.
- Each active unit/phase/task: plan and ledger paths, branch/base/head, status,
  owner/worker ID, dispatched time, outstanding review IDs/rounds, next legal action.
- Pending results/subprocesses: task ID or handle, log/report path, branch/database,
  observed status and time; completed results still awaiting controller consumption.
- Evidence receipts: code SHA, test command/result/database, reviewer and report,
  merge SHA, deploy target/start/end/stamp, verify verdict/deferred rows. Use ledger
  pointers for detail; retain distinctions between these stages.
- Counters and gates: CT day, dispatch totals per active unit/day, failures,
  retries/rate-limit state, blocking questions and their affected scope.
- Deadlines: next duty/judge-after, actual wakeup IDs, latest permitted deployment
  opportunity and fallback, remaining acceptance for every partial milestone.
- Applicable unresolved lessons/risks and their evidence pointers. Archive resolved
  observations in the append-only journal; load them when the task touches that area.""",
 """Use concise fields with paths in place of pasted logs and historical lessons. Do not
remove an unresolved fact to meet a token target. `state.md` has fixed level-2 headings
in this order, exact titles: `Resume first` (optional handoff, at most 4,000 characters;
removed at the first checkpoint after the resumed session consumes it, every fact moving
to its section or the resume entry in the same commit), `Right now`, `Order of work`,
`Active units`, `Pending results`, `Counters and deadlines`, `Constraints`; the file minus
`Resume first` is at most 8,000 characters, and `context.py check` enforces both. Record:

- Updated time (CT with UTC), controller session and checkout, last journal entry
  (the header line above the headings).
- Each active unit/phase/task: plan and ledger paths, branch/base/head, status,
  owner/worker ID, dispatched time, outstanding review IDs/rounds, next legal action
  (`Active units`).
- Pending results/subprocesses: task ID or handle, log/report path, branch/database,
  observed status and time; completed results still awaiting controller consumption
  (`Pending results`).
- Evidence receipts: code SHA, test command/result/database, reviewer and report,
  merge SHA, deploy target/start/end/stamp, verify verdict/deferred rows, one pointer
  line per stage; retain distinctions between these stages (`Pending results`).
- Counters and gates: CT day, dispatch totals per active unit/day, failures,
  retries/rate-limit state, blocking questions and their affected scope
  (`Counters and deadlines`).
- Deadlines: next duty/judge-after, actual wakeup IDs, latest permitted deployment
  opportunity and fallback, remaining acceptance for every partial milestone
  (`Counters and deadlines`).
- Applicable unresolved lessons/risks and their evidence pointers (`Constraints`).
  Resolved observations live in the append-only journal; load them when the task
  touches that area."""),
]

edits[S / 'references/hotfix.md'] = [
("journal it, remove the item from Carried fixes, fix nothing, unless the same item also failed within the last 24 h.",
 "journal it, move the row to `fixes.md` `Closed` with `transient (journal N)` in its Deploy cell in the same commit, fix nothing, unless the same item also failed within the last 24 h."),
("Batching. Carried fixes ship in batches,", "Batching. `fixes.md` `Open` rows ship in batches,"),
("""and never adds a table or a dependency; those are phase work (carry them to the next
plan-next) or a gate.""",
 """and never adds a table or a dependency; those are phase work (the row moves to `fixes.md` `Watch` with `watch: phase work
(journal N)` in its Deploy cell, and the next plan-next brief names it) or a gate."""),
("Remove each item when its rows pass.",
 "Move each row to `Closed` with `PASS (journal N)` in its Deploy cell when its rows pass, in the same commit as that journal entry."),
]

edits[S / 'references/verify.md'] = [
("""The controller copies
   screenshots with `cp -n` into canonical evidence, re-scores every FAIL plus one PASS,
   and fills cross-checks.""",
 """The controller archives
   screenshots into canonical evidence with `python3 .claude/skills/autopilot/scripts/evidence_image.py <capture>
   <evidence path>.jpg` (a JPEG under 400 KB where the image allows; an existing destination is kept, as `cp -n`
   did), re-scores every FAIL plus one PASS from the originals in `.superpowers/sdd/screenshots/` citing the
   `evidence/` path, and fills cross-checks."""),
("A FAIL adds a line to Carried fixes and the next unit is hotfix.",
 "A FAIL adds a row to `fixes.md` `Open` and the next unit is hotfix."),
]

edits[S / 'references/phase.md'] = [
("   delete its branch/ledger. In particular, the 6C deadline slice cannot trigger this completion step by itself.",
 "   delete its branch/ledger."),
]

edits[S / 'references/deploy.md'] = [
("""1. Compare deployed source with main, excluding docs/Markdown/.claude controller
   tooling.""",
 """1. Compare deployed source with main, excluding docs/Markdown/.claude controller
   tooling and `scripts/autopilot-session.sh` (Orient rule 2's pathspec)."""),
]

for path, pairs in edits.items():
    text = path.read_text()
    for old, new in pairs:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{path}: expected exactly one match, found {count}: {old[:70]!r}")
        text = text.replace(old, new)
    path.write_text(text)
    print("edited", path, len(pairs))
PY
grep -rn 'Carried fixes' .claude/skills/autopilot/SKILL.md .claude/skills/autopilot/references || echo "no Carried fixes mentions"
grep -n '500k' .claude/skills/autopilot/SKILL.md || echo "no 500k"
```

Expected: eight `edited` lines; `no Carried fixes mentions`; `no 500k`. (Every `old` string was counted exactly once in the files at review time.)

- [ ] **Step 2: Run the skill tests (they do not read these files, but the hook tests must still pass)**

Run: `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests`
Expected: pass.

- [ ] **Step 3: Show the user the skill diff and wait for approval (spec 6.3; gate 10 covers the skill)**

Run: `git diff .claude/skills/autopilot/SKILL.md .claude/skills/autopilot/references` and ask for approval. Record the answer verbatim with the CT time in the migration report, section 2, `### Rulings`.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/autopilot/SKILL.md .claude/skills/autopilot/references/recording.md .claude/skills/autopilot/references/preflight.md .claude/skills/autopilot/references/recovery.md .claude/skills/autopilot/references/hotfix.md .claude/skills/autopilot/references/verify.md .claude/skills/autopilot/references/phase.md .claude/skills/autopilot/references/deploy.md docs/superpowers/autopilot/reports/2026-09-15-context-hygiene-migration.md
git commit -m "docs(autopilot): skill text for the three-part bootstrap, fixes.md, check, the state schema, the launcher in the deploy exclusion and dead U8 text

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: User-owned roadmap text, runbook, CLAUDE.md, verify.md line 886

**Files:**
- Modify: `docs/superpowers/autopilot/roadmap.md` (edit rights 119-124; Secrets row 157; calendar rows; TODOs `[x]` items)
- Modify: `docs/runbooks/claude-omarchy-restart.md` (lines 18 and 34)
- Modify: `CLAUDE.md` (lines 12-13)
- Modify: `docs/superpowers/autopilot/verify.md` (line 886 only)

These are the user's sections; the user authorised them in the spec (3.7, 3.6). Show the user `git diff` before committing.

- [ ] **Step 1: Apply the roadmap replacements**

```bash
python3 - <<'PY'
import re
from pathlib import Path
path = Path('docs/superpowers/autopilot/roadmap.md')
text = path.read_text()
pairs = [
("""The loop edits `roadmap.md` only in the Phases table's Status column, Carried fixes, and User-side TODOs; it
rewrites `state.md`; it appends to `journal.md`, `evidence/` (screenshots and the `-layer2.txt`, `-summary.txt`,
`-preflight.txt` outputs), `reports/`, and `docs/reports/`; it edits `verify.md` only through a plan's last task. Standing authorizations, Decisions, Secrets, Pre-loaded decisions, the Operator calendar,
and the section below are the user's text.""",
 """The loop edits `roadmap.md` only in the Phases table's Status column and User-side TODOs; it moves rows between
`fixes.md`'s `Open`, `Watch` and `Closed` sections and never deletes one; it rewrites `state.md`; it appends to
`journal.md`, `evidence/` (screenshots and the `-layer2.txt`, `-summary.txt`, `-preflight.txt` outputs),
`reports/`, and `docs/reports/`; it edits `verify.md` only through a plan's last task. Standing authorizations,
Decisions, Secrets, Pre-loaded decisions, the Operator calendar, the Carried fixes pointer text,
and the section below are the user's text."""),
("`.claude/skills/autopilot/SKILL.md` and the v2 spec are never edited by the loop.",
 "`.claude/skills/autopilot/` (the skill, its references, scripts and tests), `scripts/autopilot-session.sh` and the v2\nspec are never edited by the loop."),
("**Done: the production key is read-scoped (rotated 2026-09-07, journal 6-7; confirmed by the user 2026-09-14, journal 202).**",
 "**Done, read-scoped, journal 202.**"),
("| Monday 09:00 | Weekly report: `ssh trey@192.168.12.228 'cd /volume1/docker/sports-harness && docker compose run --rm -T app-run report --week N --out -' > docs/reports/2026-wNN.md` on the Mac (R16; `docs` is not in the image and `--rm` discards the container, so the file is written here). Then `harness gate` on the NAS. Commit the report; one-line push. |",
 "| Monday 09:00 | Weekly report per operate.md's Monday 09:00 CT bullet, written in the controller checkout into `docs/reports/2026-wNN.md` (R16), then `gate`; commit the report; push per U7. |"),
("| Monday 09:30, and the morning after a Thursday or Friday game | Alias pass: `harness match-report` on the NAS,",
 "| Monday 09:30, and the morning after a Thursday or Friday game | Alias pass: `harness match-report` on Omarchy (operate.md's Monday 09:30 CT bullet),"),
("| Monday, and after every phase | `git bundle create` and scp to `/volume1/docker/sports-harness/repo-backup/` (R5). |",
 "| Monday, and after every phase | `git bundle create` and scp to `/volume1/docker/sports-harness/repo-backup/` (R5; the NAS archive destination is intentional, see operate.md). |"),
("| Daily 09:00 | One journal line covering: free space on `/volume1` (a gate below 25 %),",
 "| Daily 09:00 | One journal line covering: free space on `/srv/sports-harness` (a gate below 25 %),"),
]
for old, new in pairs:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one match, found {count}: {old[:70]!r}")
    text = text.replace(old, new)
# Calendar rows that are dead: the drill, the three dated U8 rows and the "At resume" row.
dead_starts = ("| Once, before 2026-09-12 |", "| At resume, and each unit/task boundary until the Sunday/Monday duties (U8, 6C) |",
               "| Sun 2026-09-13 19:00 (U8, 6C) |", "| Mon 2026-09-14 09:00 (U8, 6C) |", "| Tue 2026-09-15 08:00-16:00 (U8, 6E) |")
lines = text.splitlines(keepends=True)
kept, removed = [], 0
for line in lines:
    if any(line.startswith(start) for start in dead_starts):
        removed += 1
        continue
    kept.append(line)
if removed != 5:
    raise SystemExit(f"expected to remove 5 calendar rows, removed {removed}")
# Checked TODOs: the bullet line and its indented continuation lines.
text = "".join(kept)
lines = text.splitlines(keepends=True)
kept, removed, skipping = [], 0, False
for line in lines:
    if line.startswith("- [x]"):
        removed += 1
        skipping = True
        continue
    if skipping and line.startswith("  "):
        continue
    skipping = False
    kept.append(line)
if removed != 12:
    raise SystemExit(f"expected to remove 12 checked TODOs, removed {removed}")
# collapse the blank runs the removals leave inside the TODO list (three blank lines before "## Carried fixes")
text = re.sub(r"\n{3,}", "\n\n", "".join(kept))
path.write_text(text)
print("roadmap edited; calendar rows removed", 5, "; TODOs removed", removed)
PY
python3 .claude/skills/autopilot/scripts/context.py bootstrap operator | head -1 && python3 .claude/skills/autopilot/scripts/context.py bootstrap operator | wc -c
```

Expected: `roadmap edited; calendar rows removed 5 ; TODOs removed 12`; the operator part starts with `Source:` (no `BUDGET EXCEEDED`) and measures about 16,400 characters (25,968 before). The unchecked paragraph "The spec §2 legal-facts correction is being applied by the controller, not by the user." stays (it is the user's note); the continuation lines under the two checked bullets (the old lines 575 and 578) go with their bullets, the continuation under the unchecked Odds API bullet (old line 573) stays.

- [ ] **Step 2: Runbook, CLAUDE.md and verify.md line 886**

```bash
python3 - <<'PY'
from pathlib import Path
pairs = {
 Path('docs/runbooks/claude-omarchy-restart.md'): [
  ("inside a `sports-autopilot` tmux session. In Claude, select `/effort` **high**, then",
   "inside a `sports-autopilot` tmux session. In Claude, select `/effort` **high** explicitly (the saved\ndefault may be xhigh, which costs three to four times the tokens for no measured gain), then"),
  ("run its bootstrap reader.", "run its bootstrap reader (all three parts: `bootstrap`, `bootstrap authority`, `bootstrap operator`)."),
 ],
 Path('CLAUDE.md'): [
  ("""2. Run `python3 .claude/skills/autopilot/scripts/context.py bootstrap` from the
   controller checkout; load the selected procedure and phase decisions as routed.""",
   """2. Run `python3 .claude/skills/autopilot/scripts/context.py bootstrap` (then `bootstrap
   authority` and `bootstrap operator`) from the controller checkout; load the selected
   procedure and phase decisions as routed."""),
 ],
 Path('docs/superpowers/autopilot/verify.md'): [
  ("`docs/superpowers/autopilot/evidence/` with `cp -n` (never overwrite a re-run) as",
   "`docs/superpowers/autopilot/evidence/` with `evidence_image.py` (a capped JPEG; never overwrite a re-run) as"),
 ],
}
for path, edits in pairs.items():
    text = path.read_text()
    for old, new in edits:
        if text.count(old) != 1:
            raise SystemExit(f"{path}: expected one match: {old[:60]!r}")
        text = text.replace(old, new)
    path.write_text(text)
    print("edited", path)
PY
git diff --numstat docs/superpowers/autopilot/verify.md
```

Expected: three `edited` lines; the verify.md numstat is `1 1`.

- [ ] **Step 3: Show the user the diff and wait for their approval of the user-owned changes**

Run: `git diff --stat && git diff docs/superpowers/autopilot/roadmap.md docs/superpowers/autopilot/verify.md CLAUDE.md docs/runbooks/claude-omarchy-restart.md | head -300`
Tell the user these are their sections and ask for approval before committing. Record their answer in the migration report under a `### Ruling on user-owned text` line in section 2 with the CT time.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/autopilot/roadmap.md docs/superpowers/autopilot/verify.md CLAUDE.md docs/runbooks/claude-omarchy-restart.md docs/superpowers/autopilot/reports/2026-09-15-context-hygiene-migration.md
git commit -m "docs: user-owned text: edit rights name fixes.md and the launcher; Secrets, calendar and TODO pruning; runbook and anchor name the three parts; verify.md 886 names the archive tool

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Independent read-only review of the condensed rows and the state rewrite

**Files:**
- Modify: `docs/superpowers/autopilot/reports/2026-09-15-context-hygiene-migration.md` (section 5)
- Possibly modify: `docs/superpowers/autopilot/fixes.md`, `docs/superpowers/autopilot/state.md`

- [ ] **Step 1: Dispatch the reviewer**

Use the Agent tool with `subagent_type: general-purpose` (the project hook sandboxes subagents to Bash inside the checkout, read-only) and this prompt, filling `<sha>` from `/tmp/context-hygiene-pre.sha` (or the report header):

```
Read-only review. Only the Bash tool works (cat, sed -n, grep -n, python3 heredocs); do not write or commit.
Compare docs/superpowers/autopilot/fixes.md against the pre-migration table: `git show <sha>:docs/superpowers/autopilot/roadmap.md | sed -n '597,668p'`.
1. For every Open and Watch row, list any fact (a file, a query, a threshold, a ruling, a deploy sha, a judge-after time) present in the original row and absent from the condensed row that its pointers (journal N, roadmap.md@<sha>:<line>, evidence path) do not reach; and any Finding cell that does not name a symptom before its pointers. Cite row number and the missing words.
2. For every Closed row, confirm the text is unchanged except the Deploy cell.
3. Compare docs/superpowers/autopilot/state.md against `git show <sha>:docs/superpowers/autopilot/state.md` using the inventory in docs/superpowers/autopilot/reports/2026-09-15-context-hygiene-migration.md section 3: list any sentence of the old file with no destination, and any inventory pointer (journal N) whose entry does not state the fact (grep it).
4. Confirm the Resume first section carries every bullet of the old Stopping point section, the batch-clock ruling with its consumed figure, and the deploy-trigger note.
Report as: ## Rows (findings or "none"), ## Closed rows (ok or diffs), ## State (findings or "none"), ## Resume first (ok or missing items). Under 800 words.
```

- [ ] **Step 2: Apply the findings**

For each finding: restore the missing fact to the row (within 600 characters, via a pointer if needed) or to the state section, or record why it is not a fact (a duplicate of the journal) in section 5. Re-run `python3 .claude/skills/autopilot/scripts/context.py check | grep -E 'fixes.md|state.md' || echo clean` after edits.

- [ ] **Step 3: Record and commit**

Paste the reviewer's report into section 5 of the migration report with the disposition of each finding, then:

```bash
git add docs/superpowers/autopilot/reports/2026-09-15-context-hygiene-migration.md docs/superpowers/autopilot/fixes.md docs/superpowers/autopilot/state.md
git commit -m "docs: migration review findings applied (report section 5)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Launcher at 300k

**Files:**
- Modify: `scripts/autopilot-session.sh:10,15`

- [ ] **Step 1: Change the compaction threshold**

```bash
python3 - <<'PYEDIT'
from pathlib import Path
path = Path('scripts/autopilot-session.sh')
text = path.read_text()
pairs = [
 ("# create a duplicate controller; only the committed worker MCP server; compact at 500k.",
  "# create a duplicate controller; only the committed worker MCP server; compact at 300k\n# (spec docs/superpowers/specs/2026-09-15-context-hygiene-design.md section 3.5)."),
 ("--autocompact 500k", "--autocompact 300k"),
]
for old, new in pairs:
    if text.count(old) != 1:
        raise SystemExit(f"expected one match: {old!r}")
    text = text.replace(old, new)
path.write_text(text)
print("launcher edited")
PYEDIT
bash -n scripts/autopilot-session.sh && grep -n 'autocompact 300k' scripts/autopilot-session.sh .claude/skills/autopilot/SKILL.md && (grep -rn '500k' scripts/autopilot-session.sh .claude/skills/autopilot/SKILL.md || echo "no 500k")
```

Expected: `launcher edited`, syntax ok, one match in each file, `no 500k`.

- [ ] **Step 2: Commit**

```bash
git add scripts/autopilot-session.sh
git commit -m "chore(autopilot): launcher compacts at 300k (measured for one day per the context hygiene spec)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: The `repair` journal entry, the live tests, acceptance and merge

**Files:**
- Append: `docs/superpowers/autopilot/journal.md`
- Modify: `docs/superpowers/autopilot/state.md` (the `Last journal entry:` number only)
- Create: `.claude/skills/autopilot/tests/test_live_repo.py`

**Interfaces:**
- Consumes: `context.check`, `context.checkpoint_raw`, `context.BUDGET`; the migration report; the rulings recorded in it (times in CT).
- Produces: journal entry N = last + 1 passing `check`; the live tests; a fast-forwarded main.

- [ ] **Step 1: Write the live tests**

Create `.claude/skills/autopilot/tests/test_live_repo.py`:

```python
"""The real repository stays inside the recording budgets the loop owns."""

import importlib.util
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


context = module("context")


class LiveRepositoryTests(unittest.TestCase):
    def setUp(self):
        for name in ("roadmap.md", "journal.md"):
            if not (context.ROOT / context.AUTOPILOT / name).exists():
                self.skipTest(f"{name} absent; not a controller checkout")
        # A missing state.md or fixes.md is a BLOCK the first test must expose, never a skip.

    def test_check_reports_no_block(self):
        blocks = [message for cls, message in context.check(context.ROOT) if cls == "BLOCK"]
        self.assertEqual(blocks, [])

    def test_checkpoint_part_fits_the_harness_budget(self):
        raw, _ = context.checkpoint_raw(context.ROOT)
        self.assertLessEqual(len(raw), context.BUDGET)
```

- [ ] **Step 2: Run them to see the expected failure**

Run: `.venv/bin/python -m pytest -q .claude/skills/autopilot/tests/test_live_repo.py`
Expected: `test_check_reports_no_block` FAILS with BLOCK lines on the controller's last entry (at review time entry 246: unit `verify re-read` fails the one-token grammar, heading 392 characters, no `- Result:` or `- Next:`); the budget test passes (the checkpoint measured 18,820 at review time).

- [ ] **Step 3: Append the repair entry**

Compute the number and time:

```bash
N=$(( $(grep -oE '^## [0-9]+\.' docs/superpowers/autopilot/journal.md | tail -1 | tr -dc '0-9') + 1 )); NOW=$(TZ=America/Chicago date '+%F %H:%M'); PRE=$(cut -c1-7 /tmp/context-hygiene-pre.sha 2>/dev/null || git merge-base main context-hygiene-2026-09-15 | cut -c1-7); HEAD7=$(git rev-parse --short HEAD); echo "$N $NOW $PRE $HEAD7"
```

Then append to `docs/superpowers/autopilot/journal.md` (one blank line, then the entry; fill every `<...>` from the report; keep the heading text after `## ` under 120 characters and the body under 6,000; quote the rulings' words as recorded, not paraphrased):

```markdown
## <N>. repair - context hygiene migration (spec 2026-09-15-context-hygiene-design) - <NOW> CT
- Orient: none (user-directed session; the controller was stopped by the user after its <last checkpoint time> CT checkpoint, state.md@<PRE>)
- Branch / commits: context-hygiene-2026-09-15 <PRE>..<HEAD7>
- Result: done
- Dispatches: 1 (independent read-only reviewer of the condensed rows and the state rewrite)
- Tests: <n> passed (.venv/bin/python -m pytest -q .claude/skills/autopilot/tests); make test not run, no file under harness/ or tests/ changed
- Review: <clean | n findings fixed> (reports/2026-09-15-context-hygiene-migration.md section 5)
- Deploy: none; main's release tree now differs from f8053c6 by .claude/, scripts/autopilot-session.sh, CLAUDE.md, the runbook, the spec and the plan (non-deployable); the deploy trigger excludes them all; the next deploy needs a post-merge full-suite receipt (fix 78 part 2's rebased suite)
- Verification: not run
- Rulings: 2026-09-15 17:37 CT the user chose "Own file, open/closed split" for fix rows; 18:17 CT "Authorise the one-word edit" (verify.md line 886) and "Stop suspends the clock" (hotfix batch wall-clock); <time> CT batch clock resumes with "<the user's words>" consumed; <time> CT classification ruled: "<the user's words>"; <time> CT skill diff approved: "<the user's words>"; <time> CT user-owned roadmap text approved: "<the user's words>"
- Carried forward: fixes.md Open <n>, Watch <n>, Closed <n> (63 rows, 56 twice, baseline line); ambiguous rows in Watch pending the user: <numbers or none>; receipts found nowhere else: <n> (report section 4)
- Files: fixes.md created; roadmap Carried fixes is a row-free pointer; state.md in the fixed schema; skill text (SKILL.md, recording, preflight, recovery, hotfix, verify, phase, deploy); roadmap edit rights, Secrets row, five calendar rows, twelve checked TODOs; runbook and CLAUDE.md; launcher --autocompact 300k; new commands: context.py bootstrap {checkpoint,authority,operator}, context.py check [--entry N], evidence_image.py, usage.py
- Next: the user relaunches the controller; its first preflight runs check and the skill tests; after the first full day at 300k the user runs usage.py against spec section 3.5
```

Then set `state.md`'s header to `Last journal entry: <N>`.

- [ ] **Step 4: Check and test**

Run:
```bash
python3 .claude/skills/autopilot/scripts/context.py check; echo "exit $?"; .venv/bin/python -m pytest -q .claude/skills/autopilot/tests
```
Expected: `check: ok`, exit 0, and all tests pass (72). A `NEEDS USER` line here means an authority or operator part is over 27,000 (expected sizes: authority about 23,700, operator about 16,400): stop and ask the user which of their sections to shorten; acceptance requires neither class at merge time.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/autopilot/journal.md docs/superpowers/autopilot/state.md .claude/skills/autopilot/tests/test_live_repo.py
git commit -m "docs: autopilot journal - repair context hygiene migration; live budget tests

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 6: Acceptance checks on the branch (spec section 7)**

```bash
cd /home/trey/dev/sports && PRE=$(cat /tmp/context-hygiene-pre.sha 2>/dev/null || git merge-base main context-hygiene-2026-09-15)
.venv/bin/python -m pytest -q .claude/skills/autopilot/tests                                  # 1: 72 passed
python3 .claude/skills/autopilot/scripts/context.py check; echo "check exit $?"              # 2, 3: must print check: ok
for p in checkpoint authority operator; do printf '%s ' $p; python3 .claude/skills/autopilot/scripts/context.py bootstrap $p | wc -c; done   # 3: each under 27000
python3 - <<'PYCHECK'                                                                         # 4
import sys; sys.path.insert(0,'.claude/skills/autopilot/scripts'); import context
t=open('docs/superpowers/autopilot/state.md').read(); r=context.section(t,'Resume first')[2]
print('state minus Resume first', len(t)-len(r), 'Resume first', len(r))
PYCHECK
grep -cE '^\|\s*[0-9]+' docs/superpowers/autopilot/fixes.md                                   # 5: 63
grep -rn 'Carried fixes' .claude/skills/autopilot/SKILL.md .claude/skills/autopilot/references || echo "5 ok"
grep -n 'autocompact 300k' scripts/autopilot-session.sh .claude/skills/autopilot/SKILL.md     # 6
DEPLOYED=$(scripts/omarchy.sh health | python3 -c 'import json,sys;print(json.load(sys.stdin)["build"])'); git diff --stat "$DEPLOYED"..HEAD -- . ':!docs' ':!*.md' ':!.claude' ':!scripts/autopilot-session.sh'; echo "trigger diff above must be empty"   # 7
git diff --stat "$PRE"..HEAD -- scripts                                                       # 7: only autopilot-session.sh
git diff --numstat "$PRE"..HEAD -- docs/superpowers/autopilot/journal.md                      # deletions column 0
git diff --stat "$PRE"..HEAD -- docs/superpowers/autopilot/verify.md .claude/settings.json    # verify.md 1 line, settings none
N=$(grep -oE '^## [0-9]+\.' docs/superpowers/autopilot/journal.md | tail -1 | tr -dc '0-9'); python3 .claude/skills/autopilot/scripts/context.py check --entry "$N"   # 8
ls docs/superpowers/autopilot/reports/2026-09-15-context-hygiene-migration.md                 # 9
```

Expected: every line as commented. If `scripts/omarchy.sh health` cannot reach the host, record "trigger diff not run: host unreachable" and use `git diff --stat f8053c6..HEAD -- . ':!docs' ':!*.md' ':!.claude' ':!scripts/autopilot-session.sh'` (f8053c6 is the runtime build per state.md).

- [ ] **Step 7: Merge**

```bash
git checkout main && git merge --ff-only context-hygiene-2026-09-15 && git log --oneline -12 && git status --short
```
Expected: fast-forward; `git status --short` prints at most `?? .claude/settings.local.json`. Do not delete the branch (the user may want it); do not push.

- [ ] **Step 8: Hand off**

Tell the user, in one message: main's head; that the controller may be relaunched with `scripts/autopilot-session.sh start-herdr` (or `start`) and `/effort` high; that the first preflight will run `check` and the skill tests; and that after the first full day they run `python3 .claude/skills/autopilot/scripts/usage.py --since <that date>` and judge it against spec section 3.5. Update the memory pointer file `/home/trey/.claude/projects/-home-trey-dev-sports/memory/MEMORY.md` with one line: fix rows live in fixes.md, the bootstrap is three parts, `check` runs before docs commits, autocompact 300k pending the day-after measurement.
