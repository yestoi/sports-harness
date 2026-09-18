\set ON_ERROR_STOP on
\pset pager off
\timing on
SET default_transaction_read_only = on;
SET statement_timeout = '25s';
SET lock_timeout = '1s';
SET application_name = 'codex_fill_review';

\echo K. Veto decisions while a cancelled-or-expired order on the same key was still resting
BEGIN READ ONLY;
SELECT clock_timestamp(),current_setting('transaction_read_only');
WITH d AS MATERIALIZED (
 SELECT v.*,s.variant_id,s.venue_market_id,s.side
 FROM veto_decisions v JOIN signals s ON s.id=v.signal_id
 WHERE v.decided_at>='2026-09-15 05:23:44+00' AND v.decided_at<'2026-09-18 17:37+00'
 AND v.decision IN ('proceed','reduce','veto') AND NOT s.replay
), x AS (
 SELECT d.*, EXISTS(SELECT 1 FROM orders o WHERE NOT o.replay
 AND o.variant_id=d.variant_id AND o.venue_market_id=d.venue_market_id AND o.side=d.side
 AND o.placed_at<d.signal_created_at AND o.cancelled_at>d.signal_created_at
 AND coalesce(o.expiry,o.cancelled_at)>d.signal_created_at
 AND NOT EXISTS(SELECT 1 FROM fills f WHERE f.order_id=o.id AND NOT f.replay AND f.fill_method='queue_model')) resting_at_signal,
 EXISTS(SELECT 1 FROM orders o WHERE NOT o.replay
 AND o.variant_id=d.variant_id AND o.venue_market_id=d.venue_market_id AND o.side=d.side
 AND o.placed_at<d.decided_at AND o.cancelled_at>d.decided_at
 AND coalesce(o.expiry,o.cancelled_at)>d.decided_at
 AND NOT EXISTS(SELECT 1 FROM fills f WHERE f.order_id=o.id AND NOT f.replay AND f.fill_method='queue_model')) resting_at_decision
 FROM d
)
SELECT variant_id,count(*) decided_signals,
 count(*) FILTER(WHERE resting_at_signal) resting_at_signal,
 count(*) FILTER(WHERE resting_at_decision) resting_at_decision,
 count(*) FILTER(WHERE NOT from_cache AND call_id IS NOT NULL) uncached_decisions,
 count(*) FILTER(WHERE NOT from_cache AND call_id IS NOT NULL AND resting_at_signal) uncached_resting_at_signal,
 count(*) FILTER(WHERE NOT from_cache AND call_id IS NOT NULL AND resting_at_decision) uncached_resting_at_decision
FROM x GROUP BY 1 ORDER BY 1;
COMMIT;

\echo L. Latest veto coverage and weekly spend through fixed cutoff
BEGIN READ ONLY;
SELECT v.decision,count(*) signals,count(DISTINCT v.call_id) call_ids,
 count(*) FILTER(WHERE g.kickoff_utc-v.signal_created_at BETWEEN interval '0' AND interval '6 hours') signals_inside_6h,
 min(g.kickoff_utc-v.signal_created_at) closest_to_kickoff
FROM veto_decisions v JOIN signals s ON s.id=v.signal_id
JOIN venue_markets m ON m.id=s.venue_market_id JOIN games g ON g.id=m.game_id
WHERE v.decided_at>='2026-09-14 05:00+00' AND v.decided_at<'2026-09-18 17:37+00' AND NOT s.replay
GROUP BY 1 ORDER BY 1;
SELECT day,sum(usd) spent,sum(usd_reserved) reserved,sum(calls) model_calls
FROM research_spend WHERE day BETWEEN '2026-09-14' AND '2026-09-18' GROUP BY 1 ORDER BY 1;
COMMIT;

\echo M. Post-repair order observation quality up to FIRST no-watcher fill, descriptive not a gate recalculation
BEGIN READ ONLY;
WITH firsts AS MATERIALIZED (
 SELECT order_id,min(filled_at) first_fill FROM fills
 WHERE NOT replay AND fill_method='no_watcher' AND filled_at<'2026-09-18 17:37+00' GROUP BY 1
), x AS (
 SELECT o.id,o.variant_id,o.venue_market_id,o.placed_at,o.book_source,f.first_fill,
 extract(epoch FROM f.first_fill-o.placed_at) wait_s,
 coalesce((SELECT sum(extract(epoch FROM least(coalesce(i.ended_at,f.first_fill),f.first_fill)-greatest(i.started_at,o.placed_at)))
 FROM market_dirty_intervals i WHERE i.venue_market_id=o.venue_market_id AND NOT i.replay
 AND i.started_at<f.first_fill AND coalesce(i.ended_at,f.first_fill)>o.placed_at),0) dirty_s,
 coalesce((SELECT sum(extract(epoch FROM least(coalesce(i.ended_at,f.first_fill),f.first_fill)-greatest(i.started_at,o.placed_at)))
 FROM market_observation_intervals i WHERE i.venue_market_id=o.venue_market_id AND NOT i.replay
 AND i.started_at<f.first_fill AND coalesce(i.ended_at,f.first_fill)>o.placed_at),0) observed_s
 FROM firsts f JOIN orders o ON o.id=f.order_id WHERE NOT o.replay AND o.id>10886
)
SELECT variant_id,count(*) orders,count(*) FILTER(WHERE dirty_s=0) zero_dirty_before_first_fill,
 count(*) FILTER(WHERE book_source='ws') ws_at_placement,
 round(percentile_cont(0.5) WITHIN GROUP(ORDER BY dirty_s/3600)::numeric,2) median_dirty_h,
 round(percentile_cont(0.5) WITHIN GROUP(ORDER BY greatest(wait_s-observed_s,0)/3600)::numeric,2) median_unobserved_h,
 round(percentile_cont(0.5) WITHIN GROUP(ORDER BY wait_s/3600)::numeric,2) median_wait_h
FROM x GROUP BY 1 ORDER BY 1;
COMMIT;

\echo N. Dirty-time causes up to first no-watcher fill for post-repair orders (overlapping alternatives, not portfolio totals)
BEGIN READ ONLY;
WITH firsts AS MATERIALIZED (
 SELECT order_id,min(filled_at) first_fill FROM fills
 WHERE NOT replay AND fill_method='no_watcher' AND filled_at<'2026-09-18 17:37+00' GROUP BY 1
)
SELECT i.cause,count(DISTINCT o.id) orders,
 round(sum(extract(epoch FROM least(coalesce(i.ended_at,f.first_fill),f.first_fill)-greatest(i.started_at,o.placed_at)))/3600,2) attributed_dirty_h
FROM firsts f JOIN orders o ON o.id=f.order_id
JOIN market_dirty_intervals i ON i.venue_market_id=o.venue_market_id AND NOT i.replay
AND i.started_at<f.first_fill AND coalesce(i.ended_at,f.first_fill)>o.placed_at
WHERE NOT o.replay AND o.id>10886 GROUP BY 1 ORDER BY 3 DESC;
COMMIT;
