# Restart Visual Review — build 93dfb95

Source of record: `/home/trey/dev/sports/.superpowers/sdd/screenshots/restart-deployed-93dfb95-browser.json`
(`local_css_preview` is `null` on every page — this is the actual deployed CSS, not a preview.)
All screenshots referenced below live under `/home/trey/dev/sports/.superpowers/sdd/screenshots/`.
No source or JSON was modified. No form was submitted, no input typed, no Kill/Unkill activated.

Overall verdict: **FAIL** (freshness header is correct — `build 93dfb95` — so no FRESHNESS-FAILED stop condition,
but 3 items FAIL and 3 items are PENDING; see below).

Counts: **PASS 23, FAIL 3, PENDING 3** (0 formally DEFERRED — item 9's staleness table was populated, so no deferral applied).
FAIL items: 18, 19, 23.
PENDING items: 16, 22, 29.

---

## Legacy panel (`/`, 1440 px only)

Evidence: page[0] of the JSON (route `legacy`, width 1440), screenshots
`restart-deployed-93dfb95-1440-legacy-top.png`, `restart-deployed-93dfb95-1440-legacy-full.png`.

**1. Header build stamp — PASS**
Text: "Generated 2026-09-13T02:11:20.922870+00:00 · build 93dfb95 · deployed 2026-09-13T02:00:29.572388+00:00".
No FRESHNESS-FAILED condition triggered.

**2. Health status/credits/kill switch — PASS**
`STATUS ok`, `CREDITS REMAINING 4906693` (numeric), `KILL SWITCH off` with badge class `badge ok` (green), confirmed
in DOM overflow dump and in the screenshot.

**3. Funnel raw rows by source — PASS**
Raw rows by source: espn 171, kalshi 129547, odds_api 15283 (plus nws 62472, not required). All non-zero. Note: the
source label is literally `odds_api`, not `odds`; treated as the same odds feed per the checklist's intent.

**4. Signals per active variant — PASS (with count discrepancy noted)**
Table renders columns variant/tier/candidate/rejected. Actual rows: constrained, nfl_only, no_velocity, sharp_direct
(primary), sharp_plus_derived, sharp_two_sided, wide_band (7 non-replay variants) plus
`sharp_two_sided#e82fcd0a1e99` tagged tier `replay` — 8 rows total, not the "six" cited in the checklist. All rows
show candidate/rejected counts (all 0/0 in this snapshot). Rendering requirement is met; the variant-count text in
the checklist is stale relative to the deployed variant list — reported as data, not overridden.

**5. Match report nfl/ncaaf — PASS**
`nfl — 724 markets (100.0% matched)`; `ncaaf — 5118 markets (78.4% matched)`. Both blocks present with percentages.

**6. Signals table (primary variant) — PASS**
"Last 100 signals (primary variant)" table has columns time/variant/game/contract/fair/source/bid/ask/target/edge/
decision/reason, populated with `sharp_direct` (the primary-tier variant) rows.

**7. Unmatched markets table — PASS**
"Unmatched markets (top 50 by volume)" renders with ticker/match_status/reason/volume_24h rows (50 rows present).

**8. WebSocket — PASS**
`ORDERBOOK EVENTS (5 MIN) 497224`, `WS TRADES (1H) 116922` (both numeric), `LAST EVENT
2026-09-13T02:11:21.055000+00:00`, essentially the same instant as the page's own "Generated" timestamp
(02:11:20.92) — well within 60 minutes.

**9. Data quality staleness by book — PASS**
"Feed staleness by book (median, last 1h)" is populated (not empty): betonlineag 34.6, draftkings 31.2,
fanduel 21.4, kalshi 14.7, lowvig 37.7, novig 56.7, **pinnacle 15.3**. Because the table has data, this item is
scored normally, not deferred.

**10. Executor heartbeat — PASS**
`HEARTBEAT AGE (S) 6` rendered with an `ok` badge (green pill), well under the 60 s bound. Confirmed in both the
DOM dump (`badge ok` class family) and the screenshot.

**11. Open paper orders / today's fills — PASS**
Both tables render with correct headers (Open orders: id/variant/ticker/side/prob/contracts/filled/status/
book_source/dirty_minutes/placed_at; Fills today: id/order_id/variant/ticker/side/prob/contracts/fee/filled_at).
Zero rows in both — allowed, rows are optional.

**12. Paper P&L / open exposure — PASS**
P&L: variant `f259ca109084`, cash_delta -17.68, fees 0.1686. Open exposure: same variant,
`KXNCAAFTOTAL-26SEP12MTUMRSH-59`, side yes, open_contracts 38.92, avg_price 0.45. Numbers present.

**13. Candidates since the staleness fix — PASS (with caveat)**
"Candidates per variant" table renders a count per variant (all 0 in this snapshot: constrained, nfl_only,
no_velocity, sharp_direct, sharp_plus_derived, sharp_two_sided, sharp_two_sided#e82fcd0a1e99, wide_band). The
section itself carries a visible caveat: "not split: served from run notes (fix 17)" — i.e. the count is not
actually partitioned at the staleness-fix boundary the label implies. Reported as visible data; the per-variant
count table is present and renders, which is the literal ask.

**14. Database size vs budget — PASS (color read from a scaled screenshot; see caveat)**
`SIZE (GB) 62.40`, `% OF BUDGET (600 GB) 10.4%`, `DAYS TO CEILING 182 partial`. 182 > 30. In the Pulse page's
equivalent Storage tile (same underlying metric, `db.growth_gb_per_day` → 181.696...) the number renders in plain
white/grey text against a blue progress bar, not red. The legacy page's own full-page screenshot at reduced scale
shows the same figures in the default (non-red) text color. Note the visible "partial" qualifier on the ceiling
figure — reported as data.

**15. No `unavailable:`/exception, load under 10 s — PASS**
Searched the full page text for "unavailable" and for `Error`/`Exception`/`Traceback` patterns: none found on the
legacy page. Navigation timing: `domComplete` 543.7 ms, `first-contentful-paint` 552 ms — far under 10 s.

**16. Skip-reason table / no_book dominance — PENDING**
The "Skip reasons" table renders (headers `reason`/`count`) but has **zero rows** — no `no_book` or any other
reason is present in this snapshot. The Floor board simultaneously shows many live/imminent games ("kicks off in
0 s", games in progress), so a kickoff is within the 3-hour window, but there is no skip-reason data to evaluate
whether `no_book` dominates. Table-render requirement is met; the substantive "not the only reason" check cannot
be assessed from an empty table, so this is PENDING rather than PASS/FAIL.
Evidence: `restart-deployed-93dfb95-1440-legacy-full.png`; JSON page[0] text, "Skip reasons\nreason\tcount\nDatabase"
(section is immediately followed by "Database" with no intervening rows).

---

## Surfaces (`/ui/#pulse|floor|study|gate|ticket`), 390 px and 1440 px

Evidence: pages[1–10] of the JSON. 1440 px = pages 1–5 (pulse, floor, study, gate, ticket); 390 px = pages 6–10
(same order). Screenshots referenced per item below.

**17. `/ui/` responsive layout — PASS at 390 px and 1440 px**
At 390 px, for all five surfaces, `viewport.body === viewport.client === 390` in the JSON (no horizontal body
overflow) and the tab bar (Pulse/Floor/Study/Gate/Ticket with icons) renders fixed at the **bottom** of the
viewport, confirmed visually on:
`restart-deployed-93dfb95-390-pulse-top.png`, `-390-floor-top.png`, `-390-study-top.png`, `-390-gate-top.png`,
`-390-ticket-top.png`. No section required the whole page to scroll sideways; any wide table content wraps or
clips inside its own container rather than widening the body. At 1440 px all five surfaces render normally
(`-1440-*-top.png`).

**18. Header status/badges/ages — FAIL at both 390 px and 1440 px**
The header status word reads **`BROKEN`** on every surface (Pulse, Floor, Study, Gate, Ticket, both widths) — not
`FINE` or `WATCH`. Per the brief's explicit instruction, a correctly-functioning BROKEN indicator does not make
this item PASS: the literal status-word requirement is not met. Fired rules are `tape_gap`, `check_fail`,
`check_skipped`, `book_dirty_in_game` — `snapshot_disabled` is **not** among them, so that specific extra-FAIL
trigger does not additionally apply here. The `PAPER` badge does correctly render on Pulse, Floor, Study and Gate
(and `FUN MONEY · $50/WEEK · PLACED BY HAND` on Ticket, as expected). The "HOW FRESH IS EACH PAGE'S DATA?" table
shows floor 29 s/gate 2 min/pulse 1 min/study 6 min/ticket 30 s, all against their fix-31 cadences (120/300/60/
600/120 s), with empty `state`/`error` columns and no staleness banner anywhere — the age-styling and staleness-
banner sub-conditions are satisfied, but the overall item fails on the status word alone.
Evidence: `restart-deployed-93dfb95-1440-pulse-keyboard.png`, `-1440-floor-top.png`, `-390-gate-top.png`,
`-390-study-top.png`, `-390-ticket-top.png`; JSON pages[1–10] text (`"BROKEN\nPAPER\n..."` on every surface).

**19. Pulse: required sections render, no `unavailable`/exception — FAIL at both widths (exception text present)**
Status card, tape strip ("WAS THE TAPE CONTINUOUS?"), vitals row, storage arc (`STORAGE` tile with db.size_gb
gauge), invariant wall ("DID EVERY INVARIANT HOLD? · check_results", 23 passed/3 failed/1 could not run with 28
individual check tiles), and "RECENT OPERATOR EVENTS" all render at both 390 px and 1440 px. No section shows the
literal word "unavailable". However, the Recent Operator Events log's `what` column shows, twice,
`WebSocketConnectionClosedException(Connection to remote host was lost.)` — a raw exception class name/repr
embedded in the events feed. The checklist item explicitly forbids any section showing an exception class name;
this is visible verbatim in both the JSON text and the full-page screenshot, so the item is scored FAIL on that
clause even though every required section is otherwise present and populated.
Evidence: `restart-deployed-93dfb95-1440-pulse-full.png`, `-390-pulse-full.png`; JSON pages[1] and [6] text,
"RECENT OPERATOR EVENTS" block.

**20. Pulse: status card / tape strip open with plain-English sentence — PASS at both widths**
Status card opens: "Right now the machine reads BROKEN, on 4 rules." Tape strip opens (after its glossary term):
"Over the last 24 hours 5 feeds were recording." Both are plain-English sentences, confirmed in JSON text and
`restart-deployed-93dfb95-1440-pulse-top.png` / `-390-pulse-top.png`.

**21. Floor: game board / funnel / venue tile — PASS at both widths**
Game board: "50 games on the board, 28 in progress," with individual game rows rendered (rows present, not just
optional-and-absent) at both widths — see `restart-deployed-93dfb95-1440-floor-top.png` and `-390-floor-top.png`.
Funnel ("FUNNEL, TRAILING 6 H") shows counts for Raw ticks (0), Gap snapshots (0), Candidate signals (0), Intent
verdicts (0), Orders placed (0), Orders filled (0) — all six required stages present with numeric counts. Venue
tile ("VENUE CALLS · venue_requests") shows `0`, "no control breach", and a breakdown table `env=prod
method=GET count=29` with no other method row — i.e. the production non-GET count is 0, satisfying the control
condition. Confirmed identically in JSON pages[2] (1440) and [7] (390) text.

**22. Study: week chosen / ledger interval marks / markdown block — PENDING at both widths**
A week is selectable and shown: "WEEK 2026-37" with a clickable `2026-37` control, "snapshot built
2026-09-13T02:04:29...". The Variant Ledger renders interval marks with a game count beside each row/cell (e.g.
`n=17 games`, `n=35 games`, `n=-- games` placeholders where uncounted) — this sub-requirement is met. However,
the "THE WEEK'S REPORT" section at the end of the page reads only: "no report has been stored for this week yet"
— there is no markdown content, so the required "plain text in a collapsed block with a hash beside it" cannot be
observed or checked for HTML-rendering. Scored PENDING rather than PASS/FAIL because the underlying data needed
to verify the specific rendering behavior is simply absent this snapshot, and item 22 (unlike 24/28) carries no
explicit "if absent, pass" rule.
Evidence: JSON pages[3] (1440, tail of the 193,717-char text) and [8] (390); screenshots
`restart-deployed-93dfb95-1440-study-top.png`, `-390-study-top.png` (full-page study screenshots were truncated
per `full_capture_truncated: true`, but text extraction was not width/height-limited and captured the full DOM
text including the "no report" line for both widths).

**23. Gate: PASSING/NOT PASSING, date, hash, twelve criteria — FAIL at both widths**
Page reads: "No gate evaluation has been stored yet." Verdict placeholders show `--`/`-- pass`/`-- fail`/
`-- insufficient` — not the required single word `PASSING` or `NOT PASSING`. `criteria hash --` and
`variant: --` — no evaluation date and no real hash. "THE TWELVE CRITERIA" section reads "No criteria are stored
for this evaluation." / "no criteria stored yet" — zero criteria rows, let alone twelve with stored definitions
and thresholds. Clear FAIL on every sub-condition of this item.
Evidence: `restart-deployed-93dfb95-1440-gate-top.png`, `-390-gate-top.png`; JSON pages[4], [9] text.

**24. Ticket: badge / between-cards state / no paper or research variant — PASS at both widths**
Badge reads exactly `FUN MONEY · $50/WEEK · PLACED BY HAND`. Between-cards state present: "No card is live right
now.", "The next one is built on Saturday evening, and it always carries an LSU or Saints leg.", "$50.00 of this
week's $50 is still unspent." (next build day, anchor rule, budget left — all three). Searched the full ticket
page text at both widths for the strings "paper", "research", "variant" (case-insensitive): none found.
Evidence: `restart-deployed-93dfb95-1440-ticket-top.png`, `-390-ticket-top.png`; JSON pages[5], [10] text.

**25. Keyboard: glossary term opens on focus; arrow key moves tab selection — PASS at both widths**
JSON `glossary_keyboard` field on pages[1] and [6]: `{"term": "tape", "expanded": "true", "tooltip": "THE
RECORDED ORDER BOOK ..."}` — the dotted-underlined "tape" term's definition opened purely from keyboard focus (no
mouse event in the capture). `arrow_from_focused_tab`: `"#floor"` on both pages — pressing the right arrow key
while a tab-bar button was focused moved the selection to the Floor surface, confirmed as actual recorded keyboard
interaction, not an inferred/assumed one. Visual confirmation of the open tooltip on focus:
`restart-deployed-93dfb95-1440-pulse-keyboard.png`, `-390-pulse-keyboard.png`.

**26. No control under `/ui/` — PASS at both widths**
Across all ten surface-page control lists (pages[1–10]) there are zero `FORM` elements and zero controls whose
text mentions "kill" (case-insensitive) — no Kill/Unkill anywhere under `/ui/`. The only link whose `href` leaves
the surfaces is `Legacy panel` (`href="/"`); the other link, `How it works` (`href="#how"`), is an in-page anchor.
Note: the five tab-bar buttons (Pulse/Floor/Study/Gate/Ticket) carry a `type="submit"` HTML attribute, but no
enclosing `FORM` was found in the DOM dump for any surface, so clicking them cannot submit anything — this looks
like a vestigial attribute on client-side-routed buttons, not an actual submission control, but is noted for the
record since the checklist is otherwise read literally elsewhere in this review.

**27. Pulse: research tile spend/reserved — PASS at both widths**
"RESEARCH SPEND AND THE VETO — Research spend today: $0.13 of $25.00, with $0.00 reserved. This week: $24.73 of
$150.00." Reserved figure is $0.00 in this snapshot (between calls, not mid-call), and neither figure reads "not
evaluated." Identical text confirmed on pages[1] (1440) and [6] (390).

**28. Ticket: card rationale plain text, or "no card" — PASS at both widths (no card this week)**
"LIVE TICKETS — no ticket live right now." No card is live, so per the item's explicit fallback rule this is
recorded as "no card" and passes. Confirmed on pages[5], [10] and screenshots `-1440-ticket-top.png`,
`-390-ticket-top.png`.

**29. Study: annotation block placement — PENDING at both widths**
As in item 22, "THE WEEK'S REPORT" section shows "no report has been stored for this week yet" — there is no
markdown fence to inspect for a "Model notes (model-written, unverified)" block, so its placement (inside vs.
outside the fence) and any citation content cannot be verified this snapshot. Noted for the controller: Pulse's
`research.annotations_week` metric reads `2` (both widths) while Study simultaneously reports no report stored
for the current week — a visible inconsistency between the two surfaces, reported as data rather than resolved
here. Scored PENDING, not FAIL, because no fence/annotation content is present to judge as misplaced.

---

## Files referenced (all read-only)
- JSON: `/home/trey/dev/sports/.superpowers/sdd/screenshots/restart-deployed-93dfb95-browser.json`
- Screenshots (legacy): `restart-deployed-93dfb95-1440-legacy-top.png`, `-1440-legacy-full.png`
- Screenshots (pulse): `-1440-pulse-top.png`, `-1440-pulse-full.png`, `-1440-pulse-keyboard.png`,
  `-390-pulse-top.png`, `-390-pulse-full.png`, `-390-pulse-keyboard.png`
- Screenshots (floor): `-1440-floor-top.png`, `-1440-floor-full.png`, `-390-floor-top.png`, `-390-floor-full.png`
- Screenshots (study): `-1440-study-top.png`, `-1440-study-full.png`, `-390-study-top.png`, `-390-study-full.png`
- Screenshots (gate): `-1440-gate-top.png`, `-390-gate-top.png`
- Screenshots (ticket): `-1440-ticket-top.png`, `-390-ticket-top.png`, `-390-ticket-full.png`

No instruction-like content encountered inside the reviewed data required escalation beyond what is noted above
(the "not split: served from run notes (fix 17)" caveat on item 13, and the exception-name text on item 19, are
reported as observed data, not treated as instructions).
