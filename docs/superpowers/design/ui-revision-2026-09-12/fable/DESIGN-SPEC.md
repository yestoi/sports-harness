# Fun tickets inside the existing dashboard. Fable design specification

Date: 2026-09-13. Written in the Fable design session from the package one directory up (README, PRODUCT-BRIEF, EXPERIENCE-CONTRACTS, DATA-AND-INTEGRATIONS, AFTER-FABLE) and the user's decisions in that session. Status: **design output, approved in conversation section by section; not an implementation plan, not a roadmap phase, not a loop launch.** The planner turns this into the dated addendum under `docs/superpowers/specs/` and the plan under `docs/superpowers/plans/` per AFTER-FABLE §3, after reconciling current source.

Reading anchor: `main` a2a1791 (2026-09-13). The dashboard spec `docs/superpowers/specs/2026-09-07-dashboard-surfaces-design.md` ("the spec") and its phase 4.5 addendum remain binding wherever this document is silent. Where this document and the package's reference concepts differ, this document wins; the concepts are inspiration only.

## 0. Decisions taken in this session

| ID | Decision | Source |
|---|---|---|
| F01 | The revision lives inside the existing five surfaces. No new tab, no new navigation, no replacement shell. | User, 2026-09-13, choosing "Ticket grows, Floor gets game detail" |
| F02 | Ticket stays the only surface that shows real money and never shows a paper number. Floor never shows a fun-money amount. The two link by game and nothing else. | Spec §2.5 rule, reaffirmed by F01 |
| F03 | Card shapes are unchanged: the $25 smart card is cross-game and may carry prop legs; a same-game parlay is one kind of $5 lottery card, since lottery cards already allow correlated legs. Stakes, the $50 week and the LSU/Saints anchor are untouched. | User, 2026-09-13 |
| F04 | Placement is confirmed in the UI from the phone or laptop over the home network. This adds a LAN listener, an owner login and the dashboard's first write route beyond the kill pair. | User, 2026-09-13 |
| F05 | Release one includes favorite teams pinned (from the existing anchors) and live player-stat lines on prop legs. The "since you last checked" digest is out of release one. | User, 2026-09-13 |
| F06 | The package's proposed Game Room, Today/Tickets/Research/System navigation, TV-sync, light broadsheet treatment and oversized greeting copy are rejected. | User's brief and this session |
| F07 | Broadcast typography is used in exactly three places (§6). Everything else keeps the existing token set, faces and card grammar. | Fable, within U11/U12 |
| F08 | The interaction prototype deliverable is met by state artboards of the critical journey, not a clickable prototype. | Fable; see §10 limitations |

User decisions U01 to U14 in PRODUCT-BRIEF stand. Nothing here changes a stake, a limit, an anchor, a scientific criterion, the paper posture or a provider account.

## 1. Surfaces and navigation

Unchanged: the header, the five tabs (Pulse, Floor, Study, Gate, Ticket), the phone tab bar under 720 px, the status word and badges, the theme toggle, the hash router, the polling loop, the snapshot layer, the DOM rule (text nodes only), the sentence-first rule, and the glossary mechanism.

Changed:

| Surface | Change |
|---|---|
| Ticket | Gains **This week's ideas** above Live tickets; Live tickets gains prop legs, stat lines, corrections and the settlement-pending state; Season gains declined and expired chips and defined tile labels. "Between cards" becomes the empty state of the ideas section. |
| Floor | Game board cards become buttons opening a **game detail** (§4); favorite-team games sort first with a star; board cards drop technical field names in favor of one figures row. |
| Pulse, Study, Gate | No change. |
| How it works | One new paragraph on ideas, placement confirmation and corrections; glossary entries for every new label (§9). |

Routes: `#ticket` unchanged; `#floor/game/<game_id>` opens the detail and the back button closes it. `#ticket/card/<card_id>` scrolls to and expands one slip so a laptop link can point a phone at a card.

## 2. Ticket surface

Order on both widths: **This week's ideas**, **Live tickets**, **Season**. On the laptop at 1440 px the ideas section lays drafts side by side, three across; Live tickets keeps one slip per row at the slip's current width; Season is unchanged. On the phone every slip stacks.

### 2.1 The draft slip

A proposed card renders as a **draft slip**: the same night-paper slip (`.slip`, `--slip-paper`, `--slip-ink`) as a placed card, so there is one object to learn. Exactly three marks distinguish a draft:

1. A **dashed** perforation top and bottom instead of the dotted one.
2. The badge reads `PROPOSED · NOT PLACED` beside the kind badge.
3. Legs carry a **hollow ring** (`○` in `--slip-muted`) where a placed card has a lamp; no lamp animation runs on a draft.

The payout line reads `would pay $137.50 at +450` in the slip's body size, not the 40 px live payout; the large payout is earned by placement.

Kind badges: `SMART CARD · $25`, `LOTTERY CARD · $5`, `LOTTERY · SAME GAME · $5`. A same-game card is a lottery card with `correlated = true` and every leg in one game; the existing correlation sentence ("DraftKings will quote lower than this") is shown as written when the combined price is a calculation.

Contents, top to bottom:

- **Anchor line.** "carries LSU −3.5" or "carries Saints to win". One line, always present; a card without an anchor is not built (existing D14 rule).
- **Rationale.** The builder's sentences as today (template or the bounded model call), fan voice, sanitized.
- **Legs.** Each leg:
  - the plain text ("Nussmeier 225+ passing yards");
  - the **exact selection** in small technical type: player or team, market, period, side, line, DraftKings price, and the price's age ("DK −115 · 4 min ago");
  - a **context line**: for a prop, the player's season average and last game from the stat feed with its source ("avg 262 · last 241 · BDL"); for a game line, "sharps say NN %" as today;
  - for a same-game card, a **relationship note** under the legs that share a game: "both move on a Nussmeier completion"; "a touchdown pass moves both".
- **Footer.** The combined price stated as one of three things, never blurred:
  - `DraftKings quotes +450 for the slip` (a quoted combined price, with its age);
  - `+450 calculated from the legs · assumes independence` (the existing product; on a same-game card also the correlation sentence);
  - `no combined price · DraftKings will quote it in the app`.
  Then the hold line as today, and the week line: `$25 recorded · $25 left of $50`. Unspent money is stated, never urged.
- **Actions**, three buttons in the slip footer, in this order:
  - **Open in DraftKings**, labelled by the recorded link capability: `Open full slip` (`full_slip`), `Open selection` (`selection`), `Open event` (`event`), or `Copy selections` (`none`). `Copy selections` copies the exact-selection lines as text. A capability above `event` is shown only after the planner verifies it on the owner's devices (DATA-AND-INTEGRATIONS "DraftKings links").
  - **I placed this** (§3).
  - **Not this one.** Declines the version: status `void`, `declined_reason = declined`, and a request for a replacement of the same shape (a new card with `parent_card_id` set). No money moves. If the builder cannot produce a replacement, the draft is replaced by the no-idea sentence for that shape (§2.2).

Opening a link, copying, or leaving the page records nothing.

### 2.2 No idea: the named reasons

When a shape has no proposed card, its slot shows one sentence from a fixed vocabulary, plus the next build time. The vocabulary (a `reason_phrase` table like the existing one, tested for completeness):

| Code | Sentence |
|---|---|
| `no_anchor_priced` | No LSU or Saints price is fresh enough to build on. |
| `anchor_bye` | LSU and the Saints are both off this week. |
| `no_props_fresh` | No prop price inside the age limit. |
| `player_unmatched` | A prop we wanted names a player we cannot match to the stat feed. |
| `market_unsupported` | The market DraftKings offers is not one we grade. |
| `week_at_cap` | This week's $50 is fully recorded. |
| `not_built_yet` | The next card is built Saturday evening. (Friday for college, as today.) |
| `builder_failed` | The builder could not finish; the reason is in the log. |

The existing between-cards facts (next build day, anchor rule, budget left) stay under the sentence.

### 2.3 Live tickets

The existing slip, with these additions.

- **Prop leg stat line**, under the plain text: `208 of 225 passing yards · 17 to go · ESPN 40 s ago`. Source name and age always present. A player absent from the latest stat update reads `unchanged · last seen 2 min ago`, never zero.
- **Needs phrase** extended (§5.3): pushes on whole-number lines are called pushes; a milestone reached before the final reads `reached 59 of 50 · provisional until final`; an under below its line reads `under by 31 · live until the game ends`.
- **Correction note** on the leg: `corrected from 12 to 8 · ESPN`. The number moves down with no animation; the lamp does not replay.
- **Sharps say** on a prop only when a sharp book prices that prop; otherwise the leg reads `no sharp read` and no history bar is drawn.
- **Settlement pending.** A card whose legs have all hit reads `all legs hit · awaiting DraftKings settlement` and keeps its lamps lit; the CASHED stamp lands only when a return is recorded (`parlay_ledger.kind = 'return'`). A recorded void reads `voided by DraftKings · stake returned` and stamps `VOID` in `--slip-muted`. BUSTED lands on the first missed leg as today.
- **Corrected placement.** A slip with a correction row shows `corrected` beside the stake; tapping it shows the original figures.
- **On a Floor game.** No cross-reference from the slip to Floor; the crossing is one-way (§4).

### 2.4 Season

Layout unchanged. Tile labels become `staked`, `returned (includes stake)`, `net`. The strip gains grey chips for `declined` and `expired` cards so the offered history is visible; those cards are not in the ledger and the figures do not move. Best hit and streak unchanged.

### 2.5 States

Every state below is a fixture in the plan and an artboard in §7.

| State | Rendering |
|---|---|
| Ideas present, nothing placed | Three drafts (or fewer), Live tickets says "no ticket live right now", Season |
| One draft placed, others open | The placed card moves to Live tickets; its slot in ideas shows `placed` with a link down |
| No idea for a shape | The no-idea sentence for that slot |
| Every shape at cap | `week_at_cap` in each slot; Live tickets carries the placed cards |
| Stale quote | Price age over the config limit: the leg's price line turns `--warn` and the footer reads `prices older than 30 min · rebuilt at the next tick`; the actions stay |
| Removed prop | Leg reads `no longer offered by DraftKings`; the draft's Open button drops to `Copy selections`; I placed this stays (the owner may have placed before removal) |
| Declined | Slot shows the replacement or the no-idea sentence; the declined version is a grey chip in Season |
| Snapshot section failed | "unavailable" as today; never "no ticket live" |
| Disconnected browser | The shell's existing staleness banner; the confirm sheet refuses to submit and says why |

## 3. The handoff and "I placed this"

### 3.1 Flow

1. Open the draft, read the legs.
2. **Open in DraftKings** with the labelled capability; place the ticket in DraftKings.
3. Back in the app, **I placed this** opens a **sheet on the slip** (a `<dialog>` on the laptop, full-width bottom sheet on the phone). Nothing else on the page changes.
4. The sheet is prefilled: stake (the card's stake), each leg's proposed line, and an empty **accepted odds** field (American, required). A leg whose DraftKings line differed is typed over in place; the field shows `was −3.5` beside it. Optional note (200 chars).
5. **Record** submits. On success the sheet closes and the draft flips to a live slip in place: badge, perforation and rings change, the payout grows to live size, lamps light with their existing animation.

Every step before 5 records nothing. A link click, a copy, or a returned tab never implies placement, and the app never reads an accepted price from an external page.

### 3.2 Rules carried from the recorder

The write route calls `harness.parlay.placement.mark_placed` and inherits its refusals, shown in the sheet in plain words:

| Refusal | Sheet text |
|---|---|
| `BudgetExceeded` | `$25 is already recorded this week; $25 more would pass $50. Nothing was recorded.` |
| `CardNotPlaceable` | `This card is no longer open (it is placed / void). Nothing was recorded.` |
| `LineMoved` | `DraftKings moved leg 2 from −3.5 to −4. Type the line you got, or cancel.` The moved legs are highlighted; typing a line resubmits with `leg_lines`. |

The weekly cap is never relaxed in the UI. An already-placed out-of-budget ticket cannot be recorded; the sheet says so and the owner's remediation is a separate policy decision (EXPERIENCE-CONTRACTS §2), not a UI path.

### 3.3 Idempotency

The sheet generates a `confirmation_id` (UUID v4) when it opens and sends it with every submit. The route records it on the placement row (`parlay_placements.confirmation_id`, unique). A repeat with the same id returns the existing placement as success; a different id for an already-placed card returns `CardNotPlaceable`. A retry after a lost response, a double tap, and a phone and a laptop submitting together therefore record one stake, and the cap check runs inside the same transaction as the ledger insert, under a row lock the route takes on the card (`select ... for update`), which the command-line path does not need today.

### 3.4 Corrections

A placed slip is immutable. A later change of stake, accepted odds or a leg's line writes one row per changed field to `parlay_placement_corrections(card_id, ts, field, old_value, new_value, note)` through a second route. The ledger's stake row is corrected by an offsetting `stake` row (negative or positive delta) in the same transaction so the week's cap and the season figures follow the corrected stake. The slip shows `corrected` and the original on tap. Corrections after grading are refused; grading uses the corrected terms.

## 4. Floor game detail

### 4.1 Opening

Each board card is a `<button>` (accessible name: the matchup). It opens the detail at `#floor/game/<id>`: on the laptop a full-width card inserted directly under the board row containing the game, with a close control; on the phone a full-height sheet over the board. Escape and the back button close it. One detail open at a time.

### 4.2 Contents

1. **Scoreline.** Away and home abbreviations in the display face at 40 px with the score beside them in the numeral face at the same size; period and clock with their source age (`Q4 · 8:42 · 40 s ago`); before kickoff, the kickoff time in Central and the countdown. The clock is the source's state, never animated.
2. **Our position.** Contracts held, entry price, paper stake and the result the position needs in the slip's phrasing: `LSU winner YES · 40 contracts at 58¢ · needs LSU to win`. Before kickoff: what the strategy is watching on this game and until when. During play: `placed before kickoff · following the outcome`. Never a word that implies in-play trading. No position: `no position on this game` with the reason from the story below.
3. **Decision story.** Chronological rows from records that already exist, each with its own time and unit:

   | Row | Source | Text shape |
   |---|---|---|
   | price observed and evaluated | `signals` bounded by this game's `venue_market_id`s | `5:42 PM · evaluated · fair 62¢ against 58¢ on the book` |
   | passed | `signals.rejection_reason`, `intents` skips | `5:51 PM · passed · our fair price was too old` (the existing reason phrases) |
   | order resting | `orders`, `order_watch_samples` | `5:42 PM · resting · 40 at 58¢ · 120 ahead of us` |
   | partial or full simulated fill | `fills` | `5:44 PM · filled · 40 of 40 at 58¢ (simulated)` |
   | cancelled or expired | `order_events` | `6:02 PM · cancelled · the book moved away` |
   | closing benchmark | `gap_outcomes` | `kickoff · closing 64¢ · we entered at 58¢` |
   | settlement | `positions`, `ledger` | `final · settled · +$16.80 paper` |
   | gap | derived | `no records between 5:51 PM and kickoff` |

   A row expands (`<details>`) to its source facts: the ids, the variant, the raw figures. "Evaluated and passed", "not evaluated" and "evaluation failed" are three different first rows. Gaps are rows, never smoothed over. Real market trades, simulated fills and replayed fills are labelled with those words.
4. **Markets.** The markets we can price on this game as a short table: market, fair, book, edge now, age. The figures Floor already computes for open orders, extended to the game's priced markets.
5. **On your ticket.** If a live slip has a leg on this game: one line, `on your ticket`, linking to `#ticket/card/<id>`. No amount, no leg detail. This is the only crossing between the two worlds and it is one-way.

### 4.3 Bounds

The detail is built by the Floor snapshot builder for games in the board window only, keyed by game id, reading only the bounded tables the phase 4.5 addendum allows; the five forbidden tables stay forbidden. A detail for a game outside the window says `not in the board window`. Payload size per game is capped and measured under the existing snapshot budget rows.

### 4.4 Board card

The board card keeps its matchup and kickoff or score, and replaces the two labelled figures with one figures row: `46 markets · 0 resting`. The technical names move to the detail's glossary terms. Favorite-team games (§5.5) sort first with a star before the matchup.

## 5. Data and contracts (additive only)

### 5.1 Legs

`parlay_legs` gains: `player_id int null`, `stat String(12) null` (`pass_yds`, `rush_yds`, `rec_yds`, `receptions`, `anytime_td`, or null for a game line), `period String(6)` (`game`, `1h`, `2h`, `q1`..`q4`; default `game`), `operator String(6)` (`over`, `under`, `atleast`, `yes`), `market_def String(120)` (the recorded DraftKings market definition, e.g. "Anytime TD scorer: player scores a rushing or receiving TD"), `dk_link String(300) null`, `dk_sid String(64) null`. `market_type` widens to `String(12)` to hold `prop`. The needs function reads `stat`, `period` and `operator` for a prop leg.

### 5.2 Cards

`parlay_cards` gains: `policy_version String(16)`, `parent_card_id int null`, `declined_reason String(16) null` (`declined`, `expired`), `combined_kind String(10)` (`quoted`, `calculated`, `none`), `dk_combined_american int null`, `dk_combined_at timestamptz null`, `link_capability String(10)` (`full_slip`, `selection`, `event`, `none`). Status vocabulary unchanged.

### 5.3 Player stats

New `player_stat_events(id bigserial PK, game_id int, player_id int, ts timestamptz, source_ts timestamptz null, stat String(12), value Numeric(8,2), source String(12), raw_id bigint null, correction bool default false)`, index `(game_id, player_id, ts desc)`. Written by a collector for the games and players on placed or alive cards only (bounded polling; no watching of every game). New `players(id, sport, espn_id, provider_id, name, team_id)` with an identity map that fails loudly on an unmatched player (`player_unmatched`).

The needs function gains a `StatState(stat, value, source_ts, final: bool)` input for prop legs: `atleast` and `over` compare value against threshold with provisional wording until final; `under` stays live until the leg's period ends; `yes` (anytime TD) hits on the first qualifying scoring event per `market_def`. Missing stat state reads as unknown, not zero.

### 5.4 Placements and corrections

`parlay_placements` gains `confirmation_id String(36) unique null`. New `parlay_placement_corrections(id, card_id, ts, field String(16), old_value String(32), new_value String(32), note String(200))`. Ledger unchanged; a correction writes an offsetting stake row.

### 5.5 Favorites

No table. `parlay.yaml`'s `anchors` list is the favorite list; the Floor builder reads it through the existing config loader.

### 5.6 Generation policy

`parlay.yaml` gains a `policy_version` and the prop and same-game rules under it: the prop pool (which stat families, which books, the age limit reused), the same-game assembly rule (anchor plus 2 to 5 legs from the anchor's game, each a different player or market, correlated flagged), and disqualifiers (unmatched player, unsupported market, stale price). Every card records the version it was built under. Changing the policy is a config commit, never a UI action.

### 5.7 Providers and budget

The Odds API request adds `includeLinks` and `includeSids` under a fixed monthly credit allocation recorded in config and enforced against quota headers; the existing strategy feed's allocation is protected first. The player-stat provider is chosen by the planner from DATA-AND-INTEGRATIONS inside the $100 ceiling, with NFL and college coverage measured before launch scope is called complete; a college gap is reported as an unmet requirement, not narrowed silently. No provider account is opened by this document.

### 5.8 Access

A second listener on Omarchy's LAN address serves the same app over HTTPS with a self-signed certificate installed once on the phone and the laptop. The loopback listener and the tunnel stay. Reads and writes on the LAN listener require a session cookie obtained by a single owner password whose hash lives in `secrets/`; the cookie lasts the season and is `Secure`, `HttpOnly`, `SameSite=Strict`. The kill pair keeps its token header. Routes: `POST /api/parlay/placed` (card id, confirmation id, stake, accepted odds, leg lines, note) and `POST /api/parlay/correct` (card id, field, new value, note), both answering the recorded row or a named refusal with HTTP 409. No provider credential reaches the browser. Remote and public access remain out of scope.

## 6. Visual language

Kept: every token in `app.css`, both themes, the three faces, the card grammar, the slip, the lamps and stamps, the sentence-first rule, and the no-vendored-fonts decision.

Broadcast typography in exactly three places:

1. The live payout on a placed slip (exists, 40 px display face).
2. The scoreline in the game detail (40 px display face for abbreviations, numeral face for the score).
3. Team abbreviations on favorite games on the board (display face, 15 px, with the star).

Density, from tightening what exists: the board card's one figures row; the slip's leg rows as a two-column grid at 1440 (plain text and selection left, score, needs and sharps right); three drafts across at 1440. New components are limited to the draft marks, the confirm sheet, the game detail and the stat line. Type never below 12 px; touch targets 44 px; focus ring visible; reduced motion honoured (lamps and stamps already respect it). No new colour outside the token set, so light theme holds.

## 7. Screens

Drawn 2026-09-13 as fourteen artboards in this directory (`*.dc.html`, laid out by `canvas.json`), using the phase 4.5 canvas's tokens and faces, 390 and 1440 wide, every value fictional and marked. Canvas: https://claude.ai/code/artifact/fdcd5d4f-69a5-4b04-8197-85fcb33a18b4

| Artboard | Frame | Shows |
|---|---|---|
| `Main.dc.html` | 1440 | Ticket, three drafts (smart, same-game, lottery), Live tickets empty, Season with declined and expired chips |
| `TicketIdeasPhone.dc.html` | 390 | The same, stacked |
| `TicketNoIdea.dc.html` | 390 | Three no-idea slots with named reasons |
| `ConfirmSheet.dc.html`, `ConfirmSheetMoved.dc.html`, `ConfirmSheetCap.dc.html` | 390 | The sheet blank, with a moved line, and refused at the cap |
| `LiveSlipProps.dc.html`, `LiveSlipPropsPhone.dc.html` | 1440, 390 | A live slip with two prop legs, a stat line, a correction and a provisional crossing |
| `LiveSlipPending.dc.html` | 390 | All legs hit, settlement pending, no stamp |
| `FloorBoard.dc.html` | 1440 | The board with favourites first and the one figures row |
| `GameDetail.dc.html`, `GameDetailGap.dc.html` | 1440 | The detail open under the board, with a full story and with a failed evaluation and a gap |
| `GameDetailPhone.dc.html`, `GameDetailNoPosition.dc.html` | 390 | The detail as a sheet, with a position and with none |

The list that was drawn from:

1. Ticket, ideas present, 1440 and 390 (three drafts; the same-game draft with relationship notes)
2. Ticket, no idea for one shape, 390
3. Confirm sheet, prefilled, 390; with a moved line, 390; with the cap refusal, 390
4. Live slip with prop legs, a correction and a provisional crossing, 1440 and 390
5. Live slip, all legs hit, settlement pending, 390
6. Floor board with favorites pinned and the figures row, 1440
7. Game detail, in play with a position, 1440 and 390
8. Game detail, no position, evaluated and passed, 390
9. Game detail with a gap in the story, 1440

## 8. Acceptance

The eight production examples in EXPERIENCE-CONTRACTS §7 carry into the plan unchanged. Added:

1. A draft slip and a live slip render from the same card fixture, differing only in perforation, badge, rings, payout line and actions.
2. Every no-idea code renders its sentence; an unknown code renders as itself and is reported in `sentences_gaps`.
3. The three refusals render in the sheet from fixture responses; the moved-line case resubmits with `leg_lines` and succeeds.
4. Two submits with one confirmation id record one placement and one ledger row; two ids on one card record one placement and one 409.
5. A stat correction fixture lowers the stat line and adds the note with no lamp replay.
6. A card with all legs hit shows settlement pending; a return row stamps CASHED; a void row stamps VOID and returns the stake.
7. The game detail renders a full story, a no-position story, a not-evaluated story and a gapped story from fixtures; no row is invented for a gap.
8. Every new label has a glossary entry (the existing walk over `LABELS`).
9. Static budget, no external URL, and the DOM rule tests pass with the sheet and detail added.
10. Walker at 390 and 1440: ideas section, a draft's three actions reachable by keyboard, the sheet opening and closing by keyboard, the game detail opening from a board card and closing with Escape.
11. LAN listener: a request without a session is refused; the kill pair still works with its token; a placement from the phone is visible on the laptop within one Ticket cadence.
12. Builder and detail measured under the existing snapshot budget rows.

## 9. Glossary additions

`draft`, `proposed`, `anchor`, `same game`, `combined price`, `quoted`, `calculated`, `link capability`, `confirmation id`, `correction`, `provisional`, `settlement pending`, `stat line`, `source age`, `decision story`, `not evaluated`, `gap`, `favorite`. Each with `plain`, `what`, `why` as the glossary file requires.

## 10. Limitations and open items

- **No clickable prototype** (F08). The critical journey is drawn as state artboards; the existing package HTML shows interaction ideas but not this design.
- **Link capability above `event` is unverified** until the planner checks destinations on the owner's phone and laptop.
- **College player-stat completeness is unmeasured**; launch scope stays NFL and college and a gap is an unmet requirement.
- **Provider selection and allocation** are the planner's, inside the $100 ceiling, with no account opened here.
- **The digest** ("since you last checked") is deferred by F05.
- **Access mechanics** (certificate installation, listener binding, session storage) are implementation choices within §5.8's rules.
- **Sharps say for props** depends on a sharp book pricing the prop; the design shows `no sharp read` otherwise.

## 11. Implementation handoff

Outputs of this session: this file and the artboards of §7 under `docs/superpowers/design/ui-revision-2026-09-12/fable/`. The planner records their paths and hashes in the addendum (AFTER-FABLE §1). Source contracts to inspect before planning: `harness/parlay/{build,placement,needs,pricing,config}.py` and `parlay.yaml`; `harness/dashboard/snapshots/{ticket,floor}.py`, `sentences.py`, `app.py`; `static/js/{ticket,floor,components}.mjs`, `app.css`, `glossary.json`; `harness/db/models.py` and the migration head (`0007_raw_events_lookup` at the reading anchor); `docker-compose.yml`, `compose.omarchy.yml`, `deploy/`. Suggested slices follow AFTER-FABLE §4 (A to F). Old rules this design deliberately changes, for the addendum's table: the Ticket surface's "no control anywhere" (three slip actions and the sheet); "UI read-only except kill" (two write routes); "game-line-only legs" (§5.1); "loopback only" (§5.8); the board card's labelled technical figures (§4.4).
