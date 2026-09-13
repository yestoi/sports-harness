# Data, API budget and DraftKings integration

Research checked September 12, 2026. Prices and published features are evidence for a shortlist, not measured service quality or account activation. No subscription, trial or vendor conversation was started.

## Candidate acquisition plan

| Source | Role | Published offer / remaining question |
|---|---|---|
| Existing The Odds API | Game odds, supported DraftKings props, available source links/IDs | Repository config specifies 5M monthly credits; actual deployed usage and account headroom still need inspection |
| BALLDONTLIE NFL GOAT | NFL player/game stats, plays, injuries and prop menu | $39.99/month, 600 requests/minute; measure live stat coverage and delay |
| CollegeFootballData | College scores, plays and context | Tier 2 $5/month/30k calls; Tier 3 $10/month/75k calls; confirm live player-stat completeness for target props |
| SportsDataIO / Sportradar | Fallback evaluation if essential live coverage is missing | SportsDataIO hobby plans are next-day delayed; real-time commercial packages need separate quotes |

Sources: [BALLDONTLIE NFL documentation](https://nfl.balldontlie.io/), [CollegeFootballData tiers](https://collegefootballdata.com/api-tiers), [SportsDataIO access options](https://sportsdata.io/developers), [Sportradar packages](https://sportradar.com/media-tech/data-content/sports-data-api/).

The initial paid-feed candidate totals **$44.99–49.99/month** before taxes and existing subscriptions. It is plausible within the $100/month additional sports-API ceiling; it does not establish that all desired college prop tracking and SGP features are available. Keep unallocated headroom until a demonstrated gap justifies another provider. A trial with automatic renewal is an account action, not ordinary documentation research.

The Odds API lists football props accessed per event, and the event endpoint charges by unique returned market types and requested regions. Batch selected markets/books and collect centrally; opening multiple browsers must not multiply paid requests. Use recorded quota headers to enforce a defined allocation and protect the existing strategy feed. [Market catalog](https://the-odds-api.com/sports-odds-data/betting-markets.html), [event-odds documentation](https://the-odds-api.com/liveapi/guides/v4/#get-event-odds).

## A critical separation: menu, stats and SGP quote

1. **Market menu:** what DraftKings currently offers, with exact player, game, line, period and price.
2. **Player statistics:** what has happened so far, with data-source age and corrections.
3. **Combined quote:** what DraftKings accepts for the whole ticket, including correlated-leg pricing.

One source may provide more than one of these, but none should be inferred from another. A feed that updates prop *odds* does not prove that its player *statistics* are live. A real-time player total does not establish the sportsbook's final grading.

SportsGameOdds explicitly supplies individual SGP legs rather than a single correlation-adjusted SGP price. Its per-leg links do not guarantee a full slip. This is useful evidence for the quote workflow, not a reason to buy another odds feed. [Provider SGP documentation](https://sportsgameodds.com/use-cases/parlay-builder-api).

## DraftKings links: useful first investigation

The existing Odds API supports `includeLinks=true` and `includeSids=true`: available links can point to bookmaker events, markets or betslips, and source IDs may help with supported link formats. Availability is conditional. This research did not verify a full DraftKings SGP prefill on the user's devices. [Bookmaker deep links](https://the-odds-api.com/releases/deep-links.html).

The production contract should use a link capability value such as `full_slip`, `selection`, `event` or `none`, attached to a specific proposal version. Fable should give each an honest button label. Never infer `full_slip` from the mere presence of a URL.

Investigate in this order:

1. Capture source-supported links and IDs alongside existing odds collection, under the approved quota budget.
2. Verify exact destinations on the owner's phone and laptop for a normal cross-game parlay, a player prop and an SGP, including app-installed/app-not-installed behavior where applicable.
3. Check that the exact players, sides, lines, periods and number of legs survive the handoff. A destination that loses a leg fails full-slip acceptance.
4. If full-slip support is absent, ship exact copyable selections plus verified event/selection links. Leave full-slip prefill as a named capability limitation; it is not a reason to invent an undocumented checkout integration.
5. Record actual placement separately. An expired/repriced URL or changed odds cannot alter the originally suggested or accepted ticket silently.

The owner's manual DraftKings login and final placement happen in DraftKings. No real-money action is part of the provider evaluation. This package does not authorize vendor outreach or paid partner enrollment.

## Minimum launch coverage matrix

The planner must fill each row with measured provider/source evidence, exact market codes, supported books, update semantics and final grading rules. The broad user-selected scope is NFL **and** college, wherever DraftKings offers the markets. If a required row cannot be supported, bring the concrete gap to the owner rather than describing a reduced launch as complete.

| Family | NFL | College | Required fields/semantics |
|---|---|---|---|
| Moneyline, spread, total | Verify | Verify | Game identity, side, threshold, period, final/void/push rules |
| Passing/rushing/receiving yards | Verify | Verify | Player identity, stat definition, live aggregate, corrections |
| Receptions | Verify | Verify | Catches distinct from targets; exact threshold/operator |
| Anytime TD scorer | Verify | Verify | Scoring vs passing TD; book-specific participation/void rules |
| Alternate milestones | Verify selected types | Verify selected types | `225+` differs from over 225; offered price and actual identity |
| SGP / grouped ticket | Verify | Verify | Accepted combination, group identity, combined quote and regrading |

This is a proposed starting catalog for Fable/planner refinement. Period props, first-scorer, combined-stat specials and other offered prop families need an explicit in/out decision. Do not pretend generic text support provides live automated grading for every market. Missing coverage remains visible, with manual factual recording only where the final contract explicitly permits it.

## Provider evaluation packet

Produce a small, reproducible evidence report per candidate:

- League/game/market/book coverage, player-ID mapping examples and selected sample responses with no credentials.
- Source timestamp vs receipt timestamp, polling age and observed delay. If a timestamp is absent, say what could not be measured.
- Initial, incremental, out-of-order and corrected observations; reconnect and quota-exhaustion behavior.
- Live aggregate completeness compared with an independent final result where available. Do not reconstruct box scores from play prose without validated rules.
- Polling design for watched games and recorded ticket players, calculated calls/month, existing account reserve and hard caps. “$100 total” must become enforceable provider allocations before activation.
- Applicable display/cache/history rights, asset usage, history retention and ability to preserve accepted-slip evidence when a prop disappears from the provider menu.
- Supported link destinations, not just URL presence.

No blanket 5/10/15-second “live” promise has been established. The user wants the freshest available information; measure provider cadence, then specify collection and UI freshness targets without spending extra calls that cannot improve the source.

## Home-only access and model use

Keep the UI private to the home network. The current deployment binds the dashboard to loopback; creating a usable phone/laptop LAN endpoint is concrete implementation work, not a CSS setting. Keep database/provider credentials server-side and plan authentication and recording-write protection for the new endpoint. Remote/VPN/public deployment is out of scope.

The app already has model-assisted research/rationale infrastructure. Prefer evidence-backed templates and existing bounded model calls where suitable. An always-running announcer, new local GPU model or new model subscription is not a prerequisite. Any incremental model/API spend introduced for this revision needs a numeric cap and explicit accounting; the data ceiling does not silently expand existing model budgets.
