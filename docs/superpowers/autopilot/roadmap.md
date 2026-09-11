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
| 5 Research layer and hypotheses: futures snapshots, NWS, parlay CLI, shadow veto, report annotator, RFQ listener (overview page moved to 4.5) | done | plan-next | none; veto and annotator run only when `secrets/anthropic_api_key` exists |
| 6A Preserve and define: evidence capsule (order 157 plus representative clean, interleaved, gap/recovery, delayed-loop and capacity-bound periods), correction manifest, runnable failure cases, deploy plumbing (fix 37) | **planned** (2026-09-11 13:56 CT, journal 117) | `docs/superpowers/plans/2026-09-11-phase6a-preserve-and-define.md` (spec: `docs/superpowers/specs/2026-09-11-phase6a-preserve-and-define-design.md`) | none: start now |
| 6B Repair execution: subscription continuity, recovery anchoring, trade/delta reconciliation, expiry, rejection, dirty-time scope, capacity-equivalent replay; the order 157 audit | not planned | plan-next (§6B) | 6A done |
| 6C Trustworthy reports: Chicago week keys on every surface and reader (fix 33 widened), gate documentation and eligibility, confirmation floor, exact-contract joins, funnel units, prior-week annotation backlog (after fix 41) | **planned** (2026-09-11 14:36 CT, journal 120) | `docs/superpowers/plans/2026-09-11-phase6c-trustworthy-reports.md` (spec: `docs/superpowers/specs/2026-09-11-phase6c-trustworthy-reports-design.md`) | none: plan and execute the deadline slice alongside 6A/6B; do not wait for all of 6B. Week keys by Sun 2026-09-13 19:00 CT, diagnostic report before Mon 2026-09-14 09:00 CT; final repaired-fill reports need 6B; the slice alone does not complete 6C |
| 6D Sustained evaluation: scheduled-versus-completed instrumentation, budget isolation, an explicit holding/capacity policy, the coverage contract | not planned | plan-next (§6D) | instrumentation none; the policy comparison needs 6B |
| 6E Operating environment: Mac mini inventory, complete restore rehearsal, corrected-workload benchmark, host choice, measured cutover (fix 34 self-guard) | not planned | plan-next (§6E) | inventory and rehearsal none; the benchmark needs 6B and 6A's deploy plumbing; the cutover itself is the user's yes |
| 6F Valid prospective period: recorded version boundary, first healthy-weekend checkpoint, sample-accrual forecast, revised selection/confirmation dates | not planned | plan-next (§6F) | 6B; 6C's numeric and eligibility rows; 6D's declared policy; 6E's environment acceptance |
| 7 Expand only with a working baseline: new variants and optional hypotheses from repaired research evidence | not planned | plan-next | 6F's operational checkpoint |
| Operator mode | after phase 6, and calendar duties throughout | n/a | n/a |
| Go-live gate | n/a | n/a | user's legal decision plus a stored passing gate report; never autonomous |

The carried fixes at the bottom of this file run **before** the phase 3 branch is created (R19). Novig is
dropped: no adapter, no credentials, no live path (user, 2026-09-07).

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

Controller rulings this file governs. Each is reversible; the review states the cost if wrong.

| id | Ruling |
|---|---|
| R1 | Gate criteria, thresholds, benchmark and BH families, cell grids, success thresholds and confirmation cut-offs are invariants. The loop never amends them; only a dated user decision does. |
| R2 | `no_veto` is not registered while the veto is shadow-only, because it equals the primary. H9 is measured within the primary by decision label. A live `no_veto` is a user gate and would replace a secondary by dated amendment. |
| R3 | Stop notifications use `PushNotification`, a macOS notification (`osascript -e 'display notification "…" with title "autopilot"'`), and the report file. No email, no SMS. The first preflight of each calendar day sends one test notification on each channel and journals the result. |
| R4 | Deploy window: no deploy while any matched game is `in_progress`, within 4 h after any kickoff, within 15 min before any kickoff, or 60 to 100 min before an NFL kickoff. Exceptions: only "recorder down", "executor down", "app-serve unhealthy", journaled with the games affected. |
| R5 | No git remote (a gate). After every phase and every Monday the loop writes `git bundle create` and copies it to `/volume1/docker/sports-harness/repo-backup/` over scp. The user keeps a Time Machine or equivalent copy of the Mac. |
| R6 | One resume drill before 2026-09-12, journaled as `drill`. |
| R7 | Routine reports retain America/Chicago ISO weeks: Week 1 = ISO 37 (paper orders from the phase 3 deploy through Sun 2026-09-13), Week 2 = ISO 38, Week 3 = ISO 39; Monday-night games belong to the following ISO week. **U8 overrides the former Mon 2026-09-21 09:00 selection and Mon 2026-09-28 09:00 confirmation deadlines.** No formal hypothesis selection or confirmation runs until the user ratifies the replacement dated pre-registration amendment; 6F records the new periods, artifact paths and extension rule before confirmatory estimates are examined. Routine reports continue as diagnostics in the meantime. R1 and independent variant-registration deadlines remain in force. |
| R8 | Paper orders are placed once with `expiry = kickoff − 10 min`. No per-loop renewal; `Renew` is a no-op; the orphan window is stated. Phase 4's live adapter uses the same shape plus the watcher's cancel-all fast path, and never exposes `amend(expiry=…)`. |

## Standing authorizations (user, 2026-09-07)

| Action | Authorized |
|---|---|
| Fast-forward merge to `main` after a pristine full suite | **yes** |
| `make deploy-nas` and `make deploy-nas-app` (restart NAS containers) | **yes**, inside the deploy window below |
| Deploy window (R4) | no deploy while any matched game is `in_progress`, within 4 h after any kickoff, within 15 min before any kickoff, or 60 to 100 min before an NFL kickoff. Three exceptions only: recorder down, executor down, `app-serve` unhealthy. Each exception is journaled with the games affected. Otherwise schedule a wakeup for the window's end and pick another unit. |
| Partitioning migration of `orderbook_events` and `venue_trades` as phase 3 Task 2b (U3) | **yes**, metadata-only `ATTACH PARTITION`, run on the NAS in the quiet window |
| Archiving or dropping any partition, compaction, retention | **never** without a fresh user yes; the loop may propose (U3) |
| `git bundle create` copied to `/volume1/docker/sports-harness/repo-backup/` after every phase and every Monday (R5) | **yes**, one new additive NAS write path |
| One resume drill before 2026-09-12 (R6) | **yes**, journaled as `drill` |
| Exercise the kill switch during verification | **no**, observe the badge only |
| Brainstorm, plan, and execute phases 4, 5, 6 without waiting | **yes**, decisions from the tables below or the model's judgment, each recorded in the addendum's "Decisions taken on the user's behalf" |
| Operator mode after the last phase (weekly report, alias passes, post-game verification, daily watch, hotfixes) | **yes** |
| Anything touching live trading, bankroll, the legal decision, real money, or destructive NAS actions | **never**, a gate |

Mid-phase deploys that a committed plan instructs are covered by the deploy authorization.

## Files and sections the loop may edit

The loop edits `roadmap.md` only in the Phases table's Status column, Carried fixes, and User-side TODOs; it
rewrites `state.md`; it appends to `journal.md`, `evidence/` (screenshots and the `-layer2.txt`, `-summary.txt`,
`-preflight.txt` outputs), `reports/`, and `docs/reports/`; it edits `verify.md` only through a plan's last task. Standing authorizations, Decisions, Secrets, Pre-loaded decisions, the Operator calendar,
and the section below are the user's text. The loop never edits them, not even to "record" a decision.
Decisions go in the journal, and a decision that would change these sections is a gate.
`.claude/skills/autopilot/SKILL.md` and the v2 spec are never edited by the loop.

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
   `api.weather.gov`, `api.anthropic.com`, and the Kalshi demo hosts named in Secrets.
9. The dashboard token and the kill switch: observed, never toggled, never read.

## Secrets (provision when convenient; the loop never blocks on them)

Same handling as the existing files: `secrets/`, mode 600, no trailing newline (`printf '%s' "<value>" >
secrets/<name>`), never pasted in chat or committed. Each feature is coded and tested against recorded
shapes; its live path switches on when the file exists at deploy time (the Kalshi WebSocket recorder
pattern).

| File | For | How |
|---|---|---|
| `secrets/kalshi_key_id`, `secrets/kalshi_private_key.pem` (**first user action**, F64) | the production recorder and, in phase 5, the RFQ listener | Create a **new** production API key scoped `["read"]`, replace both files, then revoke the old key. The default scope is write, so today the "no wagers" property rests only on software guards. A read-scoped key makes it physical. |
| `secrets/kalshi_demo_key_id`, `secrets/kalshi_demo_private_key.pem` | phase 4 demo smoke (`harness kalshi-smoke --env demo`): place, amend, cancel, group cancel, expiry, fills, positions, balance on play money | The demo account is a **separate signup** at demo.kalshi.co with non-interchangeable credentials and no funds: create it, add mock funds with a test card, then download the PEM once. Hosts are pinned: REST `https://external-api.demo.kalshi.co/trade-api/v2`, WS `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`. Demo prices are not evidence. |
| `secrets/anthropic_api_key` | phase 5 shadow veto and the five report bullets | Already provisioned. It must live in a **new Console workspace with a monthly spend limit and threshold alert** before the file is dropped into `secrets/`, because the loop ships the feature the moment the file appears. The Default Workspace cannot be capped and the per-member Spend Limits API is Enterprise-only. Harness-side caps are U4. |
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
6D's instrumentation can start any time, its policy comparison after 6B. 6E's inventory and restore rehearsal
can proceed alongside 6B, its
benchmark after 6B and 6A's deploy plumbing. 6F only when 6B, 6C's numeric and eligibility rows, 6D's declared
policy and 6E's environment acceptance are done; optional annotation and the migration itself are not
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
   order churn alone is not acceptance.
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
6. **6F.** Start once 6B, 6C's numeric and eligibility rows, 6D's declared policy and 6E's environment acceptance
   are in; record a version boundary; the earlier period stays as diagnostic evidence with measurement-specific
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
| Monday 09:00 | Weekly report: `ssh trey@192.168.12.228 'cd /volume1/docker/sports-harness && docker compose run --rm -T app-run report --week N --out -' > docs/reports/2026-wNN.md` on the Mac (R16; `docs` is not in the image and `--rm` discards the container, so the file is written here). Then `harness gate` on the NAS. Commit the report; one-line push. |
| Monday 09:30, and the morning after a Thursday or Friday game | Alias pass: `harness match-report` on the NAS, additions to `harness/matching/aliases_manual.yaml` on a `fix-aliases-<date>` branch (implementer plus reviewer), merge, deploy, confirm the match rate rose. |
| Monday 09:45, from phase 3 | Replay-vs-live over the last game day: `harness replay --execute` must reproduce live order and fill counts **within 2 %** (R14). Outside the band is an integrity anomaly, not a headline. |
| Monday, and after every phase | `git bundle create` and scp to `/volume1/docker/sports-harness/repo-backup/` (R5). |
| Morning after every game day | Verify unit (full contract), then the hotfix loop. |
| Daily 09:00 | One journal line covering: free space on `/volume1` (a gate below 25 %), free memory (`free -m`), database size vs budget, Odds credits remaining, Anthropic spend against the U4 caps, executor heartbeat, error lines, kill-switch state, and the age-key copy-out nag until the user confirms. Anomalies become carried fixes. |
| Tuesday 09:30 (once phase 5a ships) | Confirm the futures snapshot job ran. |
| Once, before 2026-09-12 | Resume drill (R6): end and restart the session between two units, journaled as `drill`. |
| Seven days after the veto goes live | Veto model study on the frozen week-1 cases (stored inputs and snippets, no live search): Opus 5 at `medium` and `low`, Sonnet 5 at `high` and `medium`, three to five reps each. Programmatic checks (valid JSON, every claim cites a stored snippet, veto rate in band) plus three trap cases scored separately: a stale-status trap, an injection trap, and a no-news control. A blind pairwise judge on `claude-fable-5-1` with position randomisation, given the post-hoc CLV sign at `pinnacle_t5` as a second, separate score. Outcome analysis runs **within the primary** by decision label, clustered by game, with `n_clusters` reported. Pre-registered swap rule: programmatic checks pass, blind preference ≥ 60 % on ≥ 50 disagreement cases, and the outcome table shows the candidate is not worse by an equivalence bound. Never on cost or the outcome table alone. **The swap is the user's call** (F73). |
| At resume, and each unit/task boundary until the Sunday/Monday duties (U8, 6C) | Prioritize 6C's bounded week-key/reporting plan alongside 6A/6B. Identify the last R4-permitted deployment window before Sunday 19:00 CT and work backward through review and verification; no deadline exception. If it cannot be met safely, prepare the explicit-period diagnostic report and surface labels. |
| Sun 2026-09-13 19:00 (U8, 6C) | Chicago week keys consistent on every surface and reader, or the correct report period generated explicitly and the affected surfaces labelled. |
| Mon 2026-09-14 09:00 (U8, 6C) | The weekly report is a **diagnostic** (the 6C list); the order 157 audit status stated; no criterion removed, no threshold relaxed. |
| Tue 2026-09-15 08:00-16:00 (U8, 6E) | Candidate migration window only, after the rehearsal; the 09:00 futures job and the game/job check first; the cutover is the user's yes. |
| Mon 2026-09-21 09:00 and Mon 2026-09-28 09:00 (R7) | **Former selection/confirmation deadlines overridden by U8**: no formal selection or confirmation until the user ratifies the replacement dated pre-registration amendment. 6F proposes revised dates and an extension rule before any confirmatory estimate is examined. Chicago ISO-week reports still run as diagnostics; R1 remains in force. |
| Mid-October (user) | Go-live gate review with the legal decision. The loop prepares the gate report and the numbers, never the decision. |

## User-side TODOs
- 2026-09-10 (user): a Mac mini is available as the alternate host if NAS performance impairs the experiment. The loop never moves on its own; it flags the trigger (executor loop p95 over 7.5 s for two consecutive game windows with fixes 31-32 in place, a second starvation incident, or sustained swap traffic outside deploys) in the journal and the phase report, and the migration becomes a plan-next item on the user's yes. **Trigger met 2026-09-10 20:10 CT (journal 101)**: executor loop avg 27 s / p95 118 s in the 19:00 hour with the scheduler under budget, 5 GB of swap resident, IO wait 25-32 %, memory pressure full avg300 16 %; flagged to the user in the session; the phase 5 deploy waits for the user's word on the host. 2026-09-10 20:12 CT (journal 102): the user had the sixteen media containers stopped (`docker stop`, restart with `docker start`); freed about 1.5 GB; the user plans the Mac mini migration for 2026-09-11.

- 2026-09-11 (U8, 6E): record the Mac mini's chip, RAM, free SSD space, container-runtime allocation and intended unattended operation, and tell the loop; it inventories and rehearses, and the cutover waits for your yes.
- 2026-09-11 (U8, 6F): ratify the dated pre-registration amendment 6F writes for the revised selection and confirmation dates.
- **Odds API: upgrade to the 5M-credit tier before 2026-09-12 (U1), then tell the loop.** Carried fix 10
  stays unflipped until you confirm.
- **Create a read-scoped (`["read"]`) production Kalshi API key**, replace `secrets/kalshi_key_id` and
  `secrets/kalshi_private_key.pem`, revoke the old key (F64).
- Create the Kalshi demo account at demo.kalshi.co and add mock funds with a test card.
- Move the Anthropic key into a new Console workspace with a monthly spend limit and a threshold alert
  before dropping the file into `secrets/` (F70).
- Keep a Time Machine or equivalent copy of the Mac (R5). The private GitHub remote (U7) is the second copy.
- Copy `secrets/backup_age_key` somewhere safe once the loop creates it, and say so.
- The legal decision before any live trading.

The spec §2 legal-facts correction is being applied by the controller, not by the user.

## Carried fixes

Pre-populated from the review. These run as the loop's **first hotfix units**, in this order, sequentially,
from `main`, before the phase 3 branch is created (R19). A hotfix may start from a review finding when the
brief cites the finding id and the covering test fails before the fix. Every item uses `make deploy-nas`,
not `deploy-nas-app`: the batch touches `ws_sink.py`, `ws.py` and `docker-compose.yml` (R4).

| # | Finding | Files | Change | Covering test | Deploy |
|---|---|---|---|---|---|
| 16 | verify 2026-09-08 04:58 CT after the phase deploy (journal entry 48): `check_results` shows `duplicate_trades` and `fair_values_negative_staleness` as `skip` (their statements exceed the 2000 ms check timeout: an unbounded `fair_values` scan of 475 MB; the trade dedupe over the current week) while the verify.md row expects all `pass` | `harness/ops/checks.py`, `docs/superpowers/autopilot/verify.md` (a plan task only) | Bound both statements so they finish under the timeout (the staleness check by `created_at` over 24 h through the existing index; the dedupe over the current partition with its `(venue, trade_id)` index) and amend the verify.md row to the same statements | Both checks return `pass` on the NAS-sized tables within 2000 ms | **phase work** (a check change is never a hotfix, gate 13): the next plan-next's first task |
| 20 | backup-keygen ran on the Mac 2026-09-08 21:17 CT: `secrets/backup_age_key` (0600, git-ignored, never pushed) and `deploy/backup_age.pub` (committed). The nag stands until the user confirms the private key is copied somewhere safe (roadmap User-side TODO) | n/a (user action) | copy `secrets/backup_age_key` off this Mac (a password manager or an encrypted drive) and tell the loop; a backup no one can decrypt is not a backup | the user's confirmation in chat (journal `decision`) | none |
| 21 | deploy 2026-09-08 21:19 CT (journal 66): the recipe's `init-db` ran while `app-exec`/`app-run` held AccessShare locks on `market_gap_snapshots`, and the `ADD COLUMN IF NOT EXISTS` lost the 5 s `lock_timeout` race, aborting the first phase 4 deploy | `Makefile`, `tests/test_backup_scripts.py` | `docker compose stop app-exec app-run` before the fallback's `init-db` and before the normal `init-db` (the recipe's final `up -d` recreates them); the deploy-order tests pin it | the recipe test asserts the stop precedes both DDL steps | hotfix from `main` after the phase deploy's verify (Makefile only; sonnet impl, sonnet review) |
| 22 | verify 2026-09-08 21:40 CT (journal 68): executor loops 30-140 s, loops_skipped rising, statement timeout on the `orderbook_events` delta read; EXPLAIN shows a BitmapAnd of the 1.67M-row `ts` index with `(ticker, id)` for a 3,349-row result, per ticker per loop, once open orders hit the 150 cap | `harness/execution/store.py` (`load_deltas`/`_DELTAS`), `harness/execution/loop.py` if the cursor write-back needs it, tests | when a cursor exists, read deltas by `(ticker, id)` alone (`id > :cursor`, no `ts` predicate; the id is monotone), keep the `ts` bound only for the first read (cursor 0); bound each read with a LIMIT and advance the cursor to the last row read even on a partial batch so a slow loop makes progress; a loop that errors keeps its cursor progress | query-capture test: no `ts >=` predicate once a cursor exists; a partial batch advances the cursor; the golden gateway test unchanged | hotfix from `main` (execution path: opus impl, opus review), app-only deploy |
| 23 | verify 2026-09-08 21:40 CT (journal 68): `venue_limits.tier` and capacities null on the live response; the decoder reads `tier` and `capacity` where the Trade API sends `usage_tier` and `bucket_capacity` | `harness/venues/kalshi/authed.py`, tests | decode `usage_tier` and `read.bucket_capacity`/`write.bucket_capacity` (tolerating the old names) | a recorded live-shaped fixture decodes tier and capacities | hotfix from `main` with 24 (venues path: opus impl, opus review) |
| 24 | verify 2026-09-08 21:52 CT (journal 68, demo smoke): `place` failed `KalshiDecodeError` because the V2 create-order (and amend) response carries `order_id, client_order_id, fill_count, remaining_count, average_fill_price, average_fee_paid, ts_ms` and no side or price fields; the echo check decoded it as an order | `harness/venues/kalshi/authed.py`, `harness/venues/kalshi/smoke.py`, `docs/runbooks/backups.md`/`phase0-deploy.md` and the verify.md smoke row's command (doc errata: absolute bind paths) | decode the create/amend response as `CreateOrderResult` (counts + ids), do the count arithmetic from it, then `get_order(order_id)` for the side and price comparison in our own side space; the smoke's documented command uses absolute host paths for the two binds | fixtures for both response shapes; the smoke sequence test with the V2 shapes; the demo smoke on the NAS reaches `cancel_group` with `place` and `amend` ok | hotfix from `main` with 23 |
| 25 | verify 2026-09-08 21:32 CT (journal 68): the first `/api/summary` after the deploy took 16.6 s with the page cache churned by the 1 GB nightly dump; the two remaining unindexed reads (`venue_quotes` latest-quote subquery in unmatched markets; `odds_snapshots` 1 h staleness scan) noted in journal 59 | `harness/dashboard/app.py`, tests | serve both from an indexed predicate or a run-note/metric source, as fix 17/19 did for the others; the phase 4.5 snapshot layer is the long-term home | query capture: no unbounded scan of `venue_quotes`/`odds_snapshots`; the cold measurement after the next nightly dump under 10 s | hotfix from `main` (dashboard: sonnet impl, sonnet review) after 22-24 Note 2026-09-10 06:20 CT (journal 92-93): `/api/summary` 34.7 s warm on an idle box; traced to unsummarized BRIN ranges, not these scans: fix 32. Row stays closed. |
| 26 | verify 2026-09-08 23:15 CT (journal 70): on bdea218 the fix 22 read shape plans correctly, but with `EXEC_STATEMENT_TIMEOUT_MS = 10_000` and a cold page cache under I/O pressure a 20,000-row batch cannot finish (a 500-row read at a stale cursor cost 183 disk pages); five watched tickers with cursors ~1M ids behind time out every loop and never advance | `harness/execution/store.py`, `harness/execution/loop.py`, tests | per-ticker adaptive batch: quarter on a statement timeout (floor 250), double on a full read (cap 20,000); `exec.tape_batch_min` metric; `load_deltas(limit=)` | after deploy: no `tape read failed` for 10 loops, `exec.tape_lag_tickers` falling to 0, `exec.tape_batch_min` climbing back to 20000 | hotfix from `main` (execution path: opus impl, opus review); the same item's second attempt: a third FAIL is a gate |
| 27 | verify 2026-09-09 00:10 CT (journal 73, demo smoke): `place` failed `EchoMismatch` because the confirming `GET /portfolio/orders/{id}` returned 404 twice within 270 ms of the V2 create (201); the cancel on the same id returned 200, so the order existed and the venue had not made it readable yet. The reference (ctx7, 2026-09-09) confirms the path | `harness/venues/kalshi/authed.py`, tests | a 404 on the confirming read is retried with backoff (attempts at 0, 250, 500, 750 ms; four in all) before the cancel-and-freeze; other failures keep the single immediate retry | the demo smoke reaches `cancel_group`; tests pin the schedule | hotfix from `main` (venues: opus impl, opus review) |
| 28 | verify 2026-09-09 00:53 CT (journal 76, demo smoke on 6e3b33f): `place` now confirms (404, 404, 200); `amend` 200 then the confirming read at +130 ms returned the pre-amend price (a stale 200): `EchoMismatch` on prob | `harness/venues/kalshi/authed.py`, tests | a confirming read whose `client_order_id` is not the id the write assigned (intent id at place, `updated_client_order_id` at amend) is stale and is re-read on the fix 27 backoff (shared four-read budget); a fresh read is compared once and any mismatch cancels at once; `_decode_order` reads `initial_count_fp` for `count` (V2 reads carry no `count_fp`) and the smoke's step 7 compares `fill + remaining` (journal 77, reproduced live 08:23 CT) | the demo smoke reaches `cancel_group` | deployed d347ee8 2026-09-09 09:12 CT (journal 80); closed 10:49 CT: the demo smoke passes 15/15 on 4c554ea (journal 82) |
| 29 | verify 2026-09-09 09:12 CT (journal 80, demo smoke on d347ee8): `place_expiring` 404 on `POST /portfolio/events/orders` with the `order_group_id` step 8 had deleted; the reference says `DELETE /portfolio/order_groups/{id}` permanently removes the group; the design §1.5 sequence assumed otherwise | `harness/venues/kalshi/smoke.py`, its test | the expiring order goes into a second group, cancelled after the expiry check and on the failure path | the demo smoke exits 0 (`positions` reached) | deployed 4c554ea 2026-09-09 10:48 CT (journal 82): the demo smoke passes 15/15, exit 0; closed |
| 30 | deploy 2026-09-09 23:25 CT (journal 89): `study snapshot study:2026-37 failed`, `psycopg.errors.InvalidTextRepresentation: Token "NaN" is invalid` at the `dashboard_snapshots` upsert; `report_cells.lo/hi` carry `float('nan')` for a one-cluster cell (`harness/report/stats.py:31`), `study.py:144` passes it through, jsonb rejects it, no `study:*` row is ever written | `harness/dashboard/snapshots/study.py`, `snapshots/__init__.py`, tests | non-finite `lo/hi/estimate` -> None in the cell reader; `run_builder` maps every non-finite float to None before the upsert (defence in depth) | a `study:2026-37` row exists with `error` null after the next tick; the Study surface renders the week | merged to `main` 4f987cb 2026-09-10 00:06 CT (sonnet impl, sonnet review PASS/APPROVED, 2,141 tests); deploys with fix 31, app-only |
| 31 | deploy 2026-09-09 23:25 CT (journal 89): with the snapshot scheduler on, the executor loop averaged 54.7 s (max 171 s) against 5.3 s before the deploy; the NAS swapped (4.7 GB in swap, IO wait 24-35 %, load 9.4); Floor builds took 12.5 s and Pulse 11.9 s per tick against the 250 ms budget; `exposure`, `tape`, `orders`, `board`, `funnel` sections timed out repeatedly; the scheduler was switched off at 23:37 CT (`SNAPSHOTS_ENABLED=0` in the NAS `.env` and `deploy/nas.env`, runbook) and the loop fell back to 6.3 s within two minutes | `harness/dashboard/snapshots/floor.py`, `pulse.py`, `study.py`, `scheduler.py`, `harness/dashboard/queries.py`, verify.md | bound every builder read the way the legacy page's reads were bounded (fix 17/19 style: indexed predicates, `now() - interval` on BRIN columns, no `positions`-view scan: the `_EXPOSURE stays unbounded` ruling is reversed), cap Study's weeks per tick, raise the out-of-window cadences (Pulse 60 s, Floor 120 s, Ticket 120 s) until a build is measured under 250 ms on the NAS, and add a scheduler self-guard: a builder over `FLOOR_P95_BUDGET_MS` x 10 for three ticks disables itself and Pulse says so | with the scheduler re-enabled outside a game window: every builder's `serve.snapshot_ms` p95 under 250 ms over 15 minutes, `exec.loop_ms` average within 20 % of the 15 minutes before, vmstat swap-in/out 0, then the same during Thu 2026-09-10 LAR-SF | merged to `main` 5261478 2026-09-10 06:30 CT (opus impl, opus review with two Importants fixed in round 1, sonnet re-review CLEAN, 2,284 tests); deploys app-only with 30 and 32 before the LAR-SF window (17:55 CT), deployed app-only 06:36 CT as 4f7b2c8 (journal 95); `Scheduler re-enable` row PASS 06:54 CT (journal 96): scheduler on, `SNAPSHOTS_ENABLED=1` in `deploy/nas.env` |
| 32 | verify 2026-09-10 06:20 CT (journal 93): no BRIN index had `autosummarize`; a 5-minute `orderbook_events` count took 39.6 s until 1,078 ranges were summarized (2.8 ms after); `fair_values` 24 h check 25.7 s -> 141 ms after 48 ranges; the cause of the legacy page's 35-50 s, the check skips, the WS sink timeouts and much of the snapshot storm | `harness/db/schema.py`, `harness/db/partition.py`, `harness/ops/housekeeping.py`, tests (a revision only if the catalogue diff sees reloptions) | `with (autosummarize = on)` on every BRIN DDL; `init-db` alters any BRIN lacking it; the weekly partition creator inherits or alters; housekeeping runs `brin_summarize_new_values` over every BRIN and records `db.brin_ranges_summarized` | after the deploy: `pg_class.reloptions` shows `autosummarize=on` on every BRIN including the next new partition; the 5-minute count under 50 ms cold; the three skipped checks pass at the next housekeeping | merged to `main` 32f8e4a 2026-09-10 07:11 CT (sonnet impl, sonnet review PASS/APPROVED, 2,297 tests); deployed 07:13 CT as 3ca32dd (full recipe, alembic 0003; journal 98); every physical BRIN autosummarize=on on the NAS |
| 33 | phase 5 T11 review 2026-09-10 17:05 CT (ledger): `harness/dashboard/snapshots/ticket.py:247` and `harness/settlement/report_wtd.py:67` key the ISO week with a raw `now.isocalendar()` on UTC, so Sunday 19:00-23:59 CT activity lands in the following week; T11's cap and T18's annotator use `harness.research.spend.chicago_day` | `harness/dashboard/snapshots/ticket.py`, `harness/settlement/report_wtd.py`, tests | key both to `chicago_day(now).isocalendar()`; report_wtd's week is a measurement key, so the fix carries a dated amendment (ids unchanged, pre-fix range stated) per spec §6.7 | a Sunday 20:00 CT sample groups with that Sunday's week on the Ticket surface and in the WTD report | **Phase work: 6C** (U8, 2026-09-11; sonnet impl, sonnet review): plan and execute the deadline slice alongside 6A/6B; scope widened to every raw UTC `isocalendar()` consumer (`ticket.py`, `report_wtd.py`, `dashboard/scheduler.py`, `study.py`, `pulse.py`) with boundary tests and the dated amendment; deadline Sun 2026-09-13 19:00 CT (calendar). Not a hotfix. |
| 34 | verify 2026-09-10 20:07 CT (journal 101): `snapshot_disabled` for the study builder at 17:55 CT after 2,867 / 2,908 / 3,152 ms builds, all over the 2,500 ms guard, on a NAS with 5 GB of swap resident and IO wait 25-32 % | `harness/dashboard/snapshots/study.py`, `harness/dashboard/scheduler.py`, tests | measure study's per-query cost cold; cut or bound the slow query, or move study to a longer cadence with a guard proportional to its cadence (a 600 s builder is not a 30 s one); a restart alone re-trips the guard | study rebuilds under 2,500 ms three times running on the live box; no `snapshot_disabled` in 25 h | hotfix from `main` after the phase 5 deploy (dashboard: sonnet impl, sonnet review). 2026-09-11 03:18 CT (journal 109): the phase 5 deploy's restart re-enabled study (1,805 ms) and then tripped the same guard on **pulse** during the post-restart cold-cache storm (6,334 / 4,617 / 4,298 ms with IO wait over 40 % and the executor's first loops at 91 and 129 s); the guard has no notion of a warm-up window, so every full deploy on this box will pause a builder. The fix is the guard's, not the builders': skip the guard for the first N minutes after start, or judge against the cadence, and read `free -m` before pausing **Phase work: 6E** (U8, 2026-09-11): the self-guard fix ships with 6E's environment work; startup protection must still detect persistent overload. Not a hotfix. |
| 35 | verify 2026-09-11 03:16-03:45 CT (journal 109): the RFQ shadow quote is not cheap per frame: the venue replays the open RFQ set on every subscribe (4,902 frames / 2,613 distinct in 30 min across ten reconnects; 2,423 in one minute), every `rfq_created` recomputes the quote, and every leg of every combo (2-37 legs, almost all non-football) runs the fair-value lateral that reads 16,264 `fair_values` rows through `ix_fair_game_type_created` to keep 236 (14 s cold / 10 ms warm); with the nightly dump churning the cache the executor loop went 4-7 s (the day before) to 235-336 s; 11 quote timeouts; listener switched off at 03:44 CT | `harness/venues/kalshi/{rfq_quote,rfq,rfq_socket}.py`, `harness/settlement/rfq_grade.py` (comment), `harness/db/schema.py`, `harness/db/migrate.py`, `migrations/versions/0005_rfq_lookup.py`, tests, `docs/runbooks/{research,alembic}.md` | (1) decline `no_fair`/`single_leg`/`same_game` before any `fair_values` read (a non-football leg never has a direct fair); (2) never recompute a quote the harness already holds (replayed frames are stored as arrivals only); (3) `ix_fair_leg_lookup (game_id, market_type, outcome_team_id, outcome_side, threshold, created_at desc) where fair_source = 'direct'`, concurrently, revision 0005; (4) a replay cap of 500 quoted frames per subscribe | with the listener on through a nightly dump: executor loop p95 under 15 s in the 03:30-03:45 window; zero `computing the counterfactual quote` errors; a replayed frame computes nothing; a 30-leg cross-category combo declines in under 20 ms | merged 5f18601, deployed 2026-09-11 05:26 CT with revision 0005 (journal 110); listener on; the rate limit engaged on the first replay burst as designed; verify at the morning pass |
| 36 | verify 2026-09-11 03:16-03:27 CT (journal 109): `annotate_pass` re-renders every weekly table (`weekly_tables`) on each 30 s sweep until one succeeds; the render cannot finish inside the 30 s statement timeout on the NAS, so the worker ran a permanent heavy-query stream from the phase 5 deploy until the controller switched it off at 03:27 CT (`RESEARCH_WORKER_ENABLED=0`); report_runs 4 pending; no spend | `harness/research/annotate.py`, `harness/report/render_for_model.py`, tests, `docs/runbooks/research.md` | render the model's view from the stored `report_cells` of the pending run (one bounded read; the Study surface's `_CELLS` precedent) and never call `weekly_tables`; a failed pass backs off an hour per report in `job_state` (24 h after three), success clears it | with the worker on: no `annotate failed` line in an hour; report 4 annotated within one sweep once the key is present; `research_spend` shows one `annotate` call | merged a6a27d0, deployed 5f18601 2026-09-11 05:26 CT (journal 110); worker on; verify at the morning pass |
| 37 | deploy 2026-09-11 05:20 CT (journal 110): `make deploy-nas` failed twice at the schema step: `alter table fair_values add column if not exists feed_kind` (a no-op ALTER that still takes ACCESS EXCLUSIVE) hit the 5 s `DDL_LOCK_TIMEOUT` behind an app-serve builder holding ACCESS SHARE on `fair_values` for 236-286 s inside one transaction; the recipe had already stopped `app-exec` and `app-run`, so the executor was down four minutes and the controller started them by hand | `Makefile` (deploy-nas, deploy-nas-app), `harness/db/schema.py` (`create_schema`), tests | stop `app-serve` for the `init-db` step exactly as `app-exec`/`app-run` are stopped, and restart all three after; `deploy-nas-app` must also rebuild and recreate `app-research` (and `app-ws` when the diff touches `harness/venues/kalshi/rfq*.py` or the sink), which it predates; in `create_schema`, skip an `add column if not exists` whose column is already in `information_schema.columns` (no lock for a no-op) and likewise for `create index if not exists` | a full deploy with the snapshot scheduler on completes the schema step in under 10 s with no LockNotAvailable; the executor's downtime in a deploy under 90 s | **merged to `main` 6eb718a 2026-09-11 14:51 CT** (journal 122: sonnet review spec PASS, quality PASS, one Important fixed in round 1, haiku re-review ADDRESSED; no-op DDL skipped by catalogue lookup incl. partitioned indexes; `app-serve` stopped with the writers and every stopped service restored with `docker compose start` plus an ABORT line on failure; `deploy-nas-app` recreates `app-research` always and `app-ws` under `WITH_WS=1`); **exercised by the wave deploy bd220b8 2026-09-11 16:08 CT** (journal 123: 78 no-op DDL statements skipped, no ABORT, executor back inside 90 s) |
| 38 | verify 2026-09-11 05:31 CT (journal 110): the venue's `communications` channel delivers every RFQ create/delete on the exchange, 11-14k frames a minute sustained, three quarters deletes, almost none with a football leg; `store_rfq` wrote all of them (74,608 rows / 128 MB in 7 min, ~30 GB a day); listener off at 05:33 | `harness/venues/kalshi/rfq.py`, `rfq_socket.py`, `harness/ops/housekeeping.py`, tests, `docs/runbooks/research.md`, verify.md's `rfqs arrivals` row | store only combos with at least one `FOOTBALL_SERIES` leg; drop deletes for unknown ids; counters and one summary line a minute; rate-limit log hysteresis; bounded retention for unquoted `rfqs` rows older than 7 days | listener on through a night: `rfqs` grows under 5,000 rows a day, `frames_stored` a small fraction of `frames_seen`, app-ws CPU under 10 % | merged 2c687d2, deployed 7c3d555 2026-09-11 06:38 CT (journal 112); listener on; verify at the settle read and the morning pass |
| 39 | verify 2026-09-11 05:26 CT (journal 110): the first live annotator call returned HTTP 400; the three research schemas carry `maxItems`/`maxLength`/`minimum`/`maximum`, which the structured-output subset rejects (the SDK's parse helper strips exactly these for class schemas; raw dicts reach the API unstripped); the client records only the exception class, so the body was lost | `harness/research/{client,notes,annotate,prompt,veto}.py`, `harness/parlay/rationale.py`, tests, `docs/runbooks/research.md` | supported keywords only, constraints enforced in code after parsing (clamps and truncations), a recursive schema-keyword test; the client logs and stores the API's sanitized message as `error_detail` beside the class | the annotator annotates report_runs 4 (or the week's next final report) with bullets; the veto's first daytime calls return decisions, not `veto_error`; `research_spend` shows non-zero usd within the caps | merged 777453c, deployed 7c3d555 2026-09-11 06:38 CT (journal 112); backoff cleared; verify at the settle read |
| 40 | verify 2026-09-11 06:42 CT (journal 112): fix 38's "at least one football leg" still stores ~3,000 `rfqs` rows a minute (cross-category combos with one NFL leg) and every quote declines `no_fair` | `harness/venues/kalshi/rfq.py`, `rfq_socket.py` (counter name), tests, runbook, verify.md's `rfqs arrivals` row | store only combos whose every leg is a `FOOTBALL_SERIES` market | `rfqs` grows under 1,000 rows a day with the listener on | hotfix with fix 41 (venues: sonnet impl, opus review); **merged to `main` e1cc629 2026-09-11 13:39 CT** (journal 116: opus review spec PASS, quality PASS; two verify.md wording Importants applied); **deployed bd220b8 2026-09-11 16:08 CT** (journal 123, full recipe); verify rows judged in journal 124. U8: finish and verify as background-load containment; never disable the listener on the old firehose numbers. |
| 41 | verify 2026-09-11 06:42 CT (journal 112): the annotator's first accepted calls stopped at `max_tokens` (1,024 out) with 46,528 input tokens against a 30,000 worst case; recorded as `schema`; $0.52 spent for nothing | `harness/research/{annotate,spend,client,veto}.py`, `harness/report/render_for_model.py`, tests, runbook | annotate `MAX_OUTPUT_TOKENS = 4096`, `EFFORT = medium`; `WORST_CASE_INPUT_TOKENS = 60_000`; 60 rows per table in the model's view; `max_tokens` labelled as its own error; the veto's output ceiling checked | report_runs 4 annotated with bullets on the next attempt; `research_spend` shows the call under the projection | budget hotfix with fix 40 (research: sonnet impl, opus review); **merged to `main` e1cc629 2026-09-11 13:39 CT** (journal 116); **deployed bd220b8 2026-09-11 16:08 CT** (journal 123). U8: the budget change alone is insufficient (the worker selects only the current Chicago week, so Monday's previous-week final report is never eligible); the bounded backlog selection is 6C work, subordinate to the numeric report. |
| 42 | operate 2026-09-11 14:18 CT (journal 119): every pricing run since 13:03 CT fails `QueryCanceled` (30 s statement timeout) on `build_gap_snapshots`' select over `venue_quotes` by `run_id` (gaps.py:55-66); `venue_quotes` (3.58 M rows, 773 MB) has no index leading with `run_id`, so the plan walks every quote of each of the 1,871 matched future-kickoff markets with `run_id` as a filter (cost 52,000, cold); `runs.status = degraded`, `pricing = {}`, no signals, open orders 0 before the Friday games | `harness/db/models.py`, `harness/db/schema.py`, `migrations/versions/0006_quotes_run_index.py`, `harness/pricing/gaps.py` (comment), tests, `docs/runbooks/alembic.md` | `ix_quotes_run_market (run_id, venue_market_id)` declared on the model, in `_CONCURRENT_INDEX_DDL` and in revision 0006 (concurrently, if not exists); an EXPLAIN test pins the plan | the next pricing run after the deploy is `ok` with `pricing.ticks > 0`; no `pricing failed` line in 24 h | hotfix from `main`, ships alone ahead of every batch (pricing path: opus impl, opus review); **the controller built the identical index by hand on the NAS at 14:17 CT** (journal 119 ruling) so pricing recovers before the deploy; the revision finds it present |
| 43 | operate 2026-09-11 14:18 CT (journal 119): since 13:08 CT every 15-minute resubscribe plan is rejected by the venue, `{"code": 12, "msg": "Exactly one subscription ID is required"}` (54 frames), `should_reconnect` raises and the market socket reconnects: a tape gap every 15 min, 132 dirty books on the heartbeat; `_resubscribe` (ws.py:193-207) sends `"sids": self._sids` (every acked sid) while `_recover_gap` sends one sid per frame and is accepted | `harness/venues/kalshi/ws.py`, its tests, one runbook line | one `update_subscription` frame per sid per action, consecutive ids | no `subscription rejected` line in 24 h; `ws_disconnect` events back to the pre-13:08 rate | **merged bdd1f1d, deployed bd220b8 2026-09-11 16:08 CT** (journal 123; opus review PASS, one Important test added in round 1); verify: no `subscription rejected` after the restart (journal 124) |
| 44 | verify 2026-09-11 17:32 CT (journal 125): the RFQ listener has received nothing since the venue's error frame at 21:10:02Z (untrusted venue text: `{"code": 25, "msg": "Subscription buffer overflow"}`): no summary line, no reconnect, no frame for 80 min, `venue_status('kalshi_rfq')` still `ok`; the listener logs the frame as "not an idling code" and keeps reading a subscription the venue has dropped, and the one-hour idle rule (`IDLE_S`) never fires because it is checked on frame arrival | `harness/venues/kalshi/rfq_socket.py`, `rfq.py`, tests, runbook | treat error code 25 (and any error frame naming the listener's sid) as a reconnect trigger; an idle watchdog that fires without frames (a timer, or a check on every recv timeout) writes `venue_status` and resubscribes | after the deploy: a summary line every minute; `rfqs` arrivals resume; an injected code-25 frame in tests forces a resubscribe | hotfix from `main` (venue socket path: opus impl, opus review); deploys in the Fri 23:00 CT wave; not data-stopping for the tape (the market socket is separate, ruling A-C1) |
