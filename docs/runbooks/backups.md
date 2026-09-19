# Backups runbook

Nightly 03:30 CT and weekly Sunday 03:30 CT dumps of every table **except** the five bulk
tables (`raw_responses`, `orderbook_events`, `venue_trades`, `venue_quotes`, `odds_snapshots`),
written by the `app-backup` sidecar (image `postgres:16`, no build) to
`/volume1/docker/sports-harness/backups/`. There is **no weekly full dump**: eight full dumps of
a terabyte database do not fit beside it, and a full `pg_dump` pins the xmin horizon across
Sunday recording.

`app-run` encrypts each plaintext every 10 minutes to `<name>.dump.age` with the age v1 format
against `deploy/backup_age.pub`. The private key `secrets/backup_age_key` lives on the Mac only
and is never pushed. Retention is 30 nightly and 8 weekly **units**; `backups/partitions/` and
`backups/forever/` are never deleted.

## What a unit is

A unit is one stamp, not one file. Every kind writes the same shape under
`backups/<dir>/harness-<dir>-<stamp>.*`, where `<dir>` is `nightly`, `weekly`, `forever` or
`partitions`. The `partitions` archive is the one that carries more than a stamp: its name is
`harness-partitions-<partition name>-<stamp>.*`, so `venue_trades_y2026w36` archived on the 8th
is `backups/partitions/harness-partitions-venue_trades_y2026w36-20260908T090000Z.dump.age`.

| File | Written by | Deleted by |
|---|---|---|
| `.dump` | `dump.sh` (the plaintext `pg_dump` custom-format archive) | `harness backup-encrypt`, and only after a passing Mac-side drill for that build |
| `.meta.json` | `dump.sh` (the sidecar: kind, tables included, sizes) | the sidecar's retention loop, with the unit |
| `.dump.age` | `harness backup-encrypt` (the ciphertext) | the sidecar's retention loop, with the unit |
| `.ok` | `harness backup-encrypt`, after the ciphertext passes its structure check | the sidecar's retention loop, with the unit |
| `.dump.age.bad-<stamp>` | `harness backup-encrypt`, when a structure check fails | **nothing, ever** — it is evidence and is kept for inspection |

The sidecar's retention loop deletes only `.dump.age`, `.meta.json` and `.ok`, only for a unit
past the keep window, and only when the `.ok` marker exists and no plaintext is still sitting
beside the ciphertext. It never deletes a `.dump` and never touches a `.bad-*` file. A unit it
declines to delete prints one `KEEP` line, which is what `docker compose logs app-backup` shows.

`backup_runs.kind` takes six values: `nightly`, `weekly`, `forever`, `partition`, `encrypt`
and `drill`. Note the singular `partition` in the table against the plural `partitions` on
disk — the row names the kind of work, the directory names the archive.

## `forever/`

`backups/forever/` holds `ledger` and `gate_reports` — the two tables a human will want years
from now — dumped after every nightly and never pruned. They are **encrypted custom-format
dumps, not CSV**, because a unit has one shape and `backup-encrypt` only recognises that shape.
To get the rows back out of one:

```sh
pg_restore --data-only -t ledger -f - backups/forever/harness-forever-<stamp>.dump
```

That prints `COPY` plus tab-separated rows. For true CSV, restore the dump into the throwaway
container the drill starts and run
`COPY (SELECT * FROM ledger) TO STDOUT WITH (FORMAT csv, HEADER true)` there.

## The `.meta.json` sidecar

`dump.sh` writes one `.meta.json` beside each `.dump`, before the plaintext is renamed into
place (`scan_units` ignores both while the `.tmp` name is still in use). This is the exact
object it writes:

```json
{
  "kind": "nightly",
  "stamp": "20260909T033000Z",
  "path": "/backups/nightly/harness-nightly-20260909T033000Z.dump",
  "sha256": "<64 hex>",
  "bytes": 1234567,
  "started": "2026-09-09T03:30:00Z",
  "finished": "2026-09-09T03:34:12Z",
  "exit_code": 0,
  "tables": {
    "data_excluded": ["raw_responses", "orderbook_events", "venue_trades", "venue_quotes",
                      "odds_snapshots"],
    "counts": {"orders": 4212, "signals": 881033, "ledger": 0},
    "counts_snapshot": "same as dump"
  }
}
```

`kind`, `stamp`, `path`, `sha256`, `bytes`, `started`, `finished` and `exit_code` describe the
dump itself. `tables.data_excluded` is the fixed list of the five bulk tables this kind's dump
carries no data for (empty for `kind = "forever"`, which only ever dumps `ledger` and
`gate_reports`). `tables.counts` and `tables.counts_snapshot` are the drill's evidence:

- **`counts` is the row count at dump time**, one entry per dumped, non-excluded,
  non-partition-child table. The five excluded tables above are absent from `counts` because
  their data is not in the dump at all — counting them would record a number the restore can
  never match. A `kind = "partition"` dump (one sealed weekly partition) takes no counts at all:
  `counts` is `{}` and `counts_snapshot` is `"none"`.
- **`counts_snapshot` says how trustworthy `counts` is**, one of three values. `"same as dump"`
  means the counts were read from the same Postgres snapshot `pg_dump` used, so the numbers are
  exactly what the dump carries. `"before dump"` means the counts were read immediately *before*
  `pg_dump` started (the exported-snapshot path was unavailable), so a busy table can legitimately
  restore with **more** rows than `counts` records — never fewer; `drill.sh`'s `compare_row`
  treats that case as `GREW`, not a mismatch. `"none"` means this kind takes no counts at all
  (`kind = "partition"`); `drill.sh` skips the row-count verdict entirely for those and says so.
- **`drill.sh` compares against `counts`, never against the live database.** Production keeps
  recording after a dump is taken, so a live comparison would report a MISMATCH on every busy
  table and the `ROWS_MATCH` line would mean nothing. A table present in the restore with no
  entry in `counts` is reported as `NO_COUNT` and excluded from the verdict rather than silently
  passed — `drill.sh` prints `COMPARED n MISMATCHES n NO_COUNT n` before its `ROWS_MATCH`
  line, and a table the dump carried that the restore is missing entirely is reported as
  `MISSING_TABLE` and counted as a mismatch.

The counts cost one scan of `signals`, the largest dumped table, per night, inside the same
03:30 CT window the dump itself runs in.

## The tape has one archive and no other protection

On Mondays at 04:00 CT each sealed weekly partition of `orderbook_events` and `venue_trades`
from the previous ISO week is archived once to `backups/partitions/` and then never touched.
Between that archive and the RAID, the tape has no other protection.

## Restore drill (a backup is not done until one has run)

Two halves, both recorded in `backup_runs` with `kind = 'drill'`. Since the 2026-09-12 cutover
the stack runs on Omarchy under `/srv/sports-harness`; the NAS is a retired archive destination
that the archive puller writes to and nothing reads from for a drill. **The drill never runs on
the NAS and never opens a tunnel to it** (the earlier NAS-half text is superseded; corrected
2026-09-19 under the user's item 1 ruling, journal 298).

1. **Host half** (the controller, on Omarchy, from the deployed release tree `/srv/sports-harness`,
   whose `deploy/backup/drill.sh` is the released copy; the plaintext is the host's own copy under
   `backups/nightly/` or `backups/partitions/` there):

   ```sh
   bash -c 'cd /srv/sports-harness && deploy/backup/drill.sh backups/partitions/<file>.dump'
   ```

   **Not** `sports-compose exec app-backup /backup/drill.sh`. The sidecar has no docker socket,
   and the script runs `docker run` and `docker cp` itself: it is a host script that happens to
   live beside the sidecar's two, and its own header says so. It reads the `.meta.json` beside
   the dump, starts a throwaway `postgres:16` container (`docker run --rm`, an anonymous volume
   that disappears with it), restores the plaintext there, and stops the container. The
   production cluster is not read, created, dropped or written.

   **Read the output, not the exit status.** The script exits 0 on
   `SKIP drill free=<n>% below MIN_FREE_PCT=30%` (it measures the dump's own filesystem: on
   Omarchy `/srv/sports-harness` is the `@sports` Btrfs subvolume, whose `df` reads the shared pool), it exits 0 on `ROWS_MATCH false`, and it exits 0 on a
   partition unit's `ROWS_MATCH n/a`. The verdict is the printed line:
   - `RESTORE_OK <file>` is the restore proof and the only line a drill row may be written on.
     A skipped drill is not a passed drill.
   - A **nightly** unit carries dump-time counts in its `.meta.json`, so it also prints
     `COMPARED n MISMATCHES n NO_COUNT n` and `ROWS_MATCH true|false`; a nightly drill is passed
     when `RESTORE_OK` printed and `ROWS_MATCH true` printed.
   - A **partition** unit (`harness-partitions-<table>_y<year>w<week>-…`) takes no dump-time
     counts (`counts_snapshot=none`), prints `COMPARED 0 MISMATCHES 0 NO_COUNT 0` and
     `ROWS_MATCH n/a` by design, and can never record `rows_match = true`. Its pass is
     `RESTORE_OK` (the user's ruling of 2026-09-18, item 1) **plus** the live-versus-restored
     comparison below, because the archive carries no row count at all.
2. **Mac half** (the user; the age private key lives only there): pull the unit's `.dump.age`
   from the NAS archive (`/volume1/docker/sports-archive/encrypted/partitions/` for partition
   units, `…/nightly/` for nightlies), then `harness backup-decrypt <file>.age --out
   /tmp/restore.dump`. The printed sha256 must equal that unit's `backup_runs.plaintext_sha256`
   (also in the `.meta.json` beside it). This proves the *ciphertext* restores; the host half
   proves the plaintext does.

### Recording the drill (host, no tunnel)

`backup-drill-record` writes to the harness database, which is local on Omarchy: run it inside
the stack, never against a scratch database (a drill recorded elsewhere is a drill that never
happened as far as retention is concerned):

```sh
/srv/sports-harness/sports-compose run --rm -T app-run backup-drill-record \
  --build-sha <encrypt row's build_sha> --decrypt-ok --plaintext-sha256 <sha256 from the Mac half>
```

Add `--rows-match` only for a nightly unit whose host half printed `ROWS_MATCH true`; for a
partition unit pass neither `--rows-match` nor `--no-rows-match` (the column stays null, which
is the honest record of `ROWS_MATCH n/a`).

The build sha is the **encrypt row's own** build, not the sha deployed today: the release rule
(`delete_verified_plaintexts`, `harness/ops/backup.py`) deletes a unit's plaintext only when a
`drill` row with `decrypt_ok = true` exists for the `build_sha` of that unit's own `encrypt`
row, so a drill recorded against a different build releases nothing and leaves the plaintexts
on the host indefinitely (nightly pruning stalls on unreleased plaintexts). Read it back before
recording:

```sh
/srv/sports-harness/sports-compose exec -T postgres psql -X -U harness -d harness -At -F " | " <<'SQL'
select id, kind, build_sha, path, plaintext_sha256 from backup_runs
  where kind = 'encrypt' and status = 'ok' order by id desc limit 8;
SQL
```

Worked example: the two w37 partition units were encrypted 2026-09-14 under build `ca30ed1`
(`backup_runs` 44 and 45), so their drill row is recorded with `--build-sha ca30ed1`, whatever
build is running when the drill runs.

### Partition units: the live-versus-restored comparison

`drill.sh` removes its throwaway container before it returns, so the comparison is a separate,
read-only step run right after `RESTORE_OK`, once per partition unit, and journaled beside the
drill row:

```sh
CID=$(docker run --rm -d -e POSTGRES_PASSWORD="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')" \
      -e POSTGRES_USER=drill -e POSTGRES_DB=drill postgres:16)
until docker exec "$CID" pg_isready -U drill -d drill >/dev/null 2>&1; do sleep 2; done
docker cp /srv/sports-harness/backups/partitions/<file>.dump "$CID:/tmp/drill.dump"
docker exec "$CID" pg_restore --no-owner --no-privileges -U drill -d drill /tmp/drill.dump
docker exec "$CID" psql -U drill -d drill -At -c "select count(*), min(ts), max(ts), max(id) from <table>_y<year>w<week>"
docker stop "$CID" >/dev/null   # --rm removes the container and its anonymous volume
```

(the same throwaway recipe `drill.sh` uses: a random password, an anonymous volume, nothing on
the production cluster)

against the same four numbers from the live partition:

```sh
/srv/sports-harness/sports-compose exec -T postgres psql -X -U harness -d harness -At \
  -c "select count(*), min(ts), max(ts), max(id) from <table>_y<year>w<week>"
```

A sealed partition no longer receives rows, so all four must be **equal**; any difference is
an integrity anomaly (the archive or the partition changed after sealing) and stops the
retention step for that unit.

### Before any partition DROP (item 1, option 1: DETACH then DROP, one partition per quiet hour)

The procedure itself is the user's (`docs/superpowers/autopilot/reports/2026-09-15-storage-retention-proposal.md`; `lock_timeout = '5s'`,
`venue_trades_y2026w37` first as the rehearsal, then `orderbook_events_y2026w37`, midweek). Two
preconditions the ruling did not list, both checked before **each** DROP:

1. **No live cursor points into the partition.** `Order.tape_cursor_event_id` and
   `Order.nw_tape_cursor_event_id` are `orderbook_events` ids; `_sim_book`
   (`harness/execution/loop.py`, the historical branch) falls back to the current book
   **silently** when the cursor's event row is gone, so a dropped partition would change
   counterfactual values without an error. Read-only, must return 0:

   ```sh
   /srv/sports-harness/sports-compose exec -T postgres psql -X -U harness -d harness -At <<'SQL'
   with p as (select min(id) lo, max(id) hi from orderbook_events_y<year>w<week>)
   select count(*) from orders, p
     where tape_cursor_event_id between p.lo and p.hi
        or nw_tape_cursor_event_id between p.lo and p.hi;
   SQL
   ```

   A non-zero count is a stop: those orders' tracks must be closed or re-anchored (a ruling)
   before the partition can go.
2. **The weekly report row would blank the committed report.** `verify.md`'s Weekly report row
   (Layer 2, run on every verify) regenerates `docs/reports/2026-w37.md` with `report --week 37`; after
   the w37 partitions are dropped it would silently overwrite the committed report with empty
   tape tables. That row must be frozen or re-pointed to the committed file **before** the
   first w37 DROP. `verify.md` is edited only through a plan's last task or by the user, so
   this precondition is the user's or a plan's, not the retention step's.

Only after a `drill` row with `decrypt_ok = true` for the same build sha exists does
`backup-encrypt` delete that build's plaintexts. Until then they stay, bounded by retention.

## The key

`harness backup-keygen` writes `secrets/backup_age_key` (0600) and `deploy/backup_age.pub`, on
the Mac only, and refuses to overwrite an existing identity: regenerating the key would make
every existing backup undecryptable. **Copy the private key somewhere safe the moment it
exists.** A backup no one can decrypt is not a backup.

Run it **before the first phase 4 deploy.** `deploy/backup_age.pub` is committed source and the
Makefile pushes it through `$(wildcard deploy/backup_age.pub)`, which resolves to nothing when
the file does not exist: the recipient never reaches the NAS, Docker creates a *directory* at
the bind target, and every encrypt pass records `skipped: no recipient` for good. Commit the
public key; never commit or push the private one.

Files are standard age v1: once `brew install age` is done, `age -d -i secrets/backup_age_key
<file>.age` reads them. Until then `harness backup-decrypt` is the reader.

## Daily numbers

Two numbers go in the journal every verification (verify.md, "Daily line (phase 4)"): the size
of `backups/` and its subdirectories, and the count of plaintext units that have no ciphertext
yet. A count that rises across two consecutive verifications is a carried fix — either the
encrypt job is not running, or the recipient is missing and every pass records
`skipped: no recipient`.

## When a dump does not run

Every skip is one `SKIP` line on the sidecar's stdout.

- `SKIP ... free=<n>% below MIN_FREE_PCT=30%` — `/volume1` is under 30 % free. That is a disk
  problem, not a backup problem; fix the disk.
- Another dump held the lock (the deploy recipe's fallback dump and the scheduled nightly run
  in the same container). It waits up to 900 s and then skips.
- `SKIP partition <name> is already archived` — expected and correct: a sealed partition is
  archived once.
- `SKIP drill free=<n>% below MIN_FREE_PCT=30%` from `drill.sh` — the restore needs room for a
  second copy of the database. It exits 0, so watch for the line; the drill did not run.

A failing dump is logged and the loop continues to the next window. The container is not
restarted, because compose would only restart it into the same failure.
