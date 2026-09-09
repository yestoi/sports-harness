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

## The tape has one archive and no other protection

On Mondays at 04:00 CT each sealed weekly partition of `orderbook_events` and `venue_trades`
from the previous ISO week is archived once to `backups/partitions/` and then never touched.
Between that archive and the RAID, the tape has no other protection.

## Restore drill (a backup is not done until one has run)

Two halves, both recorded in `backup_runs` with `kind = 'drill'`.

1. **NAS half** (the controller, over ssh, from the stack directory on the NAS host):

   ```sh
   ssh $NAS_USER@$NAS_IP 'cd $NAS_STACK && deploy/backup/drill.sh backups/nightly/<file>.dump'
   ```

   **Not** `docker compose exec app-backup /backup/drill.sh`. The sidecar has no docker socket,
   and the script runs `docker run`, `docker compose exec` and `docker cp` itself: it is a host
   script that happens to live beside the sidecar's two, and its own header says so.

   It starts a throwaway `postgres:16` container (`docker run --rm`, an anonymous volume that
   disappears with it), restores the plaintext there, counts rows per non-bulk table in both it
   and `harness` (read-only), prints the comparison and a final `ROWS_MATCH true|false`, then
   stops the container. Nothing on the production cluster is created, dropped or written.

   **Read the output, not the exit status.** The script exits 0 on
   `SKIP drill free=<n>% below MIN_FREE_PCT=30%`, and it exits 0 on `ROWS_MATCH false` as well:
   the verdict is deliberately the printed line, because a dump is a point-in-time snapshot and
   a row count that has moved since is not by itself a failed restore. A drill is passed when a
   `backup_runs` row with `kind = 'drill'` and `rows_match = true` exists — never because the
   command returned 0. A skipped drill is not a passed drill.
2. **Mac half:** `scp` one nightly `.age` file over, then
   `harness backup-decrypt <file>.age --out /tmp/restore.dump`.
   The printed sha256 must equal that unit's `backup_runs.plaintext_sha256`. Record it:
   `harness backup-drill-record --build-sha <sha> --decrypt-ok --plaintext-sha256 <sha256> --rows-match`

   `backup-drill-record` writes to the harness database, which lives on the NAS, and
   `docker-compose.yml` publishes no host port for `postgres` — pointing at `127.0.0.1:5432` on
   the NAS host reaches nothing, so the tunnel has to target the container itself. Open one in
   another shell: `ssh $NAS_USER@$NAS_IP 'cd $NAS_STACK && docker compose exec -T postgres
   hostname -i'` to read the container's address, then
   `ssh -N -L 5432:<that address>:5432 $NAS_USER@$NAS_IP`. With the tunnel open, run the record
   command as `DATABASE_URL=postgresql+psycopg://harness:harness@127.0.0.1:5432/harness harness
   backup-drill-record ...`. The row must land in the NAS database or the release rule
   (`backup-encrypt`'s plaintext deletion) never sees it — a drill recorded against a local
   database is a drill that never happened as far as retention is concerned.

Only after a `drill` row with `decrypt_ok = true` for the same build sha exists does
`backup-encrypt` delete that build's plaintexts. Until then they stay, bounded by retention.

The build sha is the **encrypt row's own** build, not the sha deployed today: the release rule
keys on `backup_runs.build_sha`, so a drill recorded against a different build releases nothing.
Read it back before recording:

```sh
ssh $NAS_USER@$NAS_IP 'cd $NAS_STACK && docker compose exec -T postgres psql -U harness -d harness -At -F " | "' <<'SQL'
select id, kind, build_sha, path, plaintext_sha256 from backup_runs
  where kind = 'encrypt' order by id desc limit 5;
SQL
```

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
