# Open decisions packet (user-side items), 2026-09-15 12:20 CT

Prepared at the user's request ("Write a packet for me to review the open decisions I need to make to help the loop").
Rendered page: https://claude.ai/artifact/4wRJo92rcU5UUU9dhMt6Z1. Sources: roadmap.md User-side TODOs, journal 224-236,
`reports/2026-09-15-storage-retention-proposal.md`, results/row72-review.md (I-3/I-4). The user answers by item number
in chat; each answer is journaled as a `decision` entry quoting the user verbatim with the time. The loop edits none of
the files named as "your edit".

## Decide this week

**Revised 13:46 CT.** Items 2-10 were ruled at 12:42 CT (relayed, journal 239) and confirmed in the loop's session at 12:53 CT (journal 240; the four one-time edits landed as ab353cc). Open: items 1 and 16.

1. **Storage retention** (due 2026-09-22; invariant 5, the user executes). 121.88 of 600 GB, 13-16 GB/day; 480 GB band
   about Oct 7-12, 600 GB about Oct 14-21, filesystem gate about a week later. Options 0-5 as sized in the proposal.
   Loop's lean: option 1 with a one-week lag after an off-host checksummed copy and a partition decrypt drill.
2. **Three documentation lines stale after revision 0013** (row 72 review I-3/I-4; the alembic verify row reads FAIL by
   the letter from today's release until edited): verify.md line 486 expected `0013_nw_executor_version`;
   docs/runbooks/alembic.md 28-40 add the 0013 row and move the pinned-head sentence; the Amendment 6 record in
   docs/superpowers/reviews/2026-09-07-phase2-preregistration.md 163-169 gains the sub-population sentence.
   Loop's lean: edit all three today, or "plan task" to defer to a plan's last task.
3. **Amendment 0.18 extension to `harness rescore` / `order_rescores`**: yes or no (journal 232). Loop's lean: no.
4. **Futures request budget** in verify.md: 200 (contract) vs 500 (spec, code; job 208 used 468). Loop's lean: 500.
5. **R4 replacement wording** (journal 128) pasted into the rulings table with the Omarchy target names. No behavior change.

## Hands-on when convenient

6. LAN listener files for 4.6 (owner password hash, TLS key/cert, install on phone/laptop); the `lan` profile stays off until all exist.
7. Host time-sync unit: `systemd-time-wait-sync.service` enabled and `After=/Wants=time-sync.target` on the stack's unit (journal 175 item 2).
8. Gate measurement boundary (`GATE_ELIGIBLE_FROM_ORDER_ID` / `_RUN_ID`, set together). Loop's lean: decide with 6F's period (item 13).
9. Odds API 5M tier (U1): health reports a 5,000,000 budget; confirm so the TODO closes.
10. Kalshi demo account (optional; phase 4 demo smoke only).

## Coming up (not askable yet)

11. 6D holding/capacity policy adoption, after the Thu 2026-09-17 NFL-window acceptance rows.
12. 6E environment acceptance, after the corrected-workload benchmark.
13. 6F dated pre-registration amendment (revised selection/confirmation dates; U8 overrides R7's Sep 21/28).
14. Off-site copies (R5/U7): standing, nothing to decide.
15. The legal decision before any live trading (gate 1; never autonomous).

## Added 13:46 CT

16. **Executor backlog: the loop-metrics verify row reads FAIL** (carried fix 78, journal 241). p95 8,423 ms against 7,500 at 13:40 CT with 0 open orders;
    `exec.nw_pending` 2,878 (09:30 CT) -> 5,055 (13:30 CT), about 300 per 30 minutes; not a restart artifact (17,314 on 7c3d750 at 12:00 CT).
    Options: (a) a per-loop time budget for counterfactual advances (opus hotfix on the user's yes; loop's lean); (b) accept until Thursday's
    window drains the backlog and settle the cadence in 6D's policy (the loop gates at the next FAIL reading); (c) other. Item 1 note: the
    trailing 22.5 GB/day rate reported by the user's other session is recorded, unverified, in journal 239.
