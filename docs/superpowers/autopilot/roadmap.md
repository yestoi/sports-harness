# Roadmap: sportsbook harness autopilot

**End goal (spec §1):** a self-hosted system whose product for the first three weeks is a dataset and, if
the data supports it, a paper-validated straight-bet strategy on CFTC-regulated exchanges; live trading only
after an explicit gate and the user's separate legal decision.

Spec: `docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md` (v2) plus the phase addenda under
`docs/superpowers/specs/`. Where an addendum and the v2 spec differ, the addendum wins for its phase and
lists every difference in its §0. Review of record:
`docs/superpowers/reviews/2026-09-07-autopilot-adversarial-review.md` (six lenses, consolidated); its
section F holds the binding decisions and rulings.

## Phases (spec §15)

| Phase | Status | Plan | Gate before execution |
|---|---|---|---|
| 0 Recorder | done 2026-09-06, deployed | `docs/superpowers/plans/2026-09-06-phase0-recorder.md` | n/a |
| 1 Normalize and match | done 2026-09-06, deployed | `docs/superpowers/plans/2026-09-06-phase1-normalize-match.md` | n/a |
| 2 Pricing and signals | done 2026-09-07, deployed (hotfix `3224d0a`) | `docs/superpowers/plans/2026-09-07-phase2-pricing-signals.md` | n/a |
| 3 Paper execution, settlement, benchmarks, CLV | **done** (merged 2026-09-08 04:55 CT, journal entry 46), revised by the 2026-09-07 review; gains **Task 2b partitioning (U3, pre-authorized)**, **Task 4b NO-side (U2)** and **Task 12b telemetry (U6)** | `docs/superpowers/plans/2026-09-07-phase3-paper-execution.md` | none |
| 4 Kalshi authenticated adapter (still paper), risk gate, backups, Alembic | **done** (merged 2026-09-08 21:13 CT, journal entry 65; deployed 21:23 CT `babf8d3`, journal 67; hotfix waves `bdea218` 23:10 CT, `417eecd` 2026-09-09 00:09 CT, `6e3b33f` 00:5x CT; phase report `reports/2026-09-09-phase4.md`) | `docs/superpowers/plans/2026-09-08-phase4-kalshi-authed.md` (spec: `docs/superpowers/specs/2026-09-08-phase4-kalshi-authed-design.md`) | none; the demo smoke runs only when `secrets/kalshi_demo_*` exist |
| 4.5 Dashboard surfaces: Pulse, Floor, Study, Gate (snapshot layer, `/ui/`, table t12; absorbs phase 5(g)) | done | plan-next from `docs/superpowers/specs/2026-09-07-dashboard-surfaces-design.md` and its design canvas | none |
| 4.6 Fun tickets inside the existing dashboard (U9): this week's ideas as draft slips, in-app "I placed this" with corrections, prop legs with live stat lines, Floor game detail, a LAN listener with an owner login, the scheduled builder stage, slate-and-coral slip material | **planned** (2026-09-13 11:08 CT, journal 166; addendum revision 2 amended, plan revision 2 amended, 19 tasks in slices A-F) | plan-next (§4.6) from `docs/superpowers/design/ui-revision-2026-09-12/fable/DESIGN-SPEC.md`, `fable/FEASIBILITY.md` and `AFTER-FABLE.md` | none for slices A–D (contracts, additive data, projections and local writes, the interface on fixtures); a slice E/F claim that presents corrected execution or report evidence needs the matching 6B/6C acceptance; a paid stat provider, a new outbound host or a new dependency is gate 7; the ufw rule for LAN port 8443 is done (user, 2026-09-13) |
| 5 Research layer and hypotheses: futures snapshots, NWS, parlay CLI, shadow veto, report annotator, RFQ listener (overview page moved to 4.5) | done | plan-next | none; veto and annotator run only when `secrets/anthropic_api_key` exists |
| 6A Preserve and define: evidence capsule (order 157 plus representative clean, interleaved, gap/recovery, delayed-loop and capacity-bound periods), correction manifest, runnable failure cases, deploy plumbing (fix 37) | **done** (merged to main 2026-09-11 19:38 CT at 2d1ed62, journal 129; ledger `docs/superpowers/reviews/2026-09-11-phase6a-sdd-ledger.md`; deployed b0a3991 23:22 CT, journal 132; capsule extraction and phase report pending) | `docs/superpowers/plans/2026-09-11-phase6a-preserve-and-define.md` (spec: `docs/superpowers/specs/2026-09-11-phase6a-preserve-and-define-design.md`) | none: start now |
| 6B Repair execution: subscription continuity, recovery anchoring, trade/delta reconciliation, expiry, rejection, dirty-time scope, capacity-equivalent replay; the order 157 audit | **done** (planned 2026-09-11 21:41 CT, journal 131; started 2026-09-12 00:40 CT, journal 137; merged to main 2026-09-14 21:22 CT at 61013dd, journal 216; ledger `docs/superpowers/reviews/2026-09-11-phase6b-sdd-ledger.md`; order 157 audit **unverifiable**; the release and the D11 fill follow) | `docs/superpowers/plans/2026-09-11-phase6b-repair-execution.md` (spec: `docs/superpowers/specs/2026-09-11-phase6b-repair-execution-design.md`) | 6A done (met, journal 129); execution starts after tonight's wave deploy and 6C's merge, within the implementer ceiling |
| 6C Trustworthy reports: Chicago week keys on every surface and reader (fix 33 widened), gate documentation and eligibility, confirmation floor, exact-contract joins, funnel units, prior-week annotation backlog (after fix 41) | **planned** (2026-09-11 14:36 CT, journal 120; all 11 tasks merged to main at 6c11df3 and deployed b0a3991 23:22 CT, journal 132; stays planned until the wave-2 verify rows pass on Omarchy; the 6D deferral of the two funnel units (unique candidate opportunities, distinct intent episodes) was accepted by the user 2026-09-14, journal 184, and 6C's closure entry must name both as delivered by 6D) | `docs/superpowers/plans/2026-09-11-phase6c-trustworthy-reports.md` (spec: `docs/superpowers/specs/2026-09-11-phase6c-trustworthy-reports-design.md`) | none: plan and execute the deadline slice alongside 6A/6B; do not wait for all of 6B. Week keys by Sun 2026-09-13 19:00 CT, diagnostic report before Mon 2026-09-14 09:00 CT; final repaired-fill reports need 6B; the slice alone does not complete 6C |
| 6D Sustained evaluation: scheduled-versus-completed instrumentation, budget isolation, an explicit holding/capacity policy, the coverage contract | **in progress** (started 2026-09-14 05:2x CT, journal 186; branch `phase6d-sustained-evaluation`; merged to main at ffbecd5 and **released as b69b880 2026-09-15 01:25 CT**, journal 221; acceptance rows deferred to the first game window / 04:00 CT sweep; the policy comparison is the user's adoption decision) | plan-next (§6D) | instrumentation none; the policy comparison needs 6B |
| 6D.1 Execution viability experiment: stateful policy comparison, observation/holding tradeoff, book-health diagnosis, independent veto pacing, feasibility forecast | done (2026-09-19, journal 295: branch 60dd46a..88490e1 merged to main, archived under docs/superpowers/reviews/2026-09-19-phase6d1-*; released 01e7b0c 2026-09-19 06:37 CT (journal 299); §4.7 grant, §4.6 activation and §0.14a-c remain the user's) | `docs/superpowers/plans/2026-09-18-phase6d1-execution-viability.md` (spec: `docs/superpowers/specs/2026-09-18-phase6d1-execution-viability-design.md`) | isolated implementation and bounded reads authorized; historical runs need 6B inputs and baseline proof; prospective activation needs a frozen manifest, budget/coverage preflight and existing release rules; production holding-policy adoption remains the user's dated decision |
| 6E Operating environment: inventory, complete restore rehearsal, corrected-workload benchmark, host choice, measured cutover (fix 34 self-guard) | **partially delivered; acceptance pending** (Omarchy inventory/restore/cutover recorded2026-09-12; corrected6B workload and original operational acceptance remain; **cold-start/reboot observation: the Tue 2026-09-15 07:00 CT slot was missed (the 06:35 CT wakeup never fired and no reminder file existed, journal 223); the user reschedules it**, same procedure as journal 203: checkpoint, clean stack stop, the controller session dies with the reboot, relaunch per Kickoff, cold-start evidence at the new session's preflight); **cold-start observed 2026-09-15 07:43 CT, PASS (journal 229): boot to first healthy tick 1 min 55 s, unattended from the path unit; notes: app-backup is SIGKILLed at a stop (no SIGTERM handling), a clean stop writes no `gap` row (the `ws_connect` event is the boundary)** | plan-next (§6E) | inventory and rehearsal none; the benchmark needs 6B and 6A's deploy plumbing; the cutover itself is the user's yes |
| 6F Valid prospective period: recorded version boundary, first healthy-weekend checkpoint, sample-accrual forecast, revised selection/confirmation dates | not planned; diagnostic forecast brought forward into 6D.1 (U10) | plan-next (§6F) | 6B; 6C's numeric and eligibility rows; 6D's declared policy plus 6D.1's decision report and any required policy-adoption decision; 6E's environment acceptance |
| 7 Expand only with a working baseline: new variants and optional hypotheses from repaired research evidence | not planned | plan-next | 6F's operational checkpoint |
| Operator mode | after phase 6, and calendar duties throughout | n/a | n/a |
| Go-live gate | n/a | n/a | user's legal decision plus a stored passing gate report; never autonomous |

The carried fixes at the bottom of this file run **before** the phase 3 branch is created (R19). Novig is
dropped: no adapter, no credentials, no live path (user, 2026-09-07).

## Current host and restart setup (user-directed, 2026-09-12)

The user selected “Run the controller, development, and tests on Omarchy” and asked
“Lets do as much for Claude to be able to start it's loop back up. Lets ensure a smooth restart by doing all the steps until continuing the 6B correctness work.”
This setup session may update host routing, controller tooling and recovery instructions,
resolve fix45's bounded third wave, and review/test/integrate the prerequisite hotfixes.
It stops at the committed handoff before new 6B tasks; it is not an autonomous loop launch.
The resumed loop still cannot edit its own authority.

Production is `/srv/sports-harness` on Omarchy; controller `/home/trey/dev/sports`,
task worktrees `/home/trey/dev/sports-wt`, independent test PostgreSQL on loopback 5433.
Use the Omarchy release targets. The retired NAS remains an off-host bundle/archive
source; never restart its writer stack. Migration inventory, restore reconciliation,
and cutover evidence are preserved in `docs/runbooks/omarchy-operations.md` and the
migration documents; historical commands retain their original provenance.

Omarchy has 32 GB RAM and a roughly 1 TB filesystem. Preserve the deployed 600 GB
capacity alert budget and 25% free-space gate. U3's 2 TB NAS budget does not apply to
this filesystem; no retention/deletion is authorized. NAS-era RSS restart thresholds
are retired; profile growth while observing cadence, host memory and tape health.
Fix 49's original memory acceptance remains open until measured, not redefined.

6E's host choice and cutover are already performed. Use the recorded Omarchy inventory
instead of requesting Mac mini specifications or repeating a migration. Keep 6E unaccepted
until corrected-workload/two-window performance, cold-start and all original operational
acceptance checks pass. The 6B-dependent benchmark, 6D policy and 6F amendment still apply.
Worker Git metadata stays read-only; the controller commits returned worker patches.
The shared suite slot is cooperative scheduling; controller release receipts are private.
Linux desktop notifications plus durable reminder files replace macOS notifications;
native push/wakeup tools are checked in the actual session. Timers never launch a model.

## Decisions (2026-09-07)

User decisions (Trey), with direct quotations identified separately from their implementation. Only a dated
user decision changes them.

| id | Decision |
|---|---|
| U1 Odds API | Upgrade to the 5M-credit tier before 2026-09-12; the spec's "median feed staleness < 90 s" criterion stands unchanged. After the user confirms the tier, the loop flips the recorder settings: `odds_monthly_credits` 100000 → 5000000 and alternates cadence 900 s → 120 s for every event inside 36 h of kickoff (featured cadence unchanged); the 80 % budget alarm follows the new tier. |
| U2 NO-side | Yes: phase 3 gains Task 4b. Replay the NO-side rule over the recorded tape first, then register a two-sided variant under a **new** id by dated pre-registration amendment 3 before 2026-09-16. The six existing ids stay; their YES-only period is labelled. |
| U3 Storage | Partition `orderbook_events` and `venue_trades` now as phase 3 Task 2b (metadata-only `ATTACH PARTITION`). The harness's ceiling is 2 TB of the 3.5 TB free on `/volume1`. Archiving or dropping sealed partitions happens only on the user's later explicit yes: the loop may propose, never execute (gate). |
| U4 Veto spend | `veto_daily_usd_cap` = $25, `veto_weekly_usd_cap` = $150, enforced in code from the usage fields; over budget the veto and shadow go dormant for the day and every skipped call is labelled `veto_skipped_budget`. The Sonnet shadow runs on every call. The key must live in a capped Console workspace (user action). |
| U5 Gate variant | From amendment 3 onward the go-live gate is judged on the `sharp_two_sided` variant's `gate_reports` row (`gate_variant = true`); the pre-registered YES-only primary's row is stored and reported beside it every week. `Settings.gate_variant` defaults to the primary and is flipped by phase 3 Task 4b's deploy; Amendment 3 records the first switched evaluation date. Decided 2026-09-07 before any two-sided data exists. |
| U7 GitHub remote | Decided 2026-09-07 evening: a **private** GitHub repository is the remote `origin`, added by the user as an off-site backup. R5 is amended: the loop **pushes** `main` and the current phase branch (`git push origin main <phase-branch>`) at the same moments it writes the bundle (after every phase and every Monday), and the bundle continues. The loop never pulls, never rebases onto the remote, never opens pull requests, never pushes task worktree branches, and the NAS still deploys from the local `main`. A failed push is journaled, never retried in a loop, and never blocks a unit. |
| U6 Dashboard | Decided 2026-09-07 (design session, spec `docs/superpowers/specs/2026-09-07-dashboard-surfaces-design.md`). Architecture: compute once, render in the browser: jobs write pre-aggregated snapshots, `app-serve` serves them by primary key, a static client renders four surfaces (Pulse, Floor, Study, Gate) under `/ui/`; the legacy page at `/` and the `/api/summary` contract are frozen. Telemetry tables that cannot be backfilled land in phase 3 as **Task 12b** (`metric_samples`, `operator_events`, `order_watch_samples`, `equity_snapshots`, `game_score_events`, `check_results`, `report_runs`/`report_cells`); the front end is **phase 4.5**, planned after phase 4, absorbing phase 5(g). Mobile and desktop both in scope. Visual direction comes from a Claude Design canvas, refined on or after 2026-09-14. |
| U8 Phase 6 direction and resume setup | Integration requested 2026-09-11 (journal 113, recorded 12:08 CT): "I have a working loop setup in this project I'd like to get this roadmap integrated into." The adopted `docs/superpowers/reviews/2026-09-11-phase6-roadmap/ROADMAP.md` replaces phase 6's feature-first order with milestones 6A-6F and 7; its reconciliation, evidence and hashes remain the preserved review record. Resume correction authorized 2026-09-11: "Lets correct the resume instructions and get me ready to run /autopilot." The setup choices below implement that request, including 6C's parallel deadline slice, fix 37 before dependent deployments, and two 6B design-review lenses; those reviewer choices were not requirements in the original review roadmap. The roadmap adds no deploy trigger: standing authorization and R4 still govern deployment, and changing the recording host still requires the user's yes. Do not begin by relaxing freshness or adding a fill-producing variant; do not remove gate criteria or relax sample thresholds. September 14 is diagnostic. R7's September 21 selection and September 28 confirmation dates are overridden: no formal selection or confirmation until a replacement dated pre-registration amendment is ratified by the user. 6F proposes dates and any extension rule from operational completeness and sample accrual before examining confirmatory estimates. Chicago ISO-week reporting continues; R1's authority rule, the H1 floor, gate criteria, thresholds, families, cell grids and frozen variant ids remain in force. Order 157 requires a tape audit until 6A/6B publish validated, corrected or unverifiable. |
| U9 Fun tickets UI revision | Decided 2026-09-13 (design session, then the PR review session). User, verbatim, 2026-09-13 morning: "I have two PRs to review and merge. After I would like to get the autopilot loop ready to tackle the rest of our roadmap and add the new dashboard work to it." The approved design is PR #1, merged as `docs/superpowers/design/ui-revision-2026-09-12/` (Fable spec `fable/DESIGN-SPEC.md`, decisions F01–F09; PRODUCT-BRIEF U01–U14 stand). Phase **4.6** carries it. It is a **parallel track**: the loop may plan 4.6 at its next plan-next opportunity and run its tasks while 6B's remaining tasks (T5–T12, final review), the 6C deadline duties and actionable hotfixes continue; those keep priority for the implementer ceiling and the suite slot, and 6D's instrumentation may still be planned alongside. Three standing dashboard rules are widened by the design and are recorded here so the loop does not treat them as scope beyond the roadmap: loopback-only becomes loopback plus one home-network HTTPS listener behind an owner login (never public or remote; **amended by the user 2026-09-16, journal 260**: the loopback dashboard is also published at `https://sports.tunderwood.com` through the Hetzner gateway behind Authelia, via the socat user unit `sports-gateway-forward` on `10.1.0.3:8180`; runbook `docs/runbooks/sports-gateway.md`; the loop does not change the gateway, the unit or the ufw rule); UI read-only except the kill pair becomes read-only plus two owner POST routes (`/api/parlay/placed`, `/api/parlay/correct`); game-line-only legs become game lines plus the named prop families. Stakes, the $50 week, the LSU/Saints anchors, the paper posture, the scientific criteria and every invariant below are unchanged; fun-ticket accounting stays separate from paper (F02). PR #2 (the Qwen adoption review package) was merged the same morning as a review record only: no Qwen route, budget, skill edit or activation follows from it; D1–D6 await the user. |

### U10: execution viability milestone (user-directed, 2026-09-18)

User, verbatim: "Ok, lets apply the recommendation. Should this be separate from the existing code? Should it be it's own milestone?"

Apply the [confirmed review](reports/2026-09-18-fill-starvation-review-confirmed.md) as **6D.1**, a separate
milestone in this repo using shared algorithms and isolated state. The adopted
[draft design](../specs/2026-09-18-phase6d1-execution-viability-design.md) and
[delivery outline](../plans/2026-09-18-phase6d1-execution-viability.md) are plan-next inputs. Scope:
stateful baseline/cadence comparison, bounded faster observations, book-health diagnosis, independent
veto pacing and accrual forecast; conclude retain/revise/stop/insufficient. Positive performance is not
required. Existing `policy-compare` cannot establish alternative-fill performance.

Resume correction (user's sequencing question, journal 289): after recovery/preflight and applicable
Orient 0-4 duties, prioritize **6D.1 plan-next**, then its ready phase tasks. This explicitly overrides
Orient 5/6's first-phase ordering under U8. Complete standard design/conformance/plan reviews, bind
task `Files:`/`Depends on:` and final verification, commit the reviewed plan and set `planned` before T1.
While waiting on review/data/dependencies, continue other ready work. Existing milestone acceptance,
recording, timed verification, coverage, expiry-backlog and storage duties retain priority when due;
file ownership and ceilings stand. Bounded implementation and isolated paper work are authorized;
activation needs the manifest and budget/coverage checks. Adoption, registration, confirmation, spend
and release rules are unchanged. The study is exploratory; the formal prospective period remains 6F.

### Controller rulings

Controller rulings this file governs. Each is reversible; the review states the cost if wrong.

| id | Ruling |
|---|---|
| R1 | Gate criteria, thresholds, benchmark and BH families, cell grids, success thresholds and confirmation cut-offs are invariants. The loop never amends them; only a dated user decision does. |
| R2 | `no_veto` is not registered while the veto is shadow-only, because it equals the primary. H9 is measured within the primary by decision label. A live `no_veto` is a user gate and would replace a secondary by dated amendment. |
| R3 | Stop notifications use native `PushNotification` when available, the Omarchy desktop notification (`scripts/autopilot-session.sh notify`), and the report file. No email, no SMS. The first preflight of each calendar day sends one test notification on each channel and journals the result. |
| R4 | Deploy window: no **full** deploy (`make deploy-omarchy`, which recreates `app-ws`) while any matched game is `in_progress`, within 4 h after any kickoff, within 15 min before any kickoff, or 60 to 100 min before an NFL kickoff; an **app-only** deploy (`make deploy-omarchy-app`, app-ws untouched) may run inside an NCAAF window on Thursday, Friday or Saturday when the full-deploy trigger diff is empty; NFL windows (Sunday from 10:20 CT, Monday night) block every deploy. Exceptions: only 'recorder down', 'executor down', 'app-serve unhealthy', journaled with the games affected. (Journal 128's wording, decided 2026-09-11, pasted 2026-09-15 by the user's ruling on decisions packet item 5 with the Omarchy target names in place of `make deploy-nas` / `make deploy-nas-app`.) |
| R5 | No git remote (a gate). After every phase and every Monday the loop writes `git bundle create` and copies it to `/volume1/docker/sports-harness/repo-backup/` over scp. The user keeps a Time Machine or equivalent copy of the Mac. |
| R6 | One resume drill before 2026-09-12, journaled as `drill`. |
| R7 | Routine reports retain America/Chicago ISO weeks: Week 1 = ISO 37 (paper orders from the phase 3 deploy through Sun 2026-09-13), Week 2 = ISO 38, Week 3 = ISO 39; Monday-night games belong to the following ISO week. **U8 overrides the former Mon 2026-09-21 09:00 selection and Mon 2026-09-28 09:00 confirmation deadlines.** No formal hypothesis selection or confirmation runs until the user ratifies the replacement dated pre-registration amendment; 6F records the new periods, artifact paths and extension rule before confirmatory estimates are examined. Routine reports continue as diagnostics in the meantime. R1 and independent variant-registration deadlines remain in force. |
| R8 | Paper orders are placed once with `expiry = kickoff − 10 min`. No per-loop renewal; `Renew` is a no-op; the orphan window is stated. Phase 4's live adapter uses the same shape plus the watcher's cancel-all fast path, and never exposes `amend(expiry=…)`. |

## Standing authorizations (user, 2026-09-07)

| Action | Authorized |
|---|---|
| Fast-forward merge to `main` after a pristine full suite | **yes** |
| `make deploy-omarchy` and `make deploy-omarchy-app` (restart Omarchy application containers) | **yes**, inside the deploy window below |
| Deploy window (R4) | no deploy while any matched game is `in_progress`, within 4 h after any kickoff, within 15 min before any kickoff, or 60 to 100 min before an NFL kickoff. Three exceptions only: recorder down, executor down, `app-serve` unhealthy. Each exception is journaled with the games affected. Journal128 also permits app-only releases during Thursday–Saturday NCAAF windows when the full-trigger diff is empty and no NFL window is active. Otherwise schedule a wakeup for the window's end and pick another unit. |
| Partitioning migration of `orderbook_events` and `venue_trades` as phase 3 Task 2b (U3) | **yes**, metadata-only `ATTACH PARTITION`, run on Omarchy in the quiet window |
| Archiving or dropping any partition, compaction, retention | **never** without a fresh user yes; the loop may propose (U3) |
| `git bundle create` copied to `/volume1/docker/sports-harness/repo-backup/` after every phase and every Monday (R5) | **yes**, one new additive NAS write path |
| One resume drill before 2026-09-12 (R6) | **yes**, journaled as `drill` |
| Exercise the kill switch during verification | **no**, observe the badge only |
| Brainstorm, plan, and execute phases 4, 5, 6 without waiting | **yes**, decisions from the tables below or the model's judgment, each recorded in the addendum's "Decisions taken on the user's behalf" |
| Operator mode after the last phase (weekly report, alias passes, post-game verification, daily watch, hotfixes) | **yes** |
| Anything touching live trading, bankroll, the legal decision, real money, or destructive NAS actions | **never**, a gate |

Mid-phase deploys that a committed plan instructs are covered by the deploy authorization.

## Files and sections the loop may edit

The loop edits `roadmap.md` only in the Phases table's Status column and User-side TODOs; it adds rows to
`fixes.md`'s `Open` section and moves rows only as `fixes.md`'s preamble allows (`Open` to `Watch` only per hotfix.md's
scope rule, `Watch` to `Open` only by the user's ruling), never deleting one; it rewrites `state.md`; it appends to
`journal.md`, `evidence/` (screenshots and the `-layer2.txt`, `-summary.txt`, `-preflight.txt` outputs),
`reports/`, and `docs/reports/`; it edits `verify.md` only through a plan's last task. Standing authorizations,
Decisions, Secrets, Pre-loaded decisions, the Operator calendar, the Carried fixes pointer text, `fixes.md`'s preamble
and its baseline line, and the section below are the user's text. The loop never edits them, not even to "record" a decision.
Decisions go in the journal, and a decision that would change these sections is a gate.
`.claude/skills/autopilot/` (the skill, its references, scripts and tests), `scripts/autopilot-session.sh` and the v2
spec are never edited by the loop.

## Invariants the loop never changes (hard-forbidden; always a gate, never a ruling)

1. Files under `harness/variants/`, `MAX_PRIMARY`, `MAX_SECONDARY`, and any registered `variant_id`. New
   variants only by a dated pre-registration amendment this roadmap names.
2. Gate criteria and their thresholds (spec §9.5) as coded in `harness/report/gate.py`, plus benchmark and
   BH families, cell grids, success thresholds and confirmation cut-offs (R1). A stored gate definition is
   changed only by a dated user decision.
3. `mode` defaulting to paper; the `LIVE_TRADING` guard; the canary numbers; the paper bankroll;
   `secrets/legal_decision`; any code that would make `harness gate` pass.
4. The log-redaction filter; secrets handling beyond adding a listed file's deploy push.
5. Anything non-additive on the database, in code or by hand: DROP, RENAME, TRUNCATE, DELETE, compaction,
   database retention, `pgdata`, backups already written. Creating partitions is additive and authorized
   (U3); archiving or dropping one is a gate.
6. Journal entries, evidence, reports, archived ledgers: append-only.
7. The spend caps and the code that enforces them: the Odds API credits-per-day band, `veto_daily_usd_cap` =
   $25 and `veto_weekly_usd_cap` = $150 (U4).
8. Outbound hosts beyond: `api.the-odds-api.com`, `api.elections.kalshi.com`, `site.api.espn.com`,
   `api.weather.gov`, `api.anthropic.com`, the Kalshi demo hosts named in Secrets, and
   `site.web.api.espn.com` (user, 2026-09-14, journal 184 item 3 and journal 212: GET only, the one
   path `/apis/common/v3/sports/football/nfl/athletes/<id>/gamelog` for the 4.6 game-log fetcher).
9. The dashboard token and the kill switch: observed, never toggled, never read.

## Secrets (provision when convenient; the loop never blocks on them)

Same handling as the existing files: `secrets/`, mode 600, no trailing newline (`printf '%s' "<value>" >
secrets/<name>`), never pasted in chat or committed. Each feature is coded and tested against recorded
shapes; its live path switches on when the file exists at deploy time (the Kalshi WebSocket recorder
pattern).

| File | For | How |
|---|---|---|
| `secrets/kalshi_key_id`, `secrets/kalshi_private_key.pem` (**first user action**, F64) | the production recorder and, in phase 5, the RFQ listener | Create a **new** production API key scoped `["read"]`, replace both files, then revoke the old key. The default scope is write, so today the "no wagers" property rests only on software guards. A read-scoped key makes it physical. **Done, read-scoped, journal 202.** |
| `secrets/kalshi_demo_key_id`, `secrets/kalshi_demo_private_key.pem` | phase 4 demo smoke (`harness kalshi-smoke --env demo`): place, amend, cancel, group cancel, expiry, fills, positions, balance on play money | The demo account is a **separate signup** at demo.kalshi.co with non-interchangeable credentials and no funds: create it, add mock funds with a test card, then download the PEM once. Hosts are pinned: REST `https://external-api.demo.kalshi.co/trade-api/v2`, WS `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`. Demo prices are not evidence. |
| `secrets/anthropic_api_key` | phase 5 shadow veto and the five report bullets | Already provisioned. It must live in a **new Console workspace with a monthly spend limit and threshold alert** before the file is dropped into `secrets/`, because the loop ships the feature the moment the file appears. The Default Workspace cannot be capped and the per-member Spend Limits API is Enterprise-only. Harness-side caps are U4. **Satisfied 2026-09-14 (journal 201): account-level spending limits are in place and the account cannot auto-buy more; the U4 caps stay.** |
| `secrets/backup_age_key` | phase 4 encrypted backups, generated **by the loop** on the Mac; copy it somewhere safe (a backup no one can decrypt is not a backup) | nothing to do until the loop tells you it exists |
| Novig credentials | **dropped** (user 2026-09-07: not usable in Louisiana) | n/a |

The age private key is never in the deploy push list. Only the committed public key `deploy/backup_age.pub`
reaches the NAS. No key is needed for NWS forecasts, futures snapshots, or the parlay CLI. Optional secrets
are pushed conditionally, so a missing file never fails a deploy:

```
for f in kalshi_demo_key_id kalshi_demo_private_key.pem anthropic_api_key; do
  [ -f secrets/$f ] && scp -O secrets/$f trey@192.168.12.228:/volume1/docker/sports-harness/secrets/
done
```

A Claude subscription OAuth token (`claude setup-token`, `CLAUDE_CODE_OAUTH_TOKEN`) authenticates only
Claude Code and its wrappers (the CLI in `-p` mode, the Agent SDK, GitHub Actions). It is not a credential
for the harness's own API client, so the research layer needs a Console API key with pay-as-you-go billing
(verified against the Claude Code authentication and Agent SDK docs on 2026-09-07). Do not route the
per-candidate veto through `claude -p`: it would draw on the same subscription rate limits the autopilot
runs on.

## Pre-loaded decisions

The brainstorm for each phase treats these as the user's answers. Anything not listed is the model's call,
written into the addendum with rationale, cost if wrong, and how to reverse it.

### Phase 4: Kalshi authenticated adapter (paper), risk gate, backups, Alembic (spec §15.4, §5.2, §9.1–9.4, §14)

1. `Settings.kalshi_env ∈ {demo, prod}`, per-environment secret files. `kalshi-smoke --env demo` asserts the
   resolved host ends in `demo.kalshi.co` before signing anything, and asserts a non-zero balance first,
   journaling "demo unfunded" rather than failing (F63).
2. Reader/writer split (F61): `KalshiReader` has list and get methods only (test: `assert not
   hasattr(reader, "place_limit")`); `KalshiWriter` comes only from a module-level factory. A transport
   backstop `harness/venues/kalshi/http.py` routes every Kalshi call, REST and RFQ, through one `request()`
   that raises `PaperModeViolation` on any method other than GET or HEAD unless live is enabled. Tests: a
   GET passes; `POST /portfolio/events/orders` and `POST /communications/quotes` both raise.
3. `KalshiAuthed` implements the §5.2 protocol on the existing RSA-PSS signing, targeting the **V2
   event-order endpoints** (F62): `POST /portfolio/events/orders`, `POST
   /portfolio/events/orders/{order_id}/amend`, `DELETE …/orders/{order_id}`. `side` in {bid, ask} on the YES
   leg; fixed-point `price` and `count` strings; `time_in_force` and `self_trade_prevention_type` always
   sent; `expiration_time` int64 Unix seconds on send and RFC3339 on read; decoding reads
   `outcome_side`/`book_side` and tolerates an absent `side`/`action`; the echo check compares
   `remaining_count` and `fill_count` as Decimals. Also `post_only=true`, `order_group_id` on every order,
   `cancel_order_on_pause=true`, and the fee model read from `GET /series` and asserted. Amend fields are
   `ticker, side, price, count, client_order_id, updated_client_order_id, exchange_index`; the adapter
   **never exposes `amend(expiry=…)`** (R8, F39).
4. Live guard: constructing the adapter in `prod` with write methods enabled requires `LIVE_TRADING=1`,
   `mode: live`, a stored passing gate report, and `secrets/legal_decision`. None exists; tests assert the
   refusal. Nothing in phase 4 sends an order to production. The demo smoke (a tiny post-only order far from
   the market, then amend, cancel, group cancel, expiry, fills, positions, balance) is a runbook step; the
   verify unit runs it itself when the demo secrets exist.
5. Sharding and limits (F67): `VenueMarket.exchange_index` (`Integer`, default 0) populated in
   `upsert_venue_markets` and passed on every order; a dashboard data-quality line when a matched market
   reports a non-zero shard. Read `GET /account/limits` at adapter construction, log the tier and buckets
   into `runs.notes` and the Health block, and size the recorder's page pause from the read bucket. Treat
   429 as backoff-and-retry and keep it out of the §9.4 two-consecutive-errors outage rule, which counts
   auth and geo errors only.
6. Unchanged from spec: startup reconciliation, the 60 messages/min budget, the three-reject freeze (15
   min), the echo check, `venue_status`, the outage rules. Drawdown stop on equity (−20 % over 7 days):
   paper equity = paper bankroll + ledger; paper mode raises a dashboard alert and labels signals, live mode
   trips the kill switch.
7. Paper-posture tripwire (F30): `venue_requests(method, path, ts)` written by the single Kalshi client,
   never headers or bodies; weekly table 11 asserts non-GET = 0.
8. Backups (F66): nightly 03:30 CT dump of every table except the five bulk tables (`raw_responses`,
   `orderbook_events`, `venue_trades`, `venue_quotes`, `odds_snapshots`), and a weekly dump with the same
   exclusions. **No weekly full dump**: eight full dumps of a terabyte database do not fit beside it, and a
   full `pg_dump` pins the xmin horizon across Sunday recording. A sealed weekly partition is archived once
   (`pg_dump -t orderbook_events_y2026wNN -Fc`, or `COPY … TO PROGRAM 'zstd -T2 > …'`), encrypted, never
   touched again; between the archive and the RAID the tape has no protection, and the addendum says so.
   Encrypted with `age` to `deploy/backup_age.pub`, written to `/volume1/docker/sports-harness/backups/`,
   retention 30 nightly and 8 weekly, `ledger` and `gate_reports` CSV exports kept forever. A backup is not
   done until one **restore drill** into a scratch database has been run with row counts compared. On
   generating `secrets/backup_age_key` the loop notifies immediately with the copy-out instruction and adds
   the nag to Carried fixes until the user confirms.
9. Alembic (F65): the baseline is **hand-written** from `create_schema`. Autogenerate would drop the BRIN
   and functional unique indexes and knows nothing of the partitions, so it is used only for later diffs,
   behind an `include_object` filter excluding those indexes and the partitioned tables, verified by diffing
   `pg_dump --schema-only` of a from-migration database against a `create_schema` database. The migration
   connection sets `lock_timeout = '5s'` and `statement_timeout = '300s'`; every index on a bulk table uses
   `CREATE INDEX CONCURRENTLY` under `autocommit_block()`; `alembic upgrade head` runs only after a
   successful pre-migration dump of the non-bulk tables; the runbook carries a numbered rollback. DROP,
   `ALTER COLUMN … TYPE`, or a non-concurrent bulk index is a gate.
10. Dependency pins (F68): `constraints.txt` from `pip freeze` of the working `.venv`, and `RUN pip install
    -c constraints.txt .` in the Dockerfile, so the NAS builds what was verified. Regenerating
    `constraints.txt` is a gate.
11. The adverse-selection estimate stays frozen at the seed through phase 4 (F56). `signals.as_measured`
    records the trailing markout estimate without using it in the decision. Any promotion is a
    pre-registered week-4 amendment.

### Phase 5: research layer and hypotheses (spec §7, §8, §15.5; Novig removed; items independent, in order)

- **(a)** Futures and ladder weekly snapshots for H7, Tuesdays 09:00 CT, discovered by Kalshi football
  series prefix, raw plus normalized.
- **(b)** NWS forecast snapshots for H6: a stadium YAML (NFL and FBS, lat/lon, roof) built by the model from
  public sources, `api.weather.gov` gridpoint forecasts for outdoor games inside 72 h, hourly, User-Agent
  `sports-harness/1 (self-hosted research harness)`.
- **(c)** Parlay CLI per §8.1 with `parlay.yaml`, writing the dashboard's parlay tables (dashboard spec §3.9:
  `parlay_cards`, `parlay_legs`, `harness parlay placed` into `parlay_placements`, the `parlay_grade` settlement
  stage, `parlay_leg_probs` from the recorder tick while a card is live): weekly budget **$50** (user), smart card $25, lottery card
  $5, at most three lottery cards, legs from moneyline/spread/total at DraftKings prices already in the
  feed, LSU or Saints anchor, rationale from a template unless the Anthropic key exists.
- **(d)** Shadow veto per §7.1: week 1 on `claude-opus-5` at default effort with adaptive thinking,
  structured output (decision ∈ {proceed, reduce, veto}, confidence, reason, evidence ids), web search
  capped at three uses per call, the stable system prompt cached. A paired `claude-sonnet-5` shadow runs on
  the identical frozen prompt for **every call** (U4) and is recorded, never used. Spend caps are U4:
  `veto_daily_usd_cap` $25, `veto_weekly_usd_cap` $150, enforced in code from the usage fields; over budget
  the veto and shadow go dormant for the day, every skipped call is labelled `veto_skipped_budget`, and the
  dashboard shows it. The harness precomputes the numeric features (line moves, disagreement, staleness,
  time to kickoff) and passes explicit timestamps; the prompt defaults to `proceed` and requires quoted
  evidence ids for any `reduce` or `veto`. `research_notes` stores, per call and per model: `subject_id =
  signal_id`, prompt hash, model id and effort, the frozen feature vector, every retrieved snippet with URL,
  timestamp and snippet id, tool calls, the output JSON, usage tokens, cost, latency, request id, both
  models' outputs under one call id, and the joins to the candidate's later CLV and markouts (F73).
  30-minute cache, veto-rate alert, dormant without the key. `no_veto` is **not** registered while the veto
  is shadow-only (R2); H9 is measured within the primary by decision label. The swap rule, trap cases and
  outcome analysis are in the calendar row below.
- **(e)** Weekly report annotator, `claude-opus-5`, five bullets that cite table cells only, rendered inside
  a fenced "model-written, unverified" block, dormant without the key. The Monday duty acts on tables, never
  on the bullets.
- **(f)** RFQ listener per §8.2, on the **read-scoped** production key (F64). The module never calls `POST
  /communications/quotes` and contains no code that could; quotes are computed and stored in `rfq_quotes`
  only. It subscribes to the `communications` WebSocket channel and persists `rfq_created` and `rfq_deleted`
  on arrival, because quotes have not been queryable after the fact since 2026-06-25; it never relies on
  `GET /communications/quotes` for history, and `rfqs` carries the arrival timestamp and the raw message. It
  idles on any non-200 from the subscribe and on any permission error, recording the status code and body in
  `venue_status.reason` (F71). Combo fee rule (F72): subtract a maker fee only when the combo is **not**
  NFL-only-independent, meaning all component events are `KXNFL*` and distinct; record the branch taken on
  `rfq_quotes` so grading can be re-run either way.
- **(g)** Overview page: **moved to phase 4.5 by U6** (Study surface draws equity, CLV by week and fills from
  `equity_snapshots` and `report_cells`). Nothing to build here.

Text handling (F60): RFQ free text is stored but never rendered raw (a 120-character quoted cell) and never
placed in a prompt. The veto `reason` is capped at 300 characters with control characters and markup
stripped; snippets live in `research_notes` and are never re-rendered. The veto design review runs one
injection case through the frozen prompt. The log-redaction patterns (`sk-ant-[A-Za-z0-9_\-]+` and the PEM
block) ship before the Anthropic client exists (F55).

Novig: no adapter, no credentials, no live path (user, 2026-09-07). The `novig` bookmaker column from the
Odds API stays as a read-only benchmark feed already being recorded; H8 is measured from that feed or
reported "not collected".

### Phase 4.5: Dashboard surfaces (U6; spec `docs/superpowers/specs/2026-09-07-dashboard-surfaces-design.md`)

1. The spec is the brief: §2 surfaces and their "never shown" lists, §3.8 and §4 snapshot layer inside
   `app-serve` with `SNAPSHOT_STATEMENT_TIMEOUT_MS = 2000`, §5 front end (static, no build step, vendored
   uPlot, no CDN or font download, 300 KB asset budget, light and dark, 360 px to 2560 px), §6 budgets, §8
   tests. No snapshot builder reads `orderbook_events`, `venue_trades` or `raw_responses`.
2. Visual direction is fixed by the approved design canvas (sources in `docs/superpowers/design/dashboard/`,
   live at https://claude.ai/code/artifact/78f3e81e-bae8-4d69-aa30-2b6697e7dd3b, refinable after week 1);
   the implementer builds from the canvas and the spec, and the reviewer checks the §1.1
   honesty rules (PAPER badge everywhere, no estimate without n and interval, thresholds imported from code).
3. Table t12 (declined candidates with counterfactual CLV) is additive to the report and is not a gate
   input (R1 untouched).
4. `verify.md` gains: `/ui/` loads at 390 px and 1440 px through Chrome, `/api/snap` lists every name with
   age under twice its cadence, the legacy page and `/api/summary` unchanged.
5. Phase 4 item 6 (drawdown alert) becomes a Pulse rule; item 7 (`venue_requests`) becomes a Floor tile.
7. The **Ticket** surface (spec §2.5, user 2026-09-07 evening): the fun-parlay page, phone-first, the
   ticket concept, live "sharps say" probabilities per leg; creates the parlay tables of spec §3.9 (empty until
   phase 5c) and reads them. Real money, its own badge, never on a page with paper numbers.
6. The learnable layer (spec §1.2, user 2026-09-07 evening): server-written sentences per section, two-level
   labels, `glossary.json`, the How-it-works page, and plain phrases for every reason code. The reviewer
   checks every technical term on a surface has a glossary entry and every reason code has a phrase.

### Phase 4.6: Fun tickets inside the existing dashboard (U9; design `docs/superpowers/design/ui-revision-2026-09-12/`)

The brief is the merged design package: `fable/DESIGN-SPEC.md` (the approved design; §0 decisions F01–F09 are the
user's answers), `fable/FEASIBILITY.md` (read against `main` a2a1791; findings 1–5 shape the plan), `AFTER-FABLE.md`
(§2 reconcile the repository first, §3 the addendum's table of changed rules, §4 slices A–F, §5 the ready-to-run
criteria, §7 the completion definition) and the package's PRODUCT-BRIEF, EXPERIENCE-CONTRACTS and DATA-AND-INTEGRATIONS.
The 2026-09-07 dashboard spec and the phase 4.5 addendum remain binding wherever the design is silent. The addendum
records the fable file paths and their hashes (AFTER-FABLE §1) so tasks build the reviewed output, not a canvas.

1. **Scope and order.** Slices A–D (contracts and provider proof; additive data and accounting; read projections and
   local writes; the responsive interface on fixtures) have no 6x dependency and may run while 6B continues. Slice E
   (integrated rehearsal) and F (controlled release and observation) may present corrected execution or report evidence
   only once the matching 6B/6C acceptance exists; until then those rows are labelled and the release is truthfully
   incomplete. Launch scope is NFL and college; an internal NFL-only slice is not the finished scope, and a college gap
   is reported as an unmet requirement, never narrowed silently.
2. **Player stats.** First candidate is ESPN's summary endpoint on the already-allowlisted host (feasibility finding 1:
   one new fetch per watched game, bounded to games and players on placed or alive cards). Measure a live game window
   (update cadence, corrections, missing players) before choosing anything paid. A paid provider inside the $100/month
   ceiling is gate 7 (the user's account action); its secret file is conditional, its feature switches on
   `Path.exists()`. Unmatched players fail loudly as `player_unmatched`; missing stat state reads unknown, never zero.
3. **Props and links.** The Odds API `player_*` families on the per-event endpoint the recorder already calls, plus
   `includeLinks` and `includeSids`, under a fixed monthly prop credit allocation recorded in config and enforced from
   the quota headers with the strategy feed's allocation protected first; gate 5 and the bookmakers string unchanged.
   Plan for `selection` and `event` link capabilities only; `full_slip` does not exist in the product; a label above
   `event` is shown only after verification on the owner's phone and laptop.
4. **Access (spec §5.8).** A second HTTPS listener on the Omarchy LAN address with a self-signed certificate, a single
   owner password whose scrypt hash lives in `secrets/owner_password_hash` (a listed secret: conditional push, never
   read by a brief), an HMAC-signed `Secure; HttpOnly; SameSite=Strict` season cookie, and exactly two POST routes.
   Stdlib only; a new dependency is gate 7. The loopback listener, the tunnel, the kill pair and its token header are
   unchanged (invariant 9 stands). TLS placement (uvicorn on a second serve container or a small proxy) is the model's
   call, recorded in the addendum. No public or remote exposure of this listener (the separate Authelia-gated gateway route to the loopback dashboard is the user's 2026-09-16 amendment, U9, journal 260). **Port 8443** (user, 2026-09-13: "I opened
   port 8443 with ufw"): the LAN listener binds `192.168.12.127:8443` and the compose mapping is
   `192.168.12.127:8443:8443`, never `0.0.0.0` (Docker publishes ahead of ufw, so the address binding is
   the boundary on the WireGuard and Docker interfaces). The ufw rule allows TCP 8443 from `192.168.12.0/24`
   only; the plan verifies it with `sudo ufw status` evidence supplied by the user, not by the loop.
5. **Data (spec §5.1–5.4).** Additive only: new columns with `ADD COLUMN IF NOT EXISTS` mirrored in the model and the
   revision (the repo's first add-column revision; the catalogue-equality test must pass), new tables, indexes built
   `CONCURRENTLY` on bulk tables; `market_type` widens to `String(12)`. The placement route takes a row lock on the card
   and records `confirmation_id` (unique) so two submits record one stake; corrections are offsetting ledger rows;
   corrections after grading are refused. Nothing under `harness/variants/`, nothing in gate code.
6. **Builder.** A scheduled `parlay_build` stage (or scheduler job) replaces the by-hand build; `parlay.yaml` gains
   `policy_version`, the prop pool, the same-game assembly rule and disqualifiers; every card records its version;
   policy changes are config commits, never UI actions. Card shapes, stakes and anchors unchanged (F03).
7. **Front end.** Inside the five surfaces (F01), the 300 KiB budget, the no-external-URL, DOM and glossary tests, the
   five-tab test; the §6 token changes with the light values checked for contrast and the two theme blocks byte-identical;
   broadcast typography in exactly the three places of §6; touch targets 44 px, type never below 12 px, reduced motion.
8. **Reviews and allocation.** Design review: two `opus` reviewers with split lenses (access, secrets and write-path
   security; product data, grading semantics and the paper/fun separation), a setup choice recorded here. Task
   reviewers per the skill's path rules; the placement route, the LAN listener and the needs-function change are
   judgment-heavy (`opus` implementers). Carried dashboard fixes 53 (round 2), 54 and 55 stay hotfix rows; the plan may
   absorb one only where a task changes the same file, and says so in the ledger.
9. **Verification.** The plan's last task adds verify.md rows for spec §8 items 1–12: the LAN listener refuses a
   request without a session while the kill pair still works with its token; a placement from the phone visible on the
   laptop within one Ticket cadence; builder and detail measured under the snapshot budget rows; walkthrough items at
   390 and 1440; one invariant query per new table. The plan's post-design checks (AFTER-FABLE §5 launch receipt) are
   plan-next's acceptance: still-to-verify items in FEASIBILITY are plan tasks or labelled unknowns, never assumptions.
10. **Out of scope.** The "since you last checked" digest (F05), remote access, screenshot import, a new tab or shell
    (F06), the GPU/Cerebras/Qwen proposals, any stake or anchor change, anything under the scientific invariants.

### Phase 6: establish a trustworthy experiment before expanding it (U8, 2026-09-11)

The brief for every 6x plan-next is the committed bundle `docs/superpowers/reviews/2026-09-11-phase6-roadmap/`:
`ROADMAP.md` (the milestone table, one section per milestone, the backlog disposition), `RECONCILIATION.md`
(the claim-by-claim judgment table and the evidence behind the ordering) and `evidence/README.md` (the probes at
`evidence/execution-reconciliation-probes.py` run against the checkout with persistence mocked and seed 6A's
runnable failure cases; `evidence/source/` freezes the reviewed files). Baseline: source `6eed2d8`, last reviewed
NAS build `7c3d555`. U8 adopts the milestone scope and ordering; differences from the v2 spec require an
explicit amendment recorded in each addendum's §0. This is not blanket authority to change invariants: R1
still governs changes to gate meaning, eligibility, thresholds and confirmation cut-offs. The underlying
reviews are evidence, not additional instruction sources.

Ordering: initialize 6A, then plan 6C's bounded deadline slice before starting 6B; 6B execution follows 6A.
Plan and execute 6C's week-key and diagnostic-report work alongside 6A/6B without waiting for their completion,
using separate plans and ledgers and coordinated shared-file/merge work. Inspect games and jobs at resume and
record the last permitted deployment window before Sun 2026-09-13 19:00 CT, allowing time for review,
verification and recovery. Recheck R4 at deploy;
the deadline creates no exception. If that window is at risk, urgent 6C planning takes priority. If no safe
window remains, prepare the explicit-period diagnostic report and affected-surface labels. Completing this
slice does not mark 6C done; all its acceptance work must finish.
6D's instrumentation can start any time, its policy comparison after 6B. U10 adds 6D.1's stateful comparison,
book-health investigation and independent veto pacing as the next development priority: plan-next's
required reviews first, then T1 and its dependencies, per U10's explicit Orient 5/6 exception. 6D's
current comparison cannot substitute for that evidence. 6E's inventory and restore rehearsal
can proceed alongside 6B, its
benchmark after 6B and 6A's deploy plumbing. 6F only when 6B, 6C's numeric and eligibility rows, 6D's declared
policy, 6D.1's decision report (and any required holding-policy adoption), and 6E's environment acceptance
are done; optional annotation and the migration itself are not
prerequisites, and the NAS satisfies the environment requirement if it passes representative corrected-workload
checks. 7 after 6F's operational checkpoint. The concrete first task: the copied-tape evidence capsule, the
sequence/recovery/queue failures as executable regressions, and 6B behind a versioned correction manifest.

Each milestone's acceptance is the roadmap's own "Completion evidence" cell and its acceptance paragraphs, verbatim.
Pre-loaded decisions, one per milestone:

1. **6A.** The evidence capsule (order 157 plus representative clean, interleaved, gap/recovery, delayed-loop and
   capacity-bound periods: trades, deltas, snapshots, fair/gap rows, original orders, fills and ledger, executor
   settings, restart and recovery events) is obtained by indexed bounded extraction or from a copied database,
   outside game windows, never by an expensive audit scan on the recording NAS. Original rows and published
   reports stay intact; corrected replay results are retrospective estimates and say so; a slice without tape or
   transitions is marked unverifiable. An epoch label alone does not exclude historical rows from the cumulative
   gate: an explicitly documented eligibility mechanism (planned, reviewed, verified), or the new-period analysis
   stays separate until one is approved. The correction manifest records old and new code and measurement
   versions, strategy and config hashes, the deployment boundary, affected order and run intervals, eligible and
   excluded measurements, and exact re-score commands. Fix 37 ships in 6A as a hotfix before any deploy that
   depends on it: every changed service (`app-research`; `app-ws` when the diff touches the listener or the sink),
   no no-op DDL lock stalls, stopped services restored when a schema step fails. Fixes 40 and 41 are both in
   `9e21d4f`, unreviewed; review that branch independently while fixing 37, then coordinate the merge/deploy
   wave so the verified fix-37 recipe is in place before any dependent 40/41 deployment. The deployed diff
   and live worktrees are rechecked before any fix is assumed reviewed, merged or deployed.
2. **6B.** One correctness milestone in small reviewed changes: the six bullets of ROADMAP.md §6B (continuity
   over the whole subscription stream with per-market sequence skips allowed and real missing frames still
   detected; snapshot anchoring for deltas and trade watermarks together so a trade already in the snapshot never
   reduces the queue again; trade/decrement reconciliation across equal timestamps, delayed, out-of-order and
   batch-split arrivals, with sensitivity reported where queue ownership is unknowable; the expiry clamp and no
   placement from a rejected latest signal, idempotent under cancel, retry and restart; watched versus
   counterfactual dirty intervals with cause labels and elapsed-time measurement; baseline replay reproducing the
   live variant population and shared capacity). Acceptance: realistic mixed-market fixtures, a genuine
   gap/reconnect case, a recovery containing an older trade, liquidity conservation, no post-expiry fills, no
   placement from rejected targets, batch/restart consistency, and an independently calculated expected
   queue/ledger result (a test that repeats the implementation's assumption is insufficient). Timing-policy
   differences are recorded, never hidden under a parity claim. The order 157 audit publishes validated, corrected
   or unverifiable with the supporting tape; no-watcher outcomes are re-scored only after the same repairs
   (27/445 is not a fill ceiling). Retain raw historical cleanliness measurements. Restoring the existing
   documented measurement is a versioned repair; changing the gate's meaning or eligibility (including which
   placement, fill or resting interval counts) requires R1's dated user decision before adoption, even if
   variant ids stay unchanged. Resume setup choice under U8: use `opus` implementers and reviewers for
   judgment-heavy execution tasks, and **two** `opus` reviewers for the 6B design, covering execution/queue
   modeling and experimental design. This allocation is adopted now as an implementation choice, not quoted
   from the original review roadmap; other task allocation follows the skill's judgment-based rules.
3. **6C.** By Sun 2026-09-13 19:00 CT: Chicago week selection consistent across Ticket, WTD, the Study scheduler
   and readers, and Pulse (fix 33 widened to every raw UTC `isocalendar()` consumer: `ticket.py`,
   `report_wtd.py`, `dashboard/scheduler.py`, `study.py`, `pulse.py`), tested at Sunday evening, Monday, year and
   daylight-saving boundaries; UTC tape partitioning unchanged; the WTD week is a measurement key, so the fix
   carries a dated amendment. If it is not safely deployable in time (R4 and the Friday and Saturday windows
   apply), generate the correct report period explicitly and label the affected surfaces; never call the data
   lost or count the wrong week. Before Mon 2026-09-14 09:00 CT the scheduled report is a **diagnostic**: the
   gate variant's actual filled orders and distinct games, actual and counterfactual methods separated,
   operational coverage and skipped work stated, the order 157 audit status, dashboard freshness distinguished
   from the age of the underlying report cells. README and the coded gate `CRITERIA` reconciled with the
   pre-registration record without adding or removing a criterion, every requirement in its proper place,
   external operational duties named. Confirmation path: the missing ten-game cluster floor (`weekly.py:201`),
   the intended effect direction stored at selection, a definition of confirmation, amendment-specific
   eligibility with the excluded and missing games or outcomes shown (the gate is cumulative and excludes replay
   rows: a new label or a replayed period repairs nothing by itself). Floor's exact-contract fair-value join and
   every side-space comparison fixed (a NO order compares against `1 - mid`). Funnel units explicit: unique
   candidate opportunities, distinct intent episodes, placements, skips, actual filled orders, counterfactual
   outcomes; the 14-day exposure bound becomes a complete aggregate or an explicit coverage limitation.
   Annotation after the numbers: fix 41, then a bounded backlog of unannotated final reports selected from stored
   report cells with backoff and idempotence, and a test that a prior-week final report is annotated; a failed or
   missing annotation never delays the report or draws on the executor's budget.
4. **6D.** Instrument scheduled versus completed collection and evaluation by sport, time to kickoff, feed, market
   and variant; distinguish source quote age, transport lag, fair-calculation age, signal-to-order delay, clean
   resting seconds and tape continuity; count scheduled work that produced no fair, gap or signal row (the
   scored-tick denominator hides missing stages); capacity exclusions separate from strategy rejections and
   unreliable-data skips. Direct fair values and the primary/gate evaluations complete before derived pricing and
   optional research (the 45 s budget can expire before the first variant); stage costs measured, duplicate work
   removed; if a process or job split is needed, raw recording is preserved and queues, budgets and coverage are
   explicit. The old item 4 normalizer is investigated here: the 109/243 exhaustion count is over priced runs
   (2,220 total, 562 non-skipped that day), so the eligible game-day denominator is defined before the 10 %
   trigger is claimed, and a normalizer process alone proves nothing about contention or cost. Policy: the
   baseline keeps the current freshness standards and provides timely refresh in explicitly defined
   participation windows; any alternative proposed for adoption (a 600-900 s stale allowance, rest-to-expiry,
   fixed per-variant slots, fillability admission, join-the-bid, near-kickoff-only) is compared with it on
   equivalent tape and resources
   after 6B, never smuggled in as a hardware workaround; the selected policy is registered and versioned before
   its prospective period. Acceptance is the coverage contract declared before the run: every scheduled eligible
   primary/gate evaluation completes inside its freshness window or leaves an explicit reason and interval, zero
   unexplained omissions; documented missingness is quantified and its tolerance agreed before inference. Reduced
   order churn alone is not acceptance. **U10 follow-on, 6D.1:** the stateful experiment, independent veto
   pacing, book-health diagnosis and diagnostic forecast are a separate milestone, governed by
   `docs/superpowers/specs/2026-09-18-phase6d1-execution-viability-design.md`; its completion evidence and
   retain/revise/stop/insufficient decision precede 6F. Existing 6D acceptance is not reopened or expanded
   by this follow-on.
5. **6E.** Preparation alongside 6B-6D. The user records the Mac mini's chip, RAM, free SSD space,
   container-runtime allocation and intended unattended operation (User-side TODO); nothing is purchased or
   required on the strength of the other review's unverified prices or latency figures. The benchmark uses the
   corrected workload (false dirty states removed activate skipped work): collection, every intended variant,
   research and background jobs, report generation, backup and cold start; indexed reads, batching, cached
   immutable inputs and stored report-cell rendering are kept; next-partition BRIN maintenance and the self-guard
   are fixed here (fix 34: startup protection must still detect persistent overload). The rehearsal inventories
   active and legacy partitions, non-bulk tables, sequences, schema and index options, settings, strategy and
   gate identities, secret availability and permissions, and backup-key handling; the nightly archive excludes
   bulk data and is not a migration source. A plain logical-dump cutover stops every writer before the final
   dump's snapshot and keeps the old stack stopped; otherwise a separately tested synchronization; the "copy
   partitions written since the dump" shortcut is rejected. Contents are reconciled at the boundary by counts and
   key or content checks, never `max(id)` alone; sequences, ledger totals, code and settings identities, restored
   indexes and backup recoverability verified; exactly one writer stack at cutover; rollback data reconciliation
   defined before the move. Tue 2026-09-15 08:00-16:00 CT is a candidate window only (the 09:00 futures job
   accounted for, games and jobs checked). The cutover, and any change of recording host, is the user's yes
   (existing TODO). Operational acceptance: executor loop p95 at most 7.5 s across two representative game
   windows with the telemetry sampling stated, no persistent tape backlog, no sustained paging, real gaps
   handled, timely primary/gate evaluation, completed markout and report work, fresh required surfaces, one
   backup and one cold-start observation, refreshed growth and free-space projections (a retained NAS copy frees
   nothing). The storage and retention proposal is written now from measured growth; no deletion (U3).
6. **6F.** Start once 6B, 6C's numeric and eligibility rows, 6D's declared policy, 6D.1's decision report
   and any required policy adoption (U10), and 6E's environment acceptance are in; record a version boundary;
   the earlier period stays as diagnostic evidence with measurement-specific
   eligibility (no blanket reset, no discarded adverse observation, no claim that every old snapshot is invalid).
   At the first complete healthy football weekend, per variant: eligible market and game coverage, clean
   executable resting hours, unique order episodes, actual queue-filled orders, independent filled games, mature
   fresh CLV and markout coverage, with cancellations, capacity exclusions, gaps, outcome maturity and
   uncertainty; this establishes accrual and operating behaviour, not profitability. Forecast the time to 150
   actual filled orders across 40 games and both sports for `sharp_two_sided` alone: no pooling, no partial-fill
   rows as orders, no counterfactual fills. Revised selection and confirmation dates and any extension rule are
   chosen from completeness and accrual before any confirmatory estimate is examined (U8), selection kept
   separate from the confirmation sample, and an interim decision gets a prospectively specified analysis rule.
   A useful sample that will not accrue in the planned period ends this maker configuration as operationally
   unproductive, or extends the calendar explicitly; a new strategy is a separately registered experiment (7).

Disposition of the previous phase 6 list (ROADMAP.md's table):

| Previous item | Disposition |
|---|---|
| 1 key-number variant `sharp_plus_derived_kn` | phase 7, or exploratory replay after the model repair; it cannot validate the current gate strategy |
| 2 duplicate quote selection | 6D, where it affects source identity or cost |
| 3 taker-imbalance label | deferred; retrospective only where the recorded tape supports it |
| 4 dedicated normalizer | 6D now: define the eligible denominator, choose the design from stage timings |
| 5 bare-city aliases | the operator duty continues; amendments versioned |
| 6 `market_lifecycle_v2` | deferred unless a concrete missing lifecycle input blocks settlement or measurement |
| 7 archive-and-drop policy | the proposal is written in 6E; no deletion (U3) |
| carried fix 33 (week keys) | 6C, before the Sunday/Monday rollover |
| carried fix 34 (self-guard) | 6E |
| carried fix 37 (deploy plumbing) | 6A, a hotfix before dependent deploys |
| carried fix 40 (RFQ boundary) | finish and verify as background-load containment; the listener is not disabled on the old firehose numbers |
| carried fix 41 (annotator budget) | 6C, subordinate to the numeric report |
| cosmetic surfaces, extra model research | phase 7 |

Closed since the reviews: NO-side signals moved into phase 3 as Task 4b (U2); edge priced at the order's
actual contract count is closed by the centicent fee fix (F11), which makes the 100-contract reference exact.

## Operator calendar (America/Chicago)

| When | Duty |
|---|---|
| Monday 09:00 | Weekly report per operate.md's Monday 09:00 CT bullet, written in the controller checkout into `docs/reports/2026-wNN.md` (R16), then `gate`; commit the report; push per U7. |
| Monday 09:30, and the morning after a Thursday or Friday game | Alias pass: `harness match-report` on Omarchy (operate.md's Monday 09:30 CT bullet), additions to `harness/matching/aliases_manual.yaml` on a `fix-aliases-<date>` branch (implementer plus reviewer), merge, deploy, confirm the match rate rose. |
| Monday 09:45, from phase 3 | Replay-vs-live over the last game day: `harness replay --execute` must reproduce live order and fill counts **within 2 %** (R14). Outside the band is an integrity anomaly, not a headline. |
| Monday, and after every phase | `git bundle create` and scp to `/volume1/docker/sports-harness/repo-backup/` (R5; the NAS archive destination is intentional, see operate.md). |
| Morning after every game day | Verify unit (full contract), then the hotfix loop. |
| Daily 09:00 | One journal line covering: free space on `/srv/sports-harness` (a gate below 25 %), free memory (`free -m`), database size vs budget, Odds credits remaining, Anthropic spend against the U4 caps, executor heartbeat, error lines, kill-switch state. (The age-key copy-out nag ended 2026-09-14: the user holds the private key off this host, journal 200; ask for it only when a restore rehearsal must decrypt.) Anomalies become carried fixes. |
| Tuesday 09:30 (once phase 5a ships) | Confirm the futures snapshot job ran. |
| Seven days after the veto goes live | Veto model study on the frozen week-1 cases (stored inputs and snippets, no live search): Opus 5 at `medium` and `low`, Sonnet 5 at `high` and `medium`, three to five reps each. Programmatic checks (valid JSON, every claim cites a stored snippet, veto rate in band) plus three trap cases scored separately: a stale-status trap, an injection trap, and a no-news control. A blind pairwise judge on `claude-fable-5-1` with position randomisation, given the post-hoc CLV sign at `pinnacle_t5` as a second, separate score. Outcome analysis runs **within the primary** by decision label, clustered by game, with `n_clusters` reported. Pre-registered swap rule: programmatic checks pass, blind preference ≥ 60 % on ≥ 50 disagreement cases, and the outcome table shows the candidate is not worse by an equivalence bound. Never on cost or the outcome table alone. **The swap is the user's call** (F73). |
| Mon 2026-09-21 09:00 and Mon 2026-09-28 09:00 (R7) | **Former selection/confirmation deadlines overridden by U8**: no formal selection or confirmation until the user ratifies the replacement dated pre-registration amendment. 6F proposes revised dates and an extension rule before any confirmatory estimate is examined. Chicago ISO-week reports still run as diagnostics; R1 remains in force. |
| Mid-October (user) | Go-live gate review with the legal decision. The loop prepares the gate report and the numbers, never the decision. |

## User-side TODOs
- 2026-09-19 (release 01e7b0c, journal 299; the user's rulings 298): (1) §4.7 in two parts, as the `harness` owner role: part A now (the secret file, `CREATE ROLE harness_exp`, CONNECT/USAGE/SELECT, the REVOKEs and default privileges), part B (the eleven `exp_*` GRANTs) now that 0015 is verified; the runbook block in `docs/runbooks/experiments.md` is not re-runnable as one script (`CREATE ROLE` fails on a second run under ON_ERROR_STOP), so run each part once; the observer flag stays unset; then tell the loop (the privilege pair is read on the next verify). (2) §4.6 activation checklist and §0.14a-c before the first experiment run; M20 index and M13 (journal 295) at your convenience. (3) item 1 (a): decrypt the NAS-pulled w37 `.dump.age` files on the Mac and match sha256 to `.meta.json`; the loop's drill runbook correction is in `docs/runbooks/backups.md` (Restore drill); before the first w37 DROP, freeze or re-point verify.md's Weekly report row (your edit or a plan's last task).
- 2026-09-18 (6D.1, addendum §4.7, ruling C2; journal 291): before the first 6D.1 experiment run (not before its code can land; every run fails closed until this exists): (1) write a password of your choosing to `/srv/sports-harness/secrets/exp_db_password` (mode 600, no trailing newline, like `anthropic_api_key`); (2) run the `CREATE ROLE harness_exp ...` / `GRANT` / `REVOKE` block of addendum §4.7(ii) once as the `harness` database's owner role **after** the release carrying migration 0015 (its `GRANT INSERT` lines name the eleven `exp_*` tables); T1 copies the same text into `docs/runbooks/experiments.md`. No CREATEDB, no second database. Then tell the loop.
- 2026-09-14 (journal 175, host outage): the box lost power or hung Sun 15:32 CT with nothing logged first, and at boot the RTC read 2020-01-01: (1) check the CMOS battery and the PSU/thermal side (**done**: the user reported the hardware failure corrected, 2026-09-14 03:10 CT, journal 178); (2) stop the app from stamping rows with a stale clock: `sudo systemctl enable --now systemd-time-wait-sync.service` and give the unit that starts `/srv/sports-harness` `After=time-sync.target` and `Wants=time-sync.target` (the loop never edits host units; carried fix 57 adds the in-app guard); (3) rule on recorder runs 14485/14486 and their child rows (stamped Sun 15:31-15:32 CT, written Mon 02:21-02:22 CT): leave, annotate, or exclude; a DELETE is gate 3 (**closed 2026-09-14 15:38 CT, journal 205: leave them as recorded; 14485 already carries `clock = unsynced`; no annotation, exclusion or DELETE**). (4) ESPN game log host (4.6 T3/T18a, 2026-09-14 04:1x CT, gate 7): the addendum's `site.api.espn.com/apis/site/v2/.../athletes/<id>/gamelog` form returns 404 for every athlete tried from the host; the working endpoint is `site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/<id>/gamelog` (a different host). A new outbound host is the user's decision; until then the game-log fetcher stays as specified and every prop family stays `market_unsupported` (T18a Path B). Evidence: `.superpowers/sdd/results/t18a-espn-gamelog-v3-*.json`. (**closed 2026-09-14 15:38 CT, journal 212: `site.web.api.espn.com` approved, path-pinned, invariant 8 amended; the fetcher already uses it fail-soft on the branch, journal 184 item 3; the four yardage/receptions families stay `market_unsupported` only because DraftKings' page carries no family rule for them.**) **Relayed ruling 2026-09-15 12:42 CT via session sports-05 (journal 239, confirmed by the user in the loop's session 2026-09-15 12:53 CT, journal 240): item 7 "It is enabled" (systemd-time-wait-sync; the drop-in is on the host per evidence/2026-09-15-coldstart-0746.txt).**
- 2026-09-13 (4.6, D7; journal 166): before the LAN listener can exist, on the Omarchy host as the container uid: (1) run `harness owner-password-hash` (Task 10 adds it) and write its one line into `/srv/sports-harness/secrets/owner_password_hash` (mode 600); (2) generate the self-signed certificate with the runbook's `openssl req -x509 -newkey rsa:2048 -nodes -days 730 -subj /CN=sports-harness -addext subjectAltName=IP:192.168.12.127 -keyout /srv/sports-harness/secrets/lan_tls.key -out /srv/sports-harness/secrets/lan_tls.crt` (Task 17's runbook page; mode 600); (3) install `lan_tls.crt` on the phone and the laptop from the runbook's fingerprint, never by clicking through a warning; then bookmark `https://192.168.12.127:8443/ui/`. The loop creates, copies and reads none of these; the `lan` compose profile switches on only when all three files exist and are non-empty. Optional: add the two TLS files to the Secrets table (user-owned text).
- 2026-09-10 (user): a Mac mini is available as the alternate host if NAS performance impairs the experiment. The loop never moves on its own; it flags the trigger (executor loop p95 over 7.5 s for two consecutive game windows with fixes 31-32 in place, a second starvation incident, or sustained swap traffic outside deploys) in the journal and the phase report, and the migration becomes a plan-next item on the user's yes. **Trigger met 2026-09-10 20:10 CT (journal 101)**: executor loop avg 27 s / p95 118 s in the 19:00 hour with the scheduler under budget, 5 GB of swap resident, IO wait 25-32 %, memory pressure full avg300 16 %; flagged to the user in the session; the phase 5 deploy waits for the user's word on the host. 2026-09-10 20:12 CT (journal 102): the user had the sixteen media containers stopped (`docker stop`, restart with `docker start`); freed about 1.5 GB; the user plans the Mac mini migration for 2026-09-11.

- 2026-09-12 (U8,6E): Omarchy selected and migrated; inventory/restore/cutover recorded. Remaining user-side item: console LUKS unlock for the scheduled cold-start/reboot observation; do not repeat Mac mini inventory or cutover approval. **Scheduled (user, 2026-09-14, journal 203): Tue 2026-09-15 07:00 CT at the console; the loop checkpoints, stops the stack cleanly by 06:50 CT and expects its session to die with the reboot; the user relaunches per Kickoff.** **Missed 2026-09-15 07:00 CT (journal 223): reschedule and tell the loop the date.** **Done 2026-09-15 07:43 CT (decision 226, journals 227 and 229): rebooted at the console, LUKS unlocked, the stack came up from the path unit; cold-start observed.**
- 2026-09-11 (U8, 6F): ratify the dated pre-registration amendment 6F writes for the revised selection and confirmation dates.
- **Odds API: upgrade to the 5M-credit tier before 2026-09-12 (U1), then tell the loop.** Carried fix 10 **Relayed ruling 2026-09-15 12:42 CT via session sports-05 (journal 239, confirmed by the user in the loop's session 2026-09-15 12:53 CT, journal 240): "U1 confirmed"; the runtime reports the 5,000,000 budget.**
  stays unflipped until you confirm.
- Create the Kalshi demo account at demo.kalshi.co and add mock funds with a test card. **Relayed ruling 2026-09-15 12:42 CT via session sports-05 (journal 239, confirmed by the user in the loop's session 2026-09-15 12:53 CT, journal 240): "Optional".**
- Keep a Time Machine or equivalent copy of the Mac (R5). The private GitHub remote (U7) is the second copy.
- 2026-09-11 (journal 128, user decision): paste this replacement for R4 into the rulings table when convenient: "Deploy window: no **full** deploy (`make deploy-nas`, which recreates `app-ws`) while any matched game is `in_progress`, within 4 h after any kickoff, within 15 min before any kickoff, or 60 to 100 min before an NFL kickoff; an **app-only** deploy (`make deploy-nas-app`, app-ws untouched) may run inside an NCAAF window on Thursday, Friday or Saturday when the full-deploy trigger diff is empty; NFL windows (Sunday from 10:20 CT, Monday night) block every deploy. Exceptions: only 'recorder down', 'executor down', 'app-serve unhealthy', journaled with the games affected." Until pasted, journal 128 governs.
- 2026-09-11 (6A, R1): gate measurement boundary. Setting `GATE_ELIGIBLE_FROM_ORDER_ID` / `GATE_ELIGIBLE_FROM_RUN_ID` changes which rows every gate criterion sees. The mechanism ships dormant and the loop never sets it; say the word and the date, and the loop records the decision (set both together: one alone leaves criterion 8 on the whole history). **Relayed ruling 2026-09-15 12:42 CT via session sports-05 (journal 239, confirmed by the user in the loop's session 2026-09-15 12:53 CT, journal 240): "dormant until 6F".**
- The legal decision before any live trading.
- 2026-09-15 (journal 224 item 11): **storage retention decision by 2026-09-22.** Walkthrough item 14 has read under 30 days since journal 189: 121.88 of 600 GB at about 16.9 GB/day puts the ceiling near 2026-10-13. The loop's next operate duty delivers a sized proposal by table (rows, GB, growth per day, what the spec needs kept, the cost of each option) and executes nothing; retention and compaction are invariant 5, executed by the user only. **Proposal written 2026-09-15 08:06 CT (journal 230): `reports/2026-09-15-storage-retention-proposal.md`; the loop's lean is option 1 (drop the archived w37 tape partitions after an off-host copy and a partition decrypt drill); nothing executed.**

The spec §2 legal-facts correction is being applied by the controller, not by the user.
- 2026-09-19 08:26 CT (relay from sports-a0, verified read-only by the loop, journal 304): §4.7 parts A and B done 08:24 CT (`harness_exp` LOGIN, no superuser, INSERT on exactly the 11 `exp_*` tables; secret `secrets/exp_db_password` mode 600); app-research recreated 08:25 CT so the secret binds; the Weekly report row frozen by the user's edit 0234b1c. Still the user's: item 1 (a) on the Mac, §4.6 activation, §0.14a-c, M20, M13; `EXP_OBSERVER_ENABLED` stays unset.

## Carried fixes

Rows live in `docs/superpowers/autopilot/fixes.md` (`Open`: actionable hotfix rows, Orient rule 1's only source;
`Watch`: phase-assigned, user-owned, observation and follow-up rows; `Closed`: done rows). A verify FAIL or an
integrity anomaly adds a row to `fixes.md` `Open`. Rows move between sections and are never deleted
(`context.py check` proves it against the file's baseline line). This section holds no rows; the pre-migration
table is `roadmap.md@c962036` (the migration's `repair` journal entry and
`reports/2026-09-15-context-hygiene-migration.md`).
