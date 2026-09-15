# Storage retention proposal (journal 224 item 11; decision by 2026-09-22; nothing executed)

Written 2026-09-15 08:06 CT by the controller from live measurements (`evidence/2026-09-15-storage-by-table-0803.txt`).
Invariant 5 is unchanged: this document proposes; only the user's dated yes executes anything, and the loop never runs a
DROP, TRUNCATE, DELETE, partition detach or retention job. Numbers are bytes from `pg_total_relation_size` at 08:0x CT (the evidence file's own timestamps govern).

## Where the space is

| Table family | Total | Table | Indexes | Rows (est.) | Share of DB |
|---|---|---|---|---|---|
| `orderbook_events` (tape: legacy + w37 + w38 + w39) | 105.6 GB | 66.3 GB | 39.3 GB | 177.9 M | 75.7 % |
| `signals` | 13.3 GB | 10.3 GB | 3.0 GB | 19.3 M | 9.5 % |
| `odds_snapshots` | 7.0 GB | 3.1 GB | 3.8 GB | 25.0 M | 5.0 % |
| `raw_responses` (w37 + w38 + w39) | 4.1 GB | 4.0 GB | 0.1 GB | 0.68 M | 3.0 % |
| `fair_values` | 3.1 GB | 2.6 GB | 0.5 GB | 4.4 M | 2.2 % |
| `venue_trades` (legacy + w37 + w38 + w39) | 2.5 GB | 0.9 GB | 1.6 GB | 5.7 M | 1.8 % |
| `venue_quotes` | 1.7 GB | 0.8 GB | 0.9 GB | 5.5 M | 1.2 % |
| `market_gap_snapshots` | 0.9 GB | 0.6 GB | 0.3 GB | 3.0 M | 0.7 % |
| everything else (orders, rfqs, snapshots, intents, metrics, ...) | ~1.4 GB | | | | 1.0 % |
| **database** | **139.6 GB (130 GiB)** | | | | |

The tape partitions, the only thing that moves the total:

| Partition | Total | of which indexes | Rows | State |
|---|---|---|---|---|
| `orderbook_events_legacy` (pre-partitioning, to 2026-09-07 00:34Z) | 21.9 GB | 5.4 GB (`ticker_id` 1.9, `ticker_ts` 2.6, pkey 0.9) | 29.9 M | sealed; no partition archive (predates the Monday job) |
| `orderbook_events_y2026w37` (Sep 7-13, the first two game weekends) | 84.3 GB | 32.1 GB (`ticker_id_idx` 15.6, `ticker_ts_idx` 12.1, pkey 4.4) | 139.8 M | sealed; **archived 2026-09-14 14:22-14:30 CT**: `harness-partitions-orderbook_events_y2026w37-20260914T192207Z.dump` 4.40 GB (zstd custom format), `.dump.age` 4.40 GB, `.meta.json`, `.ok` marker; `backup_runs` `partition ok` |
| `orderbook_events_y2026w38` (Sep 14-20, so far Mon-Tue, no game) | 4.8 GB | 1.8 GB | 8.2 M | open |
| `venue_trades_legacy` | 0.4 GB | 0.07 GB | 0.87 M | sealed; no archive |
| `venue_trades_y2026w37` | 1.9 GB | 1.2 GB | 4.3 M | sealed; **archived 2026-09-14 14:27 CT** (`.dump` 134 MB, `.dump.age`, `.ok`) |
| `raw_responses_y2026w37` | 3.9 GB | 0.05 GB | 0.65 M | sealed; the Monday job archives only `orderbook_events` and `venue_trades` (phase 4 spec); the nightly dump excludes bulk data |

## How fast it grows

`db.size_gb` from housekeeping: 46.3 (Sep 11) -> 62.4 (Sep 12) -> 99.5 (Sep 13) -> 121.9 (Sep 14) -> 129.9 GB (Sep 15).
`db.growth_gb_per_day` (trailing): 8.9, 10.7, 16.1, 16.9, 15.8. The growth is the weekend tape: Sat Sep 12 and Sun Sep 13
added about 37 GB and 22 GB; Mon-Tue of week 38 added 8 GB together (`orderbook_events_y2026w38` is 4.8 GB after ~1.5 quiet
days). A game week therefore costs roughly 85-95 GB (w37: 84 GB of tape plus signals/odds/fair growth), about 13 GB/day
blended, of which about 38 % is index.

Projection against the two limits the roadmap preserves:

| Limit | Headroom now | At 13 GB/day blended | At 16 GB/day (trailing) |
|---|---|---|---|
| 600 GB capacity alert budget (dashboard red at 480 GB) | 470 GB to 600; 350 GB to 480 | 600 GB about **Oct 21**; 480 GB about Oct 12 | 600 GB about **Oct 14**; 480 GB about Oct 7 |
| 25 % free on `/srv/sports-harness` (952 GB filesystem; 799 GB free at 16 % used; the gate is 238 GB free) | ~560 GB (shared with `backups/` 24 GB, images, the test cluster) | about **Oct 28** | about Oct 20 |

So with no action the 480 GB red band arrives in the second week of October and the 600 GB budget in the third, before
or at the mid-October go-live gate review; the filesystem gate about a week after that. Nothing is urgent this week; a
decision by 2026-09-22 leaves four weeks of slack.

## What the spec and the milestones need kept

- Ledger, fills, orders, settlements, gate reports: 7 years (spec §5 `ledger(...)`, §14 backups). Tiny; not at issue.
- The tape (`orderbook_events`, `venue_trades`) is the evidence for 6A's capsule, 6B's replay/re-score/audit and 6D's
  coverage contract; 6F's valid prospective period is measured from the recorded version boundary (c1066b5, 2026-09-15
  05:23:44Z), so **week 38 onward is the prospective tape** and weeks 36-37 are the pre-repair record. Order 157's capsule
  is committed separately (`docs/superpowers/reviews/2026-09-11-phase6-roadmap/capsule/order-157`).
- The phase 4 design (§4.2) already defines the archive unit: a sealed weekly partition is dumped once (zstd, custom format),
  encrypted with the age recipient, structure-checked, marked `.ok`, and its plaintext is deleted only after an off-host
  decrypt drill for the same build. The decrypt drills on record (`backup_runs` `drill ok` 2026-09-09 and 2026-09-12) cover
  the nightly kind on the Mac; **no drill row exists for a partition unit yet**, and whether the NAS pull
  (`sports-backup-publish.timer`, 15 min) has copied the w37 archives off-host is not observable from this host.
- U3 (user, 2026-09-07): archiving or dropping a sealed partition happens only on the user's explicit yes, one partition at a
  time; the loop proposes. Roadmap 6E decision 7: the proposal is written in 6E; no deletion.

## Options, sized (each is the user's; none is executed by the loop)

| # | Option | Frees now | Frees per game week | Cost / risk | Reversal |
|---|---|---|---|---|---|
| 0 | Nothing until the 480 GB band | 0 | 0 | red dashboard ~Oct 7-12; 600 GB budget ~Oct 14-21; the filesystem gate about a week later | n/a |
| 1 | **Drop the archived w37 tape partitions after an off-host decrypt drill of their `.dump.age`** (`orderbook_events_y2026w37`, `venue_trades_y2026w37`): `ALTER TABLE ... DETACH PARTITION` then `DROP TABLE`, one statement each, in a quiet hour, with the restore drill (`deploy/backup/drill.sh`) proven first | **86 GB** | 85-95 GB (each sealed week, one week after its Monday archive) | pre-repair (weeks 36-37) live tape becomes restore-only: a 6B re-audit of a pre-boundary order would need `pg_restore` into a scratch cluster (the drill path); 6C/6D reads that join the tape by week must tolerate a missing partition (they read by `ts`; a detached partition is simply absent). Gate 3 and U3: the user executes | restore the archive into the same cluster and `ATTACH PARTITION` (metadata-only, pre-authorized shape) |
| 2 | Same as 1 for `orderbook_events_legacy` and `venue_trades_legacy` (Sep 6-7, before partitioning; no archive exists) after a one-off `dump.sh partition` of each | 22 GB | 0 | the legacy tables carry the first day's tape and the first paper orders' books; same restore-only risk; needs the one-off archive first (the Monday job never sees them) | as 1 |
| 3 | **Drop the two large secondary indexes on sealed partitions only** (`ticker_id_idx`, `ticker_ts_idx`; keep the pkey and the small partial `ticker_ts_idx1`) | 27.7 GB (w37) + 4.5 GB (legacy) | ~28 GB | reads on the sealed week fall back to the pkey/BRIN paths (the executor never reads sealed weeks; 6B replay reads by `ticker, ts` and would slow from index to scan on that week); non-additive DDL, so gate 3; data untouched | `CREATE INDEX CONCURRENTLY` (additive) |
| 4 | Archive `raw_responses` weekly partitions too and drop them one week after archive | 3.9 GB | ~4 GB | small; `raw_responses` is the source-of-truth for normalizer re-runs (fix 64's score-correction analysis used it) | restore + attach |
| 5 | A second filesystem or a larger disk for `pgdata` (hardware; the user) | n/a | n/a | the only option that keeps every week live through the season; cost is the user's | n/a |

Not proposed: any change to recorder cadence, the bookmaker list or the alternates window (gate 5), compaction, or a
retention job in code (invariant 5). Option 3 is offered because index is 38 % of the tape; it is the least destructive
of the DDL options but the least valuable per statement.

## Recommendation (the loop's lean; the decision is the user's)

Option 1 with a one-week lag, starting with `orderbook_events_y2026w37` **after** (a) an off-host copy of its `.dump.age`
is confirmed (the NAS pull directory or a Mac copy: `sha256` of the ciphertext against `.meta.json`), (b) a partition
decrypt-and-restore drill writes a `drill ok` row for that unit, and (c) 6B's re-score and the order 157 audit are not
awaiting another pre-boundary tape read (they are complete: journal 216, spec 0.18). That alone moves the 600 GB budget
from mid-October to mid-November and, repeated weekly, keeps the live database near one to two game weeks of tape
(about 100-200 GB). Options 2 and 4 are housekeeping the same procedure covers later; option 3 only if the user prefers
to keep every week live. Nothing here changes before the user's dated decision, which the loop records in the journal as
a `decision` entry; the executing statements are the user's, run by hand in a quiet hour with the loop observing.

Evidence: `evidence/2026-09-15-storage-by-table-0803.txt` (sizes, partitions, growth samples, backup rows).
