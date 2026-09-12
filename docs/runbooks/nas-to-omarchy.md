# NAS to Omarchy migration plan

Prepared 2026-09-12. **Runtime migration and encrypted recovery drill completed**; see
[nas-to-omarchy-progress.md](nas-to-omarchy-progress.md) for authoritative current state.
User explicitly authorized an in-game migration and accepted the recording gap.
All seven runtime services run on Omarchy. The accepted recording gap was 65m36s.
The fresh encrypted NAS backup restored successfully with all 58 manifest counts matching.
Sustained observation, reboot verification, and controller routing remain follow-ups.
The destination is a dedicated, always-on project host: 32GB RAM, Intel Core i9-10900K,
Intel 1TB SSD, preserved WD 4TB HDD. SSH: `trey@192.168.12.127` (`omarchy`).

## Outcome and storage layout

Run the complete sports stack on the new machine, with one authoritative database and one
set of recorders/executors. Preserve all existing data, paper state, credentials, operating
settings, and backup archives. Keep the NAS as an independent backup destination.

| Location | Purpose |
| --- | --- |
| Intel 1TB SSD: OS and Docker data root | Omarchy, images, writable container layers |
| SSD: `/srv/sports-harness` | Runtime release, configuration, secrets, and `pgdata/` |
| SSD: `~/dev/sports` | Development checkout and eventual controller workspace |
| SSD: runtime `backups/` and migration staging | Bounded temporary backup/dump spool outside OS snapshots |
| WD 4TB NTFS | Preserve existing contents; not required for migration |
| NAS: separate archive and backup destination | Verified encrypted history/backup copies; original deployment retained through acceptance |

**User direction, September 12:** preserve the WD drive (probably games) and investigate
offloading unneeded history to the NAS instead. It has an unmounted NTFS partition. No
formatting, filesystem repair, partition changes or writable mount is authorized. Any
optional inspection uses a read-only block device/mount; report if repair would be required.

Revised default: spool complete migration dumps and new backups on the SSD, in bounded
storage outside OS snapshots. Transfer completed archives asynchronously over SSH to a
separate NAS directory, initially proposed as `/volume1/sports-archive`, after checking its
ownership and free space. Preserve the original NAS stack directory as rollback evidence.
Do not place active PostgreSQL storage or mandatory live queries on an SMB/NFS mount.
If NAS transfer fails, retain the local artifact, alert, and pause archival removal; live
recording must continue independently. At the spool limit, defer additional archive work
before it consumes the live database's reserved space.
All active PostgreSQL tables, indexes, and WAL stay on the SSD.

## Historical-data offload design

This is a storage design to implement and validate after full-state migration. No records or
partitions have been removed. The database is only about 79 GB, so reducing it is not a
prerequisite to move onto the 1TB SSD.

| Data | Initial policy |
| --- | --- |
| Live/recent tape and feed data; open/unsettled work; pending replay/audit evidence | Keep on SSD until every dependent job is complete |
| Orders, fills, ledger, settlements, markouts, signals, fairs, variants/configuration, report cells, source cursors | Keep on SSD initially, including history; audit relational and reporting dependencies before considering offload |
| Sealed `orderbook_events` and `venue_trades` partitions | First candidates for verified NAS archival and subsequent local removal |
| Old raw responses, odds snapshots, venue quotes | Later candidates, only after consumer/cursor/reference audit and a bounded archival mechanism |
| Legacy partitions | Explicit inventory and cutoff review; existing weekly archive naming does not cover `_legacy` automatically |

Start with a **provisional minimum of 30 days online for bulk data**, extended for the entire
active experiment, unresolved settlement/markout jobs, planned replays and audit holds.
This is a candidate planning floor, not a proven retention requirement or deletion schedule.
Current code has a 14-day order-chain lookback, week-based reports, and a 30-day dashboard
smoke window. Report table 6 reads raw responses and nearby tape; replay and markout work
also consume historical inputs. There is no single age after which all data becomes unused.
Future model-training/reporting features must declare their online history requirements.

For each eligible sealed partition, implement an explicit state machine:

1. Freeze eligibility: partition bounds, late-arrival handling, no pending consumers or
   integrity/replay holds. Sealed calendar weeks alone do not prove that late writes stopped.
2. Produce a consistent encrypted archive and manifest (schema/build, table/partition bounds,
   row count, time/ID boundaries, checksum, size and dependencies). Retain required parent
   schema and restoration/attachment instructions; dumping a child alone is not a full
   recovery recipe. Preserve schema-compatible tools for old archives across future upgrades.
3. Copy under a temporary NAS name, verify remote checksum, and atomically finalize it.
   Register archive location and coverage in the online catalog. File existence is insufficient.
4. Decrypt and restore to an isolated scratch database, validate against the manifest, and
   run representative report/replay reads. Existing partition dump metadata has no row counts,
   so the existing generic drill cannot by itself certify this stronger archive contract.
5. Evaluate the local-removal policy and recovery copies. The NAS archive becomes the sole
   history copy if all local copies are removed; identify a second independent backup for
   irreplaceable history before making automatic pruning routine.
6. Remove only specifically verified, eligible local partitions through reviewed maintenance
   code, with locks/time limits and an audit record. Dropping a detached relation reclaims
   its files; detaching alone does not save SSD space. Do not issue broad date-based deletes
   or `CASCADE`, and do not remove parent schema/cursors the live services require.

The current archive loop covers named weekly partitions of the two tape tables and skips
when any matching dump exists. It needs the verification/catalog/removal lifecycle above;
it is not a complete cold-storage implementation. Plan source: `deploy/backup/loop.sh`,
`docs/runbooks/backups.md`, `harness/report/tables.py`, `harness/settlement/markouts.py`,
`harness/replay.py` and `harness/db/partition.py`.

Historical analysis restores only the needed date ranges onto Omarchy scratch storage and
runs in an isolated database with CPU/memory limits, outside busy game periods. Historical
UI queries must explicitly report archived coverage or request restoration instead of
silently presenting missing rows as zero. Keep the NAS in the role of storage; ordinary
game-time recording, pricing and execution do not wait for it.

## PostgreSQL 18 evaluation

Recommendation: **migrate with 16.15 first, then evaluate 18 in a separate rehearsal**.
PostgreSQL 18's asynchronous I/O can improve sequential scans, bitmap heap scans and vacuum;
skip scans and partition-planning improvements may help some queries. These are relevant
to this workload, but no application-specific gain has been measured and they do not fix
the recorder's memory growth or remove the need for appropriate indexes.

Restore a complete logical dump to an isolated PostgreSQL 18 database and run the actual
schema migrations, application suite, integrity checks, backup/restore drill, and recorded
workload comparisons on the same hardware. Compare executor loop tails, query plans,
recorder RSS, ingestion throughput and report results. Use a current pinned minor release
at execution time, inspect compatibility changes, and adjust PostgreSQL-container data
mounts and backup tool versions as required. No production version change is authorized
merely by the host already having PostgreSQL 18 command-line tools installed.

A major upgrade needs logical dump/restore, `pg_upgrade`, or an appropriately designed
logical replication migration. Changing the image tag and opening the 16 data directory
with 18 is not an upgrade. If the 18 rehearsal later justifies combining it with cutover,
revise the selected-release and rollback plan explicitly before executing it; restoring new
18-written state back to 16 is not a guaranteed supported downgrade path.

## Verified destination and updated source inventory (15:28–15:30 CDT)

- Destination CPU: i9-10900K, 10 cores/20 threads; 31 GiB RAM reported, about 26 GiB available.
- Intel SSD model `SSDPEKNW010T9`, NVMe, 953.9 GiB. Omarchy occupies LUKS-encrypted Btrfs;
  approximately 912 GiB free. Snapper snapshots the root subvolume. Create a dedicated
  database/runtime subvolume and explicit mount outside root snapshot rollback before restore;
  preserve its mount configuration during any OS rollback. Native database tools are
  PostgreSQL **18.6**, so use the pinned **16.15 container tools** for this migration.
- HDD `WDC WD4005FZBX-00K5WB0`: `/dev/sda2`, approximately 3.6 TiB, NTFS, unmounted.
  User requests preservation. SMART tooling is not currently installed; health is unverified.
- Ethernet `enp4s0` negotiated 1000 Mbps; Wi-Fi interface down; IPv4 assigned by DHCP.
  This is link speed, not measured NAS-to-target throughput. Prefer direct wired transfer
  once transfer authentication is prepared; avoid routing the bulk copy through Mac Wi-Fi.
- SSH enabled at boot, time synchronized. Docker/Compose installed (Compose 5.5.1), Docker
  service inactive/disabled with its socket enabled. `trey` is not in the Docker group and
  cannot access the socket. `sudo -n true` returns `a password is required`.
- Suspend/hibernate systemd targets are not masked. Omarchy's menu suspend toggle only
  hides the menu entry on this installed version; host preparation must prevent actual
  automatic suspend, not just change the menu. Encrypted reboot requires console unlock
  unless a separate, tested mechanism is configured.
- Updated NAS: database **78,715,509,783 bytes** (~78.7 GB), WAL ~1.9 GB; build `b0a3991`,
  schema `0006_quotes_run_index`. RFQ listener **0**, research worker **1**, paper mode **on**.
  Source application UID/GID 1000:10; target UID/GID 1000:1000.
- **23 games in progress**, 28 kickoffs in the protected window. Later NCAAF kickoffs extend
  to 23:00 CDT Saturday; Sunday NFL begins at noon CDT. Do not infer a precise quiet window
  from this alone: late games and the four-hour lookback must clear. Recheck the actual
  game-window queries before any heavy source read or cutover.
- NAS executor currently unhealthy despite HTTP `/healthz` reporting `ok`; record this as
  the source baseline, and check per-service/database health rather than HTTP alone.
- Source PostgreSQL image digest:
  `postgres@sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94`.
  Image ID: `sha256:80f4c7a5e91618546dce5b4fe60cf03b14c0f9efa7e40157278d122772ced8d2`.
- Replication settings support investigation of a lower-outage fallback (`wal_level=replica`,
  10 senders/slots), but network replication authentication is not configured and slot WAL
  retention is unlimited. No replication role, slot, tunnel or configuration has been created.
  Keep the complete logical rehearsal as the default until its duration is measured.

Inert release staging completed at `/home/trey/sports-migration.coYoQequ` on Omarchy:
`release-b0a3991.tar`, `manifest.json`, and this runbook. The 2.5 MB archive contains selected
tracked application/deployment sources, no operational secrets or database data. Both ends
matched SHA-256 `972d4848de82ea2ab28342b3fe99970f6f9ec3ab282a8db696e510c9939488e1`.
Nothing from the archive has been started. Revalidate the source release before using it.

## Evidence and migration-specific traps

Read-only NAS inspection at **2026-09-12 19:47:55 UTC / 14:47:55 CDT** found:

- x86_64; PostgreSQL **16.15**, Debian build; schema `0006_quotes_run_index`.
- Database size **76,906,773,527 bytes**, approximately **76.9 GB / 71.6 GiB**.
  This excludes some cluster overhead, including WAL; measure the full cluster before copying.
- Largest relations: current-week orderbook events 36.8 GB, legacy orderbook events
  16.5 GB, signals 9.4 GB, odds snapshots 5.0 GB, current-week raw responses 3.1 GB.
- Only default tablespaces; NAS volume 11 TB, 7.5 TB used, 3.4 TB available.
- All seven containers were up; PostgreSQL up four days. Source data fits comfortably on
  a healthy, mostly empty 1TB SSD today. Future capacity depends on measured ingestion growth.

Recheck these facts immediately before migration; the loop and database remain active.

Repository findings:

1. `deploy/backup/dump.sh` deliberately excludes data in `raw_responses`,
   `orderbook_events`, `venue_trades`, `venue_quotes`, and `odds_snapshots`, including
   partition children. **Nightly/weekly backups alone cannot migrate the complete database.**
2. Compose uses `postgres:16`, a moving tag. Capture and use the source image digest for
   the move; do not combine a PostgreSQL upgrade with migration.
3. `deploy/nas.env` sets UID 1000, GID 10 and `RFQ_LISTENER_ENABLED=1`. The latest session
   records a live override of `RFQ_LISTENER_ENABLED=0`. Copy the effective source settings
   securely and explicitly preserve overrides; do not regenerate them blindly from this file.
4. `Makefile`, `scripts/preflight.sh`, `scripts/verify_summary.py`, and operational references
   assume the NAS address/path. The existing deploy target also migrates/seeds and starts
   services, so it is unsuitable for creating an inert rehearsal copy.
5. Recorder memory growth (fix 49), pricing failure (48), and other pending fixes remain
   separate application work. A hardware move does not establish that they are fixed.
6. `secrets/backup_age_key` stays on the Mac/off-host recovery storage. Transfer the existing
   public recipient and operational credentials, preserving the dashboard token; do not
   regenerate keys or copy the private backup-decryption key to the runtime host.
7. Existing untracked research documents belong to ongoing work. Use a clean isolated
   release checkout when execution begins; preserve them and this plan without enabling
   dirty deployment overrides.

## 1. Prepare the host when SSH is available

- Record SSH host identity, login user, CPU, RAM, architecture, SSD model/SMART health,
  filesystem, disk capacity, mounts, wired link speed, and time synchronization.
- Verify SSH key login, required administrative access, Docker Engine, Compose, and build
  support. Use the installed Arch/Omarchy facilities; keep the application containerized.
- Configure a stable LAN address/hostname and wired Ethernet. Keep PostgreSQL unpublished
  and the dashboard on `127.0.0.1:8180`, reached through SSH or the existing private network.
- Disable automatic suspend and hibernation. Enable boot-time SSH, Docker, and stack startup
  after storage mounts. Check every service, including `app-ws` whose `on-failure` restart
  policy alone is insufficient for boot recovery. Test a reboot before accepting traffic.
- Check disk-encryption boot behavior: default password unlock requires someone at the
  console after a reboot. Record whether that is acceptable or arrange and test an explicit
  unattended unlock method; do not silently disable encryption.
- Create runtime/spool directories with the new host's actual UID/GID. Keep secrets at
  mode 0600 and their directory restricted. Preserve PostgreSQL's distinct container ownership.
- Budget SSD space for production, a rehearsal database, WAL, image builds, restore temporary
  space, migration dumps, backup spool and growth. Measure NAS archive space separately.
- The 32 GB host provides substantially more memory than the 8 GB NAS. Preserve the existing
  PostgreSQL tuning for the initial migration baseline, then tune from measured workload
  behavior as a separate change. Account for per-connection `work_mem`, host cache, application
  processes and test databases; do not give the database or builds all available RAM.
- If SSD uses Btrfs, inspect its layout and choose database storage/snapshot treatment before
  restoring. Do not count ordinary OS snapshots as PostgreSQL backups or let them retain
  unbounded copies of the database.

## 2. Freeze the release choice and prepare deployment support

Inventory the *live* application build, schema, image digests, effective feature flags,
container mounts, secret filenames/modes, backup archives, scheduled tasks and tunnels.
Never print secret contents into logs or the conversation.

Default to moving the current deployed build and schema unchanged. If fixes 48/49 are
reviewed and authorized before cutover, explicitly select and rehearse that release instead.
Record the exact release and schema in the cutover manifest. Do not silently deploy latest
`main` or imply migration approval resolves unrelated loop gates.

Prepare explicit source and destination deployment profiles, retaining the NAS profile for
rollback. Add host/path selection to deploy, status, tunnel, preflight and verification tools;
replace `/volume1` checks with checks of the actual data and backup mounts. Ensure the
Omarchy environment preserves live overrides and paper mode (`LIVE_TRADING=0`,
`HARNESS_MODE=paper`), and the executor keeps its existing absence of venue credentials.

Build and stage the selected release in advance. Verify the resolved Compose mounts, users,
image references and environment without printing credentials. Start only isolated
PostgreSQL for rehearsal; no recorder, executor, research worker or backup scheduler.

## 3. Rehearse a complete transfer and restore

**Default method: a complete PostgreSQL 16 custom-format dump and restore.** At the measured
77 GB size, test this simpler path first. Use a separate migration command with **no table
or table-data exclusions**, including every partition, plus protected cluster-role metadata
where needed. Do not use the existing selective nightly dump as the migration artifact.

Schedule the full read in a quiet window; the NAS is already I/O-constrained during games.
Stream the dump through SSH to a restricted `.partial` file in the target SSD spool, check every
process exit status, and finalize only after success. Record size, checksum, timestamps,
source build/schema, and archive contents. Inspect required database extensions/roles and
restore all required ownership/privileges using the matching PostgreSQL image.

Restore into an isolated rehearsal database on the SSD. Use error-stopping restore behavior
and bounded parallelism selected from actual RAM/CPU. Run `ANALYZE` after restore. Validate
schema, partition membership, indexes/constraints, sequences and core paper-state tables.
Compare row counts against the dump's exported snapshot when taking exact rehearsal counts;
later live-source counts are not a valid equality reference. Avoid expensive bulk-table scans
during a game window. A successful archive listing is not proof that restoration works.

Record dump, network transfer, restore and analysis durations separately, peak disk use,
and target memory pressure. The sum plus validation/startup determines the outage budget;
there is no promised downtime until this rehearsal completes. Prepare a fresh destination
directory/database for final restoration so a partial retry cannot mix old and new state.

If logical restore exceeds the available quiet window, select and rehearse a different method
before cutover. A compatible physical copy with an online pre-seed and a final **offline,
checksum-based** synchronization preserves the whole cluster without rebuilding indexes,
but its checksum pass must read the data and can still take substantial time. Never boot a
raw copy taken while PostgreSQL was writing. Replication-based cutover is a separate,
explicitly designed fallback if the outage budget requires it.

## 4. Cut over in one controlled window

Use the existing game-window checks and current operating decisions to schedule the move.
Recheck immediately before stopping services. Name one cutover operator and reconcile the
Claude loop's monitors, restart commands, scheduled wakeups and deployment activity so they
cannot restart the source or write to both hosts. Planning has not paused that loop.

1. Confirm the rehearsal passed, mounts/capacity are ready, selected images are built, the
   destination has no active application services, and the outage/rollback steps are ready.
2. Stop all source database clients: `app-run`, `app-exec`, `app-ws`, `app-research`,
   `app-serve`, `app-backup`, plus any external jobs or sessions that can write. Keep source
   PostgreSQL available for the final logical dump. Verify no writers remain and prevent
   automatic restarts/redeploys throughout the handoff.
3. Record the last completed tick, tape event boundaries, executor state/open paper orders,
   core ledger/order/signal counts and schema. Record interrupted jobs without manually
   editing their state; apply existing recovery procedures at startup.
4. Take the final complete dump from the now-quiescent source, transfer/verify it, then stop
   source PostgreSQL cleanly. Preserve the source cluster and configuration unchanged.
5. Restore to the fresh production destination and validate it before any application
   writer starts. Compare to the frozen source manifest, including data presence in all five
   bulk families, partition/index validity, sequences and paper-state totals. Use the
   rehearsal to choose affordable exact counts and boundary checks; report unchecked rows.
6. Start target `app-ws`, then recorder and executor, checking each before adding dashboard,
   research and backup workloads. Preserve feature overrides and spending caps. Reconnect
   the tape using the existing recovery logic; explicitly record the migration data gap.
7. Repoint the dashboard tunnel, status/verification tools and controller host configuration
   to Omarchy. Confirm source containers remain stopped and only one stack records data.

Stopping capture creates an external-feed gap even though all pre-cutover database state is
preserved. Record its start/end and any recovery limits; do not claim zero data loss from
the live feed or force API activity that bypasses quiet-hour/credit rules.

## 5. Validate before retiring the source

Use `docs/superpowers/autopilot/verify.md` with the new target and a before/after baseline:

- Correct build/schema, service health, fresh recorder ticks and fresh WebSocket events.
- Executor heartbeat under 60 seconds; loop p95 within the existing period-based budget;
  skipped-loop count stable, no sustained database timeouts or reconnect storm.
- Dashboard data and paper balances/orders agree with database state; freshness checks are
  evaluated after the expected scheduled tick, not against the stale restored snapshot alone.
- Run the applicable integrity checks; document every failure or skipped check.
- Track recorder RSS, host available RAM, swap-in/out and I/O wait through at least six hours
  and a busy game window. If fix 49 is included, apply its existing acceptance criterion:
  recorder under 500 MiB across six hours. Extra RAM is not evidence that growth is bounded.
- Complete one new backup, off-host encrypted copy, and successful decrypt/restore drill.
  The local backup free-space guard measures the SSD spool; also monitor NAS archive capacity
  and transfer backlog independently.
- Verify services recover after the prepared boot/mount sequence. Monitor archive growth,
  plaintext backlog and database growth, using measured rates to set retention/capacity alerts.

Keep the source deployment stopped and intact until a busy window and backup restore have
passed. Do not run old NAS application containers just to test rollback connectivity.

## 6. Rollback rules

**Before the destination starts application writers:** stop target services, confirm they
cannot restart, restore source restart configuration, start the original NAS stack in the
recorded order, repoint tools/tunnel, and validate freshness. The source remains the original
database; record the interruption and feed gap.

**After the destination accepts writes:** the NAS copy is stale. Stop target writers and
preserve a complete backup of target state first. For a rollback that preserves new records,
transfer that state back and validate before restarting source writers. Do not copy it over
the preserved original cluster without a separate recovery copy. Simply starting the stale
NAS database would discard post-cutover paper state and recorded data; that tradeoff requires
an explicit recovery decision. Never allow both sets of application writers to run.

Trigger rollback/recovery for failed restore or integrity checks, unexplained paper-state
differences, unreadable secrets, sustained unhealthy services or unacceptable resource
pressure after the agreed warm-up. Rehearsal failures postpone cutover while the NAS runs.

## 7. Move the development/controller workspace after runtime acceptance

Initially operate from the Mac to avoid moving the database and its controller simultaneously.
Then establish `~/dev/sports` on Omarchy, transferring committed history and preserving local
work, worktree branches, ledgers and required project context deliberately. Inventory Claude
session/checkpoint locations, local hooks, absolute Mac paths, browser/tunnel access, test
database setup and scheduled tasks before resuming the loop there. Install required tools
and authenticate locally; do not copy active processes, cron wakeups or all of the Mac's
credentials blindly. Retain the backup decryption key off the runtime machine.

Give the new controller sole ownership, verify its preflight points to Omarchy and that no
old Mac/NAS automation can mutate production. Keep builds/tests from starving production:
measure their resource use, limit concurrency and containers, and run heavy suites outside
game windows. Runtime migration can complete while the Mac remains the controller.

## Ready-to-execute inputs

- SSH and CPU/RAM verified. Administrative setup remains blocked by password-requiring sudo.
- Healthy SSD, runtime/spool layout, NAS archive capacity, wired throughput and boot/unlock
  behavior. The preserved HDD is not a migration prerequisite.
- Rehearsal measurements establishing a suitable quiet window and outage budget.
- Selected deployed release and disposition of separately pending application fixes.

### Next administrative work, scoped to the new host

1. Inspect SSD health. Optionally inspect the NTFS drive read-only; preserve every existing
   file. Prepare a bounded SSD spool and a separate NAS archive destination.
2. Create runtime/database storage outside OS rollback snapshots, with appropriate ownership;
   enable Docker and configure the intended operator's access; disable automatic sleep.
3. Stage the pinned PostgreSQL image and application release, preserving NAS overrides and
   mapping the target UID/GID. Prepare a separate rehearsal database; leave app writers off.
4. Measure transfer/restore in a verified quiet window, then choose the cutover procedure.

The installed `omarchy-sudo-passwordless 30` helper can grant the logged-in user temporary
administrative access for 30 minutes after a local password/confirmation, then expires it.
This is an access requirement from the observed host, not a request to authorize NAS
downtime or erase the HDD. Any such access is used only for the agreed migration preparation.

## Sources

- Local: `docker-compose.yml`, `Makefile`, `deploy/nas.env`, `deploy/backup/dump.sh`,
  `docs/runbooks/backups.md`, `docs/superpowers/autopilot/verify.md`, journal entries 140–150.
- [Omarchy SSH/security setup](https://omarchy.org/manual/security/): SSHD is enabled through
  Setup > Security > SSHD; verify access from the controller afterward.
- [Omarchy system sleep](https://omarchy.org/manual/system-sleep/): disable suspend and
  hibernation for the dedicated host.
- [PostgreSQL 16 pg_dump](https://www.postgresql.org/docs/16/app-pgdump.html) and
  [SQL dump/restore](https://www.postgresql.org/docs/16/backup-dump.html): complete logical
  backup, restore and snapshot semantics.
- [PostgreSQL 16 filesystem backup](https://www.postgresql.org/docs/16/backup-file.html):
  requirements for the optional online pre-seed/offline checksum-copy method.
- [Docker on Arch](https://wiki.archlinux.org/title/Docker): target package/service setup.
- [Btrfs subvolumes](https://btrfs.readthedocs.io/en/latest/btrfs-subvolume.html): snapshots
  do not recurse into other subvolumes; OS rollback and database recovery must be separate.
- [PostgreSQL 18 release notes](https://www.postgresql.org/docs/release/18.0/): relevant I/O,
  index/planner improvements and major-version migration requirements.
- [Official PostgreSQL container documentation](https://github.com/docker-library/docs/blob/master/postgres/README.md):
  review version-specific `PGDATA` and volume layout when testing an upgrade.
