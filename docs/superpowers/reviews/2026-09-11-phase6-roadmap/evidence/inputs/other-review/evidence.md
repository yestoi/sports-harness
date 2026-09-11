# Field evidence, production NAS (pulled 2026-09-11 07:27-07:33 CT by the field-data agent; read-only)

Host: trey@192.168.12.228, stack /volume1/docker/sports-harness, build 7c3d555. All SQL ran with statement_timeout = 8000 ms. Times in tables are UTC unless the column is labelled CT (America/Chicago = UTC-5).

## A. Host

```
lscpu: 13th Gen Intel(R) Core(TM) i3-1315U, CPU(s) 8, scaling 37%
MemTotal 7,902,308 kB; MemFree 450,584 kB; MemAvailable 3,048,908 kB
free -m: total 7717 used 4822 free 414 shared 550 buff/cache 3864 available 2894; Swap total 9999 used 1871 free 8128
uptime 07:27:34 up 22 days 14:04, load 0.78 0.80 0.74
/proc/pressure/memory: some avg10=0.36 avg60=0.08 avg300=0.13; full avg10=0.36 avg60=0.08 avg300=0.13
/proc/pressure/io:     some avg10=0.08 avg60=0.02 avg300=0.37; full avg10=0.00 avg60=0.00 avg300=0.34
vmstat 5 2 (last line): r 1 b 0 swpd 1916632 free 424200 buff 53696 cache 3903472 si 2 so 0 bi 6 bo 165 in 1633 cs 4039 us 1 sy 0 id 99 wa 0
```

Block devices (lsblk):
```
sda            10.9T ROTA=1 disk      -> sda2 -> md1 (raid1, ROTA=1) -> ug_..._pool1-volume1 (lvm, ROTA=1) /volume1
nvme0n1       119.2G ROTA=0 (boot, /rootfs, /overlay 109G, 2G swap partition)
zram0-3       4 x 964M [SWAP]  (plus nvme0n1p5 2G swap)
/sys/block/sda/queue/rotational: 1 ; md1: 1 ; dm-0: 1 ; nvme0n1: 0
```
Postgres data mount (docker inspect sports-harness-postgres-1): bind /volume1/docker/sports-harness/pgdata -> /var/lib/postgresql/data. **pgdata is on the single rotational HDD (sda, RAID1 with one member), not on the NVMe.**

df /volume1: 11T size, 7.5T used, 3.4T avail, 69%.

Container limits (docker inspect .HostConfig.Memory / .NanoCpus): every sports-harness container mem=0 nanocpus=0 (no limits): app-backup, app-exec, app-research, app-run, app-serve, app-ws, postgres.

docker compose ps: postgres Up 3 days (healthy); app-backup Up 2 days; app-exec/app-research/app-run/app-serve/app-ws Up 48 minutes (restarted ~06:40 CT with 7c3d555); app-serve 127.0.0.1:8180->8080.

docker stats --no-stream:
```
app-research  0.01%   85.9 MiB   NET 1.25MB/280kB    BLOCK 123kB/4.93MB   PIDS 1
app-ws        2.88%   79.2 MiB   NET 283MB/191MB     BLOCK 2.43MB/418kB   PIDS 2
app-serve     0.24%  108.1 MiB   NET 97MB/44MB       BLOCK 16.9MB/811kB   PIDS 5
app-exec      0.01%  172.5 MiB   NET 3.65GB/538MB    BLOCK 13.5MB/3.46MB  PIDS 3
app-run       0.01%  137.0 MiB   NET 110MB/841MB     BLOCK 1.38MB/1.46MB  PIDS 4
app-backup    0.00%    2.6 MiB   NET 65.6GB/373MB    BLOCK 14GB/4.91GB    PIDS 2
postgres      0.74%   1.24 GiB   NET 63.5GB/312GB    BLOCK read 1.89TB / write 653GB  PIDS 16
wireguard, syncthing (55 MiB) also running
```

Backups (/volume1/docker/sports-harness/backups): dirs forever, nightly, partitions, weekly; .dump.lock (0 bytes, Sep 8 21:23).
nightly latest: harness-nightly-20260911T083001Z.dump 1,302,921,039 bytes (1.3 GB, 03:44 CT), .dump.age 1,303,239,319, .meta.json 1590, .ok 0 (03:46).
forever latest: harness-forever-20260911T084547Z.dump.age 6,182 bytes, .meta.json, .ok (03:45).

## B. Postgres settings

```
autovacuum on | checkpoint_completion_target 0.9 | effective_cache_size 196608 x 8kB = 1.5 GB
effective_io_concurrency 1 | jit on | maintenance_work_mem 262144 kB = 256 MB | max_connections 100
max_wal_size 4096 MB | random_page_cost 1.1 | seq_page_cost 1 | shared_buffers 65536 x 8kB = 512 MB
wal_compression off | work_mem 16384 kB = 16 MB
```
Note: random_page_cost 1.1 is an SSD setting; pgdata is on a rotational disk (section A).

## C. Table sizes (pg_stat_user_tables, top 20) and database size

```
 orderbook_events_y2026w37 | 29,485,094 | 17 GB
 orderbook_events_legacy   | 29,721,859 | 15 GB
 signals                   | 14,649,311 | 8828 MB
 fair_values               |  2,323,192 | 1513 MB
 raw_responses_y2026w37    |    173,400 | 1370 MB
 odds_snapshots            |  4,370,613 | 1048 MB
 venue_quotes              |  3,439,146 |  736 MB
 market_gap_snapshots      |  2,347,281 |  661 MB
 venue_trades_y2026w37     |  1,101,895 |  453 MB
 venue_trades_legacy       |    870,525 |  314 MB
 rfqs                      |     93,470 |  159 MB
 orders                    |      7,997 |  107 MB
 intents                   |     88,107 |   28 MB
 order_watch_samples       |    228,510 |   28 MB
 orderbook_snapshots       |     20,428 |   28 MB
 metric_samples            |    123,685 |   22 MB
 order_events              |    119,294 |   22 MB
 report_cells              |     54,570 |   12 MB
 runs                      |     11,218 |   10 MB
 venue_markets             |      6,084 | 9912 kB
pg_database_size('harness') = 47 GB
```
db.* metric (03:15 CT): db.size_gb 46.30, db.table_gb 31.71, db.growth_gb_per_day 8.94, db.brin_ranges_summarized 0.

## D. Executor history (metric_samples)

```
== D1 exec.loop_ms per day
     d      |  n   | avg  | p50  |  p95  |  max   
------------+------+------+------+-------+--------
 2026-09-08 |  980 | 4650 | 2246 | 16205 | 254339
 2026-09-09 | 1248 | 4127 | 3013 |  6188 | 216472
 2026-09-10 | 1233 | 8295 | 5189 | 15169 | 256171
 2026-09-11 |  364 | 9723 | 5344 | 10033 | 335763
(4 rows)

== D2 exec.loop_ms per hour 2026-09-10 16:00-23:59 CT
          h          | n  |  avg  |  p50  |  p95   |  max   
---------------------+----+-------+-------+--------+--------
 2026-09-10 16:00:00 | 52 |  9516 |  7773 |  21311 |  26647
 2026-09-10 17:00:00 | 52 |  8592 |  7876 |  12503 |  17522
 2026-09-10 18:00:00 | 48 | 18286 |  9060 | 101062 | 144216
 2026-09-10 19:00:00 | 44 | 27533 | 10165 | 114768 | 163406
 2026-09-10 20:00:00 | 48 | 20781 | 10101 |  72603 | 256171
 2026-09-10 21:00:00 | 49 | 16065 |  8939 |  52530 | 189925
 2026-09-10 22:00:00 | 53 |  7885 |  7619 |  13234 |  16516
 2026-09-10 23:00:00 | 52 |  6463 |  6025 |   9047 |  13427
(8 rows)

== D3 exec.open_orders hourly max, 3 days
          h          | mx  |  av   
---------------------+-----+-------
 2026-09-08 08:00:00 |  96 |   5.5
 2026-09-08 09:00:00 | 100 |  41.0
 2026-09-08 10:00:00 | 143 |  35.6
 2026-09-08 11:00:00 |  86 |  11.5
 2026-09-08 12:00:00 | 150 |  29.2
 2026-09-08 13:00:00 | 150 |  75.2
 2026-09-08 14:00:00 | 150 | 123.2
 2026-09-08 15:00:00 | 150 | 147.8
 2026-09-08 16:00:00 |  43 |  39.7
 2026-09-08 17:00:00 |  20 |   6.7
 2026-09-08 19:00:00 | 150 |  54.8
 2026-09-08 20:00:00 | 150 | 121.5
 2026-09-08 21:00:00 | 150 | 150.0
 2026-09-08 22:00:00 | 150 | 150.0
 2026-09-08 23:00:00 | 115 |   8.8
 2026-09-09 00:00:00 |  58 |  12.1
 2026-09-09 01:00:00 |  13 |   8.6
 2026-09-09 08:00:00 | 150 | 114.2
 2026-09-09 09:00:00 | 123 | 123.0
 2026-09-09 10:00:00 | 123 |  36.1
 2026-09-09 11:00:00 | 150 |  23.1
 2026-09-09 12:00:00 |  44 |  25.7
 2026-09-09 13:00:00 | 150 |  77.6
 2026-09-09 14:00:00 | 150 | 102.3
 2026-09-09 15:00:00 | 150 |  94.2
 2026-09-09 16:00:00 | 101 |  44.0
 2026-09-09 17:00:00 | 109 | 102.5
 2026-09-09 18:00:00 | 150 | 121.1
 2026-09-09 19:00:00 | 150 | 115.1
 2026-09-09 20:00:00 | 150 | 121.8
 2026-09-09 21:00:00 | 150 | 138.4
 2026-09-09 22:00:00 | 114 |  82.2
 2026-09-09 23:00:00 | 150 |  97.6
 2026-09-10 00:00:00 | 150 |  95.9
 2026-09-10 01:00:00 | 120 |  80.5
 2026-09-10 08:00:00 | 150 | 116.7
 2026-09-10 09:00:00 | 150 | 121.9
 2026-09-10 10:00:00 | 150 | 128.5
 2026-09-10 11:00:00 | 150 |  78.6
 2026-09-10 12:00:00 | 150 |  90.5
 2026-09-10 13:00:00 | 150 | 144.0
 2026-09-10 14:00:00 | 150 | 101.5
 2026-09-10 15:00:00 | 150 |  74.8
 2026-09-10 16:00:00 | 150 | 109.8
 2026-09-10 17:00:00 | 150 |  94.0
 2026-09-10 18:00:00 | 150 |  81.9
 2026-09-10 19:00:00 | 150 |  52.8
 2026-09-10 20:00:00 | 150 | 126.6
 2026-09-10 21:00:00 | 150 | 150.0
 2026-09-10 22:00:00 | 150 | 150.0
 2026-09-10 23:00:00 | 150 | 133.8
 2026-09-11 00:00:00 | 150 | 129.8
 2026-09-11 01:00:00 |  64 |   9.2
(53 rows)

== D4 hourly sums placed/cancelled/filled, 3 days
          h          |   placed   | cancelled  |  filled   
---------------------+------------+------------+-----------
 2026-09-08 08:00:00 |  98.000000 |  98.000000 |  0.000000
 2026-09-08 09:00:00 | 100.000000 |            |  0.000000
 2026-09-08 10:00:00 | 227.000000 | 312.000000 | 38.920000
 2026-09-08 11:00:00 |  84.000000 |  93.000000 |  0.000000
 2026-09-08 12:00:00 | 144.000000 | 140.000000 |  0.000000
 2026-09-08 13:00:00 | 211.000000 | 138.000000 |  0.000000
 2026-09-08 14:00:00 | 104.000000 |  37.000000 |  0.000000
 2026-09-08 15:00:00 |   0.000000 | 110.000000 |  0.000000
 2026-09-08 16:00:00 |   3.000000 |  26.000000 |  0.000000
 2026-09-08 17:00:00 |   3.000000 |  20.000000 |  0.000000
 2026-09-08 19:00:00 | 150.000000 |  97.000000 |  0.000000
 2026-09-08 20:00:00 | 134.000000 |  37.000000 |  0.000000
 2026-09-08 23:00:00 |   0.000000 | 150.000000 |  0.000000
 2026-09-09 00:00:00 |  58.000000 |  45.000000 |  0.000000
 2026-09-09 01:00:00 |   0.000000 |  13.000000 |  0.000000
 2026-09-09 08:00:00 | 150.000000 |  27.000000 |  0.000000
 2026-09-09 10:00:00 |   0.000000 | 120.000000 |  0.000000
 2026-09-09 11:00:00 | 404.000000 | 364.000000 |  0.000000
 2026-09-09 12:00:00 | 260.000000 | 273.000000 |  0.000000
 2026-09-09 13:00:00 | 275.000000 | 189.000000 |  0.000000
 2026-09-09 14:00:00 | 274.000000 | 240.000000 |  0.000000
 2026-09-09 15:00:00 | 260.000000 | 396.000000 |  0.000000
 2026-09-09 16:00:00 | 101.000000 |  14.000000 |  0.000000
 2026-09-09 17:00:00 |  13.000000 |   5.000000 |  0.000000
 2026-09-09 18:00:00 |  53.000000 | 115.000000 |  0.000000
 2026-09-09 19:00:00 | 171.000000 | 144.000000 |  0.000000
 2026-09-09 20:00:00 | 124.000000 |  73.000000 |  0.000000
 2026-09-09 21:00:00 | 102.000000 | 109.000000 |  0.000000
 2026-09-09 22:00:00 |  96.000000 | 121.000000 |  0.000000
 2026-09-09 23:00:00 | 274.000000 | 252.000000 |  0.000000
 2026-09-10 00:00:00 | 512.000000 | 473.000000 |  0.000000
 2026-09-10 01:00:00 |   0.000000 | 150.000000 |  0.000000
 2026-09-10 08:00:00 | 256.000000 | 106.000000 |  0.000000
 2026-09-10 09:00:00 | 149.000000 | 149.000000 |  0.000000
 2026-09-10 10:00:00 | 134.000000 | 134.000000 |  0.000000
 2026-09-10 11:00:00 | 157.000000 | 157.000000 |  0.000000
 2026-09-10 12:00:00 | 276.000000 | 276.000000 |  0.000000
 2026-09-10 13:00:00 |  78.000000 |  78.000000 |  0.000000
 2026-09-10 14:00:00 | 255.000000 | 287.000000 |  0.000000
 2026-09-10 15:00:00 | 155.000000 | 123.000000 |  0.000000
 2026-09-10 16:00:00 | 335.000000 | 335.000000 |  0.000000
 2026-09-10 17:00:00 | 441.000000 | 591.000000 |  0.000000
 2026-09-10 18:00:00 | 327.000000 | 210.000000 |  0.000000
 2026-09-10 19:00:00 | 328.000000 | 413.000000 |  0.000000
 2026-09-10 20:00:00 | 285.000000 | 167.000000 |  0.000000
 2026-09-10 21:00:00 |  10.000000 |  10.000000 |  0.000000
 2026-09-10 22:00:00 |   9.000000 |   9.000000 |  0.000000
 2026-09-10 23:00:00 | 129.000000 | 177.000000 |  0.000000
 2026-09-11 00:00:00 | 288.000000 | 326.000000 |  0.000000
 2026-09-11 01:00:00 |   0.000000 |  64.000000 |  0.000000
(50 rows)

```
Note: the D5 query bounded to 2 days did not return before the ssh session closed (recorded as timed out); rerun with a 6 h bound below.

```
== D5 latest per name (6h bound)
           name            |              ts               |  value  
---------------------------+-------------------------------+---------
 db.brin_ranges_summarized | 2026-09-11 09:15:53.532981+00 |    0.00
 db.growth_gb_per_day      | 2026-09-11 09:15:53.532981+00 |    8.94
 db.size_gb                | 2026-09-11 09:15:53.532981+00 |   46.30
 db.table_gb               | 2026-09-11 09:15:53.532981+00 |   31.71
 recorder.errors           | 2026-09-11 12:28:12.110899+00 |    0.00
 recorder.fetched          | 2026-09-11 12:28:12.110899+00 |   96.00
 recorder.tick_ms          | 2026-09-11 12:28:12.110899+00 | 6917.00
 recorder.trade_gaps       | 2026-09-11 12:28:12.110899+00 |    0.00
 serve.snapshot_ms         | 2026-09-11 12:28:12.072195+00 |    4.00
(9 rows)

== D6 distinct metric names 6h
           name            | count 
---------------------------+-------
 db.brin_ranges_summarized |     1
 db.growth_gb_per_day      |     1
 db.size_gb                |     1
 db.table_gb               |     6
 exec.cancelled            |     1
 exec.dirty_markets        |   287
 exec.filled_contracts     |   287
 exec.intents_considered   |   287
 exec.loop_ms              |   287
 exec.loops_skipped        |   287
 exec.open_orders          |   287
 exec.p95_loop_ms          |   287
 exec.placed               |   287
 exec.tape_batch_min       |   287
 exec.tape_lag_tickers     |   287
 exec.ws_event_age_s       |   287
 exec.ws_event_ahead_s     |   287
 host.disk_free_gb         |     1
 host.disk_total_gb        |     1
 host.mem_available_mb     |     1
 match.rate                |     2
 match.unmatched           |     2
 recorder.errors           |   694
 recorder.fetched          |   418
 recorder.tick_ms          |   694
 recorder.trade_gaps       |   694
 serve.snapshot_ms         |   639
 ws.events_per_min         |   351
 ws.gaps                   |   351
 ws.reconnects             |   351
 ws.sink_lag_s             |   346
 ws.subscribed_tickers     |   351
 ws.trades_per_min         |   351
(33 rows)

== E runs
                           List of relations
 Schema |              Name              |       Type        |  Owner  
--------+--------------------------------+-------------------+---------
 public | alembic_version                | table             | harness
 public | backup_runs                    | table             | harness
 public | backup_runs_id_seq             | sequence          | harness
 public | benchmarks                     | table             | harness
 public | benchmarks_id_seq              | sequence          | harness
 public | check_results                  | table             | harness
 public | check_results_id_seq           | sequence          | harness
 public | clv                            | view              | harness
 public | config_history                 | table             | harness
 public | dashboard_snapshots            | table             | harness
 public | equity_snapshots               | table             | harness
 public | exec_heartbeat                 | table             | harness
 public | fair_values                    | table             | harness
 public | fair_values_id_seq             | sequence          | harness
 public | fills                          | table             | harness
 public | fills_id_seq                   | sequence          | harness
 public | futures_snapshots              | table             | harness
 public | futures_snapshots_id_seq       | sequence          | harness
 public | game_score_events              | table             | harness
 public | game_score_events_id_seq       | sequence          | harness
 public | games                          | table             | harness
 public | games_id_seq                   | sequence          | harness
 public | gap_outcomes                   | table             | harness
 public | gate_reports                   | table             | harness
 public | gate_reports_id_seq            | sequence          | harness
 public | intents                        | table             | harness
 public | job_runs                       | table             | harness
 public | job_runs_id_seq                | sequence          | harness
 public | job_state                      | table             | harness
 public | kill_switch                    | table             | harness
 public | kill_switch_id_seq             | sequence          | harness
 public | ledger                         | table             | harness
 public | ledger_id_seq                  | sequence          | harness
 public | market_gap_snapshots           | table             | harness
 public | market_gap_snapshots_id_seq    | sequence          | harness
 public | markouts                       | table             | harness
 public | metric_samples                 | table             | harness
 public | metric_samples_id_seq          | sequence          | harness
 public | normalize_state                | table             | harness
 public | odds_snapshots                 | table             | harness
 public | odds_snapshots_id_seq          | sequence          | harness
 public | operator_events                | table             | harness
 public | operator_events_id_seq         | sequence          | harness
 public | order_clv                      | table             | harness
 public | order_episodes                 | view              | harness
 public | order_events                   | table             | harness
 public | order_events_id_seq            | sequence          | harness
 public | order_watch_samples            | table             | harness
 public | orderbook_events               | partitioned table | harness
 public | orderbook_events_id_seq        | sequence          | harness
 public | orderbook_events_legacy        | table             | harness
 public | orderbook_events_legacy_id_seq | sequence          | harness
 public | orderbook_events_y2026w37      | table             | harness
 public | orderbook_events_y2026w38      | table             | harness
 public | orderbook_snapshots            | table             | harness
 public | orderbook_snapshots_id_seq     | sequence          | harness
 public | orders                         | table             | harness
 public | orders_id_seq                  | sequence          | harness
 public | parlay_cards                   | table             | harness
 public | parlay_cards_id_seq            | sequence          | harness
 public | parlay_ledger                  | table             | harness
 public | parlay_ledger_id_seq           | sequence          | harness
 public | parlay_leg_probs               | table             | harness
 public | parlay_legs                    | table             | harness
 public | parlay_legs_id_seq             | sequence          | harness
 public | parlay_placements              | table             | harness
 public | parlay_placements_card_id_seq  | sequence          | harness
 public | positions                      | view              | harness
 public | raw_responses                  | partitioned table | harness
 public | raw_responses_id_seq           | sequence          | harness
 public | raw_responses_y2026w37         | table             | harness
 public | raw_responses_y2026w38         | table             | harness
 public | report_annotations             | table             | harness
 public | report_cells                   | table             | harness
 public | report_runs                    | table             | harness
 public | report_runs_id_seq             | sequence          | harness
 public | research_notes                 | table             | harness
 public | research_spend                 | table             | harness
 public | rfq_quotes                     | table             | harness
 public | rfq_quotes_id_seq              | sequence          | harness
 public | rfqs                           | table             | harness
 public | runs                           | table             | harness
 public | runs_id_seq                    | sequence          | harness
 public | settlements                    | table             | harness
 public | settlements_game_id_seq        | sequence          | harness
 public | signals                        | table             | harness
 public | signals_id_seq                 | sequence          | harness
 public | source_state                   | table             | harness
 public | strategy_variants              | table             | harness
 public | team_aliases                   | table             | harness
 public | teams                          | table             | harness
 public | trade_watermarks               | table             | harness
 public | venue_markets                  | table             | harness
 public | venue_markets_id_seq           | sequence          | harness
 public | venue_quotes                   | table             | harness
 public | venue_quotes_id_seq            | sequence          | harness
 public | venue_requests                 | table             | harness
 public | venue_requests_id_seq          | sequence          | harness
 public | venue_settlements              | table             | harness
 public | venue_status                   | table             | harness
 public | venue_trades                   | partitioned table | harness
 public | venue_trades_legacy            | table             | harness
 public | venue_trades_y2026w37          | table             | harness
 public | venue_trades_y2026w38          | table             | harness
 public | veto_decisions                 | table             | harness
 public | veto_h9                        | view              | harness
 public | veto_queue                     | table             | harness
 public | weather_points                 | table             | harness
 public | weather_snapshots              | table             | harness
 public | weather_snapshots_id_seq       | sequence          | harness
(110 rows)

                                          Table "public.runs"
      Column      |           Type           | Collation | Nullable |             Default              
------------------+--------------------------+-----------+----------+----------------------------------
 id               | bigint                   |           | not null | nextval('runs_id_seq'::regclass)
 started_at       | timestamp with time zone |           | not null | 
 finished_at      | timestamp with time zone |           |          | 
 status           | character varying(16)    |           | not null | 
 error            | text                     |           |          | 
 n_requests       | integer                  |           | not null | 
 credits_used     | integer                  |           | not null | 
 odds_remaining   | integer                  |           |          | 
 budget_exhausted | boolean                  |           | not null | 
 notes            | jsonb                    |           | not null | 
 build_sha        | character varying(24)    |           |          | 
Indexes:
    "runs_pkey" PRIMARY KEY, btree (id)

== E runs per day
     d      |  n   | budget_exhausted 
------------+------+------------------
 2026-09-07 | 1707 |              232
 2026-09-08 | 2599 |               73
 2026-09-09 | 2495 |              281
 2026-09-10 | 2220 |              243
 2026-09-11 |  860 |              492
(5 rows)

== E run kinds 24h
ERROR:  column "kind" does not exist
LINE 1: select kind, count(*) from runs where started_at > now() - i...
               ^
```

ws.* and exec.* averages over the last 6 h:
```
== ws metrics last 6h
         name          |  avg   |  max   
-----------------------+--------+--------
 ws.events_per_min     | 1932.8 | 6610.0
 ws.gaps               |    0.0 |    0.0
 ws.reconnects         |    0.0 |    5.0
 ws.sink_lag_s         |    1.1 |  208.1
 ws.subscribed_tickers |  500.0 |  500.0
 ws.trades_per_min     |   19.6 |   75.0
(6 rows)

== exec metrics last 6h
          name           |   avg   |   max    
-------------------------+---------+----------
 exec.cancelled          |     6.0 |      6.0
 exec.dirty_markets      |   122.8 |    125.0
 exec.filled_contracts   |     0.0 |      0.0
 exec.intents_considered |     0.0 |      0.0
 exec.loop_ms            | 10776.9 | 335763.0
 exec.loops_skipped      |     0.3 |     24.0
 exec.open_orders        |     0.3 |      6.0
 exec.p95_loop_ms        | 21006.8 | 235086.0
 exec.placed             |     0.0 |      0.0
 exec.tape_batch_min     |  5422.3 |  20000.0
 exec.tape_lag_tickers   |     0.0 |      0.0
 exec.ws_event_age_s     |     0.9 |      9.3
 exec.ws_event_ahead_s   |     0.1 |     11.4
(13 rows)

```
exec_heartbeat: last_loop_at 2026-09-11 12:30:11Z, loops 17138, open_orders 0, last_loop_ms 26754, p95_loop_ms 7112, loops_skipped 521, book_dirty_markets 125, executor_version 4.4.

## E. Recorder ticks (runs)

Columns: id, started_at, finished_at, status, error, n_requests, credits_used, odds_remaining, budget_exhausted (boolean), notes (jsonb), build_sha. No kind/stage column; notes keys (24h, 2,197 rows): errors, kalshi_trades_normalized, venue_limits, warnings, non_linear_cent, normalized, normalize_errors, odds_dropped, pricing, skipped_alternates, skipped_ladders, skipped_trades, taker_side_missing, trade_gaps, unresolved_teams; leg_probs and weather on 484.

Per day: count(*) and notes::text like '%budget_exhausted%' (this LIKE matches the key name, so it counts ticks that carry the key, not exhaustion; see the boolean and pricing sub-key tables next):
```
== E runs per day
     d      |  n   | budget_exhausted 
------------+------+------------------
 2026-09-07 | 1707 |              232
 2026-09-08 | 2599 |               73
 2026-09-09 | 2495 |              281
 2026-09-10 | 2220 |              243
 2026-09-11 |  860 |              492
(5 rows)

```
```
== E per day using bool column + status
     d      |  status  |  n   | budget_exhausted | avg_s | p95_s | credits 
------------+----------+------+------------------+-------+-------+---------
 2026-09-07 | degraded |   14 |                0 |  72.0 | 104.8 |     112
 2026-09-07 | ok       |  324 |                0 |  34.8 |  65.5 |    1090
 2026-09-07 | skipped  | 1368 |                0 |   0.3 |   0.3 |       0
 2026-09-08 | degraded |    1 |                0 | 100.0 | 100.0 |       6
 2026-09-08 | ok       |  503 |                1 |  15.5 |  92.6 |    1206
 2026-09-08 | skipped  | 2095 |                0 |   0.3 |   0.4 |       0
 2026-09-09 | degraded |    2 |                0 | 118.7 | 182.5 |      14
 2026-09-09 | ok       |  562 |                1 |  29.3 | 105.4 |    3171
 2026-09-09 | skipped  | 1931 |                0 |   0.3 |   0.3 |       0
 2026-09-10 | degraded |    4 |                0 |  91.6 | 110.2 |      72
 2026-09-10 | ok       |  558 |                1 |  44.4 | 123.1 |    7085
 2026-09-10 | skipped  | 1658 |                0 |   0.5 |   0.4 |       0
 2026-09-11 | degraded |    4 |                0 |  41.6 |  52.8 |       0
 2026-09-11 | ok       |  414 |                0 |   7.8 |  12.8 |    1075
 2026-09-11 | running  |    1 |                0 |       |       |       0
 2026-09-11 | skipped  |  442 |                0 |   0.1 |   0.3 |       0
(16 rows)

== E notes->kind/stage 24h
 kind | stage | mode | count | bx 
------+-------+------+-------+----
      |       |      |  2198 |  1
(1 row)

== E budget_exhausted by hour last 24h
          h          |  n  | bx 
---------------------+-----+----
 2026-09-10 07:00:00 |  63 |  0
 2026-09-10 08:00:00 | 104 |  1
 2026-09-10 09:00:00 | 106 |  0
 2026-09-10 10:00:00 | 105 |  0
 2026-09-10 11:00:00 | 105 |  0
 2026-09-10 12:00:00 | 107 |  0
 2026-09-10 13:00:00 | 108 |  0
 2026-09-10 14:00:00 | 108 |  0
 2026-09-10 15:00:00 | 108 |  0
 2026-09-10 16:00:00 |  41 |  0
 2026-09-10 17:00:00 |  34 |  0
 2026-09-10 18:00:00 |  40 |  0
 2026-09-10 19:00:00 |  32 |  0
 2026-09-10 20:00:00 |  49 |  0
 2026-09-10 21:00:00 |  57 |  0
 2026-09-10 22:00:00 |  65 |  0
 2026-09-10 23:00:00 | 105 |  0
 2026-09-11 00:00:00 | 109 |  0
 2026-09-11 01:00:00 | 120 |  0
 2026-09-11 02:00:00 | 120 |  0
 2026-09-11 03:00:00 | 113 |  0
 2026-09-11 04:00:00 | 120 |  0
 2026-09-11 05:00:00 | 108 |  0
 2026-09-11 06:00:00 | 113 |  0
 2026-09-11 07:00:00 |  58 |  0
(25 rows)

```
Pricing sub-key (notes->'pricing'): sample of the last two priced ticks and the per-day count of ticks whose pricing.budget_exhausted is true:
```
== E pricing notes sample (last run with non-empty pricing)
  id   |          started_at           |                                                                                                                                                                                                                                                                                                                                                                                                                                                                 left                                                                                                                                                                                                                                                                                                                                                                                                                                                                 
-------+-------------------------------+--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 10450 | 2026-09-11 05:50:27.282252+00 | {"gaps": 724, "order": ["sharp_two_sided", "sharp_direct", "constrained", "nfl_only", "no_velocity", "sharp_plus_derived", "wide_band"], "signals": {"nfl_only": {"rejected": 697, "candidate": 27}, "wide_band": {"rejected": 695, "candidate": 29}, "constrained": {"rejected": 714, "candidate": 10}, "no_velocity": {"rejected": 697, "candidate": 27}, "sharp_direct": {"rejected": 697, "candidate": 27}, "sharp_two_sided": {"rejected": 1394, "candidate": 54}, "sharp_plus_derived": {"rejected": 637, "candidate": 87}}, "budget_s": 45, "no_sharp": 2040, "variant_ms": {"nfl_only": 374, "wide_band": 435, "constrained": 441, "no_velocity": 398, "sharp_direct": 368, "sharp_two_sided": 708, "sharp_plus_derived": 358}, "fair_direct": 234, "fair_errors": 0, "fair_derived": 2327, "variants_run": ["sharp_two_sided", "sharp_direct", "constrained", "nfl_only", "no_velocity", "sharp_plus_derived", "wide_band"]
 10445 | 2026-09-11 05:46:57.282145+00 | {"gaps": 3929, "order": ["sharp_two_sided", "sharp_direct", "constrained", "nfl_only", "no_velocity", "sharp_plus_derived", "wide_band"], "signals": {"nfl_only": {"rejected": 3929, "candidate": 0}, "wide_band": {"rejected": 3852, "candidate": 77}, "constrained": {"rejected": 3920, "candidate": 9}, "no_velocity": {"rejected": 3865, "candidate": 64}, "sharp_direct": {"rejected": 3865, "candidate": 64}, "sharp_two_sided": {"rejected": 7730, "candidate": 128}, "sharp_plus_derived": {"rejected": 3741, "candidate": 188}}, "budget_s": 45, "no_sharp": 2040, "variant_ms": {"nfl_only": 2029, "wide_band": 2224, "constrained": 2111, "no_velocity": 2035, "sharp_direct": 2115, "sharp_two_sided": 4686, "sharp_plus_derived": 2138}, "fair_direct": 234, "fair_errors": 0, "fair_derived": 2327, "variants_run": ["sharp_two_sided", "sharp_direct", "constrained", "nfl_only", "no_velocity", "sharp_plus_derived"
(2 rows)

== E pricing budget_exhausted in notes->pricing, per day
     d      | priced_ticks | pricing_bx | avg_elapsed 
------------+--------------+------------+-------------
 2026-09-07 |          232 |         77 |            
 2026-09-08 |           73 |         36 |            
 2026-09-09 |          281 |         43 |            
 2026-09-10 |          243 |        109 |            
 2026-09-11 |            8 |          0 |            
(5 rows)

```
Reading: pricing ran on 243 ticks on 09-10 and 109 of them (45 %) hit the 45 s pricing budget; 09-09 43/281 (15 %); 09-08 36/73 (49 %); 09-07 77/232 (33 %). The tick-level boolean column budget_exhausted (the Odds API credit budget) is 1/day. Sample 10445 (05:46Z): 3,929 gaps, budget_s 45, variant_ms ~2.0-4.7 s each, wide_band dropped from variants_run; sample 10450 (05:50Z): 724 gaps, 0.4-0.7 s per variant.

## F. Fair values cadence

24 h hourly histogram: timed out (8 s). 6 h hourly histogram: 0 rows (no fair_values rows created 01:30-07:30 CT; the runs table shows only 8 priced ticks on 09-11 so far). 12 h count: timed out.
Most recent cancelled non-replay order: id 7960, venue_market_id 34 (KXNFLGAME-26SEP14DENKC-DEN, game_id 16, moneyline), variant 5632da729fa7, cancelled 2026-09-11 06:49:12Z, fair_stale.
fair_values rows for game_id 16 between 2026-09-10 16:00 and 21:00 CT (created_at, rows per run; first 100 rows shown; index ix_fair_game_type_created):
```
== F fair sequence game 16, 09-10 16:00-21:00 CT
          created_at           | n  
-------------------------------+----
 2026-09-10 21:01:04.165802+00 | 46
 2026-09-10 21:02:54.95234+00  | 46
 2026-09-10 21:05:26.624144+00 | 46
 2026-09-10 21:08:04.521759+00 | 46
 2026-09-10 21:09:57.553017+00 | 46
 2026-09-10 21:13:32.914794+00 | 46
 2026-09-10 21:15:52.75903+00  | 46
 2026-09-10 21:18:17.132631+00 | 46
 2026-09-10 21:20:12.013623+00 | 46
 2026-09-10 21:22:14.069654+00 | 46
 2026-09-10 21:24:38.382684+00 | 46
 2026-09-10 21:26:31.145096+00 | 46
 2026-09-10 21:28:33.510502+00 | 46
 2026-09-10 21:30:48.279785+00 | 46
 2026-09-10 21:33:13.021933+00 | 46
 2026-09-10 21:35:13.450739+00 | 46
 2026-09-10 21:37:14.380063+00 | 46
 2026-09-10 21:39:25.207409+00 | 46
 2026-09-10 21:41:51.462815+00 | 46
 2026-09-10 21:43:52.062959+00 | 46
 2026-09-10 21:45:53.480098+00 | 46
 2026-09-10 21:47:53.580221+00 | 46
 2026-09-10 21:50:18.134739+00 | 46
 2026-09-10 21:52:16.208989+00 | 46
 2026-09-10 21:54:09.809177+00 | 46
 2026-09-10 21:56:12.040515+00 | 46
 2026-09-10 21:59:03.87067+00  | 46
 2026-09-10 22:01:38.551841+00 | 46
 2026-09-10 22:05:30.183146+00 | 46
 2026-09-10 22:07:47.605793+00 | 46
 2026-09-10 22:09:53.196049+00 | 46
 2026-09-10 22:12:17.766438+00 | 46
 2026-09-10 22:14:15.512365+00 | 46
 2026-09-10 22:16:59.414854+00 | 46
 2026-09-10 22:19:56.359552+00 | 46
 2026-09-10 22:23:07.042124+00 | 46
 2026-09-10 22:25:19.755424+00 | 46
 2026-09-10 22:27:24.117479+00 | 46
 2026-09-10 22:29:52.559235+00 | 46
 2026-09-10 22:32:23.645611+00 | 46
 2026-09-10 22:34:14.801231+00 | 46
 2026-09-10 22:36:19.253748+00 | 46
 2026-09-10 22:38:53.281784+00 | 46
 2026-09-10 22:40:57.097574+00 | 46
 2026-09-10 22:42:52.486744+00 | 46
 2026-09-10 22:44:56.662924+00 | 46
 2026-09-10 22:47:26.942363+00 | 46
 2026-09-10 22:49:22.974443+00 | 46
 2026-09-10 22:51:36.437292+00 | 46
 2026-09-10 22:55:03.632839+00 | 46
 2026-09-10 22:56:41.433942+00 | 46
 2026-09-10 22:59:59.468225+00 | 46
 2026-09-10 23:01:00.207073+00 | 46
 2026-09-10 23:02:19.160752+00 | 46
 2026-09-10 23:03:14.907025+00 | 46
 2026-09-10 23:04:48.441985+00 | 46
 2026-09-10 23:06:15.123043+00 | 46
 2026-09-10 23:07:49.438991+00 | 46
 2026-09-10 23:08:42.685088+00 | 46
 2026-09-10 23:10:11.287748+00 | 46
 2026-09-10 23:11:15.615455+00 | 46
 2026-09-10 23:12:55.61385+00  | 46
 2026-09-10 23:13:47.589588+00 | 46
 2026-09-10 23:15:24.251056+00 | 46
 2026-09-10 23:17:21.433683+00 | 46
 2026-09-10 23:18:28.965818+00 | 46
 2026-09-10 23:20:23.016624+00 | 46
 2026-09-10 23:22:25.275418+00 | 46
 2026-09-10 23:25:29.774997+00 | 46
 2026-09-10 23:26:22.178566+00 | 46
 2026-09-10 23:27:51.360557+00 | 46
 2026-09-10 23:28:50.216929+00 | 46
 2026-09-10 23:30:17.892201+00 | 46
 2026-09-10 23:31:15.018779+00 | 46
 2026-09-10 23:32:50.512208+00 | 46
 2026-09-10 23:33:49.215527+00 | 46
 2026-09-10 23:35:22.459848+00 | 46
 2026-09-10 23:37:28.313834+00 | 46
 2026-09-10 23:39:50.715768+00 | 46
 2026-09-10 23:41:50.28544+00  | 46
 2026-09-10 23:43:49.022524+00 | 46
 2026-09-10 23:45:56.960913+00 | 46
 2026-09-10 23:48:54.093297+00 | 46
 2026-09-10 23:51:24.798409+00 | 46
 2026-09-10 23:53:50.472486+00 | 46
 2026-09-10 23:55:54.101249+00 | 46
 2026-09-10 23:59:30.633163+00 | 46
 2026-09-11 00:02:00.434107+00 | 46
 2026-09-11 00:04:27.322874+00 | 46
 2026-09-11 00:07:34.144172+00 | 46
 2026-09-11 00:09:58.767087+00 | 46
 2026-09-11 00:12:26.350988+00 | 46
 2026-09-11 00:16:02.492094+00 | 46
 2026-09-11 00:17:58.387301+00 | 46
 2026-09-11 00:20:37.610682+00 | 46
 2026-09-11 00:24:40.512153+00 | 46
 2026-09-11 00:26:56.235387+00 | 46
 2026-09-11 00:29:28.643944+00 | 46
 2026-09-11 00:32:14.232491+00 | 46
 2026-09-11 00:34:58.143158+00 | 46
(100 rows)

```
Reading: on Thu evening the game-16 fair values refreshed every 1-3.5 min (median gap ~2 min, 46 rows per run), not every 15 min. fair_values indexes: pkey, ix_fair_game_type_created (game_id, market_type, created_at), uq_fair_value_row, ix_fair_created_brin (autosummarize on), ix_fair_leg_lookup partial on fair_source='direct'.

## G. Orders and cancel policy

```
== G fair_stale percentiles
  n   | tib_p25 | tib_p50 | tib_p75 | tib_p95 | stale_p25 | stale_p50 | stale_p75 | stale_p95 | allow_p25 | allow_p50 | allow_p75 | allow_p95 
------+---------+---------+---------+---------+-----------+-----------+-----------+-----------+-----------+-----------+-----------+-----------
 7825 |     5.0 |    12.2 |    35.3 |   147.0 |      57.0 |      67.0 |      77.0 |      94.0 |     220.0 |     220.0 |     220.0 |     220.0
(1 row)

== G fair_stale time-in-book by day
     d      |  n   | p25 | p50  | p75  |  p95  | avg_stale | avg_allow 
------------+------+-----+------+------+-------+-----------+-----------
 2026-09-08 | 1253 | 1.3 |  4.8 | 62.0 | 223.1 |        65 |       220
 2026-09-09 | 2564 | 1.5 | 12.2 | 33.7 | 134.9 |        68 |       220
 2026-09-10 | 3720 | 5.5 | 15.5 | 36.8 |  97.5 |        71 |       220
 2026-09-11 |  288 | 5.3 | 10.0 | 15.2 |  50.5 |        56 |       220
(4 rows)

== G feed_kind / book_source
 feed_kind | book_source | count 
-----------+-------------+-------
 featured  | ws          |  7959
 featured  | rest        |    38
(2 rows)

== G distinct (variant, market, side)
 count 
-------
   445
(1 row)

 count 
-------
    27
(1 row)

== G per variant distance from mid
  variant_id  |  n   | mid_minus_price |  edge  | queue_ahead | contracts | avg_price 
--------------+------+-----------------+--------+-------------+-----------+-----------
 5632da729fa7 | 4955 |          0.0081 | 0.0424 |     15336.3 |     110.4 |     0.457
 f259ca109084 | 2651 |          0.0456 | 0.0422 |      8524.0 |     109.6 |     0.452
 ff363c8ac08d |  391 |          0.0543 | 0.0473 |      5353.4 |     137.3 |     0.369
(3 rows)

== G by sport and ttk bucket
 sport |   b    | count 
-------+--------+-------
 ncaaf | 24-72h |  3903
 ncaaf | 6-24h  |    56
 ncaaf | >72h   |  1852
 nfl   | 1-6h   |    38
 nfl   | <1h    |     9
 nfl   | 24-72h |   660
 nfl   | 6-24h  |    32
 nfl   | >72h   |  1447
(8 rows)

```
(tib = minutes in book; stale = staleness_at_place in seconds; allow = stale_allowance_at_place in seconds. fair_stale n = 7,825. stale_allowance_at_place is 220 s on every order (p25 = p95 = 220). ttk bucket: hours from placed_at to kickoff_utc.)

Order 157 (the one real queue_model fill), its fills and its first 20 order_watch_samples:
```
== G order 157
 id  |              intent_id               |  variant_id  | venue  | mode  |               client_order_id                |             ticker             | venue_market_id | side |  prob  | contracts |  status   |           placed_at           |         expiry         |         cancelled_at          | cancel_reason | fair_p_at_place | fair_row_id_at_place | fair_books_json | venue_bid_at_place | venue_ask_at_place | venue_mid_at_place | queue_ahead_at_place | book_source | book_age_s |      book_first_seen_at       | edge_at_place | edge_min_at_place | as_at_place | staleness_at_place | stale_allowance_at_place | feed_kind |                           config_hash                            | gap_snapshot_id | game_id | sport |      kickoff_utc       |      match_key       | worst_case_fill | fair_cross_fill | filled_contracts | queue_remaining | traded_at_price | tape_cursor_event_id | nw_filled_contracts | nw_queue_remaining | nw_traded_at_price | nw_tape_cursor_event_id | nw_done | dirty_minutes | replay | crossed |       last_print_ts        |                                                                                                                  last_print_ids                                                                                                                  | nw_crossed |      nw_last_print_ts      |            nw_last_print_ids             | dirty_seconds | venue_order_id | order_group_id | exchange_index_at_place 
-----+--------------------------------------+--------------+--------+-------+----------------------------------------------+--------------------------------+-----------------+------+--------+-----------+-----------+-------------------------------+------------------------+-------------------------------+---------------+-----------------+----------------------+-----------------+--------------------+--------------------+--------------------+----------------------+-------------+------------+-------------------------------+---------------+-------------------+-------------+--------------------+--------------------------+-----------+------------------------------------------------------------------+-----------------+---------+-------+------------------------+----------------------+-----------------+-----------------+------------------+-----------------+-----------------+----------------------+---------------------+--------------------+--------------------+-------------------------+---------+---------------+--------+---------+----------------------------+--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+------------+----------------------------+------------------------------------------+---------------+----------------+----------------+-------------------------
 157 | 4c13c99f-d937-449d-be33-476dfb33c934 | f259ca109084 | kalshi | paper | paper-4c13c99f-d937-449d-be33-476dfb33c934-0 | KXNCAAFTOTAL-26SEP12MTUMRSH-59 |            3031 | yes  | 0.4500 |     87.00 | cancelled | 2026-09-08 14:36:47.579399+00 | 2026-09-12 22:50:00+00 | 2026-09-08 15:12:06.083229+00 | fair_stale    |          0.4928 |               759603 |                 |             0.4800 |             0.4900 |             0.4850 |              6401.00 | ws          |          0 | 2026-09-08 14:36:47.579399+00 |        0.0385 |            0.0283 |      0.0100 |                 53 |                      220 | featured  | 15a491be8fad9588d9d5465d5a159a6cef8b32642afea11680ba71d36c5d176b |          942134 |     408 | ncaaf | 2026-09-12 23:00:00+00 | 408:total::over:58.5 | t               | f               |            38.92 |            0.00 |           63.92 |             30267634 |               87.00 |               0.00 |              88.92 |                48292304 | t       |          3020 | f      | f       | 2026-09-08 15:07:15.332+00 | ["0722873b-b281-98eb-094c-0bbd56d80b2c", "07228743-b411-9fca-1815-8b1bcad3d9a9", "0722874c-2f41-9344-1f2c-89863ccf6931", "0722874c-c981-9ee3-3838-04e41bf3bac7", "072287b2-3701-901e-2c93-d2aceca99fe4", "072287b2-f681-9d22-2be2-53e3c979cd65"] | t          | 2026-09-10 19:29:46.141+00 | ["0722bf05-8b31-9c41-dd93-ac0c0eed0fa6"] |        181200 |                |                |                        
(1 row)

 id  | order_id |  prob  | contracts |  fee   |         fee_type          | fee_multiplier | maker_rate |           filled_at           | simulated |  fill_method   |           source_trade_id            | source_event_id | taker_side | through | tape_source | has_print | replay 
-----+----------+--------+-----------+--------+---------------------------+----------------+------------+-------------------------------+-----------+----------------+--------------------------------------+-----------------+------------+---------+-------------+-----------+--------
   3 |      157 | 0.4500 |     25.00 | 0.1083 | quadratic_with_maker_fees |         1.0000 |     0.0175 | 2026-09-08 15:07:15.332+00    | t         | queue_model    | 0722873b-b281-98eb-094c-0bbd56d80b2c |                 | no         | f       | ws          | t         | f
   4 |      157 | 0.4500 |     13.92 | 0.0603 | quadratic_with_maker_fees |         1.0000 |     0.0175 | 2026-09-08 15:07:15.332+00    | t         | queue_model    | 0722874c-c981-9ee3-3838-04e41bf3bac7 |                 | no         | f       | ws          | t         | f
   5 |      157 | 0.4500 |     25.00 | 0.1083 | quadratic_with_maker_fees |         1.0000 |     0.0175 | 2026-09-08 15:07:15.332+00    | t         | no_watcher     | 0722873b-b281-98eb-094c-0bbd56d80b2c |                 | no         | f       | ws          | t         | f
   6 |      157 | 0.4500 |     13.92 | 0.0603 | quadratic_with_maker_fees |         1.0000 |     0.0175 | 2026-09-08 15:07:15.332+00    | t         | no_watcher     | 0722874c-c981-9ee3-3838-04e41bf3bac7 |                 | no         | f       | ws          | t         | f
 150 |      157 | 0.4500 |     25.00 | 0.1083 | quadratic_with_maker_fees |         1.0000 |     0.0175 | 2026-09-08 17:00:20.728+00    | t         | no_watcher     | 0722864b-4d41-9496-63d6-bb99a17e5e56 |                 | no         | f       | rest        | t         | f
 151 |      157 | 0.4500 |     23.08 | 0.1000 | quadratic_with_maker_fees |         1.0000 |     0.0175 | 2026-09-08 17:00:20.728+00    | t         | no_watcher     | 0722864b-ec99-97bb-6966-00ea0abe641c |                 | no         | t       | rest        | t         | f
 152 |      157 | 0.4500 |     48.08 | 0.2083 | quadratic_with_maker_fees |         1.0000 |     0.0175 | 2026-09-10 19:42:20.164988+00 | t         | snapshot_cross |                                      |        48292304 |            | f       | ws          | t         | f
(7 rows)

                       Table "public.order_watch_samples"
       Column       |           Type           | Collation | Nullable | Default 
--------------------+--------------------------+-----------+----------+---------
 order_id           | bigint                   |           | not null | 
 ts                 | timestamp with time zone |           | not null | 
 queue_remaining    | numeric(14,2)            |           |          | 
 nw_queue_remaining | numeric(14,2)            |           |          | 
 best_bid           | numeric(6,4)             |           |          | 
 best_ask           | numeric(6,4)             |           |          | 
 fair_p             | numeric(6,4)             |           |          | 
 book_dirty         | boolean                  |           | not null | 
 terminal           | character varying(12)    |           |          | 
Indexes:
    "order_watch_samples_pkey" PRIMARY KEY, btree (order_id, ts)

 order_id |              ts               | queue_remaining | nw_queue_remaining | best_bid | best_ask | fair_p | book_dirty | terminal 
----------+-------------------------------+-----------------+--------------------+----------+----------+--------+------------+----------
      157 | 2026-09-08 14:37:02.579549+00 |         6401.00 |            6401.00 |   0.4800 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:38:17.57797+00  |         6401.00 |            6401.00 |   0.4800 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:39:17.579417+00 |         6401.00 |            6401.00 |   0.4800 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:40:17.578343+00 |         6401.00 |            6401.00 |   0.4800 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:41:32.579761+00 |         6401.00 |            6401.00 |   0.4800 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:42:32.579164+00 |         6401.00 |            6401.00 |   0.4800 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:43:32.579117+00 |         6401.00 |            6401.00 |   0.4800 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:44:47.579665+00 |         6401.00 |            6401.00 |   0.4800 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:45:47.576972+00 |         6401.00 |            6401.00 |   0.4800 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:47:02.578216+00 |         6401.00 |            6401.00 |   0.4800 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:48:02.579629+00 |         6401.00 |            6401.00 |   0.4800 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:49:02.579031+00 |         6401.00 |            6401.00 |   0.4800 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:50:02.579385+00 |         6401.00 |            6401.00 |   0.4700 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:51:02.577497+00 |         6401.00 |            6401.00 |   0.4700 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:52:02.578447+00 |         6401.00 |            6401.00 |   0.4700 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:53:17.579052+00 |         6401.00 |            6401.00 |   0.4700 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:54:17.579636+00 |         6401.00 |            6401.00 |   0.4700 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:55:17.579397+00 |         6401.00 |            6401.00 |   0.4700 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:56:32.578993+00 |         6401.00 |            6401.00 |   0.4700 |   0.4900 | 0.4928 | t          | 
      157 | 2026-09-08 14:57:32.579269+00 |         6401.00 |            6401.00 |   0.4700 |   0.4900 | 0.4928 | t          | 
(20 rows)

```

## H. Signals, sharp_direct, LAR-SF pre-kick window (2026-09-10 23:00Z to 2026-09-11 01:00Z)

With variant_id='sharp_direct' (the name): 0 rows. With variant_id='f259ca109084' (the hash, as stored):
```
== H sharp_direct hash window 23:00-01:00Z
 decision  | rejection_reason | count 
-----------+------------------+-------
 rejected  | source_allowed   | 19203
 rejected  | has_fair         | 12983
 candidate |                  |   930
 rejected  | price_band       |   354
 rejected  | disagreement_ok  |   303
 rejected  | not_stale        |   298
 rejected  | volume           |    25
 rejected  | ttk              |     4
(8 rows)

```
(The preceding 30-minute variant_id histogram timed out.) Reading: 34,100 signal rows for one variant in 2 h; 930 candidates (2.7 %); 56 % rejected on source_allowed, 38 % on has_fair.

## I. Research and reports

report_runs columns: id, year, week, generated_at, provisional, build_sha, criteria_hash, config_hashes, markdown, markdown_sha256. Last 8 rows (markdown omitted):
```
== I report_runs last 8
 id | year | week |         generated_at          | provisional | build_sha |                          criteria_hash                           |                                                                                                                                                                                                      config_hashes                                                                                                                                                                                                       |                         markdown_sha256                          | md_len 
----+------+------+-------------------------------+-------------+-----------+------------------------------------------------------------------+--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+------------------------------------------------------------------+--------
  9 | 2026 |   37 | 2026-09-10 10:25:41.887694+00 | t           | f851128   | 5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5 | [<n config hashes>] |                                                                  |       
  8 | 2026 |   37 | 2026-09-09 20:49:29.04087+00  | t           | 4c554ea   | 5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5 | [<n config hashes>] |                                                                  |       
  7 | 2026 |   37 | 2026-09-09 13:52:44.314097+00 | t           | 6e3b33f   | 5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5 | [<n config hashes>] |                                                                  |       
  6 | 2026 |   37 | 2026-09-09 06:52:44.540548+00 | t           | 6e3b33f   | 5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5 | [<n config hashes>] |                                                                  |       
  5 | 2026 |   37 | 2026-09-08 22:46:15.795979+00 | t           | a193fd0   | 5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5 | [<n config hashes>]                                                                                                                                                                                                             |                                                                  |       
  4 | 2026 |   37 | 2026-09-08 16:56:09.722823+00 | f           | a193fd0   | 5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5 | [<n config hashes>]                                                                                                                                                                                                             | de799f03b0d5573e0f3a438805f132f98467fb575aeb0912c94eb94cd1b7988e |  73217
  3 | 2026 |   37 | 2026-09-08 16:03:36.023315+00 | t           | 1911a4f   | 5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5 | [<n config hashes>]                                                                                                                                                                                                             |                                                                  |       
  2 | 2026 |   37 | 2026-09-08 14:24:15.78345+00  | f           | 33a0e3a   | 5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5 | [<n config hashes>]                                                                                                                                                                                                                                                                                 | ebc1fb755a58cf2e3697f4e058ae78a6a6609c3147bf703abf952b09eee57932 |  72218
(8 rows)

```
Only rows 2 and 4 (2026-09-08) have markdown (72,218 and 73,217 chars); rows 3, 5-9 are provisional with empty markdown. All 9 are year 2026 week 37, same criteria_hash. Row 9 lists 6 config hashes, rows 2-4 list 2-3.
check_results (columns id, job_run_id, ts, check_name, status, value, threshold, detail), latest per check (all at 2026-09-11 09:15:53Z = 04:15 CT):
```
== I check_results latest per check
              check_name               |              ts               | status |   value    |      threshold      |  left   
---------------------------------------+-------------------------------+--------+------------+---------------------+---------
 benchmarks_source_after_target        | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 build_sha_drift                       | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 check_results_unknown_status_25h      | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 clv_p_used_matches_order_prob         | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 derived_without_venue_row_48h         | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 duplicate_trades                      | 2026-09-11 09:15:53.532981+00 | skip   |            | == 0                | timeout
 equity_mtm_coverage_out_of_range_24h  | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 fair_values_negative_feed_lag         | 2026-09-11 09:15:53.532981+00 | skip   |            | == 0                | timeout
 fair_values_negative_staleness        | 2026-09-11 09:15:53.532981+00 | skip   |            | == 0                | timeout
 fill_contracts_exceed_order_contracts | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 fills_outside_placement_window        | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 fills_without_print                   | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 game_score_went_down_24h              | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 gate_rows_one_gate_variant            | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 1 per evaluation | 
 intents_without_order_or_skip         | 2026-09-11 09:15:53.532981+00 | fail   | 483.000000 | == 0                | 
 markouts_at_after_horizon             | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 metric_samples_negative_24h           | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 operator_events_empty_summary_24h     | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 orders_filled_exceeds_contracts       | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 orders_open_past_expiry               | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 orders_without_place_event            | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 order_watch_negative_queue_24h        | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 report_cells_orphan                   | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 report_runs_generated_in_future       | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 runs_taker_side_missing_24h           | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 settle_errors_24h                     | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
 settlement_result_mismatch            | 2026-09-11 09:15:53.532981+00 | pass   |   0.000000 | == 0                | 
(27 rows)

```
Reading: 27 checks; 23 pass; 1 fail: intents_without_order_or_skip = 483 (threshold == 0); 3 skip on 'timeout': duplicate_trades, fair_values_negative_feed_lag, fair_values_negative_staleness.
```
== I rfq_quotes declined
 declined_reason | count 
-----------------+-------
 no_fair         |  7739
 same_game       |   381
 single_leg      |     1
(3 rows)

    day     |   kind   |     model     | calls | input_tokens | output_tokens | cache_read_tokens | cache_write_tokens | searches | usd_reserved |  usd   
------------+----------+---------------+-------+--------------+---------------+-------------------+--------------------+----------+--------------+--------
 2026-09-11 | annotate | claude-opus-5 |     6 |        93539 |          2048 |                 0 |                  0 |        0 |       0.0000 | 0.5189
(1 row)

 veto 
------
    0
(1 row)

 veto_queue 
------------
          0
(1 row)

ERROR:  canceling statement due to statement timeout
 weather_snapshots 
-------------------
               574
(1 row)

 weather_points 
----------------
             93
(1 row)

 futures 
---------
       0
(1 row)

 research_notes 
----------------
              6
(1 row)

 report_annotations 
--------------------
                  0
(1 row)

 gate_reports 
--------------
            0
(1 row)

```
rfq_quotes declines: no_fair 7,739; same_game 381; single_leg 1 (total 8,121). count(*) from rfqs: timed out (pg_stat says 93,470 live tuples, 159 MB). research_spend: 1 row, 2026-09-11 annotate claude-opus-5, 6 calls, 93,539 in / 2,048 out, 0 cache tokens, usd 0.5189. veto_decisions 0; veto_queue 0; weather_snapshots 574; weather_points 93; futures_snapshots 0; research_notes 6; report_annotations 0; gate_reports 0.
strategy_variants: 8 rows (7 registered 2026-09-07 03:58Z, sharp_two_sided 09-08 06:30Z, sharp_two_sided#e82fcd0a1e99 replay tier 09-08 09:58Z). apply_caps is true ONLY for constrained (ff363c8ac08d); false for sharp_direct, sharp_two_sided, nfl_only, no_velocity, sharp_plus_derived, wide_band. All: bankroll 3000, max_open 25, per_bet_cap 0.03, per_game_cap 0.05, daily_cap 0.15, kelly 0.25, stale_s 180, edge 0.02-0.06.
equity_snapshots (12:25Z): sharp_direct cash 2982.32, open_stake 17.51, mtm_open null, mtm_coverage 0, 1 open position, 0 open orders, drawdown -0.0059; constrained and sharp_two_sided 3000.00 flat.

## J. Tape volume

orderbook_events, last 1 h: 145,327 rows (~2,420/min; ~3.5 M/day at the night rate). venue_trades, last 24 h: 494,386 rows. gap_outcomes 3,840; trade_watermarks 3,366. No table matching *gap* for Kalshi tape gaps besides gap_outcomes/market_gap_snapshots (ws.gaps metric avg 0, recorder.trade_gaps 0 in the last 6 h). ws.events_per_min avg 1,933 max 6,610 (6 h); ws.subscribed_tickers 500; ws.reconnects max 5; ws.sink_lag_s avg 1.1 max 208.

## K. Odds API / healthz

```
== K healthz
{"status":"ok","last_run_at":"2026-09-11T12:30:42.110827+00:00","last_status":"ok","seconds_since":11,"credits_remaining":4986353,"credits_budget":5000000,"credits_low":false,"venue_limits":{"tier":"basic","age_s":3090.0,"read_at":"2026-09-11T11:39:12.110888+00:00","page_pause_s":0.05,"read_capacity":600.0,"write_capacity":100.0,"read_refill_rate":200.0,"write_refill_rate":100.0},"build":"7c3d555"}

```

## L. Container logs (last 200 lines each, ERROR/WARNING message field, counts)

```
== L app-exec (message field of ERROR/WARNING, last 200 lines)
      2 apscheduler.scheduler | Execution of job \
== L app-run (message field of ERROR/WARNING, last 200 lines)
== L app-serve (message field of ERROR/WARNING, last 200 lines)
== L app-ws (message field of ERROR/WARNING, last 200 lines)
      4 harness.venues.kalshi.rfq_socket | rfq listener: quote rate limit engaged (500 compute_quote calls in the last 60s); rfq_created frames store only unt
      1 harness.venues.kalshi.rfq_socket | rfq listener error frame, not an idling code: {\
      1 harness.execution.venue | venue kalshi_rfq/prod -> ok; untrusted venue text: reason=None
== L app-research (message field of ERROR/WARNING, last 200 lines)
      2 harness.report.render_for_model | render_from_cells: table t7's identity column is unrecoverable; omitting the table rather than risk printing its row
      2 harness.report.render_for_model | render_from_cells: table t10's identity column is unrecoverable; omitting the table rather than risk printing its ro
      1 harness.research.annotate | annotator failed for report 4
      1 harness.research.annotate | annotator failed for report 2
```
Line totals in the 200-line window: app-exec 200, app-run 200, app-serve 200, app-ws 14 (container restarted 48 min earlier), app-research 117. app-run and app-serve: zero ERROR/WARNING lines. app-exec: 2 apscheduler 'Execution of job ... skipped: maximum number of running instances reached' style warnings (message truncated at the escaped quote). app-ws: 4x 'rfq listener: quote rate limit engaged (500 compute_quote calls in the last 60s)', 1 rfq error frame, 1 venue kalshi_rfq/prod -> ok. app-research: 2x annotator failed (reports 2 and 4), 4x render_from_cells 'table t7/t10 identity column is unrecoverable; omitting the table'.

## Controller notes (timeouts, contradictions, surprises)

Timed out (8 s) and not retried wider: D5 with a 2-day bound (rerun at 6 h succeeded); F hourly fair_values over 24 h; F fair_values count over 12 h; H 30-minute per-variant histogram on signals; I `select count(*) from rfqs`. Section E "kind/stage" column: n/a (runs has no such column; notes has no kind/stage/mode keys).

Contradictions with the brief:
1. Pricing cadence. The brief and the phase 4.5 report describe a "15-minute pricing cadence"; the fair_values sequence for game 16 (DEN-KC) on Thu evening shows a new run every 1-3.5 min (median ~2 min). The fair_stale rule fires at max(stale_s 180, stale_allowance 220) = 220 s of fair age, and every one of the 7,825 fair_stale orders carried stale_allowance_at_place = 220 exactly, staleness_at_place p50 67 s. Median time in book 12.2 min therefore means fair values were refreshing several times while the order rested and the cancel came when a refresh was late by more than ~2.5 min (an executor loop of 100+ s, or a tick with no pricing), not on a fixed 15-min clock. Fair values rows are absent from 01:30 to 07:30 CT today (only 8 priced ticks on 09-11), so overnight orders have nothing to rest on.
2. Pricing budget exhaustion is far above the phase 6 trigger (>= 10 % of game-day ticks): pricing.budget_exhausted true on 109 of 243 priced ticks on 09-10 (45 %), 43/281 on 09-09 (15 %), 36/73 on 09-08 (49 %). price_budget_s reads 45 in the notes. A tick at 05:46Z priced 3,929 gaps at 2.0-4.7 s per variant and dropped wide_band. The tick-level boolean budget_exhausted (Odds API credits) is 1/day and is a different thing; a naive `notes::text like '%budget_exhausted%'` counts the key, not the value (492/860 today), which would overstate it.
3. Executor loop: game-night hours on 09-10 were far worse than the brief's "27.5 s avg / 118 s p95 in the 19:00 hour": 18:00 avg 18.3 s p95 101 s; 19:00 avg 27.5 s p95 115 s; 20:00 avg 20.8 s p95 72.6 s max 256 s; 21:00 avg 16.1 s p95 52.5 s max 190 s; 22:00 avg 7.9 s p95 13.2 s; 23:00 avg 6.5 s p95 9.0 s. Daily: 09-08 avg 4.65 s p95 16.2 s; 09-09 4.13 s / 6.19 s; 09-10 8.30 s / 15.2 s; 09-11 (to 07:30) 9.72 s / 10.0 s max 336 s. Against the 7.5 s p95 ceiling only 09-09 passed. exec_heartbeat loops_skipped 521 of 17,138.
4. The 150-order cap (exec_max_open_orders) was the binding constraint in 41 of the 53 hours with any open orders over the last 3 days (max = 150), including every hour from 08:00 CT 09-10 through 00:00 CT 09-11. In the 21:00 and 22:00 hours on 09-08 and 09-10 the hourly average was exactly 150.0.
5. exec.filled_contracts summed over 3 days = 38.92 (the single order 157 fill, 10:00 CT hour 09-08); zero since.
6. Hardware: pgdata is on the rotational HDD (sda -> md1 RAID1 -> LVM /volume1), not the NVMe; random_page_cost is 1.1 (SSD value); effective_io_concurrency 1; wal_compression off; no container has a memory or CPU limit. Postgres cumulative block I/O: 1.89 TB read, 653 GB written in 3 days. Swap resident 1.87 GB (zram + a 2 GB NVMe partition).
7. check_results: intents_without_order_or_skip FAILS at 483 (threshold 0); three integrity checks (duplicate_trades, fair_values_negative_feed_lag, fair_values_negative_staleness) are skipped on 'timeout' at 04:15 CT, i.e. the harness's own checks cannot scan its tables in their budget.
8. report_runs: 9 rows for week 37, all but rows 2 and 4 provisional with empty markdown; report_annotations 0; the annotator failed on reports 2 and 4 in the last app-research log window (fix 41 in flight per the brief); render_from_cells drops tables t7 and t10 as "identity column unrecoverable" (2 occurrences each).
9. Orders at placement by hours-to-kickoff: ncaaf 24-72h 3,903, >72h 1,852, 6-24h 56; nfl >72h 1,447, 24-72h 660, 6-24h 32, 1-6h 38, <1h 9. 7,388 of 7,997 orders (92 %) were placed more than 24 h before kickoff. Book source: ws 7,959, rest 38; feed_kind all featured.
10. Distinct (variant, market, side) across 7,997 orders = 445; distinct with a no_watcher fill = 27. sharp_two_sided posts 0.8 c inside the venue mid on average (0.0081), sharp_direct 4.6 c, constrained 5.4 c; avg queue_ahead 15,336 / 8,524 / 5,353 contracts; avg order 110 / 110 / 137 contracts.
11. Signals for one variant in the 2 h LAR-SF window: 34,100 rows (930 candidates); signals table 14.6 M rows / 8.8 GB.
12. orderbook_events 145,327 rows in the last (night) hour; db.growth_gb_per_day 8.94.
13. The rfq listener hit its own quote rate limit (500 compute_quote/60 s) 4 times in the 14 lines since the 06:40 CT restart; rfq_quotes are 100 % declined (no_fair 7,739, same_game 381, single_leg 1).
14. app-run and app-serve logs are clean (0 ERROR/WARNING in 200 lines). Odds API healthz ok, credits_remaining 4,986,353 / 5,000,000, venue tier basic, venue_limits age 3,090 s.
