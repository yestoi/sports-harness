# After Fable: prepare the implementation loop

Status: future handoff procedure. This file does not launch the loop, insert a phase, amend controller authority or certify the design ready to build. The owner requested preparation now and implementation readiness after the Fable design session.

## 1. What the Fable session must leave behind

Use a dedicated dated design-output directory in the repository or return an equivalent exported package. Prefer durable files to a conversation-only or inaccessible canvas result.

| Deliverable | Required content |
|---|---|
| Design specification | Final goals, navigation, user journeys, chosen density/type/color hierarchy, interaction rules, and explicit exclusions |
| Responsive screens | Phone/laptop overview, recommended tickets, DraftKings handoff/return, tracked cross-game ticket, tracked SGP/player props, game detail, completed ticket/review, and research/system access |
| State coverage | Empty/loading/stale/disconnected/partial/error states, no eligible idea, no paper position, removed/repriced market, unmatched player, provisional threshold, stat correction, void/push, pending/confirmed settlement |
| Component definitions | Tokens, type scale, spacing/density, status vocabulary, tables/charts, touch/keyboard behavior, focus/reduced-motion rules; needed assets and their sources |
| Interaction prototype | The critical journey from generated proposal through manual placement confirmation and live tracking; it can use clearly marked demo fixtures |
| Data mapping | Each screen field/action mapped to a recorded source, derived value, user entry or missing/new capability; source-time and freshness semantics |
| Decision record | Choices made by Fable within scope, user decisions, unknowns/limitations, old UI rules intentionally superseded |
| Implementation handoff | Final output paths, dependencies, intended verification examples, source contracts requiring inspection, and unresolved release blockers |

Fable can choose the exact file names. The planner should record those paths and content hashes so later tasks build the reviewed output rather than a remembered conversation or an obsolete artboard.

The previous concepts are not the accepted screens. Remove their TV-sync feature, make system-generated ideas primary, adopt dark dense presentation, and retain useful interaction evidence. The user already delegated final visual decisions to Fable; do not demand approval for each ordinary visual choice. Do obtain a concrete decision if the design changes selected release scope, spend, real-money rules or operational authority.

## 2. Reconcile the repository before planning

The existing implementation/setup work is concurrent with this package. Do not treat any recorded SHA, branch status or release receipt here as current deployment truth.

Read the current:

- `CLAUDE.md` and `.claude/skills/autopilot/SKILL.md`.
- Routed recovery, Linux-controller and plan/review procedures appropriate to preparation; do not execute loop preflight merely because a design file mentions autopilot.
- `docs/superpowers/autopilot/roadmap.md`, `state.md`, latest complete journal entries and `verify.md`.
- Current dashboard spec/addenda, phase 6B/6C/6D data/evidence contracts and their actual acceptance status.
- `docs/runbooks/omarchy-operations.md`, actual source/deployment stamps when operational verification is authorized, active worktrees/ledgers and current migration head.

Known starting areas to inspect:

| Area | Existing paths | Question for the final planner |
|---|---|---|
| UI shell/surfaces | `harness/dashboard/static/index.html`, `app.css`, `js/*.mjs` | Which pieces survive or are replaced by the final design? |
| Read API/snapshots | `harness/dashboard/app.py`, `snapshots/`, `scheduler.py` | How do game/ticket projections stay cheap and bounded? |
| Generation/recording | `harness/parlay/build.py`, `pricing.py`, `placement.py`, `config.py`, `parlay.yaml` | How do supported props/SGPs extend generation and accepted-slip recording? |
| Grading/progress | `harness/parlay/needs.py`, actual current grading module and tests | Which semantics must change for player stats, corrections and SGP outcomes? |
| Persistence | `harness/db/models.py`, Alembic migrations and current head | Which additive records and indexes are needed? |
| Scores/player inputs | `harness/normalize/espn.py`, existing source/recorder modules | Which new providers/identity maps are needed? |
| Research/accounting | Existing report, execution and correction modules | Which results may the UI present as accepted evidence? |
| Operations/access | `docker-compose.yml`, `Makefile`, `deploy/`, runbooks | LAN endpoint, auth/write protection, build/rollout/rollback and asset packaging |

This is a source map, not an exact task `Files:` list. Resolve exact files against current source after Fable's design. Do not give parallel workers this table as permission to edit entire directories.

At the September 12 read, 6B execution repairs, 6C reporting acceptance, 6D coverage and 6E operational acceptance remained relevant. A current carried dashboard fix may overlap mobile/error-state work (fix 53 appeared during this session). Reconcile it rather than creating a duplicate repair or holding an urgent defect until a full redesign.

## 3. Turn the design into an explicit addendum

Produce a dated UI revision spec under `docs/superpowers/specs/`, with a matching implementation plan under `docs/superpowers/plans/`. Choose the phase identifier only after reconciling the active roadmap. This package intentionally does not allocate the next phase number.

The addendum should explicitly address changes to older rules:

| Previous rule/assumption | New requirement to specify |
|---|---|
| Pulse/Floor/Study/Gate/Ticket surface organization | Fable's final game-centered navigation and retained research access |
| UI read-only except existing kill controls | New authorized local recording/preferences actions; exact API, authentication and idempotency |
| Game-line-only fun-card data/CLI | Player identities, stat markets, SGP grouping, combined quotes and corrected accepted slips |
| Fixed earlier fun-card shape | A versioned generation policy supporting first-release SGPs without silently changing weekly stakes |
| NAS-derived UI/asset budgets | Current-host measurements and any narrowly justified replacements; no automatic budget increases |
| Existing rendering/font/dependency policy | The chosen implementation stack/assets and precise approved exceptions, if any |
| Loopback/tunnel access | Usable home-network endpoint; no public or remote exposure |
| Current feed host/secret allowlist | Exact selected provider hosts, secret files, conditional deployment and numeric allocations |
| Floor activity vs Study quality separation | Where the game room presents each kind of evidence, with visible distinctions and provenance |

Keep the scientific criteria, preregistered strategies, paper-only posture, risk limits, retention rules and existing operational gates intact. An attractive new surface cannot certify old invalid evidence. Do not reinterpret this UI request as approval of the separate GPU/Cerebras research/controller proposals.

## 4. Suggested implementation slices

These are planning packages with dependencies, not runnable task briefs. The final plan must decompose them into exact `Files:` and `Depends on:` tasks with tests and evidence.

| Slice | Deliverable | Dependencies / completion evidence |
|---|---|---|
| A — Contracts and provider proof | Final field/state schema, coverage matrix, generation policy, combined-quote/link behavior, budget allocations | Fable outputs; provider account decisions as needed. Read-only fixtures and planning can proceed before paid activation |
| B — Data and accounting foundations | Additive player/market/accepted-slip/provenance records, collectors, correction handling, versioned ticket generation and recording | A; isolated tests for semantics, migration compatibility, budget/idempotency and historical preservation |
| C — Read projections and local writes | Game/ticket/digest snapshots, bounded query paths, LAN auth/write endpoints and persisted owner preferences | A/B; access checks, API schemas, retry/concurrency behavior and measured resource use |
| D — Final responsive interface | Fable's shell, generated-ticket review, handoff/confirmation, live ticket/SGP, game history, research/system views | Final design + stable contracts; fixture UI work may proceed alongside disjoint B/C tasks |
| E — Integrated rehearsal | Accepted-slip flow across two devices, sample game replay, all edge states, provider behavior and execution/report provenance | B/C/D; independent review and full required test/visual evidence |
| F — Controlled release and observation | Deployment in an eligible window, post-release deterministic checks, phone/laptop walkthrough, measured game-day observation, rollback readiness | E plus current operational gates and selected service setup |

UI fixture work does not need all historical execution repairs to finish. Claims using corrected execution/report evidence do depend on the appropriate accepted 6B/6C/6D outputs. Full first-release completion still requires selected NFL and college ticket capabilities; an internal NFL-only slice is not the finished scope.

## 5. Ready-to-run criteria

Before the owner starts the implementation loop, prepare a concrete launch receipt:

- Final Fable design path/version and accepted requirements; no unresolved core screen behavior hidden in screenshots.
- Updated spec and plan, with exact tasks, file ownership, dependency order and current base SHA.
- Design/plan reviews completed under the canonical controller's current review/model rules, findings ruled on and evidence linked. Do not invent a Fable model identifier or change worker allocations from this package.
- Each paid dependency either provisioned under a selected plan and caps, or a clearly independent task with its account gate and truthful incomplete-release status. No silently auto-renewed trials.
- Launch prop/league/book matrix and supported DraftKings link capabilities. If coverage cannot meet the user-selected scope, record the specific unresolved decision.
- Numeric API allocations, source polling/freshness targets and runtime/asset budgets justified by measured provider/host behavior; existing strategy quotas remain protected.
- Home-network access design and explicit authorized recording endpoints. Secrets remain server-side.
- Concrete expected results for unit/integration tests, semantic fixtures, exact-source suite, browser/device scenarios, post-deploy checks and observation windows.
- Current roadmap dependency reconciliation, adjacent work ownership and migration/base-branch reconciliation. Only add the new phase through the prepared, reviewed roadmap change; do not overwrite the active loop's updates.
- Deployment classification/window, additive migration and rollback plan, health/reader/writer feature switches, backup/restore implications and data preservation during rollback.
- Durable task ledger and recovery instructions consistent with the existing single controller, isolated worktrees/test databases, suite lock, retries and ceilings.

“Ready to run” is different from “feature complete.” A loop may have explicitly gated provider setup while making independent progress, but must keep that prerequisite open and avoid claiming a complete release.

## 6. Prompt for post-design preparation

Copy this only after the Fable design session has produced its artifacts:

> Read the UI revision package, the final Fable design outputs, and the current repository/autopilot sources. Reconcile the active work and all changed dependencies. Prepare a reviewed UI revision addendum, exact implementation plan, verification additions, and a narrowly scoped roadmap integration proposal. Preserve the selected product decisions: dense dark UI, selective broadcast typography, phone/laptop on the home network, system-generated DraftKings fun parlays, NFL and college props/SGPs wherever offered, manual placement with supported links, freshest data, $100/month additional sports APIs and unchanged existing wagering/scientific rules. Resolve ordinary design/implementation choices from the Fable output and record them. Make remaining provider/account/scope issues concrete. Prepare the launch receipt and handoff; do not start the loop or deploy during this preparation request.

Once the owner explicitly says to start/resume the UI implementation loop, use the **current canonical** autopilot startup/recovery path. Do not run a second controller alongside the existing one. Reconcile active workers and state before dispatch, keep independent work in isolated worktrees, run required tests through the shared suite slot, integrate with exact-source review evidence, and follow current deployment/verification gates. All those procedures remain in their canonical files rather than being duplicated into a stale second controller manual here.

## 7. Completion definition for the eventual loop

The loop is done when the complete selected scope is deployed and accepted on the actual home-network phone/laptop experience, provider and accounting contracts are verified, Fable's intended hierarchy/interactions survive real data, historical research is represented honestly, required game-day observations pass, and remaining limitations have explicit dispositions. A working mockup, green unit tests, a healthy container or a partially populated feed is insufficient by itself.
