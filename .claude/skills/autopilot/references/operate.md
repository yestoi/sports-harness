## Unit: operate (season duties, from the roadmap calendar)

- **Monday 09:00 CT**, week N = ISO week (R7: Week 1 is ISO 37, Week 2 ISO 38, Week 3 ISO 39). The report is written on the
  Mac, never inside the container (R16):
  ```
  mkdir -p docs/reports && ssh -o BatchMode=yes trey@192.168.12.228 'cd /volume1/docker/sports-harness && docker compose run --rm -T app-run report --week N --out -' > docs/reports/2026-wNN.md
  ssh -o BatchMode=yes trey@192.168.12.228 'cd /volume1/docker/sports-harness && docker compose run --rm -T app-run gate'
  ```
  Commit; write the `week-NN` report; one-line `PushNotification` with the headline numbers. U8 supersedes R7's original
  Mon 2026-09-21 selection and Mon 2026-09-28 confirmation commands. Those weeks still receive diagnostic reports;
  do not use `--selected-out` or `--confirm` as a formal selection/confirmation on the old dates. Resume formal selection
  and confirmation only under the user-ratified dated amendment, recorded before examining confirmatory estimates.
- **Monday 09:30 CT, and the morning after a Thursday or Friday game:** alias pass. `harness match-report` on the NAS; an
  implementer adds aliases for the top unmatched names to `harness/matching/aliases_manual.yaml` on `fix-aliases-<date>`
  with a test per alias; reviewer; merge; the aliases ride the next deploy; confirm the match rate rose.
- **Monday 09:45 CT, from phase 3:** replay-vs-live over the last game day. `harness replay --execute` must reproduce the
  live order and fill counts within 2 % (R14); outside the band is an integrity anomaly.
- **Monday, and after every phase:** the repo bundle (R5; a git remote is a gate):
  ```
  B=/tmp/sports-$(date +%F).bundle; git bundle create "$B" --all && git bundle verify "$B"
  ssh -o BatchMode=yes trey@192.168.12.228 'mkdir -p /volume1/docker/sports-harness/repo-backup' && scp -O "$B" trey@192.168.12.228:/volume1/docker/sports-harness/repo-backup/
  ```
- **Morning after every game day:** the verify unit in full (the walker runs), then the hotfix loop.
- **Daily 09:00 CT**, one journal line; anomalies become carried fixes: free space on `/volume1` (`df -h`; below 25 % is a
  gate); free memory (`free -m`, R17); database size and days-to-budget; Odds credits remaining and per-day use against the
  band; Anthropic spend against the U4 caps once phase 5 ships; executor heartbeat; ERROR messages, not counts; kill-switch
  state (observed only); the age-key nag while `secrets/backup_age_key` exists and its TODO is open, here and in every report's Needs you.
- **Tuesday 09:30 CT** (once phase 5a ships): confirm the futures snapshot job ran.
- **Once before 2026-09-12** (R6), and once more mid-phase between two tasks: the resume drill. At a unit boundary with no
  agent running, journal `drill: expecting <unit>`, commit, notify the user to restart per Kickoff, end the pass. The new
  session's Orient must pick that unit with no re-dispatch and no duplicate journal entry; journal the result as `drill`.
- **Seven days after the veto goes live:** the veto model study the calendar specifies; the score-versus-cost table and the
  disagreement cases go into the report's Needs you; the swap is the user's call.

Between duties: a wakeup for the next calendar event, `reason` naming it. Duties never pre-empt a running task;
U8's deadline work is checked at every task boundary and can proceed independently under its scheduling exception.
