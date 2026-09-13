# Autopilot restart checkpoint

Updated 2026-09-12 22:03 CT (2026-09-13 03:03Z). User-directed preparation ends at this committed handoff. No Claude
controller or new 6B task was launched. Main includes index source `4b2f2cd` plus
this documentation checkpoint. Controller: `/home/trey/dev/sports` on Omarchy;
worktrees: `/home/trey/dev/sports-wt`; production: `/srv/sports-harness`.

Last journal: 156. Plan: `docs/superpowers/plans/2026-09-12-omarchy-loop-restart.md`.
Ledger: `.superpowers/sdd/omarchy-restart-2026-09-12/progress.md`.
Launch: `docs/runbooks/claude-omarchy-restart.md`.

## Source, tests and deployment

- Runtime `93dfb95` in app-run/serve/exec/research, deployed Sep12 20:59:31–21:05:30 CT
  (Sep13 01:59:31–02:05:30Z). WS stays `b0a3991`; PostgreSQL stays schema0006.
  Healthy receipt: `/srv/sports-harness/releases/20260913T015931Z-93dfb95/receipt.json`.
- Exact `93dfb95`: pristine full suite 3,209 passed, 6 expected xfails, 1,775.15s;
  independent source and CSS reviews passed.
- Index45 source `4b2f2cd`: independent review passed; 3,230 passed, 6 expected xfails, 1862.90s, clean unfiltered full suite on Omarchy. FF merged into
  main; original `8c24df2` and historical rounds preserved. Full production release
  NOT done. Before it, run fresh exact-main full acceptance and recheck game windows.
- Index fault fixtures need database-local `UPDATE(indisvalid)` only. The controller
  provisions and revokes it per `linux-controller.md`; workers never get admin tools.
  First restricted-role full run failed eight fixtures, with 3,222 passed/6 xfails.
  All 85 schema tests passed after the column grant; no source/assertions changed.
- Controller isolation, authentication and actual two-tool child drills accepted:
  96 isolated tests, 35 final guard/MCP checks, 31 actual-child tests passed.
  Claude uses the user's claude.ai login; Superpowers 6.3.0 and launcher flags verified.

## Verification is FAIL

Summary 9/9 PASS does not establish application or research acceptance. Exact
measurements and visual rescore are in
`docs/superpowers/reviews/2026-09-12-omarchy-restart/release-and-verification-evidence.md`.

- **48:** Correct pricing order and all seven variants run, but gaps/candidates are
  zero. Markets normalization is about 12h behind raw collection; current-run venue
  quotes are absent. Carry throughput/coverage to 6D and 48 acceptance; no reset,
  reprocess, freshness relaxation or budget change.
- **49:** First five new-build RSS samples span 121.8–1,935.4 MiB. Original 20-tick,
  <5% and 6h/500MiB acceptance remains OPEN. More RAM does not resolve retention.
- **51:** Fresh job165: 23 pass, 3 fail, 1 skip. Duplicate trades still hit the
  unchanged 2s timeout; late fills154, markout timings154, score decreases11 remain.
  Literal legacy intent/build-history query discrepancies and one bulk tape-count
  timeout stay under audit. Preserve rows and check definitions.
- **50:** Four new sid2 sequence-gap markers; no release-interval reconnect and
  unchanged WS container. Marker alone does not prove frame loss. Full 24h judge-after:
  SunSep13 21:05:30 CT (Sep14 02:05:30Z).
- **47:** Settle166 produced 105 markouts, all 11 stage timings, no exhaustion.
  Due-report-first remains pending: old-build164 already refreshed WTD. Next due is
  no earlier Sun02:10:38 CT (07:10:38Z); inspect the next scheduled settle at/after due.
- **52:** Game-window cadence120 correctly skips weather. Quiet-hour zero-fetch and
  next daytime successful-fetch freshness remain due; no cadence value changed.
- **53:** Populated Gate scroll width764 at viewport 390; Pulse displays historical
  exception names. BROKEN reflects actual rules. Initial advisory walk 23 PASS /
  3 FAIL / 3 PENDING is superseded for populated Gate by controller pixels/geometry.
  Study below16,000px, final markdown/annotation fence and empty skip table are pending.
  Preserve and coordinate separate user dashboard design work in the Mac checkout.

## Phase handoff and counters

6B `phase6b-repair-execution` contains accepted T10/T1/T2 only. Original `2d0fd71`
is preserved as `recovery/phase6b-before-omarchy-20260912`. The final base/head and existing-task smoke receipt are recorded in the mirrored phase ledger; resolve that branch and reconcile its receipt before dispatch.
Ledger: `.superpowers/sdd/2026-09-11-phase6b-repair-execution/progress.md`.
Prepared `task-3-omarchy-brief.md` is NOT DISPATCHED. It uses revision0008 after0007,
retains T1's falsy-value warning and T2's probe/reconnect rulings and final-review list.
Reconcile failed verification/carried fixes and 6C deadlines before selecting T3.

CT day Sep12: historical 19 + 21 Codex preparation calls + 3 actual Claude children
= **43 dispatches**. **One failed deployment acceptance**, for fix48's affected row;
this was one verification unit. Historical fix48 redispatch1 and fix45 round2 retained.
No new model retry/rate-limit event. All preparation agents/commands consumed; no suite
or worker pending at handoff. Original Mac worktrees, Omarchy's three migration stashes
and recovered ignored ledgers remain. Bundles and mirrored evidence are preserved.

## Deadlines and remaining acceptance

6C stays partial/planned: all 11 tasks deployed earlier. SunSep13 19:00 CT Chicago
week37 discriminator; MonSep14 before09:00 CT diagnostic report. Numeric/eligibility,
funnel and 6B-dependent checks remain. Formal selection awaits the user's 6F amendment.
6E inventory/restore/cutover are done; observer started Sep12 22:11Z, expected Sep13
04:11Z, completion not claimed here. Cold-start console LUKS unlock and two representative
corrected-workload windows remain. The partial capacity projection needs reconciliation
to the actual 600GB budget. Fix51's 25h all-pass history remains unestablished.

Actual user-systemd reminders rechecked Sep12 21:35 CT (Sep13 02:35Z):

- `sports-reminder-2026091301`: Sun02:49:59 CT, recheck full-release opportunity.
- `sports-reminder-2026091302`: Sun09:29:59 CT, readiness before recorded10:20 NFL block.
- `sports-reminder-2026091303`: Sun18:29:59 CT, prepare19:00 week-key discriminator.
- `sports-reminder-2026091401`: Mon07:29:59 CT, diagnostic before09:00.

Roughly Sun03:00 CT earliest full release is an estimate; requery games/jobs and R4.
Timers survive SSH, not reboot; native Claude wakeups still need actual-session setup.
NAS SSH authentication is unavailable from Omarchy; no key/account changes were made.
Optional listed secrets do not block launch. Preserve paper/LIVE0/RFQ0, 600GB budget,
25% free-space gate and all money, provider, retention and scientific criteria.
