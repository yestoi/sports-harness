\set ON_ERROR_STOP on
\pset pager off
\timing on
SET default_transaction_read_only=on;
SET statement_timeout='25s';
SET lock_timeout='1s';
SET application_name='codex_fill_review';

\echo O. Concrete repeated liquidity, post-repair counterfactuals on the gate variant
BEGIN READ ONLY;
SELECT clock_timestamp(),current_setting('transaction_read_only');
WITH x AS MATERIALIZED (
 SELECT o.ticker,o.side,f.source_trade_id,min(f.filled_at) trade_at,
 count(DISTINCT o.id) hypothetical_orders,sum(f.contracts) attributed_contracts
 FROM fills f JOIN orders o ON o.id=f.order_id
 WHERE NOT f.replay AND NOT o.replay AND f.fill_method='no_watcher' AND f.id>1878
 AND o.variant_id='5632da729fa7' AND f.source_trade_id IS NOT NULL
 AND f.filled_at>='2026-09-15 05:23:44+00' AND f.filled_at<'2026-09-18 17:37+00'
 GROUP BY 1,2,3 ORDER BY count(DISTINCT o.id) DESC, f.source_trade_id LIMIT 5
)
SELECT x.*,p.count recorded_trade_contracts,p.ts recorded_trade_ts,
 round(attributed_contracts/nullif(p.count,0),2) attribution_multiple
FROM x LEFT JOIN LATERAL (
 SELECT count,ts FROM venue_trades t WHERE t.venue='kalshi' AND t.ticker=x.ticker
 AND t.trade_id=x.source_trade_id AND t.ts BETWEEN x.trade_at-interval '1 second' AND x.trade_at+interval '1 second'
 AND t.ts>='2026-09-15 05:23:44+00' AND t.ts<'2026-09-18 17:37+00'
 ORDER BY t.ts LIMIT 1
) p ON true;
COMMIT;

\echo P. Game concentration labels
BEGIN READ ONLY;
SELECT g.id,g.sport,h.canonical_name home,a.canonical_name away,g.kickoff_utc
FROM games g LEFT JOIN teams h ON h.id=g.home_team_id LEFT JOIN teams a ON a.id=g.away_team_id
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
