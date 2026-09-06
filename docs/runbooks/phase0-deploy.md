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
