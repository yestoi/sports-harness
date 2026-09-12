# Omarchy migration execution checkpoint — 2026-09-12

## Status

User authorized starting migration and confirmed the sports Claude Code autopilot,
including its restart/wakeup activity, is stopped. User enabled temporary sudo on Omarchy.
Host preparation and the schema-only rehearsal passed. User explicitly authorized:
**“Authorize migration during live games, accepting the recording gap.”**
The normal game-window gate is overridden for this migration only.

**Runtime migration and fresh encrypted backup recovery verification are complete.**
All seven services run on Omarchy, with PostgreSQL, executor and dashboard healthy.
NAS source services remain stopped with restart policies `no` and a retirement marker.
The full source cluster is preserved on the NAS; WD NTFS remains unmounted/unchanged.

- Full archive transfer/checksum window: 21:09:21Z–22:03:43Z (54m22s).
- Target extraction: 22:04:07Z–22:09:37Z (5m30s); validation passed 22:09:39Z.
- First new tape event: `95763991`, `2026-09-12T22:10:39.187560Z`.
- Last frozen tape event: `95763990`, `2026-09-12T21:05:02.983Z`.
- **Measured tape gap: 65m36.205s**, explicitly accepted by the user.
- First recorder tick: `13046`, 22:11:08Z–22:13:36Z, `ok`, errors/warnings zero.
- Operator event `1279` records the cutover and accepted tape gap.
- All nine deterministic dashboard-vs-SQL checks passed; page 1.2s, summary API 2.0s.
- Post-start five integrity checks remain zero; no production non-GET venue requests.
- Executor first catch-up loop 52.3s, subsequent sampled loops 8.8s then 4.2–4.3s;
  compare NAS baseline approximately 17s. Startup p95 is not steady-state performance.
- Recorder ticks still take roughly 2.5 minutes on the unchanged application release.
  Do not claim the host move fixes all game-window cadence or recorder memory issues.
- Mac localhost:8180 tunnel now points to Omarchy; verified old NAS tunnel PIDs stopped.
- Boot service enabled and active. One-time activation path unit disabled after success.
- Six-hour passive observation started, PID in `migration/observation.pid`, samples every
  five minutes in `migration/observation.jsonl`. It never restarts services. Six-hour/busy
  window acceptance and reboot recovery are not yet established.

## Restore validation passed

- Extraction ran 22:04:07Z–22:09:37Z (5m30s).
- Complete inventory: all 1,962 regular-file paths/sizes match the frozen source.
- Database system identifier `7682579637160701991`; clean shutdown before startup;
  schema `0006_quotes_run_index`; invalid indexes zero.
- All 11 frozen core table counts matched at 22:09:39Z before any application writer.
- Five prestart invariants all zero: orders without place, overfilled orders, fills
  exceeding order size, markouts after horizon, and production non-GET venue requests.
- Frozen last tape: id `95763990`, `2026-09-12T21:05:02.983Z`.
- Frozen last run: id `13045`, started `21:03:48.042672Z`, status `running` (source
  recorder was stopped during that tick). Preserve this as part of the recording-gap evidence.

## Frozen source and recovery

- Source evidence: `/volume1/docker/sports-harness/migration-20260912/`.
- `core-manifest.json`: runs 12952, fills 1488, ledger 2, orders 8829,
  markouts 19755, report_runs 9, report_cells 54570, settlements 123,
  source_state 133, normalize_state 8, strategy_variants 8; other clients zero.
- `pg_controldata`: clean `shut down`; system identifier `7682579637160701991`.
- Original restart policies saved in `restart-policies.txt`; all seven containers now `no`.
  Recorder and executor exceeded shutdown grace (exit 137); database shutdown itself
  was clean. All other application services stopped; backup sleeper was explicitly killed.
- Source pgdata remains intact as the frozen rollback copy. Once target accepts writes,
  rollback requires reconciling/copying new target state; do not simply restart stale source.
- Target archive: `/srv/sports-harness/migration/cold-cluster-20260912.tar.partial`
  was renamed `.tar` after source/target checksum match. Final archive: 82,198,179,840 bytes;
  SHA-256 `600cdffcab11ce70850288565d56218c6bd8cf39782233bb645d82d5345b51b4`.
- Physical transfer selected to preserve all tape and existing indexes without a logical
  rebuild. Source and target use identical PostgreSQL 16.15 image and x86_64 architecture.
  No tablespace symlinks. The rehearsal database is stopped; target production is now running.

## Confirmed hosts and release

- Source: `trey@192.168.12.228`, `/volume1/docker/sports-harness`.
- Destination: `trey@192.168.12.127`, hostname `omarchy`, `/srv/sports-harness`.
- Application source: `b0a3991`; exact selected tracked source archive staged earlier at
  `/home/trey/sports-migration.coYoQequ/release-b0a3991.tar` and extracted into runtime.
- PostgreSQL 16.15 pinned by image digest:
  `postgres@sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94`.
- Schema: `0006_quotes_run_index`. Native host PostgreSQL 18 tools are not used.
- Latest source size at approximately 15:49 CDT: 79,524,199,447 bytes plus WAL/cluster
  overhead. 24 games live; source swap ~4.85 GB and sampled I/O wait 21–37%.
- Source executor container reports unhealthy; its sampled loop was 17.497 seconds,
  heartbeat age 32 seconds, open orders 0. These are source observations, not target success.

## Host changes completed

- Created Btrfs **top-level** subvolume `@sports` (ID 265) and mounted it at
  `/srv/sports-harness`. Added UUID-based fstab entry. The database will be outside ordinary
  root snapshots. Original fstab: `/etc/fstab.before-sports-20260912`.
- `findmnt --verify` passed (existing swap-file warning; daemon reloaded after fstab edit).
  A future OS rollback must retain/recover the separate runtime mount entry.
- Created runtime `secrets/`, `migration/`, and `backups/{nightly,weekly,partitions,forever}`.
  Runtime user/group is 1000:1000; PostgreSQL retains its container-specific ownership.
- Masked `sleep`, `suspend`, `hibernate`, `hybrid-sleep`, `suspend-then-hibernate` targets.
- Enabled/started Docker; added `trey` to Docker group. New SSH sessions have Docker access.
- Installed `smartmontools` and `nvme-cli`. SSD SMART passed: 3% used, 0 media errors,
  0 error-log entries, 31 C. HDD SMART passed with 0 reallocated/pending/uncorrectable sectors.
  **WD NTFS partition remains unmounted and unchanged.** Its data is to be preserved.
- Systemd production unit installed and syntax-verified at
  `/etc/systemd/system/sports-harness.service`. It is **enabled and active**, and requires
  `/srv/sports-harness/migration/production-enabled`, which now exists. It uses
  `RequiresMountsFor=/srv/sports-harness`, not a bare directory check.
- `sports-harness.path` was used for activation, then disabled. Production service is
  enabled and active; its marker exists.
- Boot/reboot recovery has not been tested. LUKS still requires console unlock unless the
  user separately configures another mechanism; do not reboot the box unexpectedly.

## Runtime staging and checks completed

- Copied effective NAS `.env` and the five operational credential files securely.
  Changed `APP_GID` from 10 to 1000 for the target. Set `DB_BUDGET_GB=600`
  for capacity alerts appropriate to the 1TB SSD (source budget was 2000). Preserved `LIVE_TRADING=0`,
  `HARNESS_MODE=paper`, `RFQ_LISTENER_ENABLED=0`, research enabled and `BUILD_SHA=b0a3991`.
- All five credential hashes and the public backup recipient matched source. Modes 0600;
  `secrets/` mode 0700. Backup **private** decryption key remains on Mac, never transferred.
- `/srv/sports-harness/compose.omarchy.yml` pins PostgreSQL and application image tags.
  `/srv/sports-harness/sports-compose` selects the base and override with project
  `sports-harness`. `config --quiet` passed. Always use this wrapper for target production.
- Rebuilt application on Omarchy from source `b0a3991` and its `constraints.txt`.
  All five service tags point to `sports-migration/app:b0a3991`, image digest/ID beginning
  `61aab7604372`; full build log: `migration/app-build.log`.
  This preserves application source/dependency pins, **not the exact old binary image**.
- App import smoke passed: psycopg 3.3.5 and SQLAlchemy 2.0.52.
- Exported source schema (102,246 bytes), SHA-256
  `05c66b4c91d44582d71bc72378beb498dfe3e9125b0526b6340548b25133a423`.
- Restored it successfully into `sports-rehearsal-db`, PostgreSQL 16.15, Docker network
  `none`, 8 GiB memory limit, 4 CPU limit, no published ports. Storage is
  `migration/rehearsal-pgdata`; source SQL and log are `migration/source-schema.sql` and
  `migration/schema-restore.log`. This database has schema only, **not production rows**.
- Restored schema inventory: 68 ordinary tables, 3 partitioned parents, 35 sequences,
  158 ordinary indexes, 14 partitioned indexes, 4 views. Invalid indexes: 0.
- Application ORM comparison against that database checked all 62 model tables:
  no missing tables or columns. Does not establish data correctness, timing or full health.

## Transfer issue resolved and cleanup

NAS uses `/etc/ssh/force_command.sh`, which overrides `authorized_keys` forced commands
for admin users. A temporary command-restricted target-to-NAS key therefore did not enforce
the intended restrictions. Removed its authorization, generated key files, helper script
and temporary authorization backup. Do not reuse this approach without fixing the design.
SCP to this NAS requires `-O`; default SFTP handling did not reach the intended path.

Used a short-lived **separate** forwarded SSH agent for direct wired config/image transfer;
the Mac private key never left the Mac. Agents were killed in `finally` cleanup. Nested SSH
inside streamed shell scripts must use `-n` or it consumes the remainder of the script.

The NAS image export stalled while the source was under load. Stopped target SSH/load
processes and the verified orphaned source `docker image save` PID 1983406; abandoned import
log is `migration/image-load.log`. Pulled PostgreSQL from its pinned registry digest and
built the app locally instead. No live application container was restarted by this action.

## Backup routing and development preparation

- Added `deploy/omarchy/` host tooling and read-only `scripts/omarchy.sh` commands.
  `make status-omarchy`, `logs-omarchy`, `tunnel-omarchy`, `preflight-omarchy`, and
  `verify-summary-omarchy DEPLOY_SHA=b0a3991` select the new host and Compose wrapper.
  Existing `.env.nas` remains the rollback profile. Old deploy recipes now check
  the remote `migration-retired` marker before any write. Marker created on NAS;
  both `make deploy-nas` and `make deploy-nas-app` tested and rejected before writes.
- **Do not resume the autopilot yet:** its hardcoded NAS procedures still need routing
  updates/review. This migration does not resume it or deploy pending application fixes.
- New target publisher uses hard links to completed `.dump.age`, metadata and `.ok` units
  only. Tested exclusion of plaintext, incomplete units and symlinks; idempotence and
  export-link cleanup leave original spool files intact. Systemd publisher timer enabled,
  every 15 minutes. Export is `/srv/sports-harness/backup-export`, mode 0700.
- NAS-generated SSH key is restricted on **Omarchy** to `rrsync -ro` of that export,
  source address `192.168.12.228`, no shell/forwarding. Tested shell and `../secrets`
  access denied; empty export pull succeeded. Mac age private key remains off-host.
- NAS destination `/volume1/docker/sports-archive/encrypted`; original NAS backups
  unchanged. No `--delete` on NAS pulls. NAS user cron installation was denied, so
  `sports-archive-pull` container runs pulls every 900 seconds with restart enabled,
  read-only container filesystem, key mount read-only, 256MB RAM and 0.5 CPU limit.
  First empty pull succeeded 2026-09-12T21:20:27Z; this is access validation, not a
  successfully backed-up production database. Both the historical forever unit and the fresh
  nightly subsequently passed the end-to-end copy/decrypt/restore drills below.
- Repository history (all bundle refs) cloned to `/home/trey/dev/sports`; original origin
  URL restored without copying Git credentials. Current main is `2a1061c`, while runtime
  remains source `b0a3991`. Original Mac working documents copied, not removed.
- Installed uv and created development `.venv` with Python 3.12.14 and exact constraints.
  Isolated `harness-pg-test` container uses PG16.15, loopback port 5433, independent
  `sports-development-pgdata` volume, 4GB RAM/4 CPU bounds. Development full suite completed (3125 tests, two failures);
  log `/srv/sports-harness/migration/development-tests.log`. Both failures were weather tests mixing fixed `NOW` with actual fetch
  timestamps. Added `@freeze_time(NOW)` to those two tests only; all 15 weather tests
  passed on targeted rerun (`migration/development-weather-retest.log`). No production
  application source changed. Main includes newer settlement code than b0a3991, so these
  test results apply to development, not the pinned runtime release.
  No development secrets or
  private backup key have been copied into the checkout; no autopilot process started.

## Encrypted backup path drill completed

Existing NAS forever unit `harness-forever-20260912T084136Z` was copied as ciphertext
and metadata to the Omarchy spool, published, then pulled into the new NAS archive.
Source and new NAS ciphertext SHA-256 match:
`53022062ca35a172341911ec9bba67a0fd74ee3fb5ffbf6f2616dd069e606c00`.
Mac-only decryption produced metadata-matching SHA-256
`0a5c8f15706d5b2d17e667b5201bfc36ba714ce36f9c1c0f0f6aab878179ad44`.
Restored decrypted archive on Omarchy in an isolated PG16.15 container (network none,
4GB/2CPU limits): ledger 2/2, gate_reports 0/0; `ROWS_MATCH true`, zero mismatches.
Evidence: `migration/backup-drill/forever/restore.log`. Original archive unchanged.
This validates access/encryption/restoration of that two-table historical unit only;
The fresh nightly verification is tracked below. No drill record was written to the
frozen NAS database.

## Fresh nightly backup

Nightly `harness-nightly-20260912T221140Z` completed on Omarchy:
1,371,244,002 plaintext archive bytes, SHA-256
`8b3f931bcd3dbaab4e3da620e4c711a9a61af586a581c8995bd95dd60191152c`.
Encrypted and published; NAS pull completed. Ciphertext SHA-256 matches both hosts:
`3b145bcf3299d8a169217948e588611881c61293a4e0d7eb3c52cc7c910ef889`.
Mac decryption passed plaintext SHA-256 and byte count. Isolated PG16.15 restore completed
successfully at approximately 23:03Z (started approximately 22:30Z, about 33 minutes).
**`COMPARED 58 MISMATCHES 0 NO_COUNT 0`; `ROWS_MATCH true`.** The disposable container
and its anonymous data volume were removed by the drill cleanup. The scratch resource
watchdog did not intervene. A concurrent diagnostic command exited 137 during normal
container cleanup; the actual restore and comparison exited zero.
Recorded on live Omarchy as **backup_run=27, status=ok**, with successful Mac decryption,
matching plaintext digest, and matching rows. `backup-precheck` passed for fresh nightly
**backup_run=23**, finished 22:14:43Z. Evidence: `migration/backup-drill/nightly/`
(`restore.log`, `decrypt-result.json`, `record-result.txt`, `precheck-result.txt`). Existing dump exclusions do **not** cover `_legacy` children, observed
COPY of `orderbook_events_legacy`; the 58-table count manifest does not count these legacy
children. The drill verifies their restoration but only compares row counts for the 58
manifest tables. Historical offload policy still needs its explicit audit.

## Host graphics memory issue found during final drill

At approximately 22:22Z, host shared memory exceeded 22GB and swap rose above 1GB,
while all sports containers together used only a few GB (recorder approximately 1.6GB).
`/sys/kernel/debug/dma_buf/bufinfo` and per-process fdinfo traced approximately 28–31GB
of Intel `i915` DMA buffers to Hyprland (PID1261) and Sunshine (PID30372). The NVIDIA
RTX3080 was essentially idle. This is separate from the existing recorder memory-growth
issue. A separate user Codex process (PID49897, started17:07CDT) is also running; not ours.

User confirmed the other session is theirs and explicitly authorized **“Restart Sunshine now.”**
Restarted `app-dev.lizardbyte.app.Sunshine.service` at 22:36:25Z. DMA buffers fell to
382,214,144 bytes (14 objects), and available RAM recovered to 23,276 MiB. At 22:40Z
the buffer count/size remained stable, Sunshine was active, and all seven sports services
remained running with their existing healthy checks. No sports restart was needed.
The other Codex session was left alone. Evidence: `migration/sunshine-restart-result.txt`.
Evidence: `migration/graphics-memory-investigation.txt` and fdinfo investigation in chat.
Fresh-nightly decryption passed SHA256 and byte count; 58 table counts are available.
Decrypted archive is staged under `migration/backup-drill/nightly/`. Its isolated restore
was initially held, then started with a safer 1GB RAM/1GB total memory+swap cap, 2 CPUs,
128MB shared buffers, 32MB maintenance memory and two restore jobs. A separate watchdog
stops only `sports-migration-backup-drill` if host MemAvailable falls below 1.5GiB.
After Sunshine released its buffers, the scratch memory cap was raised to 4GB at 22:41Z;
CPU was subsequently raised to four and scratch maintenance memory to 512MB for index
building; the memory cap was finally raised to 8GB to allow more file cache while the
host had over 22GB available. Production container limits/settings were not changed.
Scratch-only durability settings were disabled to accelerate this disposable restore;
production `fsync`, `synchronous_commit`, and `full_page_writes` were separately verified
**on** at 22:40Z. The fresh nightly restore and 58-table comparison subsequently passed
at approximately 23:03Z. Production services remain running/healthy and source NAS writers
remain stopped. Graphics buffers remained 382,214,144 bytes at the 22:58Z check.

## Final checks and cleanup

At 23:03Z all five integrity checks were zero; recent recorder runs finished `ok` with
no errors. Latest executor loop was 3.026 seconds, but trailing p95 was 19.953 seconds
during the restore workload, so steady-state latency is not yet established. All seven
Omarchy services remained running; PostgreSQL, executor and dashboard health checks passed.
NAS runtime containers remained stopped; latest archive pull succeeded at 22:51:05Z.

A second, user-owned application container appeared during the other setup session; it was
not modified. Removed 2,742,835,136 bytes of generated Mac drill downloads/plaintext and
1,371,244,002 bytes of target nightly drill plaintext after preserving validation evidence.
The full migration archive, original NAS database, encrypted backups and WD data remain.
Final recovery evidence is also saved on the Mac at
`/tmp/sports-omarchy-final-evidence-20260912/`; durable host evidence stays under
`/srv/sports-harness/migration/`.

## Next work

1. Review the six-hour observation and a busy window for sustained resource behavior;
   sustained acceptance and reboot recovery remain unverified.
2. Separate follow-ups: migrate/review autopilot deployment routing before resumption,
   historical archival/removal lifecycle, PG18 rehearsal, and full controller relocation.
   Do not restart NAS writers or deploy pending application fixes as part of these checks.

Main design: [nas-to-omarchy.md](nas-to-omarchy.md).
