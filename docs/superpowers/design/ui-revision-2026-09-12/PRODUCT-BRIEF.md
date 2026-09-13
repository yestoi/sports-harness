# Product brief and decision record

## Product promise

Make the owner look forward to opening the app during football. In a few seconds it should explain which games matter personally, how the fun tickets are progressing, and what the paper system did. Deeper views should make the reasoning and evidence understandable.

The product has two connected experiences: **the owner's football and fun tickets**, and **the paper system's game-by-game activity and research**. A game can connect them; their financial totals and evidence must remain distinct.

## Confirmed user decisions

| ID | Decision | Source / interpretation |
|---|---|---|
| U01 | Game-day companion plus personal sports hub, focused on fun parlays | User selected both |
| U02 | Phone and laptop | Explicit |
| U03 | $100/month for additional sports APIs | Explicit ceiling; no provider purchase selected |
| U04 | Prepare ticket ideas and follow all legs in release one | Explicit “Both” |
| U05 | Player props and same-game parlays from the start | Explicit; do not defer silently |
| U06 | DraftKings primary | Explicit |
| U07 | NFL and college, including props/SGPs wherever DraftKings offers them | Explicit launch scope; actual availability is still conditional |
| U08 | System generates the ideas; owner places in DraftKings | User clarification supersedes a DIY builder as the primary journey |
| U09 | A link to easily load/buy the parlay in DraftKings is desirable | “If we can”; full-ticket prefill is an investigated enhancement, not an established integration |
| U10 | Freshest available information; no TV-spoiler delay/pause | Explicit |
| U11 | Dense layout with selective broadcast typography, dark | Explicit final direction for Fable |
| U12 | Fable determines final design | Explicit; reference pixels are not binding |
| U13 | Prepare for an autopilot loop after the Fable design session | Explicit sequencing; no loop starts from this package |
| U14 | Home network only for release one | Explicit; no remote-access/VPN/public-hosting work required |

## Existing settings carried forward

The repository's parlay configuration has a **$50/week ceiling**, a **$25 smart card**, **$5 lottery cards up to three**, and **LSU/Saints anchors**. The unused allocation is intentional; the app should not encourage spending the balance just because it remains. This session did not change stakes, limits or anchors.

The current smart-card template uses 3–4 legs from different games; lottery templates use 6–8 and can contain correlated legs. User selection of SGPs requires an explicit extension to the ticket-generation contract. It does not justify changing scientific strategies, thresholds or the paper gate. The planner must decide whether to add an SGP template or revise fun-card templates, documenting the user-facing behavior and preserving historic tickets.

The existing system is paper-only. The UI cannot promote it to live trading. Postgame results, closing-price quality, execution credibility and prospective research evidence answer different questions.

## Proposed defaults for Fable to refine

These are design proposals, not additional user quotations or settled implementation choices:

- A small set of explained, system-generated ideas rather than a large odds catalog. Keep existing primary/fun-longshot concepts recognizable; allow declining an idea and requesting an alternative without spending money.
- A game-day overview, a ticket recommendation/tracking area, game details, and accessible research/system diagnostics. Fable chooses the names and navigation.
- Each recommendation explains its anchor, each leg's condition, source price age, relevant player/game context, relationships between legs, and known limitations. Do not label a prop/SGP “positive EV” just because a language model says so.
- A compact **I placed this** confirmation captures the actual stake, accepted combined odds and changed legs after manual placement. Previously generated legs can be prefilled for review. Optional paste/image import can be designed later; it is not a release-one requirement from this session.
- Supported DraftKings link where available; copyable exact selections and event/leg links as a fallback. No account-password collection or automated checkout.
- Persist favorite teams, followed games and recorded slips across phone/laptop. Existing LSU/Saints favorites start the experience. Personal settings can have a small, ordinary interface.
- In-app “since you last checked” summaries are useful. External push, SMS/email, chat integrations and real-time AI commentary are not required in release one.
- First-release UI is dark; an optional light theme is Fable's choice if it does not distract from the core work. Density and appearance are separate decisions.
- Private single-owner access on the home network. A phone/laptop bookmark should work without maintaining a phone SSH tunnel. The planner chooses the LAN endpoint and authentication arrangement; remote access is out of first-release scope.

## Fable's design latitude

Choose the final layout, visual tokens, typography, navigation, component system, motion, responsive breakpoints and information disclosure. A dense interface should not become a tiny-text wall or a horizontal desktop table squeezed into a phone. Keep numbers aligned, labels short, status meaning explicit, and deeper evidence one action away.

Use broadcast typography selectively for scores, game identity and a few meaningful numbers. Avoid generic slogans, heavy decorative gradients, excessive pill labels, repeated boxed summaries and invented urgency. Meaningful team identity, crisp type, clear relationships and good transitions should create the personality.

Do not include a video-streaming player, bookmaker account integration, cash-out execution, social sharing or public multi-user product unless separately added to scope. The goal is a companion to watching the broadcast, not a licensed broadcast service.

## What remains to resolve

| Owner/Fable/planner | Question | How to finish it |
|---|---|---|
| Planner | Home-network phone/laptop access | Provide a usable LAN endpoint with appropriate authentication; no internet exposure |
| Fable | Final information architecture and exact interactions | Produce the design specification and reviewed screens |
| Fable + planner | Supported prop families and SGP group shapes | Enumerate a launch coverage matrix; preserve the selected NFL/college scope |
| Planner | College live player-stat completeness | Evidence from candidate feeds; surface any unmet release requirement |
| Planner | Actual DraftKings full-slip link support | Verify representative phone/browser examples; retain truthful fallback |
| Owner + planner | Provider subscription selection | Present the tested shortlist and total costs within the ceiling before account activation |
| Planner | Read/write access, data contracts, safe migrations and resource budgets | Reconcile source and old UI constraints into a reviewed implementation addendum |

Fable need not ask the owner again about U01–U13. A materially different budget, wagering rule, provider account action or release-scope reduction needs a concrete decision; ordinary visual/implementation choices can be resolved within the selected direction.
