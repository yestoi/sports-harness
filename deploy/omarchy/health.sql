\pset pager off
SET statement_timeout='60s';
select now() as sampled_at, version_num as schema from alembic_version;
select id, started_at, status, build_sha, notes->'errors' as errors
  from runs order by id desc limit 8;
select id, started_at, status, build_sha, notes->'errors' as errors
  from runs where status<>'skipped' order by id desc limit 3;
select last_loop_at, last_loop_ms, p95_loop_ms, loops, open_orders, last_error,
  now()-last_loop_at as heartbeat_age from exec_heartbeat;
select id, ts, kind, now()-ts as tape_age from orderbook_events order by id desc limit 3;
select pg_size_pretty(pg_database_size('harness')) as database_size,
  pg_size_pretty((select sum(size) from pg_ls_waldir())) as wal_size;
select status,count(*) from orders where replay=false group by status;
select kind,status,bytes,finished_at from backup_runs order by id desc limit 6;
select name,min(value),max(value),count(*) from metric_samples
  where ts>now()-interval '5 minutes'
  and name in ('exec.loop_ms','recorder.tick_ms','recorder.rss_mb','host.mem_available_mb','host.disk_free_gb')
  group by name order by name;
select 'orders_without_place' as check_name,count(*) as violations from orders o
  where replay=false and not exists(select 1 from order_events e where e.order_id=o.id and e.kind='place')
union all
select 'overfilled_orders',count(*) from orders where replay=false and filled_contracts>contracts
union all
select 'fills_exceed_order',count(*) from fills f join orders o on o.id=f.order_id
  where f.replay=false and f.contracts>o.contracts
union all
select 'markouts_after_horizon',count(*) from markouts where at_ts>horizon_ts
union all
select 'live_venue_writes',count(*) from venue_requests where env='prod' and method<>'GET';
