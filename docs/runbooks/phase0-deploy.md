# Phase 0 deploy (NAS)

1. Copy the repo to the NAS (git clone or rsync). Confirm `docker compose version` works and note `uname -m` (x86_64 or aarch64; python:3.12-slim is multi-arch).
2. `cp .env.example .env` and keep the Postgres URL as-is.
3. `mkdir -p secrets && printf '%s' "<ODDS_API_KEY>" > secrets/odds_api_key && chmod 600 secrets/odds_api_key`. The key file must contain only the key with no trailing newline, which is why `printf '%s'` is used above instead of `echo`.
   Then `sudo chown 65534:65534 secrets/odds_api_key`: the container runs as uid 65534 (`nobody`) and a Linux bind mount does no uid remapping, so a file owned by your login user is unreadable inside the container.
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
   `secrets/kalshi_private_key.pem`, then `chmod 600` both and `chown 65534:65534` both (same
   uid-remapping reason as `secrets/odds_api_key` above).
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

Live smoke test performed 2026-09-06 against `wss://api.elections.kalshi.com/trade-api/ws/v2`
with real (unfunded) production credentials: ran `ws-record` against tickers selected by
`select_ws_tickers` for roughly 4 minutes (widened lookahead window for the smoke only), then
SIGTERM. Recorder exited cleanly with no errors logged and flushed pending writes. Resulting
`harness_dev` row counts: `orderbook_events` 500 snapshot, 123595 delta, 0 gap; `venue_trades`
410 `ws`-sourced (alongside 10184 pre-existing `rest`-sourced rows). See
`.superpowers/sdd/2026-09-06-phase1-normalize-match/task-10-report.md` for full detail.

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

After pulling a new version, always run the schema step again before starting the services; `create_schema` is additive and idempotent, but the running containers never create tables on their own:

```bash
docker compose build
docker compose run --rm app-run init-db
docker compose run --rm app-run seed-teams   # phase 1+: teams and aliases (safe to repeat)
docker compose run --rm app-run variants register   # phase 2+: registers harness/variants/*.yaml (safe to repeat)
docker compose up -d
```

Symptom of skipping this: `relation "teams" does not exist` in logs, `app-ws` restarting, and `notes.normalize_errors` on every run.

## Deploying with the Makefile (UGREEN NAS)

`make deploy-nas` mirrors the media-stack workflow: it pushes the source tree, `deploy/nas.env` (as `.env`), and the three secret files over SSH into `NAS_STACK_DIR`, builds the image on the NAS, runs `init-db`, `seed-teams`, and `variants register`, and starts the stack. Targets: `make status-nas`, `make logs-nas`, `make ssh-nas`, `make tunnel-nas`, and `make stop-mac` to stop the Mac stopgap once the NAS is green. Connection values live in `.env.nas` (git-ignored; see `.env.nas.example`).

NAS specifics baked into `deploy/nas.env`: the app containers run as the NAS user (`APP_UID=1000`, `APP_GID=10`) so the 0600 secret files are readable without `chown`/`sudo`, and the health endpoint is published on `SERVE_PORT=8180` because 8080 is taken by SABnzbd. Docker on the NAS is 26.1 with Compose v2.26.
