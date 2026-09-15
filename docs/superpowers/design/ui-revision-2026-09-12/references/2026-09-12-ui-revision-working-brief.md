# UI revision: working design brief

Date: 2026-09-12. Status: research and discussion; proposals below are not yet user-selected or scheduled for implementation.

## User intent

The system has moved to a more capable host. Make the UI a joy to use while watching games, with an understandable account of what the system is doing and has done. Paid APIs are an option. Define the revision collaboratively and finish the design session with an autopilot-ready handoff for Claude Fable. The user has an active loop implementing the existing roadmap.

This document preserves the design session without changing the active loop's roadmap, state, journal, or production application. No service has been purchased or trial activated.

### User direction recorded during discussion

The user selected **a game-day companion plus a personal sports hub**, with the personal hub focused on **parlay fun-bets**. This supersedes the initial question asking the user to select one of three primary experiences. Ticket is a core product experience, not a secondary extra. The detailed screen structure and parlay preparation workflow below remain proposals.

The user subsequently confirmed **phone and laptop**, **$100/month for additional sports APIs**, and **both preparing/selecting parlays and following every leg after placement** in the first release. The API figure is a budget ceiling for the design, not selection of a provider or authorization to activate a renewing trial. Existing $50/week wagering settings remain a separate budget.

The user explicitly selected **player props and same-game parlays from the first release**, and confirmed **DraftKings as the primary sportsbook**. These are required scope, not deferred enhancements. Support includes preparing a ticket and following its recorded legs; exact prop families, league coverage and quote-entry workflow remain to be finalized. The system’s paper strategy and registered variants are separate from this expanded fun-ticket feature.

Visual feedback: the user is **leaning toward a denser UI**, dislikes the first concept’s **obvious AI design**, and requested an example of a **bold sports broadcast**. The original rounded/gradient/spacious treatment is not approved. Reduce oversized greeting copy and decorative containers; foreground actual games, tickets and recorded system actions. A new comparison, described below, explores broadcast and dense-desk treatments. Neither has yet been selected.

Subsequent selection supersedes that provisional status: **dense layout with selective broadcast typography, kept dark**. The user wants **Fable to determine the final design**, and requested a packaged design handoff plus preparation for an autopilot implementation loop **after** that design session. The comparison is inspiration, not an approved final screen specification. Launch scope is now explicitly **NFL and college football, including props and same-game tickets wherever DraftKings offers them**; unresolved college data coverage cannot silently reduce that requirement.

Final session clarifications: the **system generates fun parlay ideas**, which the owner **manually enters/places in DraftKings**; a reliable full-parlay link is desirable if feasible. A DIY builder or screenshot import is not the primary requirement. Show **the freshest available data**, with **no TV-spoiler delay/pause** in release one. Access is **home network only**. These instructions supersede older exploratory TV-sync, import and remote-access possibilities below.

The final design-session package is in `docs/superpowers/design/ui-revision-2026-09-12/`. Its PRODUCT-BRIEF decision record is the consolidated handoff; this file remains the dated research and exploration history.

## Current evidence

- `harness/dashboard/static/index.html` and `js/app.mjs`: current navigation is Pulse, Floor, Study, Gate, Ticket, and How it works. Default route is Pulse. Static ES modules, CSS, vendored uPlot, snapshot polling.
- `harness/dashboard/static/js/floor.mjs` and `harness/dashboard/snapshots/floor.py`: game board, activity funnel, orders, fills, exposure, executor vitals, venue telemetry. Information is organized by system function; there is no game-detail route in the current shell.
- Inspected the recorded desktop Floor screenshot `../../autopilot/evidence/2026-09-12-verify-b0a3991-0051-12-floor-board.jpg`: dozens of similar game cards foreground market counts and resting-order counts. Technical field names appear alongside everyday labels. This is historical evidence, not a fresh production check.
- `harness/normalize/espn.py`: normalized score events preserve status, period, clock, home/away scores and raw-response linkage. Rich play-by-play and possession are not part of this normalized score record.
- The current roadmap records Omarchy production at `/srv/sports-harness`, 32 GB RAM, approximately 1 TB filesystem. The earlier GPU research handoff's unconfirmed-host statements are historical and superseded by current host decisions.
- Roadmap 6B execution repair is in progress; 6C reporting acceptance and 6D coverage work remain relevant dependencies. Re-read their current status when writing the implementation plan.
- Existing snapshot isolation remains a useful architecture. Increased host capacity permits measurement of richer workloads; it does not itself establish a safe polling/query budget.

## Recommended product direction, pending discussion

A personal game-day companion whose organizing unit is the game. Let an owner answer: what deserves my attention, what did my system see, what did it do, what does the current score mean for its positions, and what did we learn?

Following the user's direction, the proposed home combines a personal watchlist, prominent active parlay tickets, and a concise system activity digest. A shared Game Room shows the game's relationship to both a fun ticket and a paper position, with clearly labeled amounts and separate totals. Proposed navigation: **Today**, **Tickets**, **Research**, **System**; Game Room is a detail route accessible from either Today or Tickets. Exact names and layout await prototype review.

The parlay journey to discuss is **prepare → record the actual hand-placed slip → follow the legs → review the result**. For example, a ticket with two settled winning legs and one remaining leg should show those facts alongside the final leg's exact condition and current score. Reaching a spread temporarily does not settle it. A potential return must be distinguished from profit and from a confirmed payout.

Additional parlay code inspection: `harness/parlay/parlay.yaml` already defines a $50 weekly ceiling, a $25 smart card, up to three $5 lottery cards, and LSU/Saints anchors. These are existing settings, not newly requested changes. `harness/parlay/placement.py` records manually placed slips through the backend/CLI, including actual odds and changed lines; the current UI is read-only. A proposed “Record my ticket” UI would need an explicitly designed write API and revised UI contract. It records an external action and does not submit a wager. Manual arbitrary-ticket entry or screenshot import is additional scope beyond exposing the existing proposed-card confirmation.

`harness/dashboard/static/js/ticket.mjs` already renders slips, leg state, “what still needs to happen,” payout/stake, and season totals. `harness/parlay/needs.py` provides score-based explanations. Reuse needs a correctness review: whole-number spread/total thresholds must distinguish a push from a win, and postponed/cancelled games must not receive ordinary final-score explanations. Include concrete boundary fixtures in the eventual plan rather than assuming current strings are reliable.

Candidate experiences:

| Experience | Proposed behavior | Data work |
|---|---|---|
| Game Day | Favorite teams and games with exposure first; live/upcoming/final filters; a selected game gets most of the screen; an activity digest says what changed while away | Reuse scores, game identity, positions and snapshots; add preferences and game-level summaries |
| Game Room | Score and situation, exact contracts held, entry price, payout conditions, market movement and a chronological decision story in one place | New game-detail projection and route; richer situation data needs play feed |
| Decision story | Observed price discrepancy → evaluated → order resting → partial/full paper fill or cancellation → close benchmark → settlement. Expand an event to see source facts | Audit completeness of existing records; capture missing links prospectively; do not infer a complete history from aggregate counters |
| Since you left | A concise digest of fills, cancellations, outcomes and noteworthy changes, with links to the events | Durable event IDs, read cursor and meaningful grouping |
| Postgame review | Separate outcome, entry-price quality, and execution quality; show what was known at decision time | Corrected 6B/6C results and explicit report provenance |
| Ticket | A distinct fun-bet slip with leg progress, team context and manual-placement status | Reuse existing Ticket; new context feed optional; keep its accounting distinct |
| Research and System | Preserve Study/Gate and diagnostics as accessible drilldowns; human-readable operational impact visible on Game Day | Remap current surfaces and severity; retain diagnostic details behind disclosure |

The pregame strategy currently stops placing before kickoff. During play, the proposed screen follows existing positions and game context. Copy must not imply that it is making in-play bets.

The interface should remain interesting on a zero-position day: show favorites, upcoming evaluations, recorded reasons for passing, and games being followed. Distinguish “evaluated and passed” from “not evaluated,” incomplete coverage, and unavailable data.

Proposed visual character: spacious sports broadcast typography, prominent scores and team identity, restrained team colors, readable charts, gentle motion on meaningful changes, dark and light themes. Use technical names in detail views instead of on every card. Avoid celebration that equates a lucky outcome or more trading with strategy quality.

## Distinctive interactions to explore

1. **Sync to my TV.** User-controlled display delay or manual pause/resume for scores, plays, position outcomes and related narrative. Apply one cutoff consistently to every spoiler-bearing field; a delayed score with a live price or notification still spoils. Keep operational freshness distinct. This delays presentation only.
2. **What needs to happen.** Exact payout conditions beside a position. Score-based progress is a current-state description, not a win probability. Any probability must identify its model or market and timestamp.
3. **Replay the decision.** Scrub through a game's recorded history with entry, fill and cancellation markers; distinguish event time, receipt time and decision time. Gaps remain visible. Historical replay must not leak later facts into an earlier explanation.
4. **Why this / why nothing.** Short explanations generated from recorded reason codes and facts, expandable to evidence. Optional language-model summaries can add fluency; they cannot invent causal reasons, calculations or system actions.
5. **Favorite-team experience.** LSU and Saints appear in existing project context; confirm favorites with the user. Team styling and imagery depend on available asset rights and should have text/initial fallbacks.

## API research, checked 2026-09-12

Prices are published subscription prices, not purchases or measured service quality. Budget figures below are incremental to existing odds/model spend.

| Provider | Published offer | Potential role | What still needs validation |
|---|---|---|---|
| [CollegeFootballData](https://collegefootballdata.com/api-tiers) | Tier 2 $5/month, 30,000 calls/month, live play-by-play; Tier 3 $10/month, 75,000 calls/month and GraphQL access | College game context and live plays | Actual observed lag, target-game coverage, correction behavior, intended-use/storage terms and full polling budget |
| [BALLDONTLIE NFL](https://nfl.balldontlie.io/) | GOAT $39.99/month, 600 requests/minute, plays, injuries, team/player stats; tiers are per sport | NFL game context and plays | Published feature list does not establish end-to-end latency or completeness; measure both during games |
| [SportsDataIO](https://sportsdata.io/developers) | Discovery Lab Fantasy or Odds $99/month each, combined $149/month; next-day delayed | Historical research rather than live companion | Real-time commercial offering requires separate evaluation/quote |
| [Sportradar](https://sportradar.com/media-tech/data-content/sports-data-api/) | Real-time and standard packages, custom quote, 30-day trial advertised | Higher-service alternative if smaller providers fail | NFL and college coverage, cost, latency, intended use and retention terms |
| [Existing The Odds API](https://the-odds-api.com/liveapi/guides/v4/) | Scores endpoint updates approximately every 30 seconds for supported leagues | Basic score fallback, alongside already recorded market data | Current plan/credit headroom and selected-league coverage; it does not substitute for a play feed |

CFBD's [live plays documentation](https://api.collegefootballdata.com/api/plays) says results may be cached up to five seconds after calculation. That is not a five-second field-to-screen guarantee. Its per-game call cost matters: as an illustrative assumption, four four-hour games/week polled every 15 seconds for four weeks consume 15,360 requests before retries and other endpoints. Twenty games/week at that cadence require 76,800. A favorites/focus polling policy materially changes the tier needed.

[The Odds API update intervals](https://the-odds-api.com/sports-odds-data/update-intervals.html) list featured-market odds at 60 seconds pregame and 40 seconds in-play; browser animation cannot make these sources fresher.

BALLDONTLIE's trial requires a payment method and converts after 48 hours unless cancelled. Do not start a trial merely to gather design information. A potential evaluation bundle is CFBD Tier 2 or 3 plus NFL GOAT: $44.99–49.99/month before taxes and existing subscriptions. It is a candidate for a measured evaluation, not a selected vendor bundle.

Sportradar also documents [game replay simulations](https://developer.sportradar.com/getting-started/docs/simulations), including NFL/NCAAFB. Consider recorded/simulated games for UI development, with unmistakable demo labeling and no mixing into research evidence.

Provider evaluation should record field coverage, late/corrected plays, ID mapping, p50/p95 observed delay where source timestamps permit, outages/recovery, request usage and applicable display/cache/history rights. If source timestamps are absent, state the limitation instead of calling polling age end-to-end latency.

### Follow-up: DraftKings props and same-game parlays

[The Odds API market catalog](https://the-odds-api.com/sports-odds-data/betting-markets.html) lists NFL/NCAAF player markets, accessed per event. Availability still depends on the actual game, market and bookmaker. [Its event-odds documentation](https://the-odds-api.com/liveapi/guides/v4/#get-event-odds) charges by unique returned markets and requested regions. Current local settings specify 5,000,000 monthly odds credits; confirm deployed usage/remaining quota before allocating prop collection. A larger configured quota does not prove remaining account headroom. Archive the exact selected leg and quote independently of the changing market menu.

[BALLDONTLIE NFL](https://nfl.balldontlie.io/#player-props) documents per-game props with player IDs, lines, vendor and update time, including DraftKings, over/under and milestone markets. Its current props menu may disappear near completion; opening-history support is distinct from a complete quote history. It also exposes player game statistics, but live statistical completeness, correction handling and field-to-screen delay remain evaluation requirements. Never treat live *odds* coverage as proof of live *stat* coverage.

[SportsGameOdds’ SGP documentation](https://sportsgameodds.com/use-cases/parlay-builder-api) explicitly says it supplies individual leg prices, not a combined correlation-adjusted SGP quote. Per-leg links are not guaranteed to populate a complete slip. This is evidence to design for a separately recorded sportsbook quote, not to assume the provider handles SGP pricing. No additional odds vendor has been selected.

Recommended first-release workflow: compose supported game/player legs; explain shared-game relationships; mark combination eligibility unconfirmed until the book accepts it; show **combined quote needed** until a source-backed DraftKings SGP quote or the user’s actual accepted odds is recorded. Changing a leg invalidates the prior quote. Do not calculate an SGP price or joint win probability by multiplying independent leg figures. Explain connections from observable football relationships without assigning an unvalidated numeric adjustment. A generic AI explanation is not an SGP pricing model.

The approximately $45–50/month candidate feed bundle remains plausible, with existing odds-account headroom used only after measurement. We have not established that all desired college player statistics or DraftKings combinations are available within $100/month. NFL and college coverage need separate acceptance rows; this is an unresolved data requirement, not permission to silently drop college props or exceed budget.

Proposed initial prop catalog for discussion: full-game passing/rushing/receiving yards, receptions, and anytime touchdown scorer, plus supported alternate milestones. Each requires provider coverage and a corresponding grading rule. Passing touchdowns and scoring a touchdown are distinct stats. Period props, combined stats, first-scorer and unusual specials must be explicitly included or excluded in the final spec, not accidentally accepted by a generic text parser.

Required data contracts for the expanded scope:

- Stable player/game/provider identity mapping; team membership as of the game, not name-only joins.
- Immutable accepted slip versions: sportsbook, placement time, actual stake and combined quote; each leg’s game, player/team, stat, period, side, line/operator, market/source identity and relevant terms.
- Player-stat observations with source, timestamps, correction identity and coverage status. Prefer provider aggregates to parsing prose; any play-derived totals must be validated against official-style aggregates before claiming complete coverage.
- Separate states for projected/upcoming, live below target, live target reached provisionally, final result, void/push, awaiting book settlement and confirmed book settlement. Stat corrections may reverse apparent progress; provider disappearance is not a void or zero.
- SGP groups and any mixed-ticket group structure, so related legs remain grouped. One leg’s void may change the book’s return; do not automatically apply ordinary independent-parlay repricing.
- A ledger-backed recording API with idempotency, concurrent budget enforcement, provenance and auditable correction flow. Keep the $50 weekly wagering ceiling independent of the $100 monthly data ceiling.

No production parlay configuration, schema, ledger or grading behavior was modified in this design session.

## Design and handoff sequence

### First interactive concept

Open [game-day-concept.html](game-day-concept.html) directly in a browser. It is a self-contained, temporary-state prototype with no network requests, dependencies, credentials, or production integration. All teams' matchups, scores, odds, fills and activity in it are fictional sample fixtures, not actual schedule or betting suggestions.

Review paths:

- **Today → Open game:** large featured game, personal ticket, paper-system digest, and game-detail decision trail.
- **My tickets → Find my ticket:** pregame explanation and sample-ticket review. “Compare a shorter ticket” describes proposed functionality; it does not calculate an alternate quote.
- **Review this sample ticket → Save sample slip:** enter illustrative accepted odds/stake and confirm sample lines; temporary demo state then advances to live tracking. Reload resets it. This is a UI concept, not a production placement or accounting API.
- **Explore a moment:** before games, live and final examples. Changing moments resets sample stake/odds and recording state. Final shows an expected return with payout confirmation pending.
- **Explore system coverage:** sample filled position versus an evaluated pass. TV sync, system diagnostics and the shorter-ticket comparison open explanatory dialogs; they are not implemented integrations.

Visual proposal: charcoal/green base, lime actions, restrained LSU purple, amber ticket surface, prominent scores and an illustrated possession field. Phone puts the ticket immediately after the featured game; laptop puts it beside the game. Current prototype navigation includes Game room directly for review; the proposed production navigation above keeps it a game-detail route and preserves Research/System.

Browser validation: headless Chrome exercised 18 combinations (three screens × three game moments × 390 and 1440 pixel widths), checking horizontal overflow, heading presence and invalid rendered values. Navigation, sample-slip recording, payout arithmetic, invalid-odds rejection and no-position copy passed. Screenshot inspection found and corrected mobile score wrapping, then the same checks were rerun. Preview images: [phone](game-day-concept-phone.png), [laptop](game-day-concept-desktop.png). These are prototype smoke checks, not production readiness or a complete accessibility audit.

The prototype uses authored template strings for speed. It is visual/interaction evidence; do not copy its rendering approach over the production DOM policy without an explicit decision. Production work still needs real data contracts, authentication for any recording API, idempotency, concurrency-safe weekly accounting, line-change handling, grading exceptions, corrected historical evidence and full accessibility/visual acceptance.

The $100/month API ceiling leaves room for the proposed approximately $45–50/month feed evaluation bundle plus headroom. No need to spend the remainder until play-feed coverage or chosen ticket types justify it. Player props and same-game parlays are now required first-release scope; the existing builder only uses game moneylines, spreads and totals, so the revision must explicitly extend that contract.

### Same-game concept revision

Open **My tickets → Same game + player props**, or load `game-day-concept.html#sgp`. The new Saints–Falcons fixture and all named players are fictional. Before games, add/remove supported sample legs and see how a quarterback and receiver prop relate. The draft shows no combined payout until a sample accepted quote is entered. Live, step through a 12-yard completion affecting both stats, then correct that play to eight yards and see both progress values decrease. At-target states remain provisional; final sample results still await book settlement.

This is a separate sample ticket scenario, not an additional concurrent ticket recorded against the same demo budget. It illustrates the desired behavior without changing production. DraftKings remains the selected book; production feed selection and a measured coverage evaluation remain pending.

Same-game prototype validation: six additional screen scenarios (pregame/live/final at 390 and 1440 pixels) passed overflow/render smoke checks. A sample completion changed passing/receiving totals from 208/47 to 220/59; correcting it from 12 to eight yards changed them to 216/55. Tests also checked provisional target status, quote invalidation after changing a leg, a blank required combined-odds field, and recording $10 at +750 as an $85 total return. The original 18 scenarios and prior interaction checks were rerun and passed. This validates the authored concept, not any provider or production calculation path.

### Broadcast versus dense-desk comparison

Open [game-day-broadcast-concept.html](game-day-broadcast-concept.html). Header controls switch between **Broadcast** and **Dense desk** without changing the underlying sample game/ticket. `#desk` starts with Dense desk; `#sgp` starts on the same-game ticket. The first concept remains available for comparison.

Shared hierarchy: a four-game scoreboard strip, narrow watchlist/paper-account column, central game and chronological decision table, and a ticket column. Phone preserves the score strip, featured game, summary readings and ticket before detailed activity. Navigation and the previously built ticket/SGP interactions remain available.

Broadcast uses a dark neutral ground, condensed display numerals, sharper panel boundaries and restrained red/orange emphasis. Dense desk uses a light neutral ground, tighter type/spacing, removes the decorative possession field, and adds a compact pregame reference table. These are deliberately concrete alternative visual treatments; color mode and information density can be made independent after the user chooses the desired direction. User preference for higher density does not imply a preference for light mode.

The new concept changes presentation only. It does not establish final production navigation, approved fonts, data contracts or feed performance. Sample scores, prices and dates remain fictional.

Comparison validation: the 18 original screen/scenario checks, six SGP screen/scenario checks and six additional dense-desk layout checks passed at 390/1440 pixels. Sample recording, navigation, quote invalidation and stat-correction interactions also passed. Inspected the laptop broadcast/dense-desk and phone dense-desk screenshots; corrected win-state color and a programmatic focus outline in the previews. Final previews: [broadcast laptop](game-day-broadcast-desktop.png), [broadcast phone](game-day-broadcast-phone.png), [dense desk laptop](game-day-desk-desktop.png), [dense desk phone](game-day-desk-phone.png). This remains a limited prototype check, not a full accessibility review.

Current recommendation for discussion: use the dense layout as the foundation, with selective broadcast typography for scores and key moments. Keep density and dark/light preference independent in the final design. Await the user’s reaction rather than interpreting the request for a broadcast example as approval of that styling.

### Remaining sequence

1. Agree on primary experience, device priorities, favorites, budget and spoiler preference.
2. Build a clickable concept for Game Day → Game Room → postgame review. Use clearly labeled sample data and cover both active-position and zero-position days.
3. Review hierarchy, visual style and interactions with the user. Choose minimum first release and later enhancements.
4. Write a design addendum explicitly replacing affected prior UI rules: navigation, permitted content per surface, visual tokens, asset/dependency budget, rendering choices and update cadence. The older September 14 canvas-refinement deferral does not prevent this user-requested design session.
5. Audit each screen's data requirements against completed 6B/6C/6D contracts. Specify provenance, units, timestamp semantics, missing/stale/disconnected/provisional states and API response shapes.
6. Prepare the implementation plan with exact `Files:` and `Depends on:` lines, fixtures, meaningful acceptance checks, deployment/rollback and visual checks at agreed device sizes. Follow the active controller's plan/review requirements at handoff; this design session has not run those reviews.
7. Reconcile the live roadmap before insertion so the existing implementation loop keeps its work. Record any selected paid providers, outbound hosts, numeric budgets and secret-provisioning requirements as concrete user decisions. General willingness to buy APIs is not a specific subscription selection.
8. Finish with a Fable start brief naming the approved spec, plan, assets, prerequisites, decisions already made and remaining account setup. Only declare autopilot-ready once design choices, dependencies and acceptance criteria are resolved.

## Questions pending

- Resolved: game-day companion plus a personal sports hub focused on parlay fun-bets.
- Resolved: both ticket discovery/preparation and following actual slips in the first release.
- Resolved: phone and laptop; $100/month additional API budget.
- Favorites and how much prominence to give Ticket?
- Resolved: player props and same-game parlays in the first release, with DraftKings primary.
- Exact supported prop families, league coverage and accepted-quote recording/import workflow?
- Broadcast spoiler protection: needed, optional or irrelevant?
- Desired first-release scope and visual direction, to decide using a prototype?

These questions are design choices, not approvals required for the research already performed.
