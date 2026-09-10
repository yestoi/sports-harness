# Dashboard runbook

Phase 4.5 adds five read-only surfaces under `/ui/`, built from `dashboard_snapshots` rows that
`app-serve`'s own scheduler keeps fresh on a cadence. No request handler ever queries Postgres
directly for a surface: a builder runs on its own schedule, writes one row, and the page reads
that row by primary key (`harness/dashboard/snapshots/__init__.py`). The legacy page at `/` and
`/api/summary` are unchanged and keep their own bounded reads.

## What the surfaces are

**Pulse** (`pulse`, 60 s) is "is it alive and honest": the recorder, the executor heartbeat, the
WebSocket, disk and credits, the invariant wall, and a rules-based status word. It is the one
surface every other one's header borrows its status word and snapshot age from.

**Floor** (`floor`, 30 s inside a game window, 120 s outside it) is "what is it doing right now":
the game board, the funnel from ticks through gaps, candidates, intents, orders and fills, open
simulated orders and today's fills, and the `venue_requests` tripwire tile.

**Study** (`study:<year>-<week>`, 10 min) is "is the strategy any good": one snapshot per ISO
week, built for the current week on a clock and rebuilt for a closed week only when its report
run changes (not on a clock), so a stale closed week is never mistaken for a dead job. Each tick
builds the current week plus at most two stale closed ones, so a backlog after a restart drains
over a few ticks instead of rebuilding the whole season at once.

Every cadence above is halved from the first phase 4.5 deploy (fix 31, 2026-09-10), and none of
them is written down anywhere but in the builder module that puts it in its own payload: the
scheduler and Pulse's staleness ladder both import it. The jobs' first runs after a start are
spread over two minutes rather than all firing at once, Pulse first and Floor second, so a
restart does not open with five cold-cache builds in the same second.

**Gate** (`gate`, 300 s) is the October read: one word, `PASSING` or `NOT PASSING`, an evaluation
date, a criteria hash, and the twelve stored criteria with their thresholds.

**Ticket** (`ticket`, 30 s inside a game window while a card is `placed` or `alive`, 120 s
otherwise) is the fun money: the $50/week parlay ledger, placed by hand, never a paper figure or
a research variant.

## How to read a stale snapshot

The header shows the age of the oldest snapshot on the surface, judged against that snapshot's
own `cadence_s`. Twice the cadence is a WATCH, three times is a BROKEN, and Pulse's
`snapshot_stale` rule says the same thing in words for the four fixed names plus the current
ISO week's Study snapshot — a closed week is not judged, because its age is by design, not a
fault.

## When the scheduler has stopped one builder on its own

A builder whose last three builds each cost more than 2,500 ms — ten times the 250 ms
budget — is **disabled by the scheduler itself**. Its job is paused, an `operator_events`
row of kind `snapshot_disabled` records the builder name and the three timings, Pulse's
`snapshot_disabled` rule goes BROKEN, and the snapshot-ages panel marks that row
`stopped over 2500 ms — restart app-serve`. Every `study:<year>-<week>` row is marked
when Study is disabled, because the guard is keyed on the builder and Study is one job.

The row keeps its last payload, so the surface still shows numbers with an age that grows: the
client's staleness banner is what tells a reader they have stopped moving.

**Re-enabling is a restart of `app-serve` and nothing else.** The disabled set lives in that
container's memory, and there is no command, no flag and no row that clears it:

```sh
docker compose restart app-serve
```

That is deliberate. A builder that cost ten times its budget three builds running needs a change
to what it reads, not another chance, and a guard that cleared itself would hide the incident a
person needs to see. Before restarting, read the `snapshot_disabled` event's three timings and
that builder's `serve.snapshot_ms` history: a restart with nothing changed will trip it again.

## How to read a whole-table staleness

If every surface is stale at once, the scheduler inside `app-serve` is not running: check
`docker compose logs app-serve` for the `snapshot scheduler started: [...]` line, and restart
`app-serve` if it is missing. If one surface is stale while the others are fresh, that one
builder is wedged or crash-looping; its row's `error` field is an exception class name only, and
`app-serve`'s own logs carry the traceback, which is the next place to look. `/api/snap` lists
every row's `age_s`, `cadence_s` and `error` in one read.

A snapshot stale on one surface with an `error` beside it in `/api/snap` means that builder is
failing on its own schedule while the others keep running: the `error` is the exception's class
name only, never its message, and the payload shown beside it is the **last good build**, which
is why the surface still shows numbers even while its row is failing.

## How to read a section that says "unavailable"

Every builder guards each of its sections independently (the `section()` helper in
`harness/dashboard/snapshots/__init__.py`): one failing section writes
`{"error": "<exception class name>"}` into that key alone, and every other section on the same
surface keeps rendering normally. The word in the payload is always a class name, never a
message — an exception's text can carry SQL and row content, which is why it never reaches a
payload served over a tunnel to a phone. A whole surface reading "unavailable" everywhere, not
just one section, is the whole-build failure described above, not a section failure.

## Disclosure, and what controls it

The snapshot payloads disclose materially more than `/api/summary` does: gate criteria and
their measured values, per-variant equity and cash, open positions, queue positions. There is
**no authentication** beyond the `127.0.0.1` binding and the ssh tunnel — say that out loud
rather than assuming some other control covers it. Reach the surfaces with:

```sh
ssh -N -L 8180:127.0.0.1:8180 trey@192.168.12.228
```

then open `http://localhost:8180/ui/`. Do not expose port 8180 on the LAN, and do not add a
reverse proxy in front of it without adding real authentication first.

## The rollback constraint

Rolling back is:

```sh
git checkout <previous sha> && make deploy-nas-app
```

which never runs `harness migrate ensure`; the new tables are simply ignored by the old code.
A later **full** `make deploy-nas` run on a rolled-back sha **aborts at the migrate step**:
`ensure`'s `current` branch calls `upgrade_head` unconditionally, and Alembic cannot resolve
`0002_phase45` from a `versions/` directory that does not contain it. Fixing that is a hand
action the user takes, `alembic stamp 0001_baseline`, never something the loop does on its own —
the loop never stamps backwards.

## Turning the scheduler off

Edit `SNAPSHOTS_ENABLED=0` into `/volume1/docker/sports-harness/.env` on the NAS and run
`docker compose restart app-serve` there. `app-serve`'s compose block loads `env_file: .env`,
and only `make deploy-nas`/`make deploy-nas-app` copy `deploy/nas.env` onto that file — editing
`deploy/nas.env` on the Mac and restarting the NAS container changes nothing until the next
deploy. Put the same line in `deploy/nas.env` too, so the next deploy does not silently turn the
scheduler back on. No scheduler job starts at all; the API and `/ui/` keep serving whatever rows
are already in `dashboard_snapshots`, with their ages visibly growing on every surface. This is
the switch to reach for if the whole layer costs more CPU than its budget (`serve.snapshot_ms`'s
per-minute sum, budgeted at 2 s per minute): it stops all five jobs at once. For one runaway
builder the scheduler's own guard, above, already does this per builder and without a person;
this switch is the one to reach for when the machine is in trouble and the cause is not yet
known.

This is what was reached for on 2026-09-10 at 04:37Z, twelve minutes into the first phase 4.5
deploy: the builders' reads were evicting the executor's working set from a page cache too small
to hold both, and `exec.loop_ms` had gone from a 5,275 ms average to 54,653 ms. Fix 31 is the
answer to that — every builder read bounded on an indexed column, the cadences halved,
the Study per-tick cap, the self-guard and the startup stagger — and
`SNAPSHOTS_ENABLED` remains the switch of last resort.
