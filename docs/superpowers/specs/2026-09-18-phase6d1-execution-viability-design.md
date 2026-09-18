# Phase 6D.1: execution viability experiment

Date: 2026-09-18. Status: scope adopted under roadmap U10; **draft for plan-next's required design/conformance review**. This is not a completed design-review record. The controller must complete the standard design and plan reviews before implementation dispatch; implementation and experiment runs are pending.

User instruction, verbatim: "Ok, lets apply the recommendation. Should this be separate from the existing code? Should it be it's own milestone?"

The recommendation being applied is the [live-data review](../autopilot/reports/2026-09-18-fill-starvation-review-confirmed.md). The architecture and defaults below are implementation choices made in response, not quotations of choices the user individually selected. This milestone delivers an exploratory paper experiment and a retain/revise/stop recommendation. Production adoption of a selected holding policy remains the dated decision required by 6D §0.15a; formal confirmation dates remain 6F/R7's responsibility.

## 0. Roadmap amendment and purpose

6D.1 is a separate milestone between 6D and 6F, in the existing repository. It closes the gap between 6D's declared comparison and its implementation: `execution/policy.py::compare` does not carry resting orders or simulate alternative fills. Its historical fill column must not be used to select a holding policy or forecast a gate date.

6D.1 owns:

1. A comparison that carries orders, queue state, prices, capacity and outcomes through time.
2. A diagnosis of fair age, quote age, transport health and book inactivity as separate quantities.
3. A bounded prospective paper experiment comparing freshness tolerance with faster observation.
4. Independent veto pacing across kickoff windows and the week.
5. A feasibility forecast and a decision report, including negative and insufficient-evidence outcomes.

6D continues its existing coverage and acceptance work. 6E continues environment acceptance. 6F consumes this milestone's capability evidence and policy recommendation before opening its formal prospective period; its sample forecast begins here as a diagnostic. No existing gate definition, benchmark family, hypothesis threshold, frozen variant id, bankroll, spend ceiling or release ruling changes. In particular, the September 21 registration cutoff is not silently extended: a later new registration is exploratory unless separately amended under the existing rules.

Implementation, bounded read-only extraction, and isolated paper operation within existing budgets are covered by U10 and the standing Phase 6 authorization. Complete the manifest and measurement record before activating the prospective collector or changing veto sampling; record the actual activation boundary. This is not permission to adopt a different production holding policy, bypass current release holds, or start live trading.

## 1. Separation of code and state

Use an ordinary development branch/worktree and an additive package, proposed as `harness/experiments/execution_viability/`. Add a CLI entry only once its implementation exists. Use the current dependency set.

Reuse `strategy.run`, pricing/fee functions, `execution.plan.plan_actions`, `execution.fills.simulate_fills`, book reconstruction, and `report.stats.cluster_ci`. Put orchestration, experiment manifests, allocation bookkeeping, storage adapters and experiment reports in the new package. Do not copy the pricing or queue algorithm into a second implementation. A required shared-core extraction must preserve default behavior and have its own small validation step.

The current `replay.py::_execute` runs a regular clock grid and owns database transactions. It is not automatically a recorded-clock, read-only policy runner. Implement an adapter around the shared decision and fill functions; prove its behavior before reusing a larger executor path.

Use three distinct capabilities:

| Capability | Allowed access |
|---|---|
| Production source reader | Read-only, bounded extraction; source connection cannot write |
| Experiment runner | Local immutable capture plus an explicitly named scratch database or file-backed state; no production writer or venue order gateway |
| Prospective observer | Existing public/read-only source clients; metered reads within shared limits; writes experiment observations and provenance to isolated storage |

Each run and arm has independent order, queue, position, capacity and cursor state. Experiment records never enter production `orders`, `fills`, `ledger`, registered-variant performance or gate input tables. A `replay` flag by itself is insufficient isolation. Validate source and destination identity, reject a production destination, and fail closed if isolation cannot be established.

The faster observer must not make production fair/signal queries see newer observations accidentally. Store its extra odds, fairs and decisions separately. Preserve raw responses for audit. No new production WebSocket subscription or recorder restart is needed for historical work.

## 2. Reproducible inputs and historical proof

Every immutable run manifest records:

- Run id, source-code hash, schema and simulator versions, baseline settings and copied variant configurations, arm definitions and hashes.
- Source capture hashes; order/run/fill boundaries; placement start/end, warm-up start and observation end; actual extraction times and exclusions.
- Economic portfolio identity, shared operational slot limit, clock mode, market/game cohort, deterministic selection seed and opportunity definition.
- Scheduled versus available observations, timestamp semantics, source-credit/request budget, resource limits, markout horizons, missingness policy and review deadline.

Import all inputs needed to reconstruct the selected markets: books, subscription/session boundaries, prints, fair/gap/signal observations, schedule changes, loop instants and initial state. First prove a small representative post-repair slice, then include an off-window span, an overnight boundary, a busy window, a gap/recovery, and a capacity-bound interval. Inspect actual coverage before choosing the range; do not silently cross simulator or executed-population changes.

Historical decisions use information available at that instant. Distinguish venue event time from local receipt/availability time. When availability cannot be reconstructed, label the assumption and affected slice; do not claim exact live parity. A later REST backfill is not an earlier decision input.

For recorded-clock parity, resolve what `exec.loop_ms.ts` timestamps and reconstruct the corresponding decision instants before replay. An ideal 15-second schedule is a separate sensitivity result, never silently substituted. A slower production loop caused by historical churn remains part of recorded-clock replay; a prospective equal-resource run can test the benefit of reduced workload separately.

Reconstruct outstanding orders before the placement window, or warm up from before the earliest contributing placement. Evaluate new placements only in the stated window, then follow their full lifecycle through expiry and due 30-minute/closing outcomes. Mark remaining live/censored orders and missing outcomes explicitly. A three-day input slice with 33-hour typical fill delay is not a complete cohort by default.

Acceptance: baseline actions, prices, watched fills, cancellation/expiry times and shared-capacity exclusions agree with the matching source slice wherever its input history is reconstructible. Publish every mismatch and cause. Missing history is an explicit limitation, not a passing parity result.

## 3. State, liquidity and resource accounting

Keep one active order per variant/market/side as the baseline does. Carry partial fills, queue ahead, expiry, repricing, dirty intervals and exposure forward; do not permanently block a key after its first placement or reset capacity each instant. Repricing loses priority according to the same shared model. All baseline cancellation checks remain enabled except the precise parameter an arm names.

Retain all three currently executed variants in baseline resource accounting; the 150-order operational pool is shared inside each arm. Report gate and primary separately. Giving each variant a fresh 150-slot pool is not baseline parity.

The current variants represent separate hypothetical strategies. Define an economic portfolio as `(run, arm, variant)` and enforce print-volume conservation within it. Alternative arms and separately labelled variant portfolios may reuse the same tape; their contracts or returns must never be summed as one executable portfolio. A future combined-variant portfolio needs its own common liquidity allocator.

Within each portfolio, a source print can supply at most its available contracts across simultaneous hypothetical orders, at all eligible prices. Cancels, re-placements, partial fills, retries and restarts do not replenish that quantity. Resting venue depth and trade/delta reconciliation are handled consistently with the shared simulator. Include a fixture reproducing the observed 10-contract print attributed to 45 overlapping counterfactuals: it may support at most 10 contracts in one executable portfolio. Keep independent legacy `no_watcher` histories only as labelled diagnostics, never additive outcomes.

Runs are deterministic across chunk sizes and checkpoint/restart. Each checkpoint includes event cursor, liquidity consumption, book/queue state, order state, exposure and manifest identity. Isolate replay work from recording and current execution; use bounded batches and cooperative resource limits. Do not move or disable the existing production `no_watcher` worker as a prerequisite. A later migration of that workload requires its own parity evidence and version boundary.

## 4. Initial experiments

Begin with three arms. Avoid searching the full six-policy grid or changing quote aggression and capacity simultaneously.

| Arm | Fair observations | Executor fair-age allowance | Purpose |
|---|---|---|---|
| A: baseline | Ordinary recorded cadence | Existing rule, normally 220 s for featured lines | Reproduce the current posture |
| B: cadence-aware holding | Same observations and strategy decisions as A | `max(variant.stale_s, scheduled_interval + tick_budget_s)` | Isolate predictable fair-age gaps |
| C: faster observation | Prospective featured observations every 120 s in the frozen observation window | Existing rule, normally 220 s | Test sustained freshness without increasing permitted fair age |

For B, the normal featured budgets are 1,000 s on weekdays, 400 s at weekends, 220 s in a game window and a 180 s floor during a 20 s burst. Derive the interval from the recorder's actual sport-wide schedule, not each order's time-to-kickoff alone. Test regime transitions. When polling is suspended overnight, retain the last finite allowance for that observation; age continues increasing until the stale rule cancels. Never interpret no scheduled fetch as infinite validity. A missed fetch does not extend its own deadline.

B overrides only the executor's calculated-fair age allowance. It does not widen the strategy's source-quote-age filter, suppress repricing, disable edge decay or change venue-move checks. These distinctions must appear in the manifest and tests. The existing `rest_to_expiry` parameter bypasses those checks and is not B.

C requires new observations. Historical tape cannot establish what an unrecorded faster fetch would have said. On the prospective common capture, A and B receive only their ordinary-cadence information; C receives the additional observations. All arms use the same cohort, venue tape, resource accounting and predetermined clock policy. Outcome measurement uses the common observer and a fixed benchmark schedule, not each arm's own observation frequency.

Initial prospective scope: one cohort of at most eight eligible games, up to four NFL and four NCAAF, with kickoff 24-120 hours from cohort freeze. Select by deterministic hash of a recorded seed and canonical game id within sport from confidently matched direct-fair markets. Record unavailable strata instead of filling them with convenient winners or anchor teams. Include each selected game's eligible markets under the frozen rules. Keep an all-market historical parity run separate from this restricted-cohort experiment; the latter does not estimate full-universe capacity or a 40-game gate date directly.

Freeze the game list, observation window, start time, placement stop, finite per-arm settings, data-quality tolerances, outcome schedule, cost/resource envelope and maximum follow-up before opening new outcome estimates. Default administrative review: the first fully followed cohort, no later than 14 calendar days after activation. This is an exploratory feasibility deadline, not a significance stopping rule or a revised confirmatory period. If fewer games are eligible or collection is incomplete, publish insufficient coverage; do not extend after inspecting favorable effect estimates.

Before activation, cost the actual source endpoints and retained data against the current credit/spend/storage limits. Extra Odds API reads share the recorder's aggregate quota accounting; a second process with an independent allowance is not acceptable. If the proposed eight-game/120-second scope does not fit, reduce scope or leave C unrun and report the constraint. Do not buy a higher tier or raise a cap. Future cohorts need a newly frozen manifest; no silent adaptive universe expansion.

## 5. Book health and outcome freshness

Record separately: source quote timestamp, successful fetch/transport timestamp, fair-computation timestamp, per-ticker last event, subscription continuity, session/reconnect boundaries, observation gaps and revalidation results.

First investigate the post-repair `event_age` intervals. The code may report age because a ticker was inactive or because its book is genuinely incomplete. Verify available heartbeat/session/sequence evidence and compare a bounded read-only book snapshot where appropriate. Unknown continuity stays unknown. A snapshot cannot prove no intervening changes or magically preserve queue priority across a gap.

The initial A/B/C comparison keeps production book-dirty semantics. Publish hypothetical continuity-aware classifications alongside them without changing gate eligibility. A changed health policy is a later named arm with its assumptions frozen before use; it must not be bundled into B or C. A winning assumption cannot retroactively clean historical rows.

Obtain markout and closing observations on a common schedule for all arms. Expose source age and missingness at fill, 30 minutes and close. Do not forward-fill an old quote into a claim of fresh contemporaneous value. Record matured outcomes separately from censored or unavailable ones.

## 6. Independent veto pacing

Deliver this as an independently releasable task within 6D.1; it does not wait for evidence of reduced order churn. Veto remains shadow-only and does not alter the experiment's placement decisions.

Keep every candidate and its evaluation disposition. Introduce sampling/claim order based on game, market type, kickoff window and meaningful feature/news change, with explicit cache validity and invalidation. Same-key resting orders are evidence of repeated context, not an unconditional reason to suppress new news. Preserve both source and decision timestamps and label all skipped, cached and evaluated rows.

Freeze a pacing profile before changing sampling: daily/hourly kickoff reservations and a weekly allocation keyed to the scheduled NFL/NCAAF slate, including Sunday/Monday as appropriate. Define release rules for unused reservations and deterministic ordering within each stratum. Day/week caps remain $25/$150, including annotations, parlays and paired model calls; use the existing atomic reservation path. Preflight the exact profile against stored queue arrivals and worst-case call costs before activation. Historical model answers cannot be reused as if the new profile had asked different historical questions.

Record the sampling amendment and activation boundary. Report coverage and model outcomes by sport and kickoff window, sample eligibility and selection probabilities where random sampling is used. Do not compare pre/post veto value without accounting for the changed population. This milestone selects a concrete budget-feasible profile during the task; the recommendation already authorizes that bounded work, not a new cap or model provider.

## 7. Reporting and decision

The primary descriptive economic unit is a defined opportunity episode within each arm/variant portfolio; publish the exact episode-opening/closing and re-entry rules before the run. Keep order-weighted results for comparison with existing reports, with market-side- and game-weighted sensitivity beside them. None replaces a frozen gate estimator. Report concentration, including maximum contribution by game and repeated liquidity attribution.

Every result table separates registered historical performance from exploratory arm results. Report per variant: eligible games and markets, scheduled/completed observations, placements and episodes, unique filled orders and games, partial fills/contracts, clean and dirty resting time by cause, capacity exclusions, queue tenure/repricing loss, source freshness, mature net CLV/markout, adverse drift, game-clustered uncertainty, missing/censored outcomes and resources consumed.

Bring forward the sample-accrual forecast for the gate variant alone. Separate observed watched fills, stateful exploratory estimates and conditional scenarios. Include distinct games, both sports, clean-book eligibility, outcome maturity and uncertainty. Do not turn 770 independent counterfactual tracks into a portfolio forecast. Do not announce a gate date when accrual or economics remains unidentified.

Alongside execution, publish the current coverage and result status of the charter's mispricing map, convergence lag and H9 sample. Snapshot-only hypotheses continue; no optional expansion is needed for 6D.1.

Completion evidence:

- Proven stateful baseline behavior and isolation, with mismatches explicitly resolved or bounded.
- Comparative paper results for feasible arms, or an explicit reason an arm could not be measured; zero invented faster-history observations.
- A cause-specific book-health diagnosis and fresh-outcome coverage report.
- Veto pacing implemented and verified within unchanged caps, with a recorded population boundary.
- A forecast and dated **retain / revise / stop / insufficient evidence** recommendation. Positive returns or a passing go-live gate are not completion requirements.

The user then chooses any production holding policy under the existing adoption rule. 6F freezes its valid period and confirmation protocol separately. An infeasible or economically unproductive maker configuration can close this milestone with a supported negative finding.

## 8. Delivery and scheduling

The [delivery plan](../plans/2026-09-18-phase6d1-execution-viability.md) is the task breakdown. First take the bounded manifest/state-adapter work; complete book-health investigation and veto pacing as separate units. Required recording, release verification, fix 86's scheduled evidence, storage deadlines, 6D coverage and 6E acceptance keep their existing priority. Do not hold them behind a comparison report.

Implementation choices made on the user's behalf: milestone number 6D.1; same repository/shared core; isolated experiment storage; initial A/B/C arms; 120-second faster-observation proposal; eight-game/sport-balanced first cohort; 14-day administrative bound. The manifest must resolve actual activation times, cost envelopes and coverage tolerances from measured conditions before collection. None of these choices is an assertion that the experiment has already run.
