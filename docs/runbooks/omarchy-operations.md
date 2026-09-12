# Omarchy operations

The migration checkpoint is [nas-to-omarchy-progress.md](nas-to-omarchy-progress.md).
Omarchy is the active runtime; keep the retired NAS application services stopped.

## Addresses and commands

- Production: `trey@192.168.12.127`, runtime `/srv/sports-harness` on the Intel SSD.
- Development checkout: `/home/trey/dev/sports`, Python 3.12 `.venv` and independent
  test PostgreSQL at `127.0.0.1:5433`. Run `make test` there. This does not use production.
- NAS rollback: `trey@192.168.12.228:/volume1/docker/sports-harness`, stopped.
- NAS backup archive: `/volume1/docker/sports-archive/encrypted`.

From the controller checkout:

```sh
make status-omarchy
make preflight-omarchy
make logs-omarchy
make tunnel-omarchy
# With the tunnel running, open http://localhost:8180
make verify-summary-omarchy DEPLOY_SHA=b0a3991
```

On Omarchy, always select the runtime override:

```sh
cd /srv/sports-harness
./sports-compose ps --all
./sports-compose logs --tail 100 app-run app-exec app-ws
```

PostgreSQL is not published on the LAN. The dashboard binds to loopback and uses the
existing token. Runtime credentials are under `secrets/`; do not print them in reports.
`LIVE_TRADING=0`, `HARNESS_MODE=paper`, and `RFQ_LISTENER_ENABLED=0` remain in effect.
Capacity reporting uses `DB_BUDGET_GB=600`; historical removal is not enabled.

## Boot and recovery

`sports-harness.service` starts the stack after Docker, network and the dedicated Btrfs
mount. Its `production-enabled` marker is created only after database validation.
`sports-harness.path` was used for the one-time activation and then disabled; the service
remains enabled for boot. Suspend and hibernation are masked.

LUKS still requires a console password after reboot or power loss. A reboot test has not
been performed. Keep the `/srv/sports-harness` fstab entry after any OS snapshot rollback;
the `@sports` subvolume intentionally sits outside ordinary root snapshots.

The NAS database is a frozen pre-cutover rollback copy. After Omarchy accepts new writes,
starting that old copy would discard new state. A rollback must stop Omarchy writers,
preserve its new state and reconcile/copy it back before starting source services.
Original restart policies and the frozen validation manifest are saved on both hosts.
The old Makefile deployment recipes refuse hosts carrying `migration-retired`.

## Encrypted backups

The existing sidecar schedule continues: nightly and Sunday weekly backups at 03:30
America/Chicago, plus its existing sealed-partition/forever exports. These logical backups
exclude bulk tape data; they are not a complete replacement for the full migration copy.
Historical partition offload and deletion require the dependency and restore checks in
the migration plan before implementation. The WD NTFS drive remains untouched.

Omarchy publishes hard links to complete encrypted backup units every 15 minutes via
`sports-backup-publish.timer`. Only ciphertext, metadata and completion markers enter
`backup-export/`. Removing an export link does not delete its source backup.

The NAS `sports-archive-pull` container pulls these exports every 900 seconds with a key
restricted on Omarchy to read-only rsync. It never deletes NAS files. The original NAS
backup directory is also preserved. Monitor:

```sh
# On NAS
docker logs --tail 20 sports-archive-pull
cat /volume1/docker/sports-archive/last-success.txt
cat /volume1/docker/sports-archive/last-pull.log
# On Omarchy
systemctl status sports-backup-publish.timer
journalctl -u sports-backup-publish.service --since today
```

Fresh nightly `harness-nightly-20260912T221140Z` passed NAS transfer, Mac decryption,
full isolated restoration and all 58 manifest count comparisons on September 12.
The drill is recorded as `backup_run=27`. See the checkpoint for hashes and evidence. The age private key remains on
the Mac/off-host recovery storage. Never copy it onto either runtime or NAS.

## Future releases and controller work

The application remains release `b0a3991` with PostgreSQL 16.15. The Omarchy override
pins its images, so do not repoint `deploy-nas` at Omarchy or run an unqualified Compose
deployment. Those recipes regenerate NAS ownership/settings and do not advance the new
image pins correctly. A reviewed Omarchy release procedure is required before the next
application upgrade; preserve the effective runtime configuration and backup gates.

The Claude autopilot remains stopped. Its existing hardcoded NAS routing must be updated
and reviewed before resumption. Pending pricing/recorder fixes and PostgreSQL 18 remain
separate work. Git history and working documents were copied to the new development
checkout; Mac originals were preserved. Provider/remote Git authentication was not copied.

## Resource observation after cutover

A passive six-hour observer samples health, memory, disk, and executor state every five
minutes into `/srv/sports-harness/migration/observation.jsonl`. It does not restart anything.
Check the PID in `migration/observation.pid`; review the complete window before claiming
sustained performance acceptance.

At cutover, Sunshine/Hyprland held roughly 28–31GB of shared Intel graphics buffers.
The user-authorized Sunshine restart at 22:36Z reduced these to about 365MiB and recovered
available RAM to about 23GB without restarting sports services. Capture recurrence needs
its own investigation; no permanent graphics configuration fix was applied.

The unchanged recorder release still shows memory growth and ticks around 2.5 minutes.
The host move does not resolve those application issues. Preserve their follow-up work
and review the observer output before enabling any autonomous controller.
