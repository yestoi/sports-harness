\set ON_ERROR_STOP on
\pset pager off
\timing on
SET default_transaction_read_only = on;
SET statement_timeout = '25s';
SET lock_timeout = '1s';
SET application_name = 'codex_fill_review';

\echo F. Latest fair actually timestamped before cancellation, deterministic bounded sample
BEGIN READ ONLY;
SELECT clock_timestamp(),current_setting('transaction_read_only');
WITH sampled AS MATERIALIZED (
 SELECT id,venue_market_id,fair_row_id_at_place,cancelled_at,placed_at FROM orders
 WHERE NOT replay AND cancel_reason='fair_stale' AND placed_at>='2026-09-15 05:23:44+00'
 AND cancelled_at<'2026-09-18 17:37+00' AND id%47=0 ORDER BY id LIMIT 650
), x AS (
 SELECT o.*,fv.created_at fair_ts,g.fair_value_id latest_fair_id,g.stale_allowance_s,
 extract(epoch FROM o.cancelled_at-fv.created_at) age_s
 FROM sampled o LEFT JOIN LATERAL (
 SELECT fair_value_id,stale_allowance_s FROM market_gap_snapshots
 WHERE venue_market_id=o.venue_market_id AND created_at<=o.cancelled_at
 ORDER BY created_at DESC,id DESC LIMIT 1
 ) g ON true LEFT JOIN fair_values fv ON fv.id=g.fair_value_id
)
SELECT count(*) sampled_orders,count(*) FILTER(WHERE fair_ts IS NULL) missing_fair,
 count(*) FILTER(WHERE latest_fair_id<>fair_row_id_at_place) fair_updated_since_placement,
 count(*) FILTER(WHERE age_s>greatest(180,coalesce(stale_allowance_s,0))) beyond_allowance,
 round(percentile_cont(0.5) WITHIN GROUP(ORDER BY age_s)::numeric,2) median_age_at_cancel_s,
 round(percentile_cont(0.9) WITHIN GROUP(ORDER BY age_s)::numeric,2) p90_age_at_cancel_s,
 min(age_s) min_age_s,max(age_s) max_age_s FROM x;
COMMIT;

\echo G. Counterfactual first-fill regime, using the sport-wide cadence function logic
BEGIN READ ONLY;
WITH firsts AS (
 SELECT order_id,min(filled_at) first_fill FROM fills
 WHERE NOT replay AND fill_method='no_watcher' AND filled_at<'2026-09-18 17:37+00' GROUP BY 1
), x AS (
 SELECT o.id,o.variant_id,o.game_id,o.sport,f.first_fill,
 extract(hour FROM f.first_fill AT TIME ZONE 'America/Chicago') hour_ct,
 extract(isodow FROM f.first_fill AT TIME ZONE 'America/Chicago') dow,
 EXISTS(SELECT 1 FROM games g WHERE g.sport=o.sport AND g.kickoff_utc BETWEEN f.first_fill-interval '4 hours' AND f.first_fill) in_progress,
 EXISTS(SELECT 1 FROM games g WHERE g.sport=o.sport AND g.kickoff_utc BETWEEN f.first_fill+interval '60 minutes' AND f.first_fill+interval '100 minutes') burst,
 (SELECT min(g.kickoff_utc) FROM games g WHERE g.sport=o.sport AND (g.kickoff_utc AT TIME ZONE 'America/Chicago')::date=(f.first_fill AT TIME ZONE 'America/Chicago')::date) first_kick,
 (SELECT max(g.kickoff_utc) FROM games g WHERE g.sport=o.sport AND (g.kickoff_utc AT TIME ZONE 'America/Chicago')::date=(f.first_fill AT TIME ZONE 'America/Chicago')::date) last_kick
 FROM firsts f JOIN orders o ON o.id=f.order_id WHERE NOT o.replay
), classified AS (
 SELECT *, CASE WHEN hour_ct>=1 AND hour_ct<8 AND NOT in_progress THEN 'quiet_hours'
 WHEN sport='nfl' AND burst THEN '20s_burst'
 WHEN in_progress OR first_fill BETWEEN first_kick-interval '3 hours' AND last_kick THEN '120s_window'
 WHEN dow>=6 THEN '300s_weekend' ELSE '900s_weekday' END cadence
 FROM x
)
SELECT CASE WHEN id>10886 THEN 'post_repair_order' ELSE 'pre_repair_order' END cohort,
 cadence,count(*) orders,count(DISTINCT game_id) games FROM classified GROUP BY 1,2 ORDER BY 1,2;
COMMIT;

\echo H. Markout evidence split by the simulator that produced the FIRST no-watcher fill
BEGIN READ ONLY;
WITH firsts AS MATERIALIZED (
 SELECT DISTINCT ON(order_id) order_id,id first_fill_id,filled_at FROM fills
 WHERE NOT replay AND fill_method='no_watcher' AND filled_at<'2026-09-18 17:37+00'
 ORDER BY order_id,filled_at,id
)
SELECT o.variant_id,CASE WHEN f.first_fill_id<=1878 THEN 'pre_repair_fill' ELSE 'post_repair_fill' END cohort,
 count(*) scored_orders,count(DISTINCT o.game_id) games,
 count(*) FILTER(WHERE k.fair_changed AND k.fair_p IS NOT NULL AND k.p_used IS NOT NULL AND k.fee_per_contract IS NOT NULL) changed_usable,
 count(DISTINCT o.game_id) FILTER(WHERE k.fair_changed AND k.fair_p IS NOT NULL AND k.p_used IS NOT NULL AND k.fee_per_contract IS NOT NULL) changed_games,
 round(avg(100*(k.fair_p-k.p_used-k.fee_per_contract)) FILTER(WHERE k.fair_changed),3) mean_net_markout_pts,
 round(avg(100*(k.fair_p-k.p_used-k.fee_per_contract)),3) all_rows_net_markout_pts
FROM firsts f JOIN orders o ON o.id=f.order_id JOIN markouts k ON k.order_id=o.id AND k.anchor='nw_fill' AND k.horizon='30m'
WHERE NOT o.replay AND k.horizon_ts<'2026-09-18 17:37+00'
AND NOT EXISTS(SELECT 1 FROM benchmarks b WHERE b.game_id=o.game_id AND b.kickoff_moved)
GROUP BY 1,2 ORDER BY 1,2;
COMMIT;

\echo I. Present watched versus counterfactual book dirtiness, NOT interchangeable gate populations
BEGIN READ ONLY;
SELECT variant_id,
 count(*) FILTER(WHERE filled_contracts>0) watched_filled,
 count(*) FILTER(WHERE filled_contracts>0 AND book_source='ws' AND dirty_minutes=0) watched_filled_gate_clean,
 count(*) FILTER(WHERE nw_filled_contracts>0) nw_filled,
 count(*) FILTER(WHERE nw_filled_contracts>0 AND book_source='ws' AND coalesce(nw_dirty_seconds,dirty_seconds)=0) nw_filled_zero_dirty,
 count(*) FILTER(WHERE nw_filled_contracts>0 AND nw_dirty_seconds IS NULL) nw_filled_missing_separate_dirty
FROM orders WHERE NOT replay AND placed_at>'2026-09-15 05:23:44+00' AND placed_at<'2026-09-18 17:37+00'
GROUP BY 1 ORDER BY 1;
COMMIT;

\echo J. Index definitions for choosing bounded follow-up reads
BEGIN READ ONLY;
SELECT tablename,indexname,indexdef FROM pg_indexes
WHERE schemaname='public' AND tablename IN ('orders','veto_decisions','veto_queue','signals','gate_reports') ORDER BY 1,2;
COMMIT;
