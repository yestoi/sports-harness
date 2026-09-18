\set ON_ERROR_STOP on
\pset pager off
\timing on
SET default_transaction_read_only = on;
SET statement_timeout = '25s';
SET lock_timeout = '1s';
SET application_name = 'codex_fill_review';

\echo A. Connection, clock, and read-only enforcement
BEGIN READ ONLY;
SELECT current_database(), clock_timestamp(), current_setting('transaction_read_only');
COMMIT;

\echo B. Orders and cancellation, fixed placement cutoff 2026-09-18 17:37 UTC
BEGIN READ ONLY;
SELECT variant_id, count(*) orders,
 count(*) FILTER (WHERE cancel_reason='fair_stale') stale_cancelled,
 count(*) FILTER (WHERE filled_contracts>0) queue_filled_orders,
 count(*) FILTER (WHERE nw_filled_contracts>0) nw_filled_orders,
 count(*) FILTER (WHERE NOT nw_done AND status NOT IN ('open','partially_filled')) cancelled_nw_pending,
 percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM cancelled_at-placed_at)) FILTER (WHERE cancel_reason='fair_stale') median_stale_lifetime_s,
 min(stale_allowance_at_place) min_allowance, max(stale_allowance_at_place) max_allowance
FROM orders WHERE NOT replay AND placed_at>='2026-09-08 00:00+00' AND placed_at<'2026-09-18 17:37+00'
GROUP BY 1 ORDER BY 1;
COMMIT;

\echo C. Fills by method and repair boundary, boundary is fill id 1878
BEGIN READ ONLY;
SELECT f.fill_method, CASE WHEN f.id<=1878 THEN 'pre_repair' ELSE 'post_repair' END simulator,
 o.variant_id, count(*) fill_rows, count(DISTINCT o.id) orders,
 count(DISTINCT o.game_id) games, count(DISTINCT (o.venue_market_id,o.side)) market_sides,
 sum(f.contracts) contracts, min(f.filled_at) first_fill, max(f.filled_at) last_fill
FROM fills f JOIN orders o ON o.id=f.order_id
WHERE NOT f.replay AND NOT o.replay AND f.filled_at<'2026-09-18 17:37+00'
GROUP BY 1,2,3 ORDER BY 1,2,3;
COMMIT;

\echo D. First counterfactual fill timing versus placement timing, orders placed after repair isolated
BEGIN READ ONLY;
WITH firsts AS (
 SELECT order_id,min(filled_at) first_fill FROM fills
 WHERE NOT replay AND fill_method='no_watcher' AND filled_at<'2026-09-18 17:37+00' GROUP BY order_id
), x AS (
 SELECT o.*,f.first_fill,extract(epoch FROM o.kickoff_utc-f.first_fill)/3600 fill_ttk_h,
 extract(epoch FROM f.first_fill-o.placed_at)/3600 wait_h
 FROM firsts f JOIN orders o ON o.id=f.order_id WHERE NOT o.replay
)
SELECT CASE WHEN id<=10886 THEN 'pre_repair_order' ELSE 'post_repair_order' END cohort,
 CASE WHEN fill_ttk_h<0 THEN 'after_kickoff' WHEN fill_ttk_h<=3 THEN '0_to_3h'
 WHEN fill_ttk_h<=24 THEN '3_to_24h' ELSE 'over_24h' END fill_window,
 count(*) orders, count(DISTINCT game_id) games,count(DISTINCT (venue_market_id,side)) market_sides,
 count(*) FILTER (WHERE kickoff_utc-placed_at>interval '3h') placed_over_3h,
 round(percentile_cont(0.5) WITHIN GROUP (ORDER BY wait_h)::numeric,2) median_wait_h,
 round(percentile_cont(0.5) WITHIN GROUP (ORDER BY fill_ttk_h)::numeric,2) median_fill_ttk_h
FROM x GROUP BY 1,2 ORDER BY 1,2;
COMMIT;

\echo E. Duplicate counterfactual liquidity within each variant, same ticker/side/source trade
BEGIN READ ONLY;
WITH x AS (
 SELECT CASE WHEN f.id<=1878 THEN 'pre_repair' ELSE 'post_repair' END simulator,
 o.variant_id,o.ticker,o.side,f.source_trade_id,count(*) fill_rows,count(DISTINCT o.id) orders,
 sum(f.contracts) contracts
 FROM fills f JOIN orders o ON o.id=f.order_id
 WHERE NOT f.replay AND NOT o.replay AND f.fill_method='no_watcher'
 AND f.source_trade_id IS NOT NULL AND f.filled_at<'2026-09-18 17:37+00'
 GROUP BY 1,2,3,4,5
)
SELECT simulator,variant_id,count(*) distinct_trade_keys,sum(fill_rows) attributed_fill_rows,
 count(*) FILTER(WHERE orders>1) reused_trade_keys,
 sum(fill_rows) FILTER(WHERE orders>1) rows_on_reused_trades,max(orders) max_orders_per_trade,
 sum(contracts) total_attributed_contracts
FROM x GROUP BY 1,2 ORDER BY 1,2;
COMMIT;
