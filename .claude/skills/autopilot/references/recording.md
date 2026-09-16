## Journal entry format

```
## <N>. <unit> - <slug> - <YYYY-MM-DD HH:MM[-HH:MM] CT>
- Orient: rule <n> - <evidence: file, query result, or clock>
- Branch / commits: <branch> <base7>..<head7> | n/a
- Result: done | FAIL | transient | gated: <why> | paused: rate limit | ceiling
- Dispatches: <n> (impl/review/re-review/walker)
- Tests: <count> passed, pristine | n/a
- Review: clean | <n> fix rounds | parked: <items>
- Deploy: <sha> at <HH:MM CT> via <target>, stamp verified, gap rows <n> | none
- Verification: PASS n/m (evidence: <paths>) | FAIL: <row, one clause each> | deferred: <items + judge-after time> | not run
- Rulings: <one line each, exhaustive> | none
- Carried forward: <rows added to fixes.md Open, or moved between Open, Watch and Closed> | none
- Next: <unit>, wakeup <HH:MM CT> | none
```

Heading grammar (`context.py check` enforces it on the last entry): `<unit>` is one token matching `[a-z][a-z0-9-]*`
(preflight, hotfix, deploy, verify, operate, phase, plan-next, idle, repair, decision, gate, setup, paused, stopped, drill);
qualifiers go in the slug (`verify - re-read: ...`, `paused - rate limit: ...`); the timestamp follows the last ` - ` and
nothing follows ` CT`; the heading text after `## ` is at most 120 characters. `- Result:` and `- Next:` appear in every
entry (a decision entry writes `- Result: recorded`); `- Orient:` in preflight, hotfix, deploy, verify, operate, phase,
plan-next, idle and repair entries; `- Verification:` in verify and deploy entries. The body is at most 6,000 characters
for verify, deploy and repair entries and 3,000 for every other unit; in decision and gate entries the quoted block (lines
starting `>`) is exempt. Numbers live in the evidence file the entry cites, not in the entry.

Decision entry example:

```
## 999. decision - the user's ruling on packet item 18 - 2026-09-16 09:10 CT

> the user's words, verbatim

- Applied: one line on what changed
- Result: recorded
- Next: hotfix, wakeup none
```

Ledger lines are appended with `python3 .claude/skills/autopilot/scripts/context.py append <ledger> '<text>'`,
which stamps the line from the clock; a time is never written from memory or estimated. A ledger line's text after the
stamp is at most 400 characters; `append` refuses a longer one, and the detail goes to the report or brief with its path in the line.

Shape: every field is one line; a Verification line names verdicts and the evidence path, and the numbers live in the
evidence file, not the entry. Anomalies are one line each under `Rulings` or a separate `Anomalies:` line. An entry may be
edited until the commit that first records it on `main`; from that commit on (amending or rewriting it is editing), entries
are appended, never edited, never duplicated (Orient rule 0); `roadmap.md` statuses, `fixes.md` moves and `state.md` update in the same commit, `docs: autopilot journal - <unit>
<slug>`, after every unit. Before every docs commit run `python3 .claude/skills/autopilot/scripts/context.py check`: a
`BLOCK` line is fixed before the commit by moving detail to evidence, the journal body, a report or `fixes.md`, never by
dropping an unresolved fact; a `NEEDS USER` line is copied once into the entry's `Anomalies:` line and into the next
report's Needs you, is never a repair entry and never blocks. Accepting an implementer's concern, deferring a Minor,
answering a question, parking a finding and choosing a model tier outside the table are rulings: write each as a
`Ruling:` line in the ledger, or it is missing from the roll-up by construction.

## Reports (`docs/superpowers/autopilot/reports/<date>-<slug>.md`)

Written after every phase (`phaseN`), after every Monday report (`week-NN`), and whenever the loop stops (`stopped`), each
followed by a one-line `PushNotification` and the `scripts/autopilot-session.sh notify` desktop notification. Sections, in this order: (1) **Needs you**: every
gate, secret and TODO as one line with the exact command or file drop, or "nothing"; (2) **Decisions you may want to
reverse**, each with its reversal command; (3) **What Omarchy is running**: build sha, deploy times in CT, containers;
(4) **Numbers**: the Layer 2 and band values as a table, "under audit" flagged; (5) **What shipped**: commits, tests;
(6) **Evidence**: screenshot paths and ssh numbers; (7) **Anomalies and transients**; (8) **Spend**: dispatches, Odds credits,
database growth, Anthropic dollars; (9) **Next**: the unit and the wakeup time. Times in CT with UTC in parentheses (NAS logs
are UTC). Update the project memory file with the new status.
