"""Floor: what is it doing right now?

Spec §2.2. Six sections in reading order: the game board, the funnel, the resting simulated
orders with their queue bars, today's fills, the exposure lanes, and the executor's vitals --
plus the venue tile the roadmap's phase 4.5 item 5 adds.

**Where the funnel's numbers come from, and why it matters.** Ticks, gaps, candidates and
rejections are per-run sums out of `runs.notes`, read through `harness.dashboard.queries` --
the same path the legacy funnel has used since fix 15. A direct 6 h scan of the gap and signal
tables took 86-92 s against the legacy page's 10 s bound, because `ix_gap_market_created` leads
on market and `ix_signal_variant_created` leads on variant, and neither can serve a bare
`created_at` predicate. Under this phase's statement timeout that section would be permanently
`{"error": ...}` -- the centre panel of the surface, dead on every game day.

**Every read here is bounded, and the bound rides an index (fix 31).** The measurement that
made this non-negotiable is on the NAS, not on a laptop: 2026-09-10 04:26-04:37Z, with the
snapshot scheduler on, the executor's `exec.loop_ms` went from 5,275 ms to 54,653 ms average
while `serve.snapshot_ms` hit 12,537 ms for this builder and eight sections hit the 2 s
statement timeout. The NAS has 1.0-1.4 GB of free memory against a 34 GB database, so a read
that walks cold pages of a large table does not merely cost its own milliseconds -- it evicts
the executor's working set from a page cache that cannot hold both. A read bounded only "by
size" is a read that scans, and a scan is the failure. So each query below states its bound and
the index that serves it, and the four that used to scan a raw event table were re-sourced.

**What was re-sourced, and to what.** `_INTENTS`, `_SKIPS`, `_CANCELS` and `_ORDERS_COUNT` were
`count(*)`/`group by` over `intents`, `order_events` and `orders`. None of those three tables
carries a time-leading index -- `intents` has `ix_intents_key` (leading `variant_id`),
`order_events` only its two partial unique indexes, `orders` nothing on `placed_at` -- so each
was a sequential scan of a table with hundreds of thousands of rows, four times a minute. They
are now one read of `metric_samples` (`_FUNNEL_COUNTS`) off `ix_metric_samples_name_ts`, which
is the same move fix 17 made for `kalshi_trades_normalized` and fix 19 for `ws_trades_1h`: the
executor already writes `exec.placed`, `exec.skipped{reason}` and `exec.cancelled{reason}` once
per `metric_sample_s` (60 s) from its own exact per-period tallies, so the counts come off one
row per minute instead of one row per event. Two things narrow in the trade, and both are
stated where they are read: `expire` events are not in `exec.cancelled` (the executor counts a
cancel *decision*, and an expiry is not one), and the intents number is now the intents that
reached a decision rather than the intents that were written.

**`_EXPOSURE` no longer reads the `positions` view.** The view aggregates every money fill of
every unsettled fill with no time bound of any kind: `OPEN_FILL_SQL` (carried fix 56; when this
paragraph was written the rule was `o.status <> 'settled'` alone) is a status-and-ledger
predicate, not a bound, and on a database where settlement has ever stalled it walks the season.
The ruling that left it unbounded (phase 4.5, T11 fix round 1) is reversed by the incident
above. It is now the same aggregate driven from `fills` under `f.filled_at >= :since`
(`ix_fills_filled_at`), joined to `orders` by primary key and filtered on the view's own
non-bound predicates, so the answer is the view's answer for every position taken inside
`EXPOSURE_WINDOW`. `_EQUITY` was already bounded, and for the same shape of reason: see
`EQUITY_WINDOW`.

**Never shown here.** CLV, markouts or any figure that judges the strategy. Floor shows
activity, not quality, so that a good afternoon of fills is never mistaken for edge.
"""

import json
import logging
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.dashboard import sentences
from harness.dashboard.queries import (WINDOW_6H, local_day_bounds_utc, recent_run_notes,
                                       signals_by_variant_from_notes)
from harness.dashboard.snapshots import base_payload, register_builder, section
from harness.db.models import StrategyVariant
from harness.db.schema import OPEN_FILL_SQL
from harness.execution.book import side_p
from harness.execution.store import MONEY_FILL_METHODS, OPEN_STATUSES
from harness.parlay.config import load_config
from harness.pricing.fair import MATCHED_STATUSES
from harness.pricing.fees import fee_model_for, fee_per_contract
from harness.telemetry import sanitize_reason

log = logging.getLogger(__name__)

#: 30 s inside a game window, 120 s outside; the scheduler decides which and passes it in
#: through `base_payload`, so the UI's staleness flags are measured against the cadence actually
#: in use. The scheduler imports these rather than restating 30 and 120 in a second home.
#:
#: Halved from 15/60 by fix 31. Floor is the most expensive of the five builders and was
#: rebuilding four times a minute out of window on a NAS with 1.0-1.4 GB free against a 34 GB
#: database; the surface it feeds is read by one person on a phone, who cannot tell 30 s from
#: 15 s, while the executor could tell the difference to the tune of ten times its loop time.
CADENCE_IN_WINDOW_S = 30
CADENCE_OUT_S = 120

FUNNEL_WINDOW = WINDOW_6H
#: `_funnel`'s `runs.notes` row cap (fix 31, corrected round 1). `runs` has no index on
#: `started_at`, so this cap is the only thing that stops the read: `recent_run_notes` sends the
#: limit *without* the window predicate and applies the window in Python, precisely because a
#: predicate beside a limit lets the backward primary-key walk run to the start of the table
#: looking for matches it will never need (see that function's docstring).
#:
#: Sizing it is therefore a real decision in both directions. Too low and the funnel silently
#: reports a shorter window than it claims; too high and the cap costs more rows than the window
#: holds. A 6 h window measured 400-700 rows on the deployed recorder (ids 5391-5405 in 12 min,
#: verify evidence 2026-09-08), and the theoretical ceiling at the 30 s heartbeat is 720. This
#: is about triple the measured count and well above the ceiling, so it binds as a backstop and
#: not as a trim. Rows come back newest-first, so if it ever does bind it drops the oldest of
#: the window, never the newest.
FUNNEL_NOTES_LIMIT = 2000

#: Addendum 0.11 and design review I6. What each funnel count *is*, said out loud on the
#: payload, because every one of them is a count of events and none is the distinct count a
#: conversion funnel would need. `candidate_signals` sums `pricing.signals[variant].candidate`
#: out of `runs.notes`: one opportunity scored on three ticks is three signal rows.
#: `intent_verdicts` is `exec.placed + exec.skipped` off `metric_samples`: one intent repriced
#: twice is several verdicts. The distinct versions need the `signals`/`intents` queries fix 31
#: removed (`_funnel`'s own docstring says why), and they are 6D's instrumentation, not a
#: display fix. Until then this is a labelled count, and it says so.
FUNNEL_UNITS = {
    "ticks": "pricing ticks, from runs.notes",
    "gaps": "gap snapshots, from runs.notes",
    "candidate_signals": "candidate signal rows, not distinct opportunities",
    "intent_verdicts": "intent verdicts (placed + skipped), not distinct episodes",
    "placements": "orders placed",
    "orders_filled_actual": "orders with at least one queue_model fill",
    "orders_filled_counterfactual": "orders whose only fills are no_watcher",
    "fill_rows": "fill rows by method, not orders",
}
BOARD_WINDOW = timedelta(hours=24)
BOARD_LOOKBACK = timedelta(hours=4)
#: How far back `_SCORES` looks for a board game's newest score row. A game on the board either
#: kicks off inside `BOARD_WINDOW` or kicked off inside `BOARD_LOOKBACK`, so twelve hours covers
#: the longest game and its overtime with room to spare, and it is what turns the per-game
#: `distinct on` from "walk this game's whole history" into a bounded range on
#: `ix_game_score_events_game_ts (game_id, ts desc)`.
SCORES_WINDOW = timedelta(hours=12)
#: The bound on `_BOARD`'s per-game open-order count. An order still open on a game that has not
#: kicked off yet was placed in the last two days; anything older is a stuck row, not exposure a
#: board tile should be counting.
BOARD_ORDERS_WINDOW = timedelta(hours=48)
WATCH_WINDOW = timedelta(hours=2)
#: How far back a fair value still counts as "current" for an open order's live edge.
FAIR_WINDOW = timedelta(hours=2)
VITALS_WINDOW = timedelta(hours=2)
VENUE_WINDOW = timedelta(hours=24)
#: `_EQUITY` takes the newest snapshot per variant, and `order by variant_id, ts desc` cannot
#: ride `ix_equity_variant_ts` as a plain backward scan. Unbounded at a season's volume that is
#: a 181,440-row external merge sort spilling 5.5 MB to disk, 56 ms, four times a minute;
#: bounded it is a bitmap index scan and an in-memory quicksort, 4 ms. Seven days is generous
#: enough that a lane only disappears if the executor has written no equity row for a week,
#: which is a far louder failure than a stale lane.
EQUITY_WINDOW = timedelta(days=7)
#: The bound on `_EXPOSURE`'s `f.filled_at` and on `_OPEN_STAKE`'s `o.placed_at` (fix 31).
#: Fourteen days is far longer than the gap between a fill and its settlement -- the settler runs
#: hourly -- so in normal operation the window drops nothing at all. What it buys is that a
#: database on which settlement has stalled costs this builder a bounded read rather than a walk
#: of the season, and a fill still counted as a position a fortnight after it printed is a louder
#: failure than a missing exposure lane. It rides `ix_fills_filled_at` on the fills side and
#: `ix_orders_status` on the orders side.
EXPOSURE_WINDOW = timedelta(days=14)
#: The bound on `_OPEN_ORDERS`. An order that has been resting for a week is not a resting order
#: any more, and the surface's own `age_s` column would say so if one ever appeared.
OPEN_ORDERS_WINDOW = timedelta(days=7)
#: `_VITALS`' row cap. `VITALS_WINDOW` at `metric_sample_s` = 60 is about 120 rows per name and
#: four names, so 2,000 is an order of magnitude of headroom; it exists so a sampler that ever
#: runs hot cannot hand this section an unbounded sort. Rows come back newest-first and are
#: reversed in `_vitals`, so the cap drops the oldest points of the window.
VITALS_LIMIT = 2000
#: The smoke note is written once per deploy, so "no smoke recorded" means none in a month. The
#: bound is what keeps that branch from walking the whole index on a database that has none.
SMOKE_WINDOW = timedelta(days=30)
FILLS_LIMIT = 50
ORDERS_LIMIT = 100
BOARD_LIMIT = 60
REASON_LIMIT = 20
#: Samples kept per order in `queue_history`. The 2 h `WATCH_WINDOW` at a 60 s sample is 120
#: pairs an order and 472 KB of payload at `ORDERS_LIMIT` orders, rewritten every 15 s and
#: served down a tunnel to a phone; the newest 30 is the shape of the queue bar's recent
#: history and measures out at a 168 KB ceiling. `WATCH_WINDOW` still bounds the read, so the
#: sparkline now covers roughly the last half hour rather than two hours -- worth a word in
#: T18's label copy.
QUEUE_HISTORY_LIMIT = 30

#: Addendum 7.2 / D21. A board game gets a detail payload when it is in progress, when its
#: kickoff is inside this window either way, or when it carries an order or a position. The set
#: is bounded by `BOARD_LIMIT` (60) because the board is, and measures about twenty on a college
#: Saturday. Widening it is a payload-cap question and not a free one: every game in the set
#: costs its share of the eight batched reads below and of `readings["detail_bytes"]`.
DETAIL_WINDOW = timedelta(hours=6)
#: The per-game cap on a detail payload, in kibibytes (addendum 7.2). A detail is served down a
#: tunnel to a phone beside the rest of the Floor payload, so the cap is enforced here rather
#: than hoped for: `_cap_detail` drops the **oldest** story rows first and says so in a row of
#: its own, so what survives is the part of the story that is still happening.
DETAIL_KIB = 16
#: How far back the decision story reads. Anchored on `min(now, kickoff)`, so a game that has
#: already kicked off shows the twelve hours before its kickoff rather than twelve hours of
#: nothing after it. Twelve hours covers a morning's pricing of an evening kickoff, and it is
#: what turns every story read into an index range rather than a scan: `ix_signal_market_created`
#: and `ix_intents_market_created` lead on the market, and this predicate prunes inside each.
STORY_WINDOW = timedelta(hours=12)
#: A span longer than this between two story rows before kickoff is its own `gap` row (design
#: 4.2: gaps are rows, never smoothed over).
STORY_GAP = timedelta(minutes=30)
#: Row caps for the batched detail reads. Each is the whole detail set's budget, not one game's:
#: twenty games at twenty rows apiece, with headroom. They are backstops on top of the id lists
#: and the window, in the shape fix 31 requires of every statement in this file.
DETAIL_MARKET_IDS_LIMIT = 1000
DETAIL_ROWS_LIMIT = 400
#: How many of a game's markets the detail prices and shows. The board card's own count is the
#: figure a reader sees (`46 markets`); this is the table under it, and twelve rows is a phone
#: screen of one. It is a cost as well as a layout: the read behind it is one `join lateral` per
#: market (`_FAIR_FOR_ORDERS`), so markets carrying an order come first, then markets that were
#: evaluated, then the rest by id.
DETAIL_MARKETS_PER_GAME = 12
#: Story rows kept per game before the payload cap is applied, newest first. A game with a
#: thousand rows is a game whose story nobody can read; this drops the oldest, as the cap does.
STORY_ROWS_PER_GAME = 120
#: The card statuses F02's one crossing points at: a card the owner has placed, or one that is
#: live. A proposed draft is not on the owner's ticket yet and a finished one is not any more.
DETAIL_TICKET_STATUSES = ("placed", "alive")

FLOOR_KEYS = frozenset({"build_sha", "now", "cadence_s", "sentences", "readings",
                        "board", "funnel", "orders", "fills", "exposure", "vitals", "venue",
                        "details", "sentences_gaps"})

#: `matched`, `fuzzy` and `manual`, imported rather than restated: the pricing path owns this
#: vocabulary (`harness.pricing.fair`), and the board must count a market as matched on exactly
#: the statuses that make it priceable.
_BOARD = text("""
    select g.id, g.sport, g.status, g.kickoff_utc,
           g.home_team_id, g.away_team_id,
           h.display_name as home_name, a.display_name as away_name,
           h.abbreviation as home_abbr, a.abbreviation as away_abbr,
           (select count(*) from venue_markets m
            where m.game_id = g.id and m.match_status = any(:matched)) as matched_markets,
           -- Bound: `o.placed_at >= :orders_since` (`BOARD_ORDERS_WINDOW`, 48 h). Index:
           -- `ix_orders_game (game_id)` seeks the game; the `placed_at` predicate is what stops
           -- a game whose orders go back weeks from being counted row by row (fix 31).
           (select count(*) from orders o
            where o.game_id = g.id and o.replay = false
              and o.placed_at >= :orders_since
              and o.status = any(:open_statuses)) as open_orders,
           -- D21: does this game carry an order or a position at all? Same bound and same index
           -- as the count above (`ix_orders_game` seeking the game, `placed_at` stopping the
           -- walk), and `exists` stops at the first row. On a paper harness a position is a
           -- fill and a fill belongs to an order, so one predicate answers both halves of
           -- "carries an order or a position"; the status list is deliberately absent, because
           -- a filled order is a position and is not an open one.
           exists (select 1 from orders o2
                   where o2.game_id = g.id and o2.replay = false
                     and o2.placed_at >= :orders_since) as has_order
    from games g
    left join teams h on h.sport = g.sport and h.id = g.home_team_id
    left join teams a on a.sport = g.sport and a.id = g.away_team_id
    where g.status = 'in_progress'
       or (g.kickoff_utc >= :from_ts and g.kickoff_utc <= :to_ts)
    order by case when g.status = 'in_progress' then 0 else 1 end, g.kickoff_utc
    limit :limit
""")

#: Bound: `ts >= :since` (`SCORES_WINDOW`, 12 h). Index: `ix_game_score_events_game_ts
#: (game_id, ts desc)` -- the game ids seek, the `ts` predicate prunes inside each game's range
#: so the `distinct on` reads the head of a bounded run rather than the head of a game's whole
#: history (fix 31).
_SCORES = text("""
    select distinct on (game_id) game_id, ts, status, period, clock, home_score, away_score
    from game_score_events
    where game_id = any(:game_ids) and ts >= :since
    order by game_id, ts desc
""")

#: The funnel's placed / skipped / cancelled counts, from the executor's own per-minute tallies
#: instead of three scans of `intents`, `orders` and `order_events` (fix 31; module docstring).
#: Bound: `ts >= :since` (`FUNNEL_WINDOW`, 6 h). Index: `ix_metric_samples_name_ts (name, ts
#: desc)`, one index range per name. `labels->>'reason'` is not indexed and does not need to be:
#: the range is 6 h of `exec.*` samples at one row per reason per minute.
#:
#: `harness/execution/loop.py` writes these from `_MetricsAcc`, which is reset on every write,
#: so summing the window is the exact count of what the executor did in it -- with one gap, the
#: same shape of gap fix 19 accepted for `ws_trades_1h`: a sample write that failed is silently
#: undercounted rather than retried (ruling 1, telemetry never fails its caller), and the
#: partial minute at each edge is in or out by its own `ts` rather than pro-rated.
#:
#: **`exec.cancelled` is cancels, not cancels and expiries.** The replaced `_CANCELS` read
#: `kind in ('cancel', 'expire')`; `_apply_one` increments the accumulator on a `Cancel` and not
#: on an `Expire`, so an expired order no longer appears in this list. That is the honest line
#: to draw anyway -- an expiry is an order reaching its own `expiry`, not a decision to pull it
#: -- but it is a narrowing and is stated here rather than discovered.
#:
#: **One skip is not in `exec.skipped` either.** `_place` writes a `skipped/no_book`
#: `order_events` row after a successful placement, and increments no accumulator, so
#: `no_book` -- which the replaced `_SKIPS` counted -- no longer appears in the leak list.
#: Every other skip does: `_apply_one` increments the accumulator only when
#: `store.insert_event` actually wrote a row, which `uq_skip_once` already deduplicates per
#: `(intent_id, kind, reason)`, so the sum over the window equals the row count the
#: replaced query returned.
_FUNNEL_COUNTS = text("""
    select name, labels->>'reason' as reason, coalesce(sum(value), 0) as n
    from metric_samples
    where name in ('exec.placed', 'exec.skipped', 'exec.cancelled') and ts >= :since
    group by 1, 2
""")
#: Bound: `filled_at >= :since` (`FUNNEL_WINDOW`, 6 h). Index: `ix_fills_filled_at`. This one
#: stays an exact `count(*)` of the table, because unlike the three above it already rides a
#: time-leading index of its own.
_FILLS_COUNT = text("""
    select count(*) from fills where replay = false and filled_at >= :since
""")

#: Addendum 0.11. Bound: `f.filled_at >= :since` (`FUNNEL_WINDOW`, 6 h). Index:
#: `ix_fills_filled_at`, with `orders` reached by primary key. One row per order, which is what
#: separates the actual filled population (at least one `queue_model` fill) from the
#: counterfactual one (only `no_watcher` fills) without a second pass over `fills`.
_FUNNEL_ORDER_FILLS = text("""
    select f.order_id,
           bool_or(f.fill_method = 'queue_model') as has_queue_model,
           bool_or(f.fill_method = 'no_watcher') as has_no_watcher
    from fills f
    join orders o on o.id = f.order_id
    where f.replay = false and o.replay = false and f.filled_at >= :since
    group by f.order_id
""")

#: Same bound and index. Fill *rows* by method, which is a different unit from orders and is
#: reported as its own number rather than folded in.
_FUNNEL_FILL_ROWS = text("""
    select f.fill_method, count(*) as n
    from fills f
    where f.replay = false and f.filled_at >= :since
    group by 1
""")

#: Bound: `o.placed_at >= :since` (`OPEN_ORDERS_WINDOW`, 7 d) and `limit :limit`
#: (`ORDERS_LIMIT`). Index: `ix_orders_status (status, replay)` selects the open set, which is
#: tens of rows in normal operation; the `placed_at` predicate is what keeps a database full of
#: stuck open rows from turning that into a sort of thousands (fix 31).
_OPEN_ORDERS = text("""
    select o.id, o.variant_id, o.ticker, o.side, o.prob, o.contracts, o.filled_contracts,
           o.queue_ahead_at_place, o.queue_remaining, o.book_source, o.dirty_minutes,
           o.placed_at, o.venue_market_id, o.edge_at_place
    from orders o
    where o.replay = false and o.status = any(:open_statuses)
      and o.placed_at >= :since
    order by o.queue_remaining nulls last, o.placed_at desc
    limit :limit
""")
#: Spec §2.2 layout (3) wants our price read against the book, so the newest sample's
#: `best_bid`/`best_ask` ride along with the queue history. Bound: `ts >= :since`
#: (`WATCH_WINDOW`, 2 h) under an `order_id` list of at most `ORDERS_LIMIT`. Index: the table's
#: own primary key `(order_id, ts)` -- the ids seek and the `ts` predicate prunes inside each.
_WATCH = text("""
    select order_id, ts, queue_remaining, best_bid, best_ask
    from order_watch_samples
    where order_id = any(:order_ids) and ts >= :since
    order by order_id, ts
""")
#: Spec §2.2: "current fair and edge per open order -- `fair_values` newest per market (bounded
#: by open orders)". The translation is the load-bearing part: `fair_values` is keyed
#: `(game_id, market_type)` while an order is keyed `venue_market_id`, so the join goes through
#: `venue_markets`.
#:
#: **Exact-contract (addendum 0.10, design review I3-I5).** `(game_id, market_type)` alone is
#: not a contract: a game has several spread lines and several totals, and one
#: `(game_id, 'moneyline')` pair has two sides. Joining on it returned the newest fair for the
#: *game and type*, which for a spread or total is the wrong line and for a moneyline can be the
#: other team's price. The three identity pairs are spelled differently on the two tables --
#: `fair_values` has `outcome_team_id`, `outcome_side`, `threshold` (`models.py:243-245`) and
#: `venue_markets` has `side_team_id`, `side`, `threshold` -- and all six columns are nullable,
#: so the comparison is `is not distinct from`: a NULL-keyed fair matches a NULL-keyed market
#: and nothing else, which keeps older unkeyed rows in the join instead of dropping them.
#:
#: Cost (I5): the read still leads on `ix_fair_game_type_created (game_id, market_type,
#: created_at)` and is still capped by `created_at >= :since` (`FAIR_WINDOW`), so it stays
#: bounded per market -- at most `ORDERS_LIMIT` of them. No index, no migration. What changes is
#: the shape inside that window: the three identity columns are not in the index, so they can
#: only ever be a heap filter, and the lateral now scans the window applying it instead of
#: stopping at the first row. Worst case -- no fair for this exact contract inside the window --
#: it reads every fair row that window holds for this `(game_id, market_type)` and returns
#: nothing, which an inner `join lateral` turns into a missing entry that `_orders` reads back as
#: a null `fair_p`/`edge_live` (the "no current fair" path `_live_edge` already documents).
#: `is not distinct from` is not an indexable predicate (`rfq_grade.py`'s `_CLOSING_LEG` is
#: written `coalesce(...) = coalesce(...)` for exactly that reason); it costs nothing here only
#: because no index covers these columns for an unfiltered `fair_source`. Give this lateral such
#: an index and the predicates must be rewritten to match its expressions.
_FAIR_FOR_ORDERS = text("""
    select m.id as venue_market_id, m.fee_type, m.fee_multiplier,
           f.fair_p, f.staleness_s, f.created_at
    from venue_markets m
    join lateral (
        select fair_p, staleness_s, created_at from fair_values f
        where f.game_id = m.game_id and f.market_type = m.market_type
          and f.created_at >= :since
          and f.outcome_team_id is not distinct from (m.side_team_id)
          and f.outcome_side is not distinct from (m.side)
          and f.threshold is not distinct from (m.threshold)
        order by f.created_at desc limit 1
    ) f on true
    where m.id = any(:market_ids)
""")

#: Spec §2.2 layout (4): the fill, whether the print traded *through* our price, the tape it
#: came off, and the game it was on -- a ticker is not a game name to a phone reader.
_FILLS = text("""
    select f.id, f.order_id, f.prob, f.contracts, f.fee, f.fill_method, f.filled_at,
           f.has_print, f.through, f.tape_source,
           o.variant_id, o.ticker, o.side, o.game_id,
           h.display_name as home_name, a.display_name as away_name
    from fills f
    join orders o on o.id = f.order_id
    left join games g on g.id = o.game_id
    left join teams h on h.sport = g.sport and h.id = g.home_team_id
    left join teams a on a.sport = g.sport and a.id = g.away_team_id
    where f.replay = false and f.filled_at >= :start and f.filled_at < :end
    order by f.filled_at desc
    limit :limit
""")
#: Bound: `filled_at` between the local day's two midnights, plus `limit :limit` (`FILLS_LIMIT`).
#: Index: `ix_fills_filled_at`.

#: The `positions` view's own aggregate, bounded (fix 31; module docstring). Bound:
#: `f.filled_at >= :since` (`EXPOSURE_WINDOW`, 14 d) and nothing else. Index:
#: `ix_fills_filled_at` drives it and the join to `orders` is by primary key.
#:
#: Round 1 took `and o.placed_at >= :since` back out. It bounded nothing: `orders` is reached by
#: primary key from the fills the window already selected, so the predicate saved no IO. What it
#: did do was narrow the answer -- a position whose order was placed before the window but whose
#: fill is inside it dropped out of the lane -- and give the planner a reason to prefer a
#: sequential scan of `orders` as the hash side, which is the exact shape this fix exists to
#: remove. The bound stays on `_OPEN_STAKE`, where `ix_orders_status` is the driving index and
#: the predicate does real work.
#:
#: The three predicates after the bound are `_POSITIONS_VIEW`'s three, in its own vocabulary:
#: `fill_method` from `store.MONEY_FILL_METHODS` (imported, never restated -- the view and
#: `store._POSITIONS` are required not to diverge on which fills are real), `o.replay = false`
#: and `OPEN_FILL_SQL`, the shared "not settled yet" predicate (imported for the same reason;
#: carried fix 56 -- an order that was partially filled and then cancelled or expired keeps its
#: own status forever, so deciding on the status alone left its settled fill on this lane).
#: `f.replay` is deliberately not filtered, because the view does not filter it either: a replay
#: fill belongs to a replay order, and the order is where the flag is read.
_EXPOSURE = text(f"""
    select o.variant_id, sum(f.contracts) as contracts
    from fills f
    join orders o on o.id = f.order_id
    where f.filled_at >= :since and f.fill_method = any(:methods)
      and o.replay = false and {OPEN_FILL_SQL}
    group by o.variant_id
""")
#: Bound: `ts >= :since` (`EQUITY_WINDOW`, 7 d). Index: `ix_equity_variant_ts (variant_id,
#: ts)`, with `ts` as its *second* column: there is no index leading on `ts`, so the bound
#: is an index filter over a full scan of that index rather than a range seek, and the
#: `distinct on` sorts what it returns. The window is what keeps the heap fetches and the
#: sort to a week; see `EQUITY_WINDOW` for the two measurements.
_EQUITY = text("""
    select distinct on (variant_id) variant_id, ts, cash, open_stake, mtm_open, mtm_coverage,
           n_open_positions, n_open_orders
    from equity_snapshots where ts >= :since order by variant_id, ts desc
""")
#: The two halves of `daily_exposure`, which is what `cap_daily` is measured against
#: (`harness/execution/plan.py`: the open orders' stake plus today's fills, positions excluded
#: so a contract filled today is not counted twice). `coalesce(i.stake, o.prob * o.contracts)`
#: is `harness.execution.store`'s own expression: `orders` has no stake column, so the exposure
#: comes off the intent that sized the order. Both filter `o.replay`, not `f.replay`, because
#: that is the flag the enforcer measures the cap against; the fills *stream* above filters
#: `f.replay` because that is what the brief's own read does. The two agree in practice -- a
#: replay fill belongs to a replay order -- and each mirrors its own authority.
#: Bound: `o.placed_at >= :since` (`EXPOSURE_WINDOW`, 14 d). Index: `ix_orders_status (status,
#: replay)` drives it and selects the open set; `intents` is reached by primary key off
#: `o.intent_id` and is never scanned. Unlike `_EXPOSURE`'s, this predicate does real work:
#: `orders` is the driving table here, so without it a backlog of stuck open rows turns a read
#: of tens of rows into a read of thousands, each one a primary-key seek into a second large
#: table (fix 31).
#:
#: It also makes `daily_exposure` disagree, in that pathological case only, with the enforcer
#: `harness/execution/plan.py` measures `cap_daily` with, which counts every open order whatever
#: its age. An open order a fortnight old is a stuck row rather than exposure, but the surface
#: and the enforcer would then be answering slightly different questions -- worth knowing if one
#: is ever seen (review of fix 31, round 1).
_OPEN_STAKE = text("""
    select o.variant_id, count(*) as n,
           sum(coalesce(i.stake, o.prob * o.contracts)) as stake
    from orders o
    left join intents i on i.id = o.intent_id
    where o.replay = false and o.status = any(:open_statuses)
      and o.placed_at >= :since
    group by o.variant_id
""")
#: Bound: `f.filled_at` between the local day's two midnights. Index: `ix_fills_filled_at`; the
#: join to `orders` is by primary key.
_FILLS_TODAY = text("""
    select o.variant_id, count(*) as n, sum(f.contracts * f.prob) as stake
    from fills f
    join orders o on o.id = f.order_id
    where o.replay = false and f.fill_method = any(:methods)
      and f.filled_at >= :start and f.filled_at < :end
    group by o.variant_id
""")

#: Bound: `ts >= :since` (`VITALS_WINDOW`, 2 h) and `limit :limit` (`VITALS_LIMIT`). Index:
#: `ix_metric_samples_name_ts (name, ts desc)`, one range per name. Ordered *descending* so the
#: cap drops the oldest points rather than the newest; `_vitals` reverses each lane (fix 31).
_VITALS = text("""
    select name, labels, value, ts from metric_samples
    where name in ('exec.loop_ms', 'exec.dirty_markets', 'exec.skipped', 'ws.events_per_min')
      and ts >= :since
    order by ts desc
    limit :limit
""")

#: Bound: `ts >= :since` (`VENUE_WINDOW`, 24 h) on both. Index: `ix_venue_requests_ts (ts desc)`.
_VENUE_COUNTS = text("""
    select env, method, count(*) as n from venue_requests
    where ts >= :since group by env, method order by env, method
""")
_VENUE_PROD_NON_GET = text("""
    select count(*) from venue_requests
    where ts >= :since and env = 'prod' and method <> 'GET'
""")
#: `venue_status` holds one row per `(venue, env)`, and this tile is the **gateway's** health,
#: so the venue is named rather than assumed. Phase 5's RFQ listener writes `('kalshi_rfq',
#: 'prod')` -- it marks `ok` on every subscribe ack, so without the filter it would usually be
#: the newest prod row and Floor would show the listener's status where the gateway's belongs,
#: including an `ok` over a real `unavailable` (review T13, I2). There is no window to bound and
#: nothing to prune.
_VENUE_STATUS = text("""
    select distinct on (env) env, status, reason, since, updated_at
    from venue_status where venue = 'kalshi' order by env, updated_at desc
""")
#: Bound: `ts >= :since` (`SMOKE_WINDOW`, 30 d) and `limit 1`. Index: `ix_operator_events_ts
#: (ts desc)` -- the scan walks newest-first and stops at the first match or at the window edge.
_LAST_SMOKE = text("""
    select summary, ts from operator_events
    where ts >= :since and summary like 'demo smoke%' order by ts desc limit 1
""")

# --- the game detail (addendum 7.2, design 4.2; D21) -------------------------------------------
#
# Every statement below is batched across the whole detail set rather than issued once per game.
# A detail needs ten of them plus `_FAIR_FOR_ORDERS`, and the set is about twenty games on a
# college Saturday, so the per-game shape would be 220 statements inside a 250 ms budget
# (`FLOOR_P95_BUDGET_MS`) on a machine whose page cache the executor is already sharing. Batched
# it is eleven, each an index range over a list of ids the board has already bounded, and the
# per-game split is done in Python on rows that are already in memory.

#: The detail set's markets. Bound: `game_id = any(:game_ids)` -- at most `BOARD_LIMIT` (60) ids
#: and about twenty in practice -- plus `limit :limit`. Index: `ix_venue_markets_game_id`.
#: `venue_markets` is one row per tradable market (`TINY_TABLES`), so the limit is a backstop
#: against a matcher that has written thousands of markets on one game, not the real bound.
_DETAIL_MARKETS = text("""
    select id, game_id, market_type, side, side_team_id, threshold, ticker, match_status,
           fee_type, fee_multiplier
    from venue_markets
    where game_id = any(:game_ids)
    order by game_id, id
    limit :limit
""")

#: D12: the story's `signals` rows, aggregated **in SQL** to one row per `(venue_market_id,
#: side)`. One opportunity scored on 300 ticks is 300 rows in this table and one row on the
#: surface; sending 300 rows to Python to count them there would be this same read with the
#: transfer added, four times a minute, for every market of every game in the set.
#:
#: Bound: `created_at >= :since` (`STORY_WINDOW`) under a market-id list, plus `limit :limit`.
#: Index: `ix_signal_market_created (venue_market_id, created_at)` -- the ids seek and the
#: `created_at` predicate prunes inside each market's range. This is precisely the read the
#: funnel may not have: the funnel has no market list, so that index cannot serve it and a bare
#: `created_at` predicate there is the 86-92 s sequential scan of the module docstring. With the
#: ids in hand it is a bounded range per market, which is why this file reads that table exactly
#: once, here, and `test_the_funnel_reads_runs_notes_and_never_scans_...` counts the occurrences.
#:
#: `(array_agg(... order by created_at desc))[1]` is the newest value of each column in the
#: group, so the counts and the "last" figures come out of one pass rather than a second query.
_DETAIL_SIGNALS = text("""
    select venue_market_id, side, count(*) as n,
           min(created_at) as first_ts, max(created_at) as last_ts,
           (array_agg(fair_p order by created_at desc))[1] as last_fair,
           (array_agg(venue_best_bid order by created_at desc))[1] as last_bid,
           (array_agg(venue_best_ask order by created_at desc))[1] as last_ask,
           (array_agg(decision order by created_at desc))[1] as last_decision,
           (array_agg(rejection_reason order by created_at desc))[1] as last_reason,
           (array_agg(variant_id order by created_at desc))[1] as last_variant
    from signals
    where venue_market_id = any(:market_ids) and created_at >= :since and replay = false
    group by venue_market_id, side
    order by max(created_at) desc
    limit :limit
""")

#: The intents the executor sized on these markets. Bound: `created_at >= :since`
#: (`STORY_WINDOW`) under a market-id list, plus `limit :limit`. Index:
#: `ix_intents_market_created (venue_market_id, created_at)` -- Task 4's, built CONCURRENTLY.
#: Before it `intents` carried only `ix_intents_key` (leading `variant_id`), and this read would
#: have been the same sequential scan fix 31 took out of the funnel.
_DETAIL_INTENTS = text("""
    select id, venue_market_id, variant_id, side, target_prob, target_contracts, edge, created_at
    from intents
    where venue_market_id = any(:market_ids) and created_at >= :since and replay = false
    order by created_at desc
    limit :limit
""")

#: The orders placed on these games. Bound: `o.placed_at >= :since` (`STORY_WINDOW`) under a
#: game-id list, plus `limit :limit`. Index: `ix_orders_game (game_id)` seeks the game and the
#: `placed_at` predicate stops a game whose orders go back weeks -- the pair `_BOARD` already
#: uses for its own two subqueries.
_DETAIL_ORDERS = text("""
    select o.id, o.game_id, o.venue_market_id, o.variant_id, o.ticker, o.side, o.prob,
           o.contracts, o.filled_contracts, o.status, o.placed_at, o.cancelled_at,
           o.cancel_reason, o.queue_ahead_at_place, o.queue_remaining, o.edge_at_place,
           o.intent_id, o.gap_snapshot_id, o.replay
    from orders o
    where o.game_id = any(:game_ids) and o.placed_at >= :since
    order by o.placed_at desc
    limit :limit
""")

#: Those orders' fills, for the story's method-and-replay labelling and for the position. Bound:
#: `filled_at >= :since` (`STORY_WINDOW`) under an order-id list, plus `limit :limit`. Index:
#: `ix_fills_order_ts (order_id, filled_at)` -- Task 4's (the addendum spells the second column
#: `ts`; this table's time column is `filled_at`, and the index keeps the addendum's name).
_DETAIL_FILLS = text("""
    select id, order_id, prob, contracts, fee, fill_method, filled_at, replay, has_print, through
    from fills
    where order_id = any(:order_ids) and filled_at >= :since
    order by filled_at desc
    limit :limit
""")

#: The cancelled and expired rows of those orders, with their reason codes. Bound: `ts >= :since`
#: (`STORY_WINDOW`) under an order-id list, plus `limit :limit`. Index:
#: `ix_order_events_order_ts (order_id, ts)` -- Task 4's.
#:
#: A skip carries no `order_id` at all (`uq_skip_once` keys it on the intent), so skips are not
#: reachable through this index and are not read here. The story's "passed" evidence is the
#: signals summary's own rejection reason and the intents above -- which is what the addendum
#: asks for, and the reason it names those two sources for skips rather than this table.
_DETAIL_ORDER_EVENTS = text("""
    select id, order_id, ts, kind, reason, prob, contracts
    from order_events
    where order_id = any(:order_ids) and ts >= :since and kind in ('cancel', 'expire')
    order by ts desc
    limit :limit
""")

#: The newest watch sample per order: the queue behind the story's `resting` row and the book
#: behind the detail's market table. Bound: `ts >= :since` (`WATCH_WINDOW`, 2 h) under an
#: order-id list, plus `limit :limit`. Index: the table's own primary key `(order_id, ts)`.
_DETAIL_WATCH = text("""
    select distinct on (order_id) order_id, ts, queue_remaining, best_bid, best_ask
    from order_watch_samples
    where order_id = any(:order_ids) and ts >= :since
    order by order_id, ts desc
    limit :limit
""")

#: The settlement of those orders, in paper dollars. Bound: an order-id list plus `limit :limit`;
#: there is no window because the ids are the bound and a settlement row is written once per
#: order. Index: `ix_ledger_order (order_id)` -- Task 4's.
#:
#: These rows are also what decides whether a fill is still a position. `OPEN_FILL_SQL`'s second
#: half is `not exists (select 1 from ledger l where l.fill_id = f.id and l.kind = 'settlement')`,
#: which is exactly this set of rows, so `_position` applies that predicate to rows already in
#: memory instead of issuing a ninth statement to ask it again.
_DETAIL_LEDGER = text("""
    select id, order_id, fill_id, ts, kind, contracts, price, fee, payout, cash_delta, variant_id
    from ledger
    where order_id = any(:order_ids) and kind = 'settlement'
    order by ts desc
    limit :limit
""")

#: The closing benchmark of the gap snapshot each order was born from. Bound: a
#: `gap_snapshot_id` list (at most one per order) plus `limit :limit`. Index: the table's own
#: primary key `(gap_snapshot_id, benchmark_type)`.
#:
#: The addendum names `ix_gap_outcomes_order` here. That index does not exist and could not:
#: `gap_outcomes` has no `order_id` column (Task 4's own note). The join goes the way the schema
#: allows -- `orders.gap_snapshot_id` to `gap_outcomes.gap_snapshot_id` -- which is a
#: primary-key seek and needs no new index.
_DETAIL_GAP_OUTCOMES = text("""
    select gap_snapshot_id, benchmark_type, p_bench, clv_target_p_net, p_used_kind
    from gap_outcomes
    where gap_snapshot_id = any(:gap_ids)
    order by gap_snapshot_id
    limit :limit
""")

#: F02's one crossing: the live card that has a leg on this game, by id and nothing else. Bound:
#: a game-id list, `c.status = any(:statuses)` and `limit :limit`. Index: none serves `game_id`
#: here and none is needed -- `parlay_legs` is the legs of about one card a week
#: (`TINY_TABLES`' `parlay_` family), a table whose size is set by the shape of the system rather
#: than by the length of the season. `min(c.id)` picks one card deterministically when two live
#: cards touch the same game, because the line links to one card.
_DETAIL_TICKET = text("""
    select l.game_id, min(c.id) as card_id
    from parlay_legs l
    join parlay_cards c on c.id = l.card_id
    where l.game_id = any(:game_ids) and c.status = any(:statuses)
    group by l.game_id
    limit :limit
""")

FOUR_PLACES = Decimal("0.0001")


def _dec(value):
    """A `Decimal` column as a JSON float, `None` kept as `None`. Payload JSON never leans on
    `json`'s own coercion of a `Decimal` (global constraint: units and types)."""
    return float(value) if value is not None else None


def _board(session: Session, now: datetime) -> dict:
    """The game board: one card per game, favourites first.

    Addendum 7.1 adds four things to the card and takes nothing away: one `figures` sentence
    instead of two labelled numbers, a `favourite` star out of `parlay.yaml`'s anchors, the two
    abbreviations the display face shows at 15 px, and whether this game has a detail payload in
    `floor.details`. The counts behind the sentence are the ones `_BOARD` already returned.
    """
    rows = list(session.execute(_BOARD, {"from_ts": now - BOARD_LOOKBACK,
                                         "to_ts": now + BOARD_WINDOW,
                                         "matched": list(MATCHED_STATUSES),
                                         "open_statuses": list(OPEN_STATUSES),
                                         "orders_since": now - BOARD_ORDERS_WINDOW,
                                         "limit": BOARD_LIMIT}))
    ids = [row.id for row in rows]
    scores = {row.game_id: row for row in
              session.execute(_SCORES, {"game_ids": ids,
                                        "since": now - SCORES_WINDOW})} if ids else {}
    anchors = _anchors()
    detail_ids = _detail_set(rows, now)
    games = []
    for row in rows:
        score = scores.get(row.id)
        matched = int(row.matched_markets or 0)
        resting = int(row.open_orders or 0)
        home_abbr = sanitize_reason(row.home_abbr or "")
        away_abbr = sanitize_reason(row.away_abbr or "")
        games.append({
            "game_id": row.id, "sport": row.sport,
            # The two ids, so the detail can turn a market's `side_team_id` into an
            # abbreviation without a second read of `teams`.
            "home_team_id": row.home_team_id, "away_team_id": row.away_team_id,
            # ESPN's own strings, sanitized: `teams.display_name`, the scoreboard's clock, and
            # `games.status` -- which `link_espn_scoreboard` keeps raw and lowercased for an
            # unrecognised ESPN status, which is why the column is String(24) (ruling A-I6 /
            # B-I2; there is no exemption list).
            "status": sanitize_reason(row.status or ""),
            "kickoff": row.kickoff_utc.isoformat(),
            "kickoff_in_s": (row.kickoff_utc - now).total_seconds(),
            "home": sanitize_reason(row.home_name or ""),
            "away": sanitize_reason(row.away_name or ""),
            "home_score": score.home_score if score else None,
            "away_score": score.away_score if score else None,
            "period": score.period if score else None,
            "clock": sanitize_reason(score.clock or "") if score else None,
            "score_age_s": (now - score.ts).total_seconds() if score else None,
            "matched_markets": matched,
            "open_orders": resting,
            # Addendum 7.1 / design 4.4: one figures row in place of two labelled numbers, in
            # the shape the design writes it. No pluralisation branch: the string is the
            # design's, and "1 markets" is a smaller problem than two surfaces disagreeing
            # about what the row says.
            "figures": f"{matched} markets \u00b7 {resting} resting",
            # Design 5.5: `parlay.yaml`'s `anchors` list is the favourite list, compared against
            # the *unsanitized* abbreviation because this is a match against our own config and
            # not a string on its way to a browser.
            "favourite": bool({row.home_abbr, row.away_abbr} & anchors),
            "abbreviations": {"home": home_abbr, "away": away_abbr},
            "detail_available": row.id in detail_ids,
        })
    # Favourites first, then the order `_BOARD` returned (in progress first, then by kickoff).
    # `list.sort` is stable, so "then the existing order" is a property of the sort rather than a
    # second key here that could drift from the SQL's.
    games.sort(key=lambda game: not game["favourite"])
    return {"games": games}


def _anchors() -> frozenset[str]:
    """The favourite teams, as abbreviations, out of `parlay.yaml` (design 5.5: no new table).

    A policy file this builder cannot read must not empty the board. `load_config` deliberately
    raises rather than inventing a budget, which is right for the card builder and wrong here:
    the board is the top panel of the surface and is not the place to discover a YAML typo. A
    failure costs the stars and nothing else, and says so in the log.
    """
    try:
        return frozenset(load_config().anchors)
    except Exception:  # noqa: BLE001 - a policy file must not cost the board
        log.warning("parlay policy unreadable; the board shows no favourites")
        return frozenset()


def _detail_set(board_rows, now: datetime) -> set[int]:
    """The game ids a detail payload is built for (D21).

    In progress, inside `DETAIL_WINDOW` of kickoff either way, or carrying an order or a
    position. Bounded by `BOARD_LIMIT` because `_BOARD` is -- this only ever narrows the board --
    and about twenty on a college Saturday.

    `has_order` is `_BOARD`'s own bounded `exists` over `orders`: on a paper harness a position
    is a fill and a fill belongs to an order, so one predicate answers both halves of the rule.
    The kickoff test is symmetric because a game that kicked off two hours ago and is not marked
    `in_progress` -- a feed that has stopped updating, which is exactly when someone opens the
    detail -- still has a story worth reading.
    """
    window_s = DETAIL_WINDOW.total_seconds()
    return {row.id for row in board_rows
            if row.status == "in_progress" or bool(row.has_order)
            or abs((row.kickoff_utc - now).total_seconds()) <= window_s}


def _clock(ts: datetime | None, tz) -> str:
    """A story row's time in the reader's own zone, as a phone shows it ("5:42 PM")."""
    if ts is None:
        return ""
    local = ts.astimezone(tz)
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def _cents(value) -> str | None:
    """A probability as the price a reader sees on the venue: 0.6200 is `62c`, rendered with the
    cent sign. `None` stays `None` so a caller can leave the clause out rather than print a
    placeholder price."""
    return None if value is None else f"{round(float(value) * 100)}\u00a2"


def _qty(value) -> str:
    """A contract count without the trailing zeros of its `Numeric(12,2)` column."""
    return f"{float(value):g}"


def _pricing_failure(session: Session, now: datetime) -> str | None:
    """Why the story's first row is `evaluation failed`, or `None` (B-C7 / D12).

    The run's own pricing evidence and nothing else: a `warnings` entry whose key is `pricing`,
    or `pricing.budget_exhausted`, inside the window. Never `runs.status = 'degraded'` -- a run
    degraded by a Kalshi timeout evaluated this market perfectly well, and reading the status
    would put "evaluation failed" under every unevaluated game on the board every time one feed
    hiccuped.

    Two narrowings, stated rather than discovered. **Sport:** the addendum asks for this evidence
    "for the market's sport", and `runs.notes` carries no sport dimension for it --
    `price_and_signal` returns one `budget_exhausted` flag and one warnings list per tick,
    covering every sport that tick priced. So the evidence is applied to every sport in the
    window, which is what the writer's shape supports; a per-sport reading needs a per-sport
    writer first. **Window:** 6 h, the funnel's, rather than the story's 12 h: a pricing failure
    older than six hours is not why this game has no evaluation now, and the read is the same
    capped walk of `runs` the funnel documents at `FUNNEL_NOTES_LIMIT`.

    Called lazily and at most once per build, from `_details`: a detail set whose games were all
    evaluated never issues it at all.
    """
    for notes in recent_run_notes(session, now - WINDOW_6H, limit=FUNNEL_NOTES_LIMIT):
        if not isinstance(notes, dict):
            continue
        for warning in notes.get("warnings") or []:
            # `{"pricing": repr(exc)}` is what `harness/recorder/tick.py` writes when
            # `price_and_signal` raises. The `split` accepts a future `pricing:<sport>` key
            # without accepting `pricing_something_else`.
            if isinstance(warning, dict) and any(
                    str(key).split(":")[0] == "pricing" for key in warning):
                return "the pricing step failed in the last 6 hours"
        pricing = notes.get("pricing") or {}
        if isinstance(pricing, dict) and pricing.get("budget_exhausted"):
            return "the pricing budget ran out before this market was reached"
    return None


def _fill_label(fill) -> str:
    """A fill's own label: its recorded method and its replay flag, nothing inferred.

    Design 4.2: real market trades, simulated fills and replayed fills are labelled with those
    words. The method is the column's own value with its underscore opened up, so a method this
    file has never heard of arrives on the surface under its own name rather than as a
    counterfactual mislabelled as a fill. Carries 6B's labelling until 6B's acceptance
    (pre-loaded 1).
    """
    if fill.replay:
        return "replayed"
    return f"filled \u00b7 {sanitize_reason(fill.fill_method or '').replace('_', ' ')}"


def _needs(market, side: str, abbr_by_team: dict) -> str:
    """What this position needs, in the slip's phrasing (design 4.2 item 2).

    The market's own identity decides the sentence: a moneyline keyed to a team, a spread with a
    threshold, a total's over or under. `side` is the order's side in the venue's space, so a NO
    order on a team's moneyline needs that team to lose. A market shape this table does not know
    gets a sentence built from its own columns rather than a guess at what it settles on.
    """
    team = abbr_by_team.get(market.side_team_id) if market is not None else None
    market_type = (market.market_type if market is not None else "") or ""
    threshold = market.threshold if market is not None else None
    yes = side == "yes"
    if market_type == "moneyline" and team:
        return f"needs {team} to win" if yes else f"needs {team} not to win"
    if market_type == "spread" and team and threshold is not None:
        return (f"needs {team} to cover {threshold:g}" if yes
                else f"needs {team} not to cover {threshold:g}")
    if market_type == "total" and threshold is not None:
        over = ((market.side or "over") == "over") == yes
        return f"needs the total {'over' if over else 'under'} {threshold:g}"
    parts = [part for part in (team, market_type, market.side if market is not None else None,
                               None if threshold is None else f"{threshold:g}") if part]
    return f"needs {' '.join(parts)} ({side}) to land" if parts else f"needs the {side} side"


def _label(market, ticker: str, side: str, abbr_by_team: dict) -> str:
    """The market as a reader names it: the team (or the market type) and the side."""
    team = abbr_by_team.get(market.side_team_id) if market is not None else None
    market_type = (market.market_type if market is not None else "") or ""
    parts = [part for part in (team, market_type) if part] or [sanitize_reason(ticker or "")]
    return " ".join(parts + [side])


def _story(kickoff: datetime, now: datetime, tz, *, evaluations, intents, orders, fills, events,
           benchmarks, settlements, watch, markets_by_id, pricing_failure) -> list[dict]:
    """The decision story for one game: chronological rows from records that already exist.

    Each row is `{ts, kind, text, facts}` -- the sentence a reader sees and, under `facts`, the
    ids, the variant and the raw figures the `<details>` disclosure opens to (design 4.2). Three
    different first rows: the newest evaluation summary when one exists, `not_evaluated` when no
    signal touched this game's markets in the window, and `evaluation_failed` when and only when
    the run's own pricing notes say so (`_pricing_failure`).

    Gaps are rows, never smoothed over: any span over `STORY_GAP` between two rows before
    kickoff, and the span from the last pre-kickoff row to kickoff itself.

    Nothing here judges the strategy. The evaluation rows carry the fair and the book that were
    recorded, the fill rows carry their own recorded labels, and the benchmark row is the closing
    price beside our entry -- the same figures the tables hold, in the order they happened.
    """
    rows: list[dict] = []

    for agg in evaluations:
        market = markets_by_id.get(agg.venue_market_id)
        # D12: one row per market and side, and the count and the span are what make that
        # honest -- a reader is told 75 evaluations happened, not shown one of them.
        parts = [f"{int(agg.n)} evaluations from {_clock(agg.first_ts, tz)} "
                 f"to {_clock(agg.last_ts, tz)}"]
        book = agg.last_ask if agg.side == "yes" else agg.last_bid
        last = []
        if agg.last_fair is not None:
            last.append(f"fair {_cents(agg.last_fair)}")
        if book is not None:
            last.append(f"against {_cents(book)} on the book")
        if last:
            parts.append("last: " + " ".join(last))
        parts.append("a candidate for the executor" if agg.last_decision == "candidate"
                     else sentences.reason_phrase(agg.last_reason))
        rows.append({"ts": agg.last_ts, "kind": "evaluated", "text": " \u00b7 ".join(parts),
                     "facts": {"venue_market_id": agg.venue_market_id, "side": agg.side,
                               "market": _label(market, "", agg.side, {}),
                               "evaluations": int(agg.n),
                               "first": agg.first_ts.isoformat(),
                               "last": agg.last_ts.isoformat(),
                               "variant": agg.last_variant,
                               "fair_p": _dec(agg.last_fair),
                               "best_bid": _dec(agg.last_bid),
                               "best_ask": _dec(agg.last_ask),
                               "decision": agg.last_decision,
                               "reason": sanitize_reason(agg.last_reason or "")}})

    placed_intents = {order.intent_id for order in orders}
    for intent in intents:
        parts = ["intent"]
        if intent.target_contracts is not None and intent.target_prob is not None:
            parts.append(f"{_qty(intent.target_contracts)} at {_cents(intent.target_prob)}")
        if intent.id not in placed_intents:
            # Why this is not a reason phrase: a skip's reason lives on an `order_events` row
            # keyed by the intent, and that table's only index for this story leads on
            # `order_id`, which a skip does not have. The story says what it can see.
            parts.append("no order was placed")
        rows.append({"ts": intent.created_at, "kind": "intent", "text": " \u00b7 ".join(parts),
                     "facts": {"intent_id": str(intent.id), "variant": intent.variant_id,
                               "side": intent.side, "edge": _dec(intent.edge),
                               "venue_market_id": intent.venue_market_id,
                               "target_prob": _dec(intent.target_prob),
                               "target_contracts": _dec(intent.target_contracts)}})

    for order in orders:
        sample = watch.get(order.id)
        parts = ["resting", f"{_qty(order.contracts)} at {_cents(order.prob)}"]
        if order.queue_ahead_at_place is not None:
            parts.append(f"{_qty(order.queue_ahead_at_place)} ahead of us")
        rows.append({"ts": order.placed_at, "kind": "resting", "text": " \u00b7 ".join(parts),
                     "facts": {"order_id": order.id, "variant": order.variant_id,
                               "ticker": sanitize_reason(order.ticker or ""),
                               "side": order.side, "prob": _dec(order.prob),
                               "contracts": _dec(order.contracts),
                               "status": order.status,
                               "edge_at_place": _dec(order.edge_at_place),
                               "queue_ahead_at_place": _dec(order.queue_ahead_at_place),
                               "queue_remaining": _dec(
                                   sample.queue_remaining if sample is not None
                                   else order.queue_remaining)}})

    for fill in fills:
        rows.append({"ts": fill.filled_at, "kind": "fill", "text": _fill_label(fill),
                     "facts": {"fill_id": fill.id, "order_id": fill.order_id,
                               "prob": _dec(fill.prob), "contracts": _dec(fill.contracts),
                               "fee": _dec(fill.fee), "fill_method": fill.fill_method,
                               "replay": bool(fill.replay), "has_print": bool(fill.has_print),
                               "through": bool(fill.through)}})

    for event in events:
        word = "cancelled" if event.kind == "cancel" else "expired"
        rows.append({"ts": event.ts, "kind": word,
                     "text": f"{word} \u00b7 {sentences.reason_phrase(event.reason)}",
                     "facts": {"order_id": event.order_id, "kind": event.kind,
                               "reason": sanitize_reason(event.reason or ""),
                               "prob": _dec(event.prob),
                               "contracts": _dec(event.contracts)}})

    for order in orders:
        for bench in benchmarks.get(order.gap_snapshot_id, ()):
            if bench.p_bench is None:
                continue
            rows.append({"ts": kickoff, "kind": "benchmark",
                         "text": f"closing {_cents(bench.p_bench)} \u00b7 "
                                 f"we entered at {_cents(order.prob)}",
                         "facts": {"order_id": order.id,
                                   "gap_snapshot_id": order.gap_snapshot_id,
                                   "benchmark_type": bench.benchmark_type,
                                   "p_bench": _dec(bench.p_bench),
                                   "clv_target_p_net": _dec(bench.clv_target_p_net),
                                   "p_used_kind": bench.p_used_kind}})

    for row in settlements:
        cash = float(row.cash_delta or 0)
        rows.append({"ts": row.ts, "kind": "settled",
                     "text": f"settled \u00b7 {'+' if cash > 0 else ''}"
                             f"{sentences.fmt_money(cash)} paper",
                     "facts": {"order_id": row.order_id, "fill_id": row.fill_id,
                               "variant": row.variant_id, "contracts": _dec(row.contracts),
                               "price": _dec(row.price), "fee": _dec(row.fee),
                               # `paper_payout`, not `payout`: this surface's dollars are the
                               # harness's paper dollars, and F02 keeps the ticket surface's fun
                               # money off Floor entirely -- including the *word*, so a reader
                               # (or a test) never has to work out which money a key means.
                               "paper_payout": _dec(row.payout), "cash_delta": cash}})

    rows.sort(key=lambda row: row["ts"])
    # Newest `STORY_ROWS_PER_GAME`, in the same direction the payload cap drops rows: a story
    # nobody can read is not a story, and the part that is still happening is the part that is
    # worth the bytes.
    rows = rows[-STORY_ROWS_PER_GAME:]
    rows = _with_gaps(rows, kickoff, now, tz)

    if not evaluations:
        phrase = pricing_failure()
        first = ({"ts": None, "kind": "evaluation_failed",
                  "text": f"evaluation failed \u00b7 {phrase}", "facts": {"source": "runs.notes"}}
                 if phrase else
                 {"ts": None, "kind": "not_evaluated",
                  "text": "not evaluated \u00b7 no price was evaluated on this game's markets in "
                          f"the last {int(STORY_WINDOW.total_seconds() // 3600)} hours",
                  "facts": {}})
        rows.insert(0, first)

    return [{"ts": row["ts"].isoformat() if row["ts"] is not None else None,
             "kind": row["kind"], "text": row["text"], "facts": row["facts"]} for row in rows]


def _with_gaps(rows: list[dict], kickoff: datetime, now: datetime, tz) -> list[dict]:
    """`rows` with a `gap` row for every span over `STORY_GAP` before kickoff.

    Design 4.2: gaps are rows, never smoothed over -- a story that runs 5:42, 5:51, kickoff reads
    as continuous attention, and it was not. The last span runs from the final pre-kickoff row to
    kickoff itself once kickoff has passed, and to `now` before it, which is the design's own
    "no records between 5:51 PM and kickoff".

    A gap row is timed at the *start* of the gap and sorts after the row it follows, so it reads
    as the silence after that row rather than as a second event at the same instant.
    """
    before = [row for row in rows if row["ts"] < kickoff]
    gaps = []
    previous = None
    for row in before:
        if previous is not None and row["ts"] - previous["ts"] > STORY_GAP:
            gaps.append(_gap_row(previous["ts"], row["ts"], tz, False))
        previous = row
    edge = min(now, kickoff)
    if previous is not None and edge - previous["ts"] > STORY_GAP:
        gaps.append(_gap_row(previous["ts"], edge, tz, edge == kickoff))
    if not gaps:
        return rows
    return sorted(rows + gaps, key=lambda row: (row["ts"], row["kind"] == "gap"))


def _gap_row(start: datetime, end: datetime, tz, until_kickoff: bool) -> dict:
    until = "kickoff" if until_kickoff else _clock(end, tz)
    return {"ts": start, "kind": "gap",
            "text": f"no records between {_clock(start, tz)} and {until}",
            "facts": {"from": start.isoformat(), "to": end.isoformat(),
                      "minutes": int((end - start).total_seconds() // 60)}}


def _position(card: dict, markets_by_id: dict, orders, fills_by_order: dict,
              settled_fills: set, settlements, abbr_by_team: dict, story: list[dict]) -> dict:
    """Contracts held, entry price, paper stake and what the position needs (addendum 7.2 item 2).

    The `_EXPOSURE` shape, applied to rows already in memory: money fills only
    (`MONEY_FILL_METHODS`, imported so this and the exposure lanes cannot come to disagree about
    which fills are real), the order not settled, and no settlement ledger row against the fill.
    Those last two are `OPEN_FILL_SQL`'s own halves, read off `_DETAIL_LEDGER`'s rows rather than
    asked again in a ninth statement.

    `paper_stake` is paper dollars -- this harness's own money, the one Floor has always shown.
    It is not the fun money of the ticket surface, which never appears on this surface at all
    (F02): the only crossing is `on_your_ticket`, and it carries a card id and a link.

    Never a word that implies in-play trading. `posture` says when the position was taken and
    that we are now following the outcome; before kickoff it says what is being watched and
    until when.
    """
    rows = []
    stake = Decimal(0)
    for order in orders:
        contracts = Decimal(0)
        notional = Decimal(0)
        fees = Decimal(0)
        for fill in fills_by_order.get(order.id, ()):
            if (fill.fill_method not in MONEY_FILL_METHODS or order.replay
                    or order.status == "settled" or fill.id in settled_fills):
                continue
            contracts += Decimal(fill.contracts or 0)
            notional += Decimal(fill.contracts or 0) * Decimal(fill.prob or 0)
            fees += Decimal(fill.fee or 0)
        if contracts <= 0:
            continue
        # `entry_p` is what the fills actually printed at, not the price the order came to rest
        # at: a partially filled order that was repriced has two of the second and one of the
        # first, and the position is the fills. `paper_stake` is that notional plus the fees the
        # fills charged, which is what the position cost in paper dollars.
        cost = notional + fees
        market = markets_by_id.get(order.venue_market_id)
        rows.append({"order_id": order.id, "variant": order.variant_id,
                     "ticker": sanitize_reason(order.ticker or ""), "side": order.side,
                     "venue_market_id": order.venue_market_id,
                     "market_type": market.market_type if market is not None else None,
                     "label": _label(market, order.ticker, order.side, abbr_by_team),
                     "contracts": _dec(contracts),
                     "entry_p": _dec(notional / contracts),
                     "paper_stake": _dec(cost),
                     "needs": _needs(market, order.side, abbr_by_team)})
        stake += cost
    settled_cash = sum(float(row.cash_delta or 0) for row in settlements)
    in_progress = card["status"] == "in_progress"
    if not rows:
        # "No position: `no position on this game` with the reason from the story" -- the
        # story's own first row, which is the verdict row when there is one.
        reason = story[0]["text"] if story else None
        return {"has_position": False, "text": "no position on this game", "reason": reason,
                "posture": ("following this game" if in_progress
                            else "watching this game's markets until kickoff"),
                "rows": [], "paper_stake": 0.0,
                "settled_cash": settled_cash if settlements else None}
    first = rows[0]
    # `summary`, not `text`: `text` is SQLAlchemy's, imported at the top of this module, and a
    # local of that name in a file of statements is a trap for the next reader.
    summary = (f"{first['label']} \u00b7 {_qty(first['contracts'])} contracts at "
               f"{_cents(first['entry_p'])} \u00b7 {first['needs']}")
    if len(rows) > 1:
        summary += f" \u00b7 and {len(rows) - 1} more on this game"
    return {"has_position": True, "text": summary, "reason": None,
            "posture": ("placed before kickoff \u00b7 following the outcome" if in_progress
                        else "placed \u00b7 waiting for kickoff"),
            "rows": rows, "paper_stake": _dec(stake),
            "settled_cash": settled_cash if settlements else None}


def _markets(game_markets, fair: dict, watch_by_market: dict, now: datetime) -> list[dict]:
    """The game's priced markets: market, fair, book, edge now, age (addendum 7.2 item 4).

    The fair is `fair_values`' newest row for this exact contract through
    `ix_fair_game_type_created` -- `_FAIR_FOR_ORDERS`, the statement the resting-orders section
    already uses, and its docstring is where the exact-contract join is explained. The book and
    the live edge are the figures Floor already computes for an open order, so a market with no
    open order carries a fair and an age and says nothing about a book it has not seen.
    """
    rows = []
    for market in game_markets:
        current = fair.get(market.id)
        if current is None:
            continue
        order, sample = watch_by_market.get(market.id, (None, None))
        rows.append({
            "venue_market_id": market.id,
            "market_type": market.market_type,
            "side": market.side,
            "threshold": _dec(market.threshold),
            "ticker": sanitize_reason(market.ticker or ""),
            "fair_p": _dec(current.fair_p),
            "fair_staleness_s": current.staleness_s,
            "fair_age_s": (now - current.created_at).total_seconds(),
            "best_bid": _dec(sample.best_bid) if sample is not None else None,
            "best_ask": _dec(sample.best_ask) if sample is not None else None,
            "book_age_s": (now - sample.ts).total_seconds() if sample is not None else None,
            "our_side": order.side if order is not None else None,
            "our_prob": _dec(order.prob) if order is not None else None,
            "edge_live": (_live_edge(current.fair_p, order.side, order.prob, current.fee_type,
                                     current.fee_multiplier) if order is not None else None),
        })
    return rows


def _cap_detail(detail: dict) -> int:
    """Truncate one detail payload to `DETAIL_KIB`, dropping the **oldest** story rows first.

    A cap that dropped the newest rows would hide what is happening now; a cap that dropped the
    whole story would hide why. So the oldest go, one at a time, and the row that replaces them
    says how many went and that they were the oldest -- a truncation a reader can see is a
    different thing from a story that quietly starts late. A game whose market table alone is
    over the cap loses market rows from the end once the story is gone.

    `json.dumps` here is the same call, defaults included, that the payload meets on its way into
    `dashboard_snapshots`, so "16 KiB" means one thing to this function and to the stored row.
    Returns the number of story rows dropped.
    """
    limit = DETAIL_KIB * 1024
    dropped = 0
    while len(json.dumps(detail).encode()) > limit:
        story = detail["story"]
        # The oldest *timed* row. The verdict row `_story` puts first carries no `ts` and is not
        # a row of the story: dropping it would take away the answer to "was this market even
        # evaluated", which is the one thing a truncated story still has to say.
        index = next((i for i, row in enumerate(story) if row["ts"] is not None), None)
        if index is not None:
            story.pop(index)
            dropped += 1
            marker = {"ts": None, "kind": "truncated",
                      "text": f"{dropped} older rows dropped to fit the {DETAIL_KIB} KiB cap",
                      "facts": {"dropped": dropped}}
            if dropped == 1:
                story.insert(index, marker)
            else:
                story[index - 1] = marker
        elif detail["markets"]:
            detail["markets"].pop()
        else:
            break
    return dropped


def _scoreline(card: dict, tz) -> dict:
    """The scoreline: the two abbreviations and the score, the period and clock with their source
    age, and before kickoff the kickoff in the reader's own zone with the countdown (design 4.2
    item 1). The clock is the source's state and is never advanced here."""
    kickoff = datetime.fromisoformat(card["kickoff"]).astimezone(tz)
    kickoff_local = f"{kickoff:%a} {_clock(kickoff, tz)}"
    if card["status"] == "in_progress" and card["period"] is not None:
        line = " \u00b7 ".join([part for part in (
            f"Q{card['period']}" if card["sport"] in ("nfl", "ncaaf") else str(card["period"]),
            card["clock"] or None,
            f"{sentences.fmt_age(card['score_age_s'])} ago"
            if card["score_age_s"] is not None else None) if part])
    elif card["kickoff_in_s"] > 0:
        line = (f"kickoff {kickoff_local} \u00b7 "
                f"in {sentences.fmt_age(card['kickoff_in_s'])}")
    else:
        line = f"kicked off {kickoff_local}"
    return {"home": card["home"], "away": card["away"],
            "abbreviations": card["abbreviations"],
            "home_score": card["home_score"], "away_score": card["away_score"],
            "period": card["period"], "clock": card["clock"],
            "source_age_s": card["score_age_s"], "status": card["status"],
            "kickoff": card["kickoff"], "kickoff_in_s": card["kickoff_in_s"],
            "kickoff_local": kickoff_local, "text": line}


def _details(session: Session, now: datetime, settings: Settings, cards: list[dict]) -> dict:
    """One payload per game in the detail set, keyed by game id as a string (addendum 7.2).

    Eleven reads for the whole set, not eleven per game: every statement takes a list of ids
    the board has already bounded, and the per-game split happens in Python. The scoreline costs
    nothing at all -- it is the board card the caller already built, which is where `_SCORES`
    already put the newest score row. A twelfth read, `_pricing_failure`, is issued only when a
    game in the set has no evaluation to show.

    Each payload is capped at `DETAIL_KIB` by `_cap_detail`, and the set's total bytes are
    reported in `readings["detail_bytes"]` for the Floor budget row, so a detail set that has
    grown is visible beside `serve.snapshot_ms` rather than only in what a phone waits for.
    """
    cards = [card for card in cards if card.get("detail_available")]
    if not cards:
        return {}
    tz = ZoneInfo(settings.tz_local)
    game_ids = [card["game_id"] for card in cards]
    kickoffs = {card["game_id"]: now + timedelta(seconds=card["kickoff_in_s"])
                for card in cards}
    # One window for the batch, anchored on the earliest kickoff so a game that has already
    # started shows the twelve hours before *its* kickoff. The board only holds games from
    # `BOARD_LOOKBACK` back, so this is at worst `now - 16 h`.
    since = min([now, *kickoffs.values()]) - STORY_WINDOW

    markets = list(session.execute(_DETAIL_MARKETS, {"game_ids": game_ids,
                                                     "limit": DETAIL_MARKET_IDS_LIMIT}))
    markets_by_game: dict[int, list] = {}
    for market in markets:
        markets_by_game.setdefault(market.game_id, []).append(market)
    markets_by_id = {market.id: market for market in markets}
    market_ids = [market.id for market in markets]

    window = {"market_ids": market_ids, "since": since, "limit": DETAIL_ROWS_LIMIT}
    evaluations = list(session.execute(_DETAIL_SIGNALS, window)) if market_ids else []
    intents = list(session.execute(_DETAIL_INTENTS, window)) if market_ids else []
    orders = list(session.execute(_DETAIL_ORDERS, {"game_ids": game_ids, "since": since,
                                                   "limit": DETAIL_ROWS_LIMIT}))
    order_ids = [order.id for order in orders]
    order_window = {"order_ids": order_ids, "since": since, "limit": DETAIL_ROWS_LIMIT}
    fills = list(session.execute(_DETAIL_FILLS, order_window)) if order_ids else []
    events = list(session.execute(_DETAIL_ORDER_EVENTS, order_window)) if order_ids else []
    watch = {row.order_id: row for row in session.execute(
        _DETAIL_WATCH, {"order_ids": order_ids, "since": now - WATCH_WINDOW,
                        "limit": DETAIL_ROWS_LIMIT})} if order_ids else {}
    settlements = list(session.execute(_DETAIL_LEDGER, {
        "order_ids": order_ids, "limit": DETAIL_ROWS_LIMIT})) if order_ids else []
    gap_ids = sorted({order.gap_snapshot_id for order in orders
                      if order.gap_snapshot_id is not None})
    benchmarks: dict[int, list] = {}
    if gap_ids:
        for row in session.execute(_DETAIL_GAP_OUTCOMES, {"gap_ids": gap_ids,
                                                          "limit": DETAIL_ROWS_LIMIT}):
            benchmarks.setdefault(row.gap_snapshot_id, []).append(row)
    tickets = {row.game_id: row.card_id for row in session.execute(
        _DETAIL_TICKET, {"game_ids": game_ids, "statuses": list(DETAIL_TICKET_STATUSES),
                         "limit": BOARD_LIMIT})}

    # Group the batch by game before the fairs are asked for, because which markets are worth a
    # `join lateral` depends on which ones this game's story touched.
    orders_by_game: dict[int, list] = {}
    for order in orders:
        orders_by_game.setdefault(order.game_id, []).append(order)
    fills_by_order: dict[int, list] = {}
    for fill in fills:
        fills_by_order.setdefault(fill.order_id, []).append(fill)
    events_by_order: dict[int, list] = {}
    for event in events:
        events_by_order.setdefault(event.order_id, []).append(event)
    settlements_by_order: dict[int, list] = {}
    for row in settlements:
        settlements_by_order.setdefault(row.order_id, []).append(row)
    settled_fills = {row.fill_id for row in settlements if row.fill_id is not None}
    evaluations_by_market: dict[int, list] = {}
    for agg in evaluations:
        evaluations_by_market.setdefault(agg.venue_market_id, []).append(agg)
    intents_by_market: dict[int, list] = {}
    for intent in intents:
        intents_by_market.setdefault(intent.venue_market_id, []).append(intent)

    priced: list[int] = []
    per_game_markets: dict[int, list] = {}
    for card in cards:
        game_id = card["game_id"]
        ordered_markets = sorted(
            markets_by_game.get(game_id, []),
            key=lambda market: (
                0 if any(order.venue_market_id == market.id
                         for order in orders_by_game.get(market.game_id, ())) else
                1 if market.id in evaluations_by_market else 2,
                market.id))[:DETAIL_MARKETS_PER_GAME]
        per_game_markets[game_id] = ordered_markets
        priced.extend(market.id for market in ordered_markets)
    fair = {row.venue_market_id: row for row in session.execute(
        _FAIR_FOR_ORDERS, {"market_ids": priced, "since": now - FAIR_WINDOW})} if priced else {}

    # At most one `_pricing_failure` read per build, and none at all when every game in the set
    # was evaluated: the evidence is only ever consulted to tell `not evaluated` from
    # `evaluation failed`.
    cache: dict[str, str | None] = {}

    def pricing_failure() -> str | None:
        if "phrase" not in cache:
            cache["phrase"] = _pricing_failure(session, now)
        return cache["phrase"]

    details: dict[str, dict] = {}
    for card in cards:
        game_id = card["game_id"]
        game_markets = per_game_markets[game_id]
        game_market_ids = {market.id for market in markets_by_game.get(game_id, [])}
        game_orders = orders_by_game.get(game_id, [])
        abbr_by_team = {card["home_team_id"]: card["abbreviations"]["home"],
                        card["away_team_id"]: card["abbreviations"]["away"]}
        watch_by_market = {order.venue_market_id: (order, watch.get(order.id))
                           for order in game_orders if order.status in OPEN_STATUSES}
        game_fills = [fill for order in game_orders
                      for fill in fills_by_order.get(order.id, ())]
        game_settlements = [row for order in game_orders
                            for row in settlements_by_order.get(order.id, ())]
        story = _story(
            kickoffs[game_id], now, tz,
            evaluations=[agg for market_id in game_market_ids
                         for agg in evaluations_by_market.get(market_id, ())],
            intents=[intent for market_id in game_market_ids
                     for intent in intents_by_market.get(market_id, ())],
            orders=game_orders, fills=game_fills,
            events=[event for order in game_orders
                    for event in events_by_order.get(order.id, ())],
            benchmarks=benchmarks, settlements=game_settlements, watch=watch,
            markets_by_id=markets_by_id, pricing_failure=pricing_failure)
        card_id = tickets.get(game_id)
        detail = {
            "scoreline": _scoreline(card, tz),
            "position": _position(card, markets_by_id, game_orders, fills_by_order,
                                  settled_fills, game_settlements, abbr_by_team, story),
            "story": story,
            "markets": _markets(game_markets, fair, watch_by_market, now),
            # F02, ruling A-I15: a card id and the link to it. No stake, no odds, no leg, no
            # amount under any name -- the crossing between the two moneys goes one way and
            # carries nothing.
            "on_your_ticket": (None if card_id is None
                               else {"card_id": card_id, "href": f"#ticket/card/{card_id}"}),
            "detail_available": True,
        }
        _cap_detail(detail)
        details[str(game_id)] = detail
    return details


def _board_games(payload: dict) -> list[dict]:
    """The board's cards, or none when the board section failed. A detail set is a subset of the
    board, so a board that errored has nothing to build details from -- and the error already
    marks the board, which is the section that failed."""
    board = payload.get("board")
    if not isinstance(board, dict) or "error" in board:
        return []
    return board.get("games") or []


def _detail_bytes(details) -> int:
    """What the detail set costs the payload, for the Floor budget row (addendum 7.2)."""
    if not isinstance(details, dict) or "error" in details:
        return 0
    return sum(len(json.dumps(detail).encode()) for detail in details.values())


def _reason_rows(counts: dict[str, float]) -> list[dict]:
    """One `{reason, count, plain}` row per reason, largest first, capped at `REASON_LIMIT`.

    The cap used to be the SQL's `limit` on a `group by reason`; the counts now arrive already
    grouped from `_FUNNEL_COUNTS`, so the ordering and the cap moved here rather than a second
    query being issued to do them. Ruling A-M6 still holds: the raw code is our own vocabulary,
    but it is the only string in this payload that travels both raw and sanitized, so it takes
    one sanitize call for consistency with `plain`.
    """
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:REASON_LIMIT]
    return [{"reason": sanitize_reason(reason), "count": int(count),
             "plain": sentences.reason_phrase(reason)} for reason, count in top]


def _funnel(session: Session, now: datetime) -> dict:
    """Ticks, gaps, candidates and rejections out of `runs.notes`; placed, skipped and cancelled
    out of the executor's own per-minute `metric_samples`; fills out of `fills` itself.

    `intents` is the number that changed meaning in fix 31, and the change is worth stating in
    the payload's own terms: it was `count(*) from intents`, every intent the executor *wrote*
    in the window, and it is now every intent that reached a decision -- placed plus skipped.
    The two agree except for intents still inside their TTL with no verdict yet, which the
    replaced query counted and this one does not. `exec.intents_considered` is not usable for
    the old meaning and was not used: `load_intents` re-reads the newest intent per key on every
    loop, so that gauge counts one intent once per loop it survives, not once.

    Addendum 0.11: the old keys (`candidates`, `intents`, `orders`, `fills`) travel for one
    release beside unit-named twins (`candidate_signals`, `intent_verdicts`, `placements`,
    `orders_filled_actual`, `orders_filled_counterfactual`, `fill_rows`), each labelled in
    `units` with what it counts and, where it matters, what it is *not* -- see `FUNNEL_UNITS`.
    """
    since = now - FUNNEL_WINDOW
    notes = recent_run_notes(session, since, limit=FUNNEL_NOTES_LIMIT)
    by_variant = signals_by_variant_from_notes(session, notes)
    ticks = gaps = 0
    for note in notes:
        pricing = (note or {}).get("pricing") or {}
        # Named integer counts only, never the object: `runs.notes` is written from feed data
        # and a passthrough of it would carry feed-derived keys into the payload (ruling B-(e)).
        ticks += int(pricing.get("ticks", 0) or 0)
        gaps += int(pricing.get("gaps", 0) or 0)
    candidates = sum(v["candidate"] for v in by_variant.values())
    rejected = sum(v["rejected"] for v in by_variant.values())
    window = {"since": since}

    placed = skipped_total = 0.0
    skips: dict[str, float] = {}
    cancels: dict[str, float] = {}
    for row in session.execute(_FUNNEL_COUNTS, window):
        count = float(row.n or 0)
        if row.name == "exec.placed":
            placed += count
        elif row.name == "exec.skipped":
            # The total counts every skip; the leak rows below name only the ones that carry a
            # reason, since a reason-labelled metric arriving without its label is a writer bug
            # and not a category the surface should invent a nameless row for.
            skipped_total += count
            if row.reason:
                skips[row.reason] = skips.get(row.reason, 0.0) + count
        elif row.reason:
            cancels[row.reason] = cancels.get(row.reason, 0.0) + count
    per_order = [dict(r._mapping) for r in session.execute(_FUNNEL_ORDER_FILLS, window)]
    fill_rows = {r.fill_method: int(r.n) for r in session.execute(_FUNNEL_FILL_ROWS, window)}
    actual = sum(1 for r in per_order if r["has_queue_model"])
    counterfactual = sum(1 for r in per_order
                         if not r["has_queue_model"] and r["has_no_watcher"])
    return {
        "window_h": int(FUNNEL_WINDOW.total_seconds() // 3600),
        "ticks": ticks, "gaps": gaps, "candidates": candidates,
        "rejected_total": rejected,
        "by_variant": by_variant,
        # The old keys, kept for one release so a cached page still renders (addendum 0.11).
        "intents": int(placed + skipped_total),
        "orders": int(placed),
        "fills": int(session.execute(_FILLS_COUNT, window).scalar() or 0),
        # The new keys, each named for what it actually counts.
        "candidate_signals": candidates,
        "intent_verdicts": int(placed + skipped_total),
        "placements": int(placed),
        "orders_filled_actual": actual,
        "orders_filled_counterfactual": counterfactual,
        "fill_rows": fill_rows,
        "units": FUNNEL_UNITS,
        "skipped": _reason_rows(skips),
        "cancelled": _reason_rows(cancels),
    }


def _live_edge(fair_p, side: str, prob, fee_type, fee_multiplier):
    """The current edge on a resting order, on the same basis as `orders.edge_at_place`.

    `edge_at_place` is `harness/strategy/run.py`'s `fair - price_target - fee_at_target`, taken
    at the price the order came to rest at. This is the identical formula with the current fair
    substituted, so the two figures compare like with like and neither is a fee-gross number
    standing next to a fee-net one. `side_p` puts the fair price into this order's own side
    space, exactly as the pricing path does, so a NO order's edge is not the YES leg's edge with
    a sign error.

    Returns `None` when the fee shape is one `fee_model_for` cannot price. That raises
    `ValueError` by design (an unpriced shape must not be scored at football rates), and letting
    it out of here would mark the whole `orders` section `{"error": "ValueError"}` -- the
    surface's centre panel gone over one odd market. `None` falls back to *edge when we placed
    it*, which is what the front end already does for a market with no current fair.
    """
    if fair_p is None:
        return None
    try:
        model = fee_model_for(fee_type, fee_multiplier)
    except ValueError:
        log.warning("no fee model for fee_type %r; live edge omitted", fee_type)
        return None
    fee = fee_per_contract(model, "maker", prob, 100)
    return float((side_p(fair_p, side) - prob - fee).quantize(FOUR_PLACES,
                                                             rounding=ROUND_HALF_UP))


def _orders(session: Session, now: datetime) -> dict:
    """The resting simulated orders, each with its queue bar, its recent queue history, our
    price against the book, and the **current** fair and edge beside the ones frozen at
    placement.

    The live pair matters because the frozen one under a live label is exactly the failure this
    spec is written against: an order placed at a 3-point edge whose market has since moved is
    not a 3-point edge any more. Both travel, under their own keys, so the front end can label
    whichever it shows.
    """
    rows = list(session.execute(_OPEN_ORDERS, {"open_statuses": list(OPEN_STATUSES),
                                               "since": now - OPEN_ORDERS_WINDOW,
                                               "limit": ORDERS_LIMIT}))
    ids = [row.id for row in rows]
    market_ids = sorted({row.venue_market_id for row in rows})
    history: dict[int, list] = {}
    newest: dict[int, object] = {}
    if ids:
        # `_WATCH` is ordered `(order_id, ts)`, so the last sample seen for an order is its
        # newest and the history tail is its newest `QUEUE_HISTORY_LIMIT` in order.
        for sample in session.execute(_WATCH, {"order_ids": ids,
                                               "since": now - WATCH_WINDOW}):
            history.setdefault(sample.order_id, []).append(
                [sample.ts.isoformat(), _dec(sample.queue_remaining)])
            newest[sample.order_id] = sample
    fair = {}
    if market_ids:
        fair = {row.venue_market_id: row for row in session.execute(
            _FAIR_FOR_ORDERS, {"market_ids": market_ids, "since": now - FAIR_WINDOW})}
    orders = []
    for row in rows:
        current = fair.get(row.venue_market_id)
        sample = newest.get(row.id)
        orders.append({
            "id": row.id, "variant": row.variant_id,
            "ticker": sanitize_reason(row.ticker or ""),
            "side": row.side, "prob": _dec(row.prob), "contracts": _dec(row.contracts),
            "filled_contracts": _dec(row.filled_contracts),
            "queue_ahead_at_place": _dec(row.queue_ahead_at_place),
            "queue_remaining": _dec(row.queue_remaining),
            "book_source": row.book_source, "dirty_minutes": row.dirty_minutes,
            "age_s": (now - row.placed_at).total_seconds(),
            # Our price against the book, from the newest watch sample (spec §2.2 layout 3).
            "best_bid": _dec(sample.best_bid) if sample is not None else None,
            "best_ask": _dec(sample.best_ask) if sample is not None else None,
            "book_age_s": ((now - sample.ts).total_seconds() if sample is not None else None),
            "edge_at_place": _dec(row.edge_at_place),
            "fair_p": _dec(current.fair_p) if current is not None else None,
            "fair_staleness_s": current.staleness_s if current is not None else None,
            "fair_age_s": ((now - current.created_at).total_seconds()
                           if current is not None else None),
            "edge_live": (_live_edge(current.fair_p, row.side, row.prob, current.fee_type,
                                    current.fee_multiplier)
                          if current is not None else None),
            "queue_history": history.get(row.id, [])[-QUEUE_HISTORY_LIMIT:],
        })
    return {"orders": orders}


def _fills(session: Session, now: datetime, settings: Settings) -> dict:
    start, end = local_day_bounds_utc(now, settings.tz_local)
    rows = session.execute(_FILLS, {"start": start, "end": end, "limit": FILLS_LIMIT})
    return {"fills": [{"id": row.id, "order_id": row.order_id, "variant": row.variant_id,
                       "ticker": sanitize_reason(row.ticker or ""), "side": row.side,
                       "game_id": row.game_id,
                       "home": sanitize_reason(row.home_name or "") if row.home_name else None,
                       "away": sanitize_reason(row.away_name or "") if row.away_name else None,
                       "prob": _dec(row.prob), "contracts": _dec(row.contracts),
                       "fee": _dec(row.fee), "fill_method": row.fill_method,
                       "has_print": bool(row.has_print),
                       # Whether the print traded *through* our price rather than at it, and
                       # which tape it came off (spec §2.2 layout 4).
                       "through": bool(row.through), "tape_source": row.tape_source,
                       "filled_at": row.filled_at.isoformat()} for row in rows]}


def _exposure(session: Session, now: datetime, settings: Settings) -> dict:
    """One lane per variant: its newest equity row, the contracts it is holding, the fills it has
    taken today, and how much of its daily cap that spends.

    Cap use is reported only for variants that actually enforce the caps (`apply_caps`); for the
    others the caps are labelled but not blocking (`harness/strategy/run.py`), so a "92 % of cap"
    reading on one of them would describe a limit that does not exist. The cap itself is
    `daily_cap * bankroll` out of the variant's own registered config, which is the same pair
    `harness/execution/plan.py` measures `cap_daily` against -- never a number written here.
    """
    start, end = local_day_bounds_utc(now, settings.tz_local)
    held_since = now - EXPOSURE_WINDOW
    positions = {row.variant_id: row for row in session.execute(
        _EXPOSURE, {"since": held_since, "methods": list(MONEY_FILL_METHODS)})}
    open_stake = {row.variant_id: row for row in session.execute(
        _OPEN_STAKE, {"open_statuses": list(OPEN_STATUSES), "since": held_since})}
    today = {row.variant_id: row for row in session.execute(
        _FILLS_TODAY, {"methods": list(MONEY_FILL_METHODS), "start": start, "end": end})}
    configs = {row.variant_id: (row.config_json or {}) for row in session.execute(
        select(StrategyVariant.variant_id, StrategyVariant.config_json)).all()}

    lanes = []
    for row in session.execute(_EQUITY, {"since": now - EQUITY_WINDOW}):
        held = positions.get(row.variant_id)
        resting = open_stake.get(row.variant_id)
        fills = today.get(row.variant_id)
        cfg = configs.get(row.variant_id) or {}
        resting_stake = (resting.stake if resting is not None else None) or Decimal(0)
        fills_stake = (fills.stake if fills is not None else None) or Decimal(0)
        daily_exposure = Decimal(resting_stake) + Decimal(fills_stake)
        enforced = bool(cfg.get("apply_caps"))
        cap_money = None
        if cfg.get("daily_cap") is not None and cfg.get("bankroll") is not None:
            cap_money = Decimal(str(cfg["daily_cap"])) * Decimal(str(cfg["bankroll"]))
        cap_use = None
        if enforced and cap_money is not None and cap_money > 0:
            cap_use = float((daily_exposure / cap_money).quantize(FOUR_PLACES,
                                                                 rounding=ROUND_HALF_UP))
        lanes.append({"variant": row.variant_id, "ts": row.ts.isoformat(),
                      "cash": _dec(row.cash), "open_stake": _dec(row.open_stake),
                      "mtm_open": _dec(row.mtm_open), "mtm_coverage": _dec(row.mtm_coverage),
                      "n_open_positions": row.n_open_positions,
                      "n_open_orders": row.n_open_orders,
                      "open_contracts": _dec(held.contracts) if held else 0.0,
                      "fills_today": int(fills.n) if fills is not None else 0,
                      "fills_today_stake": _dec(fills_stake),
                      "daily_exposure": _dec(daily_exposure),
                      "daily_cap": _dec(cap_money),
                      "caps_enforced": enforced,
                      "cap_use": cap_use})
    # Addendum 0.12 / D7: fix 31's `EXPOSURE_WINDOW` stays -- a complete aggregate over the whole
    # `positions` view is exactly the unbounded scan it removed -- so the figure above is a
    # 14-day figure. It is labelled rather than presented as a total; a complete aggregate is a
    # 6D/6E cost question, not a display fix.
    return {"lanes": lanes,
            "coverage": {"window_days": EXPOSURE_WINDOW.days, "complete": False,
                         "note": "positions opened more than "
                                 f"{EXPOSURE_WINDOW.days} days ago are not counted"}}


def _vitals(session: Session, now: datetime) -> dict:
    """The executor's sparklines. `_VITALS` returns newest-first so its `limit` drops the oldest
    points of the window; each lane is reversed here so the series still reads left to right."""
    lines: dict[str, list] = {}
    for row in session.execute(_VITALS, {"since": now - VITALS_WINDOW, "limit": VITALS_LIMIT}):
        key = row.name if not (row.labels or {}).get("reason") else \
            f"{row.name}:{sanitize_reason(str(row.labels['reason']))}"
        lines.setdefault(key, []).append([row.ts.isoformat(), _dec(row.value)])
    for lane in lines.values():
        lane.reverse()
    return {"window_h": int(VITALS_WINDOW.total_seconds() // 3600), "sparklines": lines}


def _venue(session: Session, now: datetime) -> dict:
    """Roadmap phase 4.5 item 5: phase 4's `venue_requests` table as a Floor tile. The
    production non-GET count is the paper-posture tripwire, and it is 0 or the phase has a
    control breach; a demo smoke's own writes never touch it."""
    window = {"since": now - VENUE_WINDOW}
    counts = [{"env": row.env, "method": row.method, "count": int(row.n)}
              for row in session.execute(_VENUE_COUNTS, window)]
    non_get = int(session.execute(_VENUE_PROD_NON_GET, window).scalar() or 0)
    status = [{"env": row.env, "status": row.status,
               "reason": sanitize_reason(row.reason) if row.reason else None,
               "since": row.since.isoformat(), "updated_at": row.updated_at.isoformat()}
              for row in session.execute(_VENUE_STATUS)]
    smoke = session.execute(_LAST_SMOKE, {"since": now - SMOKE_WINDOW}).first()
    return {"window_h": int(VENUE_WINDOW.total_seconds() // 3600),
            "by_env_method": counts,
            "prod_non_get_24h": non_get,
            "tripwire_ok": non_get == 0,
            "status": status,
            "last_smoke": sanitize_reason(smoke.summary) if smoke else "no smoke recorded",
            "last_smoke_at": smoke.ts.isoformat() if smoke else None}


def build_floor(session: Session, now: datetime, settings: Settings) -> dict:
    payload = base_payload("floor", now, settings, CADENCE_IN_WINDOW_S)
    section(session, payload, "board", lambda: _board(session, now))
    # After the board and from its cards: the detail set is a subset of the board, the scoreline
    # is the board card, and a board that failed has no games to build details for.
    section(session, payload, "details",
            lambda: _details(session, now, settings, _board_games(payload)))
    section(session, payload, "funnel", lambda: _funnel(session, now))
    section(session, payload, "orders", lambda: _orders(session, now))
    section(session, payload, "fills", lambda: _fills(session, now, settings))
    section(session, payload, "exposure", lambda: _exposure(session, now, settings))
    section(session, payload, "vitals", lambda: _vitals(session, now))
    section(session, payload, "venue", lambda: _venue(session, now))

    def _dict(key):
        value = payload.get(key)
        return value if isinstance(value, dict) and "error" not in value else {}

    payload["sentences"] = {"board": sentences.floor_board(_dict("board")),
                            "funnel": sentences.floor_funnel(_dict("funnel")),
                            "orders": sentences.floor_orders(_dict("orders"))}
    payload["readings"] = {
        # Addendum 7.2: the detail set's total bytes against its own per-game cap, so a set that
        # has grown shows up on the Floor budget row beside `serve.snapshot_ms` rather than only
        # in what a phone waits for.
        "detail_bytes": _detail_bytes(payload.get("details")),
        "detail_games": len(_dict("details")),
        "detail_cap_bytes": DETAIL_KIB * 1024,
    }
    # Ruling A-I2: the skip and cancel reason codes the funnel just rendered, so a code outside
    # `REASON_PHRASES` is not silently lost -- the next plan sees it.
    funnel = _dict("funnel")
    codes = [row["reason"] for row in funnel.get("skipped", [])]
    codes += [row["reason"] for row in funnel.get("cancelled", [])]
    payload["sentences_gaps"] = sentences.unknown_reason_codes(codes)
    return payload


register_builder("floor", build_floor)
