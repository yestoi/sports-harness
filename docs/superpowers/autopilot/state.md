# Autopilot state (rewritten by the loop at every unit boundary; the journal is the history)

- Updated: 2026-09-07 14:40 CT
- Position: decision - loop process revised by the user (journal entry 22); the loop was stopped at 14:25 CT after entry 21
- Branch: `main` at the entry-22 commit; NAS build 1bd1a26 (equal to `main` before entry 22; entry 22 is docs, scripts and the `tick-once --force` change, so Orient rule 2 fires: deploy)
- In flight: none
- Carried fixes: 8 open (12, 11, 4, 5, 6, 7, 8, 9); batches by area: matching (12), dashboard (11), venue socket (4, 5), recorder (6, 7), logging (8), compose (9, quiet window only)
- Deferred items: none
- Next: deploy `main` (Orient rule 2), then hotfix batches in parallel worktrees; game window opens 18:15 CT (deploy blackout from 16:35 CT for the NFL rule)
- Day counters: failed deploys 0, same-item repeat FAILs 0 (item 2 / carried fix 11 is open, not repeated), dispatches 20, test notifications sent today: yes (11:15 CT)
