## Journal entry format

```
## <N>. <unit> - <slug> - <YYYY-MM-DD HH:MM CT>
- Orient: rule <n> - <evidence: file, query result, or clock>
- Branch / commits: <branch> <base7>..<head7> | n/a
- Result: done | FAIL | transient | gated: <why> | paused: rate limit | ceiling
- Dispatches: <n> (impl/review/re-review/walker)
- Tests: <count> passed, pristine | n/a
- Review: clean | <n> fix rounds | parked: <items>
- Deploy: <sha> at <HH:MM CT> via <target>, stamp verified, gap rows <n> | none
- Verification: PASS n/m (evidence: <paths>) | FAIL: <row, one clause each> | deferred: <items + judge-after time> | not run
- Rulings: <one line each, exhaustive> | none
- Carried forward: <items added to roadmap Carried fixes> | none
- Next: <unit>, wakeup <HH:MM CT> | none
```

Shape: every field is one line; a Verification line names verdicts and the evidence path, and the numbers live in the
evidence file, not the entry. Anomalies are one line each under `Rulings` or a separate `Anomalies:` line. Entries are
appended, never edited, never duplicated (Orient rule 0); `roadmap.md` statuses and `state.md` update in the same commit,
`docs: autopilot journal - <unit> <slug>`, after every unit. Accepting an implementer's concern, deferring a Minor,
answering a question, parking a finding and choosing a model tier outside the table are rulings: write each as a
`Ruling:` line in the ledger, or it is missing from the roll-up by construction.

## Reports (`docs/superpowers/autopilot/reports/<date>-<slug>.md`)

Written after every phase (`phaseN`), after every Monday report (`week-NN`), and whenever the loop stops (`stopped`), each
followed by a one-line `PushNotification` and the `osascript` notification. Sections, in this order: (1) **Needs you**: every
gate, secret and TODO as one line with the exact command or file drop, or "nothing"; (2) **Decisions you may want to
reverse**, each with its reversal command; (3) **What the NAS is running**: build sha, deploy times in CT, containers;
(4) **Numbers**: the Layer 2 and band values as a table, "under audit" flagged; (5) **What shipped**: commits, tests;
(6) **Evidence**: screenshot paths and ssh numbers; (7) **Anomalies and transients**; (8) **Spend**: dispatches, Odds credits,
database growth, Anthropic dollars; (9) **Next**: the unit and the wakeup time. Times in CT with UTC in parentheses (NAS logs
are UTC). Update the project memory file with the new status.
