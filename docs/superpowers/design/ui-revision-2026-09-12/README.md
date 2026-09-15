# Harness UI revision — Fable design package

Prepared September 12, 2026. **Ready for a Fable design session. Not an implementation specification or a loop launch.**

Build a personal football companion that makes the owner enjoy watching games while understanding what the paper system saw, did, and learned. Give system-generated fun parlays equal product attention: review the idea, place it manually in DraftKings, then follow every leg.

The selected direction is **a dense, dark interface with selective sports-broadcast typography**. Fable owns the final design. The prototypes illustrate information and interactions; they are not screens to copy pixel for pixel.

## Start here

1. Paste [FABLE-DESIGN-PROMPT.md](FABLE-DESIGN-PROMPT.md) into the Fable design session with this package attached or available in the workspace.
2. Read [PRODUCT-BRIEF.md](PRODUCT-BRIEF.md) for confirmed decisions and design latitude.
3. Use [EXPERIENCE-CONTRACTS.md](EXPERIENCE-CONTRACTS.md) for user journeys, correct states, and concrete acceptance examples.
4. Use [DATA-AND-INTEGRATIONS.md](DATA-AND-INTEGRATIONS.md) for the researched API shortlist, DraftKings link feasibility, budget, and unresolved evidence.
5. After the design session, use [AFTER-FABLE.md](AFTER-FABLE.md) to turn the approved design into reviewed implementation work and prepare the later autopilot launch.

## Confirmed direction

| Decision | Requirement |
|---|---|
| Devices | Phone and laptop |
| Access | Home network only for the first release |
| Appearance | Dense layout, dark, selective broadcast typography; final design by Fable |
| Sports | NFL and college football |
| Tickets | Game lines, player props, and same-game parlays in the first release wherever DraftKings offers them |
| Preparation | The system figures out fun parlay ideas for the owner |
| Placement | Owner places manually in DraftKings; a reliable prefilled link is desirable where feasible |
| Watching | Follow game scores, player stats, exact leg conditions, and system decisions |
| Freshness | Freshest available data; no TV-delay or spoiler-pause feature in the first release |
| Additional sports APIs | $100/month ceiling; individual providers and purchases are not selected |
| Existing fun-money rules | Carry the existing $50/week ceiling and LSU/Saints anchors; this session did not change the stakes |
| Trading posture | Paper system remains paper; fun-ticket accounting stays separate |

The small post-placement confirmation form is a proposed default so tracking reflects the actual accepted slip. Screenshot import/OCR is not required by the user's clarification. Remote access is outside first-release scope.

## Reference concepts

Open [the interactive comparison](references/game-day-broadcast-concept.html), then switch **Broadcast / Dense desk**. Dense desk shows the desired information density; the user selected dark presentation, so its light palette is not the final direction. Try **My tickets → Same game + player props** for the expanded ticket scenario.

| Reference | Purpose |
|---|---|
| [Broadcast laptop](references/game-day-broadcast-desktop.png) | Score typography and stronger hierarchy |
| [Dense desk laptop](references/game-day-desk-desktop.png) | More information together, compact decision table |
| [Dense desk phone](references/game-day-desk-phone.png) | Mobile hierarchy and compact summaries |
| [SGP laptop](references/game-day-broadcast-sgp-desktop.png) | Player-stat progress and related legs |
| [Earlier concept](references/game-day-concept.html) | Historical interaction exploration; its spacious rounded/gradient look was rejected |
| [Session research](references/2026-09-12-ui-revision-working-brief.md) | Dated investigation and conversation history; later decisions in this package take precedence |

Every matchup, player, score, quote and trading event in the concepts is fictional. Prototype TV-sync controls, DIY leg selection, and oversized introductory copy are superseded by the final brief. They remain in the references as historical experiments, not implementation requirements.

## What happens next

Fable supplies the final information architecture, responsive designs, complete states, component/interaction definitions, and an implementation handoff. Then the planner reconciles the current repository and roadmap, resolves provider coverage, and creates the real spec/plan/verification rows. The owner can launch the loop when ready after that preparation.

This package does not modify the active roadmap, state, journal, controller configuration, production code, or running system. Unrelated setup/roadmap edits were already in progress in the shared workspace. Repository context was last read at local `main` commit `93dfb95773f5649d582ff39832d94241f1a8cdf3`; that is a historical reading, not a claim about the next session's source or deployment.

MANIFEST.json records the package contents and hashes. The zip contains only this package, including copied concept references; no credentials, account exports, or production data are included.
