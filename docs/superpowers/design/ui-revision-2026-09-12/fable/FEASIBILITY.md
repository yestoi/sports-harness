# Feasibility check of DESIGN-SPEC.md against `main` a2a1791

Date: 2026-09-13, morning. Read-only spike run from the Mac checkout plus two live reads (ESPN's public summary endpoint, the Odds API's public market catalog) and one ssh look at the Omarchy host. Nothing was changed. Verdict first, then the evidence.

## Verdict

**The spec is implementable on the current code with no blocker.** Every piece is either an extension of something that exists or an additive table, column or index of a kind the repo has already shipped. Four findings change the plan's shape and one item could not be verified from here.

| # | Finding | Effect on the plan |
|---|---|---|
| 1 | ESPN's summary endpoint, on the host the recorder already polls, carries a full per-player box score (passing, rushing, receiving with yards, TDs, receptions, targets) plus play-by-play and scoring plays, for NFL and college alike. | The paid player-stat feed may be unnecessary. Player stats can come from one new fetch per watched game on an existing host. The $100 budget stays in reserve. Live-update behaviour and correction behaviour must still be measured on a real game window. |
| 2 | The Odds API already serves NFL and NCAAF player props for every family the spec names, through the per-event endpoint the recorder already calls for alternate lines. | Props are a markets-string change plus a normalizer change, not a new client. Credits are charged per market family per event; the allocation needs a number. |
| 3 | The Odds API's links are per outcome ("add to betslip" style) and per event. There is no multi-leg slip link in the product. | The link capability will be `selection` at best, never `full_slip`. The spec's honest labels already cover this. |
| 4 | Card building is by hand today (`harness parlay build --sport --kind`), not scheduled. | "This week's ideas", the Saturday build and "Not this one" need a scheduled builder stage. New but small; the settlement job already has a stage list to extend. |
| 5 | The Omarchy firewall state could not be read (needs sudo). | The LAN listener plan must include a firewall check on the host. |

## Evidence by area

### A. Player stats (spec §5.3)

- Recorder calls only the scoreboard today: `harness/feeds/espn.py:8` and `harness/recorder/tick.py:28` map sport to `/nfl/scoreboard` and `/college-football/scoreboard`. No box score, no player table (`grep "class Player" harness/db/models.py` is empty).
- Live read, NFL, a completed game (`/nfl/summary?event=401872656`): `boxscore.players` holds two teams, categories `passing` (labels C/ATT, YDS, AVG, TD, INT, SACKS, QBR, RTG), `rushing` (CAR, YDS, AVG, TD, LONG), `receiving` (REC, YDS, AVG, TD, LONG, TGTS), each athlete with an ESPN id. `drives.previous` held 179 plays; `scoringPlays` held 5.
- Live read, college, Louisiana Tech at LSU 2026-09-12 (`/college-football/summary?event=401867796`): the same shape, 2 passers, 10 rushers and 7 receivers for LSU, `wallclockAvailable: true`.
- Not verified: whether the box score updates during play at the poll cadence, how corrections appear, and the ESPN terms for this unofficial endpoint. The recorder already depends on the same host for scores, so the terms exposure is not new, but it is not zero.
- Player identity: Odds API prop outcomes name the player in text; ESPN names the athlete with an id. The identity map in spec §5.3 is required and name mismatches (apostrophes, suffixes) are the expected failure, surfaced as `player_unmatched`.

Verdict: **feasible with one new fetch on an existing host; provider purchase deferred until a measured gap appears.**

### B. Props from the Odds API (spec §5.1, §5.7)

- Client: `harness/feeds/odds_api.py:6-7` fixes `FEATURED_MARKETS = "h2h,spreads,totals"` and `ALTERNATE_MARKETS = "alternate_spreads,alternate_totals"`; `fetch_event_alternates` (line 54) already calls `/sports/{sport}/events/{event_id}/odds`. Props use that same endpoint with `player_*` market keys.
- Catalog (public page, read 2026-09-13): the "NFL, NCAAF, CFL Player Props" table lists `player_pass_yds`, `player_rush_yds`, `player_reception_yds`, `player_receptions`, `player_anytime_td`, `player_1st_td`, and `_alternate` variants of each; "Player props can be accessed one event at a time using the /events/{eventId}/odds endpoint."
- Normalizer: `harness/normalize/odds.py:60` reads `oc["name"]` and `oc.get("point")` only. Prop outcomes carry the player in `description`; the normalizer and `odds_snapshots` (`harness/db/models.py:114-129`: book, game_id, market_type, outcome_team_id, outcome_side, point, price) need a player column or a sibling table. Additive.
- Budget: `harness/config/settings.py:44` sets `odds_monthly_credits = 5_000_000`; Pulse showed 4,903,697 remaining this morning; the client parses `x-requests-remaining` (`parse_credit_headers`, line 17). Per-event prop calls are charged per market family, so a fixed allocation and a watched-game list are needed, as the spec says.
- Bookmakers string already includes `draftkings` (`settings.py:16`); `harness/parlay/pricing.py:18` prices only `book = 'draftkings'`.

Verdict: **small change to the client and normalizer, one additive column or table, one allocation number.**

### C. DraftKings links (spec §2.1)

- Release note (public page): `includeLinks` returns "bookmaker links to events, markets, and betslips if available"; the example shows per-outcome `addToBetslip` links and per-event links. No parameter or example builds a multi-selection slip.
- The client has no `includeLinks` / `includeSids` today (`_params`, line 47); adding two params is trivial. `odds_snapshots` has no link or sid column; the spec's `dk_link` / `dk_sid` on legs is where they land.

Verdict: **feasible; `full_slip` should be treated as unavailable in the plan and the `selection` and `event` levels verified on the phone.**

### D. Ticket surface and builder (spec §2)

- `harness/dashboard/snapshots/ticket.py:63` filters `c.status in ('placed','alive','cashed','busted','void')`, so proposed cards are excluded today. One-line change plus a section for ideas.
- The between-cards day is a calendar rule, not a job: `ticket.py:256-257`. No stage builds cards: `harness/settlement/job.py:99-107` lists `settle`, `parlay_grade`, `rfq_grade`, `benchmarks`, `order_clv`, `markouts`, `housekeeping`, `report_wtd`; `expire_cards` is called only from the CLI (`harness/cli.py:1153`). A `parlay_build` stage, or a scheduler job, is new work.
- Builder: `harness/parlay/build.py` takes legs from `signals` with a `direct` fair value; a prop leg has no signal, so the prop pool is a second source (the spec's versioned policy). Combined price is the independence product (`dk_payout_est`, line 148).
- Sharps say: `harness/recorder/tick.py:811-823` writes `parlay_leg_probs` from the sharp books' game-line consensus; props will have none unless Pinnacle prices them. Matches the spec's `no sharp read`.
- `needs()` is pure (`harness/parlay/needs.py:151`, `leg_outcome` at 159) with `tests/test_needs.py`; adding a `StatState` input is a contained change. `parlay_grade` grades through `leg_outcome`, so prop grading lands in one place.

Verdict: **feasible; the scheduled builder stage and the prop pool are the two real additions.**

### E. Writes, auth and the LAN listener (spec §3, §5.8)

- `serve` runs `uvicorn.run(app, host=host, port=port)` with `host="0.0.0.0"` inside the container (`harness/cli.py:601-607`); the compose file maps only `127.0.0.1:${SERVE_PORT:-8080}:8080` (`docker-compose.yml:106-107`), and Omarchy listens on `127.0.0.1:8180` only (ssh `ss -ltnp`). A LAN binding is a second port mapping on `192.168.12.127`.
- TLS: `uvicorn==0.52.4` (`constraints.txt`) accepts `ssl_keyfile` / `ssl_certfile`; `openssl` exists on the host; `mkcert` does not. The planner chooses between uvicorn TLS on a second serve container and a tiny reverse proxy; the repo has no proxy container today.
- Auth: no session, cookie or middleware code in `harness/dashboard/app.py`; no `itsdangerous`, `passlib` or `bcrypt` pinned. Stdlib `hashlib.scrypt` for the password hash and `hmac` for a signed cookie need no new dependency. `python-multipart==0.0.32` is pinned for forms. The kill pair reads `/run/secrets/dashboard_token` (`settings.py:41`, `dashboard_token()` at 204), the pattern for a new `/run/secrets/owner_password_hash`.
- Routes: no test freezes the route list; `tests/test_dashboard_static.py:236` requires `api.mjs` to be the only fetcher, which is where a POST helper belongs. No POST exists in `static/js` today.
- Firewall on Omarchy: not readable without sudo. Unverified.

Verdict: **feasible with no new dependency; TLS placement is a design decision; firewall to check on the host.**

### F. Floor game detail and the query budget (spec §4)

- Indexed per market: `signals` (`ix_signal_market_created`, `models.py:370`), `market_gap_snapshots` (`ix_gap_market_created`, 328), `venue_markets` (`ix_venue_markets_game_id`), `fair_values` (`ix_fair_game_type_created`), `orders` (`ix_orders_key_placed` on variant, market, side, placed_at, phase 4.5).
- Not indexed by market or order: `intents` (has `venue_market_id`), `order_events` (has `order_id`), `fills` (has `order_id`), `gap_outcomes` (has `order_id`), `ledger` (has `order_id`). A per-game story needs additive indexes on those, built `CONCURRENTLY` the way fix 45 built its index. Without them the reads fall back to time-bounded scans, which the phase 4.5 addendum permits but the 250 ms floor budget (`harness/dashboard/scheduler.py:167`, `FLOOR_P95_BUDGET_MS`) would punish.
- The forbidden five stay out: every story row comes from `signals`, `intents`, `orders`, `order_events`, `fills`, `gap_outcomes`, `ledger`. The scheduler self-guard (line 178) disables a builder that overruns, so a slow detail degrades rather than harms.
- `positions` is a view the Floor builder no longer reads (`floor.py:39`); the detail's position row should come from `orders` + `fills` + `ledger` as `_EXPOSURE` now does.

Verdict: **feasible; budget five small additive indexes and measure the per-game payload.**

### G. Front end (spec §1, §2, §6)

- Router: `routeOf` splits the hash on `/` and only Study reads the second part (`static/js/app.mjs:25-26`, 397-398). `#floor/game/<id>` and `#ticket/card/<id>` are accepted by the parser and need only a consumer.
- Static budget: 207,330 bytes used of the 307,200 byte budget (`ASSET_BUDGET_BYTES = 300 * 1024`, `test_dashboard_static.py:20`). About 97 KB of headroom for the ideas section, the sheet and the detail. No `<dialog>` in use; the native element costs nothing.
- The tab test pins five surfaces (`test_dashboard_static.py:139`), which the spec keeps. The DOM rule, no-external-URL and glossary tests are unaffected by design.

Verdict: **feasible as-is.**

### H. Migrations (spec §5)

- Head is `0007_raw_events_lookup` (`harness/db/migrate.py:35`). No prior revision adds a column to an existing table; `tests/test_alembic.py:187` asserts catalogue equality between `create_schema` and the migrated database, covering columns, types, nullability, keys and indexes (line 200). An `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` mirror in the model and the revision satisfies it; first of its kind in this repo but ordinary.

Verdict: **feasible; the plan should call out the first add-column revision explicitly.**

## What this changes in the design

Nothing in the approved screens. Two spec lines should be read with these findings:

- §5.3 "Player stats come from one provider chosen by the planner": the first candidate is ESPN's summary on the existing host, with the paid feed as the fallback if live updates or corrections prove inadequate in a measured window.
- §2.1 link labels: plan for `selection` and `event`; do not plan UI or tests around `full_slip`.

## Still to verify before the plan is final

1. ESPN summary box-score behaviour during a live game window (update cadence, corrections, missing players). Today's NFL noon CT slate is the first chance.
2. Omarchy firewall rules for a LAN port.
3. Odds API credit cost per event for the chosen prop families, to set the allocation.
4. Which of `selection` and `event` links DraftKings actually returns for props and game lines.
