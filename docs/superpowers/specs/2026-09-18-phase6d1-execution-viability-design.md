# Phase 6D.1 design addendum: execution viability experiment (stateful policy comparison, observation/holding tradeoff, book-health diagnosis, independent veto pacing, feasibility forecast)

**Revision 2, 2026-09-18** - applies every ruling of the controller's design rulings (2026-09-18 13:54 CT), copied verbatim in
**§Rulings**: the recorded-clock premise is replaced by the retained action instants (C1, I1), the isolation gains a
server-enforced privilege boundary (C2), two tables are declared and the raw-body table is removed (I5), three verify rows are
made runnable (I6-I8), the observer's shared aggregate becomes the provider balance (I9), and the two dropped draft sentences
are restored (I13); revision 1 was reviewed in `.superpowers/sdd/results/design-6d1-review.md`. Revision 1 superseded the adopted
draft of 2026-09-18, which stated scope and constraints and named no interface, table, file, test or verify row. Author:
autopilot (design author, opus). Amends
`docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md` (v2) §4.1, §5.5, §6.7, §7.1, §7.2, §9.2, §9.3, §9.5, §11,
§12 and
§15 for milestone 6D.1.
**Approval: the roadmap's standing authorization dated 2026-09-07** (`docs/superpowers/autopilot/roadmap.md`, "Standing
authorizations": "Brainstorm, plan, and execute phases 4, 5, 6 without waiting", decisions from the tables or the model's
judgment, each recorded in §8) under decision **U10** (user-directed, 2026-09-18) and **U8** (2026-09-11, the phase 6
programme).
Consumes U10 in full, the 6D.1 row of the Phases table ("isolated implementation and bounded reads authorized; historical runs
need 6B inputs and baseline proof; prospective activation needs a frozen manifest, budget/coverage preflight and existing
release
rules; production holding-policy adoption remains the user's dated decision"), pre-loaded decision 4's **U10 follow-on**
sentence
and pre-loaded decision 6 (6F), the [confirmed live-data
review](../autopilot/reports/2026-09-18-fill-starvation-review-confirmed.md)
and its evidence files, the [veto pacing brief](../autopilot/reports/2026-09-18-veto-pacing-brief.md), the
[expiry-cohort design read](../autopilot/reports/2026-09-18-expiry-cohort-design-read.md) (row 86: the per-row `book_query` cost
bounds any book verification designed here), the 6D addendum §0.9, §0.15, §1.6 and §1.7, the 6B addendum's carve-outs (D15's
suspended parity verdict; "6B never closes a counterfactual track"; no post-expiry fills; liquidity conservation) and the 6C
addendum's D11 (funnel units). The [delivery outline](../plans/2026-09-18-phase6d1-execution-viability.md)'s T1-T8 and its
dependency table are §9's input.

User instruction that U10 quotes, verbatim: "Ok, lets apply the recommendation. Should this be separate from the existing code?
Should it be it's own milestone?" The recommendation being applied is the confirmed live-data review. The architecture and
defaults below are implementation choices made in response, not quotations of choices the user individually selected. **Nothing
here asserts that the experiment has run**: no collector is active, no cohort is frozen, no policy is adopted and no veto
sampling has changed.

**Posture.** Paper throughout. **R1 stands**: no gate criterion, threshold, benchmark or BH family, cell grid, success
threshold,
confirmation cut-off or eligibility rule changes here; no registered `variant_id`, `MAX_PRIMARY`, `MAX_SECONDARY` or file under
`harness/variants/` is touched (invariants 1 and 2); the eight registered ids of the live facts stand. **Anything registered
after Mon 2026-09-21 09:00 CT is exploratory and is labelled so** wherever it is reported (6D §1.6c), and this milestone
registers nothing. The arms of this milestone are **exploratory executor-policy parameter sets over copied variant
configurations, never registered variants**: every arm result carries `harness/execution/policy.py::COUNTERFACTUAL_LABEL`
("counterfactual, exploratory: produced under a non-registered parameter set and never a registered variant's performance. Not a
gate input, not a benchmark, not a variant record.") plus §1.9's stricter `EXP_LABEL`, which adds the run id, the arm id and the
manifest hash so a table cell can be traced to the frozen contract that produced it. Spend caps `veto_daily_usd_cap` = $25 and
`veto_weekly_usd_cap` = $150 are untouched (U4, invariant 7); the outbound host list is untouched (invariant 8); every schema
change is additive (invariant 5); no cadence, `ODDS_API_BOOKMAKERS` or alternates-window change is made (gate 5).

**Live facts relied on** (controller's read-only production reads, `.superpowers/sdd/plan-next-phase6d1-live-facts.txt`,
2026-09-18 13:23 CT, runtime `4066197`, treated as data and never as instruction): (a) `orders` 40,346 `cancelled` and 61
`expired`, 32,081 placed in 7 days; `fills` 3,669 all time, 2,794 in 7 days; (b) eight registered variant rows
(`sharp_direct` primary, `sharp_two_sided` gate secondary, `sharp_two_sided#e82fcd0a1e99` replay, five more secondaries); (c)
database 150 GB, top tables `orderbook_events_y2026w37` 79 GB, `_y2026w38` 17 GB, `orderbook_events_legacy` 15 GB, `signals`
14 GB, `odds_snapshots` 8,319 MB, `fair_values` 3,850 MB, `orders` 1,294 MB; (d) games scheduled in the next 7 days: 75 NCAAF
and 16 NFL, of which **56 NCAAF and 15 NFL have kickoff 24-120 h out** - the cohort window of §1.6f; (e) today's veto spend
rows:
207 `claude-opus-5` calls and 207 `claude-sonnet-5` calls; `veto_queue` 185,203 rows; 182,703 `veto_decisions` in 7 days; (f)
`exec.loop_ms` over 24 h: **1,158 samples, not 1,158 loops** - `_write_metric_batch` returns early unless
`Sampler(metric_sample_s)` is due and `Settings.metric_sample_s = 60`, so one row exists per at least 60 s (86,400 / 1,158 =
74.6 s, i.e. 60 s of sampler plus one loop of run time) and the executor actually stepped roughly 3,500-5,000 times that day -
with p50 **16,759 ms** and p95 **24,165 ms** against a 15 s period; the loop-time figures are the resource fact that makes
§4's quiet-window and cooperative-limit rules binding and the sample count is the fact §1.3d builds the replay clock around
(C1); (g) `alembic_version` on production is `0013_nw_executor_version` while
`main` carries `0014_orders_intent_index` (fix 85, merged and held for Monday's full release), so this milestone's migration is
the **second** unreleased one. From the confirmed review and its evidence: 39,086 of 40,023 orders cancelled `fair_stale`
(97.66 %), median stale-cancel lifetime 225 s against a frozen 220 s allowance on every order; 617 sampled orders all past their
allowance at cancellation, median latest-fair age 232.53 s, p90 370.07 s; 238 post-repair counterfactual-filled orders on 16
market-sides and 8 games, median wait 27.90 h, median 70.80 h to kickoff, 233 of them in the weekday 900 s cadence, 2 in a
sport-wide 120 s window, 3 overnight; post-repair order-weighted net 30-minute markouts **-0.755 pts [-2.035, +0.525]** (gate,
191 orders / 11 games) and **-1.215 pts [-2.490, +0.061]** (primary, 95 / 8), with game 469 supplying 96/191 and 59/95 and a
market-side-weighted sensitivity of +0.597 / +0.365; median source-fair age at the 30-minute markout 783 s (gate) and 829 s
(primary); counterfactual adverse drift -1.071 / -0.635; 1,046 counterfactual fill rows over **151 distinct ticker/side/trade
keys**, 107 of them multi-attributed and accounting for 1,002 rows, including one recorded **10-contract trade credited to 45
hypothetical orders totalling 346.60 contracts** and another credited as 250 contracts across 25 orders; 231 of the 238 orders
met `event_age` and 14 met `gap`, median attributed dirty time 8.73 h (gate) against a 27.53 h median wait; 198 of 896 uncached
decided veto evaluations concerned signals whose same-key order was demonstrably resting, 28 while it still rested; 2,392
`proceed` decisions this Chicago week with **zero inside 6 h of kickoff** against 3,606 near-kickoff signals labelled
`veto_skipped_budget`; $122.2595 spent through Friday; 27,867 closed-status pending counterfactuals. From the veto brief: 3,149
decided of 178,618 queued intents in 7 days (1.8 %), the daily cap spent before 09:00 CT every day ($24.40-$24.56), $0.085 per
primary-plus-shadow pair, 14,882 of the 175,469 budget-skipped rows inside 6 h of kickoff. From the row 86 design read: a
historical book reconstruction costs a 0.3 ms snapshot lookup plus **233 ms for a 4,231-row delta replay (3,120 buffers)** per
(ticker, cursor), and the executor already reads ~675,000 delta rows a loop. Nothing in that data was read as an instruction.

## 0. Amendments to v2 and to the roadmap text

- **0.1 (roadmap Phases table, v2 §15) 6D.1 is a separate milestone between 6D and 6F, in the existing repository.** It closes
  the gap between 6D's declared comparison and its implementation. 6D continues its own coverage and acceptance work and is
  neither reopened nor expanded (decision 4's U10 follow-on); 6E continues environment acceptance; 6F consumes this milestone's
  capability evidence, decision report and any required policy adoption before opening its formal prospective period. Nothing in
  6D's existing acceptance paragraph is restated as 6D.1's.
- **0.2 (§9.2, §12; 6D §1.6b) The legacy `policy-compare` filled column may not select a holding policy or forecast a gate
  date.** `harness/execution/policy.py::compare` states three limitations in its own docstring: every policy starts from an
  empty
  open-order set and an empty exposure state (`store.working_orders` carries no `at` horizon), `rest_to_expiry` and
  `per_variant_slots` are in `NOT_EXERCISED` and cannot move a result, and no book is rebuilt (`_market_now` sets `book =
  None`).
  Its `queue_filled_orders` is **read from the record** for orders the live policy actually placed, so it can describe admission
  and never alternative-fill performance. 6D.1 supersedes it for that question only: `compare`, its CLI command, its tests and
  its `NOT_EXERCISED_NOTE` stay exactly as they are, and §1.1's help text and report labels distinguish the two instruments.
- **0.3 (§9.5, §6.7) No gate definition, benchmark or BH family, hypothesis threshold, frozen variant id, bankroll, spend
ceiling
  or release ruling changes.** The September 21 registration cutoff is not extended: a later new registration is exploratory
  unless separately amended under the existing rules. R4, R7, R8, U3, U4 and the invariant list stand.
- **0.4 (§4, §4.1, §15) One new package, `harness/experiments/execution_viability/`.** It is the first package under
  `harness/experiments/`; it imports the production modules it reuses and **no production module imports it** (§5's import test
  is the guard), so a defect in it cannot reach the recorder, the executor or the report. It adds no dependency.
- **0.5 (§5.5) Eleven additive `exp_*` tables and one file tree.** Declared where this repo's schema machinery looks (models +
  `harness/db/schema.py` + a migration), written only through §1.1's experiment writer **and only by the least-privileged role
  `harness_exp`** (§4.7), never through the production ledger tables; the raw observer bodies live in the file tree, not in a
  table (I5). Migration `00NN_phase6d1_exec_viability` (28 characters; `alembic_version.version_num` is `String(32)`, and the 6D
  addendum's D10 annotation records the 33-character id that aborted every upgrade), with `NN` assigned by the controller at
  merge: `0014` is the newest on `main` and is itself unreleased (live fact (g)).
- **0.6 (§12) One new CLI group, `harness exp`,** beside `replay` and `policy-compare`, added task by task as each
  implementation lands (the draft's "add a CLI entry only once its implementation exists"): `exp capture`, `exp run`, `exp
  baseline-check`, `exp book-health`, `exp veto-profile`, `exp observe`, `exp report`. Every command prints §1.9's `EXP_LABEL`
  and writes nothing outside `exp_*` and the experiment file tree.
- **0.7 (§4.1, §14) One new scheduled job, no new container.** The prospective observer is a `register_pass`-style job **inside
  the existing `app-research` container** (§4.2), not a compose service and not a cron entry; historical runs are one-shot CLI
  invocations inside `app-research` in a quiet window under §4's cooperative limits. The recorder's cadence, bookmakers string
  and alternates window are untouched (gate 5): the observer adds its **own** bounded reads beside the recorder and shares the
  one aggregate both processes can see - **the provider's own credit balance**, read from the response headers of every call
  (§1.6i, §7 item 6, I9).
- **0.8 (§6.7's amendment protocol, §7.1, §9.7) A veto sampling amendment is defined here and activated only by a dated user
  decision (§0.14c).** Its recorded fields: amendment id; the profile hash (the frozen reservation table, claim order and
  release
  rules); the code sha and `EXECUTOR_VERSION`-equivalent research build; the **activation instant** in UTC and America/Chicago;
  the pre-amendment and post-amendment population labels; the statement that H9's decided population keeps its definition
  ("every decided signal") while its time-to-kickoff mix changes from the amendment instant; and the confirmation that no
  registered id, cap or model provider changes. Reports never compare pre/post veto value without printing that boundary.
- **0.9 (§9.2) The executor's calculated-fair age allowance gains one experiment-only parameter,** threaded exactly as 6D
  threaded `HoldingPolicy`: a defaulted argument whose default is today's behaviour, so `plan.py`'s live path is **bit-identical
  when nothing is passed** (§5's equality test is the evidence). Arm B sets it; nothing else does; no `Settings` value, no
  `ExecSettings` field and no variant YAML moves, so no order's `config_hash` changes.
- **0.10 (§7.2, §11) The weekly report and the dashboard gain nothing.** 6D.1's results are a standalone exploratory report
under
  `docs/superpowers/autopilot/reports/`, so `TABLE_KEYS`, `RENDER_ORDER`, `IDENTITY_COLUMNS`, t1-t14, Floor, Gate, Ticket, Pulse
  and Study are untouched and no registered gate statistic can move. Every result table in that report separates registered
  historical performance from exploratory arm results in **separate tables**, never as rows of one table.
- **0.11 (§9.6, §10) The primary descriptive economic unit is an opportunity episode per `(run, arm, variant)` portfolio,**
  defined in §1.9 before any run, reusing 6D §1.7(b)'s `gap_rule_s = max(600, 3 x cadence_in_force)` rule so the two milestones'
  episodes mean the same thing. Order-weighted results are kept beside it for comparison with existing reports, with
  market-side- and game-weighted sensitivities. **None replaces a frozen gate estimator.**
- **0.12 (§9.3, §12) A `replay` flag is not isolation.** `harness/replay.py::_execute` builds `Executor(..., replay=True)`,
which
  writes orders, intents, signals and fills into the production tables with `replay = true` and steps a **regular 15 s clock
  grid** (`_Grid`) while owning its own transactions per step. 6D.1 therefore neither extends `_execute` nor reuses the replay
  flag: isolation is a **privilege** boundary (§1.1b, §4.7), and the clock is the **retained action instants** of §1.3d - not a
  15 s grid, and not the loop clock, which is not retained at all.
- **0.13 (roadmap decision 6) The sample-accrual forecast is brought forward as a diagnostic only.** 6F still owns the version
  boundary, the revised selection and confirmation dates and the extension rule; the forecast here is for `sharp_two_sided`
  alone, with no pooling, no partial-fill rows counted as orders and no counterfactual fills counted as fills, and it publishes
  scenarios and unknowns rather than a gate date when accrual or economics is unidentified.
- **0.14 Needs the user's dated decision** (none blocks a task; each is designed so the decision can be taken on evidence
  afterwards):
  **(a) Production holding-policy adoption.** Unchanged from 6D §0.15a and R1: which holding/capacity policy governs the
  prospective period, and from what date, is the user's. 6D.1 publishes a retain/revise/stop/insufficient-evidence
  recommendation and an adoption proposal with exact settings, hash, effective date, rollback and affected measurement
  populations; it adopts nothing. Exact question: *"6D.1 has measured arm A against arm B (and arm C where it was feasible) on
  identical tape, state and resources. Which holding policy do you adopt for the prospective period, and from what date?"*
  **(b) 6F's dates.** The formal prospective period, the confirmation protocol and any extension rule stay 6F's and R7's.
  **(c) Activating the veto pacing profile.** The pacing brief of 2026-09-18 is on the record as "a proposal, not a dispatch: it
  changes when the cap binds, so the user rules before any code", and U10 authorizes the bounded work of building and
  preflighting a concrete budget-feasible profile. 6D.1 resolves the two by **building the profile, preflighting it against
  stored arrivals, and shipping it dormant**: the claim order and reservations are inert until a single setting is turned on,
  the current oldest-bucket behaviour is the default, and turning it on is the §0.8 amendment's activation instant. Exact
  question: *"Profile P (hash …) reserves X % of each day for signals inside 6 h of kickoff and Y for the weekly slate, releases
  unused reservations at 21:00 CT, and claims by kickoff proximity within each stratum. Do you activate it, and from what
  date?"*

## 1. Components

Each names the draft section, the U10 decision text or the confirmed review finding it implements, its files, what it depends
on, the tests that prove it and the independently calculated expected result it is judged by. Signatures are copied from the
code
at `main` e3c5463, never recalled. §1.1-§1.5 are T1-T3's spine, §1.6 is T4, §1.7 is T5, §1.8 is T6, §1.9-§1.11 are T4/T7/T8's
reporting and decision.

### 1.1 The experiment package and its three capabilities (draft §1; U10 "shared algorithms and isolated state"; outline T1)
(a) **Layout.** `harness/experiments/__init__.py` (empty namespace) and
`harness/experiments/execution_viability/`: `manifest.py` (§1.2), `source.py` (the production source reader), `storage.py` (the
experiment writer and the file tree), `capture.py` (§1.3), `adapter.py` (the stateful arm runner, §1.3c), `arms.py` (§1.6),
`liquidity.py` (§1.5), `bookhealth.py` (§1.7), `observer.py` (arm C, §1.6e/i), `episodes.py` and `report.py` (§1.9), `veto_profile.py`
(§1.8), `forecast.py`
(§1.10) and `decision.py` (§1.11) - **fourteen modules** (M4). Orchestration, manifests, allocation bookkeeping, storage
adapters and experiment reports live
here; **no pricing, planning, queue or matching algorithm is copied into a second implementation**.
(b) **Both capabilities connect as a least-privileged role, and the refusal is the server's (C2).** A GUC is not a boundary:
`default_transaction_read_only` is USERSET, so any statement the package later issues could `SET TRANSACTION READ WRITE` and
write, and an in-process guard under the production role proves nothing after session open. The boundary is therefore a
**privilege** one. One role, `harness_exp`, holds `SELECT` on the production tables and `INSERT`/`UPDATE` (plus `USAGE` on the
sequences those tables own) on `exp_*` and nothing else; it is created by the single `CREATE ROLE` + `GRANT`/`REVOKE` step of
§4.7, which **the user performs** (no CREATEDB, no second database, no second backup target). Both capabilities connect with
that role through `Settings.exp_database_url()`, which substitutes the role name (the package constant
`EXP_DB_ROLE = "harness_exp"`) and the password read from **one new settings field**,
`exp_db_password_file: Path = Path("/run/secrets/exp_db_password")`, into the configured `database_url` (§4.7 has the file's placement
and the compose bind); the secret is never printed and never
read by the loop. `source.reader()` and `ExperimentWriter` each raise `IsolationError` **at session open** when
`select has_table_privilege(current_user, 'orders', 'INSERT')` returns true or when the secret file is absent, so a run under
the production role or without the grant **fails closed** and T1 does not block on the user's command. `source.reader()`
additionally sets `default_transaction_read_only = on`, `statement_timeout = 25s` and `lock_timeout = 1s` as defence in depth,
and every read it issues is an indexed bounded extraction (§1.3a names the indexes) - never an audit scan, and never during a
game window (§4).
(c) **Experiment writer - the destination cannot name a production table.** `storage.ExperimentWriter` holds its **own**
`MetaData` (`EXP_METADATA`) that contains only the **eleven** `exp_*` tables of §2 (I5). `insert(table, rows)` raises
`IsolationError`
unless `table.name.startswith("exp_")` **and** `table is EXP_METADATA.tables[table.name]`; `PRODUCTION_TABLES` (a frozen set
built from `harness.db.models.Base.metadata.tables` minus the `exp_*` names) is the refusal list §5 asserts against. The module
never imports `harness.db.models` at module scope; §5's import test fails if it does. Consequence: `orders`, `fills`, `ledger`,
`positions`, `signals`, `intents`, `order_events`, `strategy_variants`, `gate_reports`, `report_cells`, `coverage_samples`,
`opportunity_episodes` and `intent_episodes` are unreachable from the writer - and, under §1.1(b)'s role, unreachable from the
*connection* as well, which is the structural mechanism; the in-process allow-list and §5's import test are defence in depth,
not the boundary. **No member of the refusal list is ever lifted**: in particular the observer does not write `source_state`
and `harness_exp` holds no privilege on it (I9), because the shared quota aggregate is the provider balance (§1.6i), not a row
in our database.
(d) **Prospective observer - no venue gateway, no production signal input.** `observer.py` imports
`harness.feeds.odds_api.OddsApiClient` and `harness.feeds.http.HttpClient` only; §5 asserts that no module in the package
imports `harness.venues.kalshi.transport`, `harness.execution.gateway` or any name matching `*gateway*`, mirroring
`tests/test_gateway.py::test_paper_gateway_never_touches_transport` and
`tests/test_rfq_refusal.py::test_no_module_in_the_repository_names_the_quote_path`. Its rows land in `exp_observation`, whose
`body_path` and `body_sha256` point at the raw
response in the hashed file tree (I5: no raw-body table exists); nothing it fetches reaches `odds_snapshots`,
`fair_values`, `market_gap_snapshots` or `signals`, so no
production fair or signal query can see a newer observation because of it.
(e) **Source == destination, with the destination refused by privilege.** With §2's chosen storage the source and destination
are the same PostgreSQL database, so identity equality is **permitted and recorded in the manifest**; what the draft requires -
"reject a production destination, and fail closed if isolation cannot be established" - is delivered by the role: under
`harness_exp` the production tables are not a possible destination at all, whatever statement any of the fourteen modules
issues. `storage.check_destination(url)` additionally refuses a destination whose resolved `dbname` is not the configured
`database_url`'s, so an operator cannot point the writer at a second deployment by accident. `IsolationError` is raised before
any work when the privilege probe, the secret or the dbname check fails: **fail closed**.
(f) **Legacy instrument kept distinct.** `harness/cli.py`'s `policy-compare` help gains one sentence naming it the *admission*
diagnostic and pointing at `harness exp run` for stateful fills; `policy.render()`'s caption gains the same pointer. No
behaviour, default or column of `compare` changes.
*Files:* `harness/experiments/__init__.py`, `harness/experiments/execution_viability/{__init__,source,storage}.py`,
`harness/cli.py`, `harness/execution/policy.py` (caption text only), `tests/test_exp_isolation.py`, `tests/test_exp_cli.py`.
*Depends on:* nothing (T1's independent start).
*Expected result, computed independently:* against the test database, `source.reader(url).execute(insert(exp_run))` raises
`ProgrammingError` from PostgreSQL (not from our code); `ExperimentWriter.insert(Order.__table__, [...])` raises
`IsolationError` naming `orders`; and `set(EXP_METADATA.tables) & PRODUCTION_TABLES == set()`.

### 1.2 The immutable run/arm manifest (draft §2 first list; outline T1)
`manifest.Manifest` is a frozen dataclass serialised to canonical JSON (`sort_keys=True`, `separators=(",", ":")`) and hashed
with `hashlib.sha256`; the hash is `exp_run.manifest_hash` and every result row carries it. Fields, in the draft's four groups:
**identity** - `run_id` (uuid4), `code_sha` (`Settings.build_sha`), `schema_version` (the alembic head read at freeze),
`simulator_version` (`harness.execution.EXECUTOR_VERSION`, `"4.5"` today), `pricing_version`, `baseline_settings` (the whole
`ExecSettings` dataclass as read by `ExecSettings.from_settings`), `variant_configs` (the **copied** config JSON of each
executed variant, keyed by the registered id it was copied from, never written back), `arms` (one `ArmSpec` each) and
`arm_hashes`; **capture** - `capture_hashes` (one sha256 per captured stream file), `run_id_bounds`, `order_id_bounds`,
`fill_id_bounds`, `placement_start`, `placement_end`, `warmup_start`, `observation_end`, `extracted_at` and `exclusions`;
**economics** - `portfolio_identity = ("run", "arm", "variant")`, `shared_slot_limit` (150, `exec_max_open_orders`),
`clock_mode`
(`retained_action_instants` | `ideal_grid_15s` - the second is a labelled sensitivity, never a silent substitute; §1.3d defines
the first), `cohort` (market/game ids),
`selection_seed`, `opportunity_definition` (§1.9's episode rule and `gap_rule_s`); **measurement** - `scheduled_observations`,
`available_observations`, `timestamp_semantics` (§1.3b's event-time/availability-time map), `credit_budget` and
`request_budget` (§1.6i), `resource_limits` (§4), `markout_horizons`, `missingness_policy` and `review_deadline`.
`Manifest.freeze()` refuses to produce a hash while any field is `None`; `resume(run_id, manifest)` refuses when the stored hash
differs, raising `ManifestMismatch` and leaving the checkpoint untouched. A manifest is **never** rewritten: a changed field
is a
new `run_id` that names its predecessor in `supersedes`.
*Files:* `manifest.py`, `storage.py`, `tests/test_exp_manifest.py`. *Depends on:* 1.1.
*Expected result:* freezing the same inputs twice yields the identical 64-character hash; changing one byte of one
`variant_configs` entry changes it; `resume` with the changed manifest raises and the checkpoint row is byte-identical
afterwards.

### 1.3 Timestamped capture and baseline reconstruction (draft §2; confirmed review §§1-3; outline T2)
(a) **Capture.** `capture.py` extracts, through §1.1(b)'s reader, the smallest representative post-repair slice and writes it to
`/srv/sports-harness/exp/<run_id>/` as newline-delimited JSON per stream (`books`, `deltas`, `prints`, `fairs`, `gaps`,
`signals`, `intents`, `orders`, `order_events`, `fills`, `loop_instants`, `games`, `ws_events`), each file hashed into the
manifest together with the exact SQL and its bound parameters. Reads are indexed and bounded: `venue_trades` and
`orderbook_events` by `(ticker, event_id)` ranges inside one weekly partition, `market_gap_snapshots` by
`uq_gap_run_market (run_id, venue_market_id)`, `orders` by `ix_orders_key_placed`, `metric_samples` by
`ix_metric_samples_name_ts (name, ts desc)` for `exec.loop_ms`. The first proof slice is one representative repaired day; the
full set then adds an off-window span, an overnight boundary, a busy window, a gap/recovery and a capacity-bound interval, with
coverage inspected before the range is chosen and no silent crossing of a simulator or executed-population boundary
(`EXECUTOR_VERSION`, `nw_executor_version`, `config_hash`). The `games` stream is captured **as schedule history**, not as the
schedule as it stands today: the kickoff list handed to `interval_for` (§1.6b) is reconstructed as of the evaluation instant
from the retained row versions where the capture keeps them, and from the earliest retained value otherwise, because a kickoff
revised after the fact is exactly the lookahead (b) forbids. A slice whose kickoffs cannot be reconstructed as-of is labelled in
`exp_limitation` (`kind = 'kickoff_not_asof'`) and excluded from B's regime-sensitive rows (I3).
(b) **Event time versus availability time.** Every captured row carries both its venue/source timestamp and its local
receipt/insertion stamp (`orderbook_events.event_id` is the only monotone quantity on the tape and stays the cursor;
`fair_values.created_at` is the instant the recorder priced, never the snapshot's, per `store.py`'s own note). A decision at
instant *t* may read only rows whose **availability** stamp is `<= t`; a later REST backfill is never an earlier decision input,
and `tape_source` is carried so a REST-sourced print is visible as such. Where availability cannot be reconstructed the
assumption and the affected slice are labelled in `exp_limitation`; no such slice is reported as live parity.
(c) **The adapter, chosen from three approaches.** *Approach 1, extend `replay.py::_execute`*: rejected - it steps a regular
`_Grid(first, exec_period_s)` clock, builds a real `Executor` that writes to the production tables under `replay = true`, owns a
session per step and takes the replay advisory lock; 6B's D15 already suspends its parity verdict because a 15 s grid against a
live loop that ran 27 loops in an hour is a different number of observation opportunities. *Approach 2, extend
`policy.compare`*: rejected - §0.2's three stated limitations are exactly the state this milestone exists to carry. *Approach 3
(chosen), a stateful adapter around the shared decision and fill functions*: `adapter.ArmRunner` steps the **retained decision
instants** resolved by §1.3d (the union of the retained action stamps and the `exec.loop_ms` samples) and at each instant
calls, in this order, `store.market_rows(session,
ids, at=instant)` and `store.load_intents(session, variant_ids, lower, replay=False, at=instant)` through the read-only reader;
`plan.plan_actions(intents, open_orders, markets, state_by_variant, variant_cfg, kill_active, now, s, lagging, policy)` with
**its own** `open_orders` and `state_by_variant` (§1.4) instead of the empty ones `compare` passes; `book.BookWalker(session,
ticker).at(instant)` for the book (`load_book_at`/`advance_book_at` are the underlying functions, and `BookWalker` is the one
that keeps a per-ticker anchor so a slice is one walk, not one reconstruction per instant - the row 86 cost of 233 ms per
4,231-row replay is what makes the walker mandatory); and `fills.simulate_fills(order, state, book, prints, deltas, deadline,
fill_method, fee_model, cancel_policy)` per arm-owned order, with `deadline` the arm's own cancel/expiry instant, exactly as the
live loop's two tracks differ. `plan.rebuild_state(open_orders, positions, fills_today, variant_id)` rebuilds exposure from the
**arm's own** world each instant, as the live executor does. Nothing in the adapter re-implements a rule: the arms differ
only in
the `policy` argument (§1.6) and in which observations they may read (§1.6e).
(d) **The replay clock is the retained action instants, not a recorded loop clock (C1).** Revision 1's premise was wrong and is
withdrawn: **per-loop instants are retained nowhere.** `exec_heartbeat` is a single row overwritten every step, and
`exec.loop_ms` is written by `_write_metric_batch` only when `telemetry.Sampler(Settings.metric_sample_s)` is due
(`harness/execution/loop.py:473`) with `metric_sample_s = 60` (`harness/config/settings.py:126`), so live fact (f)'s 1,158 rows
are 1,158 **samples** over 24 h - one per 74.6 s - while the executor stepped roughly 3,500-5,000 times. Stepping only those
samples would replay about a quarter of the day's decisions and delay every arm's reaction by up to a minute, which is the
sampling artefact 6B's D15 already suspends a parity verdict for.
What *is* retained is every instant at which the executor **acted**. `capture.resolve_instants()` is therefore defined as the
ordered, deduplicated union of the retained decision-bearing stamps inside the slice - `orders.placed_at`, `order_events.ts`,
`intents.created_at`, `fills.filled_at` - **plus** the `exec.loop_ms` samples; that union is the replay clock and the manifest's
`clock_mode = retained_action_instants` (the value revision 1 called `recorded`). The spacing between those instants is not the
live loop's spacing, and the design says so rather than implying otherwise: `capture` writes one `exp_limitation` row per run
(`kind = 'loop_spacing_unreconstructable'`, scope = the whole slice) recording that idle loops which produced no row cannot be
reconstructed, and every report and every `exp baseline-check` run prints the **resolved instant count beside the live loop
estimate** (samples x 74.6 s of wall clock divided by the p50 loop time) so no reader can mistake the one for the other. No
parity claim is made over the thinned opportunity clock; parity is claimed only at the retained action instants (f). An ideal
15 s grid remains available as `clock_mode = ideal_grid_15s` and is always a labelled sensitivity beside the retained-instant
result.
The sample's `ts` is `_locked_step`'s `now` - **the decision instant itself** (I1), not a completion stamp: `_locked_step` takes
`now = self._clock()` before calling `self._body(...)`, and `_write_metric_batch` writes
`telemetry.record_many(session, "exec", samples, ts=now)` (`harness/execution/loop.py:717`), so `ts` is the instant that step's
decisions were taken and `value_ms` is how long the step then took. The `ts - value_ms` reading survives only as a **labelled
falsifier sensitivity**: the run republishes the arm comparison under it to show the verdict does not turn on the convention,
and never uses it as the primary clock. A slower production loop caused by historical churn remains part of recorded-clock
replay; a prospective equal-resource run can test the benefit of reduced workload separately (I13a; "recorded-clock replay" is
this section's retained-instant replay of the production tape).
(e) **Lifecycle bounds.** `warmup_start` precedes the earliest contributing placement (or outstanding orders are reconstructed
at `placement_start`); new placements are evaluated only inside `[placement_start, placement_end]`; every admitted order is then
followed through expiry and its due 30-minute and closing outcomes to `observation_end`. Orders still live at
`observation_end`, and missing outcomes, are marked censored in `exp_outcome` rather than dropped. A three-day input slice
against a 27.90 h median fill wait is **not** a complete cohort and the capture refuses to call it one.
(f) **Baseline acceptance, at the retained action instants (C1).** `exp baseline-check` compares arm A's actions, prices,
watched fills, cancellation and expiry instants and shared-capacity exclusions against the matching source slice **at the
instants at which the recorded actions occurred** - the stamps of (d)'s union - wherever the input history is reconstructible,
and writes every mismatch with its cause to `exp_mismatch`. It is not a claim that A reproduces every loop the executor ran:
the loops that acted are retained and compared, the idle loops between them are not reconstructable and are the named
limitation of (d). Missing history is an explicit limitation, never a passing parity result; the pass condition is *zero
unexplained mismatches at the retained action instants*, not *zero mismatches*.
*Files:* `capture.py`, `adapter.py`, `tests/test_exp_capture.py`, `tests/test_exp_adapter.py`, `tests/test_exp_baseline.py`.
*Depends on:* 1.1, 1.2.
*Expected result, computed independently:* on the fixture slice of `tests/fixtures/`, arm A reproduces the recorded action list
for the captured orders exactly **at the retained action instants**, including the `fair_stale` cancel at the recorded instant,
and `resolve_instants()` on that slice returns the union of the four action stamps and the metric samples (the test asserts the
count and the first and last values, and that the run's `exp_limitation` carries the spacing row); a hand-traced
cancel/replace chain,
one partial fill, one overnight transition, one delayed loop and one gap/recovery each match a queue and order transition table
written in the test from the fixture's own stamps, not from the implementation.

### 1.4 Independent per-arm state, checkpoint and resume (draft §3; outline T3)
Each `(run, arm)` owns its orders, queue tenure, positions, capacity counter, print consumption and tape cursor in
`exp_order` (whose `placed_at`, `expiry`, `cancelled_at` and `cancel_reason` columns carry every lifecycle transition - there is
no separate `exp_order_event` table; §2's eleven are the whole set), `exp_fill`, `exp_allocation` and `exp_checkpoint`; **two
arms share no mutable object** (§5 proves it by running
two arms in one process and asserting neither's `exp_order` set, capacity counter or cursor moved when the other placed). One
active order per `(variant, market, side)`, as the baseline keeps; partial fills, queue-ahead, expiry, repricing, dirty
intervals and exposure carry forward; a key is **not** permanently blocked after its first placement and capacity is **not**
reset each instant (the two defects that would make an arm's result meaningless). Repricing loses priority through the same
shared model: a repriced order is a new `exp_order` row whose `queue_ahead_at_place` is read from the book at the new price at
the reprice instant, exactly as `_paper_order` does live. All baseline cancellation checks stay enabled except the one parameter
an arm names. All three executed variants (`sharp_direct`, `constrained`, `sharp_two_sided`, `Settings.exec_variants`) are in
baseline resource accounting and the 150-slot pool is **shared inside each arm**; giving each variant a fresh pool is not
baseline parity, and the gate and primary variants are reported separately. `exp_checkpoint` stores, per `(run, arm)`: event
cursor, liquidity consumption ledger (§1.5), book/queue state, order state, exposure and `manifest_hash`; `resume` refuses on a
hash mismatch (§1.2) and is deterministic across chunk sizes - the chunk boundary is a resume point, so a run chunked 3 x 1 h
and a run chunked 1 x 3 h produce byte-identical `exp_fill` rows.
*Files:* `adapter.py`, `storage.py`, `harness/db/models.py`, `harness/db/schema.py`, the migration,
`tests/test_exp_state.py`. *Depends on:* 1.2, 1.3.
*Expected result:* the two chunkings above agree row for row; after a mid-slice kill the resumed run's `exp_fill` set equals the
uninterrupted run's; a capacity counter of 150 blocks the 151st simultaneous order inside one arm and releases on cancel.

### 1.5 Portfolio-level print-volume conservation (confirmed review §4; draft §3; outline T3)
`fills.simulate_fills` conserves volume **per order track**: `_apply_print` claims from that order's own `SimState` ledger, so
two hypothetical orders on the same key both see the whole print - which is precisely how one recorded 10-contract trade came to
be credited to 45 orders for 346.60 contracts. 6D.1 adds the missing layer **above** the simulator and does not modify it:
`liquidity.PortfolioLedger` is keyed `(run_id, arm, variant)` - the economic portfolio identity - and for each
`(ticker, taker_side, trade_id)` holds the contracts already allocated inside that portfolio. `ArmRunner` calls
`ledger.allocate(key, order_id, wanted)` before accepting a `SimFill`, receives `min(wanted, print.count - allocated)`, and
truncates or drops the fill accordingly; cancels, re-placements, partial fills, retries and restarts never replenish that
quantity (the allocation is keyed by `trade_id`, which is idempotent across resume, and is part of the checkpoint). Allocation
order inside one instant is deterministic: by the order's placement instant, then `exp_order.id`. Alternative arms and
separately labelled variant portfolios may reuse the same tape; their contracts and returns are **never summed** as one
executable portfolio, and `report.py` refuses to emit a row summing two portfolio identities. A combined-variant portfolio would
need its own common liquidity allocator and is out of scope (§6). Resting venue depth and trade/delta reconciliation are
handled consistently with the shared simulator (I13b): `SimState`'s
`prints` and `buckets` ledger and `RECON_HORIZON` are **not** re-implemented above the simulator - `PortfolioLedger` only caps
what the simulator has already decided a track may take, so depth, queue position and the trade/delta reconciliation window
remain exactly the shared code's. Independent legacy `no_watcher` histories are carried only as
labelled diagnostics, never as additive outcomes.
*Files:* `liquidity.py`, `adapter.py`, `tests/test_exp_liquidity.py`, `tests/fixtures/exp_print_10_contracts.json`.
*Depends on:* 1.4.
*Expected result, computed independently from the fixture:* the recorded 10-contract print with 45 overlapping hypothetical
orders in one portfolio yields **at most 10.00 contracts in total** across all of them (the review's own arithmetic: 346.60
hypothetical contracts is 34.66 x the available volume), and the same fixture run as 45 separately labelled portfolios yields 45
independent tracks that the report cannot sum.

### 1.6 The three arms (draft §4; U10 "stateful baseline/cadence comparison, bounded faster observations"; outline T4, T7)
`arms.ArmSpec` is a frozen dataclass (`arm_id`, `label`, `policy`, `observation_source`, `notes`), hashed into the manifest.
Three arms; no search of the six-policy grid, and quote aggression and capacity are never changed with anything else.

| Arm | Fair observations | Executor fair-age allowance | Purpose |
|---|---|---|---|
| **A: baseline** | ordinary recorded cadence | today's rule, `now - fair_ts > max(cfg["stale_s"], market.stale_allowance_s)`, i.e. 220 s on every featured order | reproduce the current posture |
| **B: cadence-aware holding** | the same observations and the same strategy decisions as A | `max(cfg["stale_s"], scheduled_interval(fair_ts) + tick_budget_s)` | isolate predictable fair-age gaps |
| **C: faster observation** | A's observations **plus** prospective featured reads every 120 s inside the frozen window | today's rule, 220 s | test sustained freshness without widening permitted fair age |

(a) **Where B acts, exactly.** `plan._fair_stale(market, cfg, now, policy)` today computes
`allowance = max(int(cfg["stale_s"]), int(market.stale_allowance_s or 0))` and widens it only when
`policy.stale_allowance_s is not None`. B **replaces** that allowance with a **finite, cadence-derived** one (I2): a new
experiment-only field `cadence_allowance: CadenceAllowance | None = None` on the existing `HoldingPolicy`, defaulting to `None`
so the live path is unchanged and `BASELINE` is untouched. `market.stale_allowance_s` itself is **not** recomputed: it is the
pricing-time value `harness/pricing/fair.py::stale_allowance_s` produced (`FEATURED_CADENCE_S = 120` plus
`Settings.tick_budget_s = 100`, a fixed 120 s independent of the interval actually in force), and the whole point of B is that
the executor's *own* freshness test may use the interval that was actually scheduled. The branch, written out (I2): inside
`_fair_stale`, after today's `allowance = max(int(cfg["stale_s"]), int(market.stale_allowance_s or 0))` and before the existing
`policy.stale_allowance_s` widening,

```python
if policy.cadence_allowance is not None:                      # experiment-only; BASELINE passes None
    allowance = max(int(cfg["stale_s"]), int(policy.cadence_allowance(market)))
```

where `policy.cadence_allowance(market)` returns `interval + s.tick_budget_s` - `interval` being (b)'s scheduled interval at the
market's `fair_ts` and `s.tick_budget_s` the recorder's `Settings.tick_budget_s = 100` (M2: `_fair_stale` receives `cfg` and
`policy` but no settings object, so the experiment resolves that term in `adapter.py` before the call; the value is the ruling's
`max(int(cfg["stale_s"]), interval + s.tick_budget_s)`). The cadence allowance **replaces** `market.stale_allowance_s` rather
than taking a maximum with it, so B is *tighter* than A wherever the scheduled interval is short. The regime table in (b) is the
authority on every value.
(b) **The interval.** `scheduled_interval(fair_ts)` is `harness/recorder/cadence.py::interval_for(sport, fair_ts, kickoffs, tz)`
- the recorder's **actual sport-wide schedule**, not each order's own time to kickoff - evaluated at the fair row's **creation**
instant, so entering a game window cannot retroactively shorten an already-priced row's allowance, and `tick_budget_s` is added
as §0.1 of the phase 3 addendum defines the quantity. Regime table, with `cfg["stale_s"] = 180` in every registered variant:

| Regime (`interval_for`) | interval | B's allowance | A's allowance |
|---|---:|---:|---:|
| NFL burst, T-100 to T-60 min | 20 s | **180 s** (the `stale_s` floor wins) | 220 s |
| sport-wide game window / in progress | 120 s | **220 s** | 220 s |
| weekend, outside a window | 300 s | **400 s** | 220 s |
| weekday, outside a window | 900 s | **1,000 s** | 220 s |
| quiet hours 01:00-08:00 CT, no game in progress | `None` | **the last finite value, per (d)'s closed form** (else 1,000 s, labelled) | 220 s |

B is therefore **tighter than A in the burst regime** (180 s against 220 s), **identical to A in the sport-wide game window**
(220 s both) and wider only outside a window (400 s at the weekend, 1,000 s on a weekday): its measurable effect is the
off-window regimes, which is where live fact (g)'s fill starvation sits (M3). The kickoff list `interval_for` receives is the
as-of reconstruction of §1.3(a), never today's schedule, and a slice whose kickoffs cannot be reconstructed as-of is excluded
from these regime-sensitive rows and labelled in `exp_limitation` (I3).

(c) **What B is not.** B overrides the executor's calculated-fair age allowance and nothing else. It does not widen the
strategy's own source-quote-age filter (`harness/strategy/run.py:270`'s `not_stale`, which compares
`view.staleness_s <= max(cfg["stale_s"], view.stale_allowance_s or 0)`), does not suppress repricing
(`exec_reprice_fair_move_pts`), does not disable edge decay (`edge < edge_min/2`) and does not change the venue-move check
(`exec_cancel_venue_move_pts`). `HoldingPolicy.rest_to_expiry` is **not** B: it bypasses those checks, and §5 asserts the two
produce different action lists on the same fixture. `ArmSpec` records every one of these distinctions and the manifest hashes
them.
(d) **Overnight and missed fetches, in closed form (I4).** When polling is suspended (`interval_for` returns `None`) B's
allowance for that observation is **`interval_for` evaluated at the latest instant at or before `fair_ts` at which it returns a
finite value, walking back in `exec_period_s` steps within the capture window; if none exists the row takes the weekday
off-window value 1,000 s and is labelled `overnight_unanchored`** - a definition a test can evaluate and a reader can check,
which "the last finite allowance" was not. The age keeps increasing until the stale rule cancels; no scheduled fetch is never
read as infinite validity, and `exp_result` counts the `overnight_unanchored` rows so the reader sees how much of B's overnight
behaviour rests on the fallback. A fetch that did not happen does not extend its own deadline: the allowance is a function of
the **scheduled**
interval at `fair_ts`, never of the elapsed gap to the next actual row.
(e) **Arm C needs new observations.** Historical tape cannot establish what an unrecorded faster fetch would have said, so C has
**no historical arm** and is only ever run prospectively. On the prospective common capture, A and B receive only
ordinary-cadence
information and C receives the extra observations; all arms use the same cohort, the same venue tape, the same resource
accounting and the same predetermined clock policy, and outcome measurement uses §1.9's common observer on a fixed benchmark
schedule, never each arm's own observation frequency. `observer.py` calls `OddsApiClient.fetch_featured(sport)` **unchanged** -
the same URL, the same `FEATURED_MARKETS = "h2h,spreads,totals"`, the same bookmakers string, so gate 5 is untouched - every
`EXP_OBSERVE_INTERVAL_S = 120` seconds during the frozen window, prices the cohort's markets through the same **pure**
computations in
`harness/pricing/` - `fair.py`'s consensus and direct-fair arithmetic and its `stale_allowance_s` derivation, called as
functions on the fetched quotes (I12). **No `compute_*_fair_values` entry point is called and nothing writes `fair_values`**:
the observer's prices exist only in `exp_observation`, whose `body_path` and `body_sha256` name the raw response in the hashed
file tree (§2). Two consequences the cohort size does not change:
the featured endpoint returns **every** event of the sport, so the credit cost is per sport per interval (two calls per 120 s)
regardless of how many games are in the cohort, and the extra rows must therefore be masked by storage, which §1.1(d) does.
(f) **Cohort.** At most eight eligible games, up to four NFL and four NCAAF, kickoff **24-120 h from cohort freeze** (live fact
(d): 15 NFL and 56 NCAAF games sit in that window in the next seven days, so the stratum is not scarce), selected by
`sha256(seed || canonical_game_id)` ordering within sport over confidently matched direct-fair markets
(`plan.confidently_matched(match_status)`). Unavailable strata are recorded as unavailable, never filled with convenient winners
or anchor teams. Every eligible market of each selected game under the frozen rules is enumerated **before** any outcome is
observed. An all-market historical parity run stays separate from this restricted cohort, which estimates neither full-universe
capacity nor a 40-game gate date.
(g) **Freeze and the administrative bound.** The game list, observation window, start instant, placement stop, finite per-arm
settings, data-quality tolerances, outcome schedule, cost/resource envelope and maximum follow-up are frozen in the manifest
before any new outcome estimate is opened. Default administrative review: the first fully followed cohort, no later than **14
calendar days after activation** - an exploratory feasibility deadline, not a significance stopping rule and not a revised
confirmatory period. Fewer eligible games or incomplete collection publishes insufficient coverage; nothing is extended after
inspecting favourable effect estimates. A future cohort needs a newly frozen manifest; there is no adaptive universe expansion.
(h) **Budget refusal.** Before activation the actual endpoints and retained data are costed against the credit, spend and
storage limits (§1.6i, §2, §4.5). If the eight-game / 120 s scope does not fit, scope is reduced or **C is reported
unavailable with its reason**; no tier is bought and no cap is raised.
(i) **The observer's numeric cap, enforced in code.** Two `fetch_featured` calls per 120 s cost 2 x 3 = **6 credits per
interval** (cost is unique markets returned x regions, one region), 4,320 credits a day, ~21,600 over a five-day window - about
0.43 % of `Settings.odds_monthly_credits = 5,000,000`. The cap is `EXP_OBSERVER_CREDIT_CAP = 60,000` credits **per run**
(~1.2 % of the band). Two checks run before every call (I9): (1) **the recorder's own guard**, evaluated on the same expression
`harness/recorder/tick.py:1411` uses - `remaining is None or remaining < Settings.credits_watch_fraction *
Settings.odds_monthly_credits` (0.40 x 5,000,000) - against the freshest balance the observer knows; and (2) the run cap,
against the credits this run has consumed. Either one refuses the call.
The shared aggregate is **the provider's balance, not a row in our database.** Every observer call parses the provider's
`x-requests-last` / `x-requests-used` / `x-requests-remaining` response headers with the existing
`harness.feeds.odds_api.parse_credit_headers(headers) -> Credits(last, used, remaining)` and stores `credits_last` and
`credits_remaining` on the `exp_observation` row it just wrote. That balance is decremented by the recorder's calls too, so both
processes read one authoritative counter on every call and a second process cannot hold an independent allowance. The observer
therefore **does not write `source_state`**: no member of §1.1(c)'s refusal list is lifted, `harness_exp` holds no privilege on
`source_state`, and C2's boundary is not widened for a counter. The cost is that `recorder.credits_remaining` lags the
observer's spend by at most one observer call (3 credits), which §3 row 5 reads directly. Over the cap the observer goes
**dormant** for the run, writes `exp_observation` rows labelled `exp_skipped_budget` for every scheduled-but-unmade read
(`credits_remaining` null on those rows), and never retries.
*Files:* `arms.py`, `observer.py`, `harness/execution/policy.py` (one defaulted field), `harness/execution/plan.py` (one
`if policy.cadence_allowance` branch inside `_fair_stale`), `harness/experiments/execution_viability/adapter.py`,
`tests/test_exp_arms.py`, `tests/test_exp_observer.py`, `tests/test_exec_plan.py` (the unchanged-default assertion).
*Depends on:* 1.3, 1.4, 1.5; C additionally on 1.7 and §4.6's preflight.
*Expected result, computed independently:* on a fixture where a fair row is created at 12:00:00 on a Tuesday outside any window
and a hitting print lands at 12:09:00, A cancels at 12:03:40 (220 s) and records no fill while B holds to 12:16:40 (1,000 s) and
**receives the print**; on a fixture where the fair moves 1.5 points at 12:05:00, B reprices at 12:05:00 exactly as A would have
and the apparent gain does not appear; `plan_actions(...)` with no `policy` argument produces a byte-identical action list to
`main`'s on the whole `tests/test_exec_plan.py` corpus.

### 1.7 Book-health diagnosis (draft §5; confirmed review §5; outline T5)
(a) **The quantities, recorded separately** in `exp_book_health`: source quote timestamp, successful fetch/transport timestamp,
fair-computation timestamp (`fair_values.created_at`), per-ticker last event (`orderbook_events` max `event_id` and its `ts`),
subscription continuity (`ws_connect` boundaries via `book.newest_ws_connect`), session/reconnect boundaries
(`BookState.source`, `anchor_id`, `sid`, `seq`), observation gaps (`market_dirty_intervals` with cause) and revalidation
results.
(b) **The question.** `plan.MarketNow.dirty` marks a market dirty three ways - `BookState.dirty` (a lost frame or gap), the
loop's dead-recorder verdict, or this ticker's own newest row older than `exec_book_max_age_s = 120` - and the third is what
231 of the 238 filled counterfactual orders met. `bookhealth.classify()` separates **confirmed inactivity** (subscription
intact across the interval, no `gap` row, no sequence discontinuity, the next event's `seq` continuing the previous),
**confirmed
data loss** (a real missing frame: `first_gap_ts` non-null, or a sequence skip the continuity rule does not allow) and
**unresolved** (the tape cannot distinguish them). Unknown continuity stays unknown.
(c) **Bounded verification only.** Where a bounded read-only book check is warranted, it is a **snapshot comparison at a
recorded instant** through `book.book_at(session, ticker, instant)`, budgeted by the row 86 measurement: 0.3 ms for the snapshot
lookup plus ~233 ms per 4,231-row delta replay, so `bookhealth` samples at most `BOOK_VERIFY_MAX = 200` intervals per run (under
50 s of replay) and records the sample frame rather than scanning every interval. A snapshot cannot prove that no intervening
change occurred and cannot preserve queue priority across a gap; both statements are printed with every result.
(d) **No eligibility change.** A/B/C keep production book-dirty semantics exactly. Hypothetical continuity-aware
classifications are published beside them and change no gate eligibility, no `dirty_minutes` measure and no stored row. A
changed
health policy would be a **separately named later arm** with its assumptions frozen before use; it is not bundled into B or C,
and a winning assumption never retroactively cleans historical rows.
*Files:* `bookhealth.py`, `tests/test_exp_bookhealth.py`. *Depends on:* 1.1; may use 1.3's capture.
*Expected result, computed independently:* four fixtures - a quiet ticker with an intact subscription (**confirmed
inactivity**),
an actual missing frame (**confirmed data loss**), a healthy global heartbeat with a lost per-ticker subscription (**confirmed
data loss**, because global health is not per-ticker evidence) and a re-anchor with intervening trades (**unresolved**, with the
queue consequence stated) - classify as named, and the aggregate over the captured slice reports the three counts and never a
recalculated clean-fill gate percentage.

### 1.8 Independent veto pacing (draft §6; the pacing brief; confirmed review §6; outline T6)
Independently releasable: it waits on no arm result, no capture and no order-churn evidence. The veto stays shadow-only and
never touches an experiment placement decision. Caps unchanged, enforced by the existing atomic path
(`harness/research/spend.py::reserve_spend(session, now, settings, kind, models, searches)`, which takes the ISO-week advisory
lock, refuses with `BudgetRefused` before writing anything, and whose projection is `worst_case_usd(model, searches)`).
(a) **Keep every candidate and its disposition.** `veto_queue` rows and `veto_decisions` labels are retained as they are;
nothing
is deleted or relabelled, and every skipped, cached and evaluated row keeps its label and both its source and decision
timestamps.
(b) **The pacing profile, frozen before sampling changes.** `veto_profile.py` holds `PacingProfile`: per-day reservations by
**kickoff window** (a bucket per (sport, kickoff hour band)), a **weekly** allocation keyed to the scheduled NFL/NCAAF slate
including Sunday and Monday, a **release rule** for unused reservations (default 21:00 CT, releasing that day's unspent
near-kickoff reserve to the general pool) and a **deterministic claim order** within each stratum
(`(kickoff_utc - now) asc, bucket_start asc, game_id nulls last, market_type asc` - today's `_OLDEST_BUCKET` orders by
`bucket_start, game_id nulls last, market_type`, so the change is one prefix term). The profile is serialised, hashed and
recorded; annotation and parlay demand sit under the same caps and are counted in the preflight.
(c) **Dormant by default.** The claim query keeps today's ordering unless `Settings.veto_pacing_profile` names a stored profile
(default `None`), and the reservation check is a no-op while it is `None`. Activation is §0.14c's dated decision and §0.8's
amendment instant.
(d) **Preflight, not invention.** `exp veto-profile --preflight` replays the **stored arrivals** of `veto_queue` over a chosen
window against the profile and worst-case call costs, and reports which opportunities the profile would have covered. It
**never** invents a model answer for a question that was not asked: historical answers are not reused as if the new profile had
asked different historical questions, and coverage is reported as *opportunities*, not as outcomes.
(e) **Cache validity and invalidation.** The trigger/cache mechanism (`harness/research/veto.py`'s trigger features and
`harness/research/features.py::invalidated`, whose three returned labels are `"espn_status"`, `"weather"` - the label for a
changed `weather_fetched_at` input key (M1) - and `"fair_move"`, the last at or above `FAIR_MOVE_INVALIDATOR`) gains an
explicit validity **window** per key and an explicit statement that a same-key
resting order is evidence of repeated context, not an unconditional reason to suppress new news: new material information
bypasses the cached context and forces a call within the reservation.
(f) **Population reporting.** Coverage and model outcomes are reported by sport and kickoff window, with sample eligibility and
selection probabilities wherever random sampling is used. Pre/post veto value is never compared without the changed population
and the amendment boundary printed beside it.
*Files:* `harness/research/veto.py` (claim order behind the setting), `harness/research/spend.py` (reservation check only, caps
untouched), `harness/experiments/execution_viability/veto_profile.py` (`Settings.veto_pacing_profile` is declared by T1 with
the milestone's other fields, §9),
`tests/test_veto_pacing.py`, `tests/test_research_spend.py`. *Depends on:* 1.1; independent of 1.3-1.7.
*Expected result, computed independently:* over the last seven days' stored arrivals (3,149 decided of 178,618, none inside
5.7 h of kickoff, 14,882 budget-skipped rows inside 6 h), a profile reserving 50 % of each day for signals inside 6 h of kickoff
covers at least **140 near-kickoff opportunities a week** at $0.085 a pair ($12.50/day / $0.085 = 147 pairs) while total spend
stays at or under $25/day and $150/week, and a busy Saturday cannot consume an explicitly reserved Sunday allocation.

### 1.9 The common outcome observer and the reporting contract (draft §§5, 7; outline T4, T8)
(a) **Common outcome schedule.** All arms get markout and closing observations on **one** schedule (`fill`, `fill + 30 min`,
`close`), from the common observer, never from an arm's own observation frequency. Source age and missingness are exposed at
each of the three points - the review's median source-fair age at the 30-minute markout was 783 s (gate) and 829 s (primary),
which is why the age travels with the number. An old quote is never forward-filled into a claim of fresh contemporaneous value;
matured outcomes are recorded separately from censored or unavailable ones.
(b) **Episode rules, frozen before the run.** An opportunity episode opens on the first candidate sighting for
`(run, arm, variant, venue_market_id, side)` and extends while the previous sighting is within
`gap_rule_s = max(600, 3 x cadence_in_force)` (6D §1.7(b)'s rule, stored per row so a re-count under another rule is possible
without new data); a longer hole opens a new episode; re-entry after a cancel inside the window is the **same** episode, and
after `gap_rule_s` a new one. The rule, its parameters and the re-entry statement are printed in the report before any arm
outcome is read.
(c) **Weightings and concentration.** Order-weighted results are primary for comparability with existing reports; market-side-
and game-weighted sensitivities sit beside them (the review's own demonstration: order-weighted -0.755 / -1.215 against
market-side-weighted +0.597 / +0.365 on the same rows); game-clustered uncertainty uses
`harness/report/stats.py::cluster_ci(values,
clusters, level=0.90)` unchanged, with its one-cluster `nan` convention intact. Concentration is always reported: maximum
contribution by game (game 469 supplied 96 of 191 gate observations) and repeated liquidity attribution (§1.5's allocation
ledger, printed as allocated vs. requested contracts). None of these replaces a frozen gate estimator, and §0.10 keeps them out
of every registered surface.
(d) **Per-variant table, per arm.** eligible games and markets; scheduled and completed observations; placements and episodes;
unique filled orders and filled games; partial fills and contracts; clean and dirty resting time by cause; capacity exclusions;
queue tenure and repricing loss; source freshness at decision and at each outcome point; mature net CLV and 30-minute markout;
adverse drift; game-clustered uncertainty; missing and censored outcomes; and resources consumed (wall clock, rows read,
credits).
(e) **Separation.** Registered historical performance and exploratory arm results are in **separate tables** with separate
captions; every exploratory cell carries
`EXP_LABEL = COUNTERFACTUAL_LABEL + " run=<run_id> arm=<arm_id> manifest=<hash[:12]>"`.
(f) **Charter status carried forward.** The report also prints the current coverage and result status of the charter's
mispricing map, convergence lag and H9 sample, so the dataset goals stay visible; those snapshot-only hypotheses continue
unchanged and 6D.1 expands none of them.
*Files:* `episodes.py`, `report.py`, `tests/test_exp_report.py`, `tests/test_exp_episodes.py`. *Depends on:* 1.4, 1.6, 1.7.
*Expected result, computed independently:* one market with candidate sightings at 12:00, 12:02, 12:04 and 14:00 on a 120 s
cadence yields **2** episodes (600 s gap rule; the 116-minute hole exceeds it) and 4 candidate rows; a portfolio whose two arms
each filled 10 contracts on the same print produces two rows and **no** summed row.

### 1.10 The sample-accrual forecast (draft §7; roadmap decision 6; outline T8)
`forecast.py` projects, for **`sharp_two_sided` alone**, the time to 150 actual filled orders across 40 games in both sports:
no pooling with the primary, no partial-fill rows counted as orders, no counterfactual fills. Three quantities are kept apart:
**observed watched fills** (live facts: 3,669 fills all time, of which the review counts one gate-variant order, two primary
orders and zero constrained orders as queue-model filled), **stateful exploratory estimates** (this milestone's arm results,
labelled), and **conditional scenarios**. The scenario set is fixed before the run: (i) baseline continuation, (ii) arm B's
measured admission and fill rate carried forward at the observed distinct-game rate, (iii) arm B plus arm C's measured
freshness effect if C ran, (iv) a capacity-bound scenario in which the 150 shared slots saturate under a resting policy
(§5.3 of the packet's own caveat: the honest post-change fill count is bounded by slots x turnover), and (v) a pessimistic
scenario at the observed post-repair markout sign. Every scenario states its distinct-game count, both sports, clean-book
eligibility, outcome maturity and uncertainty, and each is reported as a range with its unknowns named. **No gate date is
announced when accrual or economics is unidentified**, and 770 independent counterfactual tracks are never turned into a
portfolio forecast.
*Files:* `forecast.py`, `tests/test_exp_forecast.py`. *Depends on:* 1.9.
*Expected result:* on a fixture with 8 distinct filled games in 10 days and a 150-order/40-game target, the forecast prints a
range and the sentence "accrual unidentified: fewer than 10 distinct filled games observed", not a date.

### 1.11 The decision report (draft §7; U10 "conclude retain/revise/stop/insufficient"; outline T8)
`decision.py` renders the milestone's completion evidence and its dated recommendation - **retain / revise / stop / insufficient
evidence** - into `docs/superpowers/autopilot/reports/2026-09-<dd>-phase6d1-decision.md`. Completion evidence, verbatim from the
draft: proven stateful baseline behaviour and isolation with mismatches explicitly resolved or bounded; comparative paper
results
for feasible arms, or an explicit reason an arm could not be measured, with **zero invented faster-history observations**; a
cause-specific book-health diagnosis and a fresh-outcome coverage report; veto pacing **implemented, preflighted against
stored arrivals under unchanged
caps, shipped dormant, with the §0.8 amendment record and boundary fields prepared; the boundary instant is written at the
user's activation** (I11, D16); and the forecast plus the dated recommendation. **Positive returns or a passing
go-live gate are not completion requirements**: an infeasible or economically unproductive maker configuration closes this
milestone with a supported negative finding. The dormant state **closes the milestone's veto component**: U10 asked for the
pacing to be
implemented and verified within the unchanged caps, and the preflight against stored arrivals plus the prepared amendment
record is that verification; activation changes H9's decided population and is the user's (§0.14c), so 6D.1 neither waits on it
nor is incomplete without it. Any production adoption proposal carries exact settings, hash, effective date,
rollback and affected measurement populations, and remains §0.14a's decision. 6D.1 is marked done only when this evidence
exists - never when its CLI and tests are finished.
*Files:* `decision.py`, `docs/superpowers/autopilot/reports/` (the report), `tests/test_exp_decision.py`. *Depends on:* 1.9,
1.10.
*Expected result:* with any required evidence section empty, `decision.render()` raises rather than emitting a recommendation.

## 2. Data

**The isolation choice, weighed.** (a) *Additive `exp_*` tables in the `harness` database, written only through §1.1(c)'s
writer **and only by §1.1(b)'s least-privileged role*** - **chosen** (C2: the role, not the writer, is the boundary; the role
holds `SELECT` on production tables and `INSERT`/`UPDATE` on `exp_*` alone, so a stray write is refused by the server). (b)
*A separate database on the same PostgreSQL server*: cleanest nominal boundary, but the `harness` role
is assumed to lack `CREATEDB`, so it is an ops action the user performs (and U10's bounded implementation must not block on
one); `deploy/backup/dump.sh` dumps the **named** database, so a second database would be outside every backup and every restore
rehearsal; and `verify.md`'s invariant queries all run against `harness`, so the isolation read-backs of §3 would need a second
connection. (c) *File-backed state under `/srv/sports-harness/` with stdlib formats*: no DDL at all, but no indexed read-back,
no invariant query, no backup coverage and no crash-safe concurrent writer. The chosen shape takes (a) for the **small,
queryable, long-lived** rows and (c) for the **large, reproducible, regenerable** ones: the immutable input capture and the raw
observer bodies live under `/srv/sports-harness/exp/<run_id>/` as hashed newline-delimited JSON, because they are re-derivable
from the record and would otherwise add tens of GB to a 150 GB database, while runs, arms, orders, fills, allocations,
observations,
outcomes, health rows, checkpoints, mismatches and limitations - **eleven tables** (I5) - live in `exp_*` tables that
`pg_dump` already covers (the nightly dump excludes only the
five bulk tape families' **data**), that `create_all` and the migration both declare, and that §3 can read back. A `replay` flag
is not part of this: isolation is §1.1(b)/(c)'s two capabilities. F65's no-carve-out rule applies unchanged - every index on a
bulk table is `CONCURRENTLY`, and none of these tables is a bulk table or a partition of one, so their plain indexes go on
`__table_args__` and in `_INDEX_DDL`. Additive only: `create table if not exists`, `create index if not exists`,
`create or replace view`; no DROP, RENAME, TRUNCATE, DELETE or backfill; `downgrade()` is `pass`. Migration
`00NN_phase6d1_exec_viability` (28 characters, inside `String(32)`), `NN` assigned by the controller at merge.

| Addition | Shape | Invariant query (must return 0) |
|---|---|---|
| `exp_run` (model) | `run_id uuid pk`, `created_at`, `manifest_hash varchar(64)`, `manifest jsonb`, `code_sha varchar(40)`, `clock_mode varchar(24)`, `status varchar(12)`, `supersedes uuid` | `select count(*) from exp_run where manifest_hash is null or length(manifest_hash) <> 64 or clock_mode not in ('retained_action_instants','ideal_grid_15s')` |
| `exp_arm` (model) | `id`, `run_id`, `arm_id varchar(8)`, `label varchar(32)`, `spec jsonb`, `spec_hash varchar(64)`; unique `(run_id, arm_id)` | `select count(*) from exp_arm a where not exists (select 1 from exp_run r where r.run_id = a.run_id)` |
| `exp_order` (model) | the `PaperOrder` columns plus `run_id`, `arm_id`, `variant_id`, `venue_market_id`, `side`, `prob`, `contracts`, `placed_at`, `expiry`, `queue_ahead_at_place`, `cancelled_at`, `cancel_reason varchar(24)`, `episode_id`; index `ix_exp_order_run_arm (run_id, arm_id, placed_at)` | `select count(*) from exp_order where contracts <= 0 or (cancelled_at is not null and cancelled_at < placed_at)` |
| `exp_fill` (model) | `id`, `run_id`, `arm_id`, `exp_order_id`, `filled_at`, `contracts`, `prob`, `fee`, `fill_method varchar(16)`, `source_trade_id varchar(64)`, `through bool`; index `ix_exp_fill_trade (run_id, arm_id, source_trade_id)` | `select count(*) from exp_fill f join exp_order o on o.id = f.exp_order_id where f.filled_at > o.expiry or (o.cancelled_at is not null and f.filled_at > o.cancelled_at and f.fill_method = 'queue_model')` |
| `exp_allocation` (model) | `run_id`, `arm_id`, `variant_id`, `source_trade_id`, `available numeric`, `allocated numeric`; pk `(run_id, arm_id, variant_id, source_trade_id)` | `select count(*) from exp_allocation where allocated > available` |
| `exp_observation` (model) | `id`, `run_id`, `observed_at`, `available_at`, `sport varchar(8)`, `game_id`, `venue_market_id`, `fair_p numeric`, `source varchar(16)`, `credits integer`, `credits_last integer`, `credits_remaining bigint`, `status varchar(24)`, `body_path text`, `body_sha256 varchar(64)` (I5: the raw body itself lives only in the file tree; I9: `credits_last`/`credits_remaining` are `parse_credit_headers`' `x-requests-last`/`x-requests-remaining`) | `select count(*) from exp_observation where available_at < observed_at or credits < 0 or (body_path is not null and body_sha256 is null) or credits_remaining < 0` |
| `exp_outcome` (model) | `id`, `run_id`, `arm_id`, `exp_order_id`, `horizon varchar(8)`, `observed_at`, `value numeric`, `source_age_s integer`, `censored bool`, `missing_reason varchar(24)` | `select count(*) from exp_outcome where (censored and value is not null) or (not censored and value is null and missing_reason is null)` |
| `exp_book_health` (model) | `id`, `run_id`, `ticker varchar(64)`, `interval_start`, `interval_end`, `classification varchar(24)`, `evidence jsonb` | `select count(*) from exp_book_health where interval_end < interval_start or classification not in ('inactive_confirmed','data_loss_confirmed','unresolved')` |
| `exp_checkpoint` (model) | `run_id`, `arm_id`, `cursor_event_id bigint`, `state jsonb`, `manifest_hash varchar(64)`, `updated_at`; pk `(run_id, arm_id)` | `select count(*) from exp_checkpoint c join exp_run r on r.run_id = c.run_id where c.manifest_hash <> r.manifest_hash` |
| `exp_mismatch` (model) | `id`, `run_id`, `arm_id varchar(8)`, `instant`, `venue_market_id`, `kind varchar(32)` (`action`, `price`, `fill`, `cancel_instant`, `expiry`, `capacity`), `expected jsonb`, `actual jsonb`, `cause varchar(32)`, `explained bool not null default false`; index `ix_exp_mismatch_run (run_id, explained)` | `select count(*) from exp_mismatch where (explained and cause is null) or kind not in ('action','price','fill','cancel_instant','expiry','capacity')` |
| `exp_limitation` (model) | `id`, `run_id`, `kind varchar(40)` (`loop_spacing_unreconstructable`, `kickoff_not_asof`, `availability_unreconstructable`, `capture_hash_mismatch`, `overnight_unanchored`, `arm_unavailable`), `scope jsonb` (the slice, arm or market the limitation applies to), `detail text`, `created_at`; index `ix_exp_limitation_run (run_id, kind)` | `select count(*) from exp_limitation where kind not in ('loop_spacing_unreconstructable','kickoff_not_asof','availability_unreconstructable','capture_hash_mismatch','overnight_unanchored','arm_unavailable') or scope is null` |
| `/srv/sports-harness/exp/<run_id>/*.ndjson` + `manifest.json` | hashed capture streams and raw observer bodies (`exp_observation.body_path` and `body_sha256` point here; no raw-body table exists) | `sha256` of each file equals its manifest entry (checked by `exp report`, recorded in `exp_limitation` when it does not) |

**Disk cost.** Database side, for one historical run over a three-day slice with three arms: `exp_order` at the observed
placement rate (32,081 orders in 7 days, ~4,580/day, x3 arms x3 days) is ~41,000 rows at ~200 B plus one index, **~10 MB/run**;
`exp_fill` and `exp_allocation` are bounded by the print population of the slice (151 distinct trade keys in the review's whole
post-repair cohort), well under 1 MB; `exp_observation` for a five-day prospective window is 3,600 intervals (five days at
120 s)
x ~40 markets - the **whole eight-game cohort across both sports**, not per sport (M5) - = ~144,000 rows at ~120 B plus an
index, **~25 MB** (the earlier `2 sports x` factor double-counted the cohort; the conclusion, tens of MB, is unchanged);
`exp_outcome` is three horizons per admitted order,
~120,000 rows, **~20 MB**; `exp_book_health` is capped at `BOOK_VERIFY_MAX = 200` sampled intervals plus one row per classified
interval, under 5 MB. The two new tables are bounded by construction: `exp_mismatch` cannot exceed one row per compared action
per arm (bounded by `exp_order`'s row count and its lifecycle transitions, ~50,000 rows per run, and a run whose unexplained
mismatches exceed
`EXP_MISMATCH_MAX = 10,000` stops and reports rather than filling the table), and `exp_limitation` is **at most a few hundred
rows per run** - one per named limitation kind per affected slice, arm or market, never one per row of data. A full milestone
- say four historical runs and one prospective cohort - is therefore **under 150 MB in
the database**, 0.1 % of the 150 GB in use and 0.025 % of the 600 GB budget. File side: the capture of a three-day slice is
dominated by `orderbook_events` deltas (the executor alone reads ~675,000 delta rows a loop today); bounded to the cohort's
tickers it is ~1-3 GB per run as compressed-free NDJSON, so `EXP_CAPTURE_MAX_GB = 20` is enforced by `capture.py` before the
first write and `/srv/sports-harness/exp/` is reported in the daily free-space line (the 25 % free-space gate stands). Raw
observer bodies are ~60 KB per featured response x 7,200 responses = **~430 MB** per five-day window; `EXP_RAW_BODY_MAX_GB = 5`
bounds them and the observer goes dormant rather than exceeding either bound.

## 3. Verification (the plan's last task adds these rows to `docs/superpowers/autopilot/verify.md`)

1. **Experiment tables' invariants.** The **eleven** queries in §2's right-hand column (one per `exp_*` table, I5) each return
   **0**. *Every verify once a run
   exists; before that the row reads "deferred: no `exp_run` row".*
2. **Isolation read-back: no experiment row ever entered a production table (I6, C2).** The read-back the experiment cannot
   fake is a **before/after count**, journaled either side of every run: for each of `orders` (`placed_at`), `fills`
   (`filled_at`), `intents` (`created_at`) and `signals` (`created_at`),
   `select count(*) from <table> where <stamp> between :warmup_start and :observation_end` is taken immediately before the run
   starts and immediately after it ends, and the difference must be **explained entirely by the live executor's own
   placements** - verified by every new row carrying production's `config_hash`, and every new `orders` row production's
   `client_order_id` prefix (the experiment computes no `config_hash`, writes no order and owns no prefix, so a row it created
   would show up as an unexplained increment; revision 1's `client_order_id like 'exp-%'` join could never fire and is
   deleted). `strategy_variants` still holds the eight ids of live fact (b) and `criteria_hash` is unchanged. Beside them, the
   **privilege read-back** of C2:
   `select t, has_table_privilege('harness_exp', t, 'INSERT') from unnest(array['orders','fills','intents','signals',
   'positions','source_state','research_spend','veto_decisions']) t` is **false on every row**, while
   `select has_table_privilege('harness_exp', 'exp_order', 'INSERT')` is true - the boundary read directly from the server
   rather than inferred from the code. *Every verify during and after a run; the privilege pair also on the first verify after
   the grant.*
3. **Gate inputs untouched.** `select count(*) from gate_reports where criteria_json ? 'eligibility'` = 0 and the gate report's
   `orders`/`fills` denominators over the run window equal the pre-run values recorded in the journal. *First verify after each
   run.*
4. **Veto pacing read-backs.** `select day, sum(usd + usd_reserved) from research_spend where day >= :monday group by 1` is at
   or under **$25 a day**, with the ISO-week total beside it -
   `select sum(usd + usd_reserved) from research_spend where day >= :monday` at or under **$150** (unchanged caps; the caps are
   checked against the sum of spent and reserved, which is what `ResearchSpend` stores - there is no `usd_spent` column, I7) -
   and
   `select reason_code, count(*) from veto_decisions where created_at > now() - interval '24 hours' and decision =
   'veto_skipped_budget' group by 1` names the new `hourly`/`reserved` codes only after the amendment instant; reservations by
   window: `select window_label, decided, reserved from exp_veto_coverage where day = :day` - a **parameterless**
   `create or replace view` (I8) over
   `veto_decisions d join veto_queue q on q.signal_id = d.signal_id join games g on g.id = q.game_id`, the authoritative join
   because `veto_queue` carries `game_id` and `veto_decisions` does not, with the day filtered in the **caller's** `where` -
   shows a non-zero **inside-6 h** count on every game day after activation, against the recorded zero of 2026-09-18. *Daily
   09:00 line and every verify after activation.*
5. **The observer's quota accounting, against the provider's balance (I9).** Three numbers are journaled together: the
   observer's per-run sum `select sum(credits) from exp_observation where run_id = :run`, at or under
   `EXP_OBSERVER_CREDIT_CAP = 60,000`; the observer's latest provider balance,
   `select credits_remaining from exp_observation where run_id = :run and credits_remaining is not null order by observed_at
   desc limit 1`; and the recorder's latest `recorder.credits_remaining` sample,
   `select value from metric_samples where name = 'recorder.credits_remaining' order by ts desc limit 1`
   (`harness/recorder/tick.py:386`). The two balances are the **same provider counter** read by two processes, so they agree to
   within the calls made between the two reads - at most one observer call, 3 credits - and a wider divergence is a defect, not
   a rounding. Both must stay above `credits_watch_fraction x odds_monthly_credits` = 2,000,000, the guard
   `harness/recorder/tick.py:1411` applies and §1.6(i) reuses. `select count(*) from exp_observation where status =
   'exp_skipped_budget'` is journaled beside them. *Every verify while the observer is active.*
6. **Recorder and executor unharmed.** `exec.loop_ms` p95 in the hour a historical run executes is within 10 % of the hour
   before it (journalled as a pair, the way fix 46's row does); `recorder.tick_ms` and the normalize backlog unchanged;
   `exec.tape_lag_tickers` no higher. *Each historical run.*
7. **Disk.** `du -sm /srv/sports-harness/exp` under `EXP_CAPTURE_MAX_GB + EXP_RAW_BODY_MAX_GB` (25 GB), free space on
   `/srv/sports-harness` above 25 %, and `select pg_total_relation_size('exp_observation')/(1024*1024)` under 100 MB per
   cohort. *Daily 09:00 line.*
8. **Baseline proof published.** `exp baseline-check` prints zero **unexplained** mismatches for the proven slice, and every
   explained one is in `exp_mismatch` with its cause; a slice with missing input history reads "incomplete/unverifiable", never
   "parity". *Before the first arm comparison is read.*
9. **Manifest freeze before activation.** For the prospective cohort: `exp_run.status = 'frozen'`, `manifest_hash` recorded in
   the journal, the cohort's game ids and seed printed, and `exp_run.created_at` **before** the first
   `exp_observation.observed_at`.
   *At activation.*
10. **The deploy is judged on** rows 2, 3, 5 and 6 plus the executor's own health: `exec.loop_ms` p95 no worse than the
    pre-deploy hour it is compared against, `recorder.tick_ms` and `recorder.rss_mb` journaled before and after, the alembic
    stamp advanced to the new head, and `select count(*) from pg_index i join pg_class c on c.oid = i.indexrelid where
    c.relname like 'ix_exp_%' and not i.indisvalid` = 0.

## 4. Ops

- 4.1 **Target.** The **full** recipe (`make deploy-omarchy`, fix 37's reviewed version) because the diff touches
  `harness/db/models.py` and adds a migration: stop `app-exec` and `app-run` before `init-db` (fix 21), `init-db`,
  `alembic upgrade head`, `up -d`, stamp check. Production is at `0013_nw_executor_version` while `main` carries
  `0014_orders_intent_index` (fix 85, held for Monday's full release), so this milestone's migration lands **after** 0014 in the
  same or a later full release and the head is verified, not assumed.
- 4.2 **Where the code runs.** Historical runs are one-shot `harness exp …` invocations **inside the existing `app-research`
  container** (`docker compose exec app-research …` in the release runbook's shape), never a new container, cron entry or bind.
  The prospective observer is a **job in `app-research`'s existing worker loop** (`register_pass`), off unless
  `Settings.exp_observer_enabled` is set and a frozen `exp_run` exists. No compose service is added; `app-ws`, `app-exec`,
  `app-run` and `app-serve` are untouched by 6D.1's own code paths. `app-research` gains exactly one bind, in the shape the
  other secrets already use (`- ./secrets/odds_api_key:/run/secrets/odds_api_key:ro`): `-
  ./secrets/exp_db_password:/run/secrets/exp_db_password:ro` (§4.7).
- 4.3 **Cooperative limits.** Every historical run sets `statement_timeout = 25s`, `lock_timeout = 1s` and
  `default_transaction_read_only = on` on its source session; walks the tape in bounded batches
  (`EXP_BATCH_ROWS = 20,000`, the executor's own `tape_batch_min` ceiling); yields between batches when
  `exec_heartbeat.last_loop_ms` exceeds 3 x `exec_period_s` (fix 46's `RFQ_YIELD_LOOP_MULT` shape) - with p95 already at
  24,165 ms against a 15 s period, that guard binds often and is meant to; and runs **only in a quiet window** (no matched game
  `in_progress`, outside R4's kickoff bands, and outside 01:00-08:00 CT only if the recorder is idle). A run that trips the
  yield guard checkpoints and stops; it never delays the recorder or the executor.
- 4.4 **What R4 forbids.** No **full** deploy while any matched game is `in_progress`, within 4 h after any kickoff, within
  15 min before any kickoff, or 60-100 min before an NFL kickoff; NFL windows (Sunday from 10:20 CT, Monday night) block every
  deploy; journal 128's app-only NCAAF allowance does **not** apply to this diff because it carries a migration. R4 is rechecked
  at the deploy instant; no experiment deadline is an exception.
- 4.5 **Rollback.** Previous sha plus `make deploy-omarchy` (or `make deploy-omarchy-app` for a code-only follow-up). The
  additive tables and indexes stay - no DROP is ever part of a rollback (invariant 5) - and a rolled-back build ignores them.
  The file tree under `/srv/sports-harness/exp/` is left in place; nothing reads it unless a run is resumed.
- 4.6 **Activation checklist for the prospective cohort** (every step journaled; the run does not start until all are done):
  (0) **the `harness_exp` grant and its secret exist** - §4.7's `CREATE ROLE` / `GRANT` / `REVOKE` has been run and
  `secrets/exp_db_password` is in place *(the user)*; until it is, every run **fails closed** with `IsolationError` at session
  open and §3 row 2's privilege read-back is the evidence it took effect *(loop)*; (1) **frozen manifest** -
  `exp_run.status = 'frozen'`, hash recorded, cohort ids and seed printed *(loop)*; (2) **recorded
  measurement boundary** - the activation instant in UTC and CT, written to the journal and to `exp_run` before the first
  observation *(loop)*; (3) **resource preflight** - free space above 25 %, `EXP_CAPTURE_MAX_GB`/`EXP_RAW_BODY_MAX_GB` headroom,
  `exec.loop_ms` p95 and `recorder.tick_ms` recorded as the before-half *(loop)*; (4) **budget and coverage checks** -
  credits remaining against `EXP_OBSERVER_CREDIT_CAP` and the `credits_watch_fraction = 0.40` guard, the cohort's
  eligible-market
  enumeration complete, coverage tolerances stated *(loop)*; (5) **ordinary release verification** - the deploy carrying the
  observer passes §3 and the standing verify rows *(loop)*; (6) **the veto profile's activation**, if any, is §0.14c's dated
  decision *(the user)*; (7) **any production holding-policy adoption** is §0.14a's dated decision *(the user)*. Steps 6 and 7
  are not prerequisites of 1-5 and never gate them. Step 0 **is** a prerequisite of 1-5, and it is the only user step that is.
- 4.7 **The experiment role and its secret - the one ops step the user performs (C2).** The `harness` application role cannot
  create a role, so the privilege boundary is a user action, exactly like today's secret placement. Two parts, both one-off:

  *(i) The secret.* The user writes a password of their choosing to `/srv/sports-harness/secrets/exp_db_password` on the
  Omarchy runtime in the existing shape - `printf '%s' '<password>' > secrets/exp_db_password && chmod 600
  secrets/exp_db_password` from the runtime directory, no trailing newline, never committed (`secrets/` is ignored) - exactly
  as `secrets/anthropic_api_key` is placed for `app-research` today (`docs/runbooks/research.md`; the Makefile's NAS scp loop
  at lines 52-53 is the retired NAS path and is **not** edited). `scripts/release-omarchy.py` creates, copies and reads no
  secret (its D7 rule); a missing file never blocks a deploy (gate 1): compose then binds a directory at the mount path, the
  `is_file()` test is false and the experiment stays dormant. `Settings.exp_db_password_file` reads
  `/run/secrets/exp_db_password` with the `is_file()`-and-non-empty test the other secret files use, and **the loop never reads
  or prints the value** - the loop tests only `is_file()` and the privilege read-back of §3 row 2.

  *(ii) The grant.* Run once by the user against the `harness` database as its owner role (psql, on the host; the exact text
  also goes into `docs/runbooks/experiments.md`, which T1 writes, and into the roadmap's `## User-side TODOs`):

  ```sql
  CREATE ROLE harness_exp LOGIN PASSWORD '<the value in secrets/exp_db_password>';
  GRANT CONNECT ON DATABASE harness TO harness_exp;
  GRANT USAGE ON SCHEMA public TO harness_exp;
  -- read everything, write nothing
  GRANT SELECT ON ALL TABLES IN SCHEMA public TO harness_exp;
  REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM harness_exp;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO harness_exp;
  -- write only the experiment's own eleven tables (run after the migration creates them)
  GRANT INSERT, UPDATE ON exp_run, exp_arm, exp_order, exp_fill, exp_allocation, exp_observation,
        exp_outcome, exp_book_health, exp_checkpoint, exp_mismatch, exp_limitation TO harness_exp;
  GRANT USAGE, SELECT ON SEQUENCE exp_arm_id_seq, exp_order_id_seq, exp_fill_id_seq,
        exp_observation_id_seq, exp_outcome_id_seq, exp_book_health_id_seq,
        exp_mismatch_id_seq, exp_limitation_id_seq TO harness_exp;
  ```

  No `CREATEDB`, no second database, no second backup target, no DROP and no change to any production grant: `harness_exp` is a
  new role that can read and can write eleven additive tables. The migration still runs as the owner role, so nothing about
  `alembic upgrade head` changes. The order matters - the `GRANT INSERT` lines name tables that exist only after the migration -
  and the runbook says so; running part (ii) before the release simply errors on the missing tables and is re-run after.
  *Verification:* §3 row 2's `has_table_privilege` pair, and `\du harness_exp` showing no `Superuser`/`Create DB` attribute.

## 5. Testing

Every test passes a fixed tz-aware `now` (`datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)` unless a case needs another) and
never reads the wall clock. Fixtures come from the recorded capture or from hand-built rows whose expectations are derived
independently of `harness.execution.fills` (6B's I-13 rule).

- `tests/test_exp_isolation.py` - **the privilege boundary first (C2)**: against the test PostgreSQL, a session opened for a
  role that holds `INSERT` on `orders` raises `IsolationError` at open from both `source.reader()` and `ExperimentWriter`
  (the `has_table_privilege(current_user, 'orders', 'INSERT')` probe), an absent secret file raises the same, and a session
  under a role granted the §4.7 shape opens and can insert into `exp_run` but has its `insert into orders` refused **by the
  server**; then the defence in depth - a write through the source reader raises from PostgreSQL, `ExperimentWriter.insert`
  refuses every `PRODUCTION_TABLES` member by name (including `source_state`, I9), `check_destination` refuses a foreign
  `dbname`, the package imports no `harness.db.models` at module scope and no gateway/transport module (the `test_gateway.py`
  /`test_rfq_refusal.py` shape), and `source == destination` is recorded, not silently allowed. No test reads or prints the
  secret's value.
- `tests/test_exp_manifest.py` - determinism of the hash; `freeze()` refuses an incomplete manifest; a manifest mismatch
  **rejects resume** and leaves the checkpoint byte-identical.
- `tests/test_exp_state.py` - two arms in one process cannot see each other's orders, capacity counter, exposure or cursor;
  restart and chunk boundaries never double-consume (3 x 1 h equals 1 x 3 h, row for row); capacity stays occupied while an
  order rests and is released on cancel, expiry and fill; **no fill after expiry**; repricing opens a new order at the back of
  the queue at the new price and the old queue position is not inherited.
- `tests/test_exp_liquidity.py` - the 10-contract print with 45 overlapping counterfactuals yields at most 10 contracts in one
  portfolio; partials, cancel/re-entry, retry and resume never replenish; two portfolios are never summed.
- `tests/test_exp_arms.py` - B's weekday (1,000 s), weekend (400 s), sport-wide window (220 s), NFL burst (180 s floor),
  overnight suspension (I4's closed form: the allowance is `interval_for` at the latest `exec_period_s` step at or before
  `fair_ts` returning a finite value, and a fixture with no such instant in the capture window takes 1,000 s and is labelled
  `overnight_unanchored`), missed fetch (deadline not extended) and regime
  transition cases; **a fixture where stale cancellation blocks a later real print and B receives it**; **a fixture where new
  information triggers repricing/edge decay and prevents the apparent counterfactual gain**; `rest_to_expiry` produces a
  different action list from B on the same fixture; A/B cannot read C-only odds (the C observations are in `exp_observation`
  and the A/B market reader never queries it); no lookahead from a later fetch or REST backfill (a row whose availability stamp
  is after the decision instant is invisible).
- `tests/test_exp_capture.py` / `test_exp_adapter.py` / `test_exp_baseline.py` - event-time versus availability-time handling;
  **`resolve_instants()` returns the union of `orders.placed_at`, `order_events.ts`, `intents.created_at`, `fills.filled_at`
  and the `exec.loop_ms` samples, sorted and deduplicated, and the run carries the `loop_spacing_unreconstructable`
  limitation row** (C1); the sample `ts` is treated as the decision instant (I1) and the `ts - value_ms` reading is exercised
  only as the labelled sensitivity; the as-of kickoff reconstruction (I3), including a fixture whose kickoff was revised after
  the decision - the revised value must not reach `interval_for`; a true cancel/replacement chain, a partial fill, an
  overnight transition, a delayed loop and a gap/recovery each traced independently; missing input history produces
  "incomplete/unverifiable", never silent parity.
- `tests/test_exp_bookhealth.py` - the four classification fixtures of §1.7; `BOOK_VERIFY_MAX` bounds the sampling.
- `tests/test_veto_pacing.py` - no overspend under concurrent reservations (two sessions racing `reserve_spend`, one
  `BudgetRefused`); midnight, ISO-week and DST boundaries (`chicago_day`, `iso_week_bounds`); a busy Saturday cannot consume an
  explicitly reserved Sunday allocation; stale backlog cannot monopolise new kickoff windows; new material information bypasses
  cached context and forces a call; every skipped evaluation stays labelled; **the profile is dormant by default and today's
  `_OLDEST_BUCKET` order is unchanged when `veto_pacing_profile is None`**.
- `tests/test_exec_plan.py` (existing, extended) - `plan_actions` and `_fair_stale` with **no** policy argument produce
  byte-identical results to `main`'s on the whole existing corpus; `tests/test_policy_compare.py` is unchanged and still passes.
- `tests/test_exp_report.py` / `test_exp_episodes.py` / `test_exp_forecast.py` / `test_exp_decision.py` - episode and re-entry
  arithmetic; the refusal to sum portfolios; the separation of registered and exploratory tables; the forecast's
  "accrual unidentified" path; `decision.render()` raising on missing evidence.
- `tests/test_alembic.py` (existing) - the catalogue diff covers the **eleven** new tables and their indexes, and asserts
  `len(EXP_METADATA.tables) == 11` (I5); `tests/test_schema.py`
  covers the `create_all` path.

## 6. Out of scope

Production adoption of any holding policy (§0.14a, the user's dated decision); 6F's version boundary, revised selection and
confirmation dates and extension rule (§0.14b); any live posture, venue write, real money or `LIVE_TRADING` change; any new
venue WebSocket subscription or recorder restart for historical work; migration of the production `no_watcher` worker (a later
migration needs its own parity evidence and version boundary); any cap, tier, provider or bookmakers change; any cadence or
alternates-window change (gate 5); any new variant id, edit under `harness/variants/`, or change to
`MAX_PRIMARY`/`MAX_SECONDARY`;
any change to a gate criterion, threshold, family, grid, success threshold or cut-off (R1); the gate-eligibility settings (still
dormant, never set); 6D's own coverage contract, acceptance rows and Task 11 work, which are not reopened or expanded by this
follow-on; 6E's environment acceptance; 4.6's fun tickets; the dashboard and the weekly report's tables (§0.10); the executor's
own carried fixes (rows 78, 79, 84, 86, 87), which keep their existing priority and vehicles; a combined-variant portfolio
with a
common liquidity allocator; and a changed book-health policy, which would be a separately frozen later arm. **The study is
exploratory**; the formal prospective period remains 6F's.

## 7. Conformance (autopilot plan-next 1a)

1. **Components.** §1.1-§1.11 implement the draft's §§1-7 and U10's five scope clauses (stateful baseline/cadence comparison,
   bounded faster observations, book-health diagnosis, independent veto pacing, accrual forecast) and nothing else.
2. **Dependencies.** None new: standard library, SQLAlchemy, pydantic, httpx, Typer and pytest as pinned; `pyproject.toml` and
   `constraints.txt` untouched (§4, §6).
3. **Pre-registered ids.** Untouched (§0.3 posture, §6). No `EXECUTOR_VERSION` or `PRICING_VERSION` bump. The complete list of
   edits outside `harness/experiments/`, with the reason each needs no version boundary (I10):
   (i) `harness/execution/policy.py` - one defaulted field `cadence_allowance: CadenceAllowance | None = None` on
   `HoldingPolicy`; `BASELINE` is unchanged, so every live construction passes `None`.
   (ii) `harness/execution/plan.py` - one `if policy.cadence_allowance is not None:` branch inside `_fair_stale` (§1.6a);
   unreachable while the field is `None`, which §5's byte-identical corpus assertion proves.
   (iii) `harness/cli.py` - the `exp` command group and its help text; no production decision path is entered by registering a
   command, and no existing command's behaviour changes.
   (iv) `harness/research/spend.py` - the reservation check inside `reserve_spend` (`harness/research/spend.py:213`); a
   **no-op while `Settings.veto_pacing_profile is None`**, and the daily/weekly caps themselves are untouched.
   (v) `harness/research/veto.py` - the claim order behind the same setting; `_OLDEST_BUCKET`'s ordering is byte-identical when
   the profile is `None` (§5 asserts it).
   (vi) `harness/research/worker.py` - one `register_pass(...)` line (`harness/research/worker.py:88`); the pass is **inert
   unless both `Settings.exp_observer_enabled` is set and a frozen `exp_run` row exists**, and it never touches a production
   table.
   (vii) `harness/config/settings.py` - new fields with inert defaults (`exp_db_password_file`, `exp_observer_enabled = False`,
   `veto_pacing_profile = None`, the `EXP_*` bounds); a declaration changes no behaviour.
   No production decision, price, size, order, fill or gate input changes under any of the seven; §5's byte-identical
   assertions are the evidence, and D6 carries the reversal.
4. **Schema.** Additive only and declared where this repo looks: **eleven** models (§2, I5 - `exp_mismatch` and
   `exp_limitation` are declared, and the raw observer bodies live in the hashed file tree, not in a table), their plain
   indexes on `__table_args__` and in
   `_INDEX_DDL`, one `create or replace view`, and a migration carrying the same statements with `downgrade()` = `pass` (§2);
   no DROP, RENAME, TRUNCATE, DELETE or backfill; F65 unaffected (no bulk table gains an index); migration number assigned by
   the controller at merge.
5. **Venue writes.** None; the gateway stays `PaperGateway` and no experiment module may import a transport or gateway module.
   Refusal tests, named: existing `tests/test_gateway.py::test_paper_gateway_never_touches_transport` and
   `::test_executor_in_live_mode_cannot_build_a_gateway`, existing
   `tests/test_rfq_refusal.py::test_the_transport_refuses_the_exact_quote_path` and
   `::test_no_module_in_the_repository_names_the_quote_path`, plus new
   `tests/test_exp_isolation.py::test_no_experiment_module_imports_a_gateway_or_transport`,
   `::test_the_source_capability_cannot_write`, `::test_the_writer_refuses_every_production_table`,
   `::test_a_foreign_destination_is_refused_before_any_work` and C2's
   `::test_a_session_whose_role_can_insert_into_orders_is_refused_at_open`.
6. **Money.** No new spend, no new provider, no tier change; `veto_daily_usd_cap`/`veto_weekly_usd_cap` untouched (invariant 7)
   and the pacing profile changes **when** the cap binds, never the cap. The only metered calls are arm C's Odds API reads:
   `EXP_OBSERVER_CREDIT_CAP = 60,000` credits per run is enforced **in code** before every call, and so is the recorder's own
   guard, `remaining < credits_watch_fraction x odds_monthly_credits` (0.40 x 5,000,000, `harness/recorder/tick.py:1411`),
   evaluated on the **provider's own balance**: every observer call parses `x-requests-last`/`x-requests-remaining` with
   `parse_credit_headers` and stores them on its `exp_observation` row, so the shared aggregate both processes read is the
   provider counter itself and no privilege on `source_state` is needed or granted (§1.6i, I9). The observer goes **dormant**
   when the cap or the guard is reached and labels every unmade scheduled read `exp_skipped_budget`; §3 row 5 reads the three
   numbers back. Anthropic spend is unchanged: the
   veto's call count is governed by the same caps through the same `reserve_spend`.
7. **Secrets.** **One new file, `secrets/exp_db_password`** (C2, §4.7), in the existing shape: a runtime file at mode 600 the
   user places under `/srv/sports-harness/secrets/` (absence never blocks a deploy - gate 1), one read-only compose bind on
   `app-research`, one `Settings.exp_db_password_file` field tested with `is_file()` and a non-empty read. **The loop never
   reads or prints its value**; the user writes it once. No other component reads `secrets/` beyond the already-provisioned
   `odds_api_key` the recorder's client already uses and the already-provisioned `anthropic_api_key` the veto already uses. No
   key handling, transport or outbound host changes.
8. **Ops.** §4: full recipe under R4, rollback to the previous sha, no new container, compose service, cron entry, outbound
   host or cadence; one job inside the existing `app-research` container; quiet windows and cooperative limits. Two additions,
   both C2's and both one-off: the `secrets/exp_db_password` file with its single read-only bind (item 7), and §4.7's
   `CREATE ROLE` / `GRANT` / `REVOKE`, which **the user** runs once - no CREATEDB, no second database, no second backup target,
   and no production grant altered. Until both exist every run fails closed, so neither blocks a release (§4.6 step 0).
9. **Verification.** §3 carries one invariant query per new table (**eleven**, §2's right-hand column), the isolation
   read-backs - now a before/after count of the four production tables plus the server-side `has_table_privilege` pair, both of
   which can actually fire (I6, C2) - the veto pacing read-backs against `sum(usd + usd_reserved)` and the parameterless
   `exp_veto_coverage` view (I7, I8), the observer's quota accounting against the provider balance (I9), expected values by
   time of day (rows 1, 4, 5, 7) and the deploy judgment (row 10); every read names the index or the cap that bounds it, and no
   row is vacuous.
10. **Decisions taken on the user's behalf.** §8's **D1-D18**, each with source, rationale, cost if wrong, blast radius and the
    exact reversal; the three questions held for the user are §0.14.
11. **Out of scope** matches the roadmap's boundaries (§6): no production policy adoption, 6F's dates, no live posture, no new
    subscription or recorder restart, no `no_watcher` migration, no cap or tier change, the study is exploratory.
12. **Files and Depends on.** Every component in §1 carries both lines. §1.1 and §1.8 share no file with each other and are the
    independent starts; §1.2 follows §1.1; §1.3 follows §1.2; §1.4 and §1.5 follow §1.3 and are serialized over `adapter.py`;
    §1.6 follows §1.5 and is serialized with §1.4 over `adapter.py`; §1.7 needs only §1.1 and may use §1.3's capture; §1.9
    follows §1.6 and §1.7; §1.10 and §1.11 follow §1.9. `harness/execution/plan.py` and `policy.py` are touched by §1.6 alone;
    `harness/config/settings.py` and `docker-compose.yml` by T1 alone (§4.7);
    `harness/research/*` by §1.8 and T7's one `register_pass` line, which are in different waves; `harness/db/models.py`, `schema.py` and the migration by §1.4's task alone (§1.9's
    view is added there too). `verify.md` is last.

## 8. Decisions taken on the user's behalf

`pre-loaded` = U10, the adopted draft or the confirmed review; `model` = this author's judgment.

| # | Decision | Source | Rationale | Cost if wrong | Blast radius | Reversal |
|---|---|---|---|---|---|---|
| D1 | The milestone number is **6D.1**, between 6D and 6F, in this repository | pre-loaded (U10; draft §0, §8) | the user's own question was whether it should be its own milestone; the roadmap row and decision 4's follow-on already name it | a renumbering of one roadmap row and three file names | documentation | renumber in the roadmap, the spec and the plan |
| D2 | Same repository and shared core: no second pricing, planning, queue or matching implementation | pre-loaded (U10 "shared algorithms and isolated state"; draft §1, §8) | a second implementation cannot be compared with the live one, and the review's whole point is that the live model is the one under test | a shared-core extraction that changes default behaviour; §5's byte-identical assertions are the guard | files, production code paths | revert the extraction; the defaulted-argument shape makes each step independently revertible |
| D3 | Isolated experiment storage (draft §1, §8) realised as **additive `exp_*` tables in the `harness` database plus a hashed file capture**, written **only by the least-privileged role `harness_exp`** (§4.7), not a scratch database and not files alone | model for the storage (the draft left "an explicitly named scratch database or file-backed state" open); C2 for the privilege boundary | the `harness` role is assumed to lack CREATEDB (a new database is an ops action the user performs), `deploy/backup/dump.sh` covers only the named database, and `verify.md`'s invariant queries all run against `harness`; the draft's "reject a production destination, and fail closed if isolation cannot be established" is then honoured by the **server**: under `harness_exp` no production table is a possible destination, whatever the code does | the boundary is now a database property, so the residual cost is an **ops** one: the role or the secret is missing and every run refuses to start (fail closed, §4.6 step 0), or the grant is made wider than §4.7's text and §3 row 2's `has_table_privilege` read-back catches it on the next verify. A defect in `ExperimentWriter` can no longer reach a production table | DB additive, files, one user-run grant | drop the role (`REVOKE` + `DROP ROLE harness_exp`) and the experiment stops; or point `ExperimentWriter` at a named scratch database (one URL setting) if the user ever creates one - the writer's metadata already contains only `exp_*` tables |
| D4 | Initial arms are **A/B/C only** | pre-loaded (draft §4, §8) | avoids searching the six-policy grid and avoids changing quote aggression and capacity at the same time as freshness | a genuinely better parameter goes unmeasured this milestone | documentation, run scope | add a fourth `ArmSpec`; the runner is arm-generic |
| D5 | The faster-observation proposal is **120 s featured reads** | pre-loaded (draft §4, §8) | it is the sport-wide game-window cadence the recorder already uses, so it needs no new endpoint and no new cost model | a slower or faster interval would answer a slightly different question | run scope, credits | change `EXP_OBSERVE_INTERVAL_S`; the cap arithmetic scales linearly |
| D6 | Arm B is implemented as **one defaulted field on the existing `HoldingPolicy`** (`cadence_allowance`) and one branch inside `_fair_stale`, not as a new policy module | model | 6D already established that shape and proved the live path stays bit-identical when nothing is passed; a parallel mechanism would be a second place the freshness rule lives | one more field on a frozen dataclass; if the branch were ever reachable in production the live rule would widen, which §5's byte-identical test and D11's version decision guard | file, production code path | delete the field and the branch (two edits) |
| D7 | B's interval is `cadence.interval_for` evaluated at the **fair row's creation instant**, not at the decision instant and not from the order's own time to kickoff | model (draft §4 "derive the interval from the recorder's actual sport-wide schedule") | entering a game window would otherwise retroactively shorten an already-priced row's allowance, which is a different rule from the one the draft states | a boundary-crossing row gets the wider allowance for the rest of its life; the regime-transition tests bound it | file, arm results | evaluate at `now` instead (one argument) |
| D8 | The overnight rule in closed form (I4): **`interval_for` evaluated at the latest instant at or before `fair_ts` at which it returns a finite value, walking back in `exec_period_s` steps within the capture window; if none exists the row takes the weekday off-window value 1,000 s and is labelled `overnight_unanchored`**; a missed fetch never extends its own deadline | pre-loaded (draft §4) for the rule, I4 for the form | the draft states both halves; "the last finite allowance" was not evaluable by a test or a reader, and this is | an unanchored row cancels at 1,000 s instead of an unknowable earlier value; those rows are counted and labelled | arm results | change the walk-back rule in `arms.py`; the fallback value is one constant |
| D9 | The first cohort is **eight games, sport-balanced, 24-120 h to kickoff**, chosen by `sha256(seed \|\| game_id)` | pre-loaded (draft §4, §8) | the stratum is not scarce (15 NFL / 56 NCAAF in that window today) and a deterministic hash is auditable | a small cohort cannot estimate full-universe capacity, which §1.6f already refuses to claim | run scope | freeze a new manifest with a larger cohort |
| D10 | The administrative bound is **14 calendar days** after activation, on the first fully followed cohort | pre-loaded (draft §4, §8) | an exploratory feasibility deadline, explicitly not a significance stopping rule | a cohort with a long tail is cut off; censored rows are labelled, not dropped | run scope | freeze a new manifest with another bound |
| D11 | No `EXECUTOR_VERSION` / `PRICING_VERSION` bump | model | nothing a version boundary separates changes: **§7 item 3 lists all seven production-code edits with the reason each is inert** - a defaulted field, a dead branch, a CLI registration, a no-op reservation check while `veto_pacing_profile is None`, a claim order behind the same setting, a `register_pass` line inert unless `exp_observer_enabled` and a frozen `exp_run` exist, and settings declarations (I10) | if a branch were reachable, the boundary would be missing; §5's equality tests and §3 row 2 are the guards | file | bump before the deploy |
| D12 | The experiment package is **`harness/experiments/execution_viability/`** with **fourteen** small modules (`veto_profile.py` included, M4), and no production module imports it | model (draft §1 "proposed as") | keeps the blast radius of a defect inside the package and makes the import direction testable | a layout change later costs a rename | files | rename the package |
| D13 | The CLI is one group, **`harness exp`**, with seven subcommands added as each lands | model (draft §1 "add a CLI entry only once its implementation exists") | matches `variants`, `migrate`, `futures` and `parlay`; keeps `policy-compare` untouched | a name collision with a later group | file | rename the group |
| D14 | The prospective observer is a **job inside `app-research`**, not a compose service | model (draft §1 leaves it open) | no new container, no new bind, no new restart surface, and the research worker already owns metered outbound calls and the spend path | the observer shares a process with the veto worker, so a slow call delays veto passes; §4.3's yield guard and the dormancy rule bound it | Omarchy container (existing) | move it to its own one-shot CLI invocation under the same limits |
| D15 | The checkpoint format is **one `exp_checkpoint` row per `(run, arm)`** carrying a JSONB state blob plus the manifest hash | model | resume must be refused on a manifest mismatch, which needs the hash beside the state; JSONB keeps `SimState`'s lists (`prints`, `buckets`, `trade_ids`) without a second schema | a very long run's blob grows; the horizon pruning in `SimState` already bounds it | DB additive | write the blob to the file tree instead |
| D16 | The pacing profile ships **dormant**, and its activation is the user's dated decision (§0.14c) | model (reconciling U10's "bounded implementation authorized" with the pacing brief's "the user rules before any code") | building and preflighting the profile is authorized work; **changing when the cap binds changes H9's decided population**, which is a measurement amendment under §6.7 | the near-kickoff coverage gap persists until the user rules; the preflight quantifies exactly what is being lost | file, settings | the user's yes sets the setting; reversal is unsetting it |
| D17 | Episode rules reuse 6D §1.7(b)'s `gap_rule_s = max(600, 3 x cadence_in_force)` and store the rule per row | model (draft §7 "publish the exact episode-opening/closing and re-entry rules before the run") | two milestones counting "opportunities" differently would be a reporting trap; storing the rule allows a re-count without new data | another rule would group differently | file, report | recompute from the stored rows with another rule |
| D18 | The forecast publishes **five named scenarios** and refuses to print a date when accrual or economics is unidentified | model (draft §7; review §7 item E "scenario ranges and explicit unknowns") | the review explicitly withdrew the pooled extrapolation | a reader wanting one number gets a range | report | add a scenario |

## 9. Task decomposition for the plan writer

The outline's task numbers are kept. Judgment-heavy tasks (**opus** implementer and **opus** reviewer): T2, T3, T4, T7, T8.
T1, T5 and T6 are opus-implementer, sonnet-reviewer at the controller's discretion; T6's spend path is opus on both sides
because it touches `reserve_spend`.

**Wave 1 - independent starts (may run in parallel, no shared file):**
- **T1. Freeze the experiment contract and isolate its capabilities** (§1.1, §1.2, §4.7, §0.4-§0.6). T1 owns C2's whole
  privilege boundary on the code side: `Settings.exp_db_password_file` and `exp_database_url()`, the `EXP_DB_ROLE` constant, the
  `has_table_privilege(current_user, 'orders', 'INSERT')` probe raising `IsolationError` **at session open** in both
  `source.reader()` and `ExperimentWriter`, the fail-closed behaviour when the secret is absent, the compose bind, and **the runbook page `docs/runbooks/experiments.md`** carrying §4.7's exact
  `CREATE ROLE`/`GRANT`/`REVOKE` text, the secret-placement command and the `has_table_privilege` verification (the same text
  the loop adds to the roadmap's `## User-side TODOs`). T1 **does not block** on the user running it: the tests use a role the
  test database creates, and every run without the grant fails closed.
  *Files:* `harness/experiments/__init__.py`, `harness/experiments/execution_viability/{__init__,manifest,source,storage}.py`,
  `harness/cli.py`, `harness/config/settings.py` (**all** the milestone's new fields, declared once with inert defaults -
  `exp_db_password_file`, `exp_observer_enabled = False`, `veto_pacing_profile = None` and the `EXP_*` bounds - so no later task
  reopens the file and wave 1 has no shared file), `docker-compose.yml` (one read-only bind on `app-research`),
  `docs/runbooks/experiments.md`, `harness/execution/policy.py` (caption only),
  `tests/test_exp_isolation.py`, `tests/test_exp_manifest.py`,
  `tests/test_exp_cli.py`. *Depends on:* nothing; inspect latest `main` and active worktrees first.
- **T6. Pace the shadow veto independently** (§1.8, §0.8, §0.14c).
  *Files:* `harness/research/veto.py`, `harness/research/spend.py`,
  `harness/experiments/execution_viability/veto_profile.py`, `tests/test_veto_pacing.py`, `tests/test_research_spend.py`
  (it **reads** `Settings.veto_pacing_profile`, which T1 declares; it does not edit `settings.py`).
  *Depends on:* T1 for the package only (its profile module may land after T1's `__init__`); independent of T2-T5 and of every
  arm result. **Independently releasable** and independently mergeable; ships dormant.
- **T5. Investigate book inactivity separately from feed failure** (§1.7).
  *Files:* `harness/experiments/execution_viability/bookhealth.py`, `tests/test_exp_bookhealth.py`. *Depends on:* T1; may use
  T2's capture when it exists, but its four fixtures do not need it.

**Wave 2 - the spine (serialized over `adapter.py` and the schema):**
- **T2. Capture the inputs and reproduce a baseline lifecycle** (§1.3).
  *Files:* `harness/experiments/execution_viability/{capture,adapter}.py`, `tests/test_exp_capture.py`,
  `tests/test_exp_adapter.py`, `tests/test_exp_baseline.py`. *Depends on:* T1.
- **T3. Carry independent arm state and conserve liquidity** (§1.4, §1.5, §2). Declares **all eleven** `exp_*` tables (I5):
  the nine of revision 1 plus `exp_mismatch` and `exp_limitation`, with `exp_observation` carrying `body_path`, `body_sha256`,
  `credits_last` and `credits_remaining` (T7 fills the last two); the raw bodies stay in the file tree and **no** raw-body
  table is created.
  *Files:* `harness/db/models.py`, `harness/db/schema.py`, `migrations/versions/00NN_phase6d1_exec_viability.py`,
  `harness/experiments/execution_viability/{adapter,storage,liquidity}.py`, `tests/test_exp_state.py`,
  `tests/test_exp_liquidity.py`, `tests/fixtures/exp_print_10_contracts.json`, `tests/test_alembic.py`. *Depends on:* T2.
  Owns every schema edit of this milestone; T4 and T7 add no DDL.

**Wave 3 - the comparison and its reporting:**
- **T4. Implement the initial holding comparison and reporting contract** (§1.6a-d, §1.9).
  *Files:* `harness/execution/policy.py` (one defaulted field), `harness/execution/plan.py` (one branch),
  `harness/experiments/execution_viability/{arms,episodes,report}.py`, `tests/test_exp_arms.py`, `tests/test_exp_report.py`,
  `tests/test_exp_episodes.py`, `tests/test_exec_plan.py`. *Depends on:* T3. Serialized with T3 over `adapter.py`.

**Wave 4 - the prospective cohort (gated as §4.6 states):**
- **T7. Collect the bounded faster-observation cohort** (§1.6e-i, §4.2, §4.6). Every observer call parses the provider's
  credit headers with `harness.feeds.odds_api.parse_credit_headers` and writes `credits_last` / `credits_remaining` onto the
  `exp_observation` row (I9), evaluates `tick.py:1411`'s guard on that balance before the next call, and writes no
  `source_state` row; it reuses `harness/pricing/fair.py`'s pure arithmetic and calls no `compute_*_fair_values` entry point
  (I12).
  *Files:* `harness/experiments/execution_viability/observer.py`, `harness/research/worker.py` (one `register_pass` line),
  `tests/test_exp_observer.py` (it reads `Settings.exp_observer_enabled` and the `EXP_*` bounds, which T1 declares). *Depends on:* T4, T5 and §4.6's steps 1-5. The code
  may land and be released before activation; **activation is a separate, journaled step** and C is reported unavailable rather
  than bought if the budget or coverage preflight fails.
- **T8. Finish the cohort and make the decision reviewable** (§1.10, §1.11).
  *Files:* `harness/experiments/execution_viability/{forecast,decision}.py`, `tests/test_exp_forecast.py`,
  `tests/test_exp_decision.py`, the report under `docs/superpowers/autopilot/reports/`. *Depends on:* T4-T7; an explicitly
  unavailable arm is a permitted input.

**Last task - verification rows (§3)**: `docs/superpowers/autopilot/verify.md` only, after every other task's behaviour exists.

## 10. Notes for the plan writer

- The replay clock is **resolved, and it is not the loop clock** (§1.3d, C1). `exec.loop_ms` is sampled every
  `metric_sample_s = 60` s, so the 1,158 rows of live fact (f) are samples, not loops - the executor stepped roughly
  3,500-5,000 times that day - and a replay stepping only those samples would thin the decision clock about fourfold. T2 steps
  `capture.resolve_instants()`: the union of `orders.placed_at`, `order_events.ts`, `intents.created_at`, `fills.filled_at`
  and the samples. The sample's `ts` is `_locked_step`'s `now`, the decision instant itself (`loop.py:717` writes
  `record_many(..., ts=now)`, I1); `ts - value_ms` is a labelled sensitivity only. T2's first validation line is the resolved
  instant count printed beside the live loop estimate, with the `loop_spacing_unreconstructable` limitation row written.
- The featured Odds API endpoint is **per sport**, not per event (`OddsApiClient.fetch_featured(sport)` with
  `FEATURED_MARKETS`), so the cohort's size does not bound arm C's credit cost and the eight-game restriction is about which
  observations are *used*, not about what is fetched. A plan task that assumes per-game fetching will size the budget wrongly.
- Production is one migration behind `main` (live fact (g)); T3's migration must chain from `0014_orders_intent_index`, and the
  release that carries it is a **full** release under R4, so the plan should not assume an app-only window.
- `harness/execution/loop.py` is 2,553 lines and is **not** edited by this milestone; every executor behaviour the arms need is
  reached through `plan.py`, `fills.py`, `book.py` and `store.py`. A plan task that opens `loop.py` has drifted.
- Rows 78, 79, 84, 86 and 87 keep their existing vehicles and priority, and 6D's acceptance, recording, timed verification,
  coverage, expiry-backlog and storage duties keep theirs. This milestone waits on none of them and blocks none of them.

## Rulings

The controller's rulings on the revision-1 review (2026-09-18 13:54 CT), copied verbatim and applied in the sections named
beside each one; the review is `.superpowers/sdd/results/design-6d1-review.md` and the rulings file is
`.superpowers/sdd/plan-next-phase6d1/design-rulings.md`. Format: `Ruling <id>: <decision> - <why> - <cost if wrong>`.

- Ruling C1: accepted - §1.3d states that per-loop instants are retained nowhere (`exec_heartbeat` is one overwritten row; `exec.loop_ms` is sampled every `metric_sample_s = 60` s, so the live fact "1,158 loops in 24 h" is 1,158 samples and the executor stepped roughly 3,500-5,000 times); `capture.resolve_instants()` is defined as the ordered union of the retained decision-bearing stamps (`orders.placed_at`, `order_events.ts`, `intents.created_at`, `fills.filled_at`) plus the `exec.loop_ms` samples; the unreconstructable spacing is a named `exp_limitation` row and the resolved instant count is printed beside the live loop estimate; `clock_mode = ideal_grid_15s` stays the labelled sensitivity; §1.3's expected result and §1.3f's acceptance are restated as action parity **at the retained action instants** (the stamps are the instants at which recorded actions occurred) and no parity claim is made over the thinned opportunity clock; the header live fact (f) and §10 bullet 1 are corrected - why: a 4x-thinned decision clock delays every arm's reaction and would make the headline A/B comparison a sampling artefact, the exact class of defect 6B's D15 suspends parity for - cost if wrong: an acceptance narrower than the draft's, stated as such, with the sensitivity run as the falsifier.
- Ruling C2: accepted - the `exp_*`-in-`harness` storage stands (D3) and gains the privilege boundary: one least-privileged role `harness_exp` with `SELECT` on the production tables and `INSERT`/`UPDATE` only on `exp_*` (and `USAGE` on the sequences they own), created by a single `CREATE ROLE` + `GRANT`/`REVOKE` step **the user performs** (the loop writes the exact SQL into the runbook and lists it in the roadmap's User-side TODOs; no CREATEDB, no second database, no second backup target); both capabilities connect through one new settings field read from a new conditional secret file (the Makefile's existing conditional-push pattern; never printed, never read by the loop), and `source.reader` and `ExperimentWriter` each raise `IsolationError` at session open when `has_table_privilege(current_user, 'orders', 'INSERT')` is true or the secret is absent, so every run **fails closed** until the grant exists and T1 does not block on it; the in-process refusals and the import test stay as defence in depth; §4.6 gains the grant as step 0 and §3 gains the privilege read-back; D3's "cost if wrong" is rewritten - why: the adopted draft requires "reject a production destination, and fail closed if isolation cannot be established", and an in-process guard under the production role satisfies neither; a stray write during a run would be near-indistinguishable from production rows and invariant 5 forbids deleting it - cost if wrong: one user command and one secret file before the first run; a listed secret file is conditional, never blocking (gate 1).
- Ruling I1: accepted - §1.3d's convention sentence becomes "the sample's `ts` is `_locked_step`'s `now`, the decision instant itself"; the `ts - value_ms` reading survives only as a labelled falsifier sensitivity; §10 bullet 1 and §5's capture tests are reworded - why: the reviewer read `_locked_step` and `telemetry.record_many(..., ts=now)` - cost if wrong: none.
- Ruling I2: accepted - §1.6(a) writes the branch out: when `policy.cadence_allowance is not None`, `allowance = max(int(cfg["stale_s"]), interval + s.tick_budget_s)`, **replacing** `market.stale_allowance_s` rather than widening it; the regime table (180 s burst) is the authority - why: the draft's formula is `max(variant.stale_s, scheduled_interval + tick_budget_s)` - cost if wrong: none.
- Ruling I3: accepted - §1.3(a) and §1.6(b) state that the kickoff list handed to `interval_for` is reconstructed as of the evaluation instant from retained schedule history (`games` row versions where the capture keeps them, else the earliest retained value), and that a slice whose kickoffs cannot be reconstructed as-of is labelled in `exp_limitation` and excluded from B's regime-sensitive rows - why: a revised kickoff is lookahead §1.3(b) forbids - cost if wrong: fewer regime-sensitive rows, labelled.
- Ruling I4: accepted - §1.6(d)/D8 define the overnight rule in closed form: "`interval_for` evaluated at the latest instant at or before `fair_ts` at which it returns a finite value, walking back in `exec_period_s` steps within the capture window; if none exists the row takes the weekday off-window value 1,000 s and is labelled `overnight_unanchored`" - why: testable, and matches the draft's "no scheduled fetch is not infinite validity" - cost if wrong: an unanchored row cancels at 1,000 s instead of an unknowable earlier value; counted and labelled.
- Ruling I5: accepted - §2 declares `exp_mismatch` and `exp_limitation` with shape, invariant query and cardinality bound (eleven tables); raw response bodies live in the hashed file tree only, `exp_raw_body` is removed from the design and `exp_observation` carries the body's path and sha256; §3 row 1 and `EXP_METADATA` say eleven - why: the migration, `create_all`, the allow-list and the catalogue test must agree - cost if wrong: none.
- Ruling I6: accepted - §3 row 2 becomes the pre-run and post-run counts of `orders`, `fills`, `intents` and `signals` rows stamped inside `[warmup_start, observation_end]` (journaled before and after each run; equal within the live executor's own placements, verified by `config_hash` and `client_order_id` prefix being production's) plus C2's privilege read-back; the vacuous join is deleted - why: the row as written cannot fire - cost if wrong: none.
- Ruling I7: accepted - §3 row 4 reads `select day, sum(usd + usd_reserved) from research_spend where day >= :monday group by 1` with the ISO-week total beside it - cost if wrong: none.
- Ruling I8: accepted - `exp_veto_coverage` is a parameterless view joining `veto_decisions -> veto_queue -> games` (the authoritative join: `veto_queue` carries `game_id`), day filtered in the caller's `where` - cost if wrong: none.
- Ruling I9: accepted with one modification - (a) as the reviewer states: the observer refuses a call whenever the recorder's own guard would (`remaining < credits_watch_fraction * odds_monthly_credits`, tick.py:1411's expression) or its run cap is reached; (b) **not** as a `source_state` exception: no member of the writer's refusal list is lifted and `harness_exp` gets no privilege on `source_state`; instead every observer call records the provider's `x-requests-last`/`x-requests-remaining` headers (`parse_credit_headers`) in `exp_observation`, so the one shared aggregate is the provider balance both processes read on every call, and §3 row 5 reads `recorder.credits_remaining`'s latest sample beside `exp_observation`'s latest `credits_remaining` and the observer's per-run sum; `sum(credits_used)` is deleted from the design - why: the draft's "aggregate quota accounting" is the provider balance, and widening the role grant to a production table for a counter would weaken C2 - cost if wrong: the recorder's metric lags the observer's spend by at most one observer call (3 credits).
- Ruling I10: accepted - §7 item 3 lists every production-code edit (`policy.py` field, `plan.py` branch, `cli.py` help text, `research/spend.py` reservation check, `research/veto.py` claim order, `research/worker.py` `register_pass` line, `config/settings.py` fields) and states per edit why no version boundary is needed: the `register_pass` is inert unless `exp_observer_enabled` and a frozen `exp_run` exist, and the reservation check is a no-op while `veto_pacing_profile is None`; D11 cites the list - cost if wrong: none.
- Ruling I11: accepted; D16 stands - §1.11's veto bullet becomes "implemented, preflighted against stored arrivals under unchanged caps, shipped dormant, with the §0.8 amendment record and boundary fields prepared; the boundary instant is written at the user's activation", and §1.11 states that the dormant state closes the milestone's veto component - why: journal 272 ("the user rules before any code" on when the cap binds) and U10's bounded implementation are both honoured; activation changes H9's decided population and is the user's - cost if wrong: the near-kickoff coverage gap persists until the user rules, quantified by the preflight.
- Ruling I12: accepted - §1.6(e) names the pure computation the observer reuses (the consensus/direct-fair arithmetic and `stale_allowance_s` derivation) and states that no `compute_*_fair_values` entry point is called and nothing writes `fair_values` - cost if wrong: none.
- Ruling I13: accepted - both draft sentences are restored verbatim: (a) "A slower production loop caused by historical churn remains part of recorded-clock replay; a prospective equal-resource run can test the benefit of reduced workload separately" in §1.3(d); (b) "Resting venue depth and trade/delta reconciliation are handled consistently with the shared simulator" in §1.5, with the sentence that `SimState`'s `prints`/`buckets` ledger and `RECON_HORIZON` are not re-implemented above the simulator - cost if wrong: none.
- Ruling M1-M5: all taken in revision 2 as the reviewer states them (M1 the `"weather"` label; M2 both `ExecSettings` and `Settings` names; M3 the sentence that B is tighter in the burst regime and identical in the game window, so its effect is the off-window regimes; M4 fourteen modules; M5 the `exp_observation` unit restated) - cost if wrong: none.
- Ruling (live facts): the controller appends a correction line to `.superpowers/sdd/plan-next-phase6d1-live-facts.txt` (the `exec_loops_24h` row counts `exec.loop_ms` samples, not loops) and the `plan-next` journal entry quotes the corrected reading - cost if wrong: none.
- Ruling (round): one design-review round (plan-next step 2); revision 2 is followed by the controller's self-review (step 3) and the 3a audit, not a second reviewer dispatch - cost if wrong: a defect the plan review (step 4, opus reviewer) catches instead.
- Ruling (self-review, step 3, 2026-09-18 14:13 CT): two inline corrections by the controller - (1) the secret is placed by the user directly in the Omarchy runtime's `secrets/` like `anthropic_api_key` (`docs/runbooks/research.md`; `scripts/release-omarchy.py` copies no secret), so the Makefile's retired NAS scp loop is not edited and `Makefile` leaves T1's `Files:` (§4.7, §7 item 7, §7 item 12, §9 T1, §1.1b); (2) §1.6(a)'s prose now says B replaces the allowance, matching its own code block and the regime table - why: the addendum must describe the host that exists - cost if wrong: none.
