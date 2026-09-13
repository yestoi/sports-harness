# Experiences and acceptance examples

This defines what the experience needs to accomplish. It deliberately leaves screen composition and final interactions to Fable. Example games, players and prices below are fictional.

## 1. Before games: the system gives me a ticket idea

The primary journey is **review a system-generated idea**, not manually search an enormous market catalog. A ticket should offer an understandable reason to follow its games and players. An editable builder can support an alternative, but it is not a substitute for a functioning recommendation generator.

A proposed card needs:

- Exact DraftKings selections, each with game, player/team, stat or market, period, side, and line.
- The personal anchor, relevant data-backed context, per-leg conditions and relationships among same-game legs.
- Price/source timestamp and a clear distinction between a quoted combined price, a calculation with stated assumptions, and an unavailable price.
- Suggested stake consistent with existing settings, plus actual weekly stakes already recorded. Proposed/unplaced ideas do not consume the ledger or imply a reservation at DraftKings.
- A meaningful reason when there is no suitable idea: missing/old odds, unmatched player, unsupported market, insufficient context, no eligible anchor or required combination unavailable.

Selection policy must be explicit and versioned. Data freshness, available market coverage and rule-based evidence should determine what the system can propose. Model-written prose can explain supplied facts; it cannot invent injury news, player usage, edge, combined odds or win probability. Future factual sources must be captured as of recommendation time.

Fable should show normal ideas, multiple alternatives, all games not yet started, a favorite team on a bye, no suitable idea, a stale quote, a removed prop and a generated idea the owner declines. A refresh may create a new proposal version; it must not alter a ticket already recorded as placed.

## 2. DraftKings handoff and recording

Preferred flow:

1. Open a recommended ticket and understand its legs.
2. Open DraftKings with the full slip prefilled **if a supported, verified link exists**. Otherwise open the known event/leg destination and provide exact copyable selections.
3. The owner reviews the final quote and manually places in DraftKings.
4. Back in the app, a small **I placed this** flow confirms actual stake, accepted combined odds, and any line/player/leg changes.
5. The recorded version becomes the tracked ticket.

Opening a link, clicking “copy,” or visiting a sportsbook never means a bet was placed. The UI must not infer an accepted combined price from an external page visit. No wager automation or DraftKings credential collection is part of this feature.

Changing a draft leg invalidates its old combined quote. For a placed slip, preserve the originally recorded version and add an auditable correction; do not silently overwrite its terms. Two rapid confirms, retry after a lost response, and simultaneous phone/laptop submits must not double-record the stake. A concurrent confirmation at the weekly ceiling must not overspend the system's configured tracking allocation.

Do not silently change the existing hard $50/week rule to accommodate an already-placed out-of-budget ticket. Define the rejection/remediation UI in the plan, and raise an explicit policy decision if a different exception-recording flow is desired.

## 3. During games: every leg in context

Show the actual recorded ticket's conditions beside current game and player data. Leg progress is an understandable current state, not a chance of winning.

| Example | Required meaning |
|---|---|
| LSU −3.5, leading 24–21 | Needs to finish ahead by at least four; currently not covering |
| Over 50 total points, current total 50 | An exact 50 is a push if final, not an over win; a positive integer scoring increment is still required |
| Brooks 225+ passing yards, currently 208 | 17 yards to the target, with live status and source age |
| Ellis 50+ receiving yards, currently 59 | Target reached provisionally; still subject to corrections and grading |
| One 12-yard completion from Brooks to Ellis | Both player totals can move from that same event; explain their relationship |
| That play is corrected from 12 yards to eight | Reduce both totals by four and identify the correction; do not preserve a monotonic animation as truth |
| A provider omits a player in an update | Unavailable/unchanged-last-observed with its age, not zero yards |
| Under prop is currently below the line | Still live; do not settle a winning under before the applicable period ends |
| Anytime touchdown scorer | Distinguish scoring a TD from throwing a TD, per the recorded market definition |

Final game results and provider stat results do not themselves prove DraftKings paid a ticket. Make pending book settlement, voids, pushes, corrections and confirmed return distinct. A voided SGP leg may change the sportsbook's price/return in a way that differs from a plain independent parlay; use the recorded book outcome rather than invent repricing.

Show the freshest available data. There is no display-delay buffer or spoiler pause in this release. A game clock should reflect the source state; do not animate it as running during stoppages without evidence. Uncertain/out-of-order source observations must not make a seemingly newer clock or total silently replace a better observation.

## 4. The paper system's story

For a selected game, show the recorded sequence: price observed/evaluation → action or pass → resting order → partial/full simulated fill or cancellation → closing benchmark → settlement and review as evidence becomes available. Each stage uses its own units and timestamps.

Keep these distinctions visible:

- Evaluated and passed vs not evaluated vs failed/incomplete evaluation.
- Real observed market trade vs simulated fill vs hypothetical/replayed fill.
- Candidate signal rows vs unique opportunities vs orders vs fills vs independent games.
- Decision-time fair value vs a later market price vs an executable exit quote.
- Paper P&L vs fun-ticket stake/return/profit.
- Game result vs price quality vs execution quality; a lucky win does not demonstrate an edge.

The current strategy acts before kickoff. During play, describe the actual recorded position and the result it needs; do not imply in-play order activity unless a future separately authorized strategy exists.

Historical gaps, disputed execution, stale reports and incomplete eligibility should be understandable from the relevant game/report. The global status can be compact, but a problem affecting the view cannot be hidden to make the interface feel calmer. Put raw diagnostics behind an evidence view, with plain consequences and named missing data on the primary surface.

## 5. Afterward and between slates

Keep completed tickets and game stories available by date/week. Show staked, returned and net with correct definitions. Distinguish expected return from confirmed return. Preserve the Chicago week keys used by the ledger/reporting contracts.

Make the next slate useful before there is a position or ticket: favorite-team schedule, next recommendation/evaluation availability and previous results. Avoid an empty home screen because the paper strategy took no action. If the data feed or provider budget is exhausted, explain the limitation and retain the last valid observation with its age.

Research and System remain navigable. The revision must retain meaningful access to the existing Study and Gate evidence even if their top-level navigation changes.

## 6. Phone/laptop and home-network behavior

One owner, two devices, home network only. The product should have a stable bookmarkable LAN address, with appropriate authentication for reads and recording writes. The owner should not need to run an SSH tunnel on a phone. Hostnames, TLS/session mechanics and networking are implementation decisions to reconcile with current deployment constraints, not a public-hosting request.

Persist real tickets, favorites and game follows on the server. Keep local-only display preferences explicitly local if Fable chooses that tradeoff. A second device should observe the confirmed ticket without manual re-entry.

Provide legible dense layouts at phone and laptop widths. Proposed verification viewports are 360/390 and 1280/1440 pixels, plus text enlargement and landscape. Fable should declare readable type and touch-target rules rather than shrink everything until it fits. Support keyboard focus, accessible names, contrast, reduced motion and data alternatives for charts; do not claim a complete accessibility conformance result based on the prototype smoke checks.

## 7. Production acceptance examples to carry into the plan

1. Generated DraftKings NFL and college ticket examples include supported player props and an SGP; exact market/player identities and source timestamps are inspectable. Missing coverage fails its own acceptance row rather than quietly narrowing launch scope.
2. A recommended slip can be handed off using the most specific verified link available; the fallback labels match the actual destination. No click records placement.
3. Actual accepted odds determine the recorded return. $25 at +450 means $137.50 gross return and $112.50 profit if it wins under those terms. SGP quote invalidates when a draft leg changes.
4. Recording retries and simultaneous submits are idempotent and respect the existing weekly rule. Recorded slips appear on the other device.
5. A player-stat correction lowers displayed progress; missing stats are not zero; a provisional threshold crossing is not final settlement.
6. Game detail shows a credible action story and a correct no-position story. The same UI handles sparse/invalid historical evidence without fabricating a timeline.
7. Home is useful with no ticket, no position, no favorite game that day, incomplete data and a disconnected browser.
8. The UI/collector workload is measured alongside the corrected paper workload. Existing recorder/executor/resource limits remain intact unless an explicitly reviewed amendment changes them.

These are testable behavioral requirements for the future implementation plan. No production implementation or provider trial has passed them during this design session.
