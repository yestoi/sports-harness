# Dashboard runbook

Phase 4.5 adds five read-only surfaces under `/ui/`, built from `dashboard_snapshots` rows that
`app-serve`'s own scheduler keeps fresh on a cadence. No request handler ever queries Postgres
directly for a surface: a builder runs on its own schedule, writes one row, and the page reads
that row by primary key (`harness/dashboard/snapshots/__init__.py`). The legacy page at `/` and
`/api/summary` are unchanged and keep their own bounded reads.

## What the surfaces are

**Pulse** (`pulse`, 30 s) is "is it alive and honest": the recorder, the executor heartbeat, the
WebSocket, disk and credits, the invariant wall, and a rules-based status word. It is the one
surface every other one's header borrows its status word and snapshot age from.

**Floor** (`floor`, 15 s inside a game window, 60 s outside it) is "what is it doing right now":
the game board, the funnel from ticks through gaps, candidates, intents, orders and fills, open
simulated orders and today's fills, and the `venue_requests` tripwire tile.

**Study** (`study:<year>-<week>`, 10 min) is "is the strategy any good": one snapshot per ISO
week, built for the current week on a clock and rebuilt for a closed week only when its report
run changes (not on a clock), so a stale closed week is never mistaken for a dead job.

**Gate** (`gate`, 60 s) is the October read: one word, `PASSING` or `NOT PASSING`, an evaluation
date, a criteria hash, and the twelve stored criteria with their thresholds.

**Ticket** (`ticket`, 15 s inside a game window while a card is `placed` or `alive`, 60 s
otherwise) is the fun money: the $50/week parlay ledger, placed by hand, never a paper figure or
a research variant.

## How to read a stale snapshot

The header shows the age of the oldest snapshot on the surface, judged against that snapshot's
own `cadence_s`. Twice the cadence is a WATCH, three times is a BROKEN, and Pulse's
`snapshot_stale` rule says the same thing in words for the four fixed names plus the current
ISO week's Study snapshot — a closed week is not judged, because its age is by design, not a
fault.

If every surface is stale at once, the scheduler inside `app-serve` is not running: check
`docker compose logs app-serve` for the `snapshot scheduler started: [...]` line, and restart
`app-serve` if it is missing. If one surface is stale while the others are fresh, that one
builder is wedged or crash-looping; its row's `error` field (an exception class name, `NAS
half`'s `app-serve` logs carry the traceback) is the next place to look. `/api/snap` lists every
row's `age_s`, `cadence_s` and `error` in one read.

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

Set `SNAPSHOTS_ENABLED=0` in `deploy/nas.env` and restart `app-serve`. No scheduler job starts
at all; the API and `/ui/` keep serving whatever rows are already in `dashboard_snapshots`, with
their ages visibly growing on every surface. This is the switch to reach for if a builder ever
costs more CPU than its budget (`serve.snapshot_ms`'s per-minute sum, budgeted at 2 s per
minute) — it stops all five jobs, not one, because there is no per-builder switch.
