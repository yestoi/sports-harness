\set ON_ERROR_STOP on
\pset pager off
\timing on
SET default_transaction_read_only=on;
SET statement_timeout='25s';
SET lock_timeout='1s';
SET application_name='codex_fill_review';

\echo P corrected. The teams key is sport/id and its label is display_name
BEGIN READ ONLY;
SELECT clock_timestamp(),current_setting('transaction_read_only');
SELECT g.id,g.sport,h.display_name home,a.display_name away,g.kickoff_utc
FROM games g LEFT JOIN teams h ON h.id=g.home_team_id AND h.sport=g.sport
LEFT JOIN teams a ON a.id=g.away_team_id AND a.sport=g.sport
WHERE g.id IN (469,116,118,549) ORDER BY g.id;
COMMIT;

\echo Q. Markout vintage and adverse drift, new post-repair orders only, no watched-fill filter
BEGIN READ ONLY;
WITH firsts AS (
 SELECT order_id,min(filled_at) first_fill FROM fills WHERE NOT replay AND fill_method='no_watcher'
 AND filled_at<'2026-09-18 17:37+00' GROUP BY 1
)
SELECT o.variant_id,count(*) orders,
 round(percentile_cont(0.5) WITHIN GROUP(ORDER BY k.fair_age_s)::numeric,1) median_markout_fair_age_s,
 round(percentile_cont(0.9) WITHIN GROUP(ORDER BY k.fair_age_s)::numeric,1) p90_markout_fair_age_s,
 count(*) FILTER(WHERE z.fair_p IS NOT NULL) drift_available,
 round(avg(100*(z.fair_p-CASE WHEN o.side='yes' THEN o.fair_p_at_place ELSE 1-o.fair_p_at_place END)),3) mean_drift_pts,
 count(*) FILTER(WHERE z.fair_p<o.prob) fair_at_fill_below_order_price
FROM firsts f JOIN orders o ON o.id=f.order_id
JOIN markouts k ON k.order_id=o.id AND k.anchor='nw_fill' AND k.horizon='30m'
LEFT JOIN markouts z ON z.order_id=o.id AND z.anchor='nw_fill' AND z.horizon='0m'
WHERE NOT o.replay AND o.id>10886 AND k.horizon_ts<'2026-09-18 17:37+00'
AND NOT EXISTS(SELECT 1 FROM benchmarks b WHERE b.game_id=o.game_id AND b.kickoff_moved)
GROUP BY 1 ORDER BY 1;
COMMIT;
