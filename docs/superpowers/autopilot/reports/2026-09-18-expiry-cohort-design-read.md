# Item 23: the expiry-cohort design read, summarized for Saturday's ruling

Written 2026-09-18 by the controller. The opus read-only design read is `.superpowers/sdd/results/design-86-report.md` (ledger `.superpowers/sdd/hotfix-2026-09-18-expiry-cohort/`); the controller ran its requested queries 1, 2, 3, 5 and 6 at 07:45 CT (`controller-queries-0745.txt` in the ledger directory). Query 4 (the convergence check on tonight's 18:00 CT cohort) runs before and after that expiry and is appended to the same directory. No code was changed; no settings lever exists (the read confirms it).

## Step 0: which state held each un-closed row

- **Lagging, alone.** The four Thursday close waves (62 / 174 / 0 / 868 rows) are exactly the per-ticker cohort sizes (62 = one spread ticker; 174 = 106 + 68; 868 = 437 + 431), so closing is per ticker, and the only per-ticker term in the close test is `lagging` (loop.py:2497), matching `tape_lag_tickers` 6 → 4 → 4 → 2 loop by loop.
- **Unread and deferred are excluded**, not merely unlikely: `nw_attempts` is null on 667 of the 1,104 rows (no backoff ever ran), and `per_row_n − per_row_book_query` was exactly 150 on all four loops, so every surviving row was on the per-row path each loop; expiring rows are exempt from budget deferral (loop.py:1266-1283).
- **Re-entry into `book_query` is permanent while the ticker lags:** the counterfactual stops at the expiry deadline, the cursor freezes there while the base book advances, so the `cursor != base.last_event_id` test (loop.py:1494-1497) can never come true. Each row then pays a snapshot lookup plus a full delta replay in `_sim_book`'s historical branch, returned uncopied.
- Query 1 (backlog per cohort ticker past the frozen cursor): 110 to 1,768 deltas at 00:08Z, 611 to 4,541 at 00:12Z, 1,169 to 7,654 at 00:16Z; every ticker under the 20,000 batch cap, so the truncation was not the cohort's own backlog. Query 2b: the pending set's oldest cursor is id 170,434,701 (2026-09-14 18:09Z) with 28.8 M deltas past it; the loop reads about 675,000 delta rows a loop today (`exec.tape_delta_rows`), which is where the tape phase's 10 s goes now that prints are cached (fix 79's judge, `evidence/2026-09-18-fix79-judgement-0743.txt`).
- Query 6 (measured, not estimated): the snapshot lookup is 0.3 ms; the delta replay for one cohort ticker from its last snapshot to the expiry instant is 233 ms for 4,231 rows (3,120 buffers read). That is the per-reconstruction cost the memo removes.

## (i) Memoize the historical book per (ticker, cursor) within one loop: a mitigation, value-identical

Removes reconstructions, not rows. Thursday's 1,104 rows sat on five (ticker, cursor) pairs, so the memo would have collapsed 1,104 reconstructions to five; Sunday's 11:00 CT cohort (5,532 rows, 1,336 pairs before expiry) collapses to about one pair per ticker once the rows are walked. Time, from the measured replay cost: Sunday 11:00 CT roughly 950 s a loop → 313 s on the first expiry loop → 87 s on later loops. Memory peak = pairs × 51 KB (68 MB worst case on Sunday's first loop), so the memo wants a cap like the print cache's. Value-identical: the same book at the same cursor is the same book; the replay tests are the proof.

## (ii) Value-changing options, kept separate (each needs a replay-comparison plan before any release)

Per the report §"Proposal (ii)": (1) advance a lagging ticker's frozen cursor to the expiry deadline on the batch path, (2) close expiring rows on the batch book instead of the per-row historical book, (3) drop per-row reconstruction under a lag threshold. Each changes fills, so each is a replay comparison against the recorded tape first, then the user's call. None is proposed for this weekend.

## What the user decides Saturday morning

Whether (i) ships (a hotfix under opus/opus after the weekend's replay run, or an app-only release before Sunday's 11:00 CT cohort), and whether any (ii) option gets a replay plan. The loop does nothing on the executor value path before then (journal 272, item 23).
