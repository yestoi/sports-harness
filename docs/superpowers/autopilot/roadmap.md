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
| 3 Paper execution, settlement, benchmarks, CLV | **planned**, revised by the 2026-09-07 review; gains **Task 2b partitioning (U3, pre-authorized)**, **Task 4b NO-side (U2)** and **Task 12b telemetry (U6)** | `docs/superpowers/plans/2026-09-07-phase3-paper-execution.md` | none |
| 4 Kalshi authenticated adapter (still paper), risk gate, backups, Alembic | not planned | plan-next | none; the demo smoke runs only when `secrets/kalshi_demo_*` exist |
| 4.5 Dashboard surfaces: Pulse, Floor, Study, Gate (snapshot layer, `/ui/`, table t12; absorbs phase 5(g)) | not planned | plan-next from `docs/superpowers/specs/2026-09-07-dashboard-surfaces-design.md` and its design canvas | none |
| 5 Research layer and hypotheses: futures snapshots, NWS, parlay CLI, shadow veto, report annotator, RFQ listener (overview page moved to 4.5) | not planned | plan-next | none; veto and annotator run only when `secrets/anthropic_api_key` exists |
| 6 Deferred items from the phase 2 and 3 reviews | not planned | plan-next | none |
| Operator mode | after phase 6, and calendar duties throughout | n/a | n/a |
| Go-live gate | n/a | n/a | user's legal decision plus a stored passing gate report; never autonomous |

The carried fixes at the bottom of this file run **before** the phase 3 branch is created (R19). Novig is
dropped: no adapter, no credentials, no live path (user, 2026-09-07).

## Decisions (2026-09-07)

User decisions (Trey), recorded verbatim. Only a dated user decision changes them.

| id | Decision |
|---|---|
| U1 Odds API | Upgrade to the 5M-credit tier before 2026-09-12; the spec's "median feed staleness < 90 s" criterion stands unchanged. After the user confirms the tier, the loop flips the recorder settings: `odds_monthly_credits` 100000 → 5000000 and alternates cadence 900 s → 120 s for every event inside 36 h of kickoff (featured cadence unchanged); the 80 % budget alarm follows the new tier. |
| U2 NO-side | Yes: phase 3 gains Task 4b. Replay the NO-side rule over the recorded tape first, then register a two-sided variant under a **new** id by dated pre-registration amendment 3 before 2026-09-16. The six existing ids stay; their YES-only period is labelled. |
| U3 Storage | Partition `orderbook_events` and `venue_trades` now as phase 3 Task 2b (metadata-only `ATTACH PARTITION`). The harness's ceiling is 2 TB of the 3.5 TB free on `/volume1`. Archiving or dropping sealed partitions happens only on the user's later explicit yes: the loop may propose, never execute (gate). |
| U4 Veto spend | `veto_daily_usd_cap` = $25, `veto_weekly_usd_cap` = $150, enforced in code from the usage fields; over budget the veto and shadow go dormant for the day and every skipped call is labelled `veto_skipped_budget`. The Sonnet shadow runs on every call. The key must live in a capped Console workspace (user action). |
| U5 Gate variant | From amendment 3 onward the go-live gate is judged on the `sharp_two_sided` variant's `gate_reports` row (`gate_variant = true`); the pre-registered YES-only primary's row is stored and reported beside it every week. `Settings.gate_variant` defaults to the primary and is flipped by phase 3 Task 4b's deploy; Amendment 3 records the first switched evaluation date. Decided 2026-09-07 before any two-sided data exists. |
| U7 GitHub remote | Decided 2026-09-07 evening: a **private** GitHub repository is the remote `origin`, added by the user as an off-site backup. R5 is amended: the loop **pushes** `main` and the current phase branch (`git push origin main <phase-branch>`) at the same moments it writes the bundle (after every phase and every Monday), and the bundle continues. The loop never pulls, never rebases onto the remote, never opens pull requests, never pushes task worktree branches, and the NAS still deploys from the local `main`. A failed push is journaled, never retried in a loop, and never blocks a unit. |
| U6 Dashboard | Decided 2026-09-07 (design session, spec `docs/superpowers/specs/2026-09-07-dashboard-surfaces-design.md`). Architecture: compute once, render in the browser: jobs write pre-aggregated snapshots, `app-serve` serves them by primary key, a static client renders four surfaces (Pulse, Floor, Study, Gate) under `/ui/`; the legacy page at `/` and the `/api/summary` contract are frozen. Telemetry tables that cannot be backfilled land in phase 3 as **Task 12b** (`metric_samples`, `operator_events`, `order_watch_samples`, `equity_snapshots`, `game_score_events`, `check_results`, `report_runs`/`report_cells`); the front end is **phase 4.5**, planned after phase 4, absorbing phase 5(g). Mobile and desktop both in scope. Visual direction comes from a Claude Design canvas, refined on or after 2026-09-14. |

Controller rulings this file governs. Each is reversible; the review states the cost if wrong.

| id | Ruling |
|---|---|
| R1 | Gate criteria, thresholds, benchmark and BH families, cell grids, success thresholds and confirmation cut-offs are invariants. The loop never amends them; only a dated user decision does. |
| R2 | `no_veto` is not registered while the veto is shadow-only, because it equals the primary. H9 is measured within the primary by decision label. A live `no_veto` is a user gate and would replace a secondary by dated amendment. |
| R3 | Stop notifications use `PushNotification`, a macOS notification (`osascript -e 'display notification "…" with title "autopilot"'`), and the report file. No email, no SMS. The first preflight of each calendar day sends one test notification on each channel and journals the result. |
| R4 | Deploy window: no deploy while any matched game is `in_progress`, within 4 h after any kickoff, within 15 min before any kickoff, or 60 to 100 min before an NFL kickoff. Exceptions: only "recorder down", "executor down", "app-serve unhealthy", journaled with the games affected. |
| R5 | No git remote (a gate). After every phase and every Monday the loop writes `git bundle create` and copies it to `/volume1/docker/sports-harness/repo-backup/` over scp. The user keeps a Time Machine or equivalent copy of the Mac. |
| R6 | One resume drill before 2026-09-12, journaled as `drill`. |
| R7 | Week 1 = ISO 37 (paper orders from the phase 3 deploy through Sun 2026-09-13), Week 2 = ISO 38, Week 3 = ISO 39. Week-2 selection is frozen Mon 2026-09-21 09:00 CT into `docs/reports/2026-w38-selected.json`; week-3 confirmation Mon 2026-09-28 09:00 CT. Monday-night games belong to the following ISO week. |
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

### Phase 6: deferred items (from the phase 2 and 3 reviews)

1. Key-number adjustment at 3 and 7 in the NFL margin model ships as a **new variant**
   `sharp_plus_derived_kn` (`margin_model: key_numbers_v1`) beside the existing one, never as a change to
   the derived model under an unchanged id (F57, F17). Point masses from published margin frequencies,
   sources cited in the addendum; CFB unchanged unless the data says otherwise.
2. Duplicate quote rows per market per run: deterministic latest-`fetched_at` pick plus a run note.
3. Taker-imbalance label from the last five minutes of prints by size bucket.
4. Dedicated normalizer process only if `budget_exhausted` appears on ≥ 10 % of game-day ticks (measure
   first).
5. I9 bare-city aliases and the alias-pass automation (also an operator duty).
6. `market_lifecycle_v2` channel and its landing table, if Task 3b's budget did not allow it in phase 3
   (R11).
7. Archive-and-drop policy for sealed weekly partitions: the loop **proposes** when free space on `/volume1`
   is below 40 % or the database exceeds 500 GB, whichever comes first; execution only on the user's
   explicit yes (U3, F19, F69).

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
| Mid-October (user) | Go-live gate review with the legal decision. The loop prepares the gate report and the numbers, never the decision. |

## User-side TODOs

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
| 14 | anomaly 2026-09-08 01:11 CT (journal entry 43): ESPN scoreboard fetched with no `dates` param, so a game live across ESPN's midnight-Eastern rollover (game 114, kickoff 01:20Z) drops off the body and its `games.status` stays `in_progress` forever (no scores, no `final`, never settles) | `harness/recorder/tick.py` (`_espn`), `harness/normalize/espn.py` | Fetch the previous Eastern date's scoreboard as well while any game from it is not `final`/`postponed`/`canceled` (or refetch by event id), so the linker sees the final status and scores; game 114 heals on the first fixed fetch | A scoreboard body for the previous date carrying a `final` event updates a game whose status was `in_progress`; a game absent from today's body is refetched via yesterday's | ships inside phase 3 on the phase branch after Task 12b merges (tick.py and espn.py are in flight there), before Task 13; rides the phase deploy |
| 15 | verify 2026-09-08 02:24 CT after the quiet-window deploy (journal entry 44): the dashboard funnel counts `signals` per variant over 24 h (3.9M rows/day since U1, 2.5 GB); one variant reads 38k blocks cold (7.2 s), six variants exceed the 30 s statement timeout after any Postgres restart, and `/api/summary` took 92 s | `harness/dashboard/app.py` (`_funnel`) | Read every funnel count that today scans a pricing table (fair values by source, gap snapshots, decisions per variant) from `runs.notes->'pricing'` aggregated over the last 24 h of `runs` (a 2 MB table) instead of scanning `fair_values` (7.5 s), `market_gap_snapshots` (13.5 s) and `signals` (25.7 s per variant, measured 02:36 CT); `raw_responses` and `venue_markets` counts stay; keep the section bounded; no schema change | With 24 h of seeded runs and large pricing tables, `_funnel` issues no query against `signals`, `fair_values` or `market_gap_snapshots` and returns the same counts; the section renders under 1 s cold | ships inside phase 3 on the phase branch with fix 14 after Task 12b merges (app.py is in flight there), before Task 13; rides the phase deploy |
