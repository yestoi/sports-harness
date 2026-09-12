## Unit: deploy

Run inline in the controller session, never inside an agent (agents cannot surface failures or prompts, and they have no
NAS access). Preconditions (any failing: do not deploy; journal why):
- `git branch --show-current` prints `main`; `git status --porcelain` prints nothing (untracked files also stamp `-dirty`;
  commit or remove them first).
- No game window (verify.md "Game window", R4): no matched game `in_progress`, no kickoff in the last 4 h or the next 15 min,
  no NFL kickoff 60-100 min away. Otherwise a wakeup for the window's end and another unit. Exceptions, journaled with the
  games affected: only "recorder down", "executor down", "app-serve unhealthy".
- Declared deployment prerequisites are included and reviewed. For the pending 40/41 wave, fix 37 must land before
  the dependent deploy: cover every changed service and restore stopped services after a schema failure. Reviewing 40/41
  can proceed independently; the old manual stop/recreate workaround does not satisfy this prerequisite.
- A lost deploy notification is not a reason to deploy again: read the stamp first (Orient rule 2). `/healthz` build equal to
  `git rev-parse --short main` means it landed. One deploy in flight at a time; a deploy after a failed one needs its
  journaled cause first (Ceilings).
- One deploy per wave, not per batch: every branch whose review is clean at deploy time is merged first, then one
  deploy and one verification cover them all (the verify rows are the union of what the merged findings name).

Target (R4): `make deploy-nas-app` (app containers only; `app-ws` keeps its socket) when the target accepts (it refuses until
`app-exec` exists, phase 3) and this diff is empty; else `make deploy-nas`, which restarts `app-ws` and loses a few seconds of WebSocket events (note it):
`git diff --stat "$DEPLOYED"..main -- harness/recorder/ws_sink.py harness/venues/kalshi/ws.py harness/db/models.py docker-compose.yml Dockerfile pyproject.toml constraints.txt`

1. `DEPLOY_SHA=$(git rev-parse --short HEAD)`; the chosen target in the foreground (a cached build lands in about a minute;
   the Mac's low-memory guard has killed a background deploy before).
2. `make status-nas`: every container `Up`, `app-serve` `(healthy)` within three minutes.
3. Freshness: `/healthz` returns `"build": "<DEPLOY_SHA>"` (curl over ssh); a mismatch or `-dirty` is a deploy failure.
4. A `seed-teams failed` warning in the deploy log: wait five minutes, re-run `docker compose run --rm app-run seed-teams` over
   ssh once, journal it. Any other failure: **REQUIRED SUB-SKILL** `superpowers:systematic-debugging`, inline; unresolved: gate.
5. Tape continuity (the verify.md Layer 2 row): after a full deploy, `app-ws` is `Up`, a snapshot has arrived since the
   restart, and the `gap` rows written around it are counted in the journal's Deploy line.
6. Forced tick: `ssh ... 'docker compose run --rm -T app-run tick-once --force'` so the pricing, ERROR-line and signals
   rows can be judged now instead of at the next cadence slot (it spends one tick's credits; never inside quiet hours,
   where the rows are deferred instead).
7. Journal a `deploy` entry (sha, time, target, containers, stamp check, gap count); continue to verify.
