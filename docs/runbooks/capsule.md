# Runbook: taking an evidence capsule

Design addendum §0.1, §0.2, §0.7, §4.3. The capsule is 6B's input: what the record actually says
about an order or a period, extracted once, hashed, and never re-derived. It is a bounded,
indexed extraction, never an audit scan.

**Who runs this.** The controller, over ssh, by hand. Never an agent: agents have no NAS access
and this reads production. Never inside or right after a game window.

## 1. The quiet window

Extraction runs only between **01:00 and 08:00 CT**, with no game in progress, and **after the
03:30 CT dump** — the dump churns the page cache the executor's own reads need. The planned
window for the 6A wave is **Saturday 2026-09-12, 04:30 to 08:00 CT**: after the dump, before the
10:45 CT game window opens. If it slips, the next window is Sunday 03:00 to 10:20 CT.

One capsule at a time. Between capsules, read `exec.loop_ms` and wait for it to fall back under
10 s before starting the next:

```
select ts, value from metric_samples where name = 'exec.loop_ms'
  order by ts desc limit 5;
```

**Abandon rule.** If `exec.loop_ms` exceeds **30 s** during a capsule, stop the extraction for the
night and take the rest in the next quiet window. A capsule is worth less than the executor's
loop time: journal 101's 19:00–20:00 CT hour (avg 27 s, p95 118 s) is what this rule exists to
avoid repeating.

## 2. The identity check (do this first, every time)

Before any capsule, journal all three of these. The manifest records them and marks a capsule
taken on a build other than the one they name.

```
git -C /Users/trey/dev/sports rev-parse --short main
git -C /Users/trey/dev/sports worktree list
curl -s http://192.168.12.228:8080/healthz | python3 -c 'import sys,json;print(json.load(sys.stdin)["build"])'
```

Fix 37 must already be on `main` and verified by a deploy before any 6A extraction: read the
deploy stamp, never a remembered notification.

## 3. Choosing the five periods

Five named periods plus order 157's own capsule. Each period's selection query **and its result**
go into the capsule through `--period-note`, so the manifest records why that hour was chosen.
All five queries read small tables only.

```
-- clean: one ws_connect, no ws_disconnect, loop p95 under 10 s, orders open
select date_trunc('hour', ts) as h,
       count(*) filter (where kind = 'ws_connect') as connects,
       count(*) filter (where kind = 'ws_disconnect') as disconnects
  from operator_events where ts > now() - interval '7 days'
  group by 1 having count(*) filter (where kind = 'ws_disconnect') = 0
                and count(*) filter (where kind = 'ws_connect') = 1
  order by 1 desc;

-- interleaved: a game-window hour with at least three tickers carrying open orders
select date_trunc('hour', placed_at) as h, count(distinct ticker) as tickers
  from orders where replay = false and placed_at > now() - interval '7 days'
  group by 1 having count(distinct ticker) >= 3 order by 1 desc;

-- gap_recovery: the hour around a disconnect/connect pair
select ts, kind, summary from operator_events
  where kind in ('ws_disconnect', 'ws_connect') and ts > now() - interval '7 days'
  order by ts desc limit 40;

-- delayed_loop: fixed by journal 101 -- 2026-09-10 19:00-20:00 CT (avg 27 s, p95 118 s)

-- capacity_bound: an hour with exec.open_orders at the 150 cap
select date_trunc('hour', ts) as h, max(value) as peak
  from metric_samples where name = 'exec.open_orders' and ts > now() - interval '7 days'
  group by 1 having max(value) >= 150 order by 1 desc;
```

For each chosen hour, list the tickers that had open orders in it; those are the `--ticker`
arguments.

```
select distinct ticker from orders
  where replay = false and placed_at >= :lower and placed_at <= :upper;
```

## 4. Taking a capsule

Order 157's capsule:

```
ssh -o BatchMode=yes trey@192.168.12.228 \
  'cd /volume1/docker/sports-harness && docker compose run --rm -T app-run \
     capsule --order 157 --out - \
       --main-sha <sha> --healthz-build <build> --worktrees "<git worktree list output>"' \
  > /tmp/capsule-order-157.tar
```

A period capsule:

```
ssh -o BatchMode=yes trey@192.168.12.228 \
  'cd /volume1/docker/sports-harness && docker compose run --rm -T app-run \
     capsule --period gap_recovery \
       --from 2026-09-10T19:00:00-05:00 --to 2026-09-10T20:00:00-05:00 \
       --ticker KXNCAAFTOTAL-26SEP12MTUMRSH-59 \
       --main-sha <sha> --healthz-build <build> --worktrees "<...>" \
       --period-note "<the selection query and its result, verbatim>" --out -' \
  > /tmp/capsule-gap-recovery.tar
```

`--out -` writes the whole capsule as a tar stream on stdout, which is the R16 shape. The archive
carries no directory prefix (its members are `manifest.json`, `orders.jsonl.gz` and so on at the root),
so each capsule gets its own directory named for its selector, and unpacking two into one directory
overwrites the first silently. Unpack on the Mac with an explicit destination:

```
mkdir -p capsule/order-157 && tar -xf /tmp/capsule-order-157.tar -C capsule/order-157
```

**Exit codes.**

| Code | Meaning | What to do |
|---|---|---|
| 0 | Clean | Unpack, read `manifest.json`, journal the counts |
| 1 | Bad selector, or an order id with no row | Fix the arguments; nothing was written |
| 2 | A file hit the row cap (`row_cap` in the manifest; 150,000 unless `--cap` overrides it) | The files are written but one table is incomplete: narrow the window and take it again |

## 5. Reading the manifest

Always read `manifest.json` before treating a capsule as evidence.

- `truncated` must be empty. A non-empty list means retake with a narrower window.
- `identity.build_mismatch` must be `false`. `true` means the container is not running the build
  the identity check named; journal it and mark the capsule.
- `unverifiable_slices` is journaled entry by entry. A `gap` entry names the `sid` and the `ts`:
  every ticker on that subscription has a hole in that window. A `no anchor` entry names a ticker
  with no snapshot within two days of the window's start, so it has no book to start from.
- `counts` and the per-file `sha256` are the capsule's own audit. Re-verify one:
  `shasum -a 256 fills.jsonl.gz`.

## 6. Where the files go

The six capsules together, gzipped, under **20 MB**: commit them to
`docs/superpowers/reviews/2026-09-11-phase6-roadmap/capsule/`.

Over 20 MB: leave them on the NAS at `/volume1/docker/sports-harness/capsule/` and commit only
the `manifest.json` of each, under the same path with the capsule's name as the filename. The
manifests are what a reviewer reads first and they are small.
