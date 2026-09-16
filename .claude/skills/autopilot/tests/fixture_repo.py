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
