# Paste-ready text for the controller session (edit freely; the loop records your words verbatim)

Prepared by the 2026-09-17 review session. Lines marked OPTIONAL are things the review recommended that you have not yet said in your own words; keep or strike them.

---

item 20: (a), amended per the 2026-09-17 review session. Build and review the print cache today with no same-day deadline; release it Friday 2026-09-18 morning, app-only, before the 18:30 CT NCAAF window. No value-path executor release before tonight's window. Use `.superpowers/sdd/hotfix-2026-09-17-executor-prints/fix-79-brief-amended.md` as the brief (it fixes a two-statement READ COMMITTED race in the draft, the missed `delete from venue_trades where source='rest'` at `harness/normalize/runner.py:355`, caches built TapePrints instead of raw rows, adds a row cap and race/fuzz tests); the reasons and production measurements are in `item-20-review-2026-09-17.md` beside it. Row 79 moves from Watch to Open for this batch.

Also today: a timers-only release of additive metrics (row 82's placement timer plus load, books, decide, intake, samples, previous-commit timers and tape/working/expiring counts), measurement only, no behaviour change. Draft brief: `timers-brief-draft.md` in the same directory; adopt it as your own batch. Do the timers first and base fix 79 on main after the timers merge, since both touch `loop.py`. If the timers are not clean by early afternoon, ship them with Friday's release instead of restarting the executor late. Row 82 moves from Watch to Open for this batch.

Make sure Friday's release happens: write a durable reminder file for it (session-only wakeups have been lost before), put it in state.md's next duties, and tell me Friday morning when it is released and judged.

The print cache will not turn the loop-metrics row to PASS (with the tape phase removed the p95 is still about 11 s) and tonight's in-game prints do not enter the rescan (every pending row on tonight's games expires by 19:05 CT), so correct the "25 s or more tonight" sentence where you carry it.

OPTIONAL: The loop-metrics row may keep reading FAIL through these two releases without tripping "the same verify item failing twice running", as under journal 242, while this work is in flight.

OPTIONAL: Read the executor-coupled 6B section 3 and 6D acceptance rows at Friday's or Saturday's NCAAF window instead of tonight if tonight's loops are over the period; take 4.6 T18b/T19 tonight as planned (they do not read executor tables).

OPTIONAL: Check that the 18:25 CT wakeup has a durable reminder file, and open a Watch row for swap growth (0, 999 MB, 1,915 MB over three days).

Friday I will run my own review session from `.superpowers/sdd/hotfix-2026-09-17-executor-prints/friday-review/launch.sh`; it is read-only and does not take the controller lock.
