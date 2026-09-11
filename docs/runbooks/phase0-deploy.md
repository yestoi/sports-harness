# Phase 0 deploy (NAS)

1. Copy the repo to the NAS (git clone or rsync). Confirm `docker compose version` works and note `uname -m` (x86_64 or aarch64; python:3.12-slim is multi-arch).
2. `cp .env.example .env` and keep the Postgres URL as-is.
3. `mkdir -p secrets && printf '%s' "<ODDS_API_KEY>" > secrets/odds_api_key && chmod 600 secrets/odds_api_key`. The key file must contain only the key with no trailing newline, which is why `printf '%s'` is used above instead of `echo`.
   No `chown` is needed: `deploy/nas.env` runs the app containers as `APP_UID=1000`/`APP_GID=10`, the NAS login user, which already owns these files. `chmod 600` is sufficient. (The `65534`/`nobody` advice applies only to a stack started without `deploy/nas.env`.)
   **Warning:** if `secrets/odds_api_key` does not exist when compose starts, Docker creates a *directory* at that path and the app fails on every tick. Delete the directory (`sudo rm -rf secrets/odds_api_key`) and recreate the file with the commands above.
4. `docker compose build && docker compose up -d postgres && docker compose run --rm app-run init-db`.
5. `docker compose run --rm app-run tick-once` and read the JSON log line `tick ok n=... credits=...`.
6. `docker compose up -d` then `curl -s localhost:8080/healthz` → `{"status":"ok",...}` within 60 s.

With a fake or invalid key, `/healthz` returns 503 with `last_status` `error` by design; a real key turns it green on the next tick.

7. Verify data: `docker compose exec postgres psql -U harness -c "select source,endpoint,count(*) from raw_responses group by 1,2 order by 3 desc;"`.
8. Credit check after the first game day: `select date_trunc('day', started_at), sum(credits_used), min(odds_remaining) from runs group by 1 order by 1;` Expect well under 100k/day.
9. Logs: `docker compose logs -f app-run`. Stop: `docker compose down` (data persists in ./pgdata).
   For unattended operation, point an external monitor (uptime checker, or a cron `curl -f`) at http://<nas>:8080/healthz; the container health status alone does not alert anyone.

Must be running before Wed 2026-09-09 19:20 CT (NE at SEA).

## Kalshi WebSocket recorder (app-ws)

The `app-ws` service runs `harness ws-record`, an append-only recorder for Kalshi trade and
orderbook messages. It never places or cancels orders. It is conditional on a Kalshi API key:

1. Create a Kalshi API key: Kalshi account → Settings → API keys → generate a new key. Download
   the private key file immediately (Kalshi shows it once).
2. Store the key id (no trailing newline) as `secrets/kalshi_key_id` and the private key as
   `secrets/kalshi_private_key.pem`, then `chmod 600` both. No `chown` is needed: `deploy/nas.env`
   runs the app containers as `APP_UID=1000`/`APP_GID=10`, the NAS login user, which already owns
   these files. (The `65534`/`nobody` advice applies only to a stack started without
   `deploy/nas.env`.)
3. **Both files must exist as files (not directories) before `docker compose up`.** As with
   `secrets/odds_api_key`, if a secret path does not exist when compose starts, Docker creates a
   directory there instead, and the service exits idle with `kalshi credentials absent; ws
   recorder disabled` on every start. Delete any such directory and recreate the file.
4. If no credentials are present, `harness ws-record` logs that line and exits 0 — this is by
   design so `app-ws` can stay defined in compose without erroring when the key hasn't been
   provisioned yet. The `restart: on-failure` policy means it won't loop-crash-restart in that
   state.
5. `docker compose up -d app-ws` once the secrets are in place. `docker compose logs -f app-ws`
   should show `ws connected wss://api.elections.kalshi.com/trade-api/ws/v2` — this is the
   confirmed-working production WebSocket URL (the connection requires the same RSA-signed
   headers as the REST API, even for public market-data channels).

**One sid per `update_subscription` frame** (fix 43, 2026-09-11). The venue accepts exactly one
subscription id per frame and answers a multi-sid `sids` list with `{"code": 12, "msg":
"Exactly one subscription ID is required"}`; that error frame reconnects the socket, so the
symptom is a tape gap and `book_dirty_markets` climbing on every 15-minute plan. The recorder
sends one frame per sid per action, with consecutive `id`s, so a rejection names the one sid.

Live smoke test performed 2026-09-06 against `wss://api.elections.kalshi.com/trade-api/ws/v2`
with real (unfunded) production credentials: ran `ws-record` against tickers selected by
`select_ws_tickers` for roughly 4 minutes (widened lookahead window for the smoke only), then
SIGTERM. Recorder exited cleanly with no errors logged and flushed pending writes. Resulting
`harness_dev` row counts: `orderbook_events` 500 snapshot, 123595 delta, 0 gap; `venue_trades`
410 `ws`-sourced (alongside 10184 pre-existing `rest`-sourced rows). See
`.superpowers/sdd/2026-09-06-phase1-normalize-match/task-10-report.md` for full detail.

## Paper executor (app-exec)

The `app-exec` service runs `harness exec`, the phase 3 paper executor. Every 15 seconds it
turns the tick's candidate signals into intents, decides what to place, cancel or expire, and
infers fills from the recorded tape. **It places no live order.** There is no venue client in
the executor, its compose block mounts no secrets and sets no `KALSHI_*` variable, and every
order it writes carries `mode = paper`. Nothing it does can reach the exchange.

Only one executor may run at a time. The loop takes a Postgres advisory lock at the start of
each step and releases it at the end, so a second copy — a stray container, or an `exec-once`
you run by hand while the service is up — finds the lock held, writes nothing at all and logs
`executor lock held elsewhere; skipping this loop`.

**Reading the heartbeat.** The `exec_heartbeat` table has one row. `last_loop_at` is the clock
of the most recent completed step, `loops` counts steps since the row was created,
`loops_skipped` counts steps the scheduler missed (a step that overran its period),
`last_loop_ms` and `p95_loop_ms` are how long steps are taking, `book_dirty_markets` is how many
markets the loop could not price this step, `ws_last_event_at` is the newest row on the tape,
and `last_error` holds the message of the last step that raised, or NULL.

```sh
ssh $NAS_USER@$NAS_IP 'cd $NAS_STACK && docker compose exec -T postgres \
  psql -U harness -d harness -c "select * from exec_heartbeat"'
```

Two readings matter most. `ws_last_event_at` far behind `last_loop_at` means the recorder has
stopped, not the executor: the loop marks every book dirty, holds every resting order and
places nothing, which is the safe behaviour but is not a working system. A rising
`loops_skipped` with a large `p95_loop_ms` means the loop no longer fits in its period.

**The healthcheck.** `harness exec-health` exits 1 when the heartbeat row is missing or its
`last_loop_at` is more than 120 s old, and 0 otherwise. Compose runs it every 60 s, so a stalled
executor shows as an unhealthy container in `make status-nas`. Run it by hand the same way:

```sh
ssh $NAS_USER@$NAS_IP 'cd $NAS_STACK && docker compose exec -T app-exec harness exec-health; echo $?'
```

**One step by hand.** `harness exec-once` runs a single step and prints its `ExecStats`
(`intents_new`, `placed`, `cancelled`, `expired`, `fills`, `nw_fills`, `skipped`, `errors`,
`loop_ms`, `locked`). It is the quickest way to see what the loop is deciding. With the service
running it will report `locked=False` and do nothing; stop `app-exec` first if you want it to
act.

```sh
ssh $NAS_USER@$NAS_IP 'cd $NAS_STACK && docker compose run --rm app-exec exec-once'
```

**Stopping it.** `docker compose stop app-exec` — the container handles SIGTERM, finishes the
step in flight and shuts the scheduler down inside the 60 s grace period. Resting paper orders
are rows in `orders` and simply stay where they are; nothing on the venue depends on them.

## Settlement, reporting, the gate, and replay (phase 3 one-offs)

These all run as `docker compose run --rm app-run <command>` on the NAS (or over ssh, `docker
compose run --rm -T app-run <command>` so nothing waits on a TTY). Every one of them is
read/write-safe to run by hand beside the scheduled services: each write is keyed and goes in
`on conflict do nothing`, so a manual run and the scheduled job cannot double-post a fill,
settlement or benchmark row.

- **`harness settle`** runs one pass of the settlement job now instead of waiting for its own
  schedule (`settle_period_s`): settle final games, fetch the venue's own result, compute
  benchmarks, drain CLV and write markouts. It prints `job_run=<id> status=<ok|error>
  budget_exhausted=<bool> stale_unsettled=<n> <stage=counts...>` and exits 1 on `status=error`.
  Re-running it inserts nothing new for work already done.
- **`harness report --week N --out -`** renders the weekly report (ten tables over one ISO
  week's non-replay rows) to stdout; `--out <path>` writes a file instead. `--year` defaults to
  2026. This is the only report path that persists `report_runs`/`report_cells` with
  `provisional = false` — the hourly `report_wtd` settlement stage writes the same tables with
  `provisional = true` so the dashboard has something to show before Monday. `--selected-out`
  and `--confirm` are the week-3 pre-registration freeze/confirmation pair (§7.2) and are not
  part of the weekly cadence.
- **`harness gate`** evaluates the go-live gate criteria and stores one `gate_reports` row per
  exec variant, always `passed = false` in phase 3 by construction (`legal_decision` and
  `live_trading_env` are false). It exits 0 regardless of the verdict — the exit code reports
  that the evaluation ran, not whether it passed — and every run stores a new row; nothing
  already stored is ever rewritten.
- **`harness exec-once`** is documented above with `app-exec`; it is safe to run beside the
  running service only because the advisory lock makes the extra invocation a no-op
  (`locked=False`, nothing written).
- **`harness replay --from-run A --to-run B --variant NAME [--file PATH] --execute`** drives the
  executor over a 15-second grid across `[pricing_clock_for_run(A), pricing_clock_for_run(B)]`
  instead of one pass per run, so it reproduces paper orders and fills the same way the live
  `app-exec` loop would have produced them for that range, tagging every row `replay = true`.
  The Monday 09:45 CT operate duty runs it over the last game day and expects the resulting
  order and fill counts to match the live ones within 2 % (R14); the unit test behind it asserts
  strict equality on a fixed run range. Without `--execute` it re-scores signals only (the
  pre-phase-3 replay path), which is what a measurement amendment cites for a labelling fix.
- **`harness export-fixture --kind day|ws-tape --out PATH`** dumps a slice of the record as a
  single self-contained JSON document, for building or refreshing a test fixture from real NAS
  data rather than by hand. `--out` is required; `--out -` writes the document to stdout
  instead of a file. `--kind day --from-run A --to-run B` writes that run range's
  `raw_responses` plus every `orderbook_events` and `venue_trades` row inside the runs' own
  window — the three tables a replay reads, and the shape `tests/fixtures/day_synthetic/day.json`
  carries. `--kind ws-tape --ticker T --from TS --to TS` (ISO-8601 instants, UTC when they carry
  no offset) writes one ticker's anchoring snapshot, deltas and prints, the shape
  `tests/fixtures/tape_sample_*.json` carries. It reads only: nothing here writes a row and no
  credential is opened.

**What "candidates since the staleness fix" means.** The dashboard and this contract sometimes
need to report a candidate count that is only meaningful starting from a specific deploy, not
since the database's beginning — the running example is a bug in how stale a feed value was
allowed to be before it fed a signal. Once that kind of bug is fixed and deployed, candidates
generated before the fix reflect the buggy logic and candidates after it don't, so summing across
the boundary understates or overstates the healthy rate depending on which side dominates. Read
"since the staleness fix" as "since the deploy time of the most recent carried fix that changed
what counts as a valid candidate" — look up that fix's deploy sha and time in `roadmap.md`'s
`Carried fixes` table and filter `signals.created_at` (or the funnel query) to `>= <that time>`.
Item 13 of the Chrome checklist below is this count, rendered per variant.

## Phase 4: backups, Alembic, and the authenticated Kalshi adapter

Phase 4 adds one service, one host directory, one deploy step and two manual commands. Nothing
in it sends an order to the production venue: the transport refuses every non-GET method before
signing when writes are disabled, and `make_writer(settings, "prod")` refuses on all four of
`LIVE_TRADING=1`, `HARNESS_MODE=live`, a passing gate report and `secrets/legal_decision`.

**Read first:** `docs/runbooks/backups.md` (units, retention, the restore drill, the key) and
`docs/runbooks/alembic.md` (the baseline, `migrate ensure`, the numbered rollback).

### `app-backup`, and the `backups/` host path

`app-backup` runs `postgres:16` with no build and no command but `/backup/loop.sh`. It is the
only place `pg_dump` 16 exists in this stack; the app image has none and gains no packages. It
bind-mounts `deploy/backup` read-only and `./backups` read-write, and holds no secret beyond
`PGPASSWORD`, which is the value `.env`'s SQLAlchemy URL already carries.

`make deploy-nas` creates `backups/`, `backups/nightly`, `backups/weekly`, `backups/partitions`
and `backups/forever` before it pushes. The recipe runs no `chown`: `deploy/nas.env` sets
`APP_UID=1000`/`APP_GID=10`, and both `app-run` and `app-backup` run as that user, which already
owns the tree. A `backups/` tree that predates the recipe can be owned by someone else; fix it
by hand once and journal it.

### The keypair, before the first phase 4 deploy

On the Mac, **before pushing anything**:

```bash
harness backup-keygen        # writes secrets/backup_age_key (0600) and deploy/backup_age.pub
git add deploy/backup_age.pub && git commit    # the public key is committed source
```

Copy `secrets/backup_age_key` somewhere safe immediately. It is never pushed to the NAS, so a
NAS compromise cannot decrypt the backups it holds — and neither can you, without that file.

Order matters. The Makefile pushes the recipient through `$(wildcard deploy/backup_age.pub)`,
which resolves to nothing when the file does not exist. Deploy without it and Docker creates a
*directory* at the bind target and every encrypt pass records `skipped: no recipient` for good.

### Deploy order

`make deploy-nas`, never `deploy-nas-app`: the Dockerfile and `docker-compose.yml` both change
in this phase. The recipe's order is fixed and the middle of it is new:

1. push the tree, `deploy/nas.env` as `.env`, `deploy/backup`, `deploy/backup_age.pub` and the
   secrets that exist;
2. `docker compose build`;
3. `docker compose up -d postgres app-backup`;
4. `harness backup-precheck` — exits 0 when the newest `kind='nightly'`, `status='ok'`
   `backup_runs` row is younger than 26 h. On a non-zero exit the recipe runs `init-db` (so the
   `backup_runs` table exists — on the very first phase 4 deploy it does not, and every phase 4
   schema change is additive, so creating it early here costs nothing), then
   `docker compose exec -T app-backup /backup/dump.sh nightly`, and **asks again**; a second
   failure aborts the deploy. On the first phase 4 deploy that fallback dump *is* the first
   dump, so expect it, and expect the deploy to pause for as long as it takes;
5. `harness migrate ensure`;
6. `init-db`, `seed-teams`, `variants register`;
7. `docker compose up -d` for the rest of the stack.

`migrate ensure` prints which branch it took. On the production database, which has tables and
no `alembic_version`, expect `stamped`: it writes the baseline revision and **never executes the
baseline against a populated database**. `upgraded` on that database would mean it tried to
create what is already there. Journal which branch fired.

### `harness kalshi-smoke --env demo` (manual, and only when the demo secrets exist)

One full authenticated round trip against play money: balance, the nearest open `KXNFLGAME`
market, an order group, a post-only 1-contract YES bid at the market's lowest grid price, an
amend one grid step up to 2 contracts, a read-back, a cancel, a group cancel, a second order
that the venue itself expires after 60 s, then fills and positions. It writes `venue_requests`
rows tagged `env = 'demo'` and, on a failure, one `venue_status` row for `('kalshi', 'demo')`.
**Demo prices are not evidence** and reach no pricing table and no tape.

**No compose service mounts the demo key pair.** The credentials reach a container only for the
length of one `docker compose run --rm`, and the controller supplies the two mounts itself.

Each `-v` source has to be an **absolute host path**. `docker compose run` reads a relative
`./secrets/...` as a *volume name*, not a bind, and refuses the run outright — which is what the
first demo smoke hit. `$NAS_STACK` is that absolute path, so it is what the mounts are written
against:

```bash
ssh $NAS_USER@$NAS_IP 'cd $NAS_STACK && ls -l secrets/kalshi_demo_key_id secrets/kalshi_demo_private_key.pem'
ssh $NAS_USER@$NAS_IP 'cd $NAS_STACK && docker compose run --rm -T \
  -v /volume1/docker/sports-harness/secrets/kalshi_demo_key_id:/run/secrets/kalshi_demo_key_id:ro \
  -v /volume1/docker/sports-harness/secrets/kalshi_demo_private_key.pem:/run/secrets/kalshi_demo_private_key.pem:ro \
  app-run kalshi-smoke --env demo'
```

`ls -l`, never `cat`: whether the files exist is the only thing anyone needs to know about them.
When either is missing the smoke is **skipped**, not failed, and `make deploy-nas`'s conditional
push loop is where to look — a demo secret present on the Mac and absent on the NAS means that
loop did not run, so re-run `make deploy-nas` and journal it.

The guard checks `Path.is_file()`, not `exists()`. If a bind source is missing, Compose creates
an empty *directory* at the mount target, and `exists()` would call that a credential.

Reading the output: it is a table of steps, each `true` or `false` with a short detail. The
details carry numbers, booleans and a small allowlist of order statuses, and never a string the
venue chose — a ticker, a title or an error message from Kalshi is untrusted text and is kept
out of the terminal deliberately. **Never log a headers mapping** from this path: the redaction
filter in `harness/logging_setup.py` rewrites `HEADER: value` text, but a dict repr walks
straight through it and a signed request's headers are a live credential.

Two outcomes that are not failures:

- **`demo unfunded`.** A zero balance exits 0 after one call and sends nothing. An unfunded demo
  account cannot rest an order, and that is not a deploy failure.
- **A venue rejection.** The encoder floors the intent's own-side probability to the market's
  grid and then converts to the YES leg, so on an asymmetric grid a NO leg's YES price can land
  off-grid and the venue refuses it. The smoke prints the grid it used (`steps=`, `low=`,
  `next=`, `high=`) and reports the rejection as a named step, not a traceback. Journal the grid.

### `harness venue-enable <venue> [--env prod]` (manual)

The re-enable after an outage mark. Two consecutive authentication failures against production
write `venue_status` = `unavailable`, and nothing clears it automatically: an account that has
been refused twice needs a human to find out why before it sends again.

```bash
ssh $NAS_USER@$NAS_IP 'cd $NAS_STACK && docker compose run --rm -T app-run venue-enable kalshi'
```

It prints `kalshi/prod: unavailable -> ok`, or `no change` when there was nothing to clear, and
exits 0 either way. The `venue_status.reason` it clears is up to 120 characters of the venue's
own body: quote it in the journal, never act on it, and never let it decide a verdict. The
controller journals every use of this command.

**It clears the row, not a running process's counter.** `OutageCounter` is in-process state and
its count is latched: once the two consecutive auth failures are in, it keeps reporting
marked-worthy while the failures continue, and only a 2xx resets it. So a process that is still
being refused will re-mark the venue on its next answer, whatever this command wrote. The reset
for the counter is restarting the process that holds it (`docker compose restart app-exec`), and
the order is: fix the credential first, then re-enable, then restart. This is moot in the
deployed paper posture — nothing authenticates a write path there, so no process holds a
non-zero counter — and it matters the first time one does.

### What phase 4 adds to a verification

`docs/superpowers/autopilot/verify.md` gains a Phase 4 SQL block, a Phase 4 checks table, five
invariants and a daily line. Two of its rows are worth knowing before the first deploy:

- The **paper-posture tripwire** has two halves. Non-GET rows on `env = 'prod'` must be 0, *and*
  prod `GET` rows in the last 2 hours must be above 0 — the recorder's hourly `/account/limits`
  read is what writes them. A green tripwire on an empty table proves nothing.
- `runs.notes->'venue_limits'` `null` on the newest non-skipped run means
  `has_kalshi_credentials()` was False inside `app-run`. Check the two production key mounts on
  the `app-run` service; without them nothing writes a `venue_requests` row at all.

## Clock accuracy

The NAS must run NTP (UGOS Control Panel → Time). Kalshi rejects a WebSocket handshake whose
`KALSHI-ACCESS-TIMESTAMP` signature is more than a few minutes off its own clock, and every
recorded `fetched_at` inherits whatever skew the host clock has, silently shifting timestamps in
the database. The recorder now compensates the handshake signature: it reads Kalshi's public
`/exchange/status` endpoint before connecting (and, if a connection attempt gets a 401, from that
response's own `Date` header) to compute the server/local clock offset and adds it to the signed
timestamp. This keeps the WebSocket connecting on a skewed host, but it does not and cannot fix
the skew in already-recorded timestamps — NTP on the NAS is still required for accurate data.

## Known external quirks

- ESPN's scoreboard API sits behind Akamai and returns HTTP 403 for custom or browser-like User-Agent strings while accepting httpx's default `python-httpx/x.y`. The HTTP client therefore sends no custom User-Agent. If ESPN rows show `http 403` in `runs.notes.errors`, check this first.

## Upgrading to new code

After pulling a new version, always run the schema step again before starting the services; `init-db` (`create_schema`) only adds tables, views and nullable columns and is idempotent, but the running containers never create tables on their own:

```bash
docker compose build
docker compose run --rm app-run init-db
docker compose run --rm app-run seed-teams   # phase 1+: teams and aliases (safe to repeat)
docker compose run --rm app-run variants register   # phase 2+: registers harness/variants/*.yaml (safe to repeat)
docker compose up -d
```

Symptom of skipping this: `relation "teams" does not exist` in logs, `app-ws` restarting, and `notes.normalize_errors` on every run.

**`partition-bulk-tables` (phase 3, one-off).** `docker compose run --rm app-run partition-bulk-tables`
turns the live `orderbook_events` and `venue_trades` tables into weekly partitions (F19), so a
Layer 2b invariant or a housekeeping check can scan one week's worth of rows instead of the
whole tape. It is safe to run again on an already-partitioned database: it logs `partitioned:
nothing (already partitioned)` and touches nothing. It ran once on the NAS on 2026-09-08,
01:31-02:07 CT with the writers stopped (`orderbook_events` in 2107 s, `venue_trades` in 19 s);
a future deploy does not need to repeat it, and the phase 3 Task 14 deploy skips it as a no-op
for that reason. Run it with the writers (`app-run`, `app-ws`, `app-exec`) stopped if it is ever
needed again on a table this large, since it validates a `CHECK` constraint by scanning the
table it is attaching.

## Deploying with the Makefile (UGREEN NAS)

`make deploy-nas` mirrors the media-stack workflow: it pushes the source tree, `deploy/nas.env` (as `.env`), and the three secret files over SSH into `NAS_STACK_DIR`, builds the image on the NAS, runs `init-db`, `seed-teams`, and `variants register`, and starts the stack. Targets: `make status-nas`, `make logs-nas`, `make ssh-nas`, `make tunnel-nas`, and `make stop-mac` to stop the Mac stopgap once the NAS is green. Connection values live in `.env.nas` (git-ignored; see `.env.nas.example`).

**A dirty tree does not deploy.** `deploy-nas` and `deploy-nas-app` both refuse to run, before anything is pushed, when `git status --porcelain` is non-empty: `refusing to deploy a dirty tree; commit first (or ALLOW_DIRTY=1)`. Commit first. The escape hatch `ALLOW_DIRTY=1 make deploy-nas` exists for a genuine emergency and produces a `-dirty` build stamp, which means the data recorded from that build is attributed to a commit that does not contain the code that produced it — say so in the journal if you ever use it.

**`make deploy-nas-app`** pushes the same tree, env, and secrets, runs the same `init-db` / `seed-teams` / `variants register` steps, then rebuilds and restarts `app-run`, `app-serve`, `app-exec`, and `app-research` with `--no-deps` (`app-research` shares the app image, so it is always part of this build; it did not exist when this recipe was first written). `app-ws` keeps its WebSocket open by default, so the tape loses nothing: a full `deploy-nas` restarts the recorder, and the reconnect resets Kalshi's sequence numbers, so the hole it leaves is not even marked by a `gap` row. Pass `WITH_WS=1` (`make deploy-nas-app WITH_WS=1`) to also rebuild and recreate `app-ws` when the diff touches `harness/venues/kalshi/rfq*.py`, `harness/recorder/ws_sink.py`, or `harness/venues/kalshi/ws.py`. Use `deploy-nas-app` for any deploy whose diff since the deployed sha touches none of `harness/db/models.py`, `docker-compose.yml`, `Dockerfile`, `pyproject.toml`, `constraints.txt` (add `WITH_WS=1` when it also touches the WebSocket/RFQ files above); use the full `deploy-nas` when it touches any of the first group. The target refuses to run until `app-exec` exists in `docker-compose.yml` (it arrives with phase 3) rather than silently restarting services out of order.

**The schema step restores the stopped writers on failure.** Both `deploy-nas` and `deploy-nas-app` stop `app-exec`, `app-run`, and `app-serve` before `init-db` — an `add column if not exists` that is not already a no-op needs an AccessExclusive lock that a running executor loop, or a live `app-serve` snapshot builder holding a read lock, blocks. Fix 37 (2026-09-11): a schema step or the backup-precheck fallback that fails after this stop now runs `docker compose start app-exec app-run app-serve` before exiting non-zero, restoring the containers on the image they were already running rather than leaving them down until someone notices. Before this fix, a failed schema step left the executor down for four minutes on a real deploy.

**A transient `seed-teams` failure no longer aborts the deploy.** ESPN answers 403 often enough that a chained `&&` would leave the old image running with `init-db` already applied; the step now prints `[DEPLOY] WARNING: seed-teams failed; teams unchanged` and the deploy continues. Read the deploy output for that line and re-run `docker compose run --rm app-run seed-teams` when it appears.

**Optional secrets** (`kalshi_demo_key_id`, `kalshi_demo_private_key.pem`, `anthropic_api_key`) are pushed only when the file exists on the Mac, so a missing one does not fail the deploy. `secrets/backup_age_key` is the age *private* key and is never pushed to the NAS; only the public key belongs there, so a NAS compromise cannot decrypt the backups it holds.

## Rolling back a deploy

```bash
git checkout <previous sha>
make deploy-nas          # or deploy-nas-app, by the same file-list rule as above
```

This works because the schema is additive: `init-db` only creates tables and adds nullable columns, so an older image finds every column it expects and simply ignores the newer ones. `init-db` re-runs harmlessly at any sha. Confirm which build is actually live from the `build` field on `/healthz` (or the dashboard header) rather than from what you believe you deployed.

The property that makes this safe is a constraint, not a coincidence: **every migration stays additive.** A migration that drops or renames a column or table is not rollback-safe, because the older image is then missing data the newer one moved. When phase 4 introduces Alembic, a revision containing `DROP` or a rename is a gate for the user, not a decision the loop makes on its own, and the rollback for one is a restore from the pre-migration dump rather than a redeploy.

## Dashboard

`app-serve` now runs the one-page operator dashboard (health, funnel, match report, recent
signals, unmatched markets, WebSocket activity, data quality, and a kill switch) instead of a
bare health endpoint; `/healthz` behaves exactly as before.

The dashboard header shows `build <sha>` and `/healthz` returns a `build` field with the same
value. `make deploy-nas` stamps the pushed `.env` with the commit it deployed, with `-dirty`
appended when the working tree had uncommitted changes at deploy time; `dev` means the
container was started without a stamp.

- **URL**: with `make tunnel-nas` running, open `http://localhost:$(SERVE_PORT)/` (the same
  tunnel that forwards `/healthz`; `SERVE_PORT` is `8180` per `deploy/nas.env`).
- **Not on the LAN**: the `127.0.0.1:` prefix on the published port in `docker-compose.yml` is
  what keeps the dashboard off the LAN; do not remove it, and do not run the stack with
  `network_mode: host`. `harness serve` binds `0.0.0.0` inside the container, which is only
  safe because of that prefix, and the Kill button takes no authentication.
- **Reading the dashboard token**: `make deploy-nas` generates `secrets/dashboard_token` on the
  NAS itself the first time it runs (32 random bytes via `openssl rand -hex 32`) and never
  overwrites an existing one; it is never copied from this machine and is never printed to the
  deploy log. To read it: `make ssh-nas`, then `cat secrets/dashboard_token`. The
  `secrets/dashboard_token` on this Mac is a different value, used only for local runs; it is
  not the NAS token and will not unkill the NAS.
- **Kill switch**: the page has a Kill button (posts a `reason`, no auth — anyone who can reach
  the dashboard can pause trading) and an Unkill button that requires the token. Equivalent
  curl commands from the NAS (or over the tunnel):
  ```bash
  curl -X POST http://127.0.0.1:$(SERVE_PORT)/kill -d 'reason=manual pause'
  curl -X POST http://127.0.0.1:$(SERVE_PORT)/unkill -H "X-Dashboard-Token: $(cat secrets/dashboard_token)"
  ```
  `/unkill` returns 403 if the token is missing, wrong, or the token file itself is absent
  (fails closed rather than allowing an unauthenticated unkill).

NAS specifics baked into `deploy/nas.env`: the app containers run as the NAS user (`APP_UID=1000`, `APP_GID=10`) so the 0600 secret files are readable without `chown`/`sudo`, and the health endpoint is published on `SERVE_PORT=8180` because 8080 is taken by SABnzbd. Docker on the NAS is 26.1 with Compose v2.26.
