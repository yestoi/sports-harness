\set ON_ERROR_STOP on
SET default_transaction_read_only=on;
SET statement_timeout='25s';
SET lock_timeout='1s';
SET application_name='codex_fill_review';
BEGIN READ ONLY;
COPY (
 WITH firsts AS (
  SELECT DISTINCT ON(order_id) order_id,id first_fill_id,filled_at,fill_method
  FROM fills WHERE NOT replay AND fill_method IN ('queue_model','no_watcher')
  AND filled_at<'2026-09-18 17:37+00' ORDER BY order_id,filled_at,id
 )
 SELECT o.id order_id,o.variant_id,o.game_id,o.venue_market_id,o.side,o.placed_at,
 f.first_fill_id,f.filled_at,f.fill_method,k.at_ts,k.horizon_ts,k.fair_changed,
 k.fair_age_s,100*(k.fair_p-k.p_used-k.fee_per_contract) net_markout_pts,
 100*(z.fair_p-(CASE WHEN o.side='yes' THEN o.fair_p_at_place ELSE 1-o.fair_p_at_place END)) adverse_drift_pts,
 z.fair_age_s fill_fair_age_s,
 extract(epoch FROM o.kickoff_utc-f.filled_at)/3600 fill_ttk_h
 FROM firsts f JOIN orders o ON o.id=f.order_id
 JOIN markouts k ON k.order_id=o.id AND k.anchor='nw_fill' AND k.horizon='30m'
 LEFT JOIN markouts z ON z.order_id=o.id AND z.anchor='nw_fill' AND z.horizon='0m'
 WHERE NOT o.replay AND o.game_id IS NOT NULL AND k.horizon_ts<'2026-09-18 17:37+00'
 AND NOT EXISTS(SELECT 1 FROM benchmarks b WHERE b.game_id=o.game_id AND b.kickoff_moved)
 ORDER BY o.variant_id,f.first_fill_id,o.id
) TO STDOUT WITH CSV HEADER;
COMMIT;
