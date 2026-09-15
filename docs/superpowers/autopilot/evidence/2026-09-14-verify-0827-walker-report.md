# Deployment walkthrough review — verify-6b9b8d0

Release 6b9b8d0, app-only, Omarchy, Mon 2026-09-14 08:24 CT (outside any game window).
Evidence: screenshots in `.superpowers/sdd/screenshots/verify-6b9b8d0-*.png` and
`.superpowers/sdd/screenshots/verify-6b9b8d0-browser.json`. Read via the screenshot tool
plus OCR (tesseract) run against the same PNGs through the sandboxed shell for
high-density tables; no browser was used and no image was altered before judging it
(OCR outputs were used only as a reading aid, cross-checked against the rendered image).

## Verdict: FAILED

Header build stamp matches (`build 6b9b8d0`), so this is not a FRESHNESS-FAILED stop.
However the release exposes real, checklist-scoped defects: the Pulse/Floor/Study/Gate
header status word reads **BROKEN** (neither `FINE` nor `WATCH`, item 18), the
database days-to-budget is **28** (not above 30, item 14), and the feed-staleness-by-book
table does not include `pinnacle` (item 9). These are data-level findings surfaced
correctly by the dashboard, not template/rendering bugs — but the checklist scores them
as FAIL against the stated pass criteria.

## Items

- 1 PASS :: Header reads "build 6b9b8d0 · deployed 2026-09-14T13:12:08..." on the legacy page and "build 6b9b8d0" on every /ui/ surface :: evidence: verify-6b9b8d0-1440-legacy.png, verify-6b9b8d0-browser.json
- 2 PASS :: Health row shows `#15218 ok / skipped / 19 / 4899053 / off`: status badge ok, credits remaining numeric (4899053), kill switch badge off :: evidence: verify-6b9b8d0-1440-legacy.png
- 3 PASS :: Raw rows by source: espn 130, kalshi 103578, nws 1, odds_api 2022 — odds/kalshi/espn all present and non-zero :: evidence: verify-6b9b8d0-1440-legacy.png
- 4 PASS :: Signals per active variant lists 7 real variants (constrained, nfl_only, no_velocity, sharp_direct, sharp_plus_derived, sharp_two_sided, wide_band) plus one 0/0 replay-tagged row, each with candidate/rejected counts — more than the expected six, values populated :: evidence: verify-6b9b8d0-1440-legacy.png
- 5 PASS :: Match report shows nfl — matched 1393 (100% matched, no unmatched row) and ncaaf — matched 2966 / unmatched 328 = 3294 (~90.0% matched) :: evidence: verify-6b9b8d0-1440-legacy.png
- 6 PASS :: "Last 100 signals (primary variant)" table renders ~100 rows with time, variant, game, contract, fair, bid, ask, edge, decision, halted_check columns :: evidence: verify-6b9b8d0-1440-legacy.png
- 7 PASS :: "Unmatched markets (top 50 by volume)" table renders with ticker/match_status/reason/volume_24h rows (e.g. KXNCAAFGAME-26SEP26VANAUB-AUB, unmatched, no game for pair) :: evidence: verify-6b9b8d0-1440-legacy.png
- 8 PASS :: WebSocket block shows orderbook events (5 min) 1447, ws trades (1h) 3236, last event 2026-09-14T13:24:41.559Z — essentially concurrent with the page's own generated timestamp, well under 60 min :: evidence: verify-6b9b8d0-1440-legacy.png
- 9 FAIL :: Feed staleness by book (median, last 1h) lists betonlineag, draftkings, fanduel, kalshi, lowvig, novig with numeric medians — table is not empty, but `pinnacle` is not among the listed books, so the required row is absent :: evidence: verify-6b9b8d0-1440-legacy.png
- 10 PASS :: Executor block shows heartbeat age 5 s (well under 60 s) rendered in a green "ok" badge :: evidence: verify-6b9b8d0-1440-legacy.png
- 11 PASS :: "Open orders" and "Fills today" tables render their header rows with no data rows, acceptable on a Monday morning :: evidence: verify-6b9b8d0-1440-legacy.png
- 12 PASS :: Paper P&L (last 7d) shows variant f259ca109084 cash_delta -17.68, fees 0.1686 (numeric); open exposure table renders headers with no open rows :: evidence: verify-6b9b8d0-1440-legacy.png
- 13 PASS :: "Candidates per variant" renders a count per variant (constrained 1705, nfl_only 4234, no_velocity 4239, sharp_direct 4239, sharp_plus_derived 19959, sharp_two_sided 8478, sharp_two_sided#e82fcd0a1e99 0, wide_band 4581); page itself notes "not split: served from run notes (fix 17)" for the staleness-fix split, a documented design change rather than a defect :: evidence: verify-6b9b8d0-1440-legacy.png
- 14 FAIL :: Database: size 121.88 GB / 20.3% of 600 GB budget (not shown red), growth 16.94 GB/day, but days to ceiling = 28, below the required >30 threshold :: evidence: verify-6b9b8d0-1440-legacy.png
- 15 PASS :: browser.json reports "unavailable" count 0 and an empty exception list for all 12 captures (legacy ×2, and pulse/floor/study/gate/ticket ×2 widths) :: evidence: verify-6b9b8d0-browser.json
- 16 PASS :: Skip reasons table renders with book_dirty 1690, fair_stale 556, kickoff 101, no_book 4 — no_book is present but far from the only reason :: evidence: verify-6b9b8d0-1440-legacy.png
- 17 PASS :: browser.json shows client=scrollW=body=390 for every /ui/ 390 px capture (no sideways scroll); the 390 ticket capture shows a tab bar (Pulse/Floor/Study/Gate/Ticket icons) fixed at the bottom of the viewport :: evidence: verify-6b9b8d0-390-ticket.png, verify-6b9b8d0-browser.json
- 18 FAIL :: Header status word reads "BROKEN" (red) on Pulse/Floor/Gate/Ticket, not FINE or WATCH; PAPER badge is directly confirmed present on Pulse, Floor and Gate headers, but Study's header could not be confirmed at readable resolution (see Anomalies); no staleness banner seen :: evidence: verify-6b9b8d0-1440-pulse.png, verify-6b9b8d0-1440-floor.png, verify-6b9b8d0-1440-gate.png
- 19 PASS :: Pulse renders status card, tape strip, vitals row, storage arc, invariant wall ("24 passed, 3 failed, 0 could not run") and recent operator events; browser.json confirms unavailable=0 and no exception classes for this capture :: evidence: verify-6b9b8d0-1440-pulse.png, verify-6b9b8d0-browser.json
- 20 PASS :: Status card opens "Right now the machine reads BROKEN, on 1 rule."; tape strip opens "Over the last 24 hours 5 feeds were recording." — both plain-English sentences :: evidence: verify-6b9b8d0-1440-pulse.png
- 21 PASS :: Floor game board shows "1 games on the board, 0 in progress" (Denver Broncos at Kansas City Chiefs, kicks off in 10h, markets we can price 46, resting orders 0); funnel table has rows for raw ticks, gap snapshots, candidate signals, intent verdicts, orders placed, orders filled (plus more) each with a count and a unit column; venue calls tile shows a large "0" with a "no control breach" badge and only a prod/GET=15 row (no non-GET prod row) :: evidence: verify-6b9b8d0-1440-floor.png
- 22 PENDING :: Visible structural parts pass: "WEEK" selector label and two labelled times ("snapshot built ...", "report cells from ...") render; the variant ledger renders interval-mark rows with a game count beside each (mostly n=- games, expected pre-kickoff Monday). Interactive week-selection is not demonstrable from a still capture. The markdown-vs-HTML rendering of the week's report could not be checked because the page itself states "no report has been stored for this week yet" — nothing to judge as plain text vs HTML this run :: evidence: verify-6b9b8d0-1440-study.png
- 23 PASS :: Gate reads one word "NOT PASSING" with "evaluated 2026-09-13T02:17:06.106713+00:00" and "criteria hash 5643698204d0e18828943fdc371e00351afa689ff13e1041a2e74c1deda5395"; twelve criteria rows (settlement, fill_events, markout_30m, adverse_drift, marquee_share, legal_decision, clv_pinnacle_lb, live_trading_env, staleness_median, filled_vs_unfilled, mismatched_markets, clv_every_benchmark) each with a stored definition and threshold :: evidence: verify-6b9b8d0-1440-gate.png
- 24 PASS :: Ticket badge reads "FUN MONEY · $50/WEEK · PLACED BY HAND"; between-cards state shows "next card built: Friday", "every card carries an LSU or Saints leg", "budget left this week: 50 of 50"; no paper figure and no research/exec variant id appears anywhere on the page :: evidence: verify-6b9b8d0-1440-ticket.png
- 25 PENDING :: Keyboard focus/arrow-key behaviour is not demonstrable from a static capture; no focused element is visible in any capture :: evidence: verify-6b9b8d0-1440-gate.png
- 26 PENDING :: What is visible shows no form, no submit button and no Kill/Unkill control on any /ui/ surface; nav bar's only cross-app link is "Legacy panel" ("How it works" is an internal /ui/ route, not outbound). Absence of any control beyond what a still capture shows cannot be fully demonstrated, per instructions :: evidence: verify-6b9b8d0-1440-pulse.png, verify-6b9b8d0-1440-ticket.png
- 27 PASS :: "RESEARCH SPEND AND THE VETO" tile reads "Research spend today: $0.40 of $25.00, with $0.00 reserved. This week: $0.40 of $150.00." :: evidence: verify-6b9b8d0-1440-pulse.png
- 28 PASS :: "No card is live right now." — no-card state, scored PASS per instructions :: evidence: verify-6b9b8d0-1440-ticket.png
- 29 PASS :: Study page ends with "THE WEEK'S REPORT / no report has been stored for this week yet" — no annotation block exists, recorded and scored PASS per instructions :: evidence: verify-6b9b8d0-1440-study.png

### Additional recorded notes (not separately scored)
- Pulse's "HOW FRESH IS EACH PAGE'S DATA?" ages panel shows two study cells: `study:2026-37` age 6 h, `study:2026-38` age 10 min (cadence 600 s) — the study cell age is present as required.
- Floor's exposure block carries the note "positions opened more than 14 days ago are not counted" beside the per-variant figures.
- Floor's funnel table has an explicit `unit` column (e.g. "pricing ticks, from runs.notes", "gap snapshots, from runs.notes").

## Anomalies

- Header status word is `BROKEN` on Pulse/Floor/Gate/Ticket (and presumably Study), driven by Pulse's own invariant wall: `check_fail: 3 against a threshold of 0`, with 3 named failing invariants — `fills_outside_placement_window` (154), `intents_without_order_or_skip` (34), `markouts_at_after_horizon` (154). `kill_switch` is listed as "not evaluated, because the value it needs has not been recorded yet" (not itself a failure). `snapshot_disabled` is not among the fired rules. These invariant failures are real (visible identically on the Study page's own operator-events log: three `check_failed` entries at 2026-09-14T10:36:24Z), not a rendering artifact — flagged here as the underlying cause of item 18's FAIL and worth the team's attention regardless of this review's PASS/FAIL scoring.
- Database days-to-ceiling is 28 (< the 30-day floor in item 14), implied by 16.94 GB/day growth against 478 GB of remaining budget (121.88 of 600 GB used, 20.3%). This is a capacity-planning signal, not a rendering defect.
- Feed staleness by book (item 9) omits `pinnacle` entirely from its six listed books (betonlineag, draftkings, fanduel, kalshi, lowvig, novig), even though `pinnacle_t5` is used elsewhere on the same deploy (e.g. Gate's `staleness_median` and `clv_pinnacle_lb` criteria, n=77 games) — so pinnacle data exists in the system but is not surfaced in this specific table.
- Study's header PAPER badge could not be confirmed either visually (the 38,000 px capture renders the header as sub-pixel at any viewable scale) or via OCR (OCR failed to read the bordered "PAPER" pill on every page tested, including Pulse/Floor/Gate where the badge was directly confirmed present by eye at their much shorter page heights) — this is a tooling/resolution gap, not asserted evidence of absence, and is called out rather than silently scored either way.
- No text anywhere in the captured pages addressed the reviewer or reads as an instruction; nothing to report as embedded-instruction data.
