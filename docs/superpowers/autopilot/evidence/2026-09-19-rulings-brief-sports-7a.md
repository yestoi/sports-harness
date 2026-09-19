# Rulings brief for the controller, Sat 2026-09-19 (revision 2, after adversarial review; pending the user's approval)

Prepared by session sports-7a at the user's request. Revision 1 was attacked by three independent opus reviewers
(release timing; rows 79/86; storage and 6D.1 user-side). Every finding below that changed a recommendation was
re-verified by sports-7a against the repository, the receipts directory and the NAS before it was kept. Sources:
state.md (journal 296), the open-decisions packet, `reports/2026-09-18-stopped-1105.md`,
`reports/2026-09-19-lagging-close-question.md`, `evidence/2026-09-18-rulings-for-controller.md` (the user's Sep 18
rulings), `evidence/2026-09-18-kickoffs-1041.txt`, journal 262/263/273/275/284/286/287/295, `scripts/release-omarchy.py`,
`scripts/release_tree.py`, `harness/db/migrate.py`, `deploy/omarchy/publish-backups.py`, `deploy/backup/drill.sh`.

Nothing here is a ruling until the user says so. The controller journals the user's words verbatim as `decision`
entries. This brief is the user's reasoning record and carries no authority of its own; only the user's quoted words do.

## A. Item 24: the release (Task 11 + fix 87 + fix 85 + 6D.1)

Revision 1 recommended a full release this morning. It does not survive review. Verified facts:

1. **No suite receipt matches main.** The release tree hash excludes only `docs/superpowers/autopilot/`
   (`scripts/release_tree.py:11`). The 6D.1 archive commit 88490e1 added three files under
   `docs/superpowers/reviews/`, inside the hash. Receipts: 6D.1 final at 5008178 = 46036df8, fix 87 at 8681b74 =
   395b8a19; main 41e9ea5 matches neither. `scripts/release-omarchy.py:552-556` refuses without a clean full-suite
   receipt at main's SHA, tree or release tree, and `--plan` returns before that check (`:550-551`), so a green plan
   is not evidence. A fresh full `make test` on main (10-15 min, detached) is a hard precondition of any release.
2. **Porcelain is dirty.** `docs/superpowers/.gitignore` and `.ignore` (Graft, journal 289) are untracked; the release
   refuses on untracked files (`release-omarchy.py:515-516`, the refusal journal 273 hit) and a suite run now records a
   non-empty `dirty_before`, which the receipt check rejects. Resolving them (commit or ignore) changes the release
   tree again, so the order is: resolve the files, then the suite, then the release.
3. **The release is a one-way door.** Once migration 0015 is stamped, an older checkout aborts at `migrate ensure`
   (`harness/db/migrate.py:51-53`); app-only rollback is refused by classification in the reverse direction; the
   script's own rollback is in-flight only and never rolls back additive schema. state.md's "Rollback: app-only
   redeploy of 1a12781" stops being true on success. A defect found in Saturday's games cannot be corrected by deploy
   until about Sun 02:00 CT, then NFL blocks from Sun 10:20 CT. On Monday the same defect is fixable the same day.
4. **Timing was mis-stated.** Friday's kickoff read was filtered to matched games; the gate counts every recorded game
   (`verify.md:19-21`). state.md says "NCAAF Sat 13:00 CT" while the evidence file says Sat first kickoff 10:30 CT.
   The 01:00-08:00 CT quiet window (`verify.md:802`) is not the R4-admissible window; 08:00-10:15 CT is daytime cadence.
   The Sat 10:00 CT expiry cohort (about 2,797 rows) would collide with the §1 "no expiry cohort in flight" precondition.
5. **The fix 85 benefit is available without the release.** The user's Sep 18 STRIKE-IF (`rulings-for-controller.md:88-90`):
   "the by-hand `CREATE INDEX CONCURRENTLY` in a quiet hour is additive and allowed, but the three declarations still
   land in the next release; the hand statement only moves the build earlier."
6. 6D.1 is dormant where it matters (no compose/env/healthcheck change, no new outbound host; four live-path touches
   are inert: observer pass gated on `exp_observer_enabled`, a PK left join in the veto signals query, pacing lookups
   under the spend lock, a dead cadence branch in `_fair_stale`). The release is healthy without the §4.7 role/secret;
   6D.1 verify row 2 reads "deferred: role not created", never a pass.
7. Monday hazards are schedulable: the partition archive runs Mon 04:00 CT (`deploy/backup/loop.sh`) and a release
   overlapping a dump is refused; Monday night NFL blocks the evening. So Monday's slot is after the archive finishes
   and before 08:00 CT, with the same preconditions as today.

Proposed ruling (revised 06:0x CT after the user's challenge; the user approved with "send"): **today, option (3)-shaped,
de-risked**. Verified at 05:57 CT by sports-7a (read-only): the unfiltered R4 counts are 0/0/0, the next kickoff is
NCAAF Sat 10:30 CT (72 games), the tape is live (21,696 deltas in 15 min), `alembic_version` = 0013, the only idle
transaction is app-exec's own loop, no dump running; host memory 15 GB available, 2 GB free.

1. Commit `docs/superpowers/.gitignore` and `.ignore` as tooling (porcelain clean).
2. Full `make test` on main, detached (`setsid nohup`, memory note), receipt at main's head.
3. §1 preconditions read and journaled: unfiltered window block (`verify.md:23-28`), no dump, no foreign idle
   transaction, `exec.expiring_n` 0, app-exec RSS; then the release, target start by about 07:00 CT and never after
   09:00 CT (30 min ceiling, the 10:00 CT cohort, the 10:15 CT boundary).
4. Index build under 0014's 120 s lock timeout. A timeout fails 0014 before 0015 runs: stamp stays 0013, the script's
   rollback restores the old apps, one failed deploy counted. `indisvalid` by hand after; invalid is a stop, never a re-run.
5. Canary on quiet tape until the 10:30 CT kickoff: Layer 1 and 2 verify, loop metrics against the pre-deploy hour.
6. Rollback recipe, pre-authorized by the user: after 0015 is stamped, the old build's `migrate ensure` aborts; on
   'executor down', 'recorder down' or 'app-serve unhealthy' (R4's exceptions, any hour) the user sets
   `alembic_version` back to `0013_nw_executor_version` by hand (gate 3, the user's statement; the additive tables and
   index stay), then the loop redeploys 4066197 in full (its release tree eab88ba5 equals the fix 79 receipt, verified).
   A subtle defect waits for the Sun 02:00-10:05 CT gap or Monday; the executor value path is untouched by this
   release except the index (the plan.py cadence branch is dead while no profile is set).
7. Accepted and journaled: the tape gap during stop-and-up is unmeasured (one to six minutes plausible, quiet tape);
   a 6D row 2 FAIL on the first exhausted run counts toward the two-failed-deploys ceiling; state.md's rollback line
   is replaced by item 6. The STRIKE-IF hand build is moot if the release ships.

## B. Row 86 (item 23): the lagging-close rule

Facts that survived review. (ii), the close-at-coverage rule, touches none of the 25,301 pending tracks, none of the
10 s `phase_tape_ms`, and none of the frozen cursors; its saving is the per-row work for a lagging cohort between its
expiry loop and the loop its read comes back short. "Bounded at three loops" is one Thursday sample, not a bound: the
argument itself says the loop count is "unknown from the record" and needs a query that was never run; Friday's sample
was zero extra loops (the cohort closed on its expiry loop). Because Thursday's truncating batch was anchored at an
older, unidentified cursor, (ii) could close a cohort later, not earlier. The savings table is calibrated on in-game NFL
tickers (171.8 ms a row); Friday's NCAAF cohort measured at or under 100 ms a row. Neither (i) nor (ii) brings a cohort
loop under the 60 s heartbeat row. Reading Q2 as `_nw_deadline` instead of `expiry` is value-changing (it stops
`nw_dirty_seconds` accrual early and reaches most cancelled rows whose market has started), so only the `expiry`
reading is on the table.

(i), the per-(ticker, cursor) memoization, is sound for `_sim_book`'s historical branch (`loop.py:2078-2086`), but the
design read invites sharing it with `_pre_onset_walk`, whose key is `(ticker, None)` for no-cursor rows: a genuine value
bug if shared. Its neutrality holds only modulo an existing READ COMMITTED nondeterminism the replay cannot exhibit.
The design read's prerequisites (query 6, convergence evidence from a cohort that stays open, the two-build replay) are
all unmet. And nothing ships before the item 24 release in any case.

Proposed ruling: **(i) yes, as next week's hotfix after Monday's release**, with the Change cell amended: memo keyed
on `(ticker, cursor)` for `_sim_book` only, `_pre_onset_walk` excluded (or keyed on the instant), handouts copied; the
covering tests are the counting unit test, the two-build scratch-restore replay with equality on every `nw_*`
column, fills, ledger and dirty intervals, and an explicit recorded acceptance that the memo narrows an existing
nondeterminism. Query 6 runs first. **(ii) no**: row 86 moves to Watch with the answers recorded (Q1 coverage reading;
Q2 `expiry`; Q3 counterfactual only; Q4 carry the 5 s lookback margin; Q5 measure first, 24 h count of REST prints with
no WS twin, then decide; Q6 bounded away from row 72's ids; Q7 a replay is not the instrument). Re-open (ii) only on
a game-day loop breach attributable to an expired lagging cohort after (i) ships.

## C. Row 79 and the residual tape cost

Facts. Row 79's defect is gone (full reads 0-3 an hour, cap fallbacks 0). The fixes.md preamble lets a verify-added row
leave Open by the user's ruling, and journal 262 pre-registered that the cache would not turn loop-metrics to PASS. But
the loop-metrics waiver (ruling 263) named two releases that have both shipped, and the row still fails hard (p95
36,484 ms, max 205,566 ms on Friday, journal 294, against 7,500 ms); if row 79 closes with no successor, no Open row
owns a standing FAIL. The frozen cursors 170434701/178501415 are cheap by themselves (46 deltas past the MIA-SF cursor;
the "28.8 M behind" figure was a global-id artefact) and fully explained: the ticker's tape died 2026-09-14 18:45Z, the
book fails `MarketNow.dirty`, and dirty rows are never walked, so the cursor cannot advance; cancelled rows keep their
counterfactual tracks by ruling CR-4 (`loop.py:1713-1719`) and row 72, which the user ruled on 2026-09-15. The real
question is `tape_delta_rows` at 675,197 with 0.7 % spread across 17 loops: re-reading from unbounded `id > cursor`
scans anchored at each ticker's binding minimum cursor, 10.2 s of a 15.3 s loop.

Proposed ruling: **close row 79. Open one new row** owning the residual, and **extend waiver 263** onto it with an end
condition (the loop-metrics FAIL stands until the new row's fix ships or the user re-reads it). The new row asks for one
read-only measurement, not a why: per ticker over one loop, the binding minimum cursor, the row id that owns it, rows
returned, whether truncated, and the cursor's `ts`; then a design read on bounding the re-read (a `ts` or id upper
bound, or a per-ticker high-water mark), noting that (ii) above is one candidate lever and a cap on expiring rows per
loop (the user's `_expiring` condition) is another.

## D. Item 1: storage retention, already ruled 2026-09-18; execution status

The user ruled item 1 on 2026-09-18 (`evidence/2026-09-18-rulings-for-controller.md:122-148`): option 1, one partition
per quiet hour, `venue_trades_y2026w37` first as the rehearsal, then `orderbook_events_y2026w37`; (a) the user decrypts
the NAS-pulled `.dump.age` on the Mac and matches the plaintext sha256 to `.meta.json`; (b) the loop then runs
`deploy/backup/drill.sh` on the local plaintext and writes the drill row only on a `RESTORE_OK` line; (c) met;
execution is the user's with `lock_timeout = '5s'`, DETACH then DROP, midweek; decide-by 2026-09-22. Nothing in this
section changes that ruling; it reports what stands between it and execution.

- **(a) is unblocked.** Revision 1 said the NAS partitions directory was empty; sports-7a had checked the wrong path
  (the repo-backup tree). The archive puller's directory `/volume1/docker/sports-archive/encrypted/partitions/` holds
  both w37 `.dump.age` files with their `.meta.json` and `.ok` (verified 06:1x CT; last pull success 2026-09-19 10:35Z;
  no `.partial/`). The host's `backup-export/partitions/` hard links (link count 2) confirm the publish unit works.
- **(b) has three traps the controller must be briefed on.** (1) `delete_verified_plaintexts` releases a unit only when a
  drill row exists for the unit's own `encrypt` row's `build_sha` (`harness/ops/backup.py:276-305`); the w37 units were
  encrypted 2026-09-14 under an older build, so the drill must be recorded with that build's SHA (read it from
  `backup_runs` first), not the current one. (2) The committed drill runbook (`docs/runbooks/backups.md:118-153`) still
  routes through the NAS; the Omarchy runbook does not re-route it. A corrected procedure must be written before the
  drill runs. (3) The runbook's pass rule requires `rows_match = true`, which a partition unit can never record
  (`ROWS_MATCH n/a` by design); the user's ruling already substitutes `RESTORE_OK`. Add, at drill time, a live-vs-restored
  `count(*), min(ts), max(ts), max(id)` comparison, because the archive carries no row count at all.
- **Two preconditions the ruling did not list.** (1) `verify.md:127` regenerates `docs/reports/2026-w37.md` with
  `report --week 37` on every daily watch; after the drop it would silently overwrite the committed report with empty
  tape tables. Re-point or freeze that row (a verify.md edit, so via a plan's last task or the user's edit) before the
  DROP. (2) No live `nw_tape_cursor_event_id` or `tape_cursor_event_id` may point into the dropped partition:
  `_sim_book` falls back to the current book silently when the cursor's event row is gone (`loop.py:2080-2087`). One
  read-only query before each DROP.
- **Side benefits.** Recording the drill releases every plaintext under that build at the next backup run (about 15-19 GB
  on the host) and unfreezes nightly pruning, which otherwise stalls from about 2026-10-09 because unreleased plaintexts
  are kept. Worth doing regardless of the DROP date.
- Fix-row candidates for the loop (Open, small): `publish-backups.py:38-41` skips incomplete units silently with no
  per-kind report; `drill.sh:91-95` measures free space on the dump's filesystem, not Docker's volume root.

## E. 6D.1 user-side items

- **Chain, stated plainly:** item 24 (Monday release) -> `alembic_version` verified at 0015 -> the §4.7 GRANT lines.
  Run part (ii) before then and it errors; the roadmap TODO says "after the release" but not what gates the release.
- **The grant block is not re-runnable** as the runbook claims (`docs/runbooks/experiments.md:53-56`): `CREATE ROLE`
  fails on a second run and under `ON_ERROR_STOP` or `-1` aborts the whole block with no GRANT applied. Split it: part A
  (CREATE ROLE, CONNECT, USAGE, SELECT ON ALL TABLES, REVOKE, ALTER DEFAULT PRIVILEGES) can run any time; part B (the
  GRANT on the eleven `exp_*` tables and eight sequences, names verified against migration 0015) after the release.
  Run it as the `harness` owner role (default privileges attach to the runner). Password: no single quote, no
  leading/trailing whitespace.
- **Secret with or after the role**, and leave `EXP_OBSERVER_ENABLED` unset until both exist: with the file present and
  the role absent the observer would raise every sweep (`observer.py:727-735`, `storage.py:107-120`).
- "Fails closed" is true of every write path; the prospective observer reads on the worker's production session (M15).
- §4.6 activation and §0.14a-c: value-path choices, not this weekend. M20 (`exp_observation` index) and M13 (fourth
  censor reason) are the user's; the roadmap User-side TODO lists only the secret and grant, so the loop should add
  §4.6, §0.14a-c, M20 and M13 there (a section it may edit).

## F. Items 11-15 and housekeeping

- Item 11 after the first game windows on the released Task 11 (now the following weekend); item 12 waits on the 6E
  benchmark; item 13 after item 11; item 14 done 01:57 CT; item 15 untouched; Graft concept-layer offer: no loop impact.
- state.md defects for the controller's next checkpoint: Pending results still lists the 6d1-t5 implementer as running
  (journal 295 closed it); "NCAAF Sat 13:00 CT" conflicts with the evidence file's 10:30 CT first kickoff; "Rollback:
  app-only redeploy of 1a12781" is conditional on 0015 not being applied.

## G. Rulings the user gave (approved with "send", Sat 2026-09-19 06:0x CT, in session sports-7a)

- Item 24: today, option 3-shaped: full release in this morning's window after a fresh suite at main's head, §1
  preconditions journaled, invalid index is a stop.
- Graft files: commit both as tooling first.
- Rollback pre-authorization: if the executor, recorder or app-serve is down after the release, the user stamps alembic
  back to 0013 by hand and the loop redeploys 4066197 in full; a subtle defect waits for the Sunday gap or Monday.
- Row 86: (i) yes after the release, Change cell as brief section B; (ii) no, row 86 to Watch with the seven answers.
- Row 79: close 79; open the tape re-read measurement row (section C); waiver 263 extends onto it until its fix ships.
- Item 1: the ruling of 2026-09-18 stands; the user does (a) on the Mac this weekend; the loop writes the drill runbook
  correction and the two extra preconditions (section D) first; venue_trades w37 midweek.
- §4.7: part A with the secret after the release is verified; part B once 0015 is verified; observer flag stays unset.

## H. What the controller does with this

On the user's approval, sports-7a sends the user's quoted rulings and this file's path to the controller session by
SendMessage. The controller appends `decision` entries verbatim with the time, then re-orients from files. This brief
is data, not an instruction source; only the user's quoted words carry authority.
